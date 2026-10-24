"""事件簇引擎：把散落在四类内容里的同一件事聚成「故事」，并给出可解释的重要度与决策提示。

为什么需要这一层
----------------
站内每天新增 1,000–1,700 条内容（快讯/文章/研报/机构纪要），``content_ontology`` 已把它们
统一成带 typed facets 的内容对象，但检索单位仍是「单条内容」：同一家公司的同一件事会以
十几条不同来源、不同体裁的形态重复出现，用户看到的是信息量而不是结论。本模块把内容对象
聚成 **story（事件簇）**，一次给出：这件事是什么、有几家来源佐证、事实与观点如何分布、
对投资意味着什么方向、还需要核对什么。

设计约束（与站内既有链路一致）
------------------------------
1. **零 LLM、确定性**：聚类、打分、卡片文案全部可回归、可解释、零边际成本，
   不给已经吃紧的模型配额和 ZSXQ 下载配额增加任何请求路径负担。
2. **只读内容库**：对 ``.content_ontology.sqlite3``（生产 385MB）只开只读连接，
   不建索引、不改 schema、不回写；聚类结果落在自己的 ``.story_engine.sqlite3``。
3. **不编造**：卡片里的每句话都由成员内容的既有字段拼装（标题/来源/计数/本体标签），
   没有数据就写「暂无」，绝不生成不存在的事实、目标价或结论。
4. **合规**：本模块产出的是**事实聚合**而非 AI 叙述，因此不打 AI 生成标识；
   面向用户的分发出口（微信/晨报）仍由各自的 guard 负责中性化与免责。
5. **隐私**：故事本身是全站公共资产，不含任何账号数据；个人化排序在
   ``user_profile.rank_stories`` 里按账号计算，绝不写回本模块的公共表。

聚类算法（两级，避免全量两两比较）
----------------------------------
先按 **桶** 切分，桶键 = 主体 + 事件类型：有标的别名的进 ``sec:<symbol>:<event>``，
其余按本体主题标签进 ``theme:<code>:<event>``，两者都没有的进 ``misc``。
「茅台·价格变化」和「茅台·业绩变化」是两件事必须分开，同一件事的不同措辞则必须合并。
公司桶规模不大时整桶即一个故事；主题桶与 misc 桶再在桶内用 **稀有签名倒排 + 并查集**
细分：标题的中文二元组/英文词构成签名，先剔除文档频率过高的通用签名（「公司」「美股」这类），
只对倒排长度受控的稀有签名生成候选对，候选对需共享 ≥2 个稀有签名且 Jaccard ≥ 阈值才合并。
复杂度近线性，生产 72 小时窗口（约 3,800 条）在秒级完成。

故事 ID 稳定性：同一件事在多次重建之间必须保持同一个 ``story_id``（否则埋点、订阅、
个人化权重全部失效）。因此用 ``story_registry`` 记住「桶 + 签名集合 + 最近活跃时间」，
新簇先与同桶内仍活跃（``MERGE_GAP_DAYS`` 内）的历史故事比签名 Jaccard，命中即复用 ID；
未命中才铸造新 ID——既稳定，又能把间隔很久的同名事件切成新的一集。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional, Sequence

from . import db
from . import securities_master
from .content_ontology import CONTENT_TYPE_LABELS, EVENT_RULES, THEME_RULES
from .shared_utils import utc_now_iso

BJ_TZ = timezone(timedelta(hours=8))

# ---- 可调参数（集中在此，回归测试按单调性断言而非硬编码数值） ----

WINDOW_HOURS_DEFAULT = 72        # 故事覆盖的内容窗口
WINDOW_HOURS_MAX = 24 * 14       # 前端可请求的最大窗口
MAX_WINDOW_ITEMS = 9000          # 单次重建读取的内容上限（生产 72h ≈ 3.8k，留足余量）
MAX_ITEMS_PER_BUCKET = 700       # 单桶参与聚类的上限（按时间新→旧截断）
MAX_MISC_ITEMS = 1200            # misc 桶（无标的无主题）上限
COMPANY_SUBCLUSTER_MIN = 12      # 公司桶不超过该规模且事件已识别时整桶即一个故事
MAX_PERSISTED_STORIES = 1200     # 单次重建落库的故事上限
HALF_LIFE_HOURS = 30.0           # 时效半衰期：越新的重要度越高
MASS_NORM = 4.0                  # 大众重要度归一化分母
MERGE_GAP_DAYS = 7               # 超过该间隔的同桶同签名簇视为新的一集
RETENTION_DAYS = 30              # 故事与成员内容行的保留期
JACCARD_MERGE = 0.30             # 桶内聚类的标题签名相似度门槛
JACCARD_COMPANY = 0.34           # 公司桶门槛更严：研报标题术语高度同质，易假合并
JACCARD_REGISTRY = 0.25          # 与历史故事比对的复用门槛
MIN_SHARED_SIGNATURES = 2        # 候选对至少要共享的签名数
MIN_SHARED_RARE = 1              # 其中至少要有若干个「桶内低频」签名，阻断术语链式合并
RARE_DF_MAX = 3                  # 桶内文档频率 ≤ 该值的签名才算低频（有区分度）
MAX_POSTING_LEN = 60             # 倒排长度超过该值的签名视为通用词，不生成候选对
GENERIC_SIGNATURE_RATIO = 0.12   # 文档频率超过该比例的签名剔除
SINGLE_SOURCE_STREAM_MIN = 6     # 单来源且成员数达到该值即视为「流」而非事件
SINGLE_SOURCE_STREAM_PENALTY = 0.6
PUBLISHER_SUBJECT_PENALTY = 0.5  # 主体是券商/投行自己时降权：那是内容生产方，不是市场事件
MIN_EXCERPT_CHARS = 24           # 短于该长度的 summary 基本是占位串，不作摘录
REBUILD_INTERVAL_MINUTES = 20    # 后台重建间隔（全表扫描 385MB 库，不宜过密）

# 来源层级重要度（与 research_index 的层级口径一致，取值独立以便故事层单独调参）
TIER_SCORE: dict[str, float] = {
    "official": 1.0,
    "institution": 0.88,
    "internal": 0.8,
    "media": 0.72,
    "community": 0.5,
    "other": 0.45,
}
# 体裁权重：研报/纪要是有结论的深度内容，快讯是原子事实
TYPE_WEIGHT: dict[str, float] = {
    "research": 1.0,
    "institution_note": 0.95,
    "article": 0.8,
    "celebrity_view": 0.7,
    "flash": 0.55,
    "evidence": 0.5,
}
_EVENT_LABELS: dict[str, str] = {code: label for code, label, _ in EVENT_RULES}
_THEME_LABELS: dict[str, str] = {code: label for code, label, _ in THEME_RULES}
_SIGNAL_LABELS = {
    "supportive": "支持论点",
    "risk": "削弱论点",
    "mixed": "多空并存",
    "unconfirmed": "待确认",
}
_HORIZON_LABELS = {
    "intraday": "盘中",
    "short": "短期",
    "medium": "中期",
    "long": "长期",
    "unassigned": "周期待定",
}
_MARKET_LABELS = {"CN": "A股", "HK": "港股", "US": "美股", "MULTI": "多市场", "": "未标注市场"}

# ---- 决策提示规则表（确定性、可审计；无对应事件时给通用核对项） ----
# 口径铁律：只描述「这类事件通常影响什么、需要核对什么」，永不给出买卖指令或目标价。
IMPACT_RULES: dict[str, str] = {
    "earnings": "影响盈利预期与估值锚，关键在业绩与一致预期的差值、以及差值是否已被股价反映。",
    "price_change": "影响产业链利润分配，关键在提价能否传导到销量、以及成本端是否同步变化。",
    "policy": "影响行业准入门槛与需求节奏，关键在适用主体范围、生效时间与执行力度。",
    "order": "影响收入可见度，关键在订单金额占年营收比重、交付周期与确认收入的节奏。",
    "capital_action": "影响股本结构与股东信号，关键在规模、价格区间与资金来源。",
    "supply_chain": "影响成本与交付确定性，关键在库存水位、产能利用率与替代供应是否可用。",
    "product": "影响竞争力与中长期份额，关键在量产时间、客户验证进度与相对竞品的差异。",
    "management": "影响战略连续性与治理评价，关键在接任者背景与过渡期安排。",
    "market_move": "多为资金与情绪的短期表达，关键在是否伴随基本面信息与成交结构变化。",
    "geopolitics": "影响供应链、能源与风险偏好，关键在涉及品种、持续时间与替代路径。",
    "*": "尚不足以判断影响方向，先确认事件主体、时间与量级三要素是否齐全。",
}
VERIFY_RULES: dict[str, list[str]] = {
    "earnings": [
        "核对财报原文的营收、净利、经营性现金流与一致预期的差值",
        "确认口径是单季还是累计，是否与可比公司同期对齐",
        "查看事件后机构是否调整盈利预测与评级",
    ],
    "price_change": [
        "核对提价/降价的品类、幅度与生效时间",
        "确认是否有第二家以上独立来源报道同一价格变化",
        "查看下游客户与竞争对手是否跟进",
    ],
    "policy": [
        "找到政策原文并确认发布主体与适用范围",
        "核对生效时间、过渡期与处罚条款",
        "确认公司是否已公告受影响业务占比",
    ],
    "order": [
        "核对中标/合同公告原文的金额、期限与对手方",
        "计算订单金额占最近一个完整年度营收的比重",
        "确认是否存在关联交易或框架协议重复计算",
    ],
    "capital_action": [
        "核对公告的规模、价格上限与实施期限",
        "确认大股东与高管是否同步增持或减持",
        "查看资金来源与对现金流的影响",
    ],
    "supply_chain": [
        "核对库存、产能与交付周期的量化数据",
        "确认上游原料价格是否同向变化",
        "查看是否有替代供应或产能爬坡时间表",
    ],
    "product": [
        "核对发布时间、量产时间与客户验证进度",
        "确认技术指标是否有第三方可验证来源",
        "对比竞品同代产品的公开参数",
    ],
    "management": [
        "核对公告的任免原文与生效日期",
        "确认接任者的履历与相关业务的连续性",
        "查看是否伴随股权或激励安排变化",
    ],
    "market_move": [
        "核对当日成交额、换手率与龙虎榜数据",
        "确认是否有同期基本面公告或行业消息",
        "查看指数与同板块个股是否同步异动",
    ],
    "geopolitics": [
        "确认事件涉及的品种、地区与时间线",
        "核对官方渠道的正式表态与制裁清单原文",
        "查看相关商品价格与运费的实际变化",
    ],
    "*": [
        "确认事件主体、发生时间与量级三要素",
        "查找是否有官方公告或第二家独立来源佐证",
        "标注仍缺失的关键数据，避免用推测补位",
    ],
}

# 2 字符简称里混着大量行业通用词，会把「新能源汽车」误认成某只股票；
# ≥3 字符的简称几乎都是真实公司名，不设限制。
_GENERIC_TWO_CHAR = frozenset({
    "中国", "人民", "银行", "证券", "保险", "能源", "科技", "电子", "国际", "股份",
    "集团", "发展", "投资", "环保", "文化", "传媒", "通信", "软件", "智能", "汽车",
    "地产", "矿业", "钢铁", "化工", "电力", "水务", "燃气", "食品", "药业", "医疗",
    "生物", "数字", "网络", "信息", "时代", "东方", "南方", "北方", "西部", "长江",
    "黄河", "重工", "置业", "控股", "实业", "医药", "新材料", "新能", "光大", "华夏",
})

_CJK_RE = re.compile(r"[\u4e00-\u9fff]+")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9.\-]{2,}")
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")
# 只吃标题前的表情/装饰符号与空白，不吃【】《》这类中文标点——否则会把「【重磅】」削成「重磅】」
_LEAD_NOISE_RE = re.compile(
    "^[\\s\u200b\u200c\u200d\ufe0f\u2190-\u21ff\u2300-\u27bf\u2b00-\u2bff\U0001f000-\U0001faff]+"
)
_WS_RE = re.compile(r"\s+")
# 站内机构纪要/研报标题里成串的表情方括号（[红包][玫瑰][發][US]…）是发布侧噪音，既污染展示也干扰签名。
_EMOJI_TAG_RE = re.compile(r"\[[^\[\]]{1,8}\]")
_HASH_LEAD_RE = re.compile(r"^#[#\s]*")
# 采集侧给研报/纪要填的占位摘要（「海外投行 · 2026-10-23 · 海外投行报告」「机构纪要」）
# 不是内容，直接展示等于让用户读废话，必须识别出来并跳过。
_PLACEHOLDER_EXCERPT_RE = re.compile(
    r"^(?:[\u4e00-\u9fffA-Za-z]{2,12}\s*·\s*)?\d{4}-\d{2}-\d{2}\s*·\s*[\u4e00-\u9fffA-Za-z]{2,12}$"
    r"|^(?:海外投行报告|海外投行研报|机构纪要|研报摘要|图片动态|视频动态|暂无摘要|无摘要|正文见原文)$"
)
# 机器生成的行情播报与占位内容不是「事件」：混进聚类会挤掉真实新闻，并让单来源刷出高重要度。
_BOILERPLATE_TITLE_RE = re.compile(
    r"^(?:(?:机会|风险|异动|提示)·[^｜|]{1,16}[｜|]"
    r"|(?:图片动态|视频动态|图文动态)$"
    r"|机构纪要有新更新"
    r"|(?:腾讯会议|共享屏幕|页面共享|会议号|入会口令))"
)
# 标题开头的作者/机构括号（【广发电新】、【国泰海通煤炭|黄涛团队】…）：里面的简称是**发布方**不是事件主体。
_BRACKET_GROUP_RE = re.compile(r"[【\[（(「]([^】\])）」\n]{1,30})[】\])）」]")
_AUTHOR_MARKER_RE = re.compile(
    r"(团队|分析师|研究|策略|宏观|固收|证券|组[）\]]?$|[|｜/]"
    r"|机械|电新|计算机|煤炭|通信|传媒|纺服|化工|医药|消费|电子|军工|有色|钢铁|农业"
    r"|地产|汽车|家电|食饮|环保|建筑|交运|轻工|银行|非银|新材料|新能源|半导体|互联网)"
)
# 券商/投行简称：它们是站内内容的**生产方**。每份研报都提自家名字，若按普通主体处理，
# 就会给每家券商挂一张「业绩变化」故事卡（生产样本已复现该假阳性）。
# 因此只做**降级**不做删除——正文里另有主体时不采信，通篇只讲券商自己时仍然采信。
PUBLISHER_ALIASES = frozenset({
    "国泰海通", "国联民生", "中信证券", "中信建投", "华泰证券", "广发证券", "招商证券", "国信证券",
    "银河证券", "中金公司", "中金财富", "申万宏源", "海通证券", "浙商证券", "天风证券", "华创证券",
    "国盛证券", "国金证券", "中泰证券", "东北证券", "信达证券", "长城证券", "长江证券", "华鑫证券",
    "东吴证券", "民生证券", "光大证券", "平安证券", "兴业证券", "国投证券", "华福证券", "开源证券",
    "财通证券", "西部证券", "东兴证券", "山西证券", "首创证券", "万联证券", "湘财证券", "德邦证券",
    "华宝证券", "国元证券", "中原证券", "恒泰证券", "太平洋证券", "华林证券", "红塔证券", "华源证券",
    "华金证券", "国泰君安", "华泰联合", "交银国际", "招银国际", "建银国际", "中银国际", "海通国际",
    "华兴证券", "中信里昂", "高华证券", "摩根大通", "摩根士丹利", "大摩", "高盛", "瑞银", "瑞信",
    "野村", "巴克莱", "花旗", "美银", "汇丰", "渣打", "麦格理", "杰富瑞", "富瑞", "里昂", "天风",
    "浙商", "国盛", "国金", "中泰", "华创", "信达", "广发", "招商", "中信",
})


def is_boilerplate(title: Any) -> bool:
    """机器播报/占位内容判定（不参与故事聚类）。"""
    return bool(_BOILERPLATE_TITLE_RE.search(str(title or "").strip()))


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------

def _db_path() -> Any:
    return db.data_path(".story_engine.sqlite3", "DEEPFOCUS_STORY_DB_PATH")


def _connect() -> sqlite3.Connection:
    conn = db.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_story_db() -> None:
    """建故事表（幂等）。只影响本模块自己的库文件。"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS stories (
                story_id TEXT PRIMARY KEY,
                bucket TEXT NOT NULL,
                kind TEXT NOT NULL,
                title TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                symbol TEXT NOT NULL DEFAULT '',
                entity_name TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT '',
                event_code TEXT NOT NULL DEFAULT '',
                event_label TEXT NOT NULL DEFAULT '',
                theme_codes TEXT NOT NULL DEFAULT '[]',
                theme_labels TEXT NOT NULL DEFAULT '[]',
                signal_code TEXT NOT NULL DEFAULT 'unconfirmed',
                horizon_code TEXT NOT NULL DEFAULT 'unassigned',
                impact TEXT NOT NULL DEFAULT '',
                verify_json TEXT NOT NULL DEFAULT '[]',
                mass REAL NOT NULL DEFAULT 0,
                top_tier TEXT NOT NULL DEFAULT 'other',
                recency REAL NOT NULL DEFAULT 0,
                item_count INTEGER NOT NULL DEFAULT 0,
                source_count INTEGER NOT NULL DEFAULT 0,
                by_type_json TEXT NOT NULL DEFAULT '{}',
                by_role_json TEXT NOT NULL DEFAULT '{}',
                by_signal_json TEXT NOT NULL DEFAULT '{}',
                symbols_json TEXT NOT NULL DEFAULT '[]',
                timeline_json TEXT NOT NULL DEFAULT '[]',
                first_seen TEXT NOT NULL DEFAULT '',
                last_seen TEXT NOT NULL DEFAULT '',
                generated_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS story_items (
                story_id TEXT NOT NULL,
                content_id TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'context',
                rank REAL NOT NULL DEFAULT 0,
                item_score REAL NOT NULL DEFAULT 0,
                content_type TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                source_name TEXT NOT NULL DEFAULT '',
                url TEXT NOT NULL DEFAULT '',
                access_level TEXT NOT NULL DEFAULT 'public',
                published_at TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (story_id, content_id)
            );
            CREATE TABLE IF NOT EXISTS story_registry (
                story_id TEXT PRIMARY KEY,
                bucket TEXT NOT NULL,
                signatures TEXT NOT NULL DEFAULT '[]',
                first_active TEXT NOT NULL DEFAULT '',
                last_active TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_stories_mass ON stories(mass DESC);
            CREATE INDEX IF NOT EXISTS idx_stories_last_seen ON stories(last_seen DESC);
            CREATE INDEX IF NOT EXISTS idx_stories_symbol ON stories(symbol, last_seen DESC);
            CREATE INDEX IF NOT EXISTS idx_story_items_rank ON story_items(story_id, rank DESC);
            CREATE INDEX IF NOT EXISTS idx_story_items_content ON story_items(content_id);
            CREATE INDEX IF NOT EXISTS idx_registry_bucket ON story_registry(bucket, last_active DESC);
            """
        )
        conn.commit()


def _ontology_conn() -> Optional[sqlite3.Connection]:
    """只读连接内容库：不改 journal_mode、不加索引、不回写，避免影响生产采集链路。

    库文件不存在（全新部署、测试环境）时返回 None，调用方按「无内容」降级。
    """
    path = db.data_path(".content_ontology.sqlite3", "DEEPFOCUS_CONTENT_ONTOLOGY_DB_PATH")
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=20.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


# ---------------------------------------------------------------------------
# 文本工具：标题清洗 / 签名 / 标的别名抽取
# ---------------------------------------------------------------------------

def clean_title(value: Any, limit: int = 110) -> str:
    """折叠空白、去掉标题前的表情装饰符与成串的表情方括号，截断到可读长度。

    日期后缀（``杰克科技20261023``）刻意保留：站内日报类内容用日期区分不同期，
    去掉会把多期折叠成同一条，既丢失证据条数也让去重误判。
    """
    text = _WS_RE.sub(" ", str(value or "")).strip()
    text = _EMOJI_TAG_RE.sub(" ", text)
    text = _LEAD_NOISE_RE.sub("", text).strip()
    text = _HASH_LEAD_RE.sub("", text).strip()
    text = _WS_RE.sub(" ", text).strip(" ·|—-")
    return text[:limit].strip() or "未命名事件"


def dedupe_items(items: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按「同来源 + 同标题签名」去重，保留重要度最高的一条。

    生产实测：72h 窗口 3792 条内容里有 1196 条是同来源同标题的重复入库（占 32%），
    不去重会让「34 条内容 / 3 个来源」这类统计与重要度全部虚高——用户看到的热度是假的。
    跨来源的同标题**不**去重：那是真实的相互印证，正是故事价值的来源。
    """
    best: dict[tuple[str, str], dict[str, Any]] = {}
    order: list[tuple[str, str]] = []
    for item in items:
        key = (str(item.get("source_name") or ""), "".join(sorted(item.get("sig") or ())))
        current = best.get(key)
        if current is None:
            best[key] = item
            order.append(key)
        elif float(item.get("score") or 0) > float(current.get("score") or 0):
            best[key] = item
    return [best[key] for key in order]


def title_signature(title: str) -> set[str]:
    """标题签名：中文二元组 + 英文词 + 数字串。用于桶内相似度聚类与同源去重，不引入任何模型。

    数字必须进签名：``杰克科技20261022`` 与 ``杰克科技20261023`` 是两期报告，
    ``目标价865`` 与 ``目标价850`` 是两个事实——忽略数字会让它们看起来完全一样。
    """
    sigs: set[str] = set()
    for run in _CJK_RE.findall(title or ""):
        if len(run) == 1:
            sigs.add(run)
            continue
        for i in range(len(run) - 1):
            sigs.add(run[i:i + 2])
    for word in _WORD_RE.findall(title or ""):
        sigs.add(word.lower())
    for number in _NUM_RE.findall(title or ""):
        sigs.add(f"#{number}")
    return sigs


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        root = x
        while self.parent[root] != root:
            root = self.parent[root]
        while self.parent[x] != root:
            self.parent[x], x = root, self.parent[x]
        return root

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[max(ra, rb)] = min(ra, rb)


_ALIAS_INDEX: Optional[dict[str, list[str]]] = None
_SECURITY_META: Optional[dict[str, tuple[str, str]]] = None


def _alias_index() -> dict[str, list[str]]:
    """简称前两字 → 该前缀下的全部简称（长的在前）。进程级缓存，主数据更新后由 reset 清掉。"""
    global _ALIAS_INDEX
    if _ALIAS_INDEX is None:
        index: dict[str, list[str]] = defaultdict(list)
        for alias in securities_master.alias_map():
            if len(alias) >= 2:
                index[alias[:2]].append(alias)
        for prefix in index:
            index[prefix].sort(key=len, reverse=True)
        _ALIAS_INDEX = dict(index)
    return _ALIAS_INDEX


def security_meta() -> dict[str, tuple[str, str]]:
    """symbol → (简称, 市场)。只读查询证券主数据，进程级缓存。"""
    global _SECURITY_META
    if _SECURITY_META is None:
        meta: dict[str, tuple[str, str]] = {}
        try:
            conn = sqlite3.connect(f"file:{securities_master.DB_PATH}?mode=ro", uri=True, timeout=10.0)
            try:
                for symbol, name, market in conn.execute("SELECT symbol, name, market FROM securities"):
                    meta[str(symbol)] = (str(name or ""), str(market or "").upper())
            finally:
                conn.close()
        except Exception:  # noqa: BLE001 — 主数据缺失不阻断故事构建
            meta = {}
        for symbol, name in securities_master.BUILTIN_HK_US.items():
            meta.setdefault(symbol, (name, "HK" if symbol.isdigit() else "US"))
        _SECURITY_META = meta
    return _SECURITY_META


def reset_caches() -> None:
    """清掉进程级缓存（主数据刷新、测试隔离用）。"""
    global _ALIAS_INDEX, _SECURITY_META
    _ALIAS_INDEX = None
    _SECURITY_META = None


def _boundary_ok(text: str, start: int, alias: str) -> bool:
    """英文简称必须整词命中：避免 META 命中 METALL、IN 命中 INTC 之类的假阳性。"""
    if alias[:1].isascii() and alias[:1].isalpha():
        prev = text[start - 1] if start > 0 else ""
        if prev.isascii() and prev.isalnum():
            return False
    if alias[-1:].isascii() and alias[-1:].isalpha():
        nxt = text[start + len(alias): start + len(alias) + 1]
        if nxt.isascii() and nxt.isalnum():
            return False
    return True


def _author_spans(text: str) -> list[tuple[int, int]]:
    """定位「作者/团队署名」括号区间：含署名特征词的括号，或位于文本末尾的括号。"""
    spans: list[tuple[int, int]] = []
    end = len(text.rstrip())
    for match in _BRACKET_GROUP_RE.finditer(text or ""):
        inner = match.group(1)
        trailing = match.end() >= end
        if trailing or _AUTHOR_MARKER_RE.search(inner):
            spans.append((match.start(), match.end()))
    return spans


def _in_spans(pos: int, spans: Sequence[tuple[int, int]]) -> bool:
    return any(start <= pos < stop for start, stop in spans)


def extract_symbols(text: str, *, max_entities: int = 3, scan_chars: int = 200) -> list[dict[str, str]]:
    """从标题+摘要开头抽取标的（简称词典最长非重叠匹配）。

    返回按出现顺序的 ``[{"symbol","name","market"}]``；抽不到返回空列表。
    只扫前 ``scan_chars`` 字，控制长文成本；2 字符简称要过通用词黑名单。

    两类假阳性必须压掉，否则故事主体会错挂（生产样本已复现）：
    ① 署名括号里的券商/团队名（``【国泰海通煤炭|黄涛团队】电投能源三季报点评``）；
    ② 券商简称本身（``国联民生证券电子薛宏伟…``）——它是内容生产方，不是事件主体。
    两者都只**降级**不删除：正文里另有主体时不采信，通篇只讲它自己时仍然采信，
    这样「国泰君安换股吸收合并海通证券」这类以券商为主体的新闻不会被误杀。
    """
    hay = str(text or "")[:scan_chars]
    if not hay:
        return []
    author_spans = _author_spans(hay)
    index = _alias_index()
    found: list[tuple[int, str]] = []
    for pos in range(len(hay) - 1):
        candidates = index.get(hay[pos:pos + 2])
        if not candidates:
            continue
        for alias in candidates:
            if len(alias) == 2 and alias in _GENERIC_TWO_CHAR:
                continue
            if not hay.startswith(alias, pos):
                continue
            if not _boundary_ok(hay, pos, alias):
                continue
            found.append((pos, alias))
            break  # 同一起点取最长简称（候选已按长度倒序）
    subject = [
        hit for hit in found
        if not _in_spans(hit[0], author_spans) and hit[1] not in PUBLISHER_ALIASES
    ]
    if subject:
        found = subject
    else:
        found = [hit for hit in found if not _in_spans(hit[0], author_spans)] or found
    found.sort(key=lambda item: (-len(item[1]), item[0]))
    taken: list[tuple[int, int, str]] = []
    for pos, alias in found:
        if any(pos < end and pos + len(alias) > start for start, end, _ in taken):
            continue  # 与已接受的更长简称重叠 → 丢弃
        taken.append((pos, pos + len(alias), alias))
        if len(taken) >= max_entities:
            break
    taken.sort(key=lambda item: item[0])
    meta = security_meta()
    aliases = securities_master.alias_map()
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for _, _, alias in taken:
        symbol = aliases.get(alias)
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        name, market = meta.get(symbol, (alias, ""))
        out.append({"symbol": symbol, "name": name or alias, "market": market})
    return out


# ---------------------------------------------------------------------------
# 时间解析
# ---------------------------------------------------------------------------

_TS_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%Y/%m/%d",
)


