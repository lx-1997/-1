"""深度研判的统一内容索引层。

各数据工具可以保持自己的上游协议，但进入模型前必须经过同一层：

* 统一内容对象（类型、标的、时间、来源、权限、证据角色）；
* 复用 ``content_ontology`` 做多维标签和可解释置信度标注；
* 按标题/事件去重，按相关性、来源可信度和新鲜度排序；
* 给每类内容设置预算，生成短证据摘要而不是把原文全部回灌给模型。

这是一个纯内存的读路径索引，标注结果可持久化到现有内容本体 SQLite；不写交易状态，
也不改变任何上游数据源。后续接 embedding/reranker 时，只需替换 ``_relevance``，
不需要改变 Harness 或前端协议。
"""
from __future__ import annotations

import hashlib
import math
import re
from datetime import datetime, timezone
from typing import Any, Iterable, Optional
from types import SimpleNamespace

from .content_ontology import ANNOTATION_VERSION, annotate_content


# 目标价/评级/估值假设采用确定性抽取：只作为“可核验提及”返回，不把抽取结果
# 自动当成推荐。后续接 LLM/RAG 时沿用同一字段即可做交叉验证。
_TARGET_PRICE_RE = re.compile(
    r"(?:目标价|目标位|target\s*price|price\s*target)\s*(?:上调|下调|至|为|:|：|raised\s*to|cut\s*to)?\s*"
    r"(?P<currency>HK\$|港元|美元|US\$|人民币|\$|¥|￥)?\s*(?P<value>\d+(?:\.\d+)?)"
    r"\s*(?P<currency_suffix>港元|美元|人民币|HKD|USD|CNY)?",
    re.IGNORECASE,
)
_RATING_RULES: tuple[tuple[str, str], ...] = (
    ("strong_buy", r"强烈买入|强推|strong\s*buy"),
    ("buy", r"买入|增持|推荐|跑赢大市|优于大市|outperform|overweight|buy"),
    ("hold", r"中性|持有|观望|neutral|hold"),
    ("sell", r"卖出|减持|回避|underperform|underweight|sell"),
)
_VALUATION_RE = re.compile(
    r"(?:给予|对应|按|目标估值|估值|市盈率|市净率|PE|P/E|PB|PEG)"
    r"[^\n。；;]{0,24}?(?P<value>\d+(?:\.\d+)?)\s*(?P<unit>倍|x|X)?",
    re.IGNORECASE,
)
_KNOWN_ENTITY_CODES: dict[str, str] = {
    "建滔集团": "00148", "建滔积层板": "01888", "英伟达": "NVDA", "Marvell": "MRVL",
    "台积电": "TSM", "生益科技": "600183", "深南电路": "002916", "长鑫存储": "688249",
    "铠侠": "285A", "光迅科技": "002281", "中际旭创": "300308", "新易盛": "300502",
    "天孚通信": "300394", "寒武纪": "688256", "工业富联": "601138", "紫光股份": "000938",
    "兆易创新": "603986", "TCL电子": "01070", "合锻智能": "603011", "欧科亿": "688308",
    "中材国际": "600970", "麦格米特": "002851", "联讯仪器": "688631", "星宇股份": "601799",
}
_ENTITY_NAME_CODE_CACHE: Optional[dict[str, str]] = None
_THEME_FALLBACKS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("人工智能", ("人工智能", "算力", "gpu", "cpo", "光模块", "数据中心", "存储", "大模型")),
    ("半导体", ("半导体", "芯片", "晶圆", "光刻", "封装", "电子布", "覆铜板", "ccl", "pcb")),
    ("有色与贵金属", ("黄金", "铜", "铝", "锂", "稀土", "贵金属")),
    ("能源", ("原油", "天然气", "光伏", "储能", "风电")),
    ("医药医疗", ("医药", "医疗", "创新药")),
    ("汽车与新能源车", ("汽车", "电池", "智驾", "新能源车")),
    ("出海与全球化", ("出海", "海外", "出口", "全球份额")),
    ("金融", ("银行", "券商", "保险", "美债", "利率")),
    ("宏观经济", ("通胀", "降息", "加息", "汇率", "gdp", "就业", "杰克逊霍尔")),
    ("消费", ("消费", "白酒", "食品饮料", "零售")),
)
_FINANCE_KEYWORDS = ("银行", "券商", "保险", "美债", "利率")


