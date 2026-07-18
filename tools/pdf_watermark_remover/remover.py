"""
PDF 去水印 + 打品牌水印  ——  pikepdf + PyMuPDF 双引擎

去水印支持:
  A. 文字水印 (旋转 / 关键词匹配 / 半透明)
  B. 图片水印 (覆盖 70%+ 页面 / 极小图标)
  C. 注释水印 (FreeText / Stamp)
  D. Artifact XObject 水印 (Aspose 系 BDC...EMC 块)
  E. 可疑名称 XObject (/Wm* /stamp* 等)

加水印支持:
  顶部 header 条 + 对角线平铺文字

用法:
  python remover.py input.pdf [output.pdf] [-v]
  python remover.py input.pdf -w                      # 去水印 + 打 daocaijing 水印
  python remover.py input.pdf -w --wm-text "自定义"  # 自定义水印文字
  python remover.py folder/                           # 批量处理目录
"""

import math
import os
import re
import argparse
from pathlib import Path

import fitz
import pikepdf


# ─── 配置 ────────────────────────────────────────────────────────────────────

OPACITY_THRESHOLD = 0.85

WATERMARK_KEYWORDS = [
    # 英文通用
    "confidential", "draft", "watermark", "sample",
    "for review", "not for distribution", "do not distribute",
    # 中文通用
    "机密", "内部", "草稿", "仅供参考", "保密",
    # 中文研报分发渠道水印（知识星球 / 公众号 / 微信群）
    "知识星球", "公众号", "调研纪要", "加入群", "获取更多",
    "不得转载", "翻版必究", "更多资料", "水木纪要",
]

_ARTIFACT_WM_RE = re.compile(
    rb'/Artifact\s*<<[^>]*?/Subtype\s*/Watermark[^>]*?>>\s*BDC\s*q\s*.*?Q\s*EMC',
    re.DOTALL,
)

_SUSPICIOUS_XOBJ_RE = [
    re.compile(r"(?i)^/Wm\d*$"),
    re.compile(r"(?i)^/Watermark"),
    re.compile(r"(?i)^/stamp", re.I),
]

# 品牌水印默认配置
DEFAULT_WM_TEXT   = "更多投研内容 | DeepFocus深度焦点 | www.daocaijing.com"
WM_HEADER_HEIGHT  = 16     # px，顶部 header 条高度
WM_HEADER_COLOR   = (0.93, 0.96, 1.0)   # 浅蓝背景
WM_TEXT_COLOR     = (0.25, 0.35, 0.55)  # 深蓝文字
WM_DIAG_COLOR     = (0.78, 0.78, 0.78)  # 对角线水印灰色
WM_DIAG_OPACITY   = 0.30   # 对角线透明度
WM_DIAG_ANGLE     = 20     # 对角线角度（度）
WM_DIAG_FONTSIZE  = 11
WM_DIAG_SPACING_X = 240    # 水平平铺间距
WM_DIAG_SPACING_Y = 140    # 垂直平铺间距


# ─── 辅助工具 ────────────────────────────────────────────────────────────────

def _is_watermark_keyword(text: str) -> bool:
    t = text.lower().strip()
    return any(kw in t for kw in WATERMARK_KEYWORDS)


def _is_suspicious_xobj(name: str) -> bool:
    return any(p.match(name) for p in _SUSPICIOUS_XOBJ_RE)


def _is_very_light_gray(packed_color: int) -> bool:
    """纯浅灰 (#bfbfbf / #9b9b9b 等)：亮度 >150 且三通道差距 <40。"""
    r = (packed_color >> 16) & 0xFF
    g = (packed_color >> 8)  & 0xFF
    b =  packed_color        & 0xFF
    return (r + g + b) / 3 > 150 and max(r, g, b) - min(r, g, b) < 40


# ─── 去水印：文字（PyMuPDF）────────────────────────────────────────────────────

def remove_text_watermarks(page: fitz.Page, verbose: bool = False) -> int:
    """物理擦除文字水印（redact 覆白）。"""
    rects = []

    # ① 关键词命中（words 接口能解码自定义字体）
    for w in page.get_text("words"):
        if _is_watermark_keyword(w[4]):
            rects.append(fitz.Rect(w[:4]))

    # ② rawdict 扫描：旋转角度异常 / 低透明度+浅灰色
    for block in page.get_text("rawdict", flags=fitz.TEXT_PRESERVE_WHITESPACE)["blocks"]:
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            d = line.get("dir", (1, 0))
            angle = round(math.degrees(math.atan2(-d[1], d[0])))
            is_rotated = angle not in (0, 90, -90, 180, -180)
            for span in line.get("spans", []):
                color   = span.get("color", 0)
                opacity = span.get("opacity", 1.0) if "opacity" in span else 1.0
                hit = (
                    is_rotated
                    or _is_watermark_keyword(span.get("text", ""))
                    or (opacity < OPACITY_THRESHOLD and _is_very_light_gray(color))
                )
                if hit:
                    r = fitz.Rect(span["bbox"])
                    if r.width > 1 and r.height > 1:
                        rects.append(r)
                        if verbose:
                            reasons = []
                            if is_rotated: reasons.append(f"旋转{angle}°")
                            if _is_watermark_keyword(span.get("text","")): reasons.append("关键词")
                            if opacity < OPACITY_THRESHOLD: reasons.append(f"低透明{opacity:.2f}")
                            if _is_very_light_gray(color): reasons.append(f"浅灰#{color:06x}")
                            print(f"    [文字] {', '.join(reasons)}")

    for r in rects:
        page.add_redact_annot(r)
    if rects:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
    return len(rects)


# ─── 去水印：注释（PyMuPDF）────────────────────────────────────────────────────

def remove_annotation_watermarks(page: fitz.Page, verbose: bool = False) -> int:
    removed = 0
    for annot in list(page.annots()):
        atype = annot.type[1]
        info  = annot.info
        text  = (info.get("content", "") + info.get("title", "")).strip()
        is_stamp   = atype in ("Stamp", "StampAnnot", "Stamp Annot")
        is_kw      = _is_watermark_keyword(text)
        is_ft_low  = atype == "FreeText" and (annot.opacity or 1.0) < OPACITY_THRESHOLD
        if is_stamp or is_kw or is_ft_low:
            if verbose:
                print(f"    [注释] type={atype} text={text!r}")
            page.delete_annot(annot)
            removed += 1
    return removed


# ─── 去水印：图片（PyMuPDF）────────────────────────────────────────────────────

def remove_image_watermarks(page: fitz.Page, verbose: bool = False) -> int:
    removed = 0
    page_area = page.rect.width * page.rect.height
    for img in page.get_images(full=True):
        xref  = img[0]
        iw, ih = img[2], img[3]
        for rect in page.get_image_rects(xref):
            cov  = rect.width * rect.height / page_area if page_area else 0
            tiny = iw < 50 or ih < 50
            if cov > 0.70 or (tiny and cov > 0.02):
                reason = f"全页覆盖{cov:.0%}" if cov > 0.70 else f"极小图标{iw}x{ih}"
                if verbose:
                    print(f"    [图片] xref={xref} {reason}")
                page.delete_image(xref)
                removed += 1
    return removed


# ─── 去水印：XObject / Artifact（pikepdf）─────────────────────────────────────