def parse_ts(value: Any) -> Optional[datetime]:
    """把站内混用的时间字符串（UTC 带冒号、北京 +0800、纯日期、naive）解析成 aware UTC。"""
    text = str(value or "").strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    for fmt in _TS_FORMATS:
        try:
            parsed = datetime.strptime(text[:32], fmt)
        except ValueError:
            continue
        if parsed.tzinfo is None:
            # naive 一律按北京时间理解：站内绝大多数 naive 时间戳来自北京时区采集
            parsed = parsed.replace(tzinfo=BJ_TZ)
        return parsed.astimezone(timezone.utc)
    return None


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _decay(hours_age: float) -> float:
    if hours_age <= 0:
        return 1.0
    return 0.5 ** (hours_age / HALF_LIFE_HOURS)


# ---------------------------------------------------------------------------
# 读取内容窗口
# ---------------------------------------------------------------------------

def _facet_map(conn: sqlite3.Connection, content_ids: Sequence[str], facets: Sequence[str]) -> dict[str, dict[str, list[tuple[str, str, float]]]]:
    """按内容 ID 批量取本体标签（分块 IN，避免 SQLite 变量上限）。"""
    out: dict[str, dict[str, list[tuple[str, str, float]]]] = defaultdict(lambda: defaultdict(list))
    wanted = tuple(facets)
    for i in range(0, len(content_ids), 400):
        chunk = list(content_ids[i:i + 400])
        marks = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"""SELECT l.content_id, t.facet, t.code, t.label, l.confidence
                FROM content_tag_links l JOIN ontology_tags t ON t.id = l.tag_id
                WHERE l.content_id IN ({marks}) AND t.facet IN ({','.join('?' * len(wanted))})""",
            (*chunk, *wanted),
        ).fetchall()
        for row in rows:
            out[row["content_id"]][row["facet"]].append((row["code"], row["label"], float(row["confidence"] or 0)))
    return {k: dict(v) for k, v in out.items()}


