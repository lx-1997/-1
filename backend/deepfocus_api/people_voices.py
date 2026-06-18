"""人物专题：焦点人物近期发言 / 观点 / 来源聚合。

数据源是 Google News RSS（真实、带出处、带时间），按人物维度并发抓取，
清洗标题、按主题打标签、按时效与权威度评分，统一标注数据可信度。

设计与 official_news.py 一致：真实抓取 → 失败优雅降级（返回缓存或空结果 + 结构化
告警，而不是 502），并写负缓存避免反复打上游。
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import os
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Optional
from urllib.parse import quote
from xml.etree import ElementTree as ET

import httpx

from .schemas import (
    DataQuality,
    PeopleSpotlightResponse,
    PersonProfile,
    PersonVoiceItem,
)
from .shared_utils import dedupe, safe_error, utc_now_iso


HTTP_TIMEOUT_SECONDS = float(os.getenv("DEEPFOCUS_PEOPLE_TIMEOUT_SECONDS", "15"))
CACHE_TTL_SECONDS = int(os.getenv("DEEPFOCUS_PEOPLE_CACHE_TTL_SECONDS", "600"))
RECENCY_WINDOW_DAYS = int(os.getenv("DEEPFOCUS_PEOPLE_WINDOW_DAYS", "21"))
MAX_ITEMS_PER_FIGURE = int(os.getenv("DEEPFOCUS_PEOPLE_MAX_ITEMS", "16"))
# 同时在途的 RSS 抓取上限：全部人物一次性并发会挤爆单一出网代理 → 尾部超时 → 「部分降级」。
# 限并发让每个请求拿到更多代理带宽、稳定全量 live；缓存命中的人物不真正打网，开销可忽略。
FETCH_CONCURRENCY = max(1, int(os.getenv("DEEPFOCUS_PEOPLE_CONCURRENCY", "6")))

PUBLIC_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    ),
    "Accept": "application/rss+xml,application/xml,text/xml,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}

CST = timezone(timedelta(hours=8))


def _news_proxy() -> Optional[str]:
    """Google News 在本沙箱只能经出网代理可达（直连超时）。

    后端启动时 configure_data_source_egress() 把 .google.com 加进了 NO_PROXY（为让
    google finance 直连），这会让 news.google.com 也走直连而超时。这里显式取回代理 URL
    并强制经代理请求新闻 RSS（不受 NO_PROXY 影响）。无代理的正常环境返回 None（直连）。
    """
    return (
        os.getenv("HTTPS_PROXY")
        or os.getenv("https_proxy")
        or os.getenv("HTTP_PROXY")
        or os.getenv("http_proxy")
    )


def _news_client() -> httpx.AsyncClient:
    # trust_env=False + 显式 proxy：绕开 NO_PROXY 对 .google.com 的排除，稳定经代理取数。
    return httpx.AsyncClient(
        timeout=HTTP_TIMEOUT_SECONDS,
        follow_redirects=True,
        headers=PUBLIC_HEADERS,
        proxy=_news_proxy(),
        trust_env=False,
    )


# 焦点人物注册表：中文名 + 职务 + 机构 + 关注领域 + 检索词 + 相关性过滤。
# query 用 Google News 搜索语法（空格=AND，OR=或，引号=短语），并加 when:Nd 限定时效。
FIGURES: list[dict[str, Any]] = [
    {
        "id": "trump",
        "image": "/people/trump.jpg",
        "name": "特朗普",
        "en_name": "Donald Trump",
        "role": "美国总统",
        "org": "白宫 / 美国政府",
        "avatar": "🇺🇸",
        "monogram": "DT",
        "accent": "#c0392b",
        "bio": "美国第 47 任总统，关税、制裁与能源政策的核心决策者，言论与签署的行政令常在数小时内改变全球风险偏好。",
        "topics": ["关税", "地缘", "美联储", "能源"],
        "why_it_matters": "关税与制裁节奏直接驱动商品、汇率与出口链定价，是宏观风险偏好的一级变量。",
        "query": '"特朗普" OR "Trump"',
        "must_any": ["特朗普", "trump"],
        "block": [],
    },
    {
        "id": "huang",
        "image": "/people/huang.jpg",
        "name": "黄仁勋",
        "en_name": "Jensen Huang",
        "role": "创始人兼 CEO",
        "org": "英伟达 NVIDIA",
        "avatar": "🟩",
        "monogram": "JH",
        "accent": "#16a34a",
        "bio": "英伟达创始人兼 CEO，全球 AI 算力的供给侧风向标，GTC 演讲与业绩指引主导市场对算力周期的预期。",
        "topics": ["AI 算力", "GPU", "数据中心", "机器人"],
        "why_it_matters": "英伟达的指引是 AI 资本开支与算力周期的领先指标，牵动整条半导体与服务器产业链。",
        "query": '"黄仁勋" OR "Jensen Huang"',
        "must_any": ["黄仁勋", "jensen huang", "英伟达", "nvidia"],
        "block": [],
    },
    {
        "id": "musk",
        "image": "/people/musk.jpg",
        "name": "马斯克",
        "en_name": "Elon Musk",
        "role": "CEO",
        "org": "特斯拉 · SpaceX · xAI",
        "avatar": "🚀",
        "monogram": "EM",
        "accent": "#475569",
        "bio": "横跨电动车、商业航天与 AI 的连续创业者，特斯拉、SpaceX 与 xAI 掌舵人，单条发言常引发个股与主题板块异动。",
        "topics": ["电动车", "自动驾驶", "商业航天", "xAI"],
        "why_it_matters": "其表态横跨车、航天、AI 多条主线，常成为特斯拉与商业航天概念股的短期催化。",
        "query": '"马斯克" OR "Elon Musk"',
        "must_any": ["马斯克", "musk", "特斯拉", "tesla", "spacex", "xai", "星舰", "星链"],
        "block": [],
    },
    {
        "id": "altman",
        "image": "/people/altman.jpg",
        "name": "山姆·奥特曼",
        "en_name": "Sam Altman",
        "role": "联合创始人兼 CEO",
        "org": "OpenAI",
        "avatar": "🤖",
        "monogram": "SA",
        "accent": "#2563eb",
        "bio": "OpenAI 联合创始人兼 CEO，ChatGPT 之父，其产品节奏与融资动作定义生成式 AI 的商业化与算力需求曲线。",
        "topics": ["通用人工智能", "ChatGPT", "算力", "融资"],
        "why_it_matters": "OpenAI 的发布与算力采购节奏，是判断 AI 应用渗透与上游算力需求的核心锚点。",
        # 「奥特曼」在中文里与特摄角色同名，靠 must_any 强制相关（须含 OpenAI / Sam / 山姆）以剔除动漫噪声。
        "query": '"Sam Altman" OR "山姆·奥特曼" OR "山姆奥特曼" OR ("奥特曼" OpenAI)',
        "must_any": ["openai", "chatgpt", "sam altman", "山姆", "奥尔特曼", "altman", "gpt"],
        "block": ["圆谷", "特摄", "怪兽", "迪迦", "赛罗", "泰迦", "奥特曼电影", "奥特曼卡片"],
    },
    {
        "id": "amodei",
        "image": "/people/amodei.jpg",
        "name": "达里奥·阿莫迪",
        "en_name": "Dario Amodei",
        "role": "联合创始人兼 CEO",
        "org": "Anthropic（Claude 母公司）",
        "avatar": "◈",
        "monogram": "DA",
        "accent": "#d97706",
        "bio": "Anthropic 联合创始人兼 CEO，大模型 Claude 的缔造者，AI 安全与对齐路线的主要倡导者，其估值与治理主张影响全行业竞争格局。",
        "topics": ["AI 安全", "Claude", "对齐", "算力"],
        "why_it_matters": "Anthropic 是 Claude 的母公司，其安全主张与估值变化牵动 AI 治理预期与头部竞争格局。",
        "query": '"Dario Amodei" OR "阿莫迪" OR "Anthropic"',
        "must_any": ["anthropic", "amodei", "阿莫迪", "claude"],
        "block": [],
    },
    {
        "id": "su",
        "image": "/people/su.jpg",
        "name": "苏姿丰",
        "en_name": "Lisa Su",
        "role": "董事长兼 CEO",
        "org": "AMD 超威半导体",
        "avatar": "🔴",
        "monogram": "LS",
        "accent": "#d4380d",
        "bio": "AMD 董事长兼 CEO，带领 AMD 在 CPU/GPU 与 AI 加速器上正面挑战英伟达、英特尔，是 AI 算力第二供给方的关键人物。",
        "topics": ["AI 芯片", "GPU", "数据中心", "半导体"],
        "why_it_matters": "AMD 的 AI 加速器路线与产能是英伟达之外最重要的算力替代变量，直接影响算力供给与定价。",
        "query": '"苏姿丰" OR "Lisa Su" OR "AMD CEO"',
        "must_any": ["苏姿丰", "lisa su", "amd ceo", "超威", "超微"],
        "block": [],
    },
    {
        "id": "nadella",
        "image": "/people/nadella.jpg",
        "name": "萨提亚·纳德拉",
        "en_name": "Satya Nadella",
        "role": "董事长兼 CEO",
        "org": "微软 Microsoft",
        "avatar": "🪟",
        "monogram": "SN",
        "accent": "#0a7ea4",
        "bio": "微软董事长兼 CEO，把微软重塑为云 + AI 巨头，深度绑定 OpenAI 并以 Copilot 推动 AI 商业化，是企业级 AI 落地的风向标。",
        "topics": ["云计算", "Copilot", "OpenAI", "数据中心"],
        "why_it_matters": "微软的资本开支与 Copilot 商业化进度，是企业级 AI 渗透与上游算力需求的核心观测点。",
        "query": '"纳德拉" OR "Satya Nadella" OR "微软CEO"',
        "must_any": ["纳德拉", "nadella", "satya", "微软ceo", "微软 ceo"],
        "block": [],
    },
    {
        "id": "wood",
        "image": "/people/wood.jpg",
        "name": "凯茜·伍德",
        "en_name": "Cathie Wood",
        "role": "创始人兼 CEO/CIO",
        "org": "ARK Invest 方舟投资",
        "avatar": "📈",
        "monogram": "CW",
        "accent": "#0d9488",
        "bio": "人称「木头姐」，ARK Invest 创始人，颠覆式创新成长股的旗帜性买方，重仓特斯拉、AI、比特币，每日调仓动向常被市场跟踪。",
        "topics": ["成长股", "颠覆创新", "特斯拉", "比特币"],
        "why_it_matters": "其每日调仓公开透明，是观察成长/主题资金对 AI、加密与颠覆式创新风险偏好的实时窗口。",
        "query": '"木头姐" OR "Cathie Wood" OR "凯茜·伍德"',
        "must_any": ["木头姐", "cathie wood", "凯茜", "ark", "方舟"],
        "block": [],
    },
    {
        "id": "zuck",
        "image": "/people/zuck.jpg",
        "name": "扎克伯格",
        "en_name": "Mark Zuckerberg",
        "role": "联合创始人兼 CEO",
        "org": "Meta（Facebook）",
        "avatar": "🌐",
        "monogram": "MZ",
        "accent": "#0866ff",
        "bio": "Meta 联合创始人兼 CEO，从社交帝国转向 AI 与元宇宙重注，开源 Llama、豪掷算力资本开支，是开源大模型与 AI 基建的关键玩家。",
        "topics": ["AI", "Llama 开源", "元宇宙", "算力"],
        "why_it_matters": "Meta 的算力资本开支与开源大模型策略，深刻影响 AI 竞争格局与上游硬件需求。",
        "query": '"扎克伯格" OR "Mark Zuckerberg" OR "小扎"',
        "must_any": ["扎克伯格", "zuckerberg", "小扎", "meta", "脸书"],
        "block": [],
    },
    {
        "id": "cook",
        "image": "/people/cook.jpg",
        "name": "蒂姆·库克",
        "en_name": "Tim Cook",
        "role": "CEO",
        "org": "苹果 Apple",
        "avatar": "🍎",
        "monogram": "TC",
        "accent": "#6e6e73",
        "bio": "苹果 CEO，掌舵全球市值最高的科技公司之一，硬件生态、供应链与 AI 战略的关键决策者，WWDC 与新品节奏牵动消费电子产业链。",
        "topics": ["消费电子", "供应链", "AI", "服务"],
        "why_it_matters": "苹果的销量、供应链与 AI 落地节奏是消费电子与亚洲制造链的需求风向标。",
        "query": '"库克" OR "Tim Cook" OR "苹果CEO"',
        "must_any": ["库克", "tim cook", "苹果", "apple"],
        "block": [],
    },
    {
        "id": "pichai",
        "image": "/people/pichai.jpg",
        "name": "桑达尔·皮查伊",
        "en_name": "Sundar Pichai",
        "role": "CEO",
        "org": "谷歌 / Alphabet",
        "avatar": "🔎",
        "monogram": "SP",
        "accent": "#1a73e8",
        "bio": "谷歌与 Alphabet CEO，主导 Gemini 大模型、搜索与云的 AI 化转型，是 AI 基础设施与应用竞争的核心玩家。",
        "topics": ["AI", "Gemini", "搜索", "云计算"],
        "why_it_matters": "谷歌的 AI 与云资本开支、Gemini 进展，是判断 AI 应用竞争与算力需求的核心观测点。",
        "query": '"皮查伊" OR "Sundar Pichai" OR "谷歌CEO"',
        "must_any": ["皮查伊", "pichai", "谷歌", "google", "alphabet", "gemini"],
        "block": [],
    },
    {
        "id": "powell",
        "image": "/people/powell.jpg",
        "name": "杰罗姆·鲍威尔",
        "en_name": "Jerome Powell",
        "role": "主席",
        "org": "美联储 Fed",
        "avatar": "🏛️",
        "monogram": "JP",
        "accent": "#1f6f43",
        "bio": "美联储主席，全球利率与流动性的总闸门，其讲话与议息决议直接定价美债、美元与全球风险资产。",
        "topics": ["利率", "通胀", "美元", "流动性"],
        "why_it_matters": "美联储的利率路径是全球资产定价的锚，鲍威尔每句措辞都可能引发股债汇大幅波动。",
        "query": '"鲍威尔" OR "Jerome Powell" OR "美联储主席"',
        "must_any": ["鲍威尔", "powell", "美联储", "fed", "联储"],
        "block": [],
    },
    {
        "id": "buffett",
        "image": "/people/buffett.jpg",
        "name": "沃伦·巴菲特",
        "en_name": "Warren Buffett",
        "role": "董事长",
        "org": "伯克希尔·哈撒韦",
        "avatar": "💰",
        "monogram": "WB",
        "accent": "#9a6a1f",
        "bio": "伯克希尔·哈撒韦董事长，价值投资旗帜人物，其持仓调整、现金水位与年度致股东信被全球投资者奉为风向标。",
        "topics": ["价值投资", "持仓", "现金", "保险"],
        "why_it_matters": "伯克希尔的持仓变动与现金水位，是观察聪明钱风险偏好与市场估值水位的经典指标。",
        "query": '"巴菲特" OR "Warren Buffett" OR "伯克希尔"',
        "must_any": ["巴菲特", "buffett", "伯克希尔", "berkshire"],
        "block": [],
    },
    {
        "id": "son",
        "image": "/people/son.jpg",
        "name": "孙正义",
        "en_name": "Masayoshi Son",
        "role": "董事长兼 CEO",
        "org": "软银 SoftBank",
        "avatar": "💴",
        "monogram": "MS",
        "accent": "#c2410c",
        "bio": "软银集团董事长兼 CEO，愿景基金掌舵人，重注 AI 与 OpenAI/Arm，其押注规模与方向常引爆科技与算力主题。",
        "topics": ["AI 投资", "OpenAI", "Arm", "愿景基金"],
        "why_it_matters": "软银的 AI 豪赌与 Arm/OpenAI 布局，是观察一级市场 AI 风险偏好与算力资本流向的窗口。",
        "query": '"孙正义" OR "Masayoshi Son" OR "软银"',
        "must_any": ["孙正义", "masayoshi", "软银", "softbank", "愿景基金", "arm"],
        "block": [],
    },
    {
        "id": "leijun",
        "image": "/people/leijun.jpg",
        "name": "雷军",
        "en_name": "Lei Jun",
        "role": "创始人兼 CEO",
        "org": "小米 Xiaomi",
        "avatar": "📱",
        "monogram": "LJ",
        "accent": "#ff6900",
        "bio": "小米创始人兼 CEO，手机 × AIoT × 汽车三线作战，SU7/YU7 让小米成为中国智能电动车与消费科技的话题中心。",
        "topics": ["智能手机", "电动车", "AIoT", "国产替代"],
        "why_it_matters": "小米的手机与汽车销量、定价策略，是观察中国消费科技与新能源车竞争格局的高频信号。",
        "query": '"雷军" OR "小米CEO"',
        "must_any": ["雷军", "小米", "xiaomi", "su7", "yu7"],
        "block": [],
    },
]

FIGURES_BY_ID = {fig["id"]: fig for fig in FIGURES}

# 头像为 Wikimedia Commons 公共/自由许可肖像（本地打包于 public/people/），统一署名。
IMAGE_CREDIT = "Wikimedia Commons"

# 主题标签分类：命中关键词即打标，供前端按看点快速筛读。
TOPIC_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("AI", ("人工智能", "ai", "大模型", "模型", "gpt", "chatgpt", "claude", "算力", "agi", "智能体", "agent")),
    ("芯片", ("芯片", "gpu", "半导体", "英伟达", "nvidia", "台积电", "晶圆", "光刻", "gtc")),
    ("关税", ("关税", "加税", "tariff", "出口管制", "制裁", "禁令", "实体清单")),
    ("政策", ("政策", "法案", "行政令", "签署", "监管", "听证", "国会", "白宫", "美联储", "降息", "加息", "总统", "政府")),
    ("地缘", ("伊朗", "俄罗斯", "乌克兰", "俄乌", "普京", "泽连斯基", "以色列", "哈马斯", "加沙", "中东",
              "北约", "冲突", "谈判", "战争", "停火", "核", "浓缩铀", "会晤", "普泽会")),
    ("资本", ("股价", "市值", "财报", "估值", "融资", "ipo", "收购", "投资", "美股", "a股", "营收", "净利")),
    ("产品", ("发布", "推出", "上线", "新品", "量产", "交付", "发布会", "演讲", "亮相")),
    ("公司", ("收购", "裁员", "高管", "离职", "董事会", "重组", "合作", "签约")),
]

# 权威来源加分（命中即视为高可信出处，评分上浮）。
AUTHORITY_SOURCES = (
    "新华", "央视", "人民", "纽约时报", "华尔街", "路透", "reuters", "bloomberg",
    "彭博", "财联社", "第一财经", "界面", "21财经", "证券时报", "经济观察",
    "美国之音", "rfi", "ft中文", "金融时报", "cnbc", "the verge", "techcrunch",
)

# 高影响关键词（市场敏感）评分加分。
HOT_TERMS = (
    "关税", "加税", "制裁", "出口管制", "降息", "加息", "财报", "收购", "ipo",
    "估值", "暴涨", "暴跌", "涨停", "异动", "突发", "重磅", "禁令", "演讲", "发布",
)

ITEM_RE = re.compile(r"<item>(.*?)</item>", re.I | re.S)
_NS_RE = re.compile(r"\sxmlns(:\w+)?=\"[^\"]*\"")

_cache: dict[str, tuple[datetime, PersonProfile]] = {}


async def _gather_bounded(limit: int, factories: list) -> list:
    """限并发地运行一批「零参协程工厂」，保持入参顺序返回结果（同 asyncio.gather）。"""
    sem = asyncio.Semaphore(max(1, limit))

    async def _run(factory):
        async with sem:
            return await factory()

    return await asyncio.gather(*(_run(factory) for factory in factories))


async def fetch_people_spotlight(*, refresh: bool = False) -> PeopleSpotlightResponse:
    """并发抓取全部焦点人物的近期发言，组装人物专题响应（限并发，避免挤爆代理）。"""
    now = datetime.now(timezone.utc)
    async with _news_client() as client:
        profiles = await _gather_bounded(
            FETCH_CONCURRENCY,
            [
                (lambda fig=fig: _resolve_figure(client, fig, now=now, refresh=refresh))
                for fig in FIGURES
            ],
        )

    warnings: list[str] = []
    for profile in profiles:
        warnings.extend(profile.warnings)

    total_items = sum(profile.item_count for profile in profiles)
    live_count = sum(1 for p in profiles if p.data_quality.level == "live")
    overall_quality = _overall_quality(profiles)

    return PeopleSpotlightResponse(
        provider="google-news" if live_count else "fallback",
        generated_at=utc_now_iso(),
        figures=list(profiles),
        total_items=total_items,
        cache_age_seconds=0,
        warnings=dedupe(warnings),
        data_quality=overall_quality,
    )


async def fetch_person_voices(figure_id: str, *, refresh: bool = False) -> PersonProfile:
    """单人物近期发言（供数字综述等下钻使用）。"""
    fig = FIGURES_BY_ID.get(figure_id)
    if not fig:
        allowed = " / ".join(FIGURES_BY_ID)
        raise KeyError(f"未知焦点人物：{figure_id}，可选 {allowed}")
    now = datetime.now(timezone.utc)
    async with _news_client() as client:
        return await _resolve_figure(client, fig, now=now, refresh=refresh)


async def _resolve_figure(
    client: httpx.AsyncClient,
    fig: dict[str, Any],
    *,
    now: datetime,
    refresh: bool,
) -> PersonProfile:
    figure_id = fig["id"]
    cached = _cache.get(figure_id)
    if cached and not refresh:
        fetched_at, profile = cached
        age = int((now - fetched_at).total_seconds())
        if age <= CACHE_TTL_SECONDS:
            return profile.model_copy(deep=True)

    warnings: list[str] = []
    items: list[PersonVoiceItem] = []
    try:
        items = await _fetch_figure_items(client, fig)
        if not items:
            warnings.append(f"{fig['name']}：上游已返回但未解析到近期条目，可能是检索无结果或页面结构变化。")
    except Exception as exc:
        warnings.append(f"{fig['name']}：新闻源暂不可用（{safe_error(exc)}）。")
        if cached:
            fetched_at, profile = cached
            stale = profile.model_copy(deep=True)
            stale.warnings = dedupe([*stale.warnings, *warnings, "已返回上次缓存结果。"])
            stale.data_quality = _degraded_quality("已返回上次缓存结果，新闻源临时不可用。")
            return stale

    profile = _build_profile(fig, items, warnings)
    # 仅缓存成功（有条目）的结果；空/失败不写缓存，使下次轮询自动重试自愈，
    # 避免单次瞬时超时把某人物钉在「降级」状态长达整个 TTL。
    if items:
        _cache[figure_id] = (now, profile)
    return profile.model_copy(deep=True)


async def _fetch_figure_items(client: httpx.AsyncClient, fig: dict[str, Any]) -> list[PersonVoiceItem]:
    query = f"{fig['query']} when:{RECENCY_WINDOW_DAYS}d"
    url = (
        "https://news.google.com/rss/search?"
        f"q={quote(query)}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    )
    response = await client.get(url)
    response.raise_for_status()
    return _parse_rss_items(response.text, fig)


def _parse_rss_items(xml_text: str, fig: dict[str, Any]) -> list[PersonVoiceItem]:
    items: list[PersonVoiceItem] = []
    seen_titles: set[str] = set()
    must_any = [t.lower() for t in fig.get("must_any", [])]
    block = [t.lower() for t in fig.get("block", [])]

    for raw in ITEM_RE.findall(xml_text):
        fragment = _NS_RE.sub("", raw)
        try:
            node = ET.fromstring(f"<item>{fragment}</item>")
        except ET.ParseError:
            continue

        title_raw = (node.findtext("title") or "").strip()
        link = (node.findtext("link") or "").strip()
        if not title_raw or not link:
            continue

        source_el = node.find("source")
        source_name = (source_el.text or "").strip() if source_el is not None else ""
        title = _clean_title(title_raw, source_name)
        if len(title) < 6:
            continue

        haystack = f"{title} {source_name}".lower()
        if must_any and not any(term in haystack for term in must_any):
            continue
        if block and any(term in haystack for term in block):
            continue

        norm = _normalize(title)
        if norm in seen_titles:
            continue
        seen_titles.add(norm)

        published_at, reported_date, recency_days = _parse_pubdate(node.findtext("pubDate"))
        # Google News 的 <description> 只是「标题 + 来源」的重复链接，对用户无增量信息，
        # 故不作为 summary 展示；标题本身即为该条「发言/观点」。
        tags = _classify_topics(title, "")
        score = _score_item(title, "", source_name, recency_days)

        items.append(
            PersonVoiceItem(
                id=_stable_id(fig["id"], link, title),
                title=title[:200],
                summary="",
                url=link,
                source_name=source_name or "Google 新闻",
                published_at=published_at,
                reported_date=reported_date,
                tags=tags,
                importance_score=score,
            )
        )

    items.sort(key=lambda it: (it.reported_date or "", it.importance_score), reverse=True)
    return items[:MAX_ITEMS_PER_FIGURE]


def _build_profile(fig: dict[str, Any], items: list[PersonVoiceItem], warnings: list[str]) -> PersonProfile:
    latest = items[0].reported_date if items else None
    if items:
        quality = DataQuality(
            level="live",
            label="实时聚合",
            detail="近期发言来自 Google 新闻聚合的公开报道，均带原文出处与时间，可点击溯源。",
            reasons=[],
        )
    else:
        quality = _degraded_quality("暂未取到该人物的近期公开报道，请稍后刷新。")
    return PersonProfile(
        id=fig["id"],
        name=fig["name"],
        en_name=fig["en_name"],
        role=fig["role"],
        org=fig["org"],
        image=fig.get("image", ""),
        image_credit=IMAGE_CREDIT,
        avatar=fig["avatar"],
        monogram=fig["monogram"],
        accent=fig["accent"],
        bio=fig["bio"],
        topics=list(fig["topics"]),
        why_it_matters=fig["why_it_matters"],
        items=items,
        item_count=len(items),
        latest_date=latest,
        warnings=dedupe(warnings),
        data_quality=quality,
    )


def _clean_title(title: str, source_name: str) -> str:
    text = html.unescape(title).strip()
    # Google 新闻标题常以 " - 来源名" 结尾，去掉这段冗余出处。
    if source_name and text.endswith(f" - {source_name}"):
        text = text[: -len(f" - {source_name}")].strip()
    else:
        parts = text.rsplit(" - ", 1)
        if len(parts) == 2 and len(parts[1]) <= 24 and "。" not in parts[1] and "，" not in parts[1]:
            text = parts[0].strip()
    return re.sub(r"\s+", " ", text).strip()


def _parse_pubdate(value: Optional[str]) -> tuple[Optional[str], Optional[str], Optional[int]]:
    if not value:
        return None, None, None
    try:
        dt = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None, None, None
    if dt is None:
        return None, None, None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    local = dt.astimezone(CST)
    recency_days = max(0, int((datetime.now(timezone.utc) - dt).total_seconds() // 86400))
    return dt.astimezone(timezone.utc).isoformat(), local.strftime("%Y-%m-%d"), recency_days


def _classify_topics(title: str, summary: str) -> list[str]:
    text = f"{title} {summary}".lower()
    tags: list[str] = []
    for label, terms in TOPIC_RULES:
        if any(term in text for term in terms):
            tags.append(label)
    return tags[:4]


def _score_item(title: str, summary: str, source_name: str, recency_days: Optional[int]) -> int:
    score = 55
    if recency_days is not None:
        if recency_days <= 1:
            score += 20
        elif recency_days <= 3:
            score += 14
        elif recency_days <= 7:
            score += 8
        elif recency_days <= 14:
            score += 3
    src = (source_name or "").lower()
    if any(term in src for term in (s.lower() for s in AUTHORITY_SOURCES)):
        score += 10
    text = f"{title} {summary}"
    if any(term in text for term in HOT_TERMS):
        score += 8
    return max(0, min(100, score))


def _degraded_quality(detail: str) -> DataQuality:
    return DataQuality(level="degraded", label="降级兜底", detail=detail, reasons=[])


def _overall_quality(profiles: list[PersonProfile]) -> DataQuality:
    live = sum(1 for p in profiles if p.data_quality.level == "live")
    total = len(profiles)
    if live == total and total > 0:
        return DataQuality(
            level="live",
            label="实时聚合",
            detail="全部焦点人物的近期发言均来自 Google 新闻公开报道，带原文出处与时间。",
            reasons=[],
        )
    if live == 0:
        return _degraded_quality("新闻源暂不可用，已返回缓存或空结果，请稍后刷新。")
    return DataQuality(
        level="degraded",
        label="部分降级",
        detail=f"{live}/{total} 位人物为实时聚合，其余暂用缓存或为空，请稍后刷新。",
        reasons=[],
    )


def _normalize(value: str) -> str:
    return re.sub(r"[\s\W_]+", "", value.lower())


def _stable_id(figure_id: str, url: str, title: str) -> str:
    digest = hashlib.sha1(f"{figure_id}:{url}:{title}".encode("utf-8")).hexdigest()[:16]
    return f"voice-{figure_id}-{digest}"


def digest_cache_key(profile: "PersonProfile") -> str:
    """AI 观点综述的缓存键：人物 id + 其 top8 标题的哈希。

    标题变了（有新发言）→ 键变 → 自动重算；标题不变 → 命中缓存、不再打模型。
    """
    headlines = "|".join(item.title for item in profile.items[:8])
    digest = hashlib.sha1(headlines.encode("utf-8")).hexdigest()[:16]
    return f"{profile.id}:{digest}"