CATEGORY_BUDGETS: dict[str, int] = {
    "site_fast_news": 6,
    "site_articles": 4,
    "stock_research": 4,
    "recent_research": 4,
    "institution_notes": 4,
    "celebrity_views": 2,
    "stock_news": 6,
    "evidence_rag": 6,
    # 结构化行业/同行/产业链上下文单独计数，避免和公司直接新闻混在一起。
    "industry_context": 4,
    "peer_comparison": 6,
    "supply_chain": 6,
}

_CATEGORY_META: dict[str, dict[str, str]] = {
    "site_fast_news": {"label": "快讯", "content_type": "flash", "role": "fact", "access": "public"},
    "site_articles": {"label": "文章", "content_type": "article", "role": "analysis", "access": "public"},
    "stock_research": {"label": "研报", "content_type": "research", "role": "opinion", "access": "public"},
    "recent_research": {"label": "投行研报", "content_type": "research", "role": "opinion", "access": "public"},
    "institution_notes": {"label": "机构纪要", "content_type": "institution_note", "role": "opinion", "access": "public"},
    "celebrity_views": {"label": "名人观点", "content_type": "celebrity_view", "role": "opinion", "access": "whitelist"},
    "stock_news": {"label": "公开资讯", "content_type": "evidence", "role": "fact", "access": "public"},
    "evidence_rag": {"label": "证据资料", "content_type": "evidence", "role": "fact", "access": "public"},
    "industry_context": {"label": "行业资料", "content_type": "industry_context", "role": "context", "access": "public"},
    "peer_comparison": {"label": "同行候选", "content_type": "peer_comparison", "role": "context", "access": "public"},
    "supply_chain": {"label": "上下游资料", "content_type": "supply_chain", "role": "context", "access": "public"},
}

_SOURCE_TIER_SCORE = {
    "official": 0.98,
    "institution": 0.86,
    "media": 0.72,
    "community": 0.56,
    "internal": 0.78,
    "unknown": 0.50,
}

_HALF_LIFE_DAYS = {
    "site_fast_news": 1.5,
    "site_articles": 10.0,
    "stock_research": 45.0,
    "recent_research": 45.0,
    "institution_notes": 14.0,
    "celebrity_views": 10.0,
    "stock_news": 3.0,
    "evidence_rag": 21.0,
    "industry_context": 30.0,
    "peer_comparison": 14.0,
    "supply_chain": 21.0,
}

_SOURCE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("official", ("交易所", "证监会", "国务院", "央行", "公司公告", "公告")),
    ("institution", ("证券", "投行", "研究所", "机构", "研报", "纪要")),
    ("internal", ("稻草财经", "deepfocus")),
    ("media", ("财联社", "彭博", "路透", "新华社", "央视", "东财", "财经")),
    ("community", ("雪球", "公众号", "知识星球", "社区")),
)


def _text(value: Any, limit: int = 420) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text[:limit]


def _terms(query: str, stock: Any = None) -> list[str]:
    # 证据检索优先使用规范标的，不能把整句用户问题拆成十几个噪声词。
    # 尤其是“我没说……”“现在贵不贵”这类上下文词，命中泛资讯后会制造实体串题。
    canonical = [
        str(getattr(stock, "symbol", "") or ""),
        str(getattr(stock, "name", "") or ""),
    ] if stock else []
    raw_query = str(query or "")
    values = canonical + [
        item for item in re.split(r"[\s,，、/|:：()（）]+", raw_query)
        if 2 <= len(item.strip()) <= 12
    ]
    values = [item.casefold() for item in values if item.strip()]
    out: list[str] = []
    for value in values:
        if value not in out:
            out.append(value)
    return out[:12]


