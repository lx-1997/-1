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
  相 2 PyMuPDF（渲染级，物理擦除 + 打品牌水印）
    E. 文字水印：关键词/正则 · 旋转 · 浅灰/低透明 · **重复平铺**（跨 span/跨页去重）
    F. 注释水印：Stamp / 低透明 FreeText / 关键词
    G. 图片水印：半透明覆盖 · **平铺小图** · **整页半透明蒙层（正文页才删，扫描件不动）** · 角落二维码

稳健性红线（本次强化）:
  - 单相 try/except 隔离；任一相失败仍尽力产出（至少解密 + 打品牌水印）。
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
from collections import Counter, OrderedDict
from pathlib import Path

import fitz
import pikepdf

logger = logging.getLogger(__name__)

_CACHE_DIR = Path(os.getenv("PDF_CACHE_DIR", "/opt/deepfocus/pdf_cache"))

# 去水印/打标逻辑版本号。**改动去水印或品牌逻辑时 +1**：并入缓存键，
# 使已缓存的旧成品自动失效重跑（否则老 PDF 会一直回放旧的弱去水印结果）。
_PROC_VERSION = os.getenv("PDF_BRAND_PROC_VERSION", "v2")
_SWEPT = False

# ── 内存 LRU ──────────────────────────────────────────────────────────────────
_MEM_CACHE: "OrderedDict[str, bytes]" = OrderedDict()
_MEM_CACHE_MAX = int(os.getenv("PDF_BRAND_MEM_CACHE", "100"))

# ── 预热已见集合 ──────────────────────────────────────────────────────────────
_PREWARM_SEEN: set = set()
_PREWARM_SEM: asyncio.Semaphore | None = None

# ── 水印关键词（子串匹配）────────────────────────────────────────────────────
# **高精度红线**：关键词命中会直接物理擦除该文字行（含 apply_redactions 的溢出擦除），
# 故只收「研报正文几乎不可能出现」的分发渠道/社群词。**严禁**放泛词：
#   已剔除 sample(→samples)、内部(→内部收益率)、仅供参考(每篇免责声明都有)、
#   海外投行研报(产品自身来源标签)、vip、主播、draft、保密(保密协议) 等——
#   这些会误删正文；真水印靠「浅灰/旋转/重复平铺」等信号兜住，不依赖泛关键词。
_WATERMARK_KEYWORDS = [
    "confidential", "not for distribution", "do not distribute", "do not redistribute",
    "知识星球", "加入星球", "加入知识星球", "水木纪要", "shuimu", "水木2026",
    "扫码关注", "扫码添加", "扫码进群", "长按识别", "长按二维码", "识别二维码",
    "不得转载", "翻版必究", "严禁外传", "禁止外传", "禁止传播", "严禁传播",
    "大家好我是",
    "加v看", "加V看", "加v：", "加v:", "加V：", "加V:", "加微信", "薇信",
]
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

# Aspose Artifact BDC...EMC 正则
_ARTIFACT_WM_RE = re.compile(
    rb'/Artifact\s*<<[^>]*?/Subtype\s*/Watermark[^>]*?>>\s*BDC\s*q\s*.*?Q\s*EMC',
    re.DOTALL,
)

# 可疑 XObject 名称
_SUSPICIOUS_XOBJ_RE = [
    re.compile(r"(?i)^/Wm\d*$"),
    re.compile(r"(?i)^/Watermark"),
    re.compile(r"(?i)^/stamp", re.I),
]

# 品牌水印配置
_HEADER_H    = 16
_HEADER_FILL = (0.93, 0.96, 1.0)
_TEXT_COLOR  = (0.25, 0.35, 0.55)
_DIAG_COLOR  = (0.78, 0.78, 0.78)
_DIAG_ANGLE  = 20
_DIAG_FS     = 11
_DIAG_SX     = 240
_DIAG_SY     = 140


# ── 辅助 ─────────────────────────────────────────────────────────────────────

def _norm(text: str) -> str:
    """归一化文字（折叠空白）——用于重复平铺计数。"""
    return re.sub(r"\s+", " ", (text or "").strip())

def _is_kw(text: str) -> bool:
    """关键词子串 或 高信号正则 命中。"""
    t = (text or "").lower().strip()
    if not t:
        return False
    if any(k in t for k in _WATERMARK_KEYWORDS):
        return True
    return any(rx.search(text or "") for rx in _WATERMARK_REGEXES)

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
    return _CACHE_DIR / f"fid_{_PROC_VERSION}_{safe}.pdf"


