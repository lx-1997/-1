"""Synthetic, isolated stores protect platform provenance and account boundaries."""
from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from deepfocus_api import stock_platform_sources as sources


@pytest.fixture
def stores(tmp_path, monkeypatch):
    # No test may accidentally fall back to the running workbench or process
    # cache. All network readers and all persisted paths are synthetic.
    async def offline(**kwargs):
        raise TimeoutError("synthetic offline reader")
    monkeypatch.setitem(sys.modules, "deepfocus_api.zsxq_stream", SimpleNamespace(
        _CACHE={}, stream_groups=lambda: [], fetch_stream=offline))
    monkeypatch.setitem(sys.modules, "deepfocus_api.celebrity_views", SimpleNamespace(
        _CACHE={}, get_config=lambda: {"enabled": True}, _config_signature=lambda _: "current", fetch_celebrity_views=offline))
    monkeypatch.setitem(sys.modules, "deepfocus_api.research_jobs", SimpleNamespace(manager=SimpleNamespace(jobs={})))
    return {key: tmp_path / (key + ".sqlite3") for key in sources._STORES}


def execute(path, script, rows=()):
    with sqlite3.connect(path) as connection:
        connection.executescript(script)
        for sql, args in rows:
            connection.execute(sql, args)


def date():
    return datetime.now(timezone.utc).isoformat()


def messages(stores, rows):
    execute(stores["messages"], """
        CREATE TABLE realtime_messages (
          id TEXT PRIMARY KEY,title TEXT,content TEXT,source_id TEXT,source_name TEXT,
          source_type TEXT,symbol TEXT,topic TEXT,severity TEXT,url TEXT,
          tags_json TEXT,metadata_json TEXT,created_at TEXT);
    """, [("INSERT INTO realtime_messages VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
        row.get("id", str(i)), row.get("title", "测试公司"), row.get("content", "经营改善"),
        row.get("source_id", "site"), "来源", row.get("source_type", "server_api"), row.get("symbol"),
        row.get("topic", "快讯"), "info", row.get("url"), "[]", json.dumps(row.get("metadata", {})), row.get("created_at", date()),
    )) for i, row in enumerate(rows)])


def load(stores, query=None, **kwargs):
    arguments = dict(owner_id="alice-id", username="alice", paths=stores,
                     message_view=lambda item: item, allow_remote=False)
    arguments.update(kwargs)
    result = asyncio.run(sources.make_platform_loader(**arguments)(query or {"symbol": "600519", "name": "测试公司", "market": "CN"}))
    return {item["channel"]: item for item in result}


def test_visible_full_text_is_not_replaced_by_a_short_snippet(stores):
    content = "开头信息。" * 200 + "600519 年度订单增长40%，上游原材料涨价导致毛利率下降。"
    messages(stores, [{"content": content, "title": "行业观察"}])
    result = load(stores)["site_fast_news"]
    assert result["items"][0]["visible_text"] == content
    assert result["items"][0]["body_state"] == "full"
    assert result["coverage"]["read_chars"] == len(content)
    assert any(row["kind"] == "upstream" and row["verification"] == "context" for row in result["items"][0]["relation_refs"])


def test_nonmember_matching_cannot_use_locked_body_or_metadata(stores):
    messages(stores, [{"title": "行业资料", "topic": "文章", "content": "公开导语。" * 30 + "600519 私密业务变化。",
        "symbol": "600519", "metadata": {"raw_body": "会员秘密", "ai_summary": "600519 增长爆发"}}])
    def teaser(item):
        return item.model_copy(update={"content": item.content[:120]})
    result = load(stores, message_view=teaser)
    assert result["site_articles"]["items"] == []
    assert result["site_articles"]["coverage"]["matched"] == 0
    assert "会员秘密" not in json.dumps(result, ensure_ascii=False)


