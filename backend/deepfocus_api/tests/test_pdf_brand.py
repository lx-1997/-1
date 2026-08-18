from __future__ import annotations

import math

import fitz
import pikepdf

from deepfocus_api import pdf_brand as pb


def _plain_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_text((72, 90), "BODY_KEEP earnings and valuation", fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def test_compact_keyword_matches_spaced_channel_watermark():
    assert pb._is_kw("水 木 纪 要")
    assert pb._is_kw("加 入 知 识 星 球")


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
        assert page.get_text("text").count("www.daocaijing.com") == 2
        branded_body = page.search_for("BODY_KEEP")[0]
        assert branded_body == original_body

    twice, ok = pb._process_sync(once)
    assert ok
    with fitz.open(stream=twice, filetype="pdf") as branded_again:
        page = branded_again[0]
        assert page.rect.height == original_height + pb._BRAND_FOOTER_H
        assert page.get_text("text").count("www.daocaijing.com") == 2


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
    page = doc.new_page(width=595, height=854)
    page.insert_text((72, 90), "BODY_KEEP", fontsize=12)
    page.draw_rect(
        fitz.Rect(0, 842, 595, 854),
        fill=pb._BRAND_FOOTER_FILL,
        color=None,
    )
    page.insert_text(
        (390, 851),
        pb._BRAND_TEXT,
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
        assert page.get_text("text").count("www.daocaijing.com") == 2
