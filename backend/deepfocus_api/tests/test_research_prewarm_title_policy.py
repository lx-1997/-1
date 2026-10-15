from deepfocus_api.research_prewarm_title_policy import (
    is_bare_company_date_title,
    normalize_company_names,
)

import pytest

A_NAMES = normalize_company_names({
    "聚灿光电", "惠泉啤酒", "捷成股份", "朗姿股份", "飞龙股份", "宁德时代",
    "京东方Ａ", "五 粮 液", "机器人", "*ST测试", "中国移动", "九号公司-WD",
})


@pytest.mark.parametrize("title", [
    "聚灿光电20261013", "惠泉啤酒20261013", "捷成股份20261013",
    "朗姿股份20261013", "飞龙股份20261013", " 宁德时代 20261013.pdf ",
    "宁德时代_2026-10-13.PDF", "宁德时代：2026/10/13",
    "宁德时代 2026.10.13", "宁德时代2026年10月13日",
    "宁德时代（20261013）", "宁德时代[2026-10-13]",
    "宁德时代【20261013】", "京东方A２０２６１０１３",
    "五粮液20261013", "机器人20261013", "*ST测试20261013",
    "九号公司-WD20261013", "宁德时代20240229",
])
def test_skip_exact_a_company_and_valid_date(title):
    assert is_bare_company_date_title(title, A_NAMES)


@pytest.mark.parametrize("title", [
    "创想三维20261013", "汇量科技20261013", "腾讯20261013", "AAPL20261013",
    "宁德时代盈利展望20261013", "宁德时代：三季报点评20261013",
    "宁德时代20261013业绩点评", "中国移动与AI算力20261013",
    "宁德时代和比亚迪20261013", "宁德20261013", "新公司20261013",
    "20261013宁德时代", "2026-10-13 宁德时代20261013",
    "宁德时代", "宁德时代202610", "宁德时代261013", "宁德时代202610133",
    "宁德时代20260229", "宁德时代20261301", "宁德时代20261000",
    "宁德时代2026-10/13", "宁德时代（20261013", "宁德时代20261013）",
    "宁德时代(业绩点评20261013)", "宁德时代20261013()", "宁德时代20261013.exe", "", None,
])
def test_keep_topic_reports_foreign_or_unknown_companies_and_invalid_dates(title):
    assert not is_bare_company_date_title(title, A_NAMES)


def test_market_scope_can_extend_by_explicit_authoritative_name_set():
    all_names = normalize_company_names({"汇量科技", "创想三维", "AAPL"})
    assert is_bare_company_date_title("汇量科技20261013", all_names)
    assert is_bare_company_date_title("创想三维20261013", all_names)
    assert is_bare_company_date_title("aapl20261013", all_names)
    assert not is_bare_company_date_title("汇量科技商业化20261013", all_names)


def test_empty_authoritative_snapshot_never_guesses():
    assert not is_bare_company_date_title("宁德时代20261013", frozenset())
