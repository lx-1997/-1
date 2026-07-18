"""
创建包含不同类型水印的测试 PDF，用于验证去水印工具
"""
import fitz
import math
import os

OUT_DIR = os.path.dirname(__file__)


def make_base_page(doc: fitz.Document) -> fitz.Page:
    page = doc.new_page(width=595, height=842)
    # 正文
    page.insert_text((72, 100), "这是一份财务分析报告", fontsize=18, color=(0, 0, 0))
    for i, line in enumerate([
        "• 营业收入：120亿元，同比增长 23%",
        "• 净利润：18亿元，利润率 15%",
        "• 研发投入：9.6亿元，占营收 8%",
        "• 每股收益：2.35元",
    ]):
        page.insert_text((72, 160 + i * 28), line, fontsize=12, color=(0.1, 0.1, 0.1))
    return page


def create_text_watermark_pdf():
    """类型 A: 斜向文字水印（重复平铺）"""
    doc = fitz.open()
    page = make_base_page(doc)
    # 用灰色文字铺满（PyMuPDF insert_text rotate 只接受 0/90/180/270）
    # 改用 insert_textbox + morph 实现斜向
    for x in range(50, 560, 200):
        for y in range(120, 820, 160):
            rect = fitz.Rect(x, y, x + 200, y + 50)
            page.insert_textbox(
                rect,
                "CONFIDENTIAL",
                fontsize=24,
                color=(0.75, 0.75, 0.75),
                align=fitz.TEXT_ALIGN_CENTER,
            )
    path = os.path.join(OUT_DIR, "test_text_watermark.pdf")
    doc.save(path)
    doc.close()
    print(f"[A] 文字水印 → {path}")
    return path


def create_image_watermark_pdf():
    """类型 B: 图片水印（XObject 形式）"""
    doc = fitz.open()
    page = make_base_page(doc)

    # 先生成一个水印 PNG（用 fitz 绘制）
    wm_doc = fitz.open()
    wm_page = wm_doc.new_page(width=200, height=80)
    wm_page.insert_text((10, 55), "★ 内部使用 ★", fontsize=22, color=(0.8, 0.2, 0.2))
    wm_png = os.path.join(OUT_DIR, "_wm_tmp.png")
    wm_page.get_pixmap(alpha=True).save(wm_png)
    wm_doc.close()

    # 将 PNG 嵌入为图片水印
    rect = fitz.Rect(150, 350, 450, 490)
    page.insert_image(rect, filename=wm_png, overlay=True)

    path = os.path.join(OUT_DIR, "test_image_watermark.pdf")
    doc.save(path)
    doc.close()
    os.remove(wm_png)
    print(f"[B] 图片水印 → {path}")
    return path


def create_annotation_watermark_pdf():
    """类型 C: 注释层水印（FreeText Annot）"""
    doc = fitz.open()
    page = make_base_page(doc)
    annot = page.add_freetext_annot(
        fitz.Rect(100, 300, 500, 400),
        "★ 草稿 DRAFT ★",
        fontsize=36,
        text_color=(1.0, 0.0, 0.0),
        fill_color=(1, 1, 1, 0),
    )
    annot.set_opacity(0.4)
    annot.update()
    path = os.path.join(OUT_DIR, "test_annot_watermark.pdf")
    doc.save(path)
    doc.close()
    print(f"[C] 注释水印 → {path}")
    return path


def create_stamp_watermark_pdf():
    """类型 D: StampAnnot（图章水印）"""
    doc = fitz.open()
    page = make_base_page(doc)
    # 用 Stamp 注释
    rect = fitz.Rect(150, 200, 450, 320)
    annot = page.add_stamp_annot(rect, stamp=0)  # 0 = "Approved"
    annot.set_opacity(0.5)
    annot.update()
    path = os.path.join(OUT_DIR, "test_stamp_watermark.pdf")
    doc.save(path)
    doc.close()
    print(f"[D] 图章水印 → {path}")
    return path


def create_overlay_xobject_pdf():
    """类型 E: pikepdf 方式 —— /Watermark XObject 嵌入内容流"""
    import pikepdf
    from pikepdf import Pdf, Dictionary, Array, Name, Stream

    doc = fitz.open()
    make_base_page(doc)
    tmp = os.path.join(OUT_DIR, "_tmp_base.pdf")
    doc.save(tmp)
    doc.close()

    pdf = pikepdf.open(tmp)
    page = pdf.pages[0]

    # 构造一个简单的 XObject 水印
    wm_stream = b"BT /F1 40 Tf 0.5 g 100 400 Td (WATERMARK) Tj ET"
    xobj = Stream(pdf, wm_stream)
    xobj.stream_dict = Dictionary(
        Type=Name("/XObject"),
        Subtype=Name("/Form"),
        BBox=Array([0, 0, 595, 842]),
    )
    if "/Resources" not in page:
        page["/Resources"] = Dictionary()
    res = page["/Resources"]
    if "/XObject" not in res:
        res["/XObject"] = Dictionary()
    res["/XObject"]["/Wm0"] = xobj

    # 在页面内容流末尾调用
    existing = page.obj.get("/Contents")
    extra = Stream(pdf, b" /Wm0 Do ")
    if existing is not None:
        if isinstance(existing, pikepdf.Array):
            existing.append(extra)
        else:
            page["/Contents"] = Array([existing, extra])
    else:
        page["/Contents"] = extra

    path = os.path.join(OUT_DIR, "test_xobject_watermark.pdf")
    pdf.save(path)
    pdf.close()
    os.remove(tmp)
    print(f"[E] XObject水印 → {path}")
    return path


if __name__ == "__main__":
    os.makedirs(OUT_DIR, exist_ok=True)
    paths = []
    paths.append(create_text_watermark_pdf())
    paths.append(create_image_watermark_pdf())
    paths.append(create_annotation_watermark_pdf())
    paths.append(create_stamp_watermark_pdf())
    paths.append(create_overlay_xobject_pdf())
    print(f"\n✅ 共创建 {len(paths)} 个测试 PDF")
