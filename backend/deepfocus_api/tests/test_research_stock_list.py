from deepfocus_api.research_digest import ResearchDeepDraftRequest, SourceDocument, _fallback_from_compact
from deepfocus_api.research_multi_agent import _compact_page_result, _page_result_is_usable


def test_numbered_scan_preserves_ten_constituents_instead_of_eight_chips():
    rows = [[f"公司{i}", f"{i:06d}.SZ"] for i in range(10)]
    page = {"stock_rows": rows}
    assert _page_result_is_usable(page)
    compact = {"summary": "筛选条件", "pages_analyzed": 2, "page_results": [_compact_page_result(page, 2)]}
    request = ResearchDeepDraftRequest(title="中国股票策略：值得关注的十只超跌股票")
    docs = [SourceDocument(source_id="test", title=request.title, total_pages=2)]
    result = _fallback_from_compact(compact, request, docs)
    assert result.stock_list_complete
    assert result.sections[0].tables[0].rows == rows
    assert result.sections[0].tables[0].note == "原文页码 2"
    assert not result.tables
    compact["page_results"][0]["stock_rows"].pop()
    result = _fallback_from_compact(compact, request, docs)
    assert result.stock_list_complete is False
    assert "识别到 9 家" in result.sections[0].summary
