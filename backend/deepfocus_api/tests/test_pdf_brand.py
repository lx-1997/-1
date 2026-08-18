from __future__ import annotations

import math

import fitz
import numpy as np
import pikepdf

from deepfocus_api import pdf_brand as pb


def _plain_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "BODY_KEEP earnings and valuation", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _image_only_report(*, watermark: bool, shifted: bool = False) -> bytes:
    """生成多页整图研报；可把固定或逐页平移的斜水印烙进像素。"""
    raster = fitz.open()
    angle = math.radians(20)
    matrix = fitz.Matrix(
        math.cos(angle), -math.sin(angle), math.sin(angle), math.cos(angle), 0, 0
    )
    for index in range(5):
        vector = fitz.open()
        page = vector.new_page(width=600, height=800)
        page.insert_text((55, 90 + index * 38), f"BODY KEEP PAGE {index}", fontsize=18)
        page.draw_rect(
            fitz.Rect(55, 150 + index * 25, 500, 156 + index * 25),
            fill=(0.08, 0.08, 0.08),
            color=None,
        )
        if watermark:
            shift = (index - 2) * 28 if shifted else 0
            for pivot in (fitz.Point(15, 460 + shift), fitz.Point(305, 600 + shift)):
                page.insert_text(
                    pivot,
                    "PRIVATE RESEARCH SHUIMU2026",
                    fontsize=25,
                    color=(0.604, 0.604, 0.604),
                    morph=(pivot, matrix),
                )
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        vector.close()
        out_page = raster.new_page(width=600, height=800)
        out_page.insert_image(out_page.rect, stream=pix.tobytes("png"))
    data = raster.tobytes(garbage=2, deflate=True)
    raster.close()
    return data


def _single_page_channel_watermark_report(*, watermark: bool) -> bytes:
    """生成只有一页出现超长斜排引流语的整图研报。"""
    raster = fitz.open()
    angle = math.radians(20)
    matrix = fitz.Matrix(
        math.cos(angle), -math.sin(angle), math.sin(angle), math.cos(angle), 0, 0
    )
    for index in range(5):
        vector = fitz.open()
        page = vector.new_page(width=600, height=800)
        page.insert_text((330, 90), f"BODY KEEP PAGE {index}", fontsize=14)
        for y in range(125, 420, 28):
            page.insert_text(
                (330, y),
                "Incremental policies and market outlook",
                fontsize=9,
            )
        if watermark and index == 1:
            pivot = fitz.Point(12, 500)
            page.insert_text(
                pivot,
                "更多一手调研纪要和海外投行研报 数据加微信 shuimu",
                fontsize=18,
                fontname="china-s",
                color=(0.65, 0.65, 0.65),
                morph=(pivot, matrix),
            )
        pix = page.get_pixmap(matrix=fitz.Matrix(1.5, 1.5), alpha=False)
        vector.close()
        out_page = raster.new_page(width=600, height=800)
        out_page.insert_image(out_page.rect, stream=pix.tobytes("png"))
    data = raster.tobytes(garbage=2, deflate=True)
    raster.close()
    return data


def _render_rgb_pages(data: bytes) -> list[np.ndarray]:
    rendered = []
    with fitz.open(stream=data, filetype="pdf") as doc:
        for page in doc:
            pix = page.get_pixmap(alpha=False)
            rendered.append(
                np.frombuffer(pix.samples, dtype=np.uint8)
                .reshape(pix.height, pix.width, pix.n)[:, :, :3]
                .copy()
            )
    return rendered


def test_compact_keyword_matches_spaced_channel_watermark():
    assert pb._is_kw("水 木 纪 要")
    assert pb._is_kw("加 入 知 识 星 球")
    assert pb._is_kw("更多 一手调研纪要 和 海外投行研报")


def test_generic_confidential_body_is_not_a_direct_keyword():
    assert not pb._is_kw("The agreement contains confidential information")
    assert pb._is_generic_wm_label("CONFIDENTIAL")


def test_single_rotated_xobject_is_preserved_but_tiling_is_removed():
    one = b"q .94 .34 -.34 .94 10 20 cm /Fm0 Do Q\n"
    cleaned, count = pb._remove_wm_q_blocks(one, {})
    assert cleaned == one
    assert count == 0

    tiled = one * pb._REPEAT_ON_PAGE
    cleaned, count = pb._remove_wm_q_blocks(tiled, {})
    assert b"/Fm0 Do" not in cleaned
    assert count == pb._REPEAT_ON_PAGE