def _fid_mem_key(file_id: str) -> str:
    return f"fid:{_PROC_VERSION}:{file_id}"


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
    removed = 0
    xobj_names: list[str] = []
    skip_bytes: list[tuple[int, int]] = []  # (start_byte, end_byte)

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
                skip_bytes.append((toks[i][1], toks[j-1][2]))
                xobj_names.extend(do_found)
                removed += 1

        i = j

    if not skip_bytes:
        return raw, 0

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
        stack: list = []
        kept: list = []
        removed = 0
        for ins in insns:
            op = str(ins.operator)
            od = ins.operands
            if op == "q":
                stack.append((list(ctm), fill_gray))
            elif op == "Q":
                if stack:
                    saved_ctm, fill_gray = stack.pop()
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
                except Exception:
                    fill_gray = False
            elif op == "g":
                try:
                    fill_gray = float(od[0]) > 0.55
                except Exception:
                    fill_gray = False
            elif op in ("cs", "sc", "scn", "k"):
                fill_gray = False          # 未知/非灰阶色空间：宁可不删
            elif op == "BT":
                tm = [1.0, 0.0, 0.0, 1.0]
            elif op == "Tm":
                try:
                    t = [float(x) for x in od]
                    tm = t[:4]
                except Exception:
                    tm = [1.0, 0.0, 0.0, 1.0]

            if op in _SHOW_OPS:
                comb = _mat2_mul(tm, ctm)
                if fill_gray and _is_wm_rotation(*comb):
                    removed += 1
                    continue               # 丢弃这条水印显示指令
            kept.append(ins)

        if removed:
            try:
                page.obj["/Contents"] = pdf.make_stream(
                    pikepdf.unparse_content_stream(kept))
                total += removed
            except Exception:
                pass
    return total


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
    ph = page.rect.height or 1.0

    def _is_wm(s: dict) -> bool:
        norm = s["norm"]
        gray = _is_light_gray(s["color"])
        faint = s["opacity"] < _OPACITY_THRESHOLD
        rot = s["is_rot"]
        kw = _is_kw(s["text"])
        long_enough = len(norm) >= _MIN_WM_LEN
        whitelisted = any(w in norm for w in _LEGIT_REPEAT)
        rep_page = bool(norm) and long_enough and not whitelisted and page_counts[norm] >= _REPEAT_ON_PAGE
        rep_cross = (bool(norm) and long_enough and not whitelisted
                     and npages >= _MIN_PAGES_FOR_CROSS
                     and doc_presence.get(norm, 0) >= cross_thresh)

        if kw:
            return True
        if rot:
            # 旋转文字：**只按 浅灰/低透明 判**。深色斜置文字（图表类目轴标签、斜置图表标题，
            # 无论字号大小、是否重复）一律保留——重复/大字号都不是水印信号（合法轴标签也重复）。
            return gray or faint
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
            if (not inter.is_empty) and inter.get_area() > 0.30 * br.get_area():
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

def _add_brand(page: fitz.Page) -> None:
    pw, ph = page.rect.width, page.rect.height
    page.draw_rect(fitz.Rect(0, 0, pw, _HEADER_H),
                   color=None, fill=_HEADER_FILL, overlay=True)
    SEG_FS = 8
    segments = [
        ("更多投研内容", "china-s"),
        ("  |  DeepFocus", "helv"),
        ("深度焦点",      "china-s"),
        ("  |  www.daocaijing.com", "helv"),
    ]
    total_w = sum(fitz.get_text_length(t, fontname=f, fontsize=SEG_FS)
                  for t, f in segments)
    x = pw / 2 - total_w / 2
    y_text = _HEADER_H - 3
    for seg_text, seg_font in segments:
        seg_w = fitz.get_text_length(seg_text, fontname=seg_font, fontsize=SEG_FS)
        page.insert_text((x, y_text), seg_text, fontsize=SEG_FS,
                         fontname=seg_font, color=_TEXT_COLOR, overlay=True)
        x += seg_w
    rad   = math.radians(_DIAG_ANGLE)
    cos_a = math.cos(rad)
    sin_a = math.sin(rad)
    mat   = fitz.Matrix(cos_a, -sin_a, sin_a, cos_a, 0, 0)
    short = "www.daocaijing.com"
    for xi in range(-1, int(pw / _DIAG_SX) + 2):
        for yi in range(-1, int(ph / _DIAG_SY) + 2):
            ax = xi * _DIAG_SX; ay = yi * _DIAG_SY
            px_ = ax * cos_a - ay * sin_a
            py_ = ax * sin_a + ay * cos_a
            if px_ < -100 or px_ > pw + 100 or py_ < -100 or py_ > ph + 100:
                continue
            pivot = fitz.Point(px_, py_)
            page.insert_text(pivot, short, fontsize=_DIAG_FS, fontname="helv",
                             color=_DIAG_COLOR, morph=(pivot, mat), overlay=True)


# ── 相隔离的同步主流程 ────────────────────────────────────────────────────────

def _open_fitz(data: bytes) -> fitz.Document:
    doc = fitz.open(stream=data, filetype="pdf")
    if doc.needs_pass:
        if not doc.authenticate(""):          # 仅空 user 口令可解；真 user 密码放弃
            doc.close()
            raise ValueError("user-password protected PDF")
    return doc


def _process_sync(content: bytes) -> tuple[bytes, bool]:
    """去水印 + 品牌水印，全程内存处理（零临时文件）。

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
            try:
                _add_brand(page)
            except Exception as exc:
                logger.debug("[pdf_brand] 品牌水印第 %d 页失败: %s", i, exc)
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

    key = f"{hashlib.sha256(content).hexdigest()[:20]}_{_PROC_VERSION}"

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