def remove_xobject_watermarks(pdf: pikepdf.Pdf, verbose: bool = False) -> int:
    """
    双策略:
    1. 从内容流切除 /Artifact /Subtype/Watermark BDC...EMC 段（Aspose 水印）
    2. 删除名称可疑的 XObject 及其 Do 调用
    """
    total = 0

    for pn, page in enumerate(pdf.pages):
        res   = page.get("/Resources")
        xobjs = res.get("/XObject") if res else None
        contents = page.obj.get("/Contents")
        if contents is None:
            continue

        items = list(contents) if isinstance(contents, pikepdf.Array) else [contents]

        # 策略 1：Artifact Watermark BDC...EMC
        page_removed = 0
        last_raw = b""
        for stream in items:
            try:
                raw = stream.read_bytes()
                last_raw = raw
                cleaned = _ARTIFACT_WM_RE.sub(b"", raw)
                # 清理空白 OL overlay 调用
                cleaned = re.sub(
                    rb'q\s+1\s+0\s+0\s+1\s+0\s+0\s+cm\s+/OL\d+\s+Do\s+Q\s+Q',
                    b"", cleaned,
                )
                if len(cleaned) != len(raw):
                    stream.write(cleaned)
                    n = len(_ARTIFACT_WM_RE.findall(raw))
                    page_removed += n
                    if verbose and n:
                        print(f"    [Artifact] 页{pn+1} 删除 {n} 块")
            except Exception:
                pass

        # 删除被切掉块引用的 /Fm Form XObject（精确匹配，不误删页眉/logo）
        if page_removed and xobjs:
            wm_fm = set()
            for blk in _ARTIFACT_WM_RE.findall(last_raw):
                for fm in re.findall(rb'/(Fm\d+)\s+Do', blk):
                    wm_fm.add("/" + fm.decode())
            for name in wm_fm:
                try:
                    del xobjs[name]
                except Exception:
                    pass
        total += page_removed

        # 策略 2：可疑名称 XObject
        if xobjs:
            to_del = [k for k in xobjs if _is_suspicious_xobj(str(k))]
            for name in to_del:
                if verbose:
                    print(f"    [XObject] 页{pn+1} 删除 {name}")
                del xobjs[name]
                _strip_do_call(page, str(name)[1:])
            total += len(to_del)

    return total


def _strip_do_call(page: pikepdf.Page, xobj_name: str):
    pat = re.compile(rb'(?m)^\s*/' + re.escape(xobj_name.encode()) + rb'\s+Do\s*$')
    contents = page.obj.get("/Contents")
    if contents is None:
        return
    items = list(contents) if isinstance(contents, pikepdf.Array) else [contents]
    for s in items:
        try:
            s.write(pat.sub(b"", s.read_bytes()))
        except Exception:
            pass


# ─── 加水印：daocaijing.com 品牌水印 ─────────────────────────────────────────

def add_brand_watermark(page: fitz.Page, text: str = DEFAULT_WM_TEXT) -> None:
    """
    在每页添加两层品牌水印：
    1. 顶部 header 条（浅蓝底 + 深蓝字）
    2. 对角线平铺浅灰文字（20°，仿研报常见格式）
    """
    pw, ph = page.rect.width, page.rect.height

    # ── 1. 顶部 header 条 ──────────────────────────────────────
    header_rect = fitz.Rect(0, 0, pw, WM_HEADER_HEIGHT)
    page.draw_rect(header_rect, color=None, fill=WM_HEADER_COLOR, overlay=True)

    # 分四段，中文 china-s / ASCII helv 各用各的字体（避免 china-s 全角间距）
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
    y_text = WM_HEADER_HEIGHT - 3
    for seg_text, seg_font in segments:
        seg_w = fitz.get_text_length(seg_text, fontname=seg_font, fontsize=SEG_FS)
        page.insert_text((x, y_text), seg_text, fontsize=SEG_FS,
                         fontname=seg_font, color=WM_TEXT_COLOR, overlay=True)
        x += seg_w

    # ── 2. 对角线平铺文字 ───────────────────────────────────────
    rad    = math.radians(WM_DIAG_ANGLE)
    cos_a  = math.cos(rad)
    sin_a  = math.sin(rad)
    # 旋转矩阵（顺时针 WM_DIAG_ANGLE 度）
    mat = fitz.Matrix(cos_a, -sin_a, sin_a, cos_a, 0, 0)

    # 短显示文本（对角线只用域名，不重复长串）
    short = "www.daocaijing.com"

    for xi in range(-1, int(pw / WM_DIAG_SPACING_X) + 2):
        for yi in range(-1, int(ph / WM_DIAG_SPACING_Y) + 2):
            # 平铺锚点（未旋转坐标系）
            ax = xi * WM_DIAG_SPACING_X
            ay = yi * WM_DIAG_SPACING_Y
            # 旋转后的实际页面坐标
            px_ = ax * cos_a - ay * sin_a
            py_ = ax * sin_a + ay * cos_a
            if px_ < -100 or px_ > pw + 100 or py_ < -100 or py_ > ph + 100:
                continue
            pivot = fitz.Point(px_, py_)
            page.insert_text(
                pivot,
                short,
                fontsize=WM_DIAG_FONTSIZE,
                fontname="helv",
                color=WM_DIAG_COLOR,
                morph=(pivot, mat),
                overlay=True,
            )