def _parse_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    raw = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y-%m-%d %H:%M:%S"):
            try:
                parsed = datetime.strptime(raw[:19], fmt)
                break
            except ValueError:
                parsed = None
        if parsed is None:
            return None
    return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc)


def freshness_score(published_at: Any, category: str, *, now: Optional[datetime] = None) -> float:
    """按内容类型做半衰期衰减；没有时间戳时保守给 0.35。"""
    date = _parse_dt(published_at)
    if date is None:
        return 0.35
    current = now or datetime.now(timezone.utc)
    age_days = max(0.0, (current - date).total_seconds() / 86400.0)
    half_life = _HALF_LIFE_DAYS.get(category, 14.0)
    return round(max(0.03, min(1.0, math.pow(0.5, age_days / half_life))), 3)


def source_tier(source_name: str, category: str = "") -> tuple[str, float]:
    text = str(source_name or "").casefold()
    if category in {"stock_research", "recent_research", "institution_notes"}:
        return "institution", _SOURCE_TIER_SCORE["institution"]
    if category in {"site_fast_news", "site_articles"} and text in {"快讯", "文章", "稻草财经"}:
        return "internal", _SOURCE_TIER_SCORE["internal"]
    if category == "celebrity_views":
        return "community", _SOURCE_TIER_SCORE["community"]
    for tier, keywords in _SOURCE_KEYWORDS:
        if any(keyword.casefold() in text for keyword in keywords):
            return tier, _SOURCE_TIER_SCORE[tier]
    return "unknown", _SOURCE_TIER_SCORE["unknown"]


def _stock_context(stock: Any) -> Optional[dict[str, str]]:
    if not stock:
        return None
    symbol = str(getattr(stock, "symbol", "") or "").strip().upper()
    market = str(getattr(stock, "market", "") or "").strip().upper()
    name = str(getattr(stock, "name", "") or symbol).strip()
    if not symbol:
        return None
    suffix = {"CN": ".SH" if symbol.startswith(("6", "68")) else ".SZ", "HK": ".HK", "US": ""}.get(market, "")
    canonical = f"{symbol}{suffix}"
    return {
        "security_id": f"security:{market.lower() or 'unknown'}:{canonical}",
        "label": name,
        "canonical_key": canonical,
        "market": market,
    }


def _id_for(category: str, title: str, source: str, published_at: str) -> str:
    raw = f"{category}\n{title}\n{source}\n{published_at[:10]}"
    return f"research:{category}:{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:20]}"


def _dedupe_key(title: str) -> str:
    # 去掉日期、代码和标点，跨供应商重复发布时可以合并；保留中文词序避免误合并。
    normalized = re.sub(r"20\d{2}[-/.年]?\d{1,2}[-/.月]?\d{0,2}日?", "", title.casefold())
    normalized = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", normalized)
    return normalized[:160]


def _as_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        if isinstance(value.get("items"), list):
            return value["items"]
        # 个别旧工具自身返回 {ok, data}，execute_tool 外层还会再包一层。
        nested = value.get("data")
        if isinstance(nested, list):
            return nested
        if isinstance(nested, dict) and isinstance(nested.get("items"), list):
            return nested["items"]
    return []


def _celebrity_rows(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, dict):
        return []
    rows: list[dict[str, Any]] = []
    for figure in value.get("celebrities") or []:
        if not isinstance(figure, dict):
            continue
        name = _text(figure.get("name"), 80)
        digest = _text(figure.get("digest"), 260)
        for item in figure.get("latest") or []:
            if not isinstance(item, dict):
                continue
            rows.append({
                "title": f"{name}：{_text(item.get('title'), 160)}",
                "summary": _text(item.get("summary") or digest, 420),
                "source_name": f"{name}（名人观点）" if name else "名人观点",
                "date": item.get("date") or "",
                "figure": name,
            })
    return rows


