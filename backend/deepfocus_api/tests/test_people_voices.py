"""人物专题解析单测（纯函数，不触网）。

样本取自 Google News RSS 的真实 <item> 结构，验证：标题去冗余出处、
相关性过滤（must_any / block，重点是「奥特曼」与特摄角色去歧义）、
主题打标签、时效评分、档案与数据可信度组装。
"""
import asyncio
from datetime import datetime, timedelta, timezone

from deepfocus_api.people_voices import (
    FIGURES_BY_ID,
    _build_profile,
    _classify_topics,
    _clean_title,
    _gather_bounded,
    _parse_rss_items,
    digest_cache_key,
)
from deepfocus_api.schemas import PersonVoiceItem


def _rss(items_xml: str) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/">'
        f"<channel>{items_xml}</channel></rss>"
    )


# 时效评分依赖真实墙钟：头条 pubDate 用「6 小时前」动态生成，测试不随日期推移而过期。
_HUANG_PUB_DT = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=6)
_HUANG_PUB_RFC822 = _HUANG_PUB_DT.strftime("%a, %d %b %Y %H:%M:%S GMT")
_HUANG_PUB_DATE_CST = _HUANG_PUB_DT.astimezone(timezone(timedelta(hours=8))).strftime("%Y-%m-%d")

_HUANG_ITEMS = _rss(
    f"""
    <item>
      <title>黄仁勋刚抵韩就放“大招”：宣布HBM4均获认证 - 财联社</title>
      <link>https://news.google.com/rss/articles/HUANG1</link>
      <guid>HUANG1</guid>
      <pubDate>{_HUANG_PUB_RFC822}</pubDate>
      <description>&lt;a href="x"&gt;相关报道&lt;/a&gt;</description>
      <source url="https://cls.cn">财联社</source>
    </item>
    <item>
      <title>无关公司财报快讯 - 某网</title>
      <link>https://news.google.com/rss/articles/NOISE</link>
      <guid>NOISE</guid>
      <pubDate>Fri, 05 Jun 2026 01:00:00 GMT</pubDate>
      <source url="https://x.com">某网</source>
    </item>
    """
)

_ALTMAN_ITEMS = _rss(
    """
    <item>
      <title>OpenAI前CTO回忆奥特曼被罢免 - 凤凰网</title>
      <link>https://news.google.com/rss/articles/ALT1</link>
      <pubDate>Sat, 06 Jun 2026 02:00:00 GMT</pubDate>
      <source url="https://ifeng.com">凤凰网</source>
    </item>
    <item>
      <title>奥特曼新电影定档：迪迦与赛罗同框登场 - 某影视</title>
      <link>https://news.google.com/rss/articles/ULTRA</link>
      <pubDate>Sat, 06 Jun 2026 02:00:00 GMT</pubDate>
      <source url="https://x.com">某影视</source>
    </item>
    """
)


def test_clean_title_strips_trailing_source():
    assert _clean_title("黄仁勋放大招：HBM4获认证 - 财联社", "财联社") == "黄仁勋放大招：HBM4获认证"
    # 无 source 元数据时，按短尾 token 启发式去掉 " - RFI"
    assert _clean_title("黄仁勋评价华为半导体新突破 - RFI", "") == "黄仁勋评价华为半导体新突破"
    # 句子里带破折号但尾段是长句时不应被误删
    kept = "特朗普：协议如达成 - 否则继续施压，态度强硬"
    assert _clean_title(kept, "新华网") == kept


def test_must_any_filters_irrelevant_items():
    items = _parse_rss_items(_HUANG_ITEMS, FIGURES_BY_ID["huang"])
    titles = [it.title for it in items]
    assert any("HBM4" in t for t in titles)
    assert all("无关公司财报快讯" not in t for t in titles)  # 不含黄仁勋/英伟达 → 被过滤


def test_block_terms_disambiguate_ultraman():
    items = _parse_rss_items(_ALTMAN_ITEMS, FIGURES_BY_ID["altman"])
    titles = [it.title for it in items]
    assert any("OpenAI" in t for t in titles)  # 真·Sam Altman 命中 must_any
    assert all("迪迦" not in t and "赛罗" not in t for t in titles)  # 特摄角色被 block 过滤


def test_topic_classification():
    assert "AI" in _classify_topics("黄仁勋谈大模型与算力", "")
    assert "地缘" in _classify_topics("特朗普：乐见普泽会 俄乌会妥协", "")
    assert "资本" in _classify_topics("SpaceX估值下调至1.8万亿美元", "")


def test_recency_and_authority_scoring():
    items = _parse_rss_items(_HUANG_ITEMS, FIGURES_BY_ID["huang"])
    assert items, "应至少解析出黄仁勋的相关条目"
    top = items[0]
    assert top.source_name == "财联社"
    assert top.reported_date == _HUANG_PUB_DATE_CST  # GMT → +08:00 折算
    assert top.importance_score >= 70  # 近期(≤1天+20) + 权威源(+10)


def test_build_profile_quality_live_when_items_present():
    items = _parse_rss_items(_HUANG_ITEMS, FIGURES_BY_ID["huang"])
    profile = _build_profile(FIGURES_BY_ID["huang"], items, [])
    assert profile.name == "黄仁勋"
    assert profile.item_count == len(items)
    assert profile.data_quality.level == "live"
    assert profile.latest_date == items[0].reported_date


def test_build_profile_degraded_when_empty():
    profile = _build_profile(FIGURES_BY_ID["amodei"], [], ["新闻源暂不可用"])
    assert profile.item_count == 0
    assert profile.data_quality.level == "degraded"


def test_gather_bounded_caps_concurrency_and_keeps_order():
    state = {"current": 0, "max": 0}

    def make(i):
        async def factory():
            state["current"] += 1
            state["max"] = max(state["max"], state["current"])
            await asyncio.sleep(0.01)
            state["current"] -= 1
            return i

        return factory

    factories = [make(i) for i in range(10)]
    results = asyncio.run(_gather_bounded(3, factories))

    assert results == list(range(10))  # 结果按入参顺序返回
    assert state["max"] <= 3  # 在途并发被限到 3
    assert state["max"] >= 2  # 但确实并发了，不是退化成串行


def _profile_with_titles(figure_id, titles):
    items = [
        PersonVoiceItem(id=f"i{i}", title=t, url="https://example.com/x", source_name="财联社")
        for i, t in enumerate(titles)
    ]
    return _build_profile(FIGURES_BY_ID[figure_id], items, [])


def test_digest_cache_key_invalidates_on_headline_change():
    same_a = _profile_with_titles("huang", ["黄仁勋抵韩宣布 HBM4 认证"])
    same_b = _profile_with_titles("huang", ["黄仁勋抵韩宣布 HBM4 认证"])
    changed = _profile_with_titles("huang", ["黄仁勋谈华为半导体突破"])
    other_person = _profile_with_titles("musk", ["黄仁勋抵韩宣布 HBM4 认证"])

    assert digest_cache_key(same_a) == digest_cache_key(same_b)  # 标题不变 → 命中缓存
    assert digest_cache_key(same_a) != digest_cache_key(changed)  # 有新发言 → 键变、重算
    assert digest_cache_key(same_a) != digest_cache_key(other_person)  # 人物不同 → 键不同