def test_full_page_zsxq_link_redirects_to_research_hub_only():
    pdf = pikepdf.Pdf.new()
    page = pdf.add_blank_page(page_size=(612, 792))

    def link(rect, uri):
        return pdf.make_indirect(pikepdf.Dictionary(
            Type=pikepdf.Name("/Annot"),
            Subtype=pikepdf.Name("/Link"),
            Rect=pikepdf.Array(rect),
            A=pikepdf.Dictionary(
                S=pikepdf.Name("/URI"),
                URI=pikepdf.String(uri),
            ),
        ))

    page.obj["/Annots"] = pikepdf.Array([
        link([0, 0, 612, 792], "https://wx.zsxq.com/group/88888142214212"),
        link([10, 10, 110, 40], "https://wx.zsxq.com/group/88888142214212"),
        link([0, 0, 612, 792], "https://example.com/source"),
    ])

    assert pb._redirect_full_page_source_links(pdf) == 1
    uris = [str(ref["/A"]["/URI"]) for ref in page.obj["/Annots"]]
    assert uris == [
        "https://www.daocaijing.com/?tab=research",
        "https://wx.zsxq.com/group/88888142214212",
        "https://example.com/source",
    ]


def test_repeated_light_confidential_marks_are_removed_without_touching_body():
    doc = fitz.open(stream=_plain_pdf(), filetype="pdf")
    page = doc[0]
    for y in (180, 360, 540):
        page.insert_text(
            (90, y),
            "CONFIDENTIAL",
            fontsize=24,
            color=(0.78, 0.78, 0.78),
        )
    source = doc.tobytes()
    doc.close()

    result, ok = pb._process_sync(source, add_brand=False)
    assert ok
    with fitz.open(stream=result, filetype="pdf") as cleaned:
        text = cleaned[0].get_text("text")
    assert "BODY_KEEP" in text
    assert "CONFIDENTIAL" not in text


def test_unique_light_rotated_chart_label_is_preserved():
    doc = fitz.open(stream=_plain_pdf(), filetype="pdf")
    page = doc[0]
    angle = math.radians(20)
    matrix = fitz.Matrix(
        math.cos(angle), -math.sin(angle), math.sin(angle), math.cos(angle), 0, 0
    )
    pivot = fitz.Point(180, 300)
    page.insert_text(
        pivot,
        "Forecast axis",
        fontsize=11,
        color=(0.72, 0.72, 0.72),
        morph=(pivot, matrix),
    )
    source = doc.tobytes()
    doc.close()

    result, ok = pb._process_sync(source, add_brand=False)
    assert ok
    with fitz.open(stream=result, filetype="pdf") as cleaned:
        text = cleaned[0].get_text("text")
    assert "BODY_KEEP" in text
    assert "Forecast axis" in text


def test_brand_uses_dedicated_footer_and_is_idempotent():
    original = _plain_pdf()
    with fitz.open(stream=original, filetype="pdf") as source:
        original_height = source[0].rect.height
        original_body = source[0].search_for("BODY_KEEP")[0]

    once, ok = pb._process_sync(original)
    assert ok
    with fitz.open(stream=once, filetype="pdf") as branded:
        page = branded[0]
        assert page.rect.height == original_height + pb._BRAND_FOOTER_H
        assert page.get_text("text").count("www.daocaijing.com") == 3
        assert page.get_text("text").count("股票投资信息与深度研究平台") == 3
        rotated_brand_lines = [
            line
            for block in page.get_text("dict").get("blocks", [])
            for line in block.get("lines", [])
            if abs(float(line.get("dir", (1, 0))[1])) > 0.1
            and any("DeepFocus" in span.get("text", "") for span in line.get("spans", []))
        ]
        assert len(rotated_brand_lines) == 3
        branded_body = page.search_for("BODY_KEEP")[0]
        assert branded_body == original_body

    twice, ok = pb._process_sync(once)
    assert ok
    with fitz.open(stream=twice, filetype="pdf") as branded_again:
        page = branded_again[0]
        assert page.rect.height == original_height + pb._BRAND_FOOTER_H
        assert page.get_text("text").count("www.daocaijing.com") == 3


