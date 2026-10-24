"""事件簇引擎回归测试：聚类正确性、故事 ID 稳定性、重要度单调性、卡片不编造、简报确定性。

夹具把三个库（内容本体 / 故事 / 证券主数据）全部指到 tmp_path，测试不碰仓库里的任何真实数据。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import pytest

from deepfocus_api import securities_master, story_engine
from deepfocus_api.content_ontology import annotate_content

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc)

SECURITIES = [
    ("600519", "贵州茅台", "CN"),
    ("300750", "宁德时代", "CN"),
    ("002594", "比亚迪", "CN"),
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "ontology.sqlite3"))
    monkeypatch.setenv("DEEPFOCUS_STORY_DB_PATH", str(tmp_path / "stories.sqlite3"))
    monkeypatch.setattr(securities_master, "DB_PATH", tmp_path / "realtime.sqlite3")
    securities_master._ALIASES = None
    story_engine.reset_caches()
    securities_master.upsert_securities(SECURITIES)
    yield
    securities_master._ALIASES = None
    story_engine.reset_caches()


def _add(
    content_id: str,
    title: str,
    *,
    content_type: str = "flash",
    text: str = "",
    source_name: str = "财联社",
    hours_ago: float = 1.0,
    symbol: str = "",
    evidence_role: str = "context",
) -> None:
    annotate_content(
        content_id=content_id,
        content_type=content_type,
        title=title,
        text=text,
        source_name=source_name,
        symbol=symbol,
        published_at=(NOW - timedelta(hours=hours_ago)).isoformat(),
        access_level="public",
        evidence_role=evidence_role,
    )


def _seed_maotai_story() -> None:
    """同一件事的多来源多体裁报道：应聚成一个公司故事。"""
    _add("c1", "贵州茅台上调出厂价 渠道批价同步走高", source_name="交易所公告", hours_ago=2.0)
    _add("c2", "贵州茅台上调出厂价 经销商确认已经执行", source_name="财联社", hours_ago=3.0)
    _add("c3", "贵州茅台提价落地 白酒渠道库存仍处高位", source_name="彭博", hours_ago=5.0)
    _add(
        "c4", "贵州茅台提价影响测算：吨价提升但销量存不确定",
        content_type="research", source_name="某证券研究所", hours_ago=6.0, evidence_role="opinion",
        text="我们认为提价对全年收入弹性有限，渠道库存仍需消化。",
    )
    _add(
        "c5", "贵州茅台机构交流纪要：批价与库存是核心跟踪指标",
        content_type="institution_note", source_name="机构纪要", hours_ago=8.0, evidence_role="opinion",
        text="管理层强调价格体系稳定，风险提示需求疲弱。",
    )


# ---------------------------------------------------------------------------
# 文本工具
# ---------------------------------------------------------------------------

def test_clean_title_strips_emoji_and_whitespace():
    assert story_engine.clean_title("  🌅 【重磅】 贵州茅台   提价  ") == "【重磅】 贵州茅台 提价"
    assert story_engine.clean_title("") == "未命名事件"
    assert story_engine.clean_title("🔥🚀 ") == "未命名事件"
    assert story_engine.clean_title("a" * 200, limit=50) == "a" * 50


def test_title_signature_uses_cjk_bigrams_and_latin_words():
    sig = story_engine.title_signature("贵州茅台上调出厂价 NVDA 算力")
    assert {"贵州", "州茅", "茅台", "台上", "上调"} <= sig
    assert "nvda" in sig
    assert "算" not in sig  # 长度 ≥2 的中文串才产生二元组


def test_jaccard_bounds():
    assert story_engine.jaccard({"a", "b"}, {"a", "b"}) == 1.0
    assert story_engine.jaccard(set(), {"a"}) == 0.0
    assert story_engine.jaccard({"a", "b", "c"}, {"c", "d"}) == pytest.approx(1 / 4)


def test_parse_ts_handles_mixed_production_formats():
    assert story_engine.parse_ts("2026-10-24T06:02:26.475827+00:00").hour == 6
    # +0800 的 14:00 = UTC 06:00
    assert story_engine.parse_ts("2026-10-24T14:00:37.443+0800").hour == 6
    # 纯日期按北京时间零点理解
    assert story_engine.parse_ts("2026-10-24") == datetime(2026, 10, 23, 16, 0, tzinfo=timezone.utc)
    assert story_engine.parse_ts("2026-10-24T06:02:26") == datetime(2026, 10, 23, 22, 2, 26, tzinfo=timezone.utc)
    assert story_engine.parse_ts("") is None
    assert story_engine.parse_ts("未知时间") is None


def test_extract_symbols_prefers_longest_and_skips_generic_words(env):
    found = story_engine.extract_symbols("贵州茅台与宁德时代签署战略合作")
    symbols = [item["symbol"] for item in found]
    assert symbols == ["600519", "300750"]
    assert found[0]["name"] == "贵州茅台"
    assert found[0]["market"] == "CN"
    # 行业通用词不能当成公司简称
    assert story_engine.extract_symbols("新能源汽车产业链能源价格波动") == []
    # 英文简称必须整词命中，避免 META 命中 METALL
    assert story_engine.extract_symbols("METALLURGY 行业报告") == []


def test_extract_symbols_falls_back_to_builtin_hk_us(env):
    found = story_engine.extract_symbols("腾讯控股发布新模型")
    assert found and found[0]["symbol"] == "00700"
    assert found[0]["market"] == "HK"


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

def _synthetic(title: str, **overrides):
    item = {
        "id": overrides.pop("id", title),
        "content_type": "flash",
        "title": title,
        "summary": "",
        "source_name": "财联社",
        "url": "",
        "published_at": "",
        "ts": NOW,
        "access_level": "public",
        "evidence_role": "context",
        "themes": [],
        "events": [],
        "signals": [],
        "horizons": [],
        "source_tiers": ["media"],
        "markets": [],
        "entities": [],
        "tier": "media",
        "score": 0.4,
    }
    item.update(overrides)
    item["sig"] = story_engine.title_signature(title)
    return item


def test_cluster_items_merges_paraphrases_and_keeps_unrelated_apart():
    items = [
        _synthetic("宁德时代发布新一代磷酸铁锂电池"),
        _synthetic("宁德时代发布新一代磷酸铁锂电池 能量密度提升"),
        _synthetic("美联储议息会议维持利率不变"),
    ]
    sigs, _ = story_engine.filtered_signatures(items)
    groups = story_engine.cluster_items(sigs)
    assert sorted(len(group) for group in groups) == [1, 2]
    big = max(groups, key=len)
    assert {items[idx]["title"] for idx in big} == {items[0]["title"], items[1]["title"]}


def test_filtered_signatures_drops_over_frequent_tokens():
    tokens = [
        "产能", "库存", "订单", "价格", "毛利", "费用", "现金", "资本", "研发", "客户",
        "渠道", "供应", "交付", "招标", "并购", "回购", "增持", "减持", "分红", "评级",
        "盈利", "收入", "成本", "销量", "份额", "政策", "监管", "诉讼", "人事", "专利",
    ]
    items = [_synthetic(f"公司发布公告 关于{token}项目的说明") for token in tokens]
    sigs, rares = story_engine.filtered_signatures(items)
    # 30 条全含「公司/公告」→ 通用签名剔除，否则整桶会被并成一簇
    assert all("公司" not in sig and "公告" not in sig for sig in sigs)
    # 每条仍保留自己的区分性签名，不会退化成孤立点
    assert all(len(sig) >= 2 for sig in sigs)
    assert all(rares[index] for index in range(len(items)))
    # 不修改入参：同一条内容可以归属多个桶，签名不能被就地覆盖
    assert all("公司" in item["sig"] for item in items)


def test_buckets_split_by_entity_and_event(env):
    item = _synthetic("贵州茅台上调出厂价", entities=[{"symbol": "600519", "name": "贵州茅台", "market": "CN"}], events=["price_change"])
    assert story_engine.bucket_of(item)[0] == "sec:600519:price_change"
    assert story_engine.bucket_of(item)[1] == "company"
    themed = _synthetic("算力需求持续扩张", themes=["ai"], events=["product"])
    assert story_engine.bucket_of(themed)[0] == "theme:ai:product"
    loose = _synthetic("某地天气影响物流")
    assert story_engine.bucket_of(loose)[0] == "misc"


def test_multi_entity_content_lands_in_every_company_bucket(env):
    item = _synthetic(
        "宁德时代与比亚迪签署电池供应协议",
        entities=[
            {"symbol": "300750", "name": "宁德时代", "market": "CN"},
            {"symbol": "002594", "name": "比亚迪", "market": "CN"},
        ],
        events=["order"],
    )
    buckets = [bucket for bucket, _, _ in story_engine.buckets_for(item)]
    assert buckets == ["sec:300750:order", "sec:002594:order"]


# ---------------------------------------------------------------------------
# 端到端构建
# ---------------------------------------------------------------------------

def test_rebuild_stories_clusters_one_event_into_one_company_story(env):
    _seed_maotai_story()
    stats = story_engine.rebuild_stories(window_hours=72, now=NOW)
    assert stats["content_items"] == 5
    assert stats["stories_persisted"] >= 1
    assert stats["company_stories"] >= 1

    cards = story_engine.list_stories(limit=20, now=NOW)
    company = [card for card in cards if card["kind"] == "company" and card["symbol"] == "600519"]
    assert len(company) == 1, f"同一件事被拆成多张卡：{[c['title'] for c in company]}"
    card = company[0]
    assert card["entity_name"] == "贵州茅台"
    assert card["event_label"] == "价格变化"
    assert card["item_count"] == 5
    assert card["source_count"] == 5
    assert card["by_role"]["opinion"] == 2
    assert 0 < card["mass"] <= 1
    assert card["impact"]
    assert card["verify"]
    assert card["why"]
    assert card["timeline"]
    assert "不构成投资建议" in card["disclaimer"]


def test_story_summary_only_reports_observed_counts(env):
    _seed_maotai_story()
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    card = next(c for c in story_engine.list_stories(limit=20, now=NOW) if c["symbol"] == "600519")
    assert "5 条相关内容" in card["summary"]
    assert "5 个来源" in card["summary"]
    # 不编造：没有目标价、没有买卖指令、没有未出现的数字结论
    for banned in ("目标价", "买入", "卖出", "建议"):
        assert banned not in card["summary"]


def test_story_ids_are_stable_across_rebuilds(env):
    _seed_maotai_story()
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    first = {card["story_id"] for card in story_engine.list_stories(limit=20, now=NOW)}
    # 新增一条同事件报道后重建：故事 ID 必须复用，埋点与个人化权重才不会断链
    _add("c6", "贵州茅台上调出厂价 多地经销商已收到通知", source_name="路透社", hours_ago=0.5)
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    second = {card["story_id"] for card in story_engine.list_stories(limit=20, now=NOW)}
    assert first <= second
    card = next(c for c in story_engine.list_stories(limit=20, now=NOW) if c["symbol"] == "600519")
    assert card["item_count"] == 6
    assert card["source_count"] == 6


def test_more_sources_and_freshness_raise_mass(env):
    _add("a1", "比亚迪海外销量创新高 出口结构改善", source_name="交易所公告", hours_ago=1.0)
    _add("a2", "比亚迪海外销量创新高 欧洲市场占比提升", source_name="财联社", hours_ago=1.5)
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    strong = next(c for c in story_engine.list_stories(limit=20, now=NOW) if c["symbol"] == "002594")

    _add("b1", "宁德时代海外销量创新高 出口结构改善", source_name="社区帖", hours_ago=60.0)
    _add("b2", "宁德时代海外销量创新高 欧洲市场占比提升", source_name="社区帖", hours_ago=61.0)
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    cards = {card["symbol"]: card for card in story_engine.list_stories(limit=20, now=NOW) if card["kind"] == "company"}
    assert cards["002594"]["mass"] > cards["300750"]["mass"]
    assert cards["002594"]["mass"] >= strong["mass"] - 1e-9


def test_story_detail_splits_facts_opinions_and_risks(env):
    _seed_maotai_story()
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    story_id = next(c for c in story_engine.list_stories(limit=20, now=NOW) if c["symbol"] == "600519")["story_id"]
    detail = story_engine.story_detail(story_id)
    assert detail is not None
    assert len(detail["facts"]) == 3
    assert len(detail["opinions"]) == 2
    assert all(item["source_name"] for item in detail["facts"])
    assert all(item["content_type_label"] for item in detail["opinions"])
    assert story_engine.story_detail("story:does-not-exist") is None


def test_stories_for_symbol_returns_timeline(env):
    _seed_maotai_story()
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    timeline = story_engine.stories_for_symbol("600519", days=7, with_items=3)
    assert len(timeline) == 1
    assert timeline[0]["item_count"] == 5
    assert timeline[0]["items"]
    assert story_engine.stories_for_symbol("") == []
    assert story_engine.stories_for_symbol("999999") == []


def test_window_excludes_stale_content(env):
    _add("old1", "贵州茅台去年渠道调研纪要", hours_ago=200.0)
    _add("old2", "贵州茅台去年渠道调研纪要 续", hours_ago=201.0)
    _seed_maotai_story()
    stats = story_engine.rebuild_stories(window_hours=72, now=NOW)
    assert stats["content_items"] == 5
    assert all("去年" not in card["title"] for card in story_engine.list_stories(limit=20, now=NOW))


def test_missing_content_store_degrades_to_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_STORY_DB_PATH", str(tmp_path / "stories.sqlite3"))
    monkeypatch.setenv("DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH", str(tmp_path / "absent.sqlite3"))
    stats = story_engine.rebuild_stories(window_hours=72, now=NOW)
    assert stats["content_source"] == "missing"
    assert stats["content_items"] == 0
    assert stats["stories_persisted"] == 0
    assert story_engine.list_stories(limit=10, now=NOW) == []
    assert story_engine.story_health()["stories"] == 0


# ---------------------------------------------------------------------------
# 大众简报
# ---------------------------------------------------------------------------

def test_mass_brief_is_structured_deterministic_and_neutral(env):
    _seed_maotai_story()
    _add("t1", "算力需求持续扩张 数据中心订单饱满", source_name="财联社", hours_ago=2.0)
    _add("t2", "算力需求持续扩张 数据中心订单饱满 多家厂商确认", source_name="彭博", hours_ago=3.0)
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    cards = story_engine.list_stories(limit=30, window_hours=72, with_items=5, now=NOW)
    brief = story_engine.build_mass_brief(cards, window_hours=72)
    assert brief["scope"] == "mass"
    assert [section["key"] for section in brief["sections"]] == ["themes", "companies", "risks"]
    assert brief["stats"]["stories"] == len(cards)
    assert brief["headline"]

    text = story_engine.render_brief_text(brief)
    assert text == story_engine.render_brief_text(story_engine.build_mass_brief(cards, window_hours=72))
    assert "站内事件简报" in text
    assert "不构成投资建议" in text
    for banned in ("建议买入", "建议卖出", "目标价", "必涨", "稳赚"):
        assert banned not in text
    # 每件事都必须带可核对的下一步，否则简报只是又一层信息堆叠
    assert "待核对" in text
    assert "影响" in text


def test_theme_story_covers_content_without_symbol(env):
    for index in range(3):
        _add(f"ai{index}", f"算力需求持续扩张 数据中心订单饱满 第{index}批交付", source_name="财联社", hours_ago=1.0 + index)
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    themes = [card for card in story_engine.list_stories(limit=30, now=NOW) if card["kind"] == "theme"]
    assert themes, "无标的的内容必须仍能聚成主题故事，否则六成内容无人认领"
    assert themes[0]["item_count"] >= 2
    assert themes[0]["theme_labels"]


def test_story_health_reports_build_state(env):
    _seed_maotai_story()
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    health = story_engine.story_health()
    assert health["stories"] >= 1
    assert health["story_items"] >= 5
    assert health["last_build"]
    assert health["top"]


# ---------------------------------------------------------------------------
# 生产样本回归：2026-10-24 用 3792 条真实内容 + 5597 条证券主数据验证后补的门槛。
# 这些用例锁住的是真实数据暴露出来的四类失真：同源重复入库、机器播报刷屏、
# 券商署名被当成事件主体、占位式标题/摘要被当成内容。
# ---------------------------------------------------------------------------

def test_same_source_duplicates_are_dropped_but_cross_source_counts(env):
    _add("d1", "贵州茅台上调出厂价 渠道批价同步走高", source_name="财联社", hours_ago=1.0)
    _add("d2", "贵州茅台上调出厂价  渠道批价同步走高", source_name="财联社", hours_ago=2.0)
    _add("d3", "贵州茅台上调出厂价 渠道批价同步走高", source_name="彭博", hours_ago=3.0)
    result = story_engine.rebuild_stories(window_hours=72, now=NOW)
    assert result["duplicates_dropped"] == 1
    cards = story_engine.list_stories(limit=10, now=NOW, symbol="600519")
    assert cards, "去重不能把整件事去没了"
    assert cards[0]["item_count"] == 2
    assert cards[0]["source_count"] == 2


def test_machine_alert_stream_never_forms_a_story(env):
    for index in range(8):
        _add(
            f"alert{index}", f"机会·短时急拉｜贵州茅台(600519.SH) 第{index}次",
            source_name="DAO财经", content_type="evidence", hours_ago=1.0 + index,
        )
    result = story_engine.rebuild_stories(window_hours=72, now=NOW)
    assert result["boilerplate_skipped"] == 8
    assert story_engine.list_stories(limit=10, now=NOW, symbol="600519") == []
    assert story_engine.is_boilerplate("图片动态")
    assert not story_engine.is_boilerplate("贵州茅台上调出厂价")


def test_broker_byline_and_publisher_names_are_not_story_subjects(env):
    securities_master.upsert_securities([("601211", "国泰海通", "CN"), ("600028", "中国石化", "CN")])
    securities_master._ALIASES = None
    story_engine.reset_caches()
    title = "【国泰海通煤炭 | 黄涛团队】中国石化三季报点评：分红持续提高"
    assert [item["name"] for item in story_engine.extract_symbols(title)] == ["中国石化"]
    # 券商简称只降级不删除：通篇只讲券商自己时仍然采信，避免误杀「券商合并」这类真新闻
    assert [item["name"] for item in story_engine.extract_symbols("国泰海通发布2026年策略展望")] == ["国泰海通"]
    # 正文中间的括号不是署名，其中的主体照常采信
    middle = story_engine.extract_symbols("浙商大制造【中国石化】英伟达平台扩容")
    assert "中国石化" in [item["name"] for item in middle]


def test_publisher_subject_story_is_demoted_and_excluded_from_brief(env):
    securities_master.upsert_securities([("601211", "国泰海通", "CN")])
    securities_master._ALIASES = None
    story_engine.reset_caches()
    for index in range(3):
        _add(
            f"pub{index}", f"国泰海通策略观点更新 第{index}期", source_name="机构纪要",
            content_type="institution_note", hours_ago=1.0 + index, evidence_role="opinion",
            text="市场成交量回升，风险提示海外利率波动加剧。",
        )
    for index in range(3):
        _add(f"mo{index}", f"贵州茅台批价上行 渠道打款加快 第{index}批", source_name="财联社", hours_ago=1.0 + index)
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    cards = story_engine.list_stories(limit=20, now=NOW, min_items=2)
    by_entity = {card["entity_name"]: card for card in cards}
    assert "国泰海通" in by_entity and "贵州茅台" in by_entity
    assert by_entity["国泰海通"]["mass"] < by_entity["贵州茅台"]["mass"]
    brief = story_engine.build_mass_brief(cards)
    subjects = {item["subject"] for section in brief["sections"] for item in section["items"]}
    assert "国泰海通" not in subjects


def test_placeholder_titles_and_summaries_never_reach_the_card(env):
    _add("ph1", "贵州茅台20261023", content_type="research", source_name="海外投行", hours_ago=1.0,
         text="海外投行 · 2026-10-23 · 海外投行报告")
    _add("ph2", "贵州茅台20261022", content_type="research", source_name="海外投行", hours_ago=2.0,
         text="海外投行报告")
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    card = story_engine.list_stories(limit=5, now=NOW, symbol="600519")[0]
    assert card["item_count"] == 2
    assert not re.search(r"20\d{6}", card["title"])
    assert card["title"].startswith("贵州茅台")
    assert "海外投行报告" not in card["summary"]
    assert "暂无可直接引用" in card["summary"]


def test_real_excerpt_wins_over_placeholder(env):
    _add("ex1", "贵州茅台三季报点评：营收符合预期", content_type="research", source_name="海外投行",
         hours_ago=1.0, text="海外投行 · 2026-10-23 · 海外投行报告")
    _add("ex2", "贵州茅台三季报点评：毛利率保持稳定", content_type="research", source_name="海外投行",
         hours_ago=2.0, text="公司前三季度实现营业收入1200亿元，同比增长8%，毛利率维持在91%以上。")
    story_engine.rebuild_stories(window_hours=72, now=NOW)
    card = story_engine.list_stories(limit=5, now=NOW, symbol="600519")[0]
    assert "毛利率维持在91%" in card["summary"]
    assert "海外投行报告" not in card["summary"]


def test_single_source_stream_mass_is_penalized():
    def build(sources):
        items = [
            _synthetic(f"市场快照 第{index}期 成交量与指数同步波动", source_name=sources[index % len(sources)])
            for index in range(6)
        ]
        return story_engine.build_story_card(
            items, story_id="stream", bucket="misc", kind="macro", window_hours=72, now=NOW
        )

    stream = build(["DAO财经"])
    corroborated = build(["财联社", "彭博", "交易所公告", "某证券研究所", "机构纪要", "DAO财经"])
    assert stream["item_count"] == corroborated["item_count"] == 6
    assert stream["source_count"] == 1 and corroborated["source_count"] == 6
    # 同样 6 条内容：单来源刷屏必须显著低于六家来源相互印证
    assert stream["mass"] < corroborated["mass"] * 0.6
    assert "单一来源，建议交叉验证" in stream["why"]
    assert "单一来源，建议交叉验证" not in corroborated["why"]


def test_cluster_requires_shared_rare_signature_not_just_jargon():
    # 四份研报共享大量行业术语，但各自讲的是不同的事：不能靠术语链式并成一坨
    items = [
        _synthetic("机器人产业链更新 减速器订单饱满 产能爬坡顺利"),
        _synthetic("机器人产业链更新 丝杠良率提升 客户验证推进"),
        _synthetic("半导体设备更新 刻蚀机招标落地 国产替代加速"),
        _synthetic("半导体设备更新 薄膜沉积放量 稼动率回升"),
    ]
    sigs, _ = story_engine.filtered_signatures(items)
    groups = story_engine.cluster_items(sigs, threshold=story_engine.JACCARD_COMPANY)
    assert sorted(len(group) for group in groups) == [1, 1, 1, 1]