def test_nonmember_uses_gate_returned_teaser_only_and_never_article_ai_cache(stores, monkeypatch):
    messages(stores, [{"topic": "文章", "content": "测试公司导语" + "私密段落" * 200,
                      "metadata": {"content": "会员元数据全文"}}])
    monkeypatch.setattr(sources._Collector, "cache", lambda *args: (_ for _ in ()).throw(AssertionError("article AI cache")))
    visible = "测试公司可读导语"
    result = load(stores, message_view=lambda item: item.model_copy(update={"content": visible}))
    item = result["site_articles"]["items"][0]
    assert item["visible_text"] == visible
    assert item["body_state"] == "teaser"
    assert result["site_articles"]["status"] == "partial"
    assert "会员元数据全文" not in json.dumps(result, ensure_ascii=False)


def test_source_gate_precedes_matching_counting_and_links(stores):
    messages(stores, [{"title": "不能泄露的600519标题", "url": "https://private.test", "source_id": "denied"}])
    result = load(stores, message_view=lambda item: None)["site_fast_news"]
    assert result["items"] == []
    assert result["coverage"]["scanned"] == 0
    assert result["coverage"]["matched"] == 0
    assert "不能泄露" not in json.dumps(result, ensure_ascii=False)


def test_three_platform_topics_have_independent_scans_beyond_legacy_24(stores):
    rows = [{"id": str(i), "title": "无关公司", "content": "其他股票", "created_at": date()} for i in range(40)]
    rows.extend([{"id": "article", "topic": "文章", "content": "600519 财务深读"},
                 {"id": "report", "topic": "研报", "content": "600519 客户订单"}])
    messages(stores, rows)
    result = load(stores, is_member=True)
    assert result["site_fast_news"]["coverage"]["scanned"] == 40
    assert result["site_articles"]["items"][0]["id"] == "site_articles:article"
    assert result["site_research"]["items"][0]["id"] == "site_research:report"


@pytest.mark.parametrize("query,content", [
    ({"symbol": "600519", "market": "CN"}, "联系电话16005199"),
    ({"symbol": "CAT", "market": "US"}, "CATERPILLAR报告"),
    ({"symbol": "00700", "market": "HK"}, "1700用户调查"),
])
def test_identifiers_require_boundaries(stores, query, content):
    messages(stores, [{"title": "市场动态", "content": content}])
    assert load(stores, query)["site_fast_news"]["items"] == []


def test_hk_unpadded_exact_code_is_valid(stores):
    messages(stores, [{"title": "市场动态", "content": "700.HK 客户变动"}])
    result = load(stores, {"symbol": "00700", "market": "HK"})["site_fast_news"]
    assert result["items"][0]["metadata_safe"]["match_basis"] == "visible_symbol"


def test_industry_and_peer_are_context_not_company_facts(stores):
    messages(stores, [{"id": "industry", "title": "新能源行业", "content": "行业供需"},
                     {"id": "peer", "title": "竞争公司", "content": "同行订单增长"}])
    result = load(stores, {"symbol": "600519", "name": "测试公司", "industry": "新能源", "peers": [{"symbol": "000001", "name": "竞争公司"} ]})["site_fast_news"]
    by_id = {item["id"]: item for item in result["items"]}
    assert by_id["site_fast_news:industry"]["entity_refs"] == []
    assert by_id["site_fast_news:industry"]["relation_refs"][0]["verification"] == "context"
    assert by_id["site_fast_news:peer"]["relation_refs"][0]["verification"] == "candidate"


def wire(stores, logic_lines=None):
    payload = {"id": "row", "file_id": "file1", "title": "测试公司：年度展望", "org": "投行", "date": date()[:10]}
    execute(stores["wire"], "CREATE TABLE research_archive(id TEXT,date TEXT,title TEXT,payload TEXT);", [
        ("INSERT INTO research_archive VALUES (?,?,?,?)", ("row", date()[:10], payload["title"], json.dumps(payload)))])
    cached = {"summary": "测试公司利润预期增长。", "raw_pdf": "未授权PDF原文", "cookie": "不传输", "core_logic": "客户订单进入交付"}
    if logic_lines is not None:
        cached["logic_lines"] = logic_lines
    execute(stores["ai"], "CREATE TABLE ai_analysis_cache(ref TEXT,payload TEXT,created_at TEXT);", [
        ("INSERT INTO ai_analysis_cache VALUES (?,?,?)", ("file1", json.dumps(cached), date()))])