def load_window_items(
    conn: Optional[sqlite3.Connection],
    *,
    window_hours: int = WINDOW_HOURS_DEFAULT,
    limit: int = MAX_WINDOW_ITEMS,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """读取窗口内的内容对象，附上本体 facet 与抽取出的标的。

    SQL 侧用日期前缀粗筛（避免全表逐行解析时间），Python 侧再按解析后的时间精确过滤；
    解析不出时间的内容按窗口边界处理（时效最低分），不因格式问题丢弃。
    """
    if conn is None:
        return []
    reference = now or datetime.now(timezone.utc)
    cutoff = reference - timedelta(hours=window_hours)
    day_cutoff = (cutoff - timedelta(days=1)).astimezone(BJ_TZ).strftime("%Y-%m-%d")
    rows = conn.execute(
        """SELECT id, content_type, title, summary, source_name, symbol, url, published_at, metadata_json
           FROM content_objects
           WHERE substr(published_at, 1, 10) >= ? OR published_at = ''
           ORDER BY published_at DESC
           LIMIT ?""",
        (day_cutoff, max(1, int(limit))),
    ).fetchall()
    facets = _facet_map(conn, [row["id"] for row in rows], ("theme", "event", "signal", "horizon", "source", "market", "access", "evidence_role"))

    items: list[dict[str, Any]] = []
    for row in rows:
        published = parse_ts(row["published_at"])
        if published is not None and published < cutoff:
            continue
        try:
            meta = json.loads(row["metadata_json"] or "{}")
        except Exception:  # noqa: BLE001 — 元数据损坏按空处理
            meta = {}
        tags = facets.get(row["id"], {})
        title = clean_title(row["title"])
        summary = _WS_RE.sub(" ", str(row["summary"] or "")).strip()
        entities = extract_symbols(f"{title} {summary[:120]}")
        provider_symbol = str(row["symbol"] or "").strip().upper()
        if provider_symbol and provider_symbol not in {e["symbol"] for e in entities}:
            market = "CN" if re.fullmatch(r"\d{6}(\.(SH|SZ))?", provider_symbol) else ""
            meta_name = security_meta().get(provider_symbol, (provider_symbol, market))
            entities.insert(0, {"symbol": provider_symbol, "name": meta_name[0], "market": meta_name[1] or market})
        codes = {facet: [code for code, _, _ in tags.get(facet, [])] for facet in ("theme", "event", "signal", "horizon", "source", "market")}
        labels = {facet: [label for _, label, _ in tags.get(facet, [])] for facet in ("theme", "event", "signal", "horizon", "source")}
        access_tags = tags.get("access") or []
        role_tags = tags.get("evidence_role") or []
        items.append({
            "id": str(row["id"]),
            "content_type": str(row["content_type"] or "evidence"),
            "title": title,
            "raw_title": str(row["title"] or ""),
            "summary": summary[:400],
            "source_name": str(row["source_name"] or "").strip() or "站内内容",
            "url": str(row["url"] or ""),
            "published_at": _iso(published) if published else "",
            "ts": published,
            "access_level": (access_tags[0][0] if access_tags else str(meta.get("access_level") or "public")),
            "evidence_role": (role_tags[0][0] if role_tags else str(meta.get("evidence_role") or "context")),
            "themes": codes.get("theme") or [],
            "theme_labels": labels.get("theme") or [],
            "events": codes.get("event") or [],
            "event_labels": labels.get("event") or [],
            "signals": codes.get("signal") or [],
            "horizons": codes.get("horizon") or [],
            "source_tiers": codes.get("source") or [],
            "markets": codes.get("market") or [],
            "entities": entities,
        })
    for item in items:
        item["tier"] = _tier_of(item)
        item["score"] = item_score(item, reference)
        item["sig"] = title_signature(item["title"])
    return items


def _tier_of(item: dict[str, Any]) -> str:
    for tier in item.get("source_tiers") or []:
        if tier in TIER_SCORE:
            return tier
    return "other"


def item_score(item: dict[str, Any], reference: datetime) -> float:
    """单条内容的重要度 = 体裁权重 × 来源层级 × 时效。全部取值有界，便于回归断言。"""
    age_hours = float("inf")
    ts = item.get("ts")
    if isinstance(ts, datetime):
        age_hours = max(0.0, (reference - ts).total_seconds() / 3600.0)
    freshness = 0.35 if not math.isfinite(age_hours) else 0.35 + 0.65 * _decay(age_hours)
    weight = TYPE_WEIGHT.get(item.get("content_type") or "", 0.5)
    tier = TIER_SCORE.get(item.get("tier") or "other", 0.45)
    return round(weight * tier * freshness, 4)


# ---------------------------------------------------------------------------
# 聚类
# ---------------------------------------------------------------------------

def filtered_signatures(items: Sequence[dict[str, Any]]) -> tuple[list[set[str]], list[list[str]]]:
    """计算聚类用签名，剔除文档频率过高的通用签名（「公司」「美股」这类），不修改入参。

    返回 ``(签名集合列表, 稀有度排序列表)``：后者按全局频率升序，用作簇身份签名的取样来源。
    一条内容可能同时归属多个桶，因此这里绝不就地改写 ``item['sig']``。
    """
    total = max(1, len(items))
    freq: Counter[str] = Counter()
    for item in items:
        freq.update(set(item.get("sig") or ()))
    cutoff = max(6, int(total * GENERIC_SIGNATURE_RATIO))
    generic = {token for token, count in freq.items() if count > cutoff}
    sigs: list[set[str]] = []
    rares: list[list[str]] = []
    for item in items:
        original = set(item.get("sig") or ())
        kept = {token for token in original if token not in generic} or original
        sigs.append(kept)
        rares.append(sorted(kept, key=lambda token: (freq.get(token, 0), token))[:8])
    return sigs, rares


def cluster_items(sigs: Sequence[set[str]], *, threshold: float = JACCARD_MERGE) -> list[list[int]]:
    """稀有签名倒排生成候选对 + Jaccard 门槛 + 并查集。返回下标分组（大簇在前）。

    并查集是传递的：A≈B、B≈C 就把 A、C 也并进来，研报标题里「机器人/业绩/观点/更新」
    这类术语会把一整天的无关报告链成一坨（生产样本实测出过 34 条的假簇）。因此除了
    共享签名数与 Jaccard，还要求至少共享 ``MIN_SHARED_RARE`` 个**桶内低频**签名
    （文档频率 ≤ ``RARE_DF_MAX``）——低频签名才是「同一件事」的指纹。
    """
    n = len(sigs)
    if n == 0:
        return []
    if n == 1:
        return [[0]]
    df: Counter[str] = Counter()
    for sig in sigs:
        df.update(set(sig))
    # 低频门槛随桶规模放宽：34 条的桶里 df≤3 才有区分度，700 条的 misc 桶要放到 df≤6，
    # 否则一整个桶会因为「没有任何低频共享签名」碎成单条簇，覆盖率凭空塌掉。
    rare_cutoff = max(RARE_DF_MAX, min(6, int(n * 0.05)))
    postings: dict[str, list[int]] = defaultdict(list)
    for idx, sig in enumerate(sigs):
        for token in sig:
            postings[token].append(idx)
    pairs: Counter[tuple[int, int]] = Counter()
    rare_pairs: Counter[tuple[int, int]] = Counter()
    for token, posting in postings.items():
        if len(posting) < 2 or len(posting) > MAX_POSTING_LEN:
            continue
        rare = df.get(token, 0) <= rare_cutoff
        for a in range(len(posting)):
            for b in range(a + 1, len(posting)):
                i, j = posting[a], posting[b]
                key = (i, j) if i < j else (j, i)
                pairs[key] += 1
                if rare:
                    rare_pairs[key] += 1
    uf = _UnionFind(n)
    for (i, j), shared in pairs.items():
        if shared < MIN_SHARED_SIGNATURES or rare_pairs.get((i, j), 0) < MIN_SHARED_RARE:
            continue
        if jaccard(sigs[i], sigs[j]) >= threshold:
            uf.union(i, j)
    groups: dict[int, list[int]] = defaultdict(list)
    for idx in range(n):
        groups[uf.find(idx)].append(idx)
    return sorted(groups.values(), key=lambda members: (-len(members), members[0]))


def buckets_for(item: dict[str, Any], *, max_entities: int = 2) -> list[tuple[str, str, dict[str, str]]]:
    """一条内容归属的全部桶（可多归属）：``[(桶键, 故事种类, 主实体)]``。

    桶键口径：**主体 + 事件类型**——「茅台·价格变化」和「茅台·业绩变化」是两件事，
    必须分开；同一主体同一事件的不同来源报道则是同一件事，必须合并。

    多归属是刻意的：一条「宁德时代与赛力斯签署电池供应协议」既属于宁德时代也属于赛力斯，
    两家公司的持仓者都应该在自己的故事流里看到它。因此公司桶最多按前 ``max_entities``
    个标的各归一次；没有标的时退回主题桶，主题也没有则进 misc（靠标题相似度聚类）。
    """
    event = (item.get("events") or ["general"])[0] or "general"
    entities = item.get("entities") or []
    buckets: list[tuple[str, str, dict[str, str]]] = [
        (f"sec:{entity['symbol']}:{event}", "company", entity)
        for entity in entities[:max(1, max_entities)]
    ]
    if buckets:
        return buckets
    themes = item.get("themes") or []
    if themes:
        return [(f"theme:{themes[0]}:{event}", "theme", {})]
    return [("misc", "macro", {})]


def bucket_of(item: dict[str, Any]) -> tuple[str, str, dict[str, str]]:
    """主归属桶（``buckets_for`` 的第一个）。单归属场景与测试用。"""
    return buckets_for(item, max_entities=1)[0]


# ---------------------------------------------------------------------------
# 故事卡片
# ---------------------------------------------------------------------------

def _why_chips(
    *,
    kind: str,
    entity_name: str,
    theme_labels: Sequence[str],
    item_count: int,
    source_count: int,
    has_authority: bool,
    risk_count: int,
    fresh: bool,
) -> list[str]:
    """解释标签的唯一实现：实时构建与落库回读共用，避免两处口径漂移。"""
    why: list[str] = []
    if kind == "company" and entity_name:
        why.append(f"{entity_name} 相关事件")
    elif theme_labels:
        why.append(f"{theme_labels[0]} 主题")
    why.append(f"{item_count} 条内容 / {source_count} 个来源")
    if has_authority:
        why.append("含机构或官方来源")
    if source_count <= 1:
        why.append("单一来源，建议交叉验证")
    if risk_count:
        why.append(f"{risk_count} 条风险提示")
    if fresh:
        why.append("近 12 小时有更新")
    return why


def _title_quality(title: str) -> float:
    """标题信息量评分：同等权威度下，代表条目要选**读得懂**的那个。

    站内海外研报大量以「名称+日期」为标题（``杰克科技20261023``），拿它当故事标题
    等于什么都没说；同一簇里通常另有带事件描述的报告标题，必须优先选后者。
    """
    text = str(title or "").strip()
    if not text:
        return -2.0
    quality = 0.0
    if re.search(r"20\d{6}$", text):
        quality -= 1.5
    if 12 <= len(text) <= 70:
        quality += 1.0
    elif len(text) < 8:
        quality -= 0.5
    if re.search(r"[：:，,、—｜]", text):
        quality += 0.5
    if re.search(r"[0-9]+(?:\.[0-9]+)?%|亿元|万吨|同比|环比|季度|上调|下调|签|获批|处罚|减持|增持|回购", text):
        quality += 0.5
    return quality


def _representative(members: Sequence[dict[str, Any]], *, prefer_name: str = "") -> dict[str, Any]:
    """代表条目：标题点名主体 > 来源层级 > 体裁权重 > 标题信息量 > 条目分。

    第一顺位必须是「标题里就有这个主体」：公司桶的成员都提到了该主体，但有的只在摘要里提到，
    拿它当代表会出现「卡片主体是英伟达、标题却在讲另一家公司」的错位（生产样本已复现）。
    """
    name = str(prefer_name or "").strip()

    def key(item: dict[str, Any]) -> tuple[float, float, float, float, float]:
        title = clean_title(item.get("title"))
        return (
            1.0 if name and name in title else 0.0,
            TIER_SCORE.get(item.get("tier") or "other", 0.4),
            TYPE_WEIGHT.get(item.get("content_type") or "", 0.5),
            _title_quality(title),
            float(item.get("score") or 0),
        )

    return max(members, key=key)


def _role_of(item: dict[str, Any]) -> str:
    """成员在故事里的证据角色（互斥，便于前端分区展示）。

    优先级：观点 > 风险 > 事实 > 背景。研报/纪要即使提示风险也归「观点」——风险提示是
    该观点的一部分，把它挪到风险区会让用户看不到机构结论；风险区留给**新闻级**的风险事实
    （快讯/文章里的处罚、违约、下调等）与显式反证。整体多空分布另由 ``by_signal`` 承载。
    """
    signals = item.get("signals") or []
    content_type = item.get("content_type")
    role = str(item.get("evidence_role") or "context")
    if content_type in ("research", "institution_note", "celebrity_view") or role in ("opinion", "analysis"):
        return "opinion"
    if role == "counter" or "risk" in signals:
        return "risk"
    if content_type == "flash" or role == "fact":
        return "fact"
    return "context"


def _dominant(values: Sequence[str], fallback: str = "") -> str:
    counted = Counter([v for v in values if v])
    return counted.most_common(1)[0][0] if counted else fallback


def _clean_excerpt(text: Any) -> str:
    """摘录清洗：去表情方括号/前导装饰符、折叠空白。正文本身不改写。"""
    cleaned = _WS_RE.sub(" ", str(text or "")).strip()
    cleaned = _EMOJI_TAG_RE.sub(" ", cleaned)
    cleaned = _LEAD_NOISE_RE.sub("", cleaned)
    cleaned = _HASH_LEAD_RE.sub("", cleaned)
    return _WS_RE.sub(" ", cleaned).strip()


def _excerpt_ok(text: str) -> bool:
    """占位式摘要（``海外投行 · 2026-10-23 · 海外投行报告``）不算摘录。"""
    if len(text) < MIN_EXCERPT_CHARS:
        return False
    return not _PLACEHOLDER_EXCERPT_RE.search(text)


def _best_excerpt(members: Sequence[dict[str, Any]], prefer_name: str = "") -> str:
    """挑一段真正有信息量的正文摘录。

    站内海外研报的 summary 大量是「海外投行报告」「海外投行 · 日期 · 类型」这类占位串，
    机构纪要是「图片动态」；直接拿代表条目的 summary 当摘录，用户看到的就是废话。
    这里只在**通过占位判定**的成员里挑，优先点名主体的、分数高的、更长的；
    一条都不达标就返回空串，由调用方给出诚实提示。
    """
    name = str(prefer_name or "").strip()
    scored: list[tuple[float, float, int, str]] = []
    for item in members:
        excerpt = _clean_excerpt(item.get("summary"))
        if not _excerpt_ok(excerpt):
            continue
        hit = 1.0 if name and name in f"{item.get('title') or ''} {excerpt}" else 0.0
        scored.append((hit, float(item.get("score") or 0), len(excerpt), excerpt))
    if not scored:
        return ""
    return max(scored)[3]


def _summary_text(
    *,
    entity_name: str,
    event_label: str,
    theme_labels: Sequence[str],
    members: Sequence[dict[str, Any]],
    source_count: int,
    window_hours: int,
    role_counts: Counter,
) -> str:
    """确定性摘要：主体 + 事件 + 证据规模 + 代表内容摘录。没有的字段就不提，绝不补写。"""
    subject = entity_name or (theme_labels[0] if theme_labels else "市场")
    pieces: list[str] = []
    head = f"{subject}"
    if event_label:
        head += f" · {event_label}"
    pieces.append(head)
    pieces.append(
        f"近 {window_hours} 小时站内 {len(members)} 条相关内容、{source_count} 个来源"
        f"（事实 {role_counts.get('fact', 0)} / 观点 {role_counts.get('opinion', 0)} / 风险 {role_counts.get('risk', 0)}）。"
    )
    excerpt = _best_excerpt(members, entity_name)
    if excerpt:
        pieces.append(f"代表内容：{excerpt[:120]}{'…' if len(excerpt) > 120 else ''}")
    else:
        pieces.append("该事件簇暂无可直接引用的正文摘要，需点开原始内容核对。")
    return " ".join(pieces)


def _timeline(members: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """按北京日聚合的成员数量时间线（前端画热度条），每天保留分数最高的标题作样本。"""
    per_day: Counter[str] = Counter()
    best: dict[str, tuple[float, str]] = {}
    for item in members:
        ts = item.get("ts")
        if not isinstance(ts, datetime):
            continue
        day = ts.astimezone(BJ_TZ).strftime("%Y-%m-%d")
        per_day[day] += 1
        score = float(item.get("score") or 0)
        if day not in best or score > best[day][0]:
            best[day] = (score, clean_title(item["title"], 60))
    return [
        {"date": day, "count": per_day[day], "title": best.get(day, (0.0, ""))[1]}
        for day in sorted(per_day)[-14:]
    ]


def build_story_card(
    members: Sequence[dict[str, Any]],
    *,
    story_id: str,
    bucket: str,
    kind: str,
    window_hours: int,
    now: Optional[datetime] = None,
    primary_entity: Optional[dict[str, str]] = None,
    signatures: Optional[Sequence[str]] = None,
) -> dict[str, Any]:
    """把一簇成员组装成 StoryCard（结构化、可解释、可直接被前端消费）。

    ``primary_entity`` 由桶决定：一条内容可能同时提到多家公司，卡片主体必须是**这个桶**
    对应的那家，而不是标题里先出现的那家。``signatures`` 是簇身份签名（供 ID 复用），
    不传则按成员标题签名现算。
    """
    reference = now or datetime.now(timezone.utc)
    members = list(members)
    ordered = sorted(members, key=lambda item: (-float(item.get("score") or 0), item["title"]))
    all_entities: list[dict[str, str]] = []
    seen_symbols: set[str] = set()
    if primary_entity and primary_entity.get("symbol"):
        all_entities.append(dict(primary_entity))
        seen_symbols.add(primary_entity["symbol"])
    for item in ordered:
        for entity in item.get("entities") or []:
            if entity["symbol"] in seen_symbols:
                continue
            seen_symbols.add(entity["symbol"])
            all_entities.append(entity)
    primary = all_entities[0] if all_entities else {}
    entity_name = primary.get("name") or primary.get("symbol") or ""
    theme_codes = [c for c, _ in Counter(
        code for item in members for code in (item.get("themes") or [])[:2]
    ).most_common(4)]
    theme_labels = [_THEME_LABELS.get(code, code) for code in theme_codes]
    event_code = _dominant([code for item in members for code in (item.get("events") or [])[:1]])
    event_label = _EVENT_LABELS.get(event_code, "")
    signal_code = _dominant([code for item in members for code in (item.get("signals") or [])[:1]], "unconfirmed")
    horizon_code = _dominant([code for item in members for code in (item.get("horizons") or [])[:1]], "unassigned")
    market = primary.get("market") or _dominant([m for item in members for m in (item.get("markets") or [])[:1]])
    role_counts: Counter = Counter(_role_of(item) for item in members)
    type_counts: Counter = Counter(item.get("content_type") or "evidence" for item in members)
    signal_counts: Counter = Counter(
        (item.get("signals") or ["unconfirmed"])[0] for item in members
    )
    sources = {item.get("source_name") or "站内内容" for item in members}
    timestamps = [item["ts"] for item in members if isinstance(item.get("ts"), datetime)]
    newest = max(timestamps) if timestamps else None
    oldest = min(timestamps) if timestamps else None

    volume = sum(float(item.get("score") or 0) for item in members)
    breadth = len(sources)
    tiers = [item.get("tier") or "other" for item in members]
    authority = max((TIER_SCORE.get(tier, 0.45) for tier in tiers), default=0.45)
    top_tier = max(tiers, key=lambda tier: TIER_SCORE.get(tier, 0.45)) if tiers else "other"
    recency = _decay(max(0.0, (reference - newest).total_seconds() / 3600.0)) if newest else 0.35
    mass_raw = math.log1p(volume) * (0.55 + 0.25 * math.log1p(breadth)) * (0.6 + 0.4 * authority)
    if breadth <= 1 and len(members) >= SINGLE_SOURCE_STREAM_MIN:
        # 单来源刷出几十条同构内容（行情播报、指数快照）是**流**不是事件：
        # 数量再多也不该压过三家来源共同印证的一件事。
        mass_raw *= SINGLE_SOURCE_STREAM_PENALTY
    if kind == "company" and entity_name in PUBLISHER_ALIASES:
        # 主体就是券商/投行自己（其研究观点在站内被反复转发）：这不是市场事件，
        # 降权保留（个股页仍能查到），但不该占据大众看板的重要度位次。
        mass_raw *= PUBLISHER_SUBJECT_PENALTY
    mass = round(min(1.0, (mass_raw / MASS_NORM)) * (0.75 + 0.25 * recency), 4)

    cluster_signatures = list(signatures) if signatures else _cluster_signatures(
        [sorted(item.get("sig") or ())[:8] for item in members]
    )
    representative = _representative(members, prefer_name=entity_name)
    title = clean_title(representative.get("title"))
    if _title_quality(title) <= 0:
        # 代表标题是「名称+日期」这类占位式研报标题（站内海外研报的常见命名）时，
        # 用**结构化主体 + 事件类型**补一个可读标题。两者都直接来自内容自身的标注与
        # 抽取结果，不引入任何未在原文出现的说法；原始标题仍完整保留在 items 里可追溯。
        subject = entity_name or (theme_labels[0] if theme_labels else "")
        if subject:
            title = f"{subject}·{event_label or '综合动态'}"
    summary = _summary_text(
        entity_name=entity_name,
        event_label=event_label,
        theme_labels=theme_labels,
        members=members,
        source_count=breadth,
        window_hours=window_hours,
        role_counts=role_counts,
    )
    impact = IMPACT_RULES.get(event_code, IMPACT_RULES["*"])
    verify = list(VERIFY_RULES.get(event_code, VERIFY_RULES["*"]))
    risk_count = max(int(role_counts.get("risk") or 0), int(signal_counts.get("risk") or 0))
    why = _why_chips(
        kind=kind,
        entity_name=entity_name,
        theme_labels=theme_labels,
        item_count=len(members),
        source_count=breadth,
        has_authority=authority >= TIER_SCORE["institution"],
        risk_count=risk_count,
        fresh=recency >= 0.85,
    )

    items_payload = []
    for item in ordered[:40]:
        score = round(float(item.get("score") or 0), 4)
        content_type = item.get("content_type") or "evidence"
        items_payload.append({
            "content_id": item.get("id") or "",
            "role": _role_of(item),
            "rank": score,
            "item_score": score,
            "content_type": content_type,
            "content_type_label": CONTENT_TYPE_LABELS.get(content_type, content_type),
            "title": item.get("title") or "",
            "summary": item.get("summary") or "",
            "source_name": item.get("source_name") or "站内内容",
            "source_tier": item.get("tier") or "other",
            "url": item.get("url") or "",
            "access_level": item.get("access_level") or "public",
            "published_at": item.get("published_at") or "",
        })

    return {
        "story_id": story_id,
        "bucket": bucket,
        "kind": kind,
        "title": title,
        "summary": summary,
        "symbol": primary.get("symbol") or "",
        "entity_name": entity_name,
        "market": market or "",
        "market_label": _MARKET_LABELS.get(market or "", market or ""),
        "event_code": event_code,
        "event_label": event_label or "综合动态",
        "theme_codes": theme_codes,
        "theme_labels": theme_labels,
        "signal_code": signal_code,
        "signal_label": _SIGNAL_LABELS.get(signal_code, "待确认"),
        "horizon_code": horizon_code,
        "horizon_label": _HORIZON_LABELS.get(horizon_code, "周期待定"),
        "impact": impact,
        "verify": verify,
        "why": why,
        "mass": mass,
        "top_tier": top_tier,
        "recency": round(recency, 4),
        "item_count": len(members),
        "source_count": breadth,
        "by_type": dict(type_counts.most_common()),
        "by_role": dict(role_counts.most_common()),
        "by_signal": dict(signal_counts.most_common()),
        "symbols": all_entities[:8],
        "themes": [{"code": c, "label": l} for c, l in zip(theme_codes, theme_labels)],
        "timeline": _timeline(members),
        "items": items_payload,
        "first_seen": _iso(oldest) if oldest else "",
        "last_seen": _iso(newest) if newest else "",
        "window_hours": window_hours,
        "signatures": cluster_signatures,
        "actions": _actions_for(kind, primary.get("symbol") or "", story_id, title),
        "disclaimer": "以上为站内公开内容的事实聚合与核对提示，不构成投资建议。",
    }


def _actions_for(kind: str, symbol: str, story_id: str, title: str) -> list[dict[str, str]]:
    """故事卡的下一步动作：只指向站内已有视图，不生成任何交易动作。"""
    actions: list[dict[str, str]] = [
        {"kind": "story", "label": "看这件事的全部证据", "story_id": story_id},
        {"kind": "ask", "label": "让 AI 帮我核对这件事", "query": clean_title(title, 60)},
    ]
    if kind == "company" and symbol:
        actions.insert(0, {"kind": "stock", "label": f"打开 {symbol} 工作区", "symbol": symbol})
    return actions


def _cluster_signatures(rares: Sequence[Sequence[str]], top: int = 6) -> list[str]:
    """簇签名：成员稀有签名里出现最多的若干个，用于跨重建的故事身份比对。"""
    counted: Counter[str] = Counter()
    for tokens in rares:
        counted.update(tokens)
    return [token for token, _ in counted.most_common(top)]


# ---------------------------------------------------------------------------
# 构建 + 落库
# ---------------------------------------------------------------------------

def _resolve_story_id(
    conn: sqlite3.Connection,
    *,
    bucket: str,
    signatures: Sequence[str],
    last_active: str,
    first_active: str,
) -> str:
    """复用仍活跃的历史故事 ID；没有匹配则铸造新 ID 并登记签名。"""
    cutoff = ""
    try:
        cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=MERGE_GAP_DAYS))
    except Exception:  # noqa: BLE001
        cutoff = ""
    best_id, best_score = "", 0.0
    rows = conn.execute(
        "SELECT story_id, signatures, last_active FROM story_registry WHERE bucket = ? AND last_active >= ? ORDER BY last_active DESC LIMIT 200",
        (bucket, cutoff),
    ).fetchall()
    for row in rows:
        try:
            stored = json.loads(row["signatures"] or "[]")
        except Exception:  # noqa: BLE001
            stored = []
        score = jaccard(stored, signatures)
        if score > best_score:
            best_id, best_score = row["story_id"], score
    if best_id and best_score >= JACCARD_REGISTRY:
        conn.execute(
            "UPDATE story_registry SET signatures = ?, last_active = ? WHERE story_id = ?",
            (json.dumps(sorted(set(signatures)), ensure_ascii=False), last_active or first_active, best_id),
        )
        return best_id
    digest = hashlib.sha1(f"{bucket}|{'|'.join(sorted(signatures))}|{first_active}".encode("utf-8")).hexdigest()[:14]
    story_id = f"story:{digest}"
    conn.execute(
        "INSERT INTO story_registry (story_id, bucket, signatures, first_active, last_active) VALUES (?,?,?,?,?) "
        "ON CONFLICT(story_id) DO UPDATE SET signatures=excluded.signatures, last_active=excluded.last_active",
        (story_id, bucket, json.dumps(sorted(set(signatures)), ensure_ascii=False), first_active, last_active or first_active),
    )
    return story_id