def _row_from_item(category: str, item: Any, stock: Any = None) -> Optional[dict[str, Any]]:
    if not isinstance(item, dict):
        return None
    meta = _CATEGORY_META[category]
    title = _text(item.get("title") or item.get("name") or item.get("headline"), 240)
    summary = _text(item.get("summary") or item.get("snippet") or item.get("ai_summary") or item.get("text"), 420)
    if not title and not summary:
        return None
    source_name = _text(
        item.get("source_name") or item.get("source") or item.get("org") or item.get("provider") or meta["label"],
        100,
    )
    published_at = str(item.get("published_at") or item.get("date") or item.get("created_at") or item.get("reported_date") or "")[:40]
    # 不要把“没有标的字段”的泛资讯强行标成当前股票；实体关系会基于标题/摘要
    # 再判断。否则 Bloomberg/行业/竞品文章会被误当成该股票的直接证据。
    stock_symbol = str(item.get("symbol") or "").strip().upper()
    tier, tier_score = source_tier(source_name, category)
    raw_tags = item.get("tags") or []
    if isinstance(raw_tags, str):
        raw_tags = [raw_tags]
    return {
        "id": _id_for(category, title, source_name, published_at),
        "category": category,
        "category_label": meta["label"],
        "content_type": meta["content_type"],
        "title": title,
        "summary": summary,
        "source_name": source_name,
        "source_tier": tier,
        "source_score": tier_score,
        "symbol": stock_symbol,
        "url": _text(item.get("url"), 1200),
        "published_at": published_at,
        "as_of": str(item.get("as_of") or published_at),
        "collected_at": str(item.get("collected_at") or ""),
        "access_level": meta["access"],
        "evidence_role": meta["role"],
        "legacy_tags": [str(tag)[:60] for tag in raw_tags if str(tag).strip()][:10],
        "metadata": {
            key: item[key] for key in ("tone", "rating", "org", "figure") if item.get(key) not in (None, "")
        },
    }


def _extract_entities(text: str) -> list[dict[str, str]]:
    """从标题/摘要提取可核验的证券实体，宁可漏掉也不把行业词当股票。"""
    global _ENTITY_NAME_CODE_CACHE
    hay = str(text or "")
    found: dict[str, str] = {}
    for name, code in _KNOWN_ENTITY_CODES.items():
        if name.casefold() in hay.casefold():
            found[name] = code
    for code in re.findall(r"(?<![\d.])\d{5,6}(?![\d.])", hay):
        # 排除日期/报告编号，只保留常见 A 股六位代码和港股五位代码。
        valid_cn = len(code) == 6 and code.startswith(("000", "001", "002", "003", "300", "301", "600", "601", "603", "605", "688", "689"))
        valid_hk = len(code) == 5 and code.startswith("0")
        if valid_cn or valid_hk:
            found.setdefault(code, code)
    # A 股名称表是本地缓存，首次加载不联网；限制长度和数量避免全量名称造成噪声。
    if _ENTITY_NAME_CODE_CACHE is None:
        try:
            from .stock_name_index import all_name_code
            _ENTITY_NAME_CODE_CACHE = all_name_code()
        except Exception:  # noqa: BLE001 - 实体抽取是增强字段，不阻断索引
            _ENTITY_NAME_CODE_CACHE = {}
    for name, code in sorted((_ENTITY_NAME_CODE_CACHE or {}).items(), key=lambda item: len(item[0]), reverse=True):
        if len(found) >= 12 or not (3 <= len(str(name)) <= 8):
            break
        if str(name).casefold() in hay.casefold():
            found.setdefault(str(name), str(code))
    return [{"name": name, "symbol": code} for name, code in list(found.items())[:12]]


def _extract_research_claims(text: str) -> dict[str, list[dict[str, Any]]]:
    """提取目标价、评级、PE/PB/PEG 等数字，并保留原文片段供审校。"""
    hay = re.sub(r"\s+", " ", str(text or "")).strip()
    targets: list[dict[str, Any]] = []
    for match in _TARGET_PRICE_RE.finditer(hay):
        currency = str(match.group("currency") or match.group("currency_suffix") or "").upper()
        currency = {"港元": "HKD", "HK$": "HKD", "美元": "USD", "US$": "USD", "$": "USD", "人民币": "CNY", "¥": "CNY", "￥": "CNY"}.get(currency, currency or "UNSPECIFIED")
        targets.append({"value": float(match.group("value")), "currency": currency, "text": hay[max(0, match.start() - 12):match.end() + 12][:80]})
    ratings: list[dict[str, Any]] = []
    for normalized, pattern in _RATING_RULES:
        match = re.search(pattern, hay, re.IGNORECASE)
        if match:
            ratings.append({"rating": normalized, "text": match.group(0)[:30]})
            break
    valuation: list[dict[str, Any]] = []
    for match in _VALUATION_RE.finditer(hay):
        try:
            value = float(match.group("value"))
        except (TypeError, ValueError):
            continue
        prefix = match.group(0)[:30]
        metric = "PE" if re.search(r"PE|P/E|市盈率", prefix, re.IGNORECASE) else "PB" if re.search(r"PB|市净率", prefix, re.IGNORECASE) else "估值"
        valuation.append({"metric": metric, "value": value, "unit": match.group("unit") or "倍", "text": prefix[:80]})
    return {"target_prices": targets[:8], "ratings": ratings, "valuation_assumptions": valuation[:8]}