def test_wire_cached_ai_requires_its_own_membership_gate_even_admin_article_access(stores):
    wire(stores)
    result = load(stores, is_member=True, wire_ai_allowed=False)["recent_research"]
    assert result["status"] == "access_required"
    assert result["coverage"]["scanned"] == 0
    assert result["items"] == []
    assert "年度展望" not in json.dumps(result, ensure_ascii=False)


def test_wire_only_existing_interpretation_fields_no_pdf_prompt_or_signed_url(stores):
    wire(stores)
    result = load(stores, wire_ai_allowed=True)["recent_research"]
    item = result["items"][0]
    assert item["body_state"] == "summary_only"
    assert "客户订单进入交付" in item["visible_text"]
    assert item["url"] is None
    assert "未授权PDF原文" not in json.dumps(result, ensure_ascii=False)
    assert "不传输" not in json.dumps(result, ensure_ascii=False)


@pytest.mark.parametrize("allowed", [True, False])
def test_real_cached_logic_line_keeps_chain_impact_watch_only_behind_paid_gate(stores, allowed):
    from deepfocus_api.schemas import ResearchLogicLine
    logic = ResearchLogicLine(
        title="测试公司独立逻辑线",
        evidence="测试公司已有解读中的订单线索",
        chain="测试传导链：订单交付带动收入确认",
        impact="测试经营影响：产品结构改善可能提升毛利率",
        watch="测试核验条件：观察交付进度与经营现金回款",
    )
    wire(stores, [{**logic.model_dump(), "private_original": "隐藏缓存原文不能读取", "prompt": "内部提示不能透出"}])
    result = load(stores, is_member=allowed, wire_ai_allowed=allowed)["recent_research"]
    encoded = json.dumps(result, ensure_ascii=False)
    if allowed:
        item = result["items"][0]
        assert item["body_state"] == "summary_only"
        assert item["role"] == "analysis"
        for field in ("title", "evidence", "chain", "impact", "watch"):
            assert getattr(logic, field) in item["visible_text"]
    else:
        assert result["status"] == "access_required"
        assert result["items"] == []
        assert result["coverage"]["scanned"] == 0
        for field in ("title", "evidence", "chain", "impact", "watch"):
            assert getattr(logic, field) not in encoded
        assert "年度展望" not in encoded
    for hidden in ("未授权PDF原文", "不传输", "隐藏缓存原文不能读取", "内部提示不能透出"):
        assert hidden not in encoded


def test_truncated_cached_interpretation_remains_summary_only(stores, monkeypatch):
    wire(stores, [{"chain": "测试经营传导" * 50, "impact": "测试利润影响", "watch": "测试回款核验"}])
    monkeypatch.setattr(sources, "CHANNEL_TEXT_LIMIT", 30)
    result = load(stores, wire_ai_allowed=True)["recent_research"]
    item = result["items"][0]
    assert item["body_state"] == "summary_only"
    assert item["role"] == "analysis"
    assert item["metadata_safe"]["text_truncated"] is True
    assert result["status"] == "partial"
    assert result["coverage"]["limit_hit"] is True
    assert result["coverage"]["read_chars"] == 30


def test_truncated_member_teaser_never_upgrades_to_primary_source_excerpt(stores, monkeypatch):
    messages(stores, [{"topic": "文章", "title": "600519导语", "symbol": "600519",
                      "content": "测试公司公开导语" * 20 + "隐藏会员正文不得进入可见范围"}])
    monkeypatch.setattr(sources, "CHANNEL_TEXT_LIMIT", 16)
    result = load(stores, message_view=lambda item: item.model_copy(update={"content": item.content[:120]}))["site_articles"]
    item = result["items"][0]
    assert item["body_state"] == "teaser"
    assert item["metadata_safe"]["text_truncated"] is True
    assert len(item["visible_text"]) == 16
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["limit_hit"] is True
    assert "隐藏会员正文" not in json.dumps(result, ensure_ascii=False)


def test_wire_expired_cache_is_not_used(stores, monkeypatch):
    wire(stores)
    with sqlite3.connect(stores["ai"]) as connection:
        connection.execute("UPDATE ai_analysis_cache SET created_at='2001-01-01'")
    monkeypatch.setenv("DEEPFOCUS_AI_CACHE_MAX_AGE_DAYS", "30")
    item = load(stores, wire_ai_allowed=True)["recent_research"]["items"][0]
    assert item["body_state"] == "title_only"
    assert item["visible_text"] == ""


def test_notes_persisted_lead_is_teaser_not_full_text(stores):
    execute(stores["notes"], "CREATE TABLE zsxq_share(id TEXT,title TEXT,lead TEXT,date TEXT,created_at REAL);", [
        ("INSERT INTO zsxq_share VALUES (?,?,?,?,?)", ("42", "测试公司纪要", "测试公司经营导语…", date()[:10], time.time()))])
    module = SimpleNamespace(_CACHE={}, stream_groups=lambda: [{"id": "allowed"}])
    with pytest.MonkeyPatch.context() as monkeypatch:
        monkeypatch.setitem(sys.modules, "deepfocus_api.zsxq_stream", module)
        item = load(stores)["institution_notes"]["items"][0]
    assert item["body_state"] == "teaser"
    assert item["url"] == "/note/42"


def test_notes_cached_full_text_scope_checks_configured_groups_and_keeps_whole_text(stores, monkeypatch):
    module = SimpleNamespace(_CACHE={
        ("allowed", ""): (time.monotonic(), {"items": [{"id": "1", "title": "测试公司纪要", "text": "正文" * 400 + "客户订单确认", "date": date()[:10]}]}),
        ("removed", ""): (time.monotonic(), {"items": [{"id": "2", "title": "测试公司旧群秘密", "text": "不可展示"}]}),
    }, stream_groups=lambda: [{"id": "allowed"}])
    monkeypatch.setitem(sys.modules, "deepfocus_api.zsxq_stream", module)
    result = load(stores)["institution_notes"]
    assert len(result["items"]) == 1
    assert result["items"][0]["visible_text"].endswith("客户订单确认")
    assert result["items"][0]["body_state"] == "full"


def test_celebrity_whitelist_is_checked_before_cache_read(stores, monkeypatch):
    class ForbiddenCache:
        def get(self, key):
            raise AssertionError("Unauthorized cache read")
    monkeypatch.setitem(sys.modules, "deepfocus_api.celebrity_views", SimpleNamespace(_CACHE=ForbiddenCache()))
    result = load(stores)["celebrity_views"]
    assert result["status"] == "access_required"
    assert result["coverage"]["scanned"] == 0


def test_celebrity_cached_views_require_current_config_signature_and_exclude_examples(stores, monkeypatch):
    payload = {"figures": [{"name": "研究者", "items": [{"id": "real", "title": "测试公司评论", "body": "公开观点。"},
                                                       {"id": "demo", "title": "测试公司示例", "body": "示例内容"}]}]}
    module = SimpleNamespace(_CACHE={"resp": {"resp": payload, "sig": "current"}},
                             get_config=lambda: {"enabled": True}, _config_signature=lambda _: "current")
    monkeypatch.setitem(sys.modules, "deepfocus_api.celebrity_views", module)
    result = load(stores, celebrity_allowed=True)["celebrity_views"]
    assert [item["id"] for item in result["items"]] == ["celebrity_views:real"]
    module._CACHE["resp"]["sig"] = "old"
    assert load(stores, celebrity_allowed=True)["celebrity_views"]["items"] == []