def test_top_cover_is_tall_and_fully_opaque():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # 用醒目的红色模拟旧版 16pt 顶栏下方仍然露出的渠道文字 / 图形。
    page.draw_rect(fitz.Rect(0, 0, 595, 24), fill=(1, 0, 0), color=None)
    page.insert_text((72, 90), "BODY_KEEP", fontsize=12)
    source = doc.tobytes()
    doc.close()

    result, ok = pb._process_sync(source)
    assert ok
    with fitz.open(stream=result, filetype="pdf") as branded:
        page = branded[0]
        assert pb._page_has_brand(page)
        pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), alpha=False)

    # 检查顶部 24pt 范围：完全不能再看到下层的红色像素。
    row_bytes = pix.width * pix.n
    top = pix.samples[: row_bytes * 48]
    assert not any(
        top[i] > 200 and top[i + 1] < 80 and top[i + 2] < 80
        for i in range(0, len(top), pix.n)
    )


def test_legacy_footer_is_upgraded_without_extending_page_again():
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.set_mediabox(fitz.Rect(0, -12, 595, 842))
    page.insert_text((72, 90), "BODY_KEEP", fontsize=12)
    page.draw_rect(
        fitz.Rect(0, 842, 595, 854),
        fill=pb._BRAND_FOOTER_FILL,
        color=None,
    )
    page.insert_text(
        (390, 851),
        "DeepFocus Research  |  www.daocaijing.com",
        fontsize=6.2,
        color=pb._BRAND_TEXT_COLOR,
    )
    legacy = doc.tobytes()
    doc.close()

    upgraded, ok = pb._process_sync(legacy)
    assert ok
    with fitz.open(stream=upgraded, filetype="pdf") as branded:
        page = branded[0]
        assert page.rect.height == 854
        assert pb._page_has_brand(page)
        assert page.get_text("text").count("股票投资信息与深度研究平台") == 3


def test_repeated_baked_raster_watermark_is_suppressed_without_rasterizing_text_pdf():
    source = _image_only_report(watermark=True)
    clean_reference = _image_only_report(watermark=False)
    result, ok = pb._process_sync(source, add_brand=False)
    assert ok

    source_pages = _render_rgb_pages(source)
    clean_pages = _render_rgb_pages(clean_reference)
    result_pages = _render_rgb_pages(result)
    before = np.mean([
        np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()
        for a, b in zip(source_pages, clean_pages)
    ])
    after = np.mean([
        np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()
        for a, b in zip(result_pages, clean_pages)
    ])
    assert before > 0.4
    assert after < before * 0.55

    searchable = fitz.open()
    for index in range(3):
        page = searchable.new_page(width=600, height=800)
        page.insert_text((72, 90), f"SEARCHABLE BODY {index}", fontsize=14)
    assert pb._remove_repeated_raster_watermarks(searchable) == 0
    assert "SEARCHABLE BODY" in searchable[0].get_text("text")
    assert all(not page.get_images(full=True) for page in searchable)
    searchable.close()


def test_deepfocus_brand_is_baked_into_image_only_report():
    """整页图研报的主品牌水印必须进入图像像素，不能只是可删的旋转文字层。"""
    source = _image_only_report(watermark=False)
    result, ok = pb._process_sync(source)
    assert ok

    with (
        fitz.open(stream=source, filetype="pdf") as original,
        fitz.open(stream=result, filetype="pdf") as branded,
    ):
        source_info = pb._dominant_raster_image(original[0])
        branded_info = pb._dominant_raster_image(branded[0])
        assert source_info is not None
        assert branded_info is not None

        source_pix = fitz.Pixmap(original, source_info[0])
        branded_pix = fitz.Pixmap(branded, branded_info[0])
        source_rgb = (
            np.frombuffer(source_pix.samples, dtype=np.uint8)
            .reshape(source_pix.height, source_pix.width, source_pix.n)[:, :, :3]
        )
        branded_rgb = (
            np.frombuffer(branded_pix.samples, dtype=np.uint8)
            .reshape(branded_pix.height, branded_pix.width, branded_pix.n)[:, :, :3]
        )
        assert source_rgb.shape == branded_rgb.shape
        height, width = source_rgb.shape[:2]
        central_change = np.abs(
            source_rgb[int(height * 0.28):int(height * 0.78), int(width * 0.16):int(width * 0.84)].astype(np.int16)
            - branded_rgb[int(height * 0.28):int(height * 0.78), int(width * 0.16):int(width * 0.84)].astype(np.int16)
        ).mean()
        assert central_change > 0.25

        # 页面文字层没有旋转的 DeepFocus 主标，但像素已改变：证明主视觉已烘印。
        rotated_brand_lines = [
            line
            for block in branded[0].get_text("dict").get("blocks", [])
            for line in block.get("lines", [])
            if abs(float(line.get("dir", (1, 0))[1])) > 0.1
            and any("DeepFocus" in span.get("text", "") for span in line.get("spans", []))
        ]
        assert rotated_brand_lines == []
        assert branded[0].get_text("text").count("www.daocaijing.com") == 3