def _build_market_analysis(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """将全量去重证据聚合成板块、实体和目标价候选，供推荐层计算而非凭记忆选股。"""
    themes: dict[str, dict[str, Any]] = {}
    entities: dict[str, dict[str, Any]] = {}
    for row in rows:
        row_text = f"{row.get('title', '')} {row.get('summary', '')}"
        row["entities"] = _extract_entities(row_text)
        claims = _extract_research_claims(row_text)
        row.update(claims)
        tagged_themes = [tag.get("label") for tag in row.get("tags", []) if tag.get("facet") == "theme" and tag.get("label")]
        lower_text = row_text.casefold()
        explicit_themes = [label for label, keywords in _THEME_FALLBACKS if any(keyword.casefold() in lower_text for keyword in keywords)]
        # 规则标签中的“金融”容易被“投资者/投资日”等泛词触发；只有出现明确金融词时保留。
        title_text = str(row.get("title") or "").casefold()
        if "金融" in explicit_themes and not any(keyword in title_text for keyword in _FINANCE_KEYWORDS):
            explicit_themes = [theme for theme in explicit_themes if theme != "金融"]
        if "金融" in tagged_themes and not any(keyword in title_text for keyword in _FINANCE_KEYWORDS):
            tagged_themes = [theme for theme in tagged_themes if theme != "金融"]
        row["themes"] = list(dict.fromkeys([*explicit_themes, *tagged_themes]))
        title_entities = _extract_entities(str(row.get("title") or ""))
        claim_entities = title_entities or row["entities"]
        for theme in row["themes"] or ["未分类"]:
            bucket = themes.setdefault(theme, {"theme": theme, "count": 0, "score": 0.0, "titles": [], "entities": set()})
            bucket["count"] += 1
            bucket["score"] += float(row.get("score") or 0)
            if len(bucket["titles"]) < 5:
                bucket["titles"].append(row.get("title"))
            bucket["entities"].update(entity["name"] for entity in row["entities"])
        for entity in row["entities"]:
            key = entity["name"]
            bucket = entities.setdefault(key, {"name": key, "symbol": entity["symbol"], "count": 0, "score": 0.0, "themes": set(), "titles": [], "ratings": [], "target_prices": [], "valuation_assumptions": []})
            bucket["count"] += 1
            bucket["score"] += float(row.get("score") or 0)
            bucket["themes"].update(row["themes"])
            if len(bucket["titles"]) < 4:
                bucket["titles"].append(row.get("title"))
            if any(item["name"] == key for item in claim_entities):
                bucket["ratings"].extend(row.get("ratings") or [])
                bucket["target_prices"].extend(row.get("target_prices") or [])
                bucket["valuation_assumptions"].extend(row.get("valuation_assumptions") or [])
    def clean(bucket: dict[str, Any]) -> dict[str, Any]:
        out = dict(bucket)
        for key in ("entities", "themes"):
            if isinstance(out.get(key), set):
                out[key] = sorted(out[key])[:12]
        for key in ("ratings", "target_prices", "valuation_assumptions"):
            if isinstance(out.get(key), list):
                unique = []
                seen = set()
                for item in out[key]:
                    marker = repr(item)
                    if marker not in seen:
                        seen.add(marker)
                        unique.append(item)
                out[key] = unique[:8]
        out["score"] = round(float(out.get("score") or 0), 4)
        return out
    theme_rows = sorted((clean(item) for item in themes.values()), key=lambda item: (item["count"], item["score"]), reverse=True)[:12]
    entity_rows = sorted((clean(item) for item in entities.values()), key=lambda item: (item["count"], item["score"]), reverse=True)[:20]
    return {
        "sector_clusters": theme_rows,
        "entity_candidates": entity_rows,
        "claim_counts": {
            "target_price_mentions": sum(len(row.get("target_prices") or []) for row in rows),
            "rating_mentions": sum(len(row.get("ratings") or []) for row in rows),
            "valuation_assumption_mentions": sum(len(row.get("valuation_assumptions") or []) for row in rows),
        },
        "method": "规则抽取 + 证据频次/新鲜度排序；目标价与评级须回到原文交叉核验，不等同于投资建议。",
    }


_ENTITY_FILTER_CATEGORIES = {
    "site_fast_news",
    "site_articles",
    "stock_research",
    "recent_research",
    "institution_notes",
    "celebrity_views",
    "stock_news",
    "evidence_rag",
}


def _entity_relation(row: dict[str, Any], stock: Any = None) -> tuple[str, str]:
    """返回 direct/related/unrelated 及可解释的命中依据。

    direct 只接受代码精确命中，或标题以规范公司名开头/带明确括号标注；
    正文提及但标题不属于该主体降为 related，完全不相关的内容不进入股票证据池。
    """
    if not stock:
        return "direct", "no_stock_filter"
    symbol = str(getattr(stock, "symbol", "") or "").strip().casefold()
    name = str(getattr(stock, "name", "") or "").strip().casefold()
    title = str(row.get("title") or "").strip().casefold()
    summary = str(row.get("summary") or "").strip().casefold()
    row_symbol = str(row.get("symbol") or "").strip().casefold()
    if symbol and row_symbol and row_symbol == symbol:
        return "direct", "symbol_exact"
    if name and title:
        # 中文没有词边界，使用标题前缀或括号/冒号等主体标记避免“比亚迪电子”
        # 这类相关公司被误判为比亚迪母公司直接证据。
        if title.startswith(name) and (len(title) == len(name) or title[len(name)] in " ：:（(【[·-"):
            return "direct", "title_entity"
        if re.search(rf"(?:^|[（(【\[：:、\s]){re.escape(name)}(?:[）)】\]：:、\s]|$)", title):
            return "direct", "title_entity"
    if (symbol and symbol in f"{title} {summary}") or (name and name in f"{title} {summary}"):
        return "related", "body_mention"
    return "unrelated", "no_entity_match"


def _relevance(row: dict[str, Any], terms: Iterable[str]) -> float:
    hay = f"{row.get('title', '')} {row.get('summary', '')}".casefold()
    title = str(row.get("title", "")).casefold()
    matched = [term for term in terms if term in hay]
    score = min(1.0, len(matched) * 0.16)
    if any(term in title for term in terms):
        score += 0.20
    if row.get("symbol") and any(term == str(row["symbol"]).casefold() for term in terms):
        score += 0.20
    return min(1.0, score)


def _annotate(row: dict[str, Any], *, stock: Any = None, persist: bool = True) -> dict[str, Any]:
    annotation = annotate_content(
        content_id=row["id"],
        content_type=row["content_type"],
        title=row["title"],
        text=row["summary"],
        source_name=row["source_name"],
        symbol=row.get("symbol") or "",
        url=row.get("url") or "",
        published_at=row.get("published_at") or "",
        legacy_tags=row.get("legacy_tags") or [],
        security_context=_stock_context(stock),
        as_of=row.get("as_of") or row.get("published_at") or "",
        collected_at=row.get("collected_at") or "",
        access_level=row.get("access_level") or "public",
        evidence_role=row.get("evidence_role") or "context",
        annotation_version=ANNOTATION_VERSION,
        persist=persist,
    )
    row["tags"] = [
        {"facet": tag.get("facet"), "code": tag.get("code"), "label": tag.get("label"), "confidence": tag.get("confidence")}
        for tag in annotation.get("tags") or []
    ]
    row["annotation_quality"] = annotation.get("annotation_quality", 0.0)
    row["annotation_version"] = annotation.get("annotation_version", ANNOTATION_VERSION)
    return row


def build_research_index(
    modules: dict[str, Any],
    *,
    objective: str = "",
    stock: Any = None,
    persist_annotations: bool = True,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """把 Harness 模块变成去重、排序、限额后的统一研究索引。"""
    terms = _terms(objective, stock)
    all_rows: list[dict[str, Any]] = []
    raw_counts: dict[str, int] = {}
    raw_row_count = 0

    for category in CATEGORY_BUDGETS:
        module_name = {
            "site_fast_news": "get_site_fast_news",
            "site_articles": "get_site_articles",
            "stock_research": "get_stock_research",
            "recent_research": "get_recent_research",
            "institution_notes": "get_institution_notes",
            "celebrity_views": "get_celebrity_views",
            "stock_news": "get_stock_news",
            "evidence_rag": "evidence_rag",
            "industry_context": "get_industry_context",
            "peer_comparison": "get_peer_comparison",
            "supply_chain": "get_supply_chain_context",
        }[category]
        module_values: list[tuple[str, Any]] = []
        if module_name in modules:
            module_values.append((module_name, modules.get(module_name)))
        # 比较题的 Harness 会为每一边保留 ``tool:code``，索引不能因此把站内
        # 资料全部丢掉；同时把代码注入行，供实体过滤和前端溯源使用。
        module_values.extend(
            (key, value) for key, value in modules.items()
            if key.startswith(module_name + ":")
        )
        rows: list[Any] = []
        for module_key, value in module_values:
            suffix = module_key[len(module_name) + 1:] if module_key.startswith(module_name + ":") else ""
            if category == "celebrity_views":
                rows.extend(_celebrity_rows(value))
            else:
                for item in _as_list(value):
                    if suffix and isinstance(item, dict) and not item.get("symbol"):
                        item = {**item, "symbol": suffix}
                    rows.append(item)
        raw_counts[category] = len(rows)
        for item in rows:
            row = _row_from_item(category, item, stock)
            if row:
                raw_row_count += 1
                # 先标注再排序：标签命中可以补足“标题没写公司全称、正文写了主题”的召回。
                row_stock = stock
                if row.get("symbol") and stock and str(row.get("symbol")).upper() != str(getattr(stock, "symbol", "")).upper():
                    row_stock = SimpleNamespace(
                        symbol=str(row.get("symbol")),
                        name=str(row.get("title") or row.get("symbol"))[:80],
                        market=str(getattr(stock, "market", "CN") or "CN"),
                    )
                row = _annotate(row, stock=row_stock, persist=persist_annotations)
                relation, basis = _entity_relation(row, row_stock)
                row["entity_relation"] = relation
                row["entity_match_basis"] = basis
                # 股票专属资料只保留主体直接证据；若没有直接命中，允许正文明确提及
                # 的 related 资料作为降级上下文，但绝不把无关行业/竞品内容当成证据。
                if category in _ENTITY_FILTER_CATEGORIES and relation == "unrelated":
                    continue
                row["relevance_score"] = _relevance(row, terms)
                tag_text = " ".join(
                    f"{tag.get('code', '')} {tag.get('label', '')}"
                    for tag in row.get("tags") or []
                ).casefold()
                if any(term in tag_text for term in terms):
                    row["relevance_score"] = min(1.0, row["relevance_score"] + 0.12)
                row["freshness_score"] = freshness_score(row.get("published_at"), category, now=now)
                row["score"] = round(
                    0.38 * row["relevance_score"]
                    + 0.25 * float(row["source_score"])
                    + 0.22 * float(row["freshness_score"])
                    + 0.15 * (1.0 if row.get("evidence_role") in {"fact", "counter"} else 0.78),
                    4,
                )
                row["dedupe_key"] = _dedupe_key(row["title"])
                all_rows.append(row)

    # 跨源精确去重：保留得分最高的一条，记录它代表的来源数量。
    clusters: dict[str, list[dict[str, Any]]] = {}
    for row in all_rows:
        # 同一事件在“快讯”和“研报”里同时出现是有价值的交叉证据，不能把一个
        # 内容类型去重掉；只在同一类别内合并同标题/同事件的重复发布。
        cluster_key = f"{row['category']}::{row['dedupe_key'] or row['id']}"
        clusters.setdefault(cluster_key, []).append(row)
    deduped: list[dict[str, Any]] = []
    for cluster_id, rows in clusters.items():
        rows.sort(key=lambda item: (item["score"], item["freshness_score"]), reverse=True)
        winner = rows[0]
        winner["cluster_id"] = hashlib.sha1(cluster_id.encode("utf-8")).hexdigest()[:16]
        winner["cluster_size"] = len(rows)
        winner["related_sources"] = list(dict.fromkeys(item["source_name"] for item in rows))[:5]
        deduped.append(winner)

    selected: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {}
    for category, budget in CATEGORY_BUDGETS.items():
        candidates = sorted(
            (row for row in deduped if row["category"] == category),
            key=lambda item: (item["score"], item["published_at"]),
            reverse=True,
        )
        picked = candidates[:budget]
        selected.extend(picked)
        coverage[category] = {
            "label": _CATEGORY_META[category]["label"],
            "raw_count": raw_counts.get(category, 0),
            "cluster_count": len(candidates),
            "selected_count": len(picked),
            "status": "available" if picked else "no_data",
            "top_tags": _top_tags(picked),
        }

    # 先基于全量去重 rows 做实体/主题/投研主张聚合，再按类别预算截断；否则
    # 127 篇研报只看前 4 篇会把板块热度和目标价分布严重低估。
    market_analysis = _build_market_analysis(deduped)
    selected.sort(key=lambda item: (item["score"], item["published_at"]), reverse=True)
    digest = [
        {
            "id": item.get("id"),
            "category": item["category_label"],
            "title": item["title"],
            "summary": item["summary"][:260],
            "source": item["source_name"],
            "url": item.get("url") or "",
            "published_at": item["published_at"],
            "score": item["score"],
            "credibility": round(float(item["source_score"]), 3),
            "freshness": item["freshness_score"],
            "entity_relation": item.get("entity_relation", "direct"),
            "entity_match_basis": item.get("entity_match_basis", ""),
            "tags": [tag["label"] for tag in item.get("tags", []) if tag.get("facet") in {"event", "theme", "signal"}][:6],
            "cluster_size": item.get("cluster_size", 1),
            "access_level": item.get("access_level"),
            "entities": item.get("entities", [])[:6],
            "themes": item.get("themes", [])[:6],
            "target_prices": item.get("target_prices", [])[:4],
            "ratings": item.get("ratings", [])[:3],
            "valuation_assumptions": item.get("valuation_assumptions", [])[:4],
        }
        for item in selected
    ]
    return {
        "items": selected,
        "digest": digest,
        "coverage": coverage,
        "stats": {
            "raw_items": raw_row_count,
            "deduped_items": len(deduped),
            "selected_items": len(selected),
            "annotation_version": ANNOTATION_VERSION,
        },
        "analysis": market_analysis,
    }


def _top_tags(rows: list[dict[str, Any]], limit: int = 5) -> list[str]:
    counts: dict[str, int] = {}
    for row in rows:
        for tag in row.get("tags") or []:
            if tag.get("facet") not in {"event", "theme", "signal"}:
                continue
            label = str(tag.get("label") or "").strip()
            if label:
                counts[label] = counts.get(label, 0) + 1
    return [label for label, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]