def _persist(conn: sqlite3.Connection, cards: Sequence[dict[str, Any]], generated_at: str) -> None:
    for card in cards:
        conn.execute(
            """INSERT INTO stories (
                   story_id, bucket, kind, title, summary, symbol, entity_name, market,
                   event_code, event_label, theme_codes, theme_labels, signal_code, horizon_code,
                   impact, verify_json, mass, top_tier, recency, item_count, source_count, by_type_json,
                   by_role_json, by_signal_json, symbols_json, timeline_json, first_seen, last_seen,
                   generated_at, updated_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(story_id) DO UPDATE SET
                   bucket=excluded.bucket, kind=excluded.kind, title=excluded.title,
                   summary=excluded.summary, symbol=excluded.symbol, entity_name=excluded.entity_name,
                   market=excluded.market, event_code=excluded.event_code, event_label=excluded.event_label,
                   theme_codes=excluded.theme_codes, theme_labels=excluded.theme_labels,
                   signal_code=excluded.signal_code, horizon_code=excluded.horizon_code,
                   impact=excluded.impact, verify_json=excluded.verify_json, mass=excluded.mass,
                   top_tier=excluded.top_tier,
                   recency=excluded.recency, item_count=excluded.item_count,
                   source_count=excluded.source_count, by_type_json=excluded.by_type_json,
                   by_role_json=excluded.by_role_json, by_signal_json=excluded.by_signal_json,
                   symbols_json=excluded.symbols_json, timeline_json=excluded.timeline_json,
                   first_seen=excluded.first_seen, last_seen=excluded.last_seen,
                   generated_at=excluded.generated_at, updated_at=excluded.updated_at""",
            (
                card["story_id"], card["bucket"], card["kind"], card["title"], card["summary"],
                card["symbol"], card["entity_name"], card["market"], card["event_code"],
                card["event_label"], json.dumps(card["theme_codes"], ensure_ascii=False),
                json.dumps(card["theme_labels"], ensure_ascii=False), card["signal_code"],
                card["horizon_code"], card["impact"], json.dumps(card["verify"], ensure_ascii=False),
                card["mass"], card.get("top_tier") or "other", card["recency"], card["item_count"],
                card["source_count"],
                json.dumps(card["by_type"], ensure_ascii=False), json.dumps(card["by_role"], ensure_ascii=False),
                json.dumps(card["by_signal"], ensure_ascii=False), json.dumps(card["symbols"], ensure_ascii=False),
                json.dumps(card["timeline"], ensure_ascii=False), card["first_seen"], card["last_seen"],
                generated_at, generated_at,
            ),
        )
        conn.execute("DELETE FROM story_items WHERE story_id = ?", (card["story_id"],))
        conn.executemany(
            """INSERT OR REPLACE INTO story_items (
                   story_id, content_id, role, rank, item_score, content_type, title,
                   source_name, url, access_level, published_at
               ) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            [
                (
                    card["story_id"], item["content_id"], item["role"], item["rank"], item["item_score"],
                    item["content_type"], item["title"], item["source_name"], item["url"],
                    item["access_level"], item["published_at"],
                )
                for item in card["items"]
            ],
        )


def _prune(conn: sqlite3.Connection, now: datetime) -> int:
    cutoff = _iso(now - timedelta(days=RETENTION_DAYS))
    stale = [row["story_id"] for row in conn.execute("SELECT story_id FROM stories WHERE last_seen < ? OR last_seen = ''", (cutoff,))]
    for start in range(0, len(stale), 400):
        chunk = stale[start:start + 400]
        marks = ",".join("?" * len(chunk))
        conn.execute(f"DELETE FROM story_items WHERE story_id IN ({marks})", chunk)
        conn.execute(f"DELETE FROM stories WHERE story_id IN ({marks})", chunk)
    conn.execute("DELETE FROM story_registry WHERE last_active < ?", (cutoff,))
    return len(stale)


def rebuild_stories(
    *,
    window_hours: int = WINDOW_HOURS_DEFAULT,
    limit: int = MAX_WINDOW_ITEMS,
    persist: bool = True,
    now: Optional[datetime] = None,
    store: Optional[sqlite3.Connection] = None,
    source: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """重建窗口内的故事并落库。返回构建统计（可直接用于日志与健康检查）。

    ``store``/``source`` 仅供测试注入：分别是故事库连接与内容库只读连接。
    内容库不存在时按空窗口降级（不抛异常），保证后台任务不会把整个进程拖崩。
    """
    reference = now or datetime.now(timezone.utc)
    window_hours = max(6, min(int(window_hours or WINDOW_HOURS_DEFAULT), WINDOW_HOURS_MAX))
    init_story_db()
    own_store = store is None
    own_source = source is None
    handle = store or _connect()
    content_conn = source if source is not None else (_ontology_conn() if own_source else None)
    try:
        rows = load_window_items(content_conn, window_hours=window_hours, limit=limit, now=reference) if content_conn is not None else []
        usable = [item for item in rows if not is_boilerplate(item.get("raw_title") or item.get("title"))]
        items = dedupe_items(usable)
        cards = build_cards(items, window_hours=window_hours, now=reference, store=handle if persist else None)
        cards.sort(key=lambda card: (-card["mass"], -card["item_count"], card["title"]))
        kept = [card for card in cards if card["item_count"] >= 2 or _high_authority(card)][:MAX_PERSISTED_STORIES]
        generated_at = utc_now_iso()
        if persist:
            _persist(handle, kept, generated_at)
            pruned = _prune(handle, reference)
            handle.commit()
        else:
            pruned = 0
        return {
            "generated_at": generated_at,
            "window_hours": window_hours,
            "content_rows": len(rows),
            "content_items": len(items),
            "boilerplate_skipped": len(rows) - len(usable),
            "duplicates_dropped": len(usable) - len(items),
            "content_source": "missing" if content_conn is None else "ontology",
            "clusters": len(cards),
            "stories_persisted": len(kept) if persist else 0,
            "stories_pruned": pruned,
            "company_stories": sum(1 for card in kept if card["kind"] == "company"),
            "theme_stories": sum(1 for card in kept if card["kind"] == "theme"),
            "macro_stories": sum(1 for card in kept if card["kind"] == "macro"),
            "top_mass": kept[0]["mass"] if kept else 0.0,
        }
    finally:
        if own_store:
            handle.close()
        if own_source and content_conn is not None and source is None:
            content_conn.close()


def _high_authority(card: dict[str, Any]) -> bool:
    """单条内容也能成故事的前提：来源足够权威（官方公告/机构研究）。

    没有这道门槛，一条社区帖也会变成一张故事卡，首页会被噪音淹没；
    但官方公告往往只有一条，丢掉它就是漏掉真事件。
    """
    if card["item_count"] != 1:
        return False
    if card.get("top_tier") in ("official", "institution"):
        return True
    by_type = card.get("by_type") or {}
    return bool(by_type.get("research") or by_type.get("institution_note"))


def _assign_buckets(items: Sequence[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str], dict[str, dict[str, str]]]:
    """分桶，并把「同一主体下未识别事件类型」的内容并回该主体的主导事件桶。

    为什么必须做这一步：事件标签靠关键词规则，同一家公司对同一件事的不同报道措辞各异，
    有的命中「批价」有的一个词都没命中。不合并的话，未打标的那条会单独进 ``:general`` 桶，
    再被单条门槛过滤掉——用户看到的故事就少了一家来源的佐证，覆盖率凭空掉一截。
    合并有前提：该主体的主导事件必须占已打标内容的 ≥70% 且至少 2 条，否则宁可留在 general，
    也不把「业绩」和「回购」两件事混成一张卡。
    """
    entries: list[tuple[str, str, dict[str, str], dict[str, Any]]] = []
    for item in items:
        for bucket, kind, entity in buckets_for(item):
            entries.append((bucket, kind, entity, item))

    tagged: dict[str, Counter] = defaultdict(Counter)
    for bucket, _, _, _ in entries:
        if bucket.endswith(":general") or bucket == "misc":
            continue
        scope, _, event = bucket.rpartition(":")
        tagged[scope][event] += 1
    dominant: dict[str, str] = {}
    for scope, counter in tagged.items():
        total = sum(counter.values())
        event, count = counter.most_common(1)[0]
        if total >= 2 and count / total >= 0.7:
            dominant[scope] = event

    buckets: dict[str, list[dict[str, Any]]] = defaultdict(list)
    kinds: dict[str, str] = {}
    primaries: dict[str, dict[str, str]] = {}
    for bucket, kind, entity, item in entries:
        target = bucket
        if bucket.endswith(":general") and bucket != "misc":
            scope = bucket[: -len(":general")]
            if scope in dominant:
                target = f"{scope}:{dominant[scope]}"
        buckets[target].append(item)
        kinds[target] = kind
        if entity and target not in primaries:
            primaries[target] = entity
    return buckets, kinds, primaries


def build_cards(
    items: Sequence[dict[str, Any]],
    *,
    window_hours: int = WINDOW_HOURS_DEFAULT,
    now: Optional[datetime] = None,
    store: Optional[sqlite3.Connection] = None,
) -> list[dict[str, Any]]:
    """纯计算入口（测试友好）：内容条目 → StoryCard 列表。``store`` 提供时才做 ID 复用。"""
    reference = now or datetime.now(timezone.utc)
    buckets, kinds, primaries = _assign_buckets(items)
    cards: list[dict[str, Any]] = []
    for bucket, members in buckets.items():
        members.sort(key=lambda item: (item.get("ts") is None, -(item.get("ts").timestamp() if isinstance(item.get("ts"), datetime) else 0)))
        cap = MAX_MISC_ITEMS if bucket == "misc" else MAX_ITEMS_PER_BUCKET
        pool = members[:cap]
        sigs, rares = filtered_signatures(pool)
        # 公司桶已按「主体 + 事件」切过：规模很小且事件类型确实识别出来时，整桶即一个故事
        # （避免把同一件事的不同措辞拆成多张卡）。桶一大、或事件类型是兜底的 general，
        # 就必须再做标题相似度细分——否则一天里所有提到该公司的报告会糊成一张假卡。
        event_identified = not bucket.startswith("sec:") or not bucket.endswith(":general")
        if bucket.startswith("sec:") and len(pool) <= COMPANY_SUBCLUSTER_MIN and event_identified:
            groups = [list(range(len(pool)))]
        else:
            groups = cluster_items(sigs, threshold=JACCARD_COMPANY if bucket.startswith("sec:") else JACCARD_MERGE)
        for group in groups:
            cluster = [pool[idx] for idx in group]
            signatures = _cluster_signatures([rares[idx] for idx in group])
            timestamps = [item["ts"] for item in cluster if isinstance(item.get("ts"), datetime)]
            first_active = _iso(min(timestamps)) if timestamps else _iso(reference)
            last_active = _iso(max(timestamps)) if timestamps else _iso(reference)
            story_id = ""
            if store is not None:
                story_id = _resolve_story_id(
                    store, bucket=bucket, signatures=signatures,
                    last_active=last_active, first_active=first_active,
                )
            if not story_id:
                story_id = "story:" + hashlib.sha1(
                    f"{bucket}|{'|'.join(sorted(signatures))}|{first_active}".encode("utf-8")
                ).hexdigest()[:14]
            cards.append(build_story_card(
                cluster, story_id=story_id, bucket=bucket, kind=kinds[bucket],
                window_hours=window_hours, now=reference,
                primary_entity=primaries.get(bucket), signatures=signatures,
            ))
    return cards


# ---------------------------------------------------------------------------
# 查询
# ---------------------------------------------------------------------------

def _row_to_card(row: sqlite3.Row) -> dict[str, Any]:
    def load(field: str, default: Any) -> Any:
        try:
            return json.loads(row[field] or "null") or default
        except Exception:  # noqa: BLE001
            return default

    card: dict[str, Any] = {
        "story_id": row["story_id"],
        "bucket": row["bucket"],
        "kind": row["kind"],
        "title": row["title"],
        "summary": row["summary"],
        "symbol": row["symbol"],
        "entity_name": row["entity_name"],
        "market": row["market"],
        "market_label": _MARKET_LABELS.get(row["market"] or "", row["market"] or ""),
        "event_code": row["event_code"],
        "event_label": row["event_label"] or "综合动态",
        "theme_codes": load("theme_codes", []),
        "theme_labels": load("theme_labels", []),
        "signal_code": row["signal_code"],
        "signal_label": _SIGNAL_LABELS.get(row["signal_code"], "待确认"),
        "horizon_code": row["horizon_code"],
        "horizon_label": _HORIZON_LABELS.get(row["horizon_code"], "周期待定"),
        "impact": row["impact"],
        "verify": load("verify_json", []),
        "mass": float(row["mass"] or 0),
        "top_tier": row["top_tier"] or "other",
        "recency": float(row["recency"] or 0),
        "item_count": int(row["item_count"] or 0),
        "source_count": int(row["source_count"] or 0),
        "by_type": load("by_type_json", {}),
        "by_role": load("by_role_json", {}),
        "by_signal": load("by_signal_json", {}),
        "symbols": load("symbols_json", []),
        "themes": [
            {"code": code, "label": label}
            for code, label in zip(load("theme_codes", []), load("theme_labels", []))
        ],
        "timeline": load("timeline_json", []),
        "first_seen": row["first_seen"],
        "last_seen": row["last_seen"],
        "generated_at": row["generated_at"],
        "actions": _actions_for(row["kind"], row["symbol"], row["story_id"], row["title"]),
        "disclaimer": "以上为站内公开内容的事实聚合与核对提示，不构成投资建议。",
    }
    card["why"] = _why_from_card(card)
    return card


def _why_from_card(card: dict[str, Any]) -> list[str]:
    """从已落库字段重建大众侧解释标签（与实时构建口径一致，不额外存一列）。"""
    return _why_chips(
        kind=str(card.get("kind") or ""),
        entity_name=str(card.get("entity_name") or ""),
        theme_labels=list(card.get("theme_labels") or []),
        item_count=int(card.get("item_count") or 0),
        source_count=int(card.get("source_count") or 0),
        has_authority=card.get("top_tier") in ("official", "institution"),
        risk_count=_risk_weight(card),
        fresh=float(card.get("recency") or 0) >= 0.85,
    )


def list_stories(
    *,
    limit: int = 30,
    window_hours: int = WINDOW_HOURS_DEFAULT,
    kind: str = "",
    symbol: str = "",
    min_items: int = 1,
    with_items: int = 0,
    now: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    """按大众重要度取故事（公共资产，不含任何账号数据）。

    ``with_items > 0`` 时一并附上每个故事分数最高的若干条成员内容（一次批量查询），
    简报与首页卡片需要展示来源与原文链接时用。
    """
    init_story_db()
    reference = now or datetime.now(timezone.utc)
    cutoff = _iso(reference - timedelta(hours=max(6, min(int(window_hours), WINDOW_HOURS_MAX))))
    limit = max(1, min(int(limit or 30), 100))
    clauses = ["last_seen >= ?"]
    params: list = [cutoff]
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if symbol:
        clauses.append("(symbol = ? OR symbols_json LIKE ?)")
        params.extend([symbol.upper(), f'%"{symbol.upper()}"%'])
    if min_items > 1:
        clauses.append("item_count >= ?")
        params.append(int(min_items))
    params.append(limit)
    with _connect() as conn:
        rows = conn.execute(
            f"SELECT * FROM stories WHERE {' AND '.join(clauses)} ORDER BY mass DESC, item_count DESC LIMIT ?",
            params,
        ).fetchall()
        cards = [_row_to_card(row) for row in rows]
        if with_items > 0 and cards:
            _attach_items(conn, cards, per_story=max(1, min(int(with_items), 20)))
    return cards


def _item_payload(row: sqlite3.Row) -> dict[str, Any]:
    content_type = row["content_type"] or ""
    return {
        "content_id": row["content_id"],
        "role": row["role"],
        "content_type": content_type,
        "content_type_label": CONTENT_TYPE_LABELS.get(content_type, content_type),
        "title": row["title"],
        "source_name": row["source_name"],
        "url": row["url"],
        "access_level": row["access_level"],
        "published_at": row["published_at"],
        "item_score": float(row["item_score"] or 0),
    }


def _attach_items(conn: sqlite3.Connection, cards: Sequence[dict[str, Any]], *, per_story: int = 5) -> None:
    """批量给卡片附上 top-N 成员内容（就地写入 ``card['items']``），避免 N+1 查询。"""
    if not cards:
        return
    ids = [card["story_id"] for card in cards]
    marks = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT * FROM story_items WHERE story_id IN ({marks}) ORDER BY story_id, rank DESC",
        ids,
    ).fetchall()
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        bucket = grouped[row["story_id"]]
        if len(bucket) < per_story:
            bucket.append(_item_payload(row))
    for card in cards:
        card["items"] = grouped.get(card["story_id"], [])


def story_detail(story_id: str, *, item_limit: int = 40) -> Optional[dict[str, Any]]:
    """故事详情：卡片 + 分区成员内容（事实/观点/风险/背景）。"""
    init_story_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
        if row is None:
            return None
        card = _row_to_card(row)
        items = conn.execute(
            "SELECT * FROM story_items WHERE story_id = ? ORDER BY rank DESC LIMIT ?",
            (story_id, max(1, min(int(item_limit), 100))),
        ).fetchall()
    grouped: dict[str, list] = {"fact": [], "opinion": [], "risk": [], "context": []}
    for item in items:
        grouped.setdefault(item["role"], grouped["context"]).append(_item_payload(item))
    card["facts"] = grouped["fact"]
    card["opinions"] = grouped["opinion"]
    card["risks"] = grouped["risk"]
    card["contexts"] = grouped["context"]
    card["items"] = grouped["fact"] + grouped["opinion"] + grouped["risk"] + grouped["context"]
    return card


def stories_for_symbol(symbol: str, *, days: int = 30, limit: int = 30, with_items: int = 0) -> list[dict[str, Any]]:
    """个股故事时间线：该股相关事件簇按最近活跃排序。"""
    clean = str(symbol or "").strip().upper()
    if not clean:
        return []
    init_story_db()
    cutoff = _iso(datetime.now(timezone.utc) - timedelta(days=max(1, min(int(days), 180))))
    with _connect() as conn:
        rows = conn.execute(
            """SELECT * FROM stories
               WHERE (symbol = ? OR symbols_json LIKE ?) AND last_seen >= ?
               ORDER BY last_seen DESC, mass DESC LIMIT ?""",
            (clean, f'%"{clean}"%', cutoff, max(1, min(int(limit), 100))),
        ).fetchall()
        cards = [_row_to_card(row) for row in rows]
        if with_items > 0 and cards:
            _attach_items(conn, cards, per_story=max(1, min(int(with_items), 20)))
    return cards


def story_health() -> dict[str, Any]:
    """故事层健康度（管理端/自检用）：数量、覆盖、最新构建时间。"""
    init_story_db()
    with _connect() as conn:
        total = conn.execute("SELECT count(*) AS c FROM stories").fetchone()["c"]
        latest = conn.execute("SELECT max(generated_at) AS g FROM stories").fetchone()["g"]
        kinds = {row["kind"]: row["c"] for row in conn.execute("SELECT kind, count(*) AS c FROM stories GROUP BY kind")}
        top = conn.execute("SELECT story_id, title, mass, item_count, source_count FROM stories ORDER BY mass DESC LIMIT 5").fetchall()
        items = conn.execute("SELECT count(*) AS c FROM story_items").fetchone()["c"]
    return {
        "stories": int(total or 0),
        "story_items": int(items or 0),
        "by_kind": kinds,
        "last_build": latest or "",
        "top": [dict(row) for row in top],
    }


# ---------------------------------------------------------------------------
# 大众简报（零 LLM，逐字确定）
# ---------------------------------------------------------------------------

def _is_publisher_subject(card: dict[str, Any]) -> bool:
    """卡片主体是否为券商/投行（内容生产方）自身。"""
    return str(card.get("entity_name") or "").strip() in PUBLISHER_ALIASES


def _pick_distinct(
    cards: Sequence[dict[str, Any]],
    limit: int,
    used: set[str],
) -> list[dict[str, Any]]:
    """按主体去重地取前 ``limit`` 张卡；``used`` 是跨分区共享的已选集合（就地更新）。"""
    picks: list[dict[str, Any]] = []
    subjects: set[str] = set()
    for card in cards:
        story_id = str(card.get("story_id") or "")
        if story_id and story_id in used:
            continue
        subject = _brief_subject(card)
        if subject and subject in subjects:
            continue
        picks.append(card)
        if story_id:
            used.add(story_id)
        if subject:
            subjects.add(subject)
        if len(picks) >= limit:
            break
    return picks


def build_mass_brief(
    cards: Sequence[dict[str, Any]],
    *,
    window_hours: int = WINDOW_HOURS_DEFAULT,
    top_themes: int = 3,
    top_companies: int = 6,
    top_risks: int = 4,
) -> dict[str, Any]:
    """把故事卡组装成面向大众的综合简报（结构化 + 可渲染文本）。

    三个分区共用一套去重：同一件事不在「公司焦点」和「风险留意」里各出现一次。
    同主体的多个事件簇在一个分区里也只留重要度最高的那一个，否则读者看到的是同一行字重复三遍。
    主体就是券商/投行自己的卡片不进简报——那是内容生产方的观点转发，不是市场事件。
    """
    cards = [card for card in cards if not _is_publisher_subject(card)]
    companies = [card for card in cards if card["kind"] == "company"]
    themes = [card for card in cards if card["kind"] != "company"]
    risks = sorted(
        (card for card in cards if _risk_weight(card) > 0),
        key=lambda card: (-_risk_weight(card), -float(card.get("mass") or 0)),
    )
    used: set[str] = set()
    theme_picks = _pick_distinct(themes, max(1, top_themes), used)
    company_picks = _pick_distinct(companies, max(1, top_companies), used)
    risk_picks = _pick_distinct(risks, max(1, top_risks), used)
    sections = [
        {
            "key": "themes",
            "title": "市场主线",
            "note": "按跨来源覆盖度排序的主题事件簇",
            "items": [_brief_item(card) for card in theme_picks],
        },
        {
            "key": "companies",
            "title": "公司焦点",
            "note": "站内证据最多的公司事件",
            "items": [_brief_item(card) for card in company_picks],
        },
        {
            "key": "risks",
            "title": "风险留意",
            "note": "含削弱论点内容的故事，需优先核对",
            "items": [_brief_item(card) for card in risk_picks],
        },
    ]
    total_items = sum(int(card.get("item_count") or 0) for card in cards)
    total_sources = len({
        item.get("source_name") for card in cards for item in (card.get("items") or []) if item.get("source_name")
    })
    headline = ""
    if theme_picks:
        headline = (
            f"近 {window_hours} 小时站内 {len(cards)} 件事、{total_items} 条内容；"
            f"主线是「{_brief_subject(theme_picks[0])}」。"
        )
    stats = {
        "stories": len(cards),
        "content_items": total_items,
        "sources": total_sources,
        "company_stories": len(companies),
        "risk_stories": len(risks),
        "window_hours": window_hours,
    }
    return {
        "generated_at": utc_now_iso(),
        "scope": "mass",
        "window_hours": window_hours,
        "headline": headline,
        "sections": sections,
        "stats": stats,
        "disclaimer": "以上为站内公开内容的事实聚合，不构成投资建议。",
    }


def _risk_weight(card: dict[str, Any]) -> int:
    """风险条数：取「风险角色条数」与「削弱论点标签条数」的较大值。

    两者口径不同（前者是分区角色，后者是本体信号标签），相加会重复计数，取大值既不漏也不重。
    """
    by_role = int((card.get("by_role") or {}).get("risk") or 0)
    by_signal = int((card.get("by_signal") or {}).get("risk") or 0)
    return max(by_role, by_signal)


def _brief_subject(card: dict[str, Any]) -> str:
    """简报条目的主体名。

    主题/宏观簇的代表标题往往是一份报告的名字（``物理AI产业专家交流20261022``），
    拿它当主线名读起来像噪音；这类簇一律用**主题标签**作主体，原标题退为代表内容。
    """
    if card.get("kind") == "company" and card.get("entity_name"):
        return str(card["entity_name"])
    labels = card.get("theme_labels") or []
    if labels:
        return str(labels[0])
    return str(card.get("title") or "")


def _brief_item(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "story_id": card["story_id"],
        "subject": _brief_subject(card),
        "title": card["title"],
        "summary": card["summary"],
        "kind": card["kind"],
        "symbol": card.get("symbol") or "",
        "entity_name": card.get("entity_name") or "",
        "event_label": card.get("event_label") or "",
        "signal_label": card.get("signal_label") or "",
        "impact": card.get("impact") or "",
        "verify": card.get("verify") or [],
        "item_count": card.get("item_count") or 0,
        "source_count": card.get("source_count") or 0,
        "mass": card.get("mass") or 0,
        "sources": [
            {"name": item.get("source_name") or "", "url": item.get("url") or "", "published_at": item.get("published_at") or ""}
            for item in (card.get("items") or [])[:5]
        ],
    }


def render_brief_text(brief: dict[str, Any], *, max_items_per_section: int = 4) -> str:
    """把简报渲染成推送/晨报可直接使用的纯文本（确定性排版，无模型参与）。"""
    lines: list[str] = ["📌 站内事件简报"]
    headline = str(brief.get("headline") or "").strip()
    if headline:
        lines.append(headline)
    for section in brief.get("sections") or []:
        items = section.get("items") or []
        if not items:
            continue
        lines.append("")
        lines.append(f"【{section.get('title') or section.get('key')}】")
        for index, item in enumerate(items[:max_items_per_section], start=1):
            subject = item.get("subject") or item.get("entity_name") or item.get("title") or ""
            lines.append(f"{index}. {subject}")
            if item.get("event_label"):
                lines.append(f"   事件：{item['event_label']}｜证据：{item.get('item_count', 0)} 条 / {item.get('source_count', 0)} 个来源")
            title = str(item.get("title") or "").strip()
            if title and title != subject and not title.startswith(f"{subject}·"):
                lines.append(f"   代表：{title[:60]}")
            if item.get("impact"):
                lines.append(f"   影响：{item['impact']}")
            verify = item.get("verify") or []
            if verify:
                lines.append(f"   待核对：{verify[0]}")
    lines.append("")
    lines.append(str(brief.get("disclaimer") or "以上为事实聚合，不构成投资建议。"))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 后台任务
# ---------------------------------------------------------------------------

async def run_story_engine() -> None:
    """周期重建故事簇。env 总闸 ``DEEPFOCUS_STORY_ENGINE=0`` 可整体关闭。

    重建含内容库全表扫描，放线程池执行，不占用 API 事件循环。
    """
    if os.getenv("DEEPFOCUS_STORY_ENGINE", "1") != "1":
        print("[story-engine] 已通过 env 关闭")
        return
    await asyncio.sleep(45)
    interval = max(5.0, float(os.getenv("DEEPFOCUS_STORY_INTERVAL_MIN", str(REBUILD_INTERVAL_MINUTES)))) * 60
    print(f"[story-engine] 启动：每 {interval / 60:.0f} 分钟重建事件簇")
    last_prune = 0.0
    while True:
        try:
            stats = await asyncio.to_thread(rebuild_stories)
            print(
                "[story-engine] 重建完成：内容 %(content_items)s 条 → 故事 %(stories_persisted)s 个"
                "（公司 %(company_stories)s / 主题 %(theme_stories)s / 宏观 %(macro_stories)s）" % stats
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — 单次失败不影响下一轮
            print(f"[story-engine] 重建异常：{type(exc).__name__}: {exc}")
        # 埋点事件每日剪枝一次（超期行删除）；懒导入避免与 story_events 的循环依赖。
        now_ts = time.monotonic()
        if now_ts - last_prune >= 86400.0:
            last_prune = now_ts
            try:
                from . import story_events
                removed = await asyncio.to_thread(story_events.prune)
                if removed:
                    print(f"[story-engine] 埋点剪枝：清理 {removed} 条超期事件")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — 剪枝失败不影响重建
                print(f"[story-engine] 埋点剪枝异常：{type(exc).__name__}: {exc}")
        await asyncio.sleep(interval)