def test_dominant_raster_prefers_image_actually_painted_by_content_stream():
    with fitz.open(stream=_image_only_report(watermark=True), filetype="pdf") as doc:
        page = doc[0]
        original = pb._dominant_raster_image(page)
        assert original is not None
        original_xref = original[0]
        replacement = fitz.Pixmap(doc, original_xref)
        page.replace_image(original_xref, pixmap=replacement)
        assert len(page.get_images(full=True)) == 2
        assert pb._dominant_raster_image(page) == original

        width, height = original[1:]
        white = fitz.Pixmap(
            fitz.csRGB,
            width,
            height,
            b"\xff" * (width * height * 3),
            False,
        )
        pb._replace_raster_image(page, original_xref, white)
        rendered = np.frombuffer(page.get_pixmap(alpha=False).samples, dtype=np.uint8)
        assert rendered.mean() > 254.9


def test_page_shifted_baked_raster_watermark_is_suppressed():
    source = _image_only_report(watermark=True, shifted=True)
    clean_reference = _image_only_report(watermark=False, shifted=True)
    result, ok = pb._process_sync(source, add_brand=False)
    assert ok

    source_pages = _render_rgb_pages(source)
    clean_pages = _render_rgb_pages(clean_reference)
    result_pages = _render_rgb_pages(result)
    before = np.mean([
        np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()
        for a, b in zip(source_pages, clean_pages)
    ])
    after = np.mean([
        np.abs(a.astype(np.int16) - b.astype(np.int16)).mean()
        for a, b in zip(result_pages, clean_pages)
    ])
    assert before > 0.4
    assert after < before * 0.55


def test_single_page_long_channel_watermark_is_suppressed():
    source = _single_page_channel_watermark_report(watermark=True)
    clean_reference = _single_page_channel_watermark_report(watermark=False)
    result, ok = pb._process_sync(source, add_brand=False)
    assert ok

    source_page = _render_rgb_pages(source)[1]
    clean_page = _render_rgb_pages(clean_reference)[1]
    result_page = _render_rgb_pages(result)[1]
    before = np.abs(source_page.astype(np.int16) - clean_page.astype(np.int16)).mean()
    after = np.abs(result_page.astype(np.int16) - clean_page.astype(np.int16)).mean()
    assert before > 0.25
    assert after < before * 0.55


def test_stable_diagonal_profile_keeps_two_channel_watermark_tracks():
    """通用检测每页可能只抢到两条平行水印中的一条，不得合并丢失。"""
    page_bands = {}
    for page_index in range(8):
        # 两条平行水印在通用 top-N 里交替胜出；跨页合并后应恢复两条轨迹。
        center = 310 if page_index % 2 == 0 else 610
        slope = -0.375
        intercept = center - slope * 300
        bands = [(slope, intercept, 10, 590, 9)]
        if page_index == 0:
            # 封面密集正文造成的高分错斜率，只出现一页，必须排除。
            bands.append((-0.225, 520, 5, 595, 9))
        page_bands[page_index] = bands

    slope, centers = pb._stable_diagonal_profile(page_bands, 600, 800, 8)
    assert slope == -0.375
    assert len(centers) == 2
    assert sorted(round(center) for center in centers) == [310, 610]