def professional(stores):
    execute(stores["professional"], """
        CREATE TABLE professional_reports(id TEXT,title TEXT,symbol TEXT,period TEXT,created_at TEXT,updated_at TEXT,metadata_json TEXT DEFAULT '{}');
        CREATE TABLE professional_report_chunks(id TEXT,report_id TEXT,chunk_index INT,page INT,title TEXT,text TEXT);
        CREATE TABLE professional_financial_metrics(id TEXT,report_id TEXT,symbol TEXT,period TEXT,metric_key TEXT,metric_label TEXT,value REAL,normalized_value REAL,unit TEXT,raw_value TEXT,source_page INT,source_excerpt TEXT,confidence REAL);
    """, [
        ("INSERT INTO professional_reports(id,title,symbol,period,created_at,updated_at) VALUES (?,?,?,?,?,?)", ("report", "测试公司财报", "600519.SH", "2026H1", date(), date())),
        ("INSERT INTO professional_reports(id,title,symbol,period,created_at,updated_at) VALUES (?,?,?,?,?,?)", ("other", "其他公司财报", "600520", "2026H1", date(), date())),
        ("INSERT INTO professional_report_chunks VALUES (?,?,?,?,?,?)", ("chunk", "report", 0, 8, "业务", "真实经营正文")),
        ("INSERT INTO professional_report_chunks VALUES (?,?,?,?,?,?)", ("wrong", "other", 0, 8, "业务", "不可混入别的股票")),
        ("INSERT INTO professional_financial_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("metric", "report", "600519.SH", "2026H1", "revenue", "收入", 100, 1e10, "亿元", "100", 9, "收入100亿元", .95)),
    ])


def test_professional_shared_library_exact_symbol_chunks_metrics_and_citations(stores):
    professional(stores)
    result = load(stores, professional_allowed=True)["professional_reports"]
    assert result["scope"] == "shared_authenticated"
    assert result["coverage"]["scanned"] == 2  # one metric document and one cited original chunk
    chunk = next(item for item in result["items"] if item["metadata_safe"].get("chunk_id"))
    assert chunk["metadata_safe"]["report_id"] == "report"
    assert chunk["metadata_safe"]["page"] == 8
    assert chunk["visible_text"] == "真实经营正文"
    assert chunk["published_at"] is None  # ingestion timestamp is not report release date
    metric = next(item for item in result["items"] if item["metadata_safe"].get("metrics"))
    assert metric["metadata_safe"]["metrics"][0]["source_page"] == 9
    assert "不可混入" not in json.dumps(result, ensure_ascii=False)


def test_professional_capability_denied_reveals_no_counts_or_titles(stores):
    professional(stores)
    result = load(stores, professional_allowed=False)["professional_reports"]
    assert result["status"] == "access_required"
    assert result["items"] == []
    assert result["coverage"]["scanned"] == 0


def test_owned_completed_research_cannot_read_other_users_or_null_legacy_owner(stores):
    execute(stores["tasks"], "CREATE TABLE agent_tasks(id TEXT,title TEXT,symbol,result_json,completed_at TEXT,owner_user_id TEXT,status TEXT,task_type TEXT);", [
        ("INSERT INTO agent_tasks VALUES (?,?,?,?,?,?,?,?)", (owner or "legacy", "测试公司", "600519", json.dumps({"investor_summary": f"{owner} 完成观点", "raw_prompt": "不混入提示"}), date(), owner, status, "investment_research"))
        for owner, status in [("alice-id", "completed"), ("bob-id", "completed"), (None, "completed"), ("pending-id", "pending")]])
    execute(stores["memory"], "CREATE TABLE dulus_memory(id TEXT,title TEXT,content TEXT,created_at TEXT,owner_user_id TEXT,scope TEXT,source TEXT);", [
        ("INSERT INTO dulus_memory VALUES (?,?,?,?,?,?,?)", ("mine", "测试公司", "本人研究内容", date(), "alice-id", "user", "roundtable")),
        ("INSERT INTO dulus_memory VALUES (?,?,?,?,?,?,?)", ("other", "测试公司", "别人秘密", date(), "bob-id", "user", "roundtable"))])
    result = load(stores)["owned_research"]
    assert result["coverage"]["scanned"] == 2
    text = json.dumps(result, ensure_ascii=False)
    assert "alice-id 完成观点" in text and "本人研究内容" in text
    assert "bob-id" not in text and "别人秘密" not in text and "legacy" not in text and "不混入提示" not in text
    assert all(item["body_state"] == "summary_only" for item in result["items"])


def test_no_owner_is_fail_closed_even_when_query_claims_to_be_admin(stores):
    messages(stores, [{"content": "600519 原文"}])
    result = load(stores, {"symbol": "600519", "owner_id": "alice-id", "is_member": True, "celebrity_allowed": True}, owner_id="")
    assert all(channel["status"] == "access_required" and channel["coverage"]["scanned"] == 0 for channel in result.values())


def test_owner_bound_loaders_do_not_share_account_response_cache(stores):
    execute(stores["tasks"], "CREATE TABLE agent_tasks(id TEXT,title TEXT,symbol,result_json,completed_at TEXT,owner_user_id TEXT,status TEXT,task_type TEXT);", [
        ("INSERT INTO agent_tasks VALUES (?,?,?,?,?,?,?,?)", (owner, "测试公司", "600519", json.dumps({"summary": owner + "自己的资料"}), date(), owner, "completed", "investment_research"))
        for owner in ("alice-id", "bob-id")])
    first = load(stores)["owned_research"]
    second = load(stores, owner_id="bob-id", username="bob")["owned_research"]
    assert "alice-id自己的资料" in first["items"][0]["visible_text"]
    assert "bob-id自己的资料" in second["items"][0]["visible_text"]
    assert "alice" not in json.dumps(second, ensure_ascii=False)


def test_unowned_uploads_are_not_assumed_public_and_explicit_public_sources_still_use_message_gate(stores):
    execute(stores["data"], "CREATE TABLE data_items(id TEXT,title TEXT,symbol TEXT,url TEXT,text TEXT,source_name TEXT,source_type TEXT,metadata_json TEXT,collected_at TEXT);", [
        ("INSERT INTO data_items VALUES (?,?,?,?,?,?,?,?,?)", (typ, "测试公司", "600519", "https://public.test", typ + "正文", "资料", typ, json.dumps(meta), date()))
        for typ, meta in [("upload", {"access_level": "public"}), ("agent_crawl", {"access_level": "public"}), ("manual", {}), ("web_page", {"access_level": "public"}), ("server_api", {})]])
    result = load(stores)["shared_sources"]
    assert [item["id"] for item in result["items"]] == ["shared_sources:web_page"]
    assert load(stores, message_view=lambda item: None)["shared_sources"]["items"] == []


def test_character_budget_marks_excerpt_without_claiming_full_read(stores, monkeypatch):
    monkeypatch.setattr(sources, "CHANNEL_TEXT_LIMIT", 120)
    messages(stores, [{"content": "600519 " + "长正文" * 100}])
    result = load(stores)["site_fast_news"]
    assert result["items"][0]["body_state"] == "excerpt"
    assert result["coverage"]["limit_hit"] is True
    assert result["coverage"]["complete"] is False
    assert result["coverage"]["read_chars"] == 120


def test_scan_limit_is_explicit_and_output_limit_does_not_masquerade_as_complete(stores, monkeypatch):
    monkeypatch.setattr(sources, "SCAN_LIMIT", 3)
    monkeypatch.setattr(sources, "ITEM_LIMIT", 2)
    messages(stores, [{"id": str(i), "content": "600519 可见正文"} for i in range(5)])
    result = load(stores)["site_fast_news"]
    assert len(result["items"]) == 2
    assert result["status"] == "partial"
    assert result["coverage"]["limit_hit"] is True


def test_loader_opens_fixture_stores_read_only_and_does_not_create_missing_ones(stores):
    messages(stores, [{"content": "600519正文"}])
    before = hashlib.sha256(stores["messages"].read_bytes()).hexdigest()
    result = load(stores, professional_allowed=True)
    assert hashlib.sha256(stores["messages"].read_bytes()).hexdigest() == before
    assert not stores["professional"].exists()
    assert not stores["wire"].exists()
    assert result["professional_reports"]["status"] == "not_enabled"
    assert result["professional_reports"]["coverage"]["complete"] is False


def test_bad_links_are_not_forwarded_as_evidence(stores):
    messages(stores, [{"id": "unsafe", "url": "javascript:alert(1)"},
                     {"id": "signed", "url": "https://test/report?access_token=secret"},
                     {"id": "safe", "url": "https://test/public"}])
    items = {item["id"]: item for item in load(stores)["site_fast_news"]["items"]}
    assert items["site_fast_news:unsafe"]["url"] is None
    assert items["site_fast_news:signed"]["url"] is None
    assert items["site_fast_news:safe"]["url"] == "https://test/public"


def test_remote_notes_fetch_is_bounded_read_only_metadata_path_and_retains_full_visible_text(stores, monkeypatch):
    calls = []
    async def fetch_stream(**kwargs):
        calls.append(kwargs)
        return {"items": [{"id": "remote", "title": "测试公司纪要", "text": "600519 " + "全文" * 300, "date": date()[:10]}]}
    module = SimpleNamespace(_CACHE={}, stream_groups=lambda: [{"id": "allowed"}], fetch_stream=fetch_stream)
    monkeypatch.setitem(sys.modules, "deepfocus_api.zsxq_stream", module)
    result = load(stores, allow_remote=True)["institution_notes"]
    assert calls and all(call["limit"] == 40 and call["use_cache"] is True for call in calls)
    assert result["items"][0]["body_state"] == "full"
    assert len(result["items"][0]["visible_text"]) > 420


def test_remote_failure_preserves_existing_local_evidence(stores, monkeypatch):
    messages(stores, [{"content": "600519 正常快讯"}])
    async def failed(**kwargs):
        raise TimeoutError("upstream unavailable")
    monkeypatch.setitem(sys.modules, "deepfocus_api.zsxq_stream", SimpleNamespace(_CACHE={}, stream_groups=lambda: [], fetch_stream=failed))
    result = load(stores, allow_remote=True)
    assert result["site_fast_news"]["items"]
    assert result["institution_notes"]["status"] == "partial"
    assert "TimeoutError" not in json.dumps(result, ensure_ascii=False)


def test_remote_note_full_text_upgrades_same_id_persisted_teaser(stores, monkeypatch):
    execute(stores["notes"], "CREATE TABLE zsxq_share(id TEXT,title TEXT,lead TEXT,date TEXT,created_at REAL);", [
        ("INSERT INTO zsxq_share VALUES (?,?,?,?,?)", ("42", "测试公司纪要", "测试公司短导语", date()[:10], time.time()))])
    body = "测试公司 " + "业务完整原文" * 200
    async def fetch_stream(**kwargs):
        return {"items": [{"id": "42", "title": "测试公司纪要", "text": body, "date": date()[:10]}]}
    monkeypatch.setitem(sys.modules, "deepfocus_api.zsxq_stream", SimpleNamespace(_CACHE={}, stream_groups=lambda: [], fetch_stream=fetch_stream))
    result = load(stores, allow_remote=True)["institution_notes"]
    assert len(result["items"]) == 1
    assert result["items"][0]["body_state"] == "full"
    assert result["items"][0]["visible_text"] == body
    assert result["coverage"]["read_chars"] == len(body)


def test_visibility_downgrade_overrides_initial_member_capability(stores):
    messages(stores, [{"title": "行业文章", "content": "公开导语" * 30 + "600519 私密新增订单", "symbol": "600519", "topic": "文章"}])
    result = load(stores, is_member=True, message_view=lambda item: item.model_copy(update={"content": item.content[:120]}))["site_articles"]
    assert result["items"] == []


def test_disabled_celebrity_module_does_not_read_old_cache_or_query_upstream(stores, monkeypatch):
    async def fail(**kwargs):
        raise AssertionError("disabled provider was called")
    monkeypatch.setitem(sys.modules, "deepfocus_api.celebrity_views", SimpleNamespace(
        _CACHE={"resp": {"sig": "current", "resp": {"figures": [{"items": [{"id": "secret", "title": "测试公司旧观点", "body": "不展示"}]}]}}},
        get_config=lambda: {"enabled": False}, _config_signature=lambda _: "current", fetch_celebrity_views=fail))
    result = load(stores, celebrity_allowed=True, allow_remote=True)["celebrity_views"]
    assert result["status"] == "not_enabled"
    assert result["items"] == []


def test_temporary_completed_calculations_check_owner_before_result_read(stores, monkeypatch):
    class OtherJob:
        owner = "bob-id"
        state = "completed"
        @property
        def result(self):
            raise AssertionError("another user's result was read")
    own = SimpleNamespace(owner="alice-id", state="completed", touched=time.monotonic(), id="calc1", tool="financials",
                          updated_at=date(), result={"symbol": "600519", "metrics": {"revenue": 100}, "raw_upload": "private unneeded blob"})
    expired = SimpleNamespace(owner="alice-id", state="completed", touched=time.monotonic() - 2000)
    monkeypatch.setitem(sys.modules, "deepfocus_api.research_jobs", SimpleNamespace(manager=SimpleNamespace(jobs={"other": OtherJob(), "mine": own, "expired": expired})))
    result = load(stores)["owned_research"]
    assert len(result["items"]) == 1
    assert result["items"][0]["id"] == "owned_research:calculation:calc1"
    assert "revenue" in result["items"][0]["visible_text"]
    assert "private unneeded blob" not in json.dumps(result, ensure_ascii=False)


def test_shared_source_collection_date_is_not_invented_publication_date(stores):
    execute(stores["data"], "CREATE TABLE data_items(id TEXT,title TEXT,symbol TEXT,url TEXT,text TEXT,source_name TEXT,source_type TEXT,metadata_json TEXT,collected_at TEXT);", [
        ("INSERT INTO data_items VALUES (?,?,?,?,?,?,?,?,?)", ("web", "测试公司", "600519", None, "收入情况", "资料", "web_page", '{"access_level":"public"}', date()))])
    item = load(stores)["shared_sources"]["items"][0]
    assert item["published_at"] is None


def test_sector_body_ticker_mention_does_not_verify_company_or_assign_all_paragraphs_as_facts(stores):
    messages(stores, [{"title": "白酒行业周报", "content": "竞争公司利润增长50%。\n600519需要关注库存变化。\n另一个企业已获新订单。"}])
    item = load(stores, {"symbol": "600519", "name": "目标公司", "market": "CN"})["site_fast_news"]["items"][0]
    assert item["relation_refs"][0]["verification"] == "context"
    assert item["metadata_safe"]["match_basis"] == "visible_symbol"
    assert item["entity_refs"] == []
    assert item["symbol"] == ""
    assert item["role"] == "context"


@pytest.mark.parametrize("row,basis", [
    ({"title": "行业周报", "symbol": "600519", "content": "主体来自provider"}, "symbol_exact"),
    ({"title": "600519 财报解读", "content": "收入增长"}, "title_symbol"),
    ({"title": "测试公司：财报解读", "content": "利润改善"}, "title_company_subject"),
])
def test_company_verified_only_primary_provider_or_clear_title_subject(stores, row, basis):
    messages(stores, [row])
    item = load(stores)["site_fast_news"]["items"][0]
    assert item["relation_refs"][0]["verification"] == "verified"
    assert item["metadata_safe"]["match_basis"] == basis
    assert item["symbol"] == "600519"


def test_company_name_prefix_cannot_verify_related_company_title(stores):
    messages(stores, [{"title": "比亚迪电子：财报解读", "content": "业务增长"}])
    item = load(stores, {"symbol": "002594", "name": "比亚迪", "market": "CN"})["site_fast_news"]["items"][0]
    assert item["relation_refs"][0]["verification"] == "context"
    assert item["entity_refs"] == []


def test_teaser_body_code_cannot_inherit_original_primary_symbol_verification(stores):
    messages(stores, [{"title": "行业研读", "topic": "文章", "symbol": "600519", "content": "600519短导语。" + "会员全文" * 100}])
    item = load(stores, message_view=lambda item: item.model_copy(update={"content": item.content[:20]}))["site_articles"]["items"][0]
    assert item["body_state"] == "teaser"
    assert item["relation_refs"][0]["verification"] == "context"
    assert item["entity_refs"] == []


def test_total_remote_deadline_returns_completed_local_evidence_without_exposing_denied_sources(stores, monkeypatch):
    messages(stores, [{"title": "600519本地快讯", "content": "本地订单已增长"}])
    cancelled = []
    async def slow(self):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.append(True)
    monkeypatch.setattr(sources._Collector, "remote", slow)
    monkeypatch.setattr(sources, "REMOTE_BUDGET", .005)
    result = load(stores, allow_remote=True)
    assert cancelled == [True]
    assert result["site_fast_news"]["items"][0]["visible_text"] == "本地订单已增长"
    assert result["institution_notes"]["status"] == "partial"
    assert "总时限" in result["institution_notes"]["coverage"]["reason"]
    assert result["recent_research"]["status"] == "access_required"
    assert result["celebrity_views"]["coverage"]["scanned"] == 0