# ─── 主流程 ──────────────────────────────────────────────────────────────────

def process_pdf(
    input_path: str,
    output_path: str = None,
    add_watermark: bool = False,
    wm_text: str = DEFAULT_WM_TEXT,
    verbose: bool = False,
) -> dict:
    if output_path is None:
        stem = Path(input_path).stem
        suffix = "_daocaijing" if add_watermark else "_clean"
        output_path = str(Path(input_path).parent / f"{stem}{suffix}.pdf")

    print(f"\n{'─'*55}")
    print(f"输入: {Path(input_path).name}")

    stats = dict(pages=0, text=0, annot=0, xobj=0, image=0)
    tmp = output_path + ".tmp.pdf"

    # 步骤 1：pikepdf — Artifact / XObject 水印外科切除
    pdf = pikepdf.open(input_path)
    stats["xobj"] = remove_xobject_watermarks(pdf, verbose)
    pdf.save(tmp)
    pdf.close()

    # 步骤 2：PyMuPDF — 文字 / 注释 / 图片水印 + 可选打品牌水印
    doc = fitz.open(tmp)
    stats["pages"] = len(doc)
    for page in doc:
        stats["text"]  += remove_text_watermarks(page, verbose)
        stats["annot"] += remove_annotation_watermarks(page, verbose)
        stats["image"] += remove_image_watermarks(page, verbose)
        if add_watermark:
            add_brand_watermark(page, wm_text)

    doc.save(output_path, garbage=4, deflate=True)
    doc.close()
    os.remove(tmp)

    total    = sum(stats[k] for k in ("text", "annot", "xobj", "image"))
    orig_kb  = os.path.getsize(input_path) // 1024
    clean_kb = os.path.getsize(output_path) // 1024
    print(f"输出: {Path(output_path).name}")
    print(f"  页数={stats['pages']}  文字={stats['text']}  注释={stats['annot']}  "
          f"XObject={stats['xobj']}  图片={stats['image']}  "
          + (f"[已打品牌水印]" if add_watermark else ""))
    print(f"  大小: {orig_kb} KB → {clean_kb} KB  (去除 {total} 项)")
    return stats


def process_folder(
    folder: str,
    add_watermark: bool = False,
    wm_text: str = DEFAULT_WM_TEXT,
    verbose: bool = False,
):
    pdfs = [p for p in Path(folder).glob("*.pdf")
            if not p.stem.endswith(("_clean", "_daocaijing"))]
    print(f"批量处理 {len(pdfs)} 个 PDF ...")
    for p in pdfs:
        process_pdf(str(p), add_watermark=add_watermark,
                    wm_text=wm_text, verbose=verbose)


# ─── CLI ─────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="PDF 去水印 + 打 daocaijing 品牌水印",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  python remover.py report.pdf                   # 仅去水印
  python remover.py report.pdf -w                # 去水印 + 打品牌水印
  python remover.py report.pdf out.pdf -w -v     # 指定输出 + 详细日志
  python remover.py reports/                     # 批量去水印
  python remover.py reports/ -w                  # 批量去 + 打水印""",
    )
    ap.add_argument("input",     help="输入 PDF 文件或目录")
    ap.add_argument("output",    nargs="?", help="输出 PDF（可选）")
    ap.add_argument("-w", "--add-wm",   action="store_true", help="打 daocaijing 品牌水印")
    ap.add_argument("--wm-text",        default=DEFAULT_WM_TEXT, help="自定义水印文字")
    ap.add_argument("-v", "--verbose",  action="store_true",  help="详细日志")
    args = ap.parse_args()

    if os.path.isdir(args.input):
        process_folder(args.input, args.add_wm, args.wm_text, args.verbose)
    else:
        process_pdf(args.input, args.output, args.add_wm, args.wm_text, args.verbose)
