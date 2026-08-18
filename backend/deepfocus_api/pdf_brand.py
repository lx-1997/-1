"""
研报 PDF 去水印 + 打 daocaijing 品牌水印

缓存三层（速度递减）:
  1. 内存 LRU（最近 N 篇，零延迟）
  2. 磁盘 SHA-256（/opt/deepfocus/pdf_cache/，秒级 I/O）
  3. 实时处理（首次 2-5 秒，结果同时写入上两层）

去水印策略（分两相，相互隔离——一相失败不拖垮另一相）:
  相 1 pikepdf（object 级，对内容流做外科切除——正文零损）
    A. Aspose /Artifact/Subtype/Watermark BDC...EMC 块
    B. 名称可疑的 XObject（/Wm* /Watermark* /stamp*）
    C. 内容流含水印关键词的 XObject
    D. token 状态机：平衡 q...Q 块 → 非正交旋转矩阵 + Do → 按字节切除块 + 删被引 XObject
    D2. 内容流指令级：跟踪 CTM×文本矩阵 + 填充色，只删「浅灰 + 斜置」的 Tj/TJ 显示指令
        （对角平铺水印常压在正文上，矩形 redaction 会连正文一起擦掉；这里只删水印字形）
    并顺带解密（owner 加密、空 user 口令）——保证相 2 拿到的是明文
  相 2 PyMuPDF（渲染级，物理擦除 + 添加顶部遮盖带 / 独立品牌页脚）
    E. 文字水印：关键词/正则 · 旋转 · 浅灰/低透明 · **重复平铺**（跨 span/跨页去重）
    F. 注释水印：Stamp / 低透明 FreeText / 关键词
    G. 图片水印：透明叠加图 / 平铺图直接移除；无文字层的整页扫描图用跨页稳定模板
       识别重复斜水印，只修复模板像素，文字型 PDF 与单页图片一律不做栅格改写

稳健性红线（本次强化）:
  - 单相 try/except 隔离；任一相失败仍尽力产出（至少解密 + 添加品牌标记）。
  - 加密 PDF：pikepdf 空口令解密 + fitz authenticate("")；user 口令保护的优雅透传。
  - **失败（原样透传）不写盘缓存**——避免把「带水印原文」永久钉进缓存，下次仍有机会重试成功。

去水印 vs 误删的取舍:
  水印的判别核心信号是「**重复平铺**」——同一串文字在一页里出现多次、或在多数页同位置出现。
  正文（含唯一的图表斜标签、深色小字脚注）几乎不重复，故以「重复 + 浅灰/旋转/低透明」多信号
  与门收口，既加强去水印又不误删正文。
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import math
import os
import re
import threading
from collections import Counter, OrderedDict
from pathlib import Path

import fitz
import pikepdf

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.getenv("PDF_CACHE_DIR", "/opt/deepfocus/pdf_cache"))

# 去水印/打标逻辑版本号。**改动去水印或品牌逻辑时 +1**：并入缓存键，
# 使已缓存的旧成品自动失效重跑（否则老 PDF 会一直回放旧的弱去水印结果）。
_PROC_VERSION = os.getenv("PDF_BRAND_PROC_VERSION", "v9")
_SWEPT = False

# ── 内存 LRU ──────────────────────────────────────────────────────────────────
_MEM_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_MEM_CACHE_MAX = int(os.getenv("PDF_BRAND_MEM_CACHE", "100"))

# ── 预热已见集合 ──────────────────────────────────────────────────────────────
_PREWARM_SEEN: set = set()
_PREWARM_SEM: asyncio.Semaphore | None = None

# 图片型研报跨页模板识别会同时持有数张全页 RGB 数组。生产预热虽可并发下载，
# 但这里默认串行，避免 3 篇大扫描件同时处理造成内存尖峰。
_RASTER_WM_ENABLED = os.getenv("PDF_RASTER_WM_ENABLED", "1").strip().lower() not in {
    "0", "false", "no",
}
_RASTER_WM_MAX_PAGES = int(os.getenv("PDF_RASTER_WM_MAX_PAGES", "60"))
_RASTER_WM_MAX_PIXELS = int(os.getenv("PDF_RASTER_WM_MAX_PIXELS", "5000000"))
_RASTER_WM_SEM = threading.Semaphore(max(1, int(os.getenv("PDF_RASTER_WM_CONCURRENCY", "1"))))

# ── 水印关键词（子串匹配）────────────────────────────────────────────────────
# **高精度红线**：关键词命中会直接物理擦除该文字行（含 apply_redactions 的溢出擦除），
# 故只收「研报正文几乎不可能出现」的分发渠道/社群词。**严禁**放泛词：
#   已剔除 sample(→samples)、内部(→内部收益率)、仅供参考(每篇免责声明都有)、
#   海外投行研报(产品自身来源标签)、vip、主播、draft、保密(保密协议) 等——
#   这些会误删正文；真水印靠「浅灰/旋转/重复平铺」等信号兜住，不依赖泛关键词。
_WATERMARK_KEYWORDS = [
    "知识星球", "加入星球", "加入知识星球", "水木纪要", "shuimu", "水木2026",
    "更多一手调研纪要和海外投行研报",
    "扫码关注", "扫码添加", "扫码进群", "长按识别", "长按二维码", "识别二维码",
    "不得转载", "翻版必究", "严禁外传", "禁止外传", "禁止传播", "严禁传播",
    "大家好我是",
    "加v看", "加V看", "加v：", "加v:", "加V：", "加V:", "加微信", "薇信",
]
# 这些英文词会在正文的合规描述中出现，不能单靠关键词直接擦除；
# 只在浅色 / 旋转 / 重复等版式水印信号同时存在时才判定。
_GENERIC_WATERMARK_LABELS = (
    "confidential", "not for distribution", "do not distribute", "do not redistribute",
)
# 复审教训：关键词命中即物理擦整行，故这里**只留研报正文几乎不可能出现的分发/社群短语**。
# 已剔除会误删正文/标题的行业词与泛词：调研纪要(=研报体裁,如「XX公司调研纪要」)、
# 内部资料/内部参考/内部交流(内部资料库/内部交流会)、公众号/微信公众(可为研报分析对象)、
# 微信号(官方微信号；带号走正则)、机密/绝密/获取更多/更多投研 等。真水印靠 浅灰/旋转/重复 兜。

# ── 水印正则（高信号：手机号 / 微信号带 id）——单条命中即判水印 ───────────────
# 微信号一律要求显式冒号，避免 "vxworks"/"weixin" 之类词内子串误伤。
_WATERMARK_REGEXES = [
    re.compile(r"1[3-9]\d{9}"),                                          # 裸手机号(11位连续)
    re.compile(r"1[3-9]\d[\s\-]\d{4}[\s\-]\d{4}"),                       # 带分隔手机号
    re.compile(r"(?:微信号?|weixin|wechat|薇信|vx)\s*[:：]\s*[A-Za-z0-9_\-]{4,}"),
    re.compile(r"qq\s*[:：]\s*\d{5,}", re.I),
]

# 页脚型 URL/域名——仅在「深色 + 跨页重复 + 处于底部页脚带」三条同时满足时才判水印，
# 用来收「深色页脚含分发网址」这类既非关键词也非浅灰的漏网水印，且不误伤合法深色页眉。
_FOOTER_URL_RE = re.compile(
    r"(?:https?://|www\.[a-z0-9\-]+\.|[a-z0-9\-]{2,}\.(?:com|cn|net|vip|top|xyz|cc|info|pro)\b)",
    re.I,
)

# ── 重复平铺 / 误删保护参数 ──────────────────────────────────────────────────
_OPACITY_THRESHOLD = 0.85
_REPEAT_ON_PAGE = int(os.getenv("PDF_WM_REPEAT_ON_PAGE", "3"))   # 同页出现 N 次即疑平铺
_CROSS_PAGE_FRAC = float(os.getenv("PDF_WM_CROSS_PAGE_FRAC", "0.6"))
_MIN_PAGES_FOR_CROSS = 3
_MIN_WM_LEN = 4                                                   # 太短的重复串（表格值/页码）不判

# 正文常见、重复也不该删的合法短语（仅豁免「重复」这一条规则，仍可被关键词/旋转命中）。
# 冒号不敏感 + 覆盖常见「来源/注释/图表/单位」前缀，防误删重复的来源行/图注（复审教训）。
_LEGIT_REPEAT = (
    "资料来源", "数据来源", "来源", "source", "单位", "图表", "图 ", "表 ",
    "备注", "注：", "注:", "注为", "注1", "注①", "*为", "＊为", "说明：", "说明:",
    "免责声明", "风险提示", "分析师", "证券研究报告", "请阅读", "评级说明",
)

# Aspose Artifact BDC...EMC：兼容 name 之间无空格的紧凑写法。
_ARTIFACT_WM_RE = re.compile(
    rb'/Artifact\s*<<[^>]*?/Subtype\s*/Watermark\b[^>]*?>>\s*BDC\s*q\s*.*?Q\s*EMC',
    re.DOTALL,
)

# 可疑 XObject 名称
_SUSPICIOUS_XOBJ_RE = [
    re.compile(r"(?i)^/Wm\d*$"),
    re.compile(r"(?i)^/Watermark"),
    re.compile(r"(?i)^/stamp", re.I),
]

# 品牌标记：顶部用完全不透明色带覆盖原分发水印，底部增加独立页脚。
_BRAND_TOP_COVER_H = 26.0
_BRAND_TOP_FILL = (0.945, 0.970, 1.0)
_BRAND_FOOTER_H = 12.0
_BRAND_FOOTER_FILL = (0.955, 0.975, 1.0)
_BRAND_TEXT_COLOR = (0.22, 0.34, 0.52)
_BRAND_TEXT = "DeepFocus｜股票投资信息与深度研究｜www.daocaijing.com"
_BRAND_PARTS = (
    ("DeepFocus", "helv"),
    ("｜股票投资信息与深度研究｜", "china-s"),
    ("www.daocaijing.com", "helv"),
)


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """归一化文字（折叠空白）——用于重复平铺计数。"""
    return re.sub(r"\s+", " ", (text or "").strip())


def _compact(text: str) -> str:
    """去掉水印中常见的拆字空格 / 分隔符，供高精度关键词匹配。"""
    return re.sub(r"[\s\u3000|｜·•_\-:：]+", "", (text or "").lower())

def _is_kw(text: str) -> bool:
    """关键词子串 或 高信号正则 命中。"""
    t = (text or "").lower().strip()
    if not t:
        return False
    compact = _compact(t)
    if any(k.lower() in t or _compact(k) in compact for k in _WATERMARK_KEYWORDS):
        return True
    return any(rx.search(text or "") for rx in _WATERMARK_REGEXES)


def _is_generic_wm_label(text: str) -> bool:
    compact = _compact(text)
    return any(_compact(label) in compact for label in _GENERIC_WATERMARK_LABELS)


def _is_our_brand(text: str) -> bool:
    compact = _compact(text)
    return "daocaijing.com" in compact and "deepfocus" in compact


def _is_current_brand(text: str) -> bool:
    """只识别当前版本的完整品牌文案。"""
    return _compact(_BRAND_TEXT) in _compact(text)


def _brand_text_width(fontsize: float) -> float:
    return sum(
        fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
        for text, fontname in _BRAND_PARTS
    )


def _insert_brand_text(
    page: fitz.Page,
    point: tuple[float, float],
    *,
    fontsize: float,
    fill_opacity: float,
) -> None:
    """中英文混排：英文使用紧凑的 Helvetica，中文使用内置 CJK 字体。"""
    x, y = point
    for text, fontname in _BRAND_PARTS:
        page.insert_text(
            (x, y),
            text,
            fontsize=fontsize,
            fontname=fontname,
            color=_BRAND_TEXT_COLOR,
            fill_opacity=fill_opacity,
            overlay=True,
        )
        x += fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)


def _rect_area(rect: fitz.Rect) -> float:
    """PyMuPDF 1.26 的 Rect 已无 get_area()，使用宽高乘积兼容新旧版。"""
    return max(0.0, rect.width) * max(0.0, rect.height)

def _is_suspicious(name: str) -> bool:
    return any(p.match(name) for p in _SUSPICIOUS_XOBJ_RE)

def _is_light_gray(packed: int) -> bool:
    r = (packed >> 16) & 0xFF
    g = (packed >> 8) & 0xFF
    b = packed & 0xFF
    return (r + g + b) / 3 > 150 and max(r, g, b) - min(r, g, b) < 40

def _is_wm_rotation(a: float, b: float, c: float, d: float) -> bool:
    """返回 True 当 [a b c d] 是非标准旋转角（不是 0/90/180/270°）。"""
    if abs(b) < 0.04 and abs(c) < 0.04:
        return False
    if abs(a) < 0.04 and abs(d) < 0.04:
        return False
    angle = round(math.degrees(math.atan2(b, a))) % 360
    return angle not in {0, 90, 180, 270}

def _mat2_mul(m1, m2):
    """2x2 行向量矩阵乘 m1·m2（各为 [a,b,c,d]）。用于算 文本矩阵×CTM 的合成旋转。"""
    a1, b1, c1, d1 = m1
    a2, b2, c2, d2 = m2
    return (a1 * a2 + b1 * c2, a1 * b2 + b1 * d2,
            c1 * a2 + d1 * c2, c1 * b2 + d1 * d2)

def _rgb_is_light_gray(r: float, g: float, b: float) -> bool:
    return (max(r, g, b) - min(r, g, b) < 0.16) and (r + g + b) / 3 > 0.55

def _mem_put(key: str, data: bytes) -> None:
    _MEM_CACHE[key] = data
    _MEM_CACHE.move_to_end(key)
    while len(_MEM_CACHE) > _MEM_CACHE_MAX:
        _MEM_CACHE.popitem(last=False)


def _fid_cache_path(file_id: str) -> Path:
    safe = re.sub(r'[^A-Za-z0-9._-]', '_', file_id)[:80]
    return _CACHE_DIR / f"fid_{_PROC_VERSION}_links1_{safe}.pdf"


def _fid_mem_key(file_id: str) -> str:
    return f"fid:{_PROC_VERSION}:links1:{file_id}"


_OWNED_CACHE_RE = re.compile(r"^(?:fid_v\d+_|[0-9a-f]{20}_v\d+$)")
_CUR_VER_RE = re.compile(rf"(?:^|_){re.escape(_PROC_VERSION)}(?:_|$)")


def _sweep_stale_cache_once() -> None:
    """惰性清理：删掉**本模块产出**里版本 != 当前 _PROC_VERSION 的旧成品，避免版本迭代后无限增长。
    只动符合本模块命名（fid_vN_* / <20hex>_vN）的文件，绝不误删缓存目录里其它文件；
    版本按 `_vN_`/`_vN` 边界匹配（不会把 v20 当 v2）。尽力而为，失败静默；每进程只跑一次。"""
    global _SWEPT
    if _SWEPT:
        return
    _SWEPT = True
    try:
        for p in _CACHE_DIR.glob("*.pdf"):
            if not _OWNED_CACHE_RE.match(p.stem):
                continue                       # 非本模块文件 → 不碰
            if not _CUR_VER_RE.search(p.stem):
                try:
                    p.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def get_cached_by_file_id(file_id: str) -> "bytes | None":
    """按 file_id 查内存 + 磁盘缓存，命中返回字节，否则 None。
    在下载源文件之前调用，命中则完全跳过网络请求。"""
    if not file_id:
        return None
    mem_key = _fid_mem_key(file_id)
    if mem_key in _MEM_CACHE:
        _MEM_CACHE.move_to_end(mem_key)
        return _MEM_CACHE[mem_key]
    try:
        p = _fid_cache_path(file_id)
        if p.exists():
            data = p.read_bytes()
            _mem_put(mem_key, data)
            return data
    except Exception:
        pass
    return None


def has_cached_file_id(file_id: str) -> bool:
    """轻量判断某 file_id 的去水印成品是否已落盘（不读文件内容）。
    供预热调度判定「这篇原文是否还需下载去水印」，避免 get_cached_by_file_id 整文件读盘的开销。"""
    if not file_id:
        return False
    if _fid_mem_key(file_id) in _MEM_CACHE:
        return True
    try:
        return _fid_cache_path(file_id).exists()
    except Exception:
        return False


# ── 层 D：token 状态机（最核心）──────────────────────────────────────────────

_WS_BYTES = frozenset((0x20, 0x0a, 0x0d, 0x09, 0x0c, 0x00))


def _content_tokens(raw: bytes) -> list[tuple[bytes, int, int]]:
    """把内容流切成 (piece, start, end) 列表——**不改动原字节**（保留字节偏移用于精确切除）。
    在空白处切分，并在每个 '/' 处另起一段，从而 "cm/Fm" 也能识别出 cm 与 /Fm。
    **跳过 (...) 文字字符串**（含 \\ 转义、嵌套括号），避免串里的 'q'/'Q' 被误当图形算符导致
    错误配对切除正文（复审确认的破坏点）。十六进制串/内联图二进制不含算符字面量，天然安全。"""
    out: list[tuple[bytes, int, int]] = []
    n = len(raw)
    i = 0
    while i < n:
        ch = raw[i]
        if ch == 0x28:                       # '(' 文字串起始 → 跳到匹配的 ')'
            depth = 1
            j = i + 1
            while j < n and depth > 0:
                c = raw[j]
                if c == 0x5c:                # '\' 转义，跳过下一个字节
                    j += 2
                    continue
                if c == 0x28:
                    depth += 1
                elif c == 0x29:
                    depth -= 1
                j += 1
            i = j
            continue
        if ch in _WS_BYTES:
            i += 1
            continue
        start = i                            # 读一段非空白、非 '(' 的 run
        while i < n and raw[i] not in _WS_BYTES and raw[i] != 0x28:
            i += 1
        run = raw[start:i]
        cuts = [k for k, c in enumerate(run) if c == 0x2f and k > 0]  # '/' 非首位切段
        if not cuts:
            out.append((run, start, i))
        else:
            ss = [0] + cuts
            for a, b in zip(ss, ss[1:] + [len(run)]):
                out.append((run[a:b], start + a, start + b))
    return out


def _remove_wm_q_blocks(raw: bytes, xobjs) -> tuple[bytes, int]:
    """
    逐 token 扫描内容流，找平衡 q...Q 块：内含非标准旋转 cm 且内含 /Name Do → 高置信水印。
    **按字节偏移切除整块**（不再 token 重拼），从而不破坏字符串内空格与内联图像二进制。
    只删「Do 引用 XObject」的旋转块；直接旋转文字交由相 2 fitz E 层安全判定。
    返回 (new_raw, count_removed)。
    """
    toks = _content_tokens(raw)
    n = len(toks)
    if n == 0:
        return raw, 0

    words = [t[0] for t in toks]
    candidates: list[tuple[int, int, list[str]]] = []

    i = 0
    while i < n:
        if words[i] != b'q':
            i += 1
            continue

        depth = 1
        j = i + 1
        while j < n and depth > 0:
            if words[j] == b'q':
                depth += 1
            elif words[j] == b'Q':
                depth -= 1
            j += 1
        if depth != 0:                     # 没找到配对的 Q
            i += 1
            continue

        blk = words[i:j]                   # blk[0]=q, blk[-1]=Q
        rot_found = False
        for k in range(6, len(blk)):
            if blk[k] == b'cm':
                try:
                    if _is_wm_rotation(float(blk[k-6]), float(blk[k-5]),
                                       float(blk[k-4]), float(blk[k-3])):
                        rot_found = True
                        break
                except (ValueError, IndexError):
                    pass

        if rot_found:
            do_found = [blk[k-1].decode(errors="ignore")
                        for k in range(1, len(blk))
                        if blk[k] == b'Do' and blk[k-1].startswith(b'/')]
            if do_found:
                candidates.append((toks[i][1], toks[j-1][2], do_found))

        i = j

    if not candidates:
        return raw, 0

    # 单个旋转 XObject 很可能是合法的图表标签 / 旋转图片，不能直接删。
    # 只删可疑命名，或同一 XObject 在本页旋转平铺 >= N 次的块。
    # Aspose 这类每个水印都是不同 Fm 的情况由前面的 /Subtype/Watermark
    # Artifact 精确切除，不依赖这个启发式。
    name_counts: "Counter[str]" = Counter(
        name for _, _, names in candidates for name in set(names)
    )
    selected = [
        (start, end, names)
        for start, end, names in candidates
        if any(_is_suspicious(name) or name_counts[name] >= _REPEAT_ON_PAGE for name in names)
    ]
    if not selected:
        return raw, 0

    skip_bytes = [(start, end) for start, end, _ in selected]
    xobj_names = [name for _, _, names in selected for name in names]
    removed = len(selected)

    # 删被引用 XObject
    for name in xobj_names:
        if xobjs and name in xobjs:
            try:
                del xobjs[name]
            except Exception:
                pass

    # 按字节偏移拼回：删块之外的字节逐字节保留（含内联图像二进制、字符串内空白）
    skip_bytes.sort()
    out = bytearray()
    cursor = 0
    for s, e in skip_bytes:
        if s < cursor:                     # 理论上不重叠，防御性跳过
            continue
        out += raw[cursor:s]
        cursor = e
    out += raw[cursor:]
    return bytes(out), removed


# ── 去水印：pikepdf（A+B+C+D）────────────────────────────────────────────────

def _remove_xobj(pdf: pikepdf.Pdf) -> int:
    total = 0
    for page in pdf.pages:
        res   = page.get("/Resources")
        xobjs = res.get("/XObject") if res else None
        contents = page.obj.get("/Contents")
        if contents is None:
            continue
        items = list(contents) if isinstance(contents, pikepdf.Array) else [contents]

        last_raw = b""

        # ── A: Aspose Artifact BDC...EMC ─────────────────────────────────────
        artifact_removed = 0
        for stream in items:
            try:
                raw = stream.read_bytes()
                last_raw = raw
                cleaned = _ARTIFACT_WM_RE.sub(b"", raw)
                cleaned = re.sub(
                    rb'q\s+1\s+0\s+0\s+1\s+0\s+0\s+cm\s+/OL\d+\s+Do\s+Q\s*Q',
                    b"", cleaned,
                )
                if len(cleaned) != len(raw):
                    stream.write(cleaned)
                    artifact_removed += len(_ARTIFACT_WM_RE.findall(raw))
            except Exception:
                pass

        if artifact_removed and xobjs and last_raw:
            wm_fm = set()
            for blk in _ARTIFACT_WM_RE.findall(last_raw):
                for fm in re.findall(rb'/(Fm\d+)\s+Do', blk):
                    wm_fm.add("/" + fm.decode())
            for name in wm_fm:
                try: del xobjs[name]
                except Exception: pass
        total += artifact_removed

        # ── B: 可疑名称 XObject ───────────────────────────────────────────────
        if xobjs:
            for name in [k for k in list(xobjs.keys()) if _is_suspicious(str(k))]:
                try: del xobjs[name]
                except Exception: pass

        # ── C: XObject 内容含水印关键词 ───────────────────────────────────────
        if xobjs:
            kw_bytes = [k.encode("utf-8") for k in _WATERMARK_KEYWORDS]
            to_del: list = []
            for name in list(xobjs.keys()):
                try:
                    raw = xobjs[name].read_bytes()
                    if any(kw in raw for kw in kw_bytes):
                        to_del.append(name)
                except Exception:
                    pass
            for name in to_del:
                try: del xobjs[name]
                except Exception: pass
            total += len(to_del)

        # ── D: token 状态机 q...Q 旋转块 ─────────────────────────────────────
        for stream in items:
            try:
                raw = stream.read_bytes()
                new_raw, n_removed = _remove_wm_q_blocks(raw, xobjs)
                if n_removed:
                    stream.write(new_raw)
                    total += n_removed
            except Exception:
                pass

    return total


# ── 去水印：pikepdf 外科级——只删「浅灰 + 斜置」的直接文字显示指令 ──────────────
# 关键：对角平铺水印常压在正文上；相 2 的 fitz 用矩形 redaction 会**连正文一起擦掉**
# （实测密集对角水印下正文 20 行只剩 4 行）。这里在内容流层解析指令、跟踪 图形状态
# （CTM×文本矩阵 的合成旋转 + 填充色），只丢弃「浅灰填充且非正交旋转」的 Tj/TJ 显示指令，
# **只删水印自身字形，正文（深色/正交）分毫不动**。深色斜标签(图表轴)因非浅灰而保留。

_SHOW_OPS = {"Tj", "TJ", "'", '"'}


def _remove_gray_rotated_text(pdf: pikepdf.Pdf) -> int:
    total = 0
    for page in pdf.pages:
        try:
            insns = pikepdf.parse_content_stream(page)
        except Exception:
            continue
        ctm = [1.0, 0.0, 0.0, 1.0]
        tm  = [1.0, 0.0, 0.0, 1.0]
        fill_gray = False
        fill_sig = None
        font_sig = ("", 0.0)
        stack: list = []
        classified: list[tuple[object, object]] = []
        candidate_counts: "Counter[object]" = Counter()
        for ins in insns:
            op = str(ins.operator)
            od = ins.operands
            if op == "q":
                stack.append((list(ctm), fill_gray, fill_sig, font_sig))
            elif op == "Q":
                if stack:
                    saved_ctm, fill_gray, fill_sig, font_sig = stack.pop()
                    ctm = list(saved_ctm)
            elif op == "cm":
                try:
                    m = [float(x) for x in od]
                    ctm = list(_mat2_mul(m[:4], ctm))
                except Exception:
                    pass
            elif op == "rg":
                try:
                    r, g, b = (float(x) for x in od)
                    fill_gray = _rgb_is_light_gray(r, g, b)
                    fill_sig = ("rgb", round(r, 2), round(g, 2), round(b, 2)) if fill_gray else None
                except Exception:
                    fill_gray = False
                    fill_sig = None
            elif op == "g":
                try:
                    gray = float(od[0])
                    fill_gray = gray > 0.55
                    fill_sig = ("g", round(gray, 2)) if fill_gray else None
                except Exception:
                    fill_gray = False
                    fill_sig = None
            elif op in ("cs", "sc", "scn", "k"):
                fill_gray = False          # 未知/非灰阶色空间：宁可不删
                fill_sig = None
            elif op == "BT":
                tm = [1.0, 0.0, 0.0, 1.0]
            elif op == "Tf":
                try:
                    font_sig = (str(od[0]), round(float(od[1]), 1))
                except Exception:
                    font_sig = ("", 0.0)
            elif op == "Tm":
                try:
                    t = [float(x) for x in od]
                    tm = t[:4]
                except Exception:
                    tm = [1.0, 0.0, 0.0, 1.0]

            signature = None
            if op in _SHOW_OPS and fill_gray:
                comb = _mat2_mul(tm, ctm)
                if _is_wm_rotation(*comb):
                    angle = round(math.degrees(math.atan2(comb[1], comb[0])) / 2) * 2
                    signature = ("rot", angle, fill_sig, font_sig)
                elif font_sig[1] >= 16:
                    # 大字号正立平铺（如 CONFIDENTIAL）也在指令层外科删字形，
                    # 但必须是「同字体 + 同颜色 + 同显示内容」重复，避免误删表格。
                    signature = ("flat-large", fill_sig, font_sig, repr(od))
                if signature is not None:
                    candidate_counts[signature] += 1
            classified.append((ins, signature))

        removable = {
            sig for sig, count in candidate_counts.items()
            if count >= _REPEAT_ON_PAGE
        }
        kept = [ins for ins, sig in classified if sig not in removable]
        removed = sum(1 for _, sig in classified if sig in removable)

        if removed:
            try:
                page.obj["/Contents"] = pdf.make_stream(
                    pikepdf.unparse_content_stream(kept))
                total += removed
            except Exception:
                pass
    return total


_RESEARCH_HUB_URL = (
    os.getenv("DEEPFOCUS_RESEARCH_HUB_URL", "https://www.daocaijing.com/?tab=research").strip()
    or "https://www.daocaijing.com/?tab=research"
)
_ZSXQ_URI_RE = re.compile(r"^https?://(?:[^./]+\.)?zsxq\.com(?:[/:?#]|$)", re.I)


def _redirect_full_page_source_links(pdf: pikepdf.Pdf) -> int:
    """把知识星球植入的整页 PDF 链接改到本站研报栏目。

    只改覆盖页面至少 80% 的 ``/Link`` 注释，保留研报正文里的普通外部引用，
    也不触碰其它来源域名。部分上游 PDF 会在前几页铺一个透明整页链接，
    导致用户点击正文任意位置都离站；这里保留点击能力但把落点收回本站。
    """
    redirected = 0
    for page in pdf.pages:
        page_obj = page.obj
        page_box = page_obj.get("/CropBox") or page_obj.get("/MediaBox")
        try:
            px0, py0, px1, py1 = (float(value) for value in page_box)
            page_area = abs((px1 - px0) * (py1 - py0))
        except Exception:
            page_area = 0.0
        if page_area <= 0:
            continue

        for annot_ref in list(page_obj.get("/Annots") or []):
            try:
                # pikepdf 读取间接引用时已经自动解引用为 Object，没有 get_object()。
                annot = annot_ref
                if str(annot.get("/Subtype") or "") != "/Link":
                    continue
                action = annot.get("/A")
                if not action or str(action.get("/S") or "") != "/URI":
                    continue
                uri = str(action.get("/URI") or "").strip()
                if not _ZSXQ_URI_RE.match(uri):
                    continue
                rx0, ry0, rx1, ry1 = (float(value) for value in annot.get("/Rect"))
                link_area = abs((rx1 - rx0) * (ry1 - ry0))
                if link_area < page_area * 0.80:
                    continue
                action["/URI"] = pikepdf.String(_RESEARCH_HUB_URL)
                redirected += 1
            except Exception:
                continue
    return redirected


# ── 去水印：PyMuPDF（E 文字 / F 注释 / G 图片）────────────────────────────────

def _gather_text_spans(doc: fitz.Document) -> tuple[list, "Counter", int]:
    """两遍法第一遍：跨页收集文字 span（不改动文档），并统计每个归一串在多少页出现过。

    返回 (spans_by_page, doc_page_presence, npages)。
      spans_by_page[i] = [{rect,color,opacity,text,norm,is_rot,size}, ...]
      doc_page_presence[norm] = 含该串的**页数**（不是总次数，避免单页海量重复失真）
    """
    spans_by_page: list = []
    doc_page_presence: "Counter" = Counter()
    npages = 0
    for page in doc:
        npages += 1
        spans: list = []
        norms_this_page: set = set()
        try:
            blocks = page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]
        except Exception:
            blocks = []
        for block in blocks:
            if block.get("type") != 0:
                continue
            for line in block.get("lines", []):
                d = line.get("dir", (1, 0))
                angle = round(math.degrees(math.atan2(-d[1], d[0])))
                is_rot = angle not in (0, 90, -90, 180, -180)
                for span in line.get("spans", []):
                    r = fitz.Rect(span.get("bbox", [0, 0, 0, 0]))
                    if r.width < 0.5 or r.height < 0.5:
                        continue
                    # rawdict 的 span 用 "chars"（逐字）承载文字，没有合并的 "text" 键——
                    # 必须从 chars 重建，否则关键词/重复平铺判定拿不到文字（历史坑）。
                    text = span.get("text")
                    if not text:
                        text = "".join(ch.get("c", "") for ch in span.get("chars", []))
                    norm = _norm(text)
                    # 透明度：本版 PyMuPDF 的 span 用 "alpha"(0-255) 而非 "opacity"，
                    # 旧代码读 "opacity" 恒得 1.0（低透明水印判定形同虚设，历史坑）。
                    alpha = span.get("alpha")
                    opacity = (alpha / 255.0) if alpha is not None else span.get("opacity", 1.0)
                    spans.append({
                        "rect": r,
                        "color": span.get("color", 0),
                        "opacity": opacity,
                        "text": text,
                        "norm": norm,
                        "is_rot": is_rot,
                        "size": span.get("size", 0) or 0,
                    })
                    if norm:
                        norms_this_page.add(norm)
        spans_by_page.append(spans)
        for nrm in norms_this_page:
            doc_page_presence[nrm] += 1
    return spans_by_page, doc_page_presence, npages


def _redact_text_watermarks(page: fitz.Page, spans: list, doc_presence: "Counter", npages: int) -> int:
    """两遍法第二遍：按多信号 + 重复平铺规则物理擦除文字水印。"""
    if not spans:
        return 0

    page_counts: "Counter" = Counter(s["norm"] for s in spans if s["norm"])
    cross_thresh = max(_MIN_PAGES_FOR_CROSS, math.ceil(_CROSS_PAGE_FRAC * npages))
    pw = page.rect.width or 1.0
    ph = page.rect.height or 1.0

    def _is_wm(s: dict) -> bool:
        norm = s["norm"]
        gray = _is_light_gray(s["color"])
        faint = s["opacity"] < _OPACITY_THRESHOLD
        rot = s["is_rot"]
        kw = _is_kw(s["text"])
        generic_label = _is_generic_wm_label(s["text"])
        long_enough = len(norm) >= _MIN_WM_LEN
        whitelisted = any(w in norm for w in _LEGIT_REPEAT)
        rep_page = bool(norm) and long_enough and not whitelisted and page_counts[norm] >= _REPEAT_ON_PAGE
        rep_cross = (bool(norm) and long_enough and not whitelisted
                     and npages >= _MIN_PAGES_FOR_CROSS
                     and doc_presence.get(norm, 0) >= cross_thresh)

        if _is_our_brand(s["text"]):
            return False                  # 幂等重跑时保留我方页脚
        if kw:
            return True
        if generic_label and (gray or faint or rot or rep_page or rep_cross):
            return True
        if rot:
            # 旋转浅灰不等于水印：图表轴标签也经常旋转。要求重复，或者是
            # 明显跨越页面的大型单枚水印，避免误删单个斜置图注。
            large_center_mark = s["size"] >= 16 and s["rect"].width >= pw * 0.30
            return (gray or faint) and (rep_page or rep_cross or large_center_mark)
        if gray or faint:
            # 正立浅灰/低透明：唯一出现视为合法脚注（豁免），重复平铺才判水印
            return rep_page or rep_cross
        # 深色正立：正文/合法页眉一律保留；仅「跨页重复 + 底部页脚带 + 含分发网址」判水印
        if rep_cross and s["rect"].y0 > ph * 0.85 and _FOOTER_URL_RE.search(s["text"] or ""):
            return True
        return False

    flags = [(s, _is_wm(s)) for s in spans]

    # 正文词框（未判水印 + 深色 + 不透明 + 正立 + 有字）：溢出擦除保护基准。
    # redaction 是按矩形擦除的——旋转水印的外接框会盖住正文；这里若某水印框实质压住
    # 正文词（交叠 >30% 词面积），**跳过该框不擦**（宁可水印残留，绝不挖空正文）。
    # 对角浅灰水印已在相 1 外科层删除、正文零损；此处是「相 1 失败/其他残留」的兜底。
    # 正文词框含**深色不透明的旋转文字**（斜置轴标签也是正文）：这样水印框压住它们时同样跳过。
    body_rects = [s["rect"] for s, w in flags
                  if (not w) and s["norm"] and (not _is_light_gray(s["color"]))
                  and s["opacity"] >= _OPACITY_THRESHOLD]

    def _would_gut_body(r: fitz.Rect) -> bool:
        for br in body_rects:
            inter = r & br
            if (not inter.is_empty) and _rect_area(inter) > 0.30 * _rect_area(br):
                return True
        return False

    seen: set = set()
    unique: list = []
    for s, w in flags:
        if not w:
            continue
        r = s["rect"]
        if _would_gut_body(r):
            continue
        key = (round(r.x0), round(r.y0), round(r.x1), round(r.y1))
        if key not in seen:
            seen.add(key)
            unique.append(r)

    for r in unique:
        page.add_redact_annot(r)
    if unique:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    return len(unique)


def _remove_image_watermarks(page: fitz.Page) -> int:
    """G：图片水印。**红线：只动带透明通道(SMask)的图**——不透明图（图表/照片/logo/项目符号/
    扫描件）一律不碰，从根上杜绝误删合法插图（复审实证：仅凭「半透明」或「重复小图」删图会
    毁掉透明 PNG 图表与重复的栏目符号）。透明图再要满足一个真·水印信号才删：

      叠加式：图框**压住正文文字**（正文词落在图框内）→ 蒙层/水印戳（合法图不会盖在正文上）。
      平铺式：同一 xref 被放置 ≥N 次（合法透明插图不会平铺）。
    """
    page_area = page.rect.width * page.rect.height
    if page_area <= 0:
        return 0

    # 去水印文字后剩下的词≈正文词框（本函数在文字层之后跑）
    try:
        body_words = [fitz.Rect(w[:4]) for w in page.get_text("words")]
    except Exception:
        body_words = []

    info = {img[0]: {"smask": img[1]} for img in page.get_images(full=True)}
    if not info:
        return 0

    def _covers_body(rects) -> int:
        n = 0
        for r in rects:
            for wr in body_words:
                cx = (wr.x0 + wr.x1) / 2
                cy = (wr.y0 + wr.y1) / 2
                if r.x0 <= cx <= r.x1 and r.y0 <= cy <= r.y1:
                    n += 1
                    if n >= 4:
                        return n
        return n

    to_delete: set = set()
    for xref, meta in info.items():
        if meta["smask"] <= 0:          # 不透明 → 绝不删（护住一切合法图）
            continue
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            rects = []
        if not rects:
            continue
        tiled = len(rects) >= _REPEAT_ON_PAGE
        overlays_text = _covers_body(rects) >= 4    # ≥4 个正文词落在图框内 = 压正文
        if tiled or overlays_text:
            to_delete.add(xref)

    for xref in to_delete:
        try:
            page.delete_image(xref)
        except Exception:
            pass
    return len(to_delete)


def _dominant_raster_image(page: fitz.Page) -> "tuple[int, int, int] | None":
    """返回无文字层页面中唯一的整页主图 ``(xref, width, height)``。

    跨页栅格修复只允许作用于这种本来就是图片的页面，绝不把可搜索的文字型 PDF
    整页栅格化。页面含多张图、主图未覆盖 92% 页面、旋转 / 尺寸异常时全部跳过。
    """
    try:
        text = page.get_text("text").strip()
        if text and not _is_our_brand(text):
            return None
        if page.rotation != 0:
            return None
        page_area = _rect_area(page.rect)
        if page_area <= 0:
            return None
        dominant = []

        # replace_image() 在部分旧版 PyMuPDF 中会把新图只挂到 Resources，
        # 但内容流仍画原图。get_image_info(xrefs=True) 可能误报这个未显示
        # 的 fzImg0，导致二次去水印修到“幽灵副本”。优先按内容流里
        # 真正执行的 /Name Do 反查 xref；只有无法解析时才回退旧接口。
        painted_names: set[str] = set()
        try:
            raw = b"\n".join(
                page.parent.xref_stream(xref) or b""
                for xref in page.get_contents()
            )
            painted_names = {
                match.decode("latin-1")
                for match in re.findall(rb"/([^\s/<>{}\[\]()]+)\s+Do\b", raw)
            }
        except Exception:
            painted_names = set()

        if painted_names:
            for info in page.get_images(full=True):
                try:
                    xref, width, height, name = int(info[0]), int(info[2]), int(info[3]), str(info[7])
                    if name not in painted_names or xref <= 0 or width < 400 or height < 500:
                        continue
                    rects = page.get_image_rects(xref)
                    if any(_rect_area(rect) >= page_area * 0.92 for rect in rects):
                        dominant.append((xref, width, height))
                except Exception:
                    continue
        if not dominant:
            for info in page.get_image_info(xrefs=True):
                xref = int(info.get("xref") or 0)
                rect = fitz.Rect(info.get("bbox") or (0, 0, 0, 0))
                width = int(info.get("width") or 0)
                height = int(info.get("height") or 0)
                if (xref > 0 and width >= 400 and height >= 500
                        and _rect_area(rect) >= page_area * 0.92):
                    dominant.append((xref, width, height))
        if len(dominant) != 1:
            return None
        return dominant[0]
    except Exception:
        return None


def _find_repeated_diagonal_bands(core_mask) -> list[tuple[float, float, int, int, int]]:
    """在跨页稳定像素中找长斜排文字带，返回 ``(slope,b,x0,x1,half_width)``。

    这是轻量 Radon / Hough 思路：扫描 ±20° 左右的斜率，统计 ``y-m*x`` 投影峰。
    除了足够长、足够多像素，还要求沿 x 方向存在多个断续笔画；连续实线、水平页眉、
    页码均不会入选。候选间按共享像素做 NMS，避免同一水印被相邻斜率重复选中。
    """
    import numpy as np  # noqa: PLC0415 - 仅图片型 PDF 才加载

    height, width = core_mask.shape
    ys, xs = np.where(core_mask)
    if len(xs) < max(180, int(width * 0.35)):
        return []

    half = max(8, int(round(height / 90)))
    min_count = max(120, int(width * 0.24))
    min_span = int(width * 0.25)
    slopes = np.concatenate((np.linspace(-0.55, -0.20, 15), np.linspace(0.20, 0.55, 15)))
    candidates = []
    window = np.ones(2 * half + 1, dtype=np.int32)
    for slope in slopes:
        intercepts = np.rint(ys - slope * xs).astype(np.int32)
        offset = max(0, -int(intercepts.min()) + 2)
        hist = np.bincount(intercepts + offset)
        smooth = np.convolve(hist, window, mode="same")
        for idx in np.argsort(smooth)[::-1][:8]:
            intercept = float(int(idx) - offset)
            near = np.abs(ys - slope * xs - intercept) <= half
            count = int(near.sum())
            if count < min_count:
                continue
            x_near = xs[near]
            span = int(x_near.max() - x_near.min())
            median_y = float(np.median(ys[near]))
            if (span < min_span or median_y < height * 0.18 or median_y > height * 0.90):
                continue
            # 文字笔画在同一 x 列通常有多个稳定像素；单根图表斜线约为 1px / 列。
            # 中文水印字距很紧，不能强求很多空白 run，否则会把整句误判成连续线。
            if count / max(1, span) < 1.35:
                continue
            candidates.append((count, span, float(slope), intercept, near, x_near))

    chosen = []
    selected_points = None
    for count, span, slope, intercept, near, x_near in sorted(
        candidates, key=lambda item: (item[0], item[1]), reverse=True
    ):
        if selected_points is not None:
            overlap = int(np.count_nonzero(near & selected_points)) / max(1, count)
            if overlap > 0.60:
                continue
        selected_points = near.copy() if selected_points is None else (selected_points | near)
        chosen.append((slope, intercept, int(x_near.min()), int(x_near.max()), half))
        if len(chosen) >= 6:
            break
    return chosen


def _neighbor_light_fill(rgb):
    """用水印笔画四周的较亮像素做快速局部修复，不依赖 OpenCV / SciPy。"""
    import numpy as np  # noqa: PLC0415

    filled = rgb.copy()
    height, width = rgb.shape[:2]
    unit = max(2, int(round(width / 410)))
    offsets = []
    # 烘焙水印的中文字形常有 20-35px 宽；只看 12px 邻域会从同一笔画取回灰色，
    # 造成“检测到了但仍留半截”。多尺度取亮邻居，最终只写回严格 repair 蒙版内。
    for dist in (unit, unit * 2, unit * 3, unit * 4, unit * 6, unit * 8):
        offsets.extend((
            (dist, 0), (-dist, 0), (0, dist), (0, -dist),
            (dist, dist), (dist, -dist), (-dist, dist), (-dist, -dist),
        ))
    for dy, dx in offsets:
        sy0, sy1 = max(0, -dy), min(height, height - dy)
        sx0, sx1 = max(0, -dx), min(width, width - dx)
        dy0, dy1 = sy0 + dy, sy1 + dy
        dx0, dx1 = sx0 + dx, sx1 + dx
        np.maximum(
            filled[dy0:dy1, dx0:dx1],
            rgb[sy0:sy1, sx0:sx1],
            out=filled[dy0:dy1, dx0:dx1],
        )
    return filled


def _dilate_mask(mask, radius: int):
    """无 SciPy 的小半径布尔膨胀，用于保护深色正文字形边缘。"""
    import numpy as np  # noqa: PLC0415

    if radius <= 0:
        return mask.copy()
    height, width = mask.shape
    expanded = np.zeros_like(mask, dtype=bool)
    for dy in range(-radius, radius + 1):
        sy0, sy1 = max(0, -dy), min(height, height - dy)
        dy0, dy1 = sy0 + dy, sy1 + dy
        for dx in range(-radius, radius + 1):
            sx0, sx1 = max(0, -dx), min(width, width - dx)
            dx0, dx1 = sx0 + dx, sx1 + dx
            expanded[dy0:dy1, dx0:dx1] |= mask[sy0:sy1, sx0:sx1]
    return expanded


def _diagonal_band_geometry(shape, bands):
    """把斜排候选扩成覆盖完整字形的布尔区域。"""
    import numpy as np  # noqa: PLC0415

    height, width = shape
    geometry = np.zeros((height, width), dtype=bool)
    for slope, intercept, x0, x1, half in bands:
        pad_x = max(8, half)
        pad_y = max(12, int(round(half * 1.55)))
        start, stop = max(0, x0 - pad_x), min(width, x1 + pad_x + 1)
        for x in range(start, stop):
            center = int(round(slope * x + intercept))
            y0, y1 = max(0, center - pad_y), min(height, center + pad_y + 1)
            geometry[y0:y1, x] = True
    return geometry


def _dedupe_page_bands(bands, width: int, height: int, sign: int):
    """同一行水印会被相邻斜率重复命中；只保留纵向分离的最强 3 行。"""
    selected = []
    middle_x = width / 2
    min_gap = height * 0.085
    for band in bands:
        slope, intercept, *_ = band
        if (-1 if slope < 0 else 1) != sign:
            continue
        center_y = slope * middle_x + intercept
        if any(abs(center_y - existing_y) < min_gap for _, existing_y in selected):
            continue
        selected.append((band, center_y))
        if len(selected) >= 3:
            break
    return [band for band, _ in selected]


def _long_single_page_bands(bands, width: int):
    """找单页中横跨近半页以上的长斜排字带。

    这是对「更多一手调研纪要和海外投行研报…」固定引流水印的
    定点补漏：它会在各页大幅平移，无法通过跨页稳定模板。上游斜带检测
    已经要求浅灰、非正交角度、多个断续笔画和足够像素密度；这里再要求
    横跨至少 48% 页宽，以排除普通图表斜线与短轴标。
    """
    min_span = width * 0.48
    return [band for band in bands if band[3] - band[2] >= min_span]


def _remove_repeated_raster_watermarks(doc: fitz.Document) -> int:
    """移除图片型研报中烙进整页图像的重复斜水印。

    安全门同时满足才处理：3-60 页、至少 60% 页面为同尺寸单张整页图、无原生文字层、
    像素数受限、多数页面检测到同向的长斜排断续字形。首先用所有页面逐像素
    max/min 推导固定模板；水印逐页位移时，再用全报告主导斜率方向约束单页检测。
    仅修复跨页近乎恒定的水印核心，或当前页恰好等于“白底模板”的边缘像素；水印覆盖
    在深色正文上的像素保留，避免把正文抗锯齿误当水印擦掉。
    """
    if not _RASTER_WM_ENABLED or len(doc) < 3 or len(doc) > _RASTER_WM_MAX_PAGES:
        return 0

    try:
        import numpy as np  # noqa: PLC0415
    except Exception:
        return 0

    eligible = []
    for index, page in enumerate(doc):
        info = _dominant_raster_image(page)
        if info is not None:
            eligible.append((index, *info))
    if len(eligible) < max(3, math.ceil(len(doc) * 0.60)):
        return 0

    shapes = Counter((width, height) for _, _, width, height in eligible)
    (width, height), shape_count = shapes.most_common(1)[0]
    if (shape_count < max(3, math.ceil(len(doc) * 0.60))
            or width * height > _RASTER_WM_MAX_PIXELS):
        return 0
    targets = [row for row in eligible if row[2:] == (width, height)]
    # 同一 xref 跨页复用时 replace_image 会一次改多页，模板与逐页修复会互相污染；宁可跳过。
    if len({xref for _, xref, _, _ in targets}) != len(targets):
        return 0

    with _RASTER_WM_SEM:
        max_gray = min_gray = None
        page_bands = {}
        sign_score = {-1: 0, 1: 0}
        sign_pages = {-1: set(), 1: set()}
        for page_index, xref, _, _ in targets:
            pix = fitz.Pixmap(doc, xref)
            if pix.n < 3 or pix.width != width or pix.height != height:
                return 0
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(height, width, pix.n)[:, :, :3]
            gray = (
                rgb[:, :, 0].astype(np.uint16)
                + rgb[:, :, 1].astype(np.uint16)
                + rgb[:, :, 2].astype(np.uint16)
            ) // 3
            gray = gray.astype(np.uint8)
            if max_gray is None:
                max_gray, min_gray = gray.copy(), gray.copy()
            else:
                np.maximum(max_gray, gray, out=max_gray)
                np.minimum(min_gray, gray, out=min_gray)

            # 固定模板抓不住逐页平移的水印；单页先只找中灰色、长斜排的断续字形。
            spread = rgb.max(axis=2).astype(np.int16) - rgb.min(axis=2).astype(np.int16)
            page_core = (spread <= 18) & (gray >= 125) & (gray <= 225)
            edge = max(12, int(round(height * 0.045)))
            page_core[:edge] = False
            page_core[-edge:] = False
            detected = _find_repeated_diagonal_bands(page_core)
            page_bands[page_index] = detected
            for slope, _, x0, x1, _ in detected:
                sign = -1 if slope < 0 else 1
                sign_score[sign] += max(0, x1 - x0)
                sign_pages[sign].add(page_index)

        dominant_sign = -1 if sign_score[-1] >= sign_score[1] else 1
        other_sign = -dominant_sign
        adaptive_ok = (
            len(sign_pages[dominant_sign]) >= math.ceil(len(targets) * 0.60)
            and sign_score[dominant_sign] >= len(targets) * width * 0.30
            and sign_score[dominant_sign] >= sign_score[other_sign] * 1.35
        )
        solo_bands = {
            page_index: _long_single_page_bands(detected, width)
            for page_index, detected in page_bands.items()
        }
        solo_ok = any(solo_bands.values())

        template_gray = max_gray
        gray_range = max_gray.astype(np.int16) - min_gray.astype(np.int16)
        core = (template_gray >= 135) & (template_gray <= 220) & (gray_range <= 7)
        edge = max(12, int(round(height * 0.045)))
        core[:edge] = False
        core[-edge:] = False
        bands = _find_repeated_diagonal_bands(core)
        geometry = _diagonal_band_geometry((height, width), bands)

        # 白底上的水印边缘会让跨页最大值仍小于 255；正文/图表随页变化，最大值通常回到白色。
        template = geometry & (template_gray >= 125) & (template_gray < 255)
        opaque = template & (gray_range <= 7)
        template_pixels = int(template.sum())
        stable_ok = (
            bool(bands)
            and template_pixels >= max(150, int(width * 0.30))
            and template_pixels <= width * height * 0.035
        )
        if not stable_ok:
            template[:] = False
            opaque[:] = False
            template_pixels = 0
        if not stable_ok and not adaptive_ok and not solo_ok:
            return 0

        changed_pages = 0
        adaptive_band_count = 0
        for page_index, xref, _, _ in targets:
            pix = fitz.Pixmap(doc, xref)
            rgb = np.frombuffer(pix.samples, dtype=np.uint8).reshape(height, width, pix.n)[:, :, :3].copy()
            current_gray = (
                rgb[:, :, 0].astype(np.uint16)
                + rgb[:, :, 1].astype(np.uint16)
                + rgb[:, :, 2].astype(np.uint16)
            ) // 3
            current_delta = np.abs(current_gray.astype(np.int16) - max_gray.astype(np.int16))
            repair = opaque | (template & (current_delta <= 7))
            selected = []
            if adaptive_ok:
                selected.extend(_dedupe_page_bands(
                    page_bands.get(page_index, []), width, height, dominant_sign
                ))
                if stable_ok and selected:
                    # 固定模板已经覆盖的斜带不要再做宽松单页修复；否则正文抗锯齿会被
                    # 重复处理。自适应层只补模板没有覆盖到的、逐页移动的水印行。
                    middle_x = width / 2
                    stable_centers = [
                        slope * middle_x + intercept
                        for slope, intercept, *_ in bands
                        if (-1 if slope < 0 else 1) == dominant_sign
                    ]
                    min_gap = height * 0.085
                    selected = [
                        band for band in selected
                        if not any(
                            abs((band[0] * middle_x + band[1]) - center) < min_gap
                            for center in stable_centers
                        )
                    ]
            # 单页超长斜排引流字带不要求跨页重复；与自适应候选合并并去重。
            for band in solo_bands.get(page_index, []):
                if band not in selected:
                    selected.append(band)
            if selected:
                adaptive_geometry = _diagonal_band_geometry((height, width), selected)
                spread = (
                    rgb.max(axis=2).astype(np.int16)
                    - rgb.min(axis=2).astype(np.int16)
                )
                # 只擦斜带里的浅/中灰中性像素：保留黑色正文核心与彩色图表；
                # 上限要覆盖水印的浅色抗锯齿，否则会留下肉眼可见的“幽灵字”。
                adaptive = (
                    adaptive_geometry
                    & (spread <= 120)
                    & (current_gray >= 90)
                    & (current_gray <= 254)
                )
                # 只保留黑色核心还不够：局部填充会把字母的浅灰抗锯齿边缘
                # 一并变白，形成“断字”。把深色像素向外保护 2-3px，水印在空白处
                # 照常清理，与正文重合的极少量灰像素则宁可保留，不损坏原文。
                protect_radius = max(2, int(round(width / 700)))
                protected_body = _dilate_mask(current_gray < 105, protect_radius)
                adaptive &= ~protected_body
                if int(adaptive.sum()) <= width * height * 0.025:
                    repair |= adaptive
                    adaptive_band_count += len(selected)
            if int(repair.sum()) < 50:
                continue
            filled = _neighbor_light_fill(rgb)
            rgb[repair] = filled[repair]
            replacement = fitz.Pixmap(fitz.csRGB, width, height, rgb.tobytes(), False)
            doc[page_index].replace_image(xref, pixmap=replacement)
            changed_pages += 1

        logger.info(
            "[pdf_brand] raster cleaned pages=%d stable_bands=%d stable_pixels=%d "
            "adaptive=%s adaptive_bands=%d solo=%s sign=%+d",
            changed_pages, len(bands) if stable_ok else 0, template_pixels,
            adaptive_ok, adaptive_band_count, solo_ok, dominant_sign,
        )
        return changed_pages


def _remove_annots(page: fitz.Page) -> int:
    removed = 0
    try:
        annots = list(page.annots())
    except Exception:
        annots = []
    for annot in annots:
        try:
            atype = annot.type[1]
            info  = annot.info
            text  = (info.get("content", "") + info.get("title", "")).strip()
            # /Watermark 子类注释（PDF_ANNOT_WATERMARK=25）内容常为空、type 不是 Stamp，
            # 老逻辑漏删；子类本身即水印，直接删。
            if (atype in ("Stamp", "StampAnnot", "Watermark")
                    or _is_kw(text)
                    or (atype == "FreeText" and (annot.opacity or 1.0) < _OPACITY_THRESHOLD)):
                page.delete_annot(annot)
                removed += 1
        except Exception:
            pass
    return removed


# ── 加品牌水印 ────────────────────────────────────────────────────────────────

def _page_has_brand(page: fitz.Page) -> bool:
    """仅把带有当前 26pt 顶部遮盖带的页面视为已处理。

    旧版只有 16pt 顶栏 / 只有底栏，不能提前返回，否则露出的原水印会一直保留。
    """
    try:
        if not _is_current_brand(page.get_text("text")):
            return False
        pw = page.rect.width
        for drawing in page.get_drawings():
            rect = fitz.Rect(drawing.get("rect"))
            fill = drawing.get("fill")
            if not fill or len(fill) < 3:
                continue
            fill_matches = all(abs(float(fill[i]) - _BRAND_TOP_FILL[i]) < 0.035 for i in range(3))
            if (fill_matches
                    and rect.y0 <= 0.8
                    and rect.width >= pw - 1.0
                    and rect.height >= _BRAND_TOP_COVER_H - 0.8):
                return True
        return False
    except Exception:
        return False


def _add_brand(page: fitz.Page) -> None:
    """加顶部遮盖带和不遮挡正文的品牌页脚。

    顶部 26pt 色带完全不透明，专门覆盖原渠道字样；常规页同时在 MediaBox
    底部增加 12pt 独立色带，原页内容的坐标、比例与可选文字完全不变。
    异常 CropBox / 旋转页不改页框，但仍覆盖顶部。若已存在当前高度的我方
    顶栏则不重复添加，保证重跑幂等。
    """
    if _page_has_brand(page):
        return

    try:
        has_legacy_brand = _is_our_brand(page.get_text("text"))
    except Exception:
        has_legacy_brand = False
    footer_fs = 6.2
    footer_text_width = _brand_text_width(footer_fs)
    mb = page.mediabox
    cb = page.cropbox
    boxes_match = all(abs(a - b) < 0.1 for a, b in zip(mb, cb))
    can_extend = page.rotation == 0 and boxes_match
    # 旧版扩展 MediaBox 后，PyMuPDF 会把 CropBox 规范化为从 (0, 0)
    # 开始，因而两者坐标不再字面相等，但页面尺寸仍与 MediaBox 一致。
    # 只对已识别为我方旧品牌的正向页重绘现有底栏，不会再加高。
    can_upgrade_footer = (
        has_legacy_brand
        and page.rotation == 0
        and abs(page.rect.width - mb.width) < 0.1
        and abs(page.rect.height - mb.height) < 0.1
    )

    if can_extend and not has_legacy_brand:
        # PDF 原点在左下：下移 y0 才是在视觉底部加空间；扩 y1 会把
        # 原内容整体下推，导致顶部排版坐标变化。
        page.set_mediabox(fitz.Rect(mb.x0, mb.y0 - _BRAND_FOOTER_H, mb.x1, mb.y1))
        pw, ph = page.rect.width, page.rect.height
        band = fitz.Rect(0, ph - _BRAND_FOOTER_H, pw, ph)
        page.draw_rect(
            band,
            color=(0.82, 0.88, 0.96),
            fill=_BRAND_FOOTER_FILL,
            width=0.35,
            overlay=True,
        )
        footer_x = max(5.0, pw - footer_text_width - 7.0)
        footer_y = ph - 3.1
    elif can_upgrade_footer:
        # v2 / v3 成品可能已经带品牌页脚，但顶部遮盖带过短或缺失。
        # 不再扩 MediaBox，但会重绘旧页脚并替换为当前文案。
        pw, ph = page.rect.width, page.rect.height
        band = fitz.Rect(0, ph - _BRAND_FOOTER_H, pw, ph)
        page.draw_rect(
            band,
            color=(0.82, 0.88, 0.96),
            fill=_BRAND_FOOTER_FILL,
            width=0.35,
            overlay=True,
        )
        footer_x = max(5.0, pw - footer_text_width - 7.0)
        footer_y = ph - 3.1
    else:
        # 稀有的旋转 / 特殊裁切页：不改 page box，也不画底色盖正文。
        pw, ph = page.rect.width, page.rect.height
        footer_x = max(3.0, pw - footer_text_width - 4.0)
        footer_y = max(footer_fs + 1.0, ph - 2.0)

    # 先用 100% 不透明色带把原文件顶部的渠道文字 / 斜水印彻底盖住，
    # 再把我方品牌放在色带中央；26pt 覆盖旧 16pt 顶栏下方仍外露的一整行。
    top_band = fitz.Rect(0, 0, pw, min(_BRAND_TOP_COVER_H, ph))
    page.draw_rect(
        top_band,
        color=(0.78, 0.86, 0.95),
        fill=_BRAND_TOP_FILL,
        width=0.45,
        fill_opacity=1.0,
        overlay=True,
    )
    header_fs = 7.4
    header_text_width = _brand_text_width(header_fs)
    header_x = max(5.0, (pw - header_text_width) / 2.0)
    header_y = max(header_fs + 1.0, (_BRAND_TOP_COVER_H + header_fs) / 2.0 - 0.8)
    _insert_brand_text(
        page,
        (header_x, header_y),
        fontsize=header_fs,
        fill_opacity=0.92,
    )

    if can_extend or can_upgrade_footer or not has_legacy_brand:
        _insert_brand_text(
            page,
            (footer_x, footer_y),
            fontsize=footer_fs,
            fill_opacity=0.72 if (can_extend or can_upgrade_footer) else 0.48,
        )


# ── 相隔离的同步主流程 ────────────────────────────────────────────────────────

def _open_fitz(data: bytes) -> fitz.Document:
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if not doc.authenticate(""):          # 仅空 user 口令可解；真 user 密码放弃
            doc.close()
            raise ValueError("user-password protected PDF")
    return doc


def _process_sync(content: bytes, *, add_brand: bool = True) -> tuple[bytes, bool]:
    """去水印 + 可选品牌顶栏 / 页脚，全程内存处理（零临时文件）。

    返回 (result_bytes, ok)。ok=True 仅当完整跑通相 2（已打品牌水印）；
    ok=False 表示只能透传/部分处理——**调用方据此决定是否落盘缓存**。
    """
    # ── 相 1：pikepdf（A-D）+ 解密。失败则退回原文继续相 2 ─────────────────────
    working = content
    pikepdf_changed = False
    pdf = None
    try:
        pdf = pikepdf.open(io.BytesIO(content), password="")
        was_encrypted = bool(getattr(pdf, "is_encrypted", False))
        n_pikepdf = _remove_xobj(pdf)
        try:
            n_pikepdf += _remove_gray_rotated_text(pdf)   # 外科级删对角浅灰水印（正文零损）
        except Exception as exc:
            logger.debug("[pdf_brand] gray-rotated 外科层失败: %s", exc)
        try:
            n_pikepdf += _redirect_full_page_source_links(pdf)
        except Exception as exc:
            logger.debug("[pdf_brand] 整页来源链接改写失败: %s", exc)
        if n_pikepdf > 0 or was_encrypted:      # 有改动，或需解密 → 重存明文
            buf = io.BytesIO()
            pdf.save(buf)
            working = buf.getvalue()
            pikepdf_changed = n_pikepdf > 0
        logger.debug("[pdf_brand] pikepdf removed %d items (enc=%s)", n_pikepdf, was_encrypted)
    except Exception as exc:
        logger.warning("[pdf_brand] pikepdf 相跳过（退回原文继续）: %s", exc)
        working = content
    finally:
        if pdf is not None:
            try: pdf.close()
            except Exception: pass

    # ── 相 2：PyMuPDF（E-G + 品牌水印）。失败则尽力返回相 1 产物 ────────────────
    doc = None
    try:
        try:
            doc = _open_fitz(working)
        except Exception:
            doc = _open_fitz(content)           # working 打不开就用原文再试
            working = content
        try:
            _remove_repeated_raster_watermarks(doc)
        except Exception as exc:
            logger.debug("[pdf_brand] 跨页图片模板层失败: %s", exc)
        spans_by_page, doc_presence, npages = _gather_text_spans(doc)
        for i, page in enumerate(doc):
            try:
                _redact_text_watermarks(page, spans_by_page[i], doc_presence, npages)
            except Exception as exc:
                logger.debug("[pdf_brand] 文字层第 %d 页失败: %s", i, exc)
            try:
                _remove_annots(page)
            except Exception as exc:
                logger.debug("[pdf_brand] 注释层第 %d 页失败: %s", i, exc)
            try:
                _remove_image_watermarks(page)
            except Exception as exc:
                logger.debug("[pdf_brand] 图片层第 %d 页失败: %s", i, exc)
            if add_brand:
                try:
                    _add_brand(page)
                except Exception as exc:
                    logger.debug("[pdf_brand] 品牌标记第 %d 页失败: %s", i, exc)
        result = doc.tobytes(garbage=2, deflate=True)
        doc.close()
        return result, True
    except Exception as exc:
        logger.warning("[pdf_brand] PyMuPDF 相失败，退回相 1 产物: %s", exc)
        if doc is not None:
            try: doc.close()
            except Exception: pass
        # 至少交付「解密 + A-D 切除」的相 1 产物；若相 1 也没动，就是原文透传
        return working, pikepdf_changed


# ── 异步公开接口 ──────────────────────────────────────────────────────────────

async def apply_pdf_brand(content: bytes, *, file_id: str = "") -> bytes:
    """主入口：去水印 + 品牌水印。缓存：内存 LRU → 磁盘 → 实时处理。
    file_id 可选：传入后额外建立 fid_*.pdf 索引，供 get_cached_by_file_id 跳过下载。

    稳健性：仅当处理成功（ok=True）才写缓存；失败（原样/部分透传）不落盘，
    避免把带水印原文永久钉进缓存，下次仍可重试。"""
    if len(content) < 1024:
        return content

    key = f"{hashlib.sha256(content).hexdigest()[:20]}_{_PROC_VERSION}_links1"

    if key in _MEM_CACHE:
        _MEM_CACHE.move_to_end(key)
        return _MEM_CACHE[key]

    cache_file = None
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _sweep_stale_cache_once()
        cache_file = _CACHE_DIR / f"{key}.pdf"
        if cache_file.exists():
            data = cache_file.read_bytes()
            _mem_put(key, data)
            if file_id:
                _mem_put(_fid_mem_key(file_id), data)
            return data
    except Exception:
        pass

    loop = asyncio.get_event_loop()
    result, ok = await loop.run_in_executor(None, _process_sync, content)

    if not ok:
        # 未成功：直接返回尽力产物，不写任何缓存（下次重试）
        return result

    _mem_put(key, result)
    try:
        if file_id:
            # 有 file_id（知识星球研报）只落 fid 成品。此前 sha+fid 双写让每篇研报在磁盘
            # 存两份相同字节（实测 2.1GB 里 1.1GB 是孪生副本）；fid 是这类 PDF 唯一的磁盘热路径，
            # sha 盘缓存仅服务无 fid 的调用方（东财代理），互不交叉。
            _fid_cache_path(file_id).write_bytes(result)
            _mem_put(_fid_mem_key(file_id), result)
        elif cache_file is not None:
            cache_file.write_bytes(result)
    except Exception:
        pass

    return result


async def prewarm_pdf(content: bytes) -> None:
    """后台预热单个 PDF，限并发 3 个。"""
    global _PREWARM_SEM
    if _PREWARM_SEM is None:
        _PREWARM_SEM = asyncio.Semaphore(3)
    if len(content) < 1024:
        return
    key = f"{hashlib.sha256(content).hexdigest()[:20]}_{_PROC_VERSION}"
    if key in _MEM_CACHE:
        return
    try:
        if (_CACHE_DIR / f"{key}.pdf").exists():
            return
    except Exception:
        pass
    async with _PREWARM_SEM:
        try:
            await apply_pdf_brand(content)
        except Exception as e:
            logger.debug("[pdf_brand] prewarm err: %s", e)
