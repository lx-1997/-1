"""研报「AI 解读」可分享落地页（软墙）的回归守卫。

覆盖本次改动：
- save_share/get_share 往返一致，id 由内容派生且去重；
- 非法 id（注入/非 hex）一律 None；
- recent_shares / all_ids 走索引、新→旧；
- 公开页软墙：展示标题+解读导语+「登录看完整解读」深链，超长解读正文不外露全文；
- 路由：/report/{id}、/reports、/api/report/view/{id} 公开可达，POST /api/report/share 须登录。

每个测试用独立临时库（monkeypatch data_store DB_PATH），不触真实库、互不污染。
"""
from __future__ import annotations

import pytest

from deepfocus_api import data_store, report_share, seo_pages


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(data_store, "DB_PATH", tmp_path / "ds.sqlite3")
    data_store.init_data_store()
    return {}


def test_save_get_roundtrip_and_dedup(env):
    rec = report_share.save_share(
        title="台积电 2025Q2 法说会纪要", summary="【摘要】先进制程满载。\n【投资逻辑】AI 拉动资本开支。",
        source_name="摩根士丹利", symbol="tsm",
    )
    assert report_share.is_valid_id(rec["id"])
    assert rec["symbol"] == "TSM"  # 归一化大写
    got = report_share.get_share(rec["id"])
    assert got and got["title"] == rec["title"] and got["summary"] == rec["summary"]
    # 同内容再存 → 同一 id（天然去重，分享同一篇解读复用同一 URL）
    rec2 = report_share.save_share(title="台积电 2025Q2 法说会纪要", summary="【摘要】先进制程满载。\n【投资逻辑】AI 拉动资本开支。", symbol="tsm")
    assert rec2["id"] == rec["id"]


def test_invalid_id_returns_none(env):
    assert report_share.get_share("") is None
    assert report_share.get_share("../etc/passwd") is None
    assert report_share.get_share("not-hex-zzz") is None
    assert report_share.get_share("deadbeefdeadbeef") is None  # 合法 hex 格式、未存 → None


def test_empty_title_or_summary_rejected(env):
    with pytest.raises(ValueError):
        report_share.save_share(title="", summary="x")
    with pytest.raises(ValueError):
        report_share.save_share(title="x", summary="")


def test_recent_and_index_newest_first(env):
    a = report_share.save_share(title="甲公司点评", summary="甲内容")
    b = report_share.save_share(title="乙公司点评", summary="乙内容")
    ids = report_share.all_ids()
    assert ids[0] == b["id"] and a["id"] in ids  # 最新置顶
    recent = report_share.recent_shares(5)
    assert recent and recent[0]["id"] == b["id"]


def test_public_page_is_softwall(env):
    long_body = "【摘要】" + "细节内容" * 200  # 远超 300 字导语阈值
    rec = report_share.save_share(title="某行业深度", summary=long_body, source_name="高盛", symbol="600519")
    html = seo_pages.render_report_page_html(rec, report_share.recent_shares(5), page_url=f"{seo_pages.BASE_URL}/report/{rec['id']}")
    assert "看完整解读" in html                     # 软墙 CTA
    assert f"report={rec['id']}" in html            # 登录深链 ?report={id}
    assert long_body not in html                    # 完整解读正文不外露（仅导语）
    assert "✦ DeepFocus AI 研报速读" in html        # 标注为我方增值解读（非第三方原文/PDF）