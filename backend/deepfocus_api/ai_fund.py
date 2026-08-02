from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from . import ai_fund_evolve, bull_playbook, data_store
from .ifind_api import cached_single_quote, enabled as ifind_enabled, normalize_a_code
from .shared_utils import safe_float, utc_now_iso

# ROE 生命周期阶段(bull_playbook.roe_stage)→ 中文标签，给「成长质量」维度解说用
_STAGE_CN = {"startup": "初创期", "growth": "成长期", "mature": "成熟期",
             "decline": "衰退期", "transition": "过渡期", "unknown": "—"}
_TT_STAGE_CN = {"advancing": "上升段", "topping": "见顶段", "declining": "下跌段",
                "basing": "筑底段", "unknown": "—"}
_MODEL_CN = {"multifactor": "多因子催化动量", "value": "内在价值·DCF/质量", "reversion": "均值回归·抄超跌"}

"""
A股「AI 模拟盘」——一个有人设、讲章法、思路全公开的交易智能体「阿尔法」(演示位)。

灵魂：阿尔法是 DeepFocus 的 AI 操盘手，有性格、有心情(随盈亏变)，边交易边吐槽边解说。
章法(严谨)：选股=本站快讯/文章/研报催化剂 × 技术面(均线/趋势/前高) × 资金面(主力5日净流入) × 估值；
  买点要确认(站上20日线 or 强催化剂突破 + 不追涨停 + 资金不出逃)；卖点用趋势跟踪移动止损让利润奔跑 + 硬止损兜底。
可视化：每笔决策公开五维打分 + 思考链 + 买卖点理由 + 操盘解说。
盈利&节目：趋势跟踪吃大段 + 战绩统计(胜率/连胜) + 心情系统，做出直播节目效果。

⚠️ 虚拟资金、不接券商、不下真实单(牌照所限/合规)；故障安全：iFinD/日线取不到则降级、绝不臆造。
"""

BJ_TZ = timezone(timedelta(hours=8))
FUND_ID = "main"
INITIAL_CAPITAL = float(os.getenv("DEEPFOCUS_AIFUND_CAPITAL", "1000000"))
MAX_POSITIONS = 6
BOARD_LOT = 100
BUY_THRESHOLD = 0.14
SELL_THRESHOLD = -0.06
HARD_STOP = -0.08                # 硬止损
TRAIL_MIN = 0.08                 # 移动止盈最小回撤容忍
ROTATE_EDGE = 0.28
NAV_HISTORY_KEEP = 1440
FRESH_HOURS = 72.0
MD_FETCH_CAP = 8                 # 每轮最多新拉多少只东财日线/资金流(防风控；6h 缓存复用)
TONE = os.getenv("DEEPFOCUS_AIFUND_NARRATE", "1") != "0"
MUSE_ALL = os.getenv("DEEPFOCUS_AIFUND_MUSE_ALL", "0") == "1"  # 全员 LLM 独白(更生动·约5×成本)；默认仅主账户 LLM、其余走流派模板
MUSING_MIN_GAP_MIN = 75.0        # 两条「脑内独白」最小间隔(分钟)；换时段则立即可再发——24h 持续沉淀又不刷屏
MUSING_KEEP = 60                 # 脑内独白滚动保留条数(独立于观察流的 40 上限)
DEBATE = os.getenv("DEEPFOCUS_AIFUND_DEBATE", "1") != "0"  # 多空辩论总开关(可关，省 token)
DEBATE_MAX_PER_TICK = 1          # 每轮最多对几笔买入跑辩论(控成本；取信心最高的)
DEBATE_DEDUP_H = 24.0            # 同股辩论去重窗口(小时)：近 24h 已辩过则复用、不重复烧 token
# 赛马分歧票多视角解说：对「有人多有人空」的票生成两派一句话对话——只在后台 tick 后预生成并缓存,
# get_arena 请求路径只读缓存(零 LLM、零延迟)。env DEEPFOCUS_AIFUND_DIV_TAKES=0 可关。
DIV_TAKES = os.getenv("DEEPFOCUS_AIFUND_DIV_TAKES", "1") != "0"
DIV_TAKES_MAX_STOCKS = int(os.getenv("DEEPFOCUS_AIFUND_DIV_TAKES_MAX", "2"))  # 每轮最多给几只分歧票生成(控成本)
DIV_TAKES_TTL = float(os.getenv("DEEPFOCUS_AIFUND_DIV_TAKES_TTL_H", "12")) * 3600  # 同阵营组合不变就复用
DEBATE_KEEP = 40                 # 辩论记录滚动保留条数

PERSONA_NAME = "阿尔法"
PERSONA_TAG = "DeepFocus AI 操盘手 · 只买我们快讯/研报点过名的票"

# 五维加权基线（消息面=数据驱动主信号）。各智能体在此基线上调权，形成不同流派。
_BASE_WEIGHTS = {"消息面": 0.34, "技术面": 0.18, "趋势": 0.14, "资金面": 0.14,
                 "成长质量": 0.10, "基本面": 0.08, "情绪": 0.02}

DEFAULT_POOL: dict[str, str] = {
    "002594": "比亚迪", "300750": "宁德时代", "601127": "赛力斯", "688981": "中芯国际",
    "688256": "寒武纪", "002475": "立讯精密", "300059": "东方财富", "002230": "科大讯飞",
    "002415": "海康威视", "600111": "北方稀土", "601012": "隆基绿能", "300760": "迈瑞医疗",
    "601318": "中国平安", "600519": "贵州茅台", "600036": "招商银行", "601899": "紫金矿业",
    # 赛马扩池：补一批催化剂活跃的成长/题材股，给激进/事件/逆向流派施展空间（大盘股池跑不出大波段）
    "300308": "中际旭创", "002241": "歌尔股份", "603501": "韦尔股份", "688111": "金山办公",
    "300124": "汇川技术", "002371": "北方华创", "688012": "中微公司", "300433": "蓝思科技",
    "601088": "中国神华", "600900": "长江电力", "000858": "五粮液", "600276": "恒瑞医药",
}


@dataclass(frozen=True)
class AgentConfig:
    """一个 AI 操盘手的人设 + 流派参数。所有账户共用同一套引擎与数据层，靠这里的参数分化出不同打法。
    默认值精确复刻原「阿尔法」单账户行为 → 老调用 run_tick()/get_snapshot() 行为不变（向后兼容）。"""
    fund_id: str = FUND_ID
    name: str = PERSONA_NAME
    tag: str = PERSONA_TAG
    emoji: str = "🤖"
    style: str = "balanced"            # 流派 key：balanced/aggressive/value/event/contrarian
    model: str = "multifactor"         # 打分模型(真·不同算法)：multifactor / value(DCF·质量) / reversion(均值回归)
    blurb: str = "读本站快讯/研报当催化剂，趋势均衡、严谨择时"
    initial_capital: float = INITIAL_CAPITAL
    max_positions: int = MAX_POSITIONS
    buy_threshold: float = BUY_THRESHOLD
    sell_threshold: float = SELL_THRESHOLD
    hard_stop: float = HARD_STOP
    trail_min: float = TRAIL_MIN
    rotate_edge: float = ROTATE_EDGE
    fresh_hours: float = FRESH_HOURS   # 催化剂有效期窗口（价值/逆向用更长，事件驱动用更短）
    weights: dict = field(default_factory=lambda: dict(_BASE_WEIGHTS))
    pool: dict = field(default_factory=lambda: dict(DEFAULT_POOL))  # 候选股池（默认共用扩展池）
    # —— 流派旋钮（行为分化，不只是换皮）——
    chase_ok: bool = False             # 激进：允许追高/不惩罚涨停
    contrarian: bool = False           # 逆向：超跌 + 催化剂反买，不强制站上 20 日线
    max_pe: Optional[float] = None     # 价值：估值上限，超过则不出手
    pos_size_mult: float = 1.0         # 单仓大小系数（激进重仓、价值分散）
    reentry_cooldown_days: int = 1     # 平仓后至少隔日再评估；1=禁同日卖出后买回
    max_daily_buys: int = 2            # 单日最多新开仓次数，防高频来回打脸
    muse: bool = False                 # 是否产出 LLM「脑内独白」（控成本：仅主账户开）
    debate: bool = False               # 是否对重大买入跑「多空辩论→裁判」推演（控成本：仅主账户开）


# 赛马名单：阿尔法(主) + 4 个鲜明流派。共用引擎/数据缓存，靠参数分化打法 → 天然产生冠军 + 分歧 = 内容。
MAIN_CFG = AgentConfig(muse=True, debate=True)
ROSTER: list[AgentConfig] = [
    MAIN_CFG,
    AgentConfig(
        fund_id="mammoth", name="猛犸", emoji="🦣", style="aggressive",
        tag="DeepFocus AI 操盘手 · 强催化就重锤、追涨不手软",
        blurb="激进趋势：强催化重仓、敢追突破、集中持股、短打快攻",
        max_positions=4, buy_threshold=0.11, sell_threshold=-0.05, hard_stop=-0.10,
        trail_min=0.10, rotate_edge=0.22, fresh_hours=48.0, chase_ok=True, pos_size_mult=1.35,
        reentry_cooldown_days=1, max_daily_buys=3,
        weights={"消息面": 0.32, "技术面": 0.24, "趋势": 0.16, "资金面": 0.16,
                 "成长质量": 0.05, "基本面": 0.05, "情绪": 0.02}),
    AgentConfig(
        fund_id="rock", name="磐石", emoji="🗿", style="value", model="value",
        tag="DeepFocus AI 操盘手 · 只在便宜处下手、拿得住",
        blurb="稳健价值：估值有底线、重盈利质量、分散持有、拿得久",
        max_positions=8, buy_threshold=0.18, sell_threshold=-0.08, hard_stop=-0.07,
        trail_min=0.12, rotate_edge=0.34, fresh_hours=240.0, max_pe=40.0, pos_size_mult=0.8,
        reentry_cooldown_days=3, max_daily_buys=1,
        weights={"消息面": 0.24, "技术面": 0.12, "趋势": 0.12, "资金面": 0.10,
                 "成长质量": 0.20, "基本面": 0.20, "情绪": 0.02}),
    AgentConfig(
        fund_id="falcon", name="游隼", emoji="🦅", style="event",
        tag="DeepFocus AI 操盘手 · 闻讯而动、来去如风",
        blurb="事件驱动：本站快讯一响就扑、吃催化剂快进快出",
        max_positions=5, buy_threshold=0.13, sell_threshold=-0.05, hard_stop=-0.08,
        trail_min=0.07, rotate_edge=0.24, fresh_hours=36.0, pos_size_mult=1.1,
        reentry_cooldown_days=1, max_daily_buys=2,
        weights={"消息面": 0.46, "技术面": 0.16, "趋势": 0.10, "资金面": 0.16,
                 "成长质量": 0.04, "基本面": 0.04, "情绪": 0.04}),
    AgentConfig(
        fund_id="contra", name="磁极", emoji="🧲", style="contrarian", model="reversion",
        tag="DeepFocus AI 操盘手 · 别人恐惧我贪婪、抄超跌",
        blurb="逆向抄底：超跌 + 本站催化共振才反手、专挑被错杀",
        max_positions=6, buy_threshold=0.12, sell_threshold=-0.07, hard_stop=-0.09,
        trail_min=0.10, rotate_edge=0.30, fresh_hours=180.0, contrarian=True, pos_size_mult=0.95,
        reentry_cooldown_days=2, max_daily_buys=2,
        weights={"消息面": 0.30, "技术面": 0.10, "趋势": 0.10, "资金面": 0.18,
                 "成长质量": 0.12, "基本面": 0.16, "情绪": 0.04}),
]
ROSTER_BY_ID: dict[str, AgentConfig] = {c.fund_id: c for c in ROSTER}


def cfg_for(fund_id: Optional[str]) -> AgentConfig:
    """fund_id → AgentConfig，未知 id 回退主账户配置（绝不抛出）。"""
    return ROSTER_BY_ID.get(fund_id or FUND_ID, MAIN_CFG)

_SRC_LABEL = {"dao-news": "快讯", "dao-article": "文章", "dao-report": "研报", "dao-signal": "信号"}
_BULL_KW = ("利好", "大涨", "涨停", "突破", "中标", "签约", "增长", "超预期", "扩产", "提价",
            "新高", "回购", "增持", "订单", "放量", "量产", "获批", "合作", "需求旺", "涨价")
_BEAR_KW = ("利空", "大跌", "跌停", "下滑", "亏损", "减持", "警示", "下调", "处罚", "退市",
            "新低", "爆雷", "诉讼", "召回", "减产", "解禁", "问询", "风险", "出逃")


# A股法定节假日休市日历（沪深北交易所官方公告）。⚠️每年 12 月需更新次年安排（来源：上交所「部分节假日休市安排」通知）。
# 周末本就休市，此处列出全部官方休市日（含落在周末的，列全无妨）。盘前即可知今天开不开盘，比「靠指数日线判断」更早、更准。
CN_MARKET_HOLIDAYS = {
    "2026-01-01", "2026-01-02",                                                       # 元旦
    "2026-02-16", "2026-02-17", "2026-02-18", "2026-02-19", "2026-02-20", "2026-02-23",  # 春节
    "2026-04-06",                                                                      # 清明
    "2026-05-01", "2026-05-04", "2026-05-05",                                          # 劳动节
    "2026-06-19",                                                                      # 端午
    "2026-09-25",                                                                      # 中秋
    "2026-10-01", "2026-10-02", "2026-10-05", "2026-10-06", "2026-10-07",              # 国庆
}


def _is_trading_day(now: Optional[datetime] = None) -> bool:
    """今天是否 A 股交易日：周一~五 且 非法定节假日。⚠️节假日靠 CN_MARKET_HOLIDAYS 按年维护。
    （A 股不随『调休补班』在周末开市，故只需排除周末 + 法定休市日。）"""
    now = now or datetime.now(BJ_TZ)
    if now.weekday() >= 5:
        return False
    return now.strftime("%Y-%m-%d") not in CN_MARKET_HOLIDAYS


def _in_session(now: Optional[datetime] = None) -> bool:
    """是否在 A 股可交易时段：交易日(非周末/非节假日) 且 北京时间 09:30–11:30 / 13:00–15:00。"""
    now = now or datetime.now(BJ_TZ)
    if not _is_trading_day(now):
        return False
    hm = now.hour * 60 + now.minute
    return (570 <= hm <= 690) or (780 <= hm <= 900)


def _phase(now: Optional[datetime] = None) -> tuple[str, str]:
    """当前北京时段→(key, 展示标签)。驱动『脑内独白』在盘前/盘中/午间/盘后/夜间/周末/节假日用不同语气持续沉淀。"""
    now = now or datetime.now(BJ_TZ)
    wd = now.weekday(); hm = now.hour * 60 + now.minute
    if wd >= 5:
        return ("weekend", "📚 周末功课")
    if not _is_trading_day(now):
        return ("weekend", "🎏 节假日休市")  # 复用 weekend 文案逻辑(不开盘·功课不停)，仅标签不同
    if hm < 540:                # 00:00–09:00
        return ("evening", "🌙 夜间研究")
    if hm < 570:                # 09:00–09:30
        return ("preopen", "📡 盘前点火")
    if 570 <= hm <= 690:        # 09:30–11:30
        return ("morning", "📈 早盘盯盘")
    if 690 < hm < 780:          # 11:30–13:00
        return ("noon", "🍵 午间复盘")
    if 780 <= hm <= 900:        # 13:00–15:00
        return ("afternoon", "📈 午后盯盘")
    if hm <= 1080:              # 15:00–18:00
        return ("postclose", "🌆 盘后复盘")
    return ("evening", "🌙 夜间研究")  # 18:00–24:00


def _db_path() -> Path:
    return Path(os.getenv("DEEPFOCUS_AIFUND_DB_PATH",
                          str(Path(__file__).resolve().parents[1] / ".ai_fund.sqlite3")))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=5000")   # 公开读端点与写 tick 并发时不至于直接 database is locked
    conn.execute("PRAGMA journal_mode=WAL")    # 读写并发：写不再阻塞读
    return conn


def _add_col(conn, table, col, decl):
    if col not in {r["name"] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")


def init_ai_fund_db() -> None:
    p = _db_path(); p.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_state (fund_id TEXT PRIMARY KEY, cash REAL NOT NULL,
            started_at TEXT NOT NULL, started_nav REAL NOT NULL, last_tick_at TEXT)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_position (fund_id TEXT NOT NULL, symbol TEXT NOT NULL,
            name TEXT, qty REAL NOT NULL, avg_cost REAL NOT NULL, opened_at TEXT NOT NULL, updated_at TEXT NOT NULL,
            high_water REAL, PRIMARY KEY (fund_id, symbol))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_trade (id TEXT PRIMARY KEY, fund_id TEXT NOT NULL, ts TEXT NOT NULL,
            symbol TEXT NOT NULL, name TEXT, side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, amount REAL NOT NULL,
            pnl_pct REAL, reason TEXT NOT NULL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_nav (fund_id TEXT NOT NULL, ts TEXT NOT NULL, nav REAL NOT NULL,
            cash REAL NOT NULL, market_value REAL NOT NULL, PRIMARY KEY (fund_id, ts))""")
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_thought (id TEXT PRIMARY KEY, fund_id TEXT NOT NULL, ts TEXT NOT NULL,
            symbol TEXT, name TEXT, action TEXT, catalyst TEXT, thinking TEXT, narrative TEXT, confidence REAL)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_kline (symbol TEXT PRIMARY KEY, ohlc TEXT, updated_at TEXT)""")
        # 多空辩论推演（绑定到买入 trade_id）：多头立论 + 空头逐条审视 + 裁判最终裁决(含止损位/认错条件/选它的理由)
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_debate (trade_id TEXT PRIMARY KEY, fund_id TEXT NOT NULL, symbol TEXT,
            name TEXT, ts TEXT NOT NULL, payload TEXT)""")
        # 阿尔法的「长期记忆」——交易复盘教训(lesson) + 对个股的持续观点(thesis)；可召回、会衰减/强化、像人一样积累进化
        conn.execute("""CREATE TABLE IF NOT EXISTS aif_memory (id TEXT PRIMARY KEY, fund_id TEXT NOT NULL, symbol TEXT,
            name TEXT, mem_type TEXT NOT NULL, ts TEXT NOT NULL, updated_at TEXT, title TEXT NOT NULL, detail TEXT,
            confidence REAL NOT NULL, weight REAL NOT NULL, pnl_impact REAL, src TEXT, seen_count INTEGER DEFAULT 1)""")
        for col, decl in (("confidence", "REAL"), ("catalyst", "TEXT"), ("thinking", "TEXT"),
                          ("narrative", "TEXT"), ("scores", "TEXT"), ("buy_point", "TEXT"),
                          ("catalyst_ref", "TEXT"), ("composite", "REAL")):
            _add_col(conn, "aif_trade", col, decl)
        _add_col(conn, "aif_position", "high_water", "REAL")
        _add_col(conn, "aif_thought", "scores", "TEXT")
        _add_col(conn, "aif_thought", "recalled_refs", "TEXT")  # 本条独白召回了哪些记忆(给前端「想起…」chip)
        for col, decl in (("scanned_news", "INTEGER"), ("scanned_report", "INTEGER"),
                          ("scanned_article", "INTEGER"), ("scanned_date", "TEXT"), ("scanned_titles", "TEXT"),
                          ("mem_decay_date", "TEXT"), ("learned_weights", "TEXT"), ("coach_note", "TEXT"),
                          ("hold_learn_date", "TEXT")):   # 持仓期自适应学习的每日节流标记
            _add_col(conn, "aif_state", col, decl)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_aif_trade_ts ON aif_trade(fund_id, ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_aif_thought_ts ON aif_thought(fund_id, ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_aif_debate_sym ON aif_debate(fund_id, symbol, ts DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_aif_mem_w ON aif_memory(fund_id, weight DESC)")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_aif_mem_uniq ON aif_memory(fund_id, symbol, mem_type, title)")
        conn.commit()
        # 为名单里每个智能体各建一行账户状态（缺失即补，向后兼容：主账户 main 若已有历史则不动）。
        for c in ROSTER:
            if not conn.execute("SELECT 1 FROM aif_state WHERE fund_id=?", (c.fund_id,)).fetchone():
                conn.execute("INSERT INTO aif_state (fund_id,cash,started_at,started_nav,last_tick_at) VALUES (?,?,?,?,NULL)",
                             (c.fund_id, c.initial_capital, utc_now_iso(), c.initial_capital))
            _seed_trading_rules(conn, c.fund_id)   # 每个机器人都记着 A股交易规则(铁律)
        conn.commit()


def _state(conn, fund_id: str = FUND_ID): return conn.execute("SELECT * FROM aif_state WHERE fund_id=?", (fund_id,)).fetchone()
def _positions(conn, fund_id: str = FUND_ID): return conn.execute("SELECT * FROM aif_position WHERE fund_id=?", (fund_id,)).fetchall()


# --------------------------------------------------------------------------- #
# 本站内容（催化剂）/ 情绪 / 行情 / 日线技术面 + 资金面
# --------------------------------------------------------------------------- #

_CONTENT_CACHE: dict = {}          # name -> (ts, list)；本站内容查询轻缓存(护并发查看)


def _bj_date(ca: str) -> str:
    """ISO 时间 → 北京日期 YYYY-MM-DD（与 A 股日 K 对齐）。"""
    try:
        dt = datetime.fromisoformat((ca or "").replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BJ_TZ).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


# ── A股涨跌停规则：按板块/ST 的当日限幅 + 封板成交闸门(涨停买不进·跌停卖不出) ──────
def _price_limit_pct(code: str, name: str = "") -> float:
    """A股当日涨跌停幅度%：ST→5；科创688/689·创业板300/301→20；北交所8/4/920→30；其余主板→10。"""
    if "ST" in (name or "").upper():
        return 5.0
    c = code or ""
    if c.startswith(("688", "689", "300", "301")):
        return 20.0
    if c.startswith(("8", "4", "920")):
        return 30.0
    return 10.0


_LIMIT_EPS = 0.3   # 距离限板 ≤0.3% 视为封板(一字/封死)，成交不了


def _at_upper_limit(code: str, name: str, chg: Optional[float]) -> bool:
    """涨停封板：买不进(无人卖出)。"""
    return chg is not None and chg >= _price_limit_pct(code, name) - _LIMIT_EPS


def _at_lower_limit(code: str, name: str, chg: Optional[float]) -> bool:
    """跌停封板：卖不出(无人接盘)。"""
    return chg is not None and chg <= -(_price_limit_pct(code, name) - _LIMIT_EPS)


def _our_content(name: str) -> list[dict]:
    import time as _t
    hit = _CONTENT_CACHE.get(name)
    if hit and (_t.time() - hit[0]) < 60.0:
        return hit[1]
    try:
        from .realtime_messages import list_realtime_messages
        msgs = list_realtime_messages(anyq=name, limit=40)  # 多取历史，让事件按真实日期铺在 K 线上(非全堆今天)
    except Exception:  # noqa: BLE001
        return []
    now = datetime.now(timezone.utc); out = []
    for m in msgs:
        ca = getattr(m, "created_at", "") or ""
        try:
            dt = datetime.fromisoformat(ca.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            age = (now - dt).total_seconds() / 3600.0
        except Exception:  # noqa: BLE001
            age = 999.0
        out.append({"id": getattr(m, "id", ""), "title": getattr(m, "title", "") or "",
                    "src": _SRC_LABEL.get(getattr(m, "source_type", "") or "", "资讯"),
                    "severity": getattr(m, "severity", "") or "info", "age_h": round(age, 1),
                    "date": _bj_date(ca), "url": getattr(m, "url", "") or ""})
    _CONTENT_CACHE[name] = (_t.time(), out)
    return out


def _headline_dir(items: list[dict]) -> float:
    score = wsum = 0.0
    for it in items:
        age = float(it.get("age_h", 999.0))
        if age > FRESH_HOURS:
            continue
        t = it.get("title", "")
        s = sum(1 for k in _BULL_KW if k in t) - sum(1 for k in _BEAR_KW if k in t)
        s += 0.5 if it.get("severity") == "success" else (-0.5 if it.get("severity") == "warning" else 0)
        w = 1.0 / (1.0 + age / 12.0)
        score += max(-1.0, min(1.0, s / 2.0)) * w; wsum += w
    return (score / wsum) if wsum else 0.0


def _sentiment(items: list[dict]) -> Optional[float]:
    try:
        from .engagement import reactions_for
        ids = [it["id"] for it in items if it.get("id")][:30]
        if not ids:
            return None
        agg = reactions_for(ids)
        bull = sum(int(v.get("bull", 0)) for v in agg.values())
        bear = sum(int(v.get("bear", 0)) for v in agg.values())
        return (bull - bear) / (bull + bear) if (bull + bear) else None
    except Exception:  # noqa: BLE001
        return None


_FREE_Q_CACHE: dict = {}            # symbol -> (ts, quote)  iFinD 不可用时的免费兜底报价缓存
_FREE_Q_TTL = 90.0


def _free_quote(symbol: str) -> Optional[dict]:
    """iFinD 不可用(配额耗尽/未配置)时的免费实时价兜底——新浪行情(prod 实测可用,东财 push2 被服务器侧重置)。
    同步 httpx 直连(不起 asyncio 事件循环,无收尾噪音、更快);90s 缓存跨 5 策略共享、控请求。
    只取交易必需字段(价/涨跌/开高低);pe/pb/换手等缺失维度在 _analyze 里 `if x is not None` 自动跳过、不乱打分 → 优雅降级。"""
    import time as _t
    code = "".join(ch for ch in str(symbol) if ch.isdigit())[:6]
    if len(code) != 6:
        return None
    hit = _FREE_Q_CACHE.get(code)
    if hit and _t.time() - hit[0] < _FREE_Q_TTL:
        return hit[1]
    prefix = "sh" if code[0] in ("6", "9") else ("bj" if code[0] in ("4", "8") else "sz")
    try:
        import httpx
        r = httpx.get(f"https://hq.sinajs.cn/list={prefix}{code}",
                      headers={"Referer": "https://finance.sina.com.cn"}, timeout=10, trust_env=False)
        seg = r.text.split('"', 2)[1] if '"' in r.text else ""
        f = seg.split(",")  # A股: 0名 1开 2昨收 3现价 4高 5低 ...
        if len(f) < 6:
            return None
        latest = float(f[3]) or float(f[2])   # 现价为 0(停牌/集合竞价前)→ 用昨收兜底
        prev = float(f[2])
        if latest <= 0:
            return None
        chg = round((latest - prev) / prev * 100, 2) if prev else None
        q = {"latest": round(latest, 3), "changeRatio": chg,
             "open": float(f[1]) or None, "high": float(f[4]) or None, "low": float(f[5]) or None, "_src": "sina"}
        _FREE_Q_CACHE[code] = (_t.time(), q)
        return q
    except Exception:  # noqa: BLE001
        return None


def _quote(symbol):
    q = cached_single_quote(symbol)
    if q and safe_float(q.get("latest")):
        return q
    return _free_quote(symbol) or q   # iFinD 空(配额耗尽/未配置)→ 免费实时价兜底,保竞技场不停摆


def _secid(code: str) -> str:
    return f"1.{code}" if code and code[0] in ("6", "9") else f"0.{code}"


_KLINE_CACHE: dict = {}            # secid -> (ts, [{d,o,h,l,c}])
_FLOW_CACHE: dict = {}             # code -> (ts, flow5)
_KLINE_TTL = 6 * 3600.0
_EM_HEADERS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}


def _em_client():
    """东财专用 httpx 客户端：绕代理 + **强制 IPv4**（push2his 的 DNS 常只给 IPv6 且本机 IPv6 不通→秒断）。"""
    import httpx
    return httpx.AsyncClient(trust_env=False, timeout=12.0,
                             transport=httpx.AsyncHTTPTransport(local_address="0.0.0.0", retries=1))


async def _sina_kline(code: str, points: int) -> list:
    """新浪日线 OHLC（主源，IP 信誉独立于东财、稳定）：[{d,o,h,l,c}]。"""
    sym = ("sh" if code and code[0] in ("6", "9") else "sz") + code
    url = (f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol={sym}&scale=240&ma=no&datalen={points}")
    try:
        async with _em_client() as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        if r.status_code == 200:
            arr = r.json()
            out = []
            for k in (arr if isinstance(arr, list) else []):
                try:
                    out.append({"d": k["day"], "o": float(k["open"]), "h": float(k["high"]),
                                "l": float(k["low"]), "c": float(k["close"]), "v": float(k.get("volume") or 0)})
                except (KeyError, ValueError, TypeError):
                    pass
            return out
    except Exception:  # noqa: BLE001
        pass
    return []


async def _em_kline(secid: str, points: int) -> list:
    """东财日线 OHLC（备源，前复权）。强制 IPv4。"""
    url = (f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
           f"&fields1=f1&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&end=20500101&lmt={points}")
    try:
        async with _em_client() as c:
            r = await c.get(url, headers=_EM_HEADERS)
        if r.status_code == 200:
            out = []
            for line in ((r.json().get("data") or {}).get("klines")) or []:
                p = line.split(",")
                if len(p) >= 5:
                    try:
                        out.append({"d": p[0], "o": float(p[1]), "c": float(p[2]), "h": float(p[3]), "l": float(p[4]),
                                    "v": float(p[5]) if len(p) > 5 else 0})
                    except ValueError:
                        pass
            return out
    except Exception:  # noqa: BLE001
        pass
    return []


async def _fetch_kline_ohlc(secid: str, points: int = 250) -> list:
    """个股日线 OHLC：[{d,o,h,l,c}]。新浪主源→东财备源，缓存 6h。失败 []。secid 形如 '1.600111'。
    取 250 点≈一年日线：长线牛股趋势模板(MA60/120/250 + 距年高/年低)需要长历史。"""
    import time as _t
    hit = _KLINE_CACHE.get(secid)
    if hit and (_t.time() - hit[0]) < _KLINE_TTL:
        return hit[1]
    code = secid.split(".")[-1]
    out = await _sina_kline(code, points)
    if not out:
        out = await _em_kline(secid, points)
    if out:
        _KLINE_CACHE[secid] = (_t.time(), out)
    return out


async def _fetch_flow5(code: str) -> Optional[float]:
    """东财主力近5日净流入额（元）。强制 IPv4，缓存 6h。失败 None。"""
    import time as _t
    hit = _FLOW_CACHE.get(code)
    if hit and (_t.time() - hit[0]) < _KLINE_TTL:
        return hit[1]
    url = (f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={_secid(code)}"
           "&fields1=f1&fields2=f51,f52&klt=101&lmt=5")
    val = None
    for attempt in range(2):
        try:
            async with _em_client() as c:
                r = await c.get(url, headers=_EM_HEADERS)
            if r.status_code == 200:
                kl = ((r.json().get("data") or {}).get("klines")) or []
                flows = []
                for line in kl:
                    p = line.split(",")
                    if len(p) >= 2:
                        try:
                            flows.append(float(p[1]))
                        except ValueError:
                            pass
                if flows:
                    val = sum(flows); break
        except Exception:  # noqa: BLE001
            val = None
        if attempt < 1:
            await asyncio.sleep(0.6)
    if val is not None:
        _FLOW_CACHE[code] = (_t.time(), val)
    return val


_FUND_CACHE: dict = {}             # code -> (ts, {revenue_yoy, profit_yoy, roe})
_FUND_TTL = 24 * 3600.0
FUND_FETCH_CAP = 4                 # 每轮最多新拉几只基本面(季报数据，24h 缓存，限量防风控)


async def _fetch_fundamentals(code: str) -> Optional[dict]:
    """三表盈利质量(同比/ROE + 现金流八类型 + 利润含金量 + 毛利率 + 好生意分)。24h 缓存，失败 None。
    给『成长质量』维度——把长线牛股的现金流/好生意章法喂进机器人决策。"""
    import time as _t
    hit = _FUND_CACHE.get(code)
    if hit and (_t.time() - hit[0]) < _FUND_TTL:
        return hit[1]
    try:
        from .financial_statements import fetch_statements
        s = await fetch_statements(code, "A")
    except Exception:  # noqa: BLE001
        s = None
    if s:
        cft = s.get("cashflow_type") or {}
        gb = s.get("good_business") or {}
        f = {"revenue_yoy": s.get("revenue_yoy"), "profit_yoy": s.get("profit_yoy"), "roe": s.get("roe"),
             "cashflow_type": cft.get("type"), "cashflow_score": cft.get("score"), "cashflow_desc": cft.get("desc"),
             "good_business": gb.get("score"), "gross_margin": s.get("gross_margin"), "fcf": s.get("fcf"),
             "eq_score": (s.get("earnings_quality") or {}).get("score")}
        _FUND_CACHE[code] = (_t.time(), f)
        return f
    return None


async def _gather_md(codes: list[str], priority: Optional[list[str]] = None) -> dict[str, dict]:
    """拉东财日线 OHLC + 主力资金流。⚠️东财对突发并发会风控封 IP，故**严格顺序 + 间隔 + 只拉冷缓存 + 限量**；
    6h 缓存命中的直接复用(稳态几乎零请求)。priority(持仓/催化剂股)优先，每轮最多新拉 MD_FETCH_CAP 只。"""
    import time as _t
    out: dict[str, dict] = {}
    # 先吃缓存
    cold: list[str] = []
    for c in codes:
        kl = _KLINE_CACHE.get(_secid(c))
        fl = _FLOW_CACHE.get(c)
        kohlc = kl[1] if (kl and (_t.time() - kl[0]) < _KLINE_TTL) else None
        kflow = fl[1] if (fl and (_t.time() - fl[0]) < _KLINE_TTL) else None
        if kohlc is not None:
            out[c] = {"closes": [k["c"] for k in kohlc], "ohlc": kohlc, "flow5": kflow}
        else:
            cold.append(c)
    # 冷的：按 priority 排序，限量、顺序、间隔拉
    pr = set(priority or [])
    cold.sort(key=lambda c: 0 if c in pr else 1)
    for c in cold[:MD_FETCH_CAP]:
        ohlc = await _fetch_kline_ohlc(_secid(c), points=250)
        await asyncio.sleep(0.5)
        flow5 = await _fetch_flow5(c)
        await asyncio.sleep(0.5)
        out[c] = {"closes": [k["c"] for k in ohlc], "ohlc": ohlc, "flow5": flow5}
    # 基本面(季报盈利质量)：持仓/催化剂股优先，24h 缓存命中直接挂、未命中限量拉(防风控)
    for c in sorted(out.keys(), key=lambda x: 0 if x in pr else 1):
        cached = _FUND_CACHE.get(c)
        if cached and (_t.time() - cached[0]) < _FUND_TTL:
            out[c]["fundamentals"] = cached[1]
    fetched = 0
    for c in sorted(out.keys(), key=lambda x: 0 if x in pr else 1):
        if "fundamentals" in out[c] or fetched >= FUND_FETCH_CAP:
            continue
        f = await _fetch_fundamentals(c)
        fetched += 1
        if f:
            out[c]["fundamentals"] = f
        await asyncio.sleep(0.4)
    return out


def _market_data(codes: list[str], priority: Optional[list[str]] = None) -> dict[str, dict]:
    try:
        return asyncio.run(_gather_md(codes, priority))
    except Exception:  # noqa: BLE001
        return {}


def _techstats(closes: list[float], last: float) -> dict:
    """从日线收盘序列算均线/趋势/前高/波动。closes 不足则尽量给。"""
    out: dict = {"has": False}
    if not closes:
        return out
    px = closes + [last] if (last and (not closes or abs(closes[-1] - last) > 1e-9)) else list(closes)
    n = len(px)
    ma = lambda k: round(sum(px[-k:]) / k, 3) if n >= k else None  # noqa: E731
    out["ma5"], out["ma20"], out["ma60"] = ma(5), ma(20), ma(60)
    win = px[-20:] if n >= 5 else px
    out["high20"] = round(max(win), 3); out["low20"] = round(min(win), 3)
    if n >= 6:
        base = px[-6]
        out["ret5"] = round((last - base) / base * 100, 2) if base else None
    rets = [(px[i] - px[i - 1]) / px[i - 1] for i in range(1, n) if px[i - 1]]
    rets = rets[-20:]
    if len(rets) >= 5:
        mu = sum(rets) / len(rets)
        out["vol"] = round((sum((r - mu) ** 2 for r in rets) / len(rets)) ** 0.5, 4)
    out["has"] = out["ma20"] is not None
    return out


# --------------------------------------------------------------------------- #
# 多维研判（含买点确认）
# --------------------------------------------------------------------------- #

def _analyze(name: str, q: dict, items: list[dict], md: Optional[dict], cfg: AgentConfig = MAIN_CFG,
             learned_mult: Optional[dict] = None, regime: Optional[str] = None) -> dict:
    steps, scores = [], {}
    catalyst = ""; catalyst_ref = None; fresh = False; msg_dir = 0.0
    last = safe_float(q.get("latest")) or 0.0
    tech = _techstats((md or {}).get("closes") or [], last)
    fresh_h = cfg.fresh_hours

    # ① 消息面（主信号）——把催化剂从扁平关键词升级为有类型/强度的事件(第2章 业绩增长关键字)
    catalyst_kind = None
    fresh_items = [it for it in items if float(it.get("age_h", 999.0)) <= fresh_h]
    if fresh_items:
        prof = bull_playbook.catalyst_profile([it.get("title", "") for it in fresh_items])
        msg_dir = prof["net_dir"] if prof.get("n") else _headline_dir(items)
        # 催化类型与溯源必须来自同一条内容：旧逻辑会把“最强标题”的类型贴到“最新标题”上，
        # 例如把「持续涨价」误贴到“港股黄金股低开”，既误导展示也污染决策。
        classified = [(it, bull_playbook.classify_catalyst(it.get("title", ""))) for it in fresh_items]
        classified = [(it, ck_) for it, ck_ in classified if ck_]
        chosen = max(classified,
                     key=lambda pair: (pair[1]["strength"] * (1.0 if pair[1]["dir"] > 0 else 0.9),
                                       -float(pair[0].get("age_h", 999.0)))) if classified else None
        top = chosen[0] if chosen else min(fresh_items, key=lambda x: float(x.get("age_h", 999.0)))
        ta = float(top.get("age_h", 999.0)); fresh = ta <= 24
        ck = chosen[1] if chosen else None
        kind_tag = f"·{ck['label']}" if (ck and ck.get("dir", 0) > 0) else ""
        catalyst = f"【{top.get('src', '资讯')}{kind_tag}】{top.get('title', '')}"
        # 结构化溯源：本站这条催化剂内容（含领先时间 lead_h = 内容比本次决策早多少小时发布 + 催化剂类型 kind）
        catalyst_ref = {"id": top.get("id", ""), "title": top.get("title", ""),
                        "src": top.get("src", "资讯"), "url": top.get("url", ""),
                        "date": top.get("date", ""), "lead_h": round(ta, 1),
                        "kind": ck["label"] if ck else None}
        if ck and ck.get("dir", 0) > 0:
            catalyst_kind = {"key": ck["key"], "label": ck["label"], "strength": ck["strength"], "rule": ck["rule"]}
        age_txt = f"{ta:.0f}h前" if ta >= 1 else "刚刚"
        dtxt = "偏多" if msg_dir > 0.15 else ("偏空" if msg_dir < -0.15 else "中性")
        kinds_txt = ("｜催化:" + "、".join(prof["bull_types"][:3])) if prof.get("bull_types") else ""
        steps.append({"icon": "📰", "label": "消息面",
                      "text": f"本站 {len(fresh_items)} 条相关内容，最新（{age_txt}）{top.get('src', '资讯')}：「{top.get('title', '')[:42]}」，信息面{dtxt}{kinds_txt}。"})
        scores["消息面"] = round(msg_dir, 2)
        sent = _sentiment(items)
        if sent is not None and abs(sent) > 0.15:
            steps.append({"icon": "💬", "label": "情绪", "text": f"站内表态净{('看多' if sent > 0 else '看空')} {abs(sent)*100:.0f}%。"})
            scores["情绪"] = round(max(-1.0, min(1.0, sent)), 2)
    else:
        steps.append({"icon": "📰", "label": "消息面", "text": "本站近期无相关内容，缺催化剂。"})

    # ② 技术面（严谨：均线/趋势/前高 + 当日动量）
    chg = safe_float(q.get("changeRatio"))
    trend_up = False; near_high = False; t_parts = []
    if tech.get("has") and last:
        ma5, ma20, ma60, hi20 = tech["ma5"], tech["ma20"], tech.get("ma60"), tech["high20"]
        trend_up = last >= ma20
        bull_stack = bool(ma5 and ma20 and ma60 and ma5 >= ma20 >= ma60)
        near_high = bool(hi20 and last >= hi20 * 0.985)
        dist = (last - ma20) / ma20 if ma20 else 0.0
        tv = 0.0
        tv += 0.45 if trend_up else -0.45
        tv += 0.2 if bull_stack else 0.0
        tv += 0.25 if near_high else 0.0
        tv += max(-0.2, min(0.2, dist * 2))
        if cfg.contrarian and not trend_up and ma20:
            tv += min(0.25, abs(dist) * 1.5)  # 逆向：跌得越深、离 20 日线越远，反弹空间分越高（超跌买点）
        if chg is not None and chg > 9.3 and not cfg.chase_ok:
            tv -= 0.25  # 涨停不追（激进派不惩罚）
        t_parts.append("站上20日线" if trend_up else "跌破20日线")
        if bull_stack:
            t_parts.append("均线多头排列")
        if near_high:
            t_parts.append("逼近20日新高")
        if tech.get("ret5") is not None:
            t_parts.append(f"近5日 {tech['ret5']:+.1f}%")
        scores["技术面"] = round(max(-1.0, min(1.0, tv)), 2)
        steps.append({"icon": "📈", "label": "技术面", "text": "；".join(t_parts) + f"（现价 {last}，MA20 {ma20}）。"})
    elif chg is not None:
        tv = max(-1.0, min(1.0, chg / 5.0))
        if chg > 9.3 and not cfg.chase_ok:
            tv = -0.15
        scores["技术面"] = round(tv, 2)
        steps.append({"icon": "📈", "label": "技术面", "text": f"日线暂缺，仅当日 {chg:+.2f}%（{'放量上行' if chg > 1 else '横盘' if chg > -1 else '回落'}）。"})

    # ②' 长线趋势模板 + 杯柄/利弗莫尔买点(第6、7章)——日线足够长才计入，宁缺毋滥
    closes_full = (md or {}).get("closes") or []
    tt = bull_playbook.trend_template(closes_full, last) if closes_full else {"has": False}
    cup = {"detected": False}; piv = {"detected": False}
    ohlc_full = (md or {}).get("ohlc") or []
    if ohlc_full:
        try:
            cup = bull_playbook.cup_with_handle(ohlc_full)
            piv = bull_playbook.livermore_pivot(ohlc_full)
        except Exception:  # noqa: BLE001
            cup = {"detected": False}; piv = {"detected": False}
    if tt.get("has"):
        scores["趋势"] = round(tt["score"], 2)
        ftxt = "、".join(tt.get("flags", [])[:3]) or "趋势待确认"
        steps.append({"icon": "📐", "label": "趋势模板",
                      "text": f"长线模板 {tt['passed']}/{tt['total']} 条（{ftxt}），处{_TT_STAGE_CN.get(tt.get('stage'), '—')}。"})
    if cup.get("detected"):
        steps.append({"icon": "🏺", "label": "形态", "text": f"杯柄成型（{cup.get('reason', '')}），右缘买点 {cup.get('buy_point')}。"})
    elif piv.get("detected") and piv.get("buy"):
        steps.append({"icon": "🎯", "label": "形态", "text": f"利弗莫尔关键点：放量启动后缩量回踩至 {piv.get('pivot')}。"})

    # ③ 资金面（主力5日净流入；缺则用换手代理）
    flow5 = (md or {}).get("flow5")
    if flow5 is not None:
        fv = max(-1.0, min(1.0, math.tanh(flow5 / 2.0e8)))
        scores["资金面"] = round(fv, 2)
        steps.append({"icon": "💰", "label": "资金面",
                      "text": f"主力近5日净{'流入' if flow5 >= 0 else '流出'} {abs(flow5)/1e8:.2f} 亿，{'资金做多' if flow5 > 0 else '资金离场'}。"})
    else:
        turn = safe_float(q.get("turnoverRatio"))
        if turn is not None:
            fv = 0.35 if 1.0 <= turn <= 15.0 else (-0.3 if turn > 30 else 0.0)
            scores["资金面"] = round(fv, 2)
            steps.append({"icon": "💰", "label": "资金面", "text": f"换手 {turn:.1f}%，{'量能活跃' if turn >= 2 else '量能温和'}（主力数据暂缺）。"})

    # ④ 基本面：估值
    pe, pb = safe_float(q.get("pe_ttm")), safe_float(q.get("pb"))
    if pe is not None:
        if pe <= 0:
            vv, vtxt = -0.5, "尚未盈利"
        elif pe < 20:
            vv, vtxt = 0.5, f"估值不贵(PE {pe:.0f})"
        elif pe < 45:
            vv, vtxt = 0.15, f"估值中性(PE {pe:.0f})"
        else:
            vv, vtxt = -0.3, f"估值偏高(PE {pe:.0f})"
        if pb and 0 < pb < 1:
            vv += 0.2; vtxt += f"、破净(PB {pb:.2f})"
        scores["基本面"] = round(max(-1.0, min(1.0, vv)), 2)
        steps.append({"icon": "🔍", "label": "基本面", "text": f"{vtxt}。"})

    # ⑤ 成长质量：ROE 生命周期 + 现金流八类型 + 好生意(第4/9/10章)——三表可得时计入(best-effort，缺则不计)
    fund = (md or {}).get("fundamentals") or {}
    if fund.get("roe") is not None or fund.get("profit_yoy") is not None:
        rs = bull_playbook.roe_stage(roe=fund.get("roe"), revenue_yoy=fund.get("revenue_yoy"),
                                     profit_yoy=fund.get("profit_yoy"))
        if rs.get("score") is not None:
            parts = [rs["score"]]                                  # ROE 生命周期分
            if fund.get("cashflow_score") is not None:
                parts.append(fund["cashflow_score"])               # 现金流八类型分(-1~1)
            if fund.get("good_business") is not None:
                parts.append(2 * fund["good_business"] - 1)        # 好生意分 0~1→-1~1
            gq = sum(parts) / len(parts)
            scores["成长质量"] = round(max(-1.0, min(1.0, gq)), 2)
            roe_txt = f"ROE {fund['roe']:.0f}%" if fund.get("roe") is not None else ""
            pf_txt = f"、净利同比 {fund['profit_yoy']:+.0f}%" if fund.get("profit_yoy") is not None else ""
            cf_txt = f"，现金流{fund['cashflow_desc'].split('：')[0]}" if fund.get("cashflow_desc") else ""
            gb_txt = f"，好生意分 {fund['good_business']:.2f}" if fund.get("good_business") is not None else ""
            steps.append({"icon": "🏆", "label": "成长质量",
                          "text": f"{_STAGE_CN.get(rs['stage'], '—')}：{roe_txt}{pf_txt}{cf_txt}{gb_txt}，{rs['note']}。"})

    # 各流派权重 = 基线 × 自学乘子(真·调教：从实盘盈亏微调，learn_weights 持续更新)；缺数据维度自动不计入归一化
    weights = ai_fund_evolve.effective_weights(cfg.weights, learned_mult)
    tw = sum(weights.get(k, 0.0) for k in scores)
    score = sum(scores[k] * weights.get(k, 0.0) for k in scores) / tw if tw else 0.0
    score = max(-1.0, min(1.0, score))
    # 真·不同算法：value(DCF/质量) / reversion(均值回归) 用根本不同的核心打分，催化作确认(磐石/磁极)
    fu = (md or {}).get("fundamentals") or {}
    model_detail: dict = {}
    model_ready = True
    if cfg.model == "value":
        vs = ai_fund_evolve.value_score(
            fcf=fu.get("fcf"), market_cap=safe_float(q.get("totalCapital")), pe=pe,
            roe=fu.get("roe"), gross_margin=fu.get("gross_margin"),
            earnings_quality=fu.get("eq_score"), profit_yoy=fu.get("profit_yoy"),
            cashflow_type=fu.get("cashflow_type"), learned_mult=learned_mult)
        if vs.get("score") is not None:
            scores.update(vs["scores"])          # 估值/质量/成长 → 进 scores，可被学习回路归因
            score = max(-1.0, min(1.0, 0.72 * vs["score"] + 0.28 * msg_dir))  # 内在价值为主、催化为辅
            steps.append({"icon": "💎", "label": "内在价值", "text": "、".join(vs["reasons"][:3]) + "。"})
    elif cfg.model == "reversion":
        rs = ai_fund_evolve.reversion_score((md or {}).get("closes") or [], last, learned_mult)
        model_detail = rs
        if rs.get("score") is not None:
            scores.update(rs["scores"])          # 超跌度/回撤空间/企稳
            score = max(-1.0, min(1.0, 0.66 * rs["score"] + 0.34 * max(0.0, msg_dir)))  # 超跌为主、催化确认
            steps.append({"icon": "🪃", "label": "均值回归", "text": "、".join(rs["reasons"][:3]) + "。"})
    # 买点确认（严谨闸门）：趋势在上 或 强新催化剂 或 书法买点(杯柄突破/利弗莫尔关键点)；不追涨停；资金不出逃
    strong_fresh = fresh and msg_dir > 0.3
    overbought = (chg is not None and chg > 8.5) and not cfg.chase_ok  # 激进派不惧追高
    fund_ok = (flow5 is None) or (flow5 > 0) or strong_fresh
    pattern_buy = bool(cup.get("breakout")) or bool(piv.get("detected") and piv.get("buy"))
    if cfg.contrarian:
        # 逆向：超跌 + 本站催化共振即可反手，不强制站上 20 日线（专抓被错杀）
        trend_ok = (msg_dir > 0.08) and (not trend_up or strong_fresh or pattern_buy)
    elif tech.get("has"):
        trend_ok = trend_up or strong_fresh or pattern_buy
    else:
        trend_ok = strong_fresh or (chg is not None and chg > 0.5)  # 盲态只敢做强催化剂+动量
    # 价值派估值闸门：PE 超上限（或尚未盈利）直接不出手——只在便宜处下手
    valuation_ok = True if cfg.max_pe is None else (pe is not None and 0 < pe <= cfg.max_pe)
    # 多因子/事件派只认“方向明确的正催化”，不能因一条中性甚至偏空新闻恰好很新就放行。
    catalyst_ok = bool(catalyst_kind and catalyst_kind.get("strength", 0) >= 0.45 and msg_dir > 0.08)
    if cfg.style == "event":
        catalyst_ok = catalyst_ok and fresh and msg_dir > 0.22
    # 市场态势自适应：买入门槛随大盘动态升降(趋势派牛市更敢/熊市更挑、价值逆向派熊市更敢捡便宜)
    adapt = ai_fund_evolve.regime_adapt(cfg.style, regime)
    eff_thr = max(0.02, cfg.buy_threshold + adapt["thr_delta"])
    # 出手闸门按模型分化：多因子=数据驱动铁律(必须本站催化+趋势确认)；价值/均值回归靠模型本身选股
    # (便宜+质量 / 超跌+企稳)，不强制本站催化/趋势/资金时点——否则在蓝筹催化池里永远等不到、从不出手
    # (磐石招行 score 0.72、磁极平安 0.39 都够高，却因无本站催化被卡死=空转根因)。
    if cfg.model == "value":
        entry_ok = (score >= eff_thr) and (not overbought) and valuation_ok
    elif cfg.model == "reversion":
        model_ready = bool(model_detail.get("stabilizing")
                           and float(model_detail.get("z", 0.0)) <= -0.5
                           and float(model_detail.get("drawdown", 0.0)) >= 0.05)
        entry_ok = (score >= eff_thr) and (not overbought) and model_ready
    else:
        model_ready = True
        entry_ok = (score >= eff_thr) and (not overbought) and trend_ok and fund_ok and catalyst_ok and valuation_ok

    bp = []
    if cfg.model == "value":
        bp.append("内在价值低估·便宜又有质量")
    elif cfg.model == "reversion":
        bp.append("超跌企稳·逆向反手")
    if catalyst_kind:
        bp.append(f"{catalyst_kind['label']}催化（{catalyst_ref['src']}领先 {catalyst_ref['lead_h']:.0f}h）")
    elif catalyst_ref:
        bp.append(f"{catalyst_ref['src']}点火（本站领先 {catalyst_ref['lead_h']:.0f}h）")
    if cup.get("breakout"):
        bp.append("杯柄突破右缘")
    elif piv.get("detected") and piv.get("buy"):
        bp.append("利弗莫尔关键点")
    if tt.get("has") and tt.get("stage") == "advancing":
        bp.append("长线趋势上升段")
    elif trend_up:
        bp.append("站上20日线确认趋势")
    if near_high:
        bp.append("逼近前高")
    if flow5 and flow5 > 0:
        bp.append(f"主力净流入 {flow5/1e8:.1f}亿")
    buy_point = "、".join(bp) or "多维共振"

    conf = min(1.0, abs(score) * 1.1 + (0.18 if fresh else 0.0) + (0.08 if trend_up else 0.0)
               + (0.08 if (tt.get("has") and tt.get("stage") == "advancing") else 0.0)
               + (0.06 if pattern_buy else 0.0))

    return {"score": round(score, 3), "confidence": round(conf, 2), "scores": scores,
            "thinking": steps, "catalyst": catalyst, "catalyst_ref": catalyst_ref, "fresh": fresh, "tech": tech,
            "entry_ok": entry_ok, "buy_point": buy_point, "msg_dir": round(msg_dir, 2),
            "entry_threshold": round(eff_thr, 3), "catalyst_ok": catalyst_ok, "model_ready": model_ready,
            "trend_up": trend_up, "overbought": overbought, "catalyst_kind": catalyst_kind,
            "trend_template": ({"score": tt.get("score"), "stage": tt.get("stage"),
                                "passed": tt.get("passed"), "total": tt.get("total")} if tt.get("has") else None),
            "pattern": ("cup_handle" if cup.get("detected") else ("livermore" if piv.get("detected") else None)),
            "regime_adapt": adapt}


# --------------------------------------------------------------------------- #
# 战绩 / 心情（灵魂）
# --------------------------------------------------------------------------- #

def _stats(conn, fund_id: str = FUND_ID) -> dict:
    rows = conn.execute("SELECT pnl_pct,ts FROM aif_trade WHERE fund_id=? AND side='sell' AND pnl_pct IS NOT NULL ORDER BY ts ASC",
                        (fund_id,)).fetchall()
    pnls = [float(r["pnl_pct"]) for r in rows]
    closed = len(pnls); wins = sum(1 for p in pnls if p > 0)
    streak = 0
    for p in reversed(pnls):
        if p > 0:
            streak += 1
        else:
            break
    all_rows = conn.execute("SELECT side,symbol,ts FROM aif_trade WHERE fund_id=? ORDER BY ts ASC", (fund_id,)).fetchall()
    recent = pnls[-12:]
    last_sell_by_symbol: dict[str, str] = {}
    same_day_reentries = 0
    for row in all_rows:
        symbol = row["symbol"] or ""; day = _bj_date(row["ts"] or "")
        if row["side"] == "sell":
            last_sell_by_symbol[symbol] = day
        elif row["side"] == "buy" and symbol in last_sell_by_symbol and day and day == last_sell_by_symbol[symbol]:
            same_day_reentries += 1
    return {"closed": closed, "wins": wins, "win_rate": round(wins / closed * 100, 1) if closed else None,
            "best": round(max(pnls), 1) if pnls else None, "worst": round(min(pnls), 1) if pnls else None,
            "win_streak": streak, "avg": round(sum(pnls) / closed, 2) if closed else None,
            "recent_avg": round(sum(recent) / len(recent), 2) if recent else None,
            "same_day_reentries": same_day_reentries}


def _performance_brake(stats: dict) -> dict:
    """按已平仓战绩自动降档：亏损期提高门槛、缩仓、减少换仓；样本不足时不妄调参数。"""
    closed = int(stats.get("closed") or 0)
    if closed < 12:
        return {"key": "normal", "label": "正常", "thr_delta": 0.0, "size_mult": 1.0, "rotate_mult": 1.0}
    win_rate = float(stats.get("win_rate") if stats.get("win_rate") is not None else 100.0)
    avg = float(stats.get("avg") if stats.get("avg") is not None else 0.0)
    if avg <= -0.25 or win_rate < 40.0:
        return {"key": "defensive", "label": "防守", "thr_delta": 0.10, "size_mult": 0.55, "rotate_mult": 1.8}
    recent_avg = stats.get("recent_avg")
    same_day_reentries = int(stats.get("same_day_reentries") or 0)
    if avg < 0.0 or win_rate < 45.0 or same_day_reentries >= 3 or (recent_avg is not None and float(recent_avg) <= -0.1):
        return {"key": "cautious", "label": "谨慎", "thr_delta": 0.05, "size_mult": 0.75, "rotate_mult": 1.35}
    return {"key": "normal", "label": "正常", "thr_delta": 0.0, "size_mult": 1.0, "rotate_mult": 1.0}


def _retrospective(conn, fund_id: str, stats: dict, brake: dict, cfg: AgentConfig) -> dict:
    """把交易结果翻译成可读、可持续迭代的『脑内复盘』。

    只读成交表，不引入新表或额外行情请求；同一份结论同时服务快照、竞技场和分享卡。
    重点盯三件事：样本是否足够、近期盈亏是否变差、卖出后是否过快重返同一只票。
    """
    rows = conn.execute(
        "SELECT side,symbol,name,pnl_pct,ts FROM aif_trade WHERE fund_id=? ORDER BY ts ASC",
        (fund_id,),
    ).fetchall()
    sells = [r for r in rows if r["side"] == "sell" and r["pnl_pct"] is not None]
    recent = sells[-12:]
    recent_pnls = [float(r["pnl_pct"]) for r in recent]
    recent_wins = sum(1 for p in recent_pnls if p > 0)
    recent_win_rate = round(recent_wins / len(recent_pnls) * 100, 1) if recent_pnls else None
    recent_avg = round(sum(recent_pnls) / len(recent_pnls), 2) if recent_pnls else None

    # 同股卖出后在同一北京日再次买入，是最容易制造“节目效果”却伤害纪律的行为。
    last_sell_by_symbol: dict[str, str] = {}
    same_day_reentries = 0
    for row in rows:
        symbol = row["symbol"] or ""
        day = _bj_date(row["ts"] or "")
        if row["side"] == "sell":
            last_sell_by_symbol[symbol] = day
        elif row["side"] == "buy" and symbol in last_sell_by_symbol and day and day == last_sell_by_symbol[symbol]:
            same_day_reentries += 1

    closed = int(stats.get("closed") or 0)
    mode = brake.get("key", "normal")
    if closed < 12:
        headline = "样本积累中：先把每一笔理由记清楚，再放大仓位。"
        takeaway = "目前样本还不足以证明策略稳定，保持小步验证。"
    elif mode == "defensive":
        headline = "防守档不是认输：先把错误变贵，再等胜率修复。"
        takeaway = "近期性价比偏弱，已自动提高门槛并压缩仓位。"
    elif mode == "cautious":
        headline = "谨慎档复盘：少出手，等更硬的证据。"
        takeaway = "近期期望偏弱，先减少无效换手，等待信号质量回升。"
    else:
        headline = "正常档复盘：催化剂与趋势双确认，继续让利润奔跑。"
        takeaway = "当前战绩没有触发自动降档，继续执行原有买入与止损纪律。"

    good = []
    bad = []
    if recent_win_rate is not None:
        good.append(f"最近 {len(recent_pnls)} 笔胜率 {recent_win_rate:.1f}%")
    if int(stats.get("win_streak") or 0) >= 3:
        good.append(f"当前连胜 {int(stats['win_streak'])} 笔，执行力在线")
    if int(stats.get("wins") or 0) and (stats.get("avg") or 0) >= 0:
        good.append("累计单笔期望为正，暂不主动加速")
    if same_day_reentries:
        bad.append(f"发现 {same_day_reentries} 次同日回补，冷静期必须拦住冲动")
    if recent_avg is not None and recent_avg < 0:
        bad.append(f"最近单笔期望 {recent_avg:+.2f}%，先收紧而不是追单")
    if stats.get("worst") is not None and float(stats["worst"]) <= -8:
        bad.append(f"历史最差单笔 {float(stats['worst']):+.1f}%，硬止损仍是底线")
    if not good:
        good.append("已记录每笔买卖理由，等待更多平仓样本")
    if not bad:
        bad.append("暂未发现新的纪律性异常")

    next_rule = "保持本站催化剂 + 趋势确认；平仓后至少冷静 {} 天，单日最多新开 {} 笔。".format(
        cfg.reentry_cooldown_days, cfg.max_daily_buys
    )
    return {
        "headline": headline,
        "brief": takeaway,
        "mode": mode,
        "mode_label": brake.get("label", "正常"),
        "sample": closed,
        "recent_sample": len(recent_pnls),
        "recent_win_rate": recent_win_rate,
        "recent_avg": recent_avg,
        "same_day_reentries": same_day_reentries,
        "good": good[:3],
        "bad": bad[:3],
        "next_rule": next_rule,
    }


def _mood(stats: dict, nav_pct: float) -> dict:
    if stats.get("win_streak", 0) >= 3 or nav_pct >= 3:
        return {"key": "hot", "emoji": "🔥", "label": "手感火热", "tone": "自信、带点小得意，但不飘"}
    if nav_pct <= -3 or (stats.get("closed", 0) >= 3 and (stats.get("win_rate") or 100) < 35):
        return {"key": "defensive", "emoji": "😤", "label": "收敛防守", "tone": "谨慎、不服气、求稳反击"}
    return {"key": "calm", "emoji": "😎", "label": "稳中找机会", "tone": "淡定、老练、伺机而动"}


# --------------------------------------------------------------------------- #
# LLM 操盘解说（人设 + 心情）
# --------------------------------------------------------------------------- #

def _template_narrative(side, name, an, pnl):
    head = {"buy": "建仓", "sell": "了结"}.get(side, "操作")
    bits = "；".join(s["text"] for s in an["thinking"][:2])
    tail = f"，本笔{('赚' if (pnl or 0) >= 0 else '亏')} {abs(pnl):.1f}%" if pnl is not None else ""
    return f"{head}{name}{tail}。{bits}"


def _llm_narratives(decisions: list[dict], mood: dict, cfg: AgentConfig = MAIN_CFG) -> dict[str, str]:
    if not TONE or not decisions:
        return {}
    lines = []
    for d in decisions:
        an = d["an"]; t = an.get("tech", {})
        facts = []
        if an.get("catalyst"):
            facts.append("催化剂:" + an["catalyst"])
        if an.get("buy_point"):
            facts.append("买点:" + an["buy_point"])
        if t.get("ma20"):
            facts.append(f"现价{d['price']}/MA20={t['ma20']}")
        pnl = f"，本笔{d['pnl']:+.1f}%" if d.get("pnl") is not None else ""
        facts.append("依据:" + " | ".join(s["text"] for s in an["thinking"]))
        lines.append(f"[{d['tid']}] {('买入' if d['side']=='buy' else '卖出')}{d['name']}({d['symbol']}){pnl}\n  " + "；".join(facts))
    prompt = (
        f"你是「{cfg.name}」——DeepFocus 终端里一个 7×24 直播操盘的 A股交易智能体(虚拟资金)，"
        f"打法是『{cfg.blurb}』，性格鲜明、有脾气、爱用盘口黑话和金句。你现在的心情是「{mood['emoji']} {mood['label']}」({mood['tone']})。\n"
        "下面是你本轮刚做的交易及严谨依据。给每笔写一句【第一人称、口语、有锐度、像直播间主播】的操盘解说：\n"
        "· 必须把判断讲明白(技术面/资金面/催化剂至少点一项)，能点出本站快讯/研报当催化剂的务必点；\n"
        "· 让心情自然流露(火热时带劲、防守时谨慎)，可有金句，但别浮夸、别喊『推荐你买』、别编依据里没有的数字；\n"
        "· 每句≤45字。只输出 JSON object：key 是方括号里的 id，value 是解说句。\n\n"
        + "\n".join(lines)
    )
    try:
        from .compliance import neutralize_text
        from .llm import CloudResearchLLM
        data = asyncio.run(CloudResearchLLM().complete_json(prompt, max_tokens=1000, timeout_seconds=30))
        return {k: neutralize_text(str(v)[:90]) for k, v in data.items() if isinstance(v, str)} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _commentary(decisions: list[dict], narr: dict, mood: dict, pos_n: int, nav_pct: float, stats: dict,
                cfg: AgentConfig = MAIN_CFG) -> str:
    if decisions:
        for d in decisions:
            if narr.get(d["tid"]):
                return narr[d["tid"]]
    wr = f"，胜率 {stats['win_rate']:.0f}%" if stats.get("win_rate") is not None else ""
    hunt, tail = _MUSE_FLAVOR.get(cfg.style, _MUSE_FLAVOR["balanced"])   # 直播一句话也按流派口吻
    if pos_n:
        return f"{cfg.emoji} 持仓 {pos_n} 只、累计 {nav_pct:+.2f}%{wr}，{tail}。"
    return f"{cfg.emoji} 暂时空仓，{hunt}。"


# --------------------------------------------------------------------------- #
# 长期记忆（像人一样持续记忆·学习·进化）：交易复盘教训(lesson) + 个股持续观点(thesis)
#   · 事实由确定性数据沉淀(绝不让 LLM 臆造数字)；LLM 只负责在独白里把记忆讲得有灵魂
#   · 可召回(思考时想起往事)、会衰减(久不提及就淡忘)、会强化(反复印证则更笃定)
# --------------------------------------------------------------------------- #

MEM_KEEP = 220                       # 记忆上限(超出删最弱的)
MEM_DECAY = 0.96                     # 每日不被提及的记忆权重衰减
MEM_WIN_PCT = 5.0                    # 平仓收益≥此值 → 沉淀"成功模式"教训
MEM_LOSS_PCT = -4.0                  # 平仓亏损≤此值 → 沉淀"踩坑"教训


def _record_trade_memory(conn, symbol: str, name: str, pnl_pct: float, sell_reason: str, fund_id: str = FUND_ID) -> None:
    """平仓即复盘：把"什么催化剂 + 持有多久 + 结果"沉淀成一条可召回的教训。绝不抛出。"""
    try:
        if pnl_pct is None or (MEM_LOSS_PCT < pnl_pct < MEM_WIN_PCT):
            return
        win = pnl_pct >= MEM_WIN_PCT
        # 回看这只票最近一笔买入的催化剂(它当初为什么被买)
        b = conn.execute("SELECT ts, catalyst, catalyst_ref FROM aif_trade WHERE fund_id=? AND symbol=? AND side='buy' ORDER BY ts DESC LIMIT 1",
                         (fund_id, symbol)).fetchone()
        cat = (b["catalyst"] if b else "") or ""
        cref = _loadj(b["catalyst_ref"], None) if (b and "catalyst_ref" in b.keys()) else None
        src = (cref or {}).get("src") or ("快讯" if "快讯" in cat else "研报" if "研报" in cat else "文章" if "文章" in cat else "")
        hold_days = None
        if b and b["ts"]:
            try:
                hold_days = round((datetime.now(timezone.utc) - datetime.fromisoformat(b["ts"].replace("Z", "+00:00"))).total_seconds() / 86400, 1)
            except Exception:  # noqa: BLE001
                hold_days = None
        catshort = cat.split("】")[-1][:18] if cat else "信号"
        if win:
            title = f"{name}：靠{src or '本站'}催化「{catshort}」赚 +{pnl_pct:.0f}%" + (f"(持{hold_days}天)" if hold_days else "")
            conf = min(0.95, 0.55 + abs(pnl_pct) / 40.0)
        else:
            why = "追高被套" if "止损" in (sell_reason or "") else "信号转弱"
            title = f"{name}：「{catshort}」{why}亏 {pnl_pct:.0f}%" + (f"(持{hold_days}天)" if hold_days else "")
            conf = min(0.9, 0.5 + abs(pnl_pct) / 30.0)
        detail = {"catalyst": cat, "src": src, "pnl_pct": round(pnl_pct, 1), "hold_days": hold_days,
                  "reason": sell_reason, "catalyst_ref": cref}
        now = utc_now_iso()
        conn.execute("INSERT INTO aif_memory (id,fund_id,symbol,name,mem_type,ts,updated_at,title,detail,confidence,weight,pnl_impact,src,seen_count)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,1)"
                     " ON CONFLICT(fund_id,symbol,mem_type,title) DO UPDATE SET updated_at=excluded.updated_at,"
                     " weight=MIN(1.0, aif_memory.weight+0.15), seen_count=aif_memory.seen_count+1",
                     (uuid.uuid4().hex, fund_id, symbol, name, "trade_win" if win else "trade_loss", now, now,
                      title, json.dumps(detail, ensure_ascii=False), round(conf, 2), round(conf, 2), round(pnl_pct, 1), src))
    except Exception:  # noqa: BLE001
        pass


# 各流派认知口吻：同一只票，不同流派用各自的关注点 + 判断造句 → 5 份认知读着像 5 个人，而非都像阿尔法。
# 三档对应 score：看多(>0.12) / 中性(±0.12) / 回避(<-0.12)，{src}=本站内容条数描述。
_THESIS_VOICE: dict[str, tuple[str, str, str]] = {
    "balanced":   ("本站{src}在追，我看多、等买点共振", "本站{src}在追，我中性观察、等买点", "本站{src}在追，我回避"),
    "aggressive": ("本站{src}点火、催化够猛——盯突破、敢重锤", "本站{src}在追、势没起——盯着，一突破就上", "本站{src}虽在追但没冲劲——不碰、等放量"),
    "value":      ("本站{src}点名、估值质量过关——便宜，值得拿", "本站{src}在追——先看估值与盈利质量再说", "本站{src}在追但太贵/质量存疑——避、等合理价"),
    "event":      ("本站{src}刚响、催化新鲜——扑进去、快进快出", "本站{src}在追——等下一条快讯点火", "本站{src}已发酵过——过了、不追冷催化"),
    "contrarian": ("本站{src}+超跌共振——被错杀了、反手抄", "本站{src}在追——盯着跌够没、被错杀没", "本站{src}在追但还没跌透——等更超跌再反手"),
}


def _thesis_title(style: str, name: str, src_txt: str, score: float) -> str:
    bull, neutral, avoid = _THESIS_VOICE.get(style, _THESIS_VOICE["balanced"])
    frame = bull if score > 0.12 else (avoid if score < -0.12 else neutral)
    return f"{name}：{frame.format(src=src_txt)}"


def _upsert_thesis(conn, symbol: str, name: str, items: list[dict], score: float,
                   fund_id: str = FUND_ID, style: str = "balanced") -> None:
    """对一只「本站持续点名」的票形成/强化一条观点(thesis)——这就是每个智能体逐步演化的认知。绝不抛出。
    items=该股近期本站内容(快讯/文章/研报)；style=流派口吻，让认知按打法差异化措辞。
    反复出现→权重/信心累加(笃定)；久未更新→随每日衰减淡忘。"""
    try:
        # 只用面向用户的 快讯/文章/研报 形成观点(排除内部「信号」dao-signal，与展示内容口径一致)
        fresh = [it for it in items if float(it.get("age_h", 999.0)) <= FRESH_HOURS and it.get("src") in ("快讯", "文章", "研报")]
        if len(fresh) < 1:
            return
        by_src = {}
        for it in fresh:
            by_src[it.get("src", "资讯")] = by_src.get(it.get("src", "资讯"), 0) + 1
        src_txt = "、".join(f"{k}{v}条" for k, v in sorted(by_src.items(), key=lambda x: -x[1])[:3])
        latest_h = min((float(it.get("age_h", 999.0)) for it in fresh), default=999.0)
        lean = "看多" if score > 0.12 else ("回避" if score < -0.12 else "中性观察")
        title = _thesis_title(style, name, src_txt, score)
        detail = {"n": len(fresh), "src_breakdown": by_src, "latest_h": round(latest_h, 1),
                  "score": round(score, 2), "lean": lean, "style": style,
                  "top_titles": [it.get("title", "")[:30] for it in fresh[:3]]}
        # 信心随催化剂条数与新鲜度上升
        conf = max(0.25, min(0.92, 0.3 + 0.08 * len(fresh) + (0.2 if latest_h <= 24 else 0.0) + abs(score) * 0.25))
        now = utc_now_iso()
        conn.execute("INSERT INTO aif_memory (id,fund_id,symbol,name,mem_type,ts,updated_at,title,detail,confidence,weight,pnl_impact,src,seen_count)"
                     " VALUES (?,?,?,?, 'thesis', ?,?,?,?,?,?,NULL,?,1)"
                     " ON CONFLICT(fund_id,symbol,mem_type,title) DO UPDATE SET updated_at=excluded.updated_at,"
                     " detail=excluded.detail, confidence=excluded.confidence,"
                     " weight=MIN(1.0, aif_memory.weight+0.06), seen_count=aif_memory.seen_count+1",
                     (uuid.uuid4().hex, fund_id, symbol, name, now, now, title,
                      json.dumps(detail, ensure_ascii=False), round(conf, 2), round(conf, 2),
                      (sorted(by_src.items(), key=lambda x: -x[1])[0][0] if by_src else "")))
        # 同股旧 thesis(标题不同、观点已变)降权，避免自相矛盾堆积
        conn.execute("UPDATE aif_memory SET weight=weight*0.6 WHERE fund_id=? AND symbol=? AND mem_type='thesis' AND title<>? ",
                     (fund_id, symbol, title))
    except Exception:  # noqa: BLE001
        pass


# A股交易铁律——写进每个机器人的永久记忆(mem_type='rule')，认知面板可见、不衰减；行为上由 _in_session 硬闸门保证。
TRADING_RULES = ("A股交易铁律：① 只在交易日(周一~五、非法定节假日)的 09:30–11:30、13:00–15:00 撮合下单，"
                 "午休/盘后/夜间/周末/节假日一律不交易；② T+1——当日买入的当日不可卖出(含止损)，必须持到下一交易日；"
                 "③ 100股一手、不追涨停、严格止损；④ 平仓后执行回补冷静期，单日开仓设上限，亏损期自动提门槛、缩仓位。")


def _seed_trading_rules(conn, fund_id: str) -> None:
    """把 A股交易规则写进该角色记忆(mem_type='rule')——永久满权重、不衰减、不删、认知面板可见。只插一次。绝不抛出。"""
    try:
        now = utc_now_iso()
        conn.execute(
            "INSERT INTO aif_memory (id,fund_id,symbol,name,mem_type,ts,updated_at,title,detail,confidence,weight,pnl_impact,src,seen_count)"
            " VALUES (?,?,?,?, 'rule', ?,?,?,?,1.0,1.0,NULL,'规则',1)"
            " ON CONFLICT(fund_id,symbol,mem_type,title) DO UPDATE SET detail=excluded.detail",
            (uuid.uuid4().hex, fund_id, "", "交易规则", now, now, "A股交易铁律·只交易时段·T+1不当日卖",
             json.dumps({"rule": TRADING_RULES, "session": "周一~五 09:30–11:30 / 13:00–15:00",
                         "T+1": "当日买入当日不可卖，持到下一交易日",
                         "holiday": "法定节假日休市(CN_MARKET_HOLIDAYS)",
                         "discipline": "平仓后冷静期、单日开仓上限、亏损期自动降档"}, ensure_ascii=False)))
    except Exception:  # noqa: BLE001
        pass


def _decay_memory(conn, fund_id: str = FUND_ID) -> None:
    """每日一次：未被提及的记忆权重衰减(淡忘)、清理极弱与超量。规则(rule)永不衰减/删除。绝不抛出。"""
    try:
        st = _state(conn, fund_id)
        today = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
        last = st["mem_decay_date"] if (st and "mem_decay_date" in st.keys()) else None
        if last == today:
            return
        conn.execute("UPDATE aif_memory SET weight=MAX(0.02, weight*?) WHERE fund_id=? AND mem_type<>'rule'", (MEM_DECAY, fund_id))
        conn.execute("DELETE FROM aif_memory WHERE fund_id=? AND weight<=0.04 AND mem_type<>'rule'", (fund_id,))
        n = conn.execute("SELECT COUNT(*) AS n FROM aif_memory WHERE fund_id=? AND mem_type<>'rule'", (fund_id,)).fetchone()["n"]
        if n > MEM_KEEP:
            conn.execute("DELETE FROM aif_memory WHERE id IN (SELECT id FROM aif_memory WHERE fund_id=? AND mem_type<>'rule' ORDER BY weight ASC LIMIT ?)",
                         (fund_id, n - MEM_KEEP))
        conn.execute("UPDATE aif_state SET mem_decay_date=? WHERE fund_id=?", (today, fund_id))
    except Exception:  # noqa: BLE001
        pass


def _recall_memories(symbols: Optional[list[str]] = None, limit: int = 4, fund_id: str = FUND_ID) -> list[dict]:
    """召回相关记忆：持仓/关注股优先，否则取最笃定的若干条(confidence×weight)。轻量、无重框架。失败→[]。"""
    try:
        with _connect() as conn:
            rows = []
            seen = set()
            if symbols:
                qs = ",".join("?" * len(symbols))
                rows = conn.execute(
                    f"SELECT * FROM aif_memory WHERE fund_id=? AND mem_type<>'rule' AND symbol IN ({qs}) ORDER BY (confidence*weight) DESC, updated_at DESC LIMIT ?",
                    (fund_id, *symbols, limit)).fetchall()
                seen = {r["id"] for r in rows}
            if len(rows) < limit:
                extra = conn.execute(
                    "SELECT * FROM aif_memory WHERE fund_id=? AND mem_type<>'rule' ORDER BY (confidence*weight) DESC, updated_at DESC LIMIT ?",
                    (fund_id, limit * 2)).fetchall()
                for r in extra:
                    if r["id"] not in seen:
                        rows.append(r); seen.add(r["id"])
                    if len(rows) >= limit:
                        break
            return [{"id": r["id"], "symbol": r["symbol"], "name": r["name"], "mem_type": r["mem_type"],
                     "title": r["title"], "confidence": r["confidence"], "weight": r["weight"],
                     "pnl_impact": r["pnl_impact"], "src": r["src"], "ts": r["ts"], "updated_at": r["updated_at"],
                     "seen_count": r["seen_count"] if "seen_count" in r.keys() else 1} for r in rows[:limit]]
    except Exception:  # noqa: BLE001
        return []


# --------------------------------------------------------------------------- #
# 脑内独白（7×24「沉淀思考」：收盘/周末也在复盘、预研、读研报，让面板永远有灵魂在动）
# --------------------------------------------------------------------------- #

_MUSE_LEN = {"preopen": 110, "morning": 110, "afternoon": 110, "noon": 165, "postclose": 200, "evening": 170, "weekend": 175}
_AMMO_CACHE: dict = {}


def _diverse_ammo(per_type: int = 3) -> list[dict]:
    """把本站内容按 快讯/文章/研报 分桶各取最新 per_type 条再交织——保证独白/观点真用上**文章与研报**而非只快讯。缓存45s。"""
    import time as _t
    hit = _AMMO_CACHE.get("a")
    if hit and (_t.time() - hit[0]) < 45.0:
        return hit[1]
    buckets: dict[str, list] = {"研报": [], "文章": [], "快讯": []}
    try:
        from .realtime_messages import list_realtime_messages
        for m in list_realtime_messages(limit=120):
            st = getattr(m, "source_type", "") or ""
            src = _SRC_LABEL.get(st, "资讯")
            if src not in buckets:
                continue
            if len(buckets[src]) < per_type and (getattr(m, "title", "") or ""):
                buckets[src].append({"title": getattr(m, "title", "") or "", "src": src, "url": getattr(m, "url", "") or ""})
            if all(len(v) >= per_type for v in buckets.values()):
                break
    except Exception:  # noqa: BLE001
        pass
    out: list[dict] = []  # 交织：研报/文章/快讯轮流，确保前几条不被快讯独占
    for i in range(per_type):
        for s in ("研报", "文章", "快讯"):
            if i < len(buckets[s]):
                out.append(buckets[s][i])
    _AMMO_CACHE["a"] = (_t.time(), out)
    return out


def _mem_icon(t: str) -> str:
    return {"trade_win": "✅", "trade_loss": "❌", "thesis": "📌"}.get(t, "•")


def _recall_line(recalled: list) -> str:
    if not recalled:
        return ""
    return "我记得（过往复盘/观点）：\n" + "\n".join(
        f"  {_mem_icon(m['mem_type'])} {m['title']}（信度 {int((m.get('confidence') or 0)*100)}%）" for m in recalled[:4])


# 每个流派的『直播口头禅』：(找买点的说法, 收尾态度) —— 让每个角色的脑内独白读着像自己、而非都像阿尔法
_MUSE_FLAVOR = {
    "balanced":   ("等一个站上20日线的买点再动手", "买点没到不乱开枪"),
    "aggressive": ("等一个够猛的突破就重锤", "强催化就追、不手软"),
    "value":      ("等便宜的好货、不便宜不动手", "先看估值和现金流，贵了不碰"),
    "event":      ("本站快讯一响我就扑", "快进快出、吃完催化就走"),
    "contrarian": ("等超跌+本站催化共振再反手抄", "别人恐惧我贪婪、专挑被错杀的"),
}


def _template_musing(cfg: AgentConfig, phase_key: str, mood: dict, nav_pct: float, stats: dict,
                     holds: list, wire: list, recalled: Optional[list] = None) -> str:
    em = cfg.emoji                                    # 用角色自己的 emoji，一眼分得清是谁
    hunt, tail = _MUSE_FLAVOR.get(cfg.style, _MUSE_FLAVOR["balanced"])
    hold_txt = "、".join(holds[:3]) if holds else ""
    pos_line = f"手里还攥着 {hold_txt}" if hold_txt else "目前空仓"
    by = {"研报": None, "文章": None, "快讯": None}
    for w in wire:
        if w.get("title") and by.get(w.get("src")) is None and w.get("src") in by:
            by[w["src"]] = w
    parts = [f"{s}「{by[s]['title'][:20]}」" for s in ("研报", "文章", "快讯") if by[s]]
    ref = "、".join(parts[:2]) if parts else "本站快讯/文章/研报"
    rep = by["研报"]; art = by["文章"]
    mem = (recalled or [None])[0]
    mem_txt = f"我还记着上次「{mem['title'][:22]}」，" if mem else ""
    if phase_key == "preopen":
        return f"{em} 还有十几分钟开盘。隔夜我把本站快讯文章研报全过了一遍，{ref}最值得盯。{mem_txt}{pos_line}，{hunt}。"
    if phase_key == "noon":
        return f"{em} 午休复盘。上午{pos_line}，{ref}还在发酵" + (f"，{art['title'][:16]}那篇文章把逻辑讲透了" if art else "") + f"。{mem_txt}下午{tail}。"
    if phase_key == "postclose":
        wr = f"，胜率 {stats['win_rate']:.0f}%" if stats.get("win_rate") is not None else ""
        return f"{em} 收盘了，今天累计 {nav_pct:+.2f}%{wr}。复盘一遍：{ref}是今天主线" + (f"，研报那条值得反复琢磨" if rep else "") + f"。{mem_txt}{pos_line}，明天接着跟、{tail}。"
    if phase_key == "weekend":
        return f"{em} 周末不开盘，功课不停。本周本站快讯文章研报我又翻了一遍，{ref}埋了伏笔" + (f"，尤其那篇研报给了我新视角" if rep else "") + f"。{mem_txt}{pos_line}，下周{hunt}。"
    if phase_key in ("morning", "afternoon"):
        return f"{em} 盯着盘和你们的快讯文章研报找下一击。{ref}是我现在最在意的线索。{mem_txt}{pos_line}，{tail}。"
    return f"{em} 夜深了，我把今天本站快讯文章研报又读了一遍，{ref}先记小本本上" + (f"，研报这条得消化消化" if rep else "") + f"。{mem_txt}{pos_line}，养精蓄锐，{hunt}。"


def _trim_to_sentence(text: str, limit: int) -> str:
    """把直播独白截到 ≤limit 字、且落在完整句尾(。！？…)——绝不断在半句。
    无合适句尾(靠太前会砍掉太多)时以省略号软收尾，不硬切到一半。"""
    import re
    t = (text or "").strip()
    if len(t) <= limit:
        return t
    cut = t[:limit]
    ends = list(re.finditer(r"[。！？!?…]+", cut))
    if ends and ends[-1].end() >= int(limit * 0.55):
        return cut[:ends[-1].end()]
    return cut.rstrip("，、；：,;:　 ") + "…"


def _llm_musing(phase_key: str, phase_label: str, mood: dict, nav_pct: float, stats: dict, holds: list,
                wire: list, recalled: Optional[list] = None, cfg: AgentConfig = MAIN_CFG) -> Optional[str]:
    """让 LLM 用人设写一段『脑内独白』：注入**召回的记忆**(过往复盘/观点)+ 本站快讯/文章/研报，体现持续学习进化。
    没弹药或失败回退模板。绝不臆造数字。"""
    if not TONE:
        return None
    ammo = "\n".join(f"  · [{w['src']}] {w['title'][:42]}" for w in wire[:7] if w.get("title"))
    if not ammo:
        return None
    hold_txt = "、".join(holds[:6]) if holds else "空仓"
    wr = f"{stats['win_rate']:.0f}%" if stats.get("win_rate") is not None else "—"
    n = _MUSE_LEN.get(phase_key, 100)
    hint = {
        "weekend": "现在周末休市，你在做研究功课、读本站研报文章、为下周布局",
        "evening": "现在夜盘休市，你在盘后夜读、消化今天本站的快讯文章研报",
        "preopen": "马上开盘，你在做盘前最后功课、锁定今天要盯的票",
        "noon": "午间休市，你在做上午复盘、规划下午",
        "postclose": "刚收盘，你在盘后复盘、总结今天得失",
        "morning": "早盘盯盘中，你在等买点",
        "afternoon": "午后盯盘中，你在管理持仓、找机会",
    }.get(phase_key, "你在盯盘")
    mem_block = _recall_line(recalled)
    prompt = (
        f"你是「{cfg.name}」——DeepFocus 终端 7×24 直播的 A股交易智能体(虚拟资金)，打法是『{cfg.blurb}』，"
        f"人设鲜明、有脾气、爱用盘口黑话和金句，是个**有持续记忆、会从过往复盘里学习、观点不断进化**的老手。"
        f"当前心情「{mood['emoji']}{mood['label']}」({mood['tone']})。{hint}。\n"
        f"你的状态：累计收益 {nav_pct:+.2f}%、胜率 {wr}、持仓：{hold_txt}。\n"
        + (f"\n{mem_block}\n" if mem_block else "")
        + f"\nDeepFocus 本站最新情报(你做判断的弹药库，**含快讯/文章/研报三类**)：\n{ammo}\n\n"
        f"写一段【第一人称、口语、有灵魂、像直播间老主播碎碎念】的脑内独白({max(60, n-40)}~{n}字)，体现你此刻在『沉淀思考、持续进化』：\n"
        "· 优先把**研报/文章**这类有深度的内容讲出门道(不要只念快讯标题)；\n"
        "· 若上面『我记得』里有相关的复盘教训或观点，自然地『想起来』并说说这次是印证了还是要修正——体现你在学习成长；\n"
        "· 让心情和盘感流露，可有金句；别喊单、别说『推荐买』、别编上面没有的数字；\n"
        f"· ⚠️必须在 {n} 字内把话**说完整、以完整句子收尾(。！？结尾)**，绝不半句戛然而止；宁可少说一点也要收完。\n"
        "· 只输出 JSON：{\"musing\":\"……\"}。"
    )
    try:
        from .llm import CloudResearchLLM
        data = asyncio.run(CloudResearchLLM().complete_json(prompt, max_tokens=900, timeout_seconds=30))
        if isinstance(data, dict):
            v = data.get("musing") or next((x for x in data.values() if isinstance(x, str)), None)
            if v and isinstance(v, str):
                return _trim_to_sentence(v.strip().strip('"').strip(), n + 50)
    except Exception:  # noqa: BLE001
        pass
    return None


def _deposit_musing(nav: float, cfg: AgentConfig = MAIN_CFG) -> None:
    """7×24 有灵魂地『沉淀思考』：召回过往记忆 + 本站快讯/文章/研报 → 写一段脑内独白(更长更话痨)，
    节流入库(同时段≥75min 才再发、换时段即可发)，独立保留 60 条，并记下本条召回了哪些记忆。绝不抛出。
    记忆衰减对所有智能体都跑；LLM 独白成本高，仅 cfg.muse 的主账户产出。"""
    try:
        fund_id = cfg.fund_id
        phase_key, phase_label = _phase()
        with _connect() as conn:
            _decay_memory(conn, fund_id); conn.commit()  # 每日衰减(节流在函数内)，所有 agent 都跑
        # 所有角色都产『脑内独白』直播流；LLM 润色仅 cfg.muse 主账户(_llm_musing 内部 gate)，其余走模板(零 LLM 成本)
        with _connect() as conn:
            last = conn.execute("SELECT ts,catalyst FROM aif_thought WHERE fund_id=? AND action='musing' ORDER BY ts DESC LIMIT 1",
                                (fund_id,)).fetchone()
            if last:
                try:
                    gap = (datetime.now(timezone.utc) - datetime.fromisoformat((last["ts"] or "").replace("Z", "+00:00"))).total_seconds() / 60.0
                except Exception:  # noqa: BLE001
                    gap = 999.0
                if gap < MUSING_MIN_GAP_MIN and (last["catalyst"] or "") == phase_key:
                    return
            stats = _stats(conn, fund_id)
            pos = _positions(conn, fund_id)
            holds = [r["name"] or r["symbol"] for r in pos]
            held_syms = [r["symbol"] for r in pos]
        nav_pct = (nav - cfg.initial_capital) / cfg.initial_capital * 100 if cfg.initial_capital else 0.0
        mood = _mood(stats, nav_pct)
        ammo = _diverse_ammo()                       # 快讯/文章/研报三类交织
        recalled = _recall_memories(held_syms or None, limit=4, fund_id=fund_id)  # 召回相关记忆
        refs = [{"title": w["title"], "src": w["src"], "url": w.get("url", "")} for w in ammo[:3] if w.get("title")]
        rec_refs = [{"id": m["id"], "title": m["title"], "mem_type": m["mem_type"], "confidence": m.get("confidence")} for m in recalled]
        # LLM 独白仅主账户(或 MUSE_ALL 开)；其余角色用流派模板(零 LLM 成本，仍按各自口吻)
        text = (_llm_musing(phase_key, phase_label, mood, nav_pct, stats, holds, ammo, recalled, cfg=cfg)
                if (cfg.muse or MUSE_ALL) else None) \
            or _template_musing(cfg, phase_key, mood, nav_pct, stats, holds, ammo, recalled)
        with _connect() as conn:
            conn.execute("INSERT INTO aif_thought (id,fund_id,ts,symbol,name,action,catalyst,thinking,narrative,confidence,scores,recalled_refs)"
                         " VALUES (?,?,?,?,?,'musing',?,?,?,?,?,?)",
                         (uuid.uuid4().hex, fund_id, utc_now_iso(), "", phase_label, phase_key,
                          json.dumps(refs, ensure_ascii=False), text, None,
                          json.dumps({"nav_pct": round(nav_pct, 2)}, ensure_ascii=False),
                          json.dumps(rec_refs, ensure_ascii=False)))
            conn.execute("DELETE FROM aif_thought WHERE fund_id=? AND action='musing' AND id NOT IN "
                         "(SELECT id FROM aif_thought WHERE fund_id=? AND action='musing' ORDER BY ts DESC LIMIT ?)",
                         (fund_id, fund_id, MUSING_KEEP))
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# 多空辩论推演（让「思考」从打分质变成推演）：复用 deep_research 的对抗式 prompt，
#   以本轮已算出的确定性研判(五维分/催化剂/均线/资金/趋势模板)为「取证包」，不另起重框架、不重复取数。
#   多头立论 → 空头逐条审视 → 裁判最终裁决(信心/止损位/认错条件/为什么选它)。
#   仅主账户、仅重大买入、同股 24h 去重、失败安全(不阻断交易、绝不臆造)。
# --------------------------------------------------------------------------- #

def _evidence_pack(name: str, symbol: str, an: dict, q: dict) -> str:
    """把本轮确定性研判压成给辩论用的『取证包』JSON 字符串——全是我们算好的硬事实，杜绝 LLM 臆造。"""
    t = an.get("tech", {}) or {}
    pack = {
        "标的": f"{name}({symbol})",
        "现价": safe_float(q.get("latest")), "当日涨跌%": safe_float(q.get("changeRatio")),
        "PE_TTM": safe_float(q.get("pe_ttm")), "PB": safe_float(q.get("pb")),
        "本站催化剂": an.get("catalyst"), "催化剂溯源": an.get("catalyst_ref"),
        "五维打分": an.get("scores"), "综合分": an.get("score"), "信心": an.get("confidence"),
        "买点": an.get("buy_point"),
        "均线": {"MA5": t.get("ma5"), "MA20": t.get("ma20"), "MA60": t.get("ma60"),
                 "近5日%": t.get("ret5"), "20日高": t.get("high20"), "波动": t.get("vol")},
        "长线趋势模板": an.get("trend_template"),
        "研判要点": [s.get("text") for s in (an.get("thinking") or [])][:8],
    }
    return json.dumps(pack, ensure_ascii=False)[:2200]


def _aifund_judge_prompt(ev: str, bull: str, rebuttal: str) -> str:
    return (
        "你是 DeepFocus AI 操盘手的『裁判』。给你①确定性取证包②多头论点③空头逐条审视。做最终交易裁决："
        "是否值得买、信心几何、止损/认错位设在哪、什么情况下离场、为什么这只比候选池其他票更值得这一击。"
        "只用取证包内事实，不得臆造数字；措辞中性、不喊单、不承诺收益。仅返回 JSON："
        '{"decision":"建仓|观望|放弃","conviction":0.0,"net_lean":"偏多|偏空|胶着",'
        '"thesis":"≤60字一句话总论","invalidation":"止损/认错条件(含价位或破位线)",'
        '"edge_reason":"为什么这只值得(对比候选池)","key_risk":"最大风险一句话"}\n'
        f"取证包：{ev}\n多头：{bull}\n空头审视：{rebuttal}"
    )


def _run_debate(name: str, symbol: str, an: dict, q: dict) -> Optional[dict]:
    """对一笔买入跑「多头立论→空头审视→裁判」3 步推演。复用 deep_research 对抗 prompt。失败→None，绝不抛出。"""
    if not (TONE and DEBATE):
        return None
    try:
        from .deep_research import _case_prompt, _debate_prompt
        from .compliance import neutralize_deep
        from .llm import CloudResearchLLM
    except Exception:  # noqa: BLE001
        return None
    ev = _evidence_pack(name, symbol, an, q)

    async def _go():
        llm = CloudResearchLLM()
        bull = await llm.complete_json(_case_prompt("多头", ev), max_tokens=700, timeout_seconds=30)
        bull_txt = json.dumps(bull, ensure_ascii=False)[:1500]
        rebuttal = await llm.complete_json(_debate_prompt(ev, bull_txt), max_tokens=700, timeout_seconds=30)
        reb_txt = json.dumps(rebuttal, ensure_ascii=False)[:1500]
        verdict = await llm.complete_json(_aifund_judge_prompt(ev, bull_txt, reb_txt), max_tokens=700, timeout_seconds=40)
        return {"bull": bull, "rebuttal": rebuttal, "verdict": verdict}

    try:
        out = asyncio.run(_go())
        if not (isinstance(out.get("bull"), dict) and isinstance(out.get("verdict"), dict)):
            return None
        return neutralize_deep(out)  # 中性化护栏：辩论叙述出口也走 compliance（与全站一致）
    except Exception:  # noqa: BLE001
        return None


def _maybe_debate(fund_id: str, trade_id: str, symbol: str, name: str, an: dict, q: dict) -> None:
    """同股 24h 去重后跑辩论并落库（绑定 trade_id）。滚动保留 DEBATE_KEEP 条。绝不抛出。"""
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=DEBATE_DEDUP_H)).isoformat()
        with _connect() as conn:
            recent = conn.execute("SELECT 1 FROM aif_debate WHERE fund_id=? AND symbol=? AND ts>=? LIMIT 1",
                                  (fund_id, symbol, cutoff)).fetchone()
        if recent:
            return
        payload = _run_debate(name, symbol, an, q)
        if not payload:
            return
        with _connect() as conn:
            conn.execute("INSERT OR REPLACE INTO aif_debate (trade_id,fund_id,symbol,name,ts,payload) VALUES (?,?,?,?,?,?)",
                         (trade_id, fund_id, symbol, name, utc_now_iso(), json.dumps(payload, ensure_ascii=False)))
            conn.execute("DELETE FROM aif_debate WHERE fund_id=? AND trade_id NOT IN "
                         "(SELECT trade_id FROM aif_debate WHERE fund_id=? ORDER BY ts DESC LIMIT ?)",
                         (fund_id, fund_id, DEBATE_KEEP))
            conn.commit()
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------- #
# AI 教练自评（把确定性学到的权重漂移讲成第一人称自我复盘——数字真实、绝不臆造）
# --------------------------------------------------------------------------- #

def _coach_note(cfg: AgentConfig, drift: list[dict], stats: dict) -> Optional[str]:
    """真·调教的「AI 原生」出口：用一句第一人称自评说清『我根据自己盈亏把哪维调高/调低了』。
    主账户(muse)走 LLM 润色(grounded 在真实漂移数字上)，其余用模板。绝不抛出、绝不臆造数字。"""
    if not drift:
        return None
    downs = [d for d in drift if d["pct"] < 0]
    ups = [d for d in drift if d["pct"] > 0]
    bits = []
    if downs:
        bits.append(f"调低「{downs[0]['dim']}」{downs[0]['pct']}%")
    if ups:
        bits.append(f"调高「{ups[0]['dim']}」+{ups[0]['pct']}%")
    wr = f"，胜率 {stats['win_rate']:.0f}%" if stats.get("win_rate") is not None else ""
    template = f"复盘自己的盈亏{wr}：我把打法{'、'.join(bits)}——{'少踩这维的坑' if downs else '多吃这维的肉'}。"
    if not (cfg.muse and TONE):
        return template
    try:
        from .compliance import neutralize_text
        from .llm import CloudResearchLLM
        drift_txt = "、".join(f"{d['dim']}{d['pct']:+d}%" for d in drift[:4])
        prompt = (f"你是 A股交易智能体「{cfg.name}」。你刚根据自己的实盘盈亏，自动微调了各维度权重(真实数据)：{drift_txt}{wr}。"
                  "用一句第一人称、口语、有锐度的话做『自我复盘』：说清为啥这么调、接下来打法怎么变；"
                  "≤40字，别喊单、别编上面没有的数字。只输出 JSON：{\"note\":\"…\"}。")
        data = asyncio.run(CloudResearchLLM().complete_json(prompt, max_tokens=200, timeout_seconds=20))
        v = (data or {}).get("note") if isinstance(data, dict) else None
        return neutralize_text(str(v)[:80]) if v else template
    except Exception:  # noqa: BLE001
        return template


# --------------------------------------------------------------------------- #
# 一轮决策
# --------------------------------------------------------------------------- #

def _universe(conn, cfg: AgentConfig = MAIN_CFG):
    pool = dict(cfg.pool)
    for r in _positions(conn, cfg.fund_id):
        pool.setdefault(r["symbol"], r["name"] or r["symbol"])
    return pool


_CMP_DIMS = ("消息面", "资金面", "技术面", "趋势", "成长质量", "基本面", "情绪")


def _compare_step(chosen_code, chosen_an, analyses: dict, exclude: set, universe: dict) -> Optional[dict]:
    """『为什么买它不买别的』：把选中标的 vs 当下最强的落选备选做横向对比，点出胜在哪一维。
    纯确定性(无 LLM)，作为一条思考链步骤注入买入解释——让『优选』可见。无可比对象→None。"""
    alts = [(c, a) for c, a in analyses.items()
            if c != chosen_code and c not in exclude and a.get("score") is not None]
    if not alts:
        return None
    rc, ra = max(alts, key=lambda x: x[1].get("score", -9.0))
    cs = chosen_an.get("scores", {}) or {}; rs = ra.get("scores", {}) or {}
    edges = []
    for dim in _CMP_DIMS:
        mv, tv = cs.get(dim), rs.get(dim)
        if mv is not None and tv is not None and (mv - tv) >= 0.2:
            edges.append((dim, mv, tv))
    edges.sort(key=lambda e: e[1] - e[2], reverse=True)
    edge_txt = "、".join(f"{d} {mv:+.2f}↗{tv:+.2f}" for d, mv, tv in edges[:2]) or "综合分更高、买点更扎实"
    return {"icon": "⚖️", "label": "优选",
            "text": f"候选里挑它而非{universe.get(rc, rc)}（综合 {chosen_an.get('score', 0):+.2f} vs {ra.get('score', 0):+.2f}）：胜在 {edge_txt}。"}


def run_tick(trade: bool = True, cfg: Optional[AgentConfig] = None) -> dict[str, Any]:
    """跑一轮：取价+日线+资金流→五维研判(含买点确认)→严谨卖出(移动止盈/硬止损/破位/利空/转弱)
    →催化剂确认买入→满仓换强→落库+人设解说。trade=False 只观察+盯市。cfg=某智能体配置(默认主账户)。绝不抛出。"""
    cfg = cfg or MAIN_CFG
    fund_id = cfg.fund_id
    init_ai_fund_db()
    if trade and not _in_session():
        trade = False  # ⭐硬闸门：非交易时段绝不买卖(无论谁调用)，A股收盘/周末只观察盯市
    if not ifind_enabled():
        return {"ok": False, "reason": "ifind_unavailable", "traded": [], "data_quality": get_snapshot(fund_id).get("data_quality")}

    with _connect() as conn:
        universe = _universe(conn, cfg)
        held_syms = [r["symbol"] for r in _positions(conn, fund_id)]
        st0 = _state(conn, fund_id)
        learned_mult = _loadj(st0["learned_weights"] if (st0 and "learned_weights" in st0.keys()) else None, {})  # 真·调教：自学权重乘子
        performance_brake = _performance_brake(_stats(conn, fund_id))
    md = _market_data(list(universe.keys()), priority=held_syms)
    regime = _market_regime_now()           # 大盘多空(沪深300 vs MA60)——第6.3 依据指数止损/收紧买入；驱动策略动态自适应
    regime_str = regime.get("regime")
    bear = regime_str == "bear"
    quotes, analyses = {}, {}
    scanned: dict[str, dict] = {}  # 本轮检索命中的本站近 24h 内容(按 id 去重)——情报吞吐量看点
    for code, name in universe.items():
        q = _quote(code)
        if not (q and safe_float(q.get("latest"))):
            continue
        items = _our_content(name)
        for it in items:
            if it.get("id") and float(it.get("age_h", 999.0)) <= 24:
                scanned.setdefault(it["id"], it)
        quotes[code] = q
        analyses[code] = _analyze(name, q, items, md.get(code), cfg, learned_mult, regime_str)
    if not quotes:
        return {"ok": False, "reason": "no_quotes", "traded": [], "data_quality": get_snapshot(fund_id).get("data_quality")}


    traded, decisions, watch = [], [], []
    now = utc_now_iso()
    with _connect() as conn:
        st = _state(conn, fund_id); cash = float(st["cash"])
        held = {r["symbol"]: dict(r) for r in _positions(conn, fund_id)}
        today_bj = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
        cooldown_start = (datetime.now(BJ_TZ) - timedelta(days=max(0, cfg.reentry_cooldown_days - 1))).strftime("%Y-%m-%d")
        recent_trades = conn.execute(
            "SELECT side,symbol,ts FROM aif_trade WHERE fund_id=? ORDER BY ts DESC LIMIT 500",
            (fund_id,)).fetchall()
        reentry_blocked = {r["symbol"] for r in recent_trades
                           if r["side"] == "sell" and _bj_date(r["ts"]) >= cooldown_start}
        daily_buys = sum(1 for r in recent_trades if r["side"] == "buy" and _bj_date(r["ts"]) == today_bj)

        # 更新移动止盈高水位
        for code, pos in held.items():
            q = quotes.get(code)
            price = safe_float(q.get("latest")) if q else None
            if price:
                hw = max(float(pos.get("high_water") or pos["avg_cost"]), price)
                if hw != (pos.get("high_water") or 0):
                    conn.execute("UPDATE aif_position SET high_water=? WHERE fund_id=? AND symbol=?", (hw, fund_id, code))
                    pos["high_water"] = hw

        # 1) 卖出（严谨章法）
        sold_now: set[str] = set()
        for code, pos in (list(held.items()) if trade else []):
            q = quotes.get(code)
            price = safe_float(q.get("latest")) if q else None
            if not price:
                continue
            # ⭐A股 T+1 铁律：当日买入的，当日不可卖出(含止损)——必须持有到下一交易日
            if _bj_date(pos.get("opened_at", "") or "") == today_bj:
                continue
            # ⭐A股涨跌停：跌停封板卖不出(无人接盘)——含硬止损也只能等下一交易日
            if _at_lower_limit(code, pos.get("name", ""), safe_float(q.get("changeRatio"))):
                continue
            avg = float(pos["avg_cost"]); pnl = (price - avg) / avg if avg else 0.0
            an = analyses.get(code, {"score": 0.0, "thinking": [], "tech": {}, "scores": {}})
            t = an.get("tech", {}); hw = float(pos.get("high_water") or avg)
            trail = max(cfg.trail_min, 2.2 * (t.get("vol") or 0.03))
            if bear:
                trail = max(0.05, trail * 0.65)   # 大盘转空：收紧移动止盈，让利润落袋(第6章)
            reason = None
            if pnl <= cfg.hard_stop:
                reason = f"硬止损 {pnl*100:.1f}%"
            elif hw > avg and price <= hw * (1 - trail) and pnl > 0:
                reason = f"移动止盈：自高点 {hw} 回落超 {trail*100:.0f}%，落袋 +{pnl*100:.1f}%"
            elif bear and t.get("ma20") and price < t["ma20"] and pnl < 0.02:
                reason = f"大盘转空(沪深300跌破60日线)+个股破位，依指数止损出局 {pnl*100:+.1f}%"
            elif t.get("ma20") and price < t["ma20"] * 0.985:
                reason = f"跌破20日线（{t['ma20']}）止盈止损 {pnl*100:+.1f}%"
            elif an.get("msg_dir", 0) < -0.35 and an.get("fresh"):
                reason = f"利空催化兑现 {pnl*100:+.1f}%"
            elif an["score"] <= cfg.sell_threshold:
                reason = f"信号转弱（综合 {an['score']:+.2f}）{pnl*100:+.1f}%"
            if reason:
                qty = float(pos["qty"]); amount = qty * price; cash += amount
                conn.execute("DELETE FROM aif_position WHERE fund_id=? AND symbol=?", (fund_id, code))
                tid = uuid.uuid4().hex
                think = an["thinking"] + [{"icon": "🧠", "label": "卖点", "text": reason + "。"}]
                conn.execute("INSERT INTO aif_trade (id,fund_id,ts,symbol,name,side,qty,price,amount,pnl_pct,confidence,catalyst,thinking,narrative,scores,buy_point,catalyst_ref,composite,reason)"
                             " VALUES (?,?,?,?,?,'sell',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                             (tid, fund_id, now, code, pos["name"], qty, price, amount, pnl*100, an.get("confidence"),
                              an.get("catalyst"), json.dumps(think, ensure_ascii=False), "",
                              json.dumps(an.get("scores", {}), ensure_ascii=False), "",
                              json.dumps(an.get("catalyst_ref"), ensure_ascii=False) if an.get("catalyst_ref") else None,
                              an.get("score"), reason))
                # 卖出也写穿公共数据层(与买入侧对称)：卖因(硬止损/移动止盈/破位…)+落袋盈亏，供跨模块历史复用
                data_store.record("simulation_trade", code, {"side": "sell", "price": price,
                                  "pnl_pct": round(pnl * 100, 2) if isinstance(pnl, (int, float)) else None,
                                  "reason": reason or "", "agent": fund_id}, market="A")
                held.pop(code, None); sold_now.add(code)
                decisions.append({"tid": tid, "side": "sell", "symbol": code, "name": pos["name"], "qty": qty,
                                  "price": price, "pnl": pnl*100, "an": {**an, "thinking": think}})
                traded.append({"side": "sell", "symbol": code, "name": pos["name"], "qty": qty, "price": price, "reason": reason})

        # 2) 买入：先过研判分，再过买点确认闸门；大盘转空时只做最强的(高分 + 书法买点/上升段)，宁踏空不接飞刀
        def _buy_allowed(an):
            if not an.get("entry_ok"):
                return False
            if an.get("score", 0) < float(an.get("entry_threshold", cfg.buy_threshold)) + performance_brake["thr_delta"]:
                return False
            if bear:
                return an.get("score", 0) >= 0.30 and (
                    an.get("pattern") is not None
                    or (an.get("trend_template") or {}).get("stage") == "advancing"
                    or (an.get("msg_dir", 0) > 0.45 and an.get("fresh")))
            return True
        ranked = sorted(((c, analyses[c]) for c in quotes
                         if c not in held and c not in sold_now and c not in reentry_blocked
                         and daily_buys < cfg.max_daily_buys and _buy_allowed(analyses[c])),
                        key=lambda x: x[1]["score"], reverse=True) if trade else []

        def _do_buy(code, an):
            nonlocal cash, daily_buys
            if daily_buys >= cfg.max_daily_buys or code in reentry_blocked:
                return False
            price = safe_float(quotes[code].get("latest"))
            if not price:
                return False
            # ⭐A股涨跌停：涨停封板买不进(无人卖出)——不模拟成交，避免「买在涨停板」的笑话
            if _at_upper_limit(code, universe.get(code, code), safe_float(quotes[code].get("changeRatio"))):
                return False
            nav_now = cash + sum(float(p["qty"]) * (safe_float(quotes.get(p["symbol"], {}).get("latest")) or float(p["avg_cost"])) for p in held.values())
            # 单仓大小按流派系数 × 市场态势系数缩放：激进重仓(系数>1)/价值分散(<1)，再叠牛市加仓/熊市减仓；硬上限不超 50%
            size_mult = (cfg.pos_size_mult * (an.get("regime_adapt", {}).get("size_mult") or 1.0)
                         * performance_brake["size_mult"])
            target = nav_now * (0.18 + 0.14 * an["confidence"]) * size_mult
            cap = nav_now * min(0.5, 0.33 * size_mult)
            budget = min(cash, target, cap)
            qty = math.floor(budget / (price * BOARD_LOT)) * BOARD_LOT
            if qty <= 0 or qty * price > cash:
                return False
            amount = qty * price; cash -= amount; name = universe.get(code, code); now2 = utc_now_iso()
            think = list(an["thinking"])
            cmp = _compare_step(code, an, analyses, set(held) | {code}, universe)  # 为什么买它不买别的(横向对比)
            if cmp:
                think.append(cmp)
            if performance_brake["key"] != "normal":
                think.append({"icon": "🛡", "label": f"{performance_brake['label']}档",
                              "text": f"近期战绩触发自动降档：门槛 +{performance_brake['thr_delta']:.2f}、"
                                      f"仓位缩至 {performance_brake['size_mult']*100:.0f}%。"})
            think.append({"icon": "🧠", "label": "买点", "text": f"{an['buy_point']}；综合 {an['score']:+.2f}、信心 {an['confidence']*100:.0f}%，出手。"})
            conn.execute("INSERT INTO aif_position (fund_id,symbol,name,qty,avg_cost,opened_at,updated_at,high_water) VALUES (?,?,?,?,?,?,?,?)",
                         (fund_id, code, name, qty, price, now2, now2, price))
            held[code] = {"symbol": code, "name": name, "qty": qty, "avg_cost": price, "high_water": price, "opened_at": now2}
            tid = uuid.uuid4().hex
            conn.execute("INSERT INTO aif_trade (id,fund_id,ts,symbol,name,side,qty,price,amount,pnl_pct,confidence,catalyst,thinking,narrative,scores,buy_point,catalyst_ref,composite,reason)"
                         " VALUES (?,?,?,?,?,'buy',?,?,?,NULL,?,?,?,?,?,?,?,?,?)",
                         (tid, fund_id, now2, code, name, qty, price, amount, an.get("confidence"), an.get("catalyst"),
                          json.dumps(think, ensure_ascii=False), "", json.dumps(an.get("scores", {}), ensure_ascii=False),
                          an.get("buy_point"),
                          json.dumps(an.get("catalyst_ref"), ensure_ascii=False) if an.get("catalyst_ref") else None,
                          an.get("score"), (an.get("catalyst") or an.get("buy_point") or "")[:120]))
            data_store.record("simulation_trade", code, {"side": "buy", "price": price, "agent": fund_id}, market="A")
            decisions.append({"tid": tid, "side": "buy", "symbol": code, "name": name, "qty": qty, "price": price, "pnl": None, "an": {**an, "thinking": think}})
            traded.append({"side": "buy", "symbol": code, "name": name, "qty": qty, "price": price, "reason": an.get("buy_point") or ""})
            daily_buys += 1
            return True

        for code, an in ranked:
            if daily_buys >= cfg.max_daily_buys:
                break
            if len(held) < cfg.max_positions:
                _do_buy(code, an)
            else:
                # ⭐换仓卖出同样守 A股规则：当日新仓(T+1)不可卖、跌停封板卖不出——只在可卖持仓里挑最弱
                sellable = [c for c in held
                            if _bj_date(held[c].get("opened_at", "") or "") != today_bj
                            and not _at_lower_limit(c, held[c].get("name", ""), safe_float((quotes.get(c) or {}).get("changeRatio")))]
                if not sellable:
                    continue
                weakest = min(sellable, key=lambda c: analyses.get(c, {}).get("score", 0.0))
                ws = analyses.get(weakest, {}).get("score", 0.0)
                # 换仓前确认接盘股买得进(没涨停封板)，否则会「卖了最弱却买不进」→ 空仓换仓、现金空置、直播流食言
                incoming_sealed = _at_upper_limit(code, universe.get(code, code), safe_float((quotes.get(code) or {}).get("changeRatio")))
                rotate_edge = cfg.rotate_edge * performance_brake["rotate_mult"]
                if code not in held and not incoming_sealed and an["score"] - ws >= rotate_edge:
                    q = quotes.get(weakest)
                    if q and safe_float(q.get("latest")):
                        wp = safe_float(q.get("latest")); wpos = held[weakest]
                        wpnl = (wp - float(wpos["avg_cost"])) / float(wpos["avg_cost"]) if wpos["avg_cost"] else 0.0
                        cash += float(wpos["qty"]) * wp
                        conn.execute("DELETE FROM aif_position WHERE fund_id=? AND symbol=?", (fund_id, weakest))
                        tid = uuid.uuid4().hex; wan = analyses.get(weakest, {"thinking": [], "scores": {}})
                        rtxt = f"换仓：{wpos['name']}（{ws:+.2f}）让位给更强的 {universe.get(code,code)}（{an['score']:+.2f}）"
                        think = wan.get("thinking", []) + [{"icon": "🔄", "label": "换仓", "text": rtxt + "。"}]
                        conn.execute("INSERT INTO aif_trade (id,fund_id,ts,symbol,name,side,qty,price,amount,pnl_pct,confidence,catalyst,thinking,narrative,scores,buy_point,catalyst_ref,composite,reason)"
                                     " VALUES (?,?,?,?,?,'sell',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                     (tid, fund_id, utc_now_iso(), weakest, wpos["name"], float(wpos["qty"]), wp, float(wpos["qty"])*wp, wpnl*100,
                                      wan.get("confidence"), wan.get("catalyst"), json.dumps(think, ensure_ascii=False), "",
                                      json.dumps(wan.get("scores", {}), ensure_ascii=False), "",
                                      json.dumps(wan.get("catalyst_ref"), ensure_ascii=False) if wan.get("catalyst_ref") else None,
                                      wan.get("score"), rtxt))
                        # 换仓卖出同样写穿公共数据层(与买入/止损卖出侧字段一致)
                        data_store.record("simulation_trade", weakest, {"side": "sell", "price": wp,
                                          "pnl_pct": round(wpnl * 100, 2) if isinstance(wpnl, (int, float)) else None,
                                          "reason": rtxt or "", "agent": fund_id}, market="A")
                        decisions.append({"tid": tid, "side": "sell", "symbol": weakest, "name": wpos["name"], "qty": float(wpos["qty"]), "price": wp, "pnl": wpnl*100, "an": {**wan, "thinking": think}})
                        traded.append({"side": "sell", "symbol": weakest, "name": wpos["name"], "qty": float(wpos["qty"]), "price": wp, "reason": rtxt})
                        held.pop(weakest, None); _do_buy(code, an)

        # 3) 观察流（有催化剂没动手）
        traded_syms = {t["symbol"] for t in traded}
        for code in quotes:
            an = analyses.get(code, {})
            if an.get("fresh") and code not in traded_syms and len(watch) < 3:
                name = universe.get(code, code)
                if code in held:
                    txt = "已持有，按章法持股待涨"
                elif not trade:
                    txt = "盘后捕捉到催化剂，待开盘验证买点" if an.get("score", 0) >= cfg.buy_threshold else "盘后扫到相关动态，记一笔"
                elif code in reentry_blocked:
                    txt = f"刚平仓，执行 {cfg.reentry_cooldown_days} 日回补冷静期"
                elif daily_buys >= cfg.max_daily_buys:
                    txt = f"今日已达 {cfg.max_daily_buys} 笔开仓上限，停止追单"
                elif not an.get("entry_ok"):
                    why = "趋势未确认" if not an.get("trend_up") else ("涨幅过高不追" if an.get("overbought") else "资金/分数不够")
                    txt = f"有催化剂但买点不成立（{why}），观望"
                else:
                    continue
                # 拟人化旁白(不喊单、口语)：让"观察"像人随口点评，而非机器贴催化剂原文
                md_ = an.get("msg_dir", 0)
                if code in held:
                    human = f"{name}还在手里，按计划拿着，让利润再飞会儿。"
                elif md_ < -0.2:
                    human = f"{name}冒出利空苗头，我先躲一躲、不接飞刀。"
                elif not an.get("trend_up"):
                    human = f"{name}有消息但还没站上 20 日线，先盯着、不急。"
                elif an.get("overbought"):
                    human = f"{name}今天冲太猛，追高不划算，等回踩再说。"
                else:
                    human = f"{name}信号还差点意思，列入观察名单。"
                tid = uuid.uuid4().hex
                think = an.get("thinking", []) + [{"icon": "👀", "label": "观察", "text": f"{txt}（综合 {an.get('score', 0):+.2f}）。"}]
                conn.execute("INSERT INTO aif_thought (id,fund_id,ts,symbol,name,action,catalyst,thinking,narrative,confidence,scores)"
                             " VALUES (?,?,?,?,?,'watch',?,?,?,?,?)",
                             (tid, fund_id, utc_now_iso(), code, name, an.get("catalyst"),
                              json.dumps(think, ensure_ascii=False), human, an.get("confidence"),
                              json.dumps(an.get("scores", {}), ensure_ascii=False)))
                watch.append({"symbol": code, "name": name})

        # 3.5) 记忆沉淀(像人一样持续学习/演化)：
        #   ① 对本站持续点名的票形成/强化「观点」thesis(空仓/盘后也在积累认知)
        for code in quotes:
            an = analyses.get(code, {})
            if an.get("catalyst_ref"):
                _upsert_thesis(conn, code, universe.get(code, code), _our_content(universe.get(code, code)), an.get("score", 0.0), fund_id, cfg.style)
        #   ② 每笔平仓即复盘 → 沉淀「成功模式/踩坑」教训 + 真·调教：按盈亏归因微调自己的维度权重
        learned_changed = False
        for d in decisions:
            if d.get("side") == "sell" and d.get("pnl") is not None:
                rr = conn.execute("SELECT reason FROM aif_trade WHERE id=?", (d["tid"],)).fetchone()
                _record_trade_memory(conn, d["symbol"], d["name"], d["pnl"], (rr["reason"] if rr else "") or "", fund_id)
                # 取该股最近一笔买入时的维度打分 → 信用分配学习
                b = conn.execute("SELECT scores FROM aif_trade WHERE fund_id=? AND symbol=? AND side='buy' ORDER BY ts DESC LIMIT 1",
                                 (fund_id, d["symbol"])).fetchone()
                buy_scores = _loadj(b["scores"], {}) if b else {}
                if buy_scores:
                    learned_mult = ai_fund_evolve.learn_weights(learned_mult, buy_scores, d.get("pnl"))
                    learned_changed = True
        #   ②' 持仓期自适应(每日一次)：按未平仓持仓的浮盈浮亏轻调权重——不必死等平仓，
        #       让角色持续适应「当下市场什么因子在起效」。日期节流，避免每 tick 反复学同一批浮动。
        _today_bj = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
        _hld_date = st0["hold_learn_date"] if (st0 and "hold_learn_date" in st0.keys()) else None
        if _hld_date != _today_bj:
            holds_for_learn = []
            for prow in _positions(conn, fund_id):
                q = quotes.get(prow["symbol"]); cur = safe_float(q.get("latest")) if q else None
                ac = float(prow["avg_cost"]) if prow["avg_cost"] else 0.0
                if cur and ac:
                    bb = conn.execute("SELECT scores FROM aif_trade WHERE fund_id=? AND symbol=? AND side='buy' ORDER BY ts DESC LIMIT 1",
                                      (fund_id, prow["symbol"])).fetchone()
                    bs = _loadj(bb["scores"], {}) if bb else {}
                    if bs:
                        holds_for_learn.append({"buy_scores": bs, "unrealized_pct": (cur - ac) / ac * 100})
            if holds_for_learn:
                learned_mult = ai_fund_evolve.learn_from_holdings(learned_mult, holds_for_learn)
                learned_changed = True
            conn.execute("UPDATE aif_state SET hold_learn_date=? WHERE fund_id=?", (_today_bj, fund_id))
        if learned_changed:
            conn.execute("UPDATE aif_state SET learned_weights=? WHERE fund_id=?",
                         (json.dumps(learned_mult, ensure_ascii=False), fund_id))
            _note = _coach_note(cfg, ai_fund_evolve.weights_drift(cfg.weights, learned_mult), _stats(conn, fund_id))
            if _note:
                conn.execute("UPDATE aif_state SET coach_note=? WHERE fund_id=?", (_note, fund_id))

        # 修剪观察流，防累积重复(同股反复观察)；脑内独白(musing)走独立保留，不被此 40 上限挤掉
        conn.execute("DELETE FROM aif_thought WHERE fund_id=? AND COALESCE(action,'watch')<>'musing' AND id NOT IN "
                     "(SELECT id FROM aif_thought WHERE fund_id=? AND COALESCE(action,'watch')<>'musing' ORDER BY ts DESC LIMIT 40)", (fund_id, fund_id))

        # 落 K 线（OHLC）供前端蜡烛图，仅存有数据的
        for code in quotes:
            ohlc = (md.get(code) or {}).get("ohlc") or []
            if ohlc:
                conn.execute("INSERT OR REPLACE INTO aif_kline (symbol, ohlc, updated_at) VALUES (?,?,?)",
                             (code, json.dumps(ohlc[-55:], ensure_ascii=False), now))

        # 情报吞吐量(本轮检索命中近24h本站内容的计数 + 最近标题)——空窗期也体现"靠本站内容流活着"
        sc_news = sc_rep = sc_art = 0; sc_titles = []
        for it in scanned.values():
            s = it.get("src")
            if s == "快讯":
                sc_news += 1
            elif s == "研报":
                sc_rep += 1
            elif s == "文章":
                sc_art += 1
            if len(sc_titles) < 4 and it.get("title"):
                sc_titles.append(it["title"])
        conn.execute("UPDATE aif_state SET scanned_news=?,scanned_report=?,scanned_article=?,scanned_date=?,scanned_titles=? WHERE fund_id=?",
                     (sc_news, sc_rep, sc_art, datetime.now(BJ_TZ).strftime("%Y-%m-%d"),
                      json.dumps(sc_titles, ensure_ascii=False), fund_id))

        # 4) 现金 + 净值快照
        conn.execute("UPDATE aif_state SET cash=?, last_tick_at=? WHERE fund_id=?", (cash, now, fund_id))
        mkt = 0.0
        for pos in _positions(conn, fund_id):
            q = quotes.get(pos["symbol"])
            mkt += float(pos["qty"]) * ((safe_float(q.get("latest")) if q else None) or float(pos["avg_cost"]))
        nav = cash + mkt
        conn.execute("INSERT OR REPLACE INTO aif_nav (fund_id,ts,nav,cash,market_value) VALUES (?,?,?,?,?)", (fund_id, now, nav, cash, mkt))
        conn.execute("DELETE FROM aif_nav WHERE fund_id=? AND ts NOT IN (SELECT ts FROM aif_nav WHERE fund_id=? ORDER BY ts DESC LIMIT ?)", (fund_id, fund_id, NAV_HISTORY_KEEP))
        conn.commit()

    # 5) 人设解说（成交才调）
    if decisions:
        with _connect() as conn:
            stats = _stats(conn, fund_id)
        nav_pct = (nav - cfg.initial_capital) / cfg.initial_capital * 100 if cfg.initial_capital else 0.0
        narr = _llm_narratives(decisions, _mood(stats, nav_pct), cfg)
        with _connect() as conn:
            for d in decisions:
                txt = narr.get(d["tid"]) or _template_narrative(d["side"], d["name"], d["an"], d.get("pnl"))
                conn.execute("UPDATE aif_trade SET narrative=? WHERE id=?", (txt, d["tid"]))
            conn.commit()

    # 5.5) 多空辩论推演：仅主账户、仅最强的若干笔买入跑「多头立论→空头审视→裁判」(同股 24h 去重、失败安全)
    if cfg.debate and DEBATE and decisions:
        buys = sorted([d for d in decisions if d.get("side") == "buy"],
                      key=lambda d: (d["an"].get("confidence") or 0), reverse=True)
        for d in buys[:DEBATE_MAX_PER_TICK]:
            _maybe_debate(cfg.fund_id, d["tid"], d["symbol"], d["name"], d["an"], quotes.get(d["symbol"]) or {})

    # 6) 脑内独白：无论是否成交、是否开盘，都让阿尔法持续『沉淀思考』(节流入库)——收盘/周末也有灵魂在动
    _deposit_musing(nav, cfg)

    return {"ok": True, "traded": traded, "watch": watch, "nav": nav, "regime": regime.get("regime"),
            "data_quality": {"level": "live", "label": "实时撮合", "detail": "iFinD 行情 + 东财日线/资金流撮合", "reasons": []}}


# --------------------------------------------------------------------------- #
# 快照
# --------------------------------------------------------------------------- #

def _loadj(s, default):
    if not s:
        return default
    try:
        v = json.loads(s); return v
    except Exception:  # noqa: BLE001
        return default


_BENCH_CACHE: dict = {}  # 'csi300' -> (ts, [{day, open, close}])
RISK_MIN_DAYS = 5        # 风险比率(夏普/波动/Beta)样本下限——不足只报最大回撤+天数，绝不在极短曲线上瞎算


async def _fetch_index_klines(points: int = 160) -> list:
    """沪深300 日线 [{day, open, close}]：新浪 sh000300 主源(含开盘价)→东财备源(无开盘价则 open=close)。"""
    url = (f"https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData"
           f"?symbol=sh000300&scale=240&ma=no&datalen={points}")
    try:
        async with _em_client() as c:
            r = await c.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
        if r.status_code == 200:
            arr = r.json()
            out = [{"day": k["day"], "close": float(k["close"]),
                    "open": float(k.get("open") or k["close"])}
                   for k in (arr if isinstance(arr, list) else [])
                   if k.get("day") and k.get("close")]
            if out:
                return out
    except Exception:  # noqa: BLE001
        pass
    try:
        from .eastmoney_data import fetch_eastmoney_index
        em = await fetch_eastmoney_index("1.000300", points=points)
        if em:
            return [{"day": d, "close": c, "open": c} for d, c in em]   # 东财备源无开盘价→open=close
    except Exception:  # noqa: BLE001
        pass
    return []


async def _fetch_index_series(points: int = 160) -> list:
    """沪深300 日线 [(date, close)]——兼容旧调用(regime/risk)，由 K 线派生。"""
    return [(k["day"], k["close"]) for k in await _fetch_index_klines(points)]


_REGIME_CACHE: dict = {}   # 'r' -> (ts, regime_dict)


def _market_regime_now() -> dict:
    """大盘多空(沪深300 vs MA60/MA120)——第6.3「依据指数止损」：大盘跌则 90% 个股同跌。
    缓存 2h、绝不抛出，失败→unknown(按非空头处理，不误杀)。"""
    import time as _t
    hit = _REGIME_CACHE.get("r")
    if hit and (_t.time() - hit[0]) < 7200:
        return hit[1]
    out = {"regime": "unknown", "above_ma60": None, "ma60_rising": None}
    try:
        series = asyncio.run(_fetch_index_series(points=160))
        closes = [c for _, c in series]
        if len(closes) >= 60:
            out = bull_playbook.market_regime(closes)
    except Exception:  # noqa: BLE001
        pass
    _REGIME_CACHE["r"] = (_t.time(), out)
    return out


def _benchmark(started_date: str, nav_pct: float) -> Optional[dict]:
    """沪深300 同期对比：归一净值序列 + 超额收益 alpha。缓存 6h(失败短缓存)。
    ⭐基线用开赛日『开盘点位』而非收盘——开赛当天日线起=终(同一根今日K)会让 bench_ret 恒 0、
    使 alpha 退化成原始收益、毫无意义。用开盘价后，基金(开赛起建仓)与大盘同窗口可比。"""
    import time as _t
    hit = _BENCH_CACHE.get("csi300")
    if not (hit and (_t.time() - hit[0]) < 21600 and hit[1]):
        try:
            kl = asyncio.run(_fetch_index_klines())
        except Exception:  # noqa: BLE001
            kl = []
        _BENCH_CACHE["csi300"] = (_t.time() if kl else _t.time() - 19800, kl)  # 失败仅缓存 30min
        hit = _BENCH_CACHE["csi300"]
    kl = hit[1]
    if not kl:
        return None
    sd = started_date[:10]
    start_bar = next((k for k in kl if k["day"] >= sd), kl[0])
    baseline = start_bar.get("open") or start_bar.get("close")
    last_close = kl[-1]["close"]
    bench_ret = (last_close / baseline - 1) * 100 if baseline else 0.0
    # 归一曲线以开盘基线锚定 1.0(与基金 started_nav 同起点)，再接每日收盘点
    norm = ([{"date": sd, "value": 1.0}] +
            [{"date": k["day"], "value": round(k["close"] / baseline, 4)} for k in kl if k["day"] >= sd]) if baseline else []
    return {"name": "沪深300", "series": norm, "bench_ret": round(bench_ret, 2),
            "alpha": round(nav_pct - bench_ret, 2)}


def _risk_metrics(history: list[dict], started_nav: float, bench_series: Optional[list[dict]]) -> dict:
    """从日净值序列算专业风险度量(最大回撤/年化波动/夏普/索提诺/卡玛/Beta·Beta调整alpha)。
    复用 backtest_engine 的标准实现(同一套数学,不另造轮子)；
    ⚠️诚实纪律：样本<2 点什么都不算；2~RISK_MIN_DAYS 只给最大回撤；够样本才出年化比率与 Beta/alpha。"""
    pts = [h for h in history if h.get("value")]
    n = len(pts)
    out: dict = {"sample_days": n, "sufficient": n >= RISK_MIN_DAYS,
                 "max_drawdown_pct": None, "annualized_vol": None, "sharpe": None,
                 "sortino": None, "calmar": None, "beta": None, "alpha_annual": None}
    if n < 2 or not started_nav:
        return out
    try:
        from .backtest_engine import calculate_backtest_metrics
    except Exception:  # noqa: BLE001
        return out
    equity = [round(float(h["value"]) * started_nav, 2) for h in pts]
    bench_curve = None
    if bench_series:
        bmap = {b.get("date"): b.get("value") for b in bench_series
                if b.get("date") and b.get("value") is not None}
        if bmap:
            last = bench_series[0].get("value")  # 首日缺基准则用基准序列起点回填，保证等长可算 Beta
            aligned = []
            for h in pts:
                v = bmap.get(h.get("date"))
                if v is not None:
                    last = v
                aligned.append(last)
            if len(aligned) == len(equity) and all(x is not None for x in aligned):
                bench_curve = aligned
    m = calculate_backtest_metrics(equity, bench_curve, initial_capital=started_nav)
    out["max_drawdown_pct"] = m.get("max_drawdown_pct")
    if n >= RISK_MIN_DAYS:  # 够样本才出对短序列敏感的年化比率
        out["annualized_vol"] = m.get("annualized_volatility")
        out["sharpe"] = m.get("sharpe_ratio")
        out["sortino"] = m.get("sortino_ratio")
        out["calmar"] = m.get("calmar_ratio")
        if bench_curve:
            out["beta"] = m.get("beta")
            out["alpha_annual"] = m.get("alpha")  # CAPM Beta 调整后年化超额(非简单收益相减)
    return out


_WIRE_CACHE: dict = {}  # 站内最新快讯滚动条(轻缓存护并发)


def _latest_wire(limit: int = 16) -> list[dict]:
    """DeepFocus 站内最新快讯/文章/研报(给页面滚动条用，体现'本站信息'+引流)。缓存 30s。"""
    import time as _t
    hit = _WIRE_CACHE.get("w")
    if hit and (_t.time() - hit[0]) < 30.0:
        return hit[1]
    out: list[dict] = []
    try:
        from .realtime_messages import list_realtime_messages
        for m in list_realtime_messages(limit=60):
            st = getattr(m, "source_type", "") or ""
            if st == "dao-signal":  # 内部风控信号不放滚动条(给读者看快讯/文章/研报)
                continue
            out.append({"title": getattr(m, "title", "") or "", "src": _SRC_LABEL.get(st, "资讯"),
                        "ts": getattr(m, "created_at", "") or "", "symbol": getattr(m, "symbol", "") or "",
                        "url": getattr(m, "url", "") or ""})
            if len(out) >= limit:
                break
    except Exception:  # noqa: BLE001
        out = []
    _WIRE_CACHE["w"] = (_t.time(), out)
    return out


def _info_attribution(conn, fund_id: str = FUND_ID) -> Optional[dict]:
    """『本站信息驱动』归因：带强催化剂(消息面>0.3 且有 catalyst)的买入占比 + 其平仓收益占比。样本太少→None。"""
    rows = conn.execute("SELECT side,scores,catalyst,pnl_pct,symbol FROM aif_trade WHERE fund_id=? ORDER BY ts ASC",
                        (fund_id,)).fetchall()
    buys = [r for r in rows if r["side"] == "buy"]
    if len(buys) < 1:
        return None

    def _strong(r):
        sc = _loadj(r["scores"], {})
        return bool(r["catalyst"]) and float(sc.get("消息面", 0) or 0) > 0.3
    strong = [r for r in buys if _strong(r)]
    strong_syms = {r["symbol"] for r in strong}
    sells = [r for r in rows if r["side"] == "sell" and r["pnl_pct"] is not None]
    pnl_share = None
    if sells:
        tot = sum(abs(float(r["pnl_pct"])) for r in sells)
        sp = sum(abs(float(r["pnl_pct"])) for r in sells if r["symbol"] in strong_syms)
        pnl_share = round(sp / tot * 100) if tot else None
    return {"trade_pct": round(len(strong) / len(buys) * 100), "pnl_pct": pnl_share, "count": len(strong)}


def get_snapshot(fund_id: str = FUND_ID) -> dict[str, Any]:
    init_ai_fund_db()
    cfg = cfg_for(fund_id)
    fund_id = cfg.fund_id   # 未知/别名 strategy 归一到真实 fund_id，避免 _state 取到 None 后 st["cash"] 崩、被上游静默兜回主账户
    with _connect() as conn:
        st = _state(conn, fund_id); cash = float(st["cash"]); started_nav = float(st["started_nav"])
        positions = _positions(conn, fund_id)
        trades = conn.execute("SELECT * FROM aif_trade WHERE fund_id=? ORDER BY ts DESC LIMIT 40", (fund_id,)).fetchall()
        thoughts = conn.execute("SELECT * FROM aif_thought WHERE fund_id=? AND COALESCE(action,'watch')<>'musing' ORDER BY ts DESC LIMIT 20", (fund_id,)).fetchall()
        musing_rows = conn.execute("SELECT * FROM aif_thought WHERE fund_id=? AND action='musing' ORDER BY ts DESC LIMIT 24", (fund_id,)).fetchall()
        nav_rows = conn.execute("SELECT ts, nav FROM aif_nav WHERE fund_id=? ORDER BY ts ASC", (fund_id,)).fetchall()
        stats = _stats(conn, fund_id)
        klines = {r["symbol"]: r["ohlc"] for r in conn.execute("SELECT symbol, ohlc FROM aif_kline").fetchall()}
        debates = {r["trade_id"]: r["payload"] for r in
                   conn.execute("SELECT trade_id, payload FROM aif_debate WHERE fund_id=?", (fund_id,)).fetchall()}
        info_driven = _info_attribution(conn, fund_id)
        decisions_total = conn.execute(  # 「出手次数」= 交易 + 观察，不含脑内独白(独白只体现思考而非出手)
            "SELECT (SELECT COUNT(*) FROM aif_trade WHERE fund_id=?)+(SELECT COUNT(*) FROM aif_thought WHERE fund_id=? AND COALESCE(action,'watch')<>'musing') AS n",
            (fund_id, fund_id)).fetchone()["n"]
        thinking_total = conn.execute("SELECT COUNT(*) AS n FROM aif_thought WHERE fund_id=? AND action='musing'", (fund_id,)).fetchone()["n"]
        mem_rows = conn.execute("SELECT * FROM aif_memory WHERE fund_id=? ORDER BY (confidence*weight) DESC, updated_at DESC LIMIT 30", (fund_id,)).fetchall()
        mem_agg = conn.execute("SELECT mem_type, COUNT(*) AS n, AVG(confidence) AS c FROM aif_memory WHERE fund_id=? GROUP BY mem_type", (fund_id,)).fetchall()
        # 操盘纪律(Alpha Arena 教训:赢家拼的不是交易频次，是出手克制 + 每笔有据 + 回撤可控)
        disc = conn.execute("SELECT COUNT(*) AS n, "
                            "SUM(CASE WHEN side='buy' THEN 1 ELSE 0 END) AS b, "
                            "SUM(CASE WHEN side='buy' AND catalyst_ref IS NOT NULL THEN 1 ELSE 0 END) AS bc "
                            "FROM aif_trade WHERE fund_id=?", (fund_id,)).fetchone()
    musings = [{"ts": m["ts"], "phase": m["name"] or "", "phase_key": m["catalyst"] or "",
                "text": m["narrative"] or "", "refs": _loadj(m["thinking"], []),
                "recalled": _loadj(m["recalled_refs"] if "recalled_refs" in m.keys() else "", [])} for m in musing_rows]
    # 阿尔法的记忆/认知：交易教训 + 个股观点(thesis)，给前端「🧠 认知」面板
    memory = [{"symbol": r["symbol"], "name": r["name"] or r["symbol"], "mem_type": r["mem_type"], "title": r["title"],
               "confidence": round(float(r["confidence"] or 0), 2), "weight": round(float(r["weight"] or 0), 2),
               "pnl_impact": r["pnl_impact"], "src": r["src"], "ts": r["ts"], "updated_at": r["updated_at"],
               "seen_count": r["seen_count"] if "seen_count" in r.keys() else 1,
               "detail": _loadj(r["detail"], {})} for r in mem_rows]
    _ma = {r["mem_type"]: {"n": int(r["n"]), "conf": round(float(r["c"] or 0), 2)} for r in mem_agg}
    _ml = {k: v for k, v in _ma.items() if k != "rule"}   # "学到"统计不含规则(规则是铁律不是学来的)
    memory_stats = {"total": sum(v["n"] for v in _ml.values()),
                    "wins": _ml.get("trade_win", {}).get("n", 0), "losses": _ml.get("trade_loss", {}).get("n", 0),
                    "theses": _ml.get("thesis", {}).get("n", 0),
                    "avg_confidence": round(sum(v["n"] * v["conf"] for v in _ml.values()) / sum(v["n"] for v in _ml.values()), 2) if _ml else 0.0}
    _tc = int(disc["n"] or 0); _bc = int(disc["b"] or 0); _cc = int(disc["bc"] or 0)
    # 出手依据按模型分化：价值=估值驱动、逆向=超跌驱动、多因子=本站催化数据驱动
    _drive = {"value": "估值驱动", "reversion": "超跌驱动"}.get(cfg.model, "数据驱动")
    _drule = {"value": "内在价值低估(便宜+质量)才买，不追催化",
              "reversion": "超跌+企稳才反手抄，不接飞刀",
              }.get(cfg.model, "每笔买入需本站快讯/文章/研报催化剂 + 站上20日线确认")
    _brake = _performance_brake(stats)
    discipline = {"trades": _tc, "buys": _bc,
                  "catalyst_pct": round(_cc / _bc * 100) if _bc else None,  # 多少买入由本站催化剂触发
                  "drive_label": _drive, "rule": _drule,
                  "brain_mode": _brake["key"], "brain_mode_label": _brake["label"],
                  "threshold_add": _brake["thr_delta"], "size_mult": _brake["size_mult"],
                  "reentry_cooldown_days": cfg.reentry_cooldown_days,
                  "daily_buy_cap": cfg.max_daily_buys}
    review = _retrospective(conn, fund_id, stats, _brake, cfg)
    _k = lambda c: (st[c] if c in st.keys() else None)  # noqa: E731 老库列可能缺
    scan = {"news": int(_k("scanned_news") or 0), "report": int(_k("scanned_report") or 0),
            "article": int(_k("scanned_article") or 0), "titles": _loadj(_k("scanned_titles"), [])}

    pos_out, market_value, degraded = [], 0.0, False
    for p in positions:
        q = _quote(p["symbol"]) if ifind_enabled() else None
        price = safe_float(q.get("latest")) if q else None
        if price is None:
            price = float(p["avg_cost"]); degraded = True
        mv = float(p["qty"]) * price; market_value += mv; avg = float(p["avg_cost"])
        pos_out.append({"symbol": p["symbol"], "name": p["name"] or p["symbol"], "qty": float(p["qty"]),
                        "avg_cost": round(avg, 3), "current_price": round(price, 3), "market_value": round(mv, 2),
                        "pnl_pct": round((price - avg) / avg * 100, 2) if avg else 0.0})
    # 成交标记(买卖点)：symbol -> [{date, side, price}]，按 BJ 日聚合到日 K
    marks_by_sym: dict[str, list] = {}
    for t in trades:
        marks_by_sym.setdefault(t["symbol"], []).append(
            {"date": _bj_date(t["ts"]), "side": t["side"], "price": round(float(t["price"]), 3)})
    for po in pos_out:
        po["weight"] = round(po["market_value"] / (cash + market_value), 4) if (cash + market_value) else 0.0
        po["kline"] = _loadj(klines.get(po["symbol"]), [])
        po["marks"] = marks_by_sym.get(po["symbol"], [])
        # 浮盈金额(精确成本法)：盈利感
        po["float_pnl"] = round(po["market_value"] - po["qty"] * po["avg_cost"], 2)
        # 事件标记(本站快讯/文章/研报)：按真实发布日铺在 K 线上；去重(同日同标题)、跨日期取样
        _ev, _seen = [], set()
        for it in _our_content(po["name"]):
            if not it.get("date"):
                continue
            key = (it["date"], (it.get("title") or "")[:20])
            if key in _seen:
                continue
            _seen.add(key)
            _ev.append({"date": it["date"], "title": it["title"], "src": it["src"],
                        "url": it.get("url", ""), "age_h": it.get("age_h")})
        po["events"] = _ev[:14]
        po["info_fresh"] = sum(1 for it in _ev if float(it.get("age_h", 999.0)) <= 72)  # 72h 内本站相关条数(信息温度)
        po["info_latest_h"] = round(min((float(it.get("age_h", 999.0)) for it in _ev), default=999.0), 1)
    pos_out.sort(key=lambda x: x["market_value"], reverse=True)

    nav = cash + market_value
    nav_pct = (nav - started_nav) / started_nav * 100 if started_nav else 0.0
    dedup = {}
    for r in nav_rows:
        dedup[(r["ts"] or "")[:10]] = {"date": (r["ts"] or "")[:10], "value": round(float(r["nav"]) / started_nav, 4)}
    history = list(dedup.values())

    # buy 配对索引(给 sell 补建仓时间 + 建仓催化剂，做"信息→决策→收益"因果带)
    buys_by_sym: dict[str, list] = {}
    for t in sorted(trades, key=lambda x: x["ts"]):
        if t["side"] == "buy":
            buys_by_sym.setdefault(t["symbol"], []).append(t)
    feed = []
    for t in trades:
        cref = _loadj(t["catalyst_ref"] if "catalyst_ref" in t.keys() else "", None)
        item = {"ts": t["ts"], "kind": "trade", "side": t["side"], "symbol": t["symbol"], "name": t["name"] or t["symbol"],
                "qty": float(t["qty"]), "price": round(float(t["price"]), 3),
                "pnl_pct": round(float(t["pnl_pct"]), 2) if t["pnl_pct"] is not None else None,
                "confidence": t["confidence"], "catalyst": t["catalyst"] or "", "buy_point": (t["buy_point"] if "buy_point" in t.keys() else "") or "",
                "narrative": t["narrative"] or "", "scores": _loadj(t["scores"] if "scores" in t.keys() else "", {}),
                "thinking": _loadj(t["thinking"], []), "reason": t["reason"],
                "catalyst_ref": cref, "composite": t["composite"] if "composite" in t.keys() else None,
                "debate": _loadj(debates.get(t["id"]), None)}  # 多空辩论推演(若该笔跑过)：多头/空头审视/裁判
        if t["side"] == "sell":  # 配对最近一笔更早的同股买入 → 因果带
            prior = [b for b in buys_by_sym.get(t["symbol"], []) if b["ts"] < t["ts"]]
            if prior:
                b = prior[-1]
                item["opened_at"] = b["ts"]
                item["buy_ref"] = _loadj(b["catalyst_ref"] if "catalyst_ref" in b.keys() else "", None)
                try:
                    dd = (datetime.fromisoformat(t["ts"].replace("Z", "+00:00")) - datetime.fromisoformat(b["ts"].replace("Z", "+00:00"))).total_seconds() / 86400
                    item["hold_days"] = round(dd, 1)
                except Exception:  # noqa: BLE001
                    pass
        feed.append(item)
    _wseen = set()
    for th in thoughts:  # thoughts 按 ts DESC，去重(同股同催化剂只留最新一条)、限量，避免刷屏
        wkey = (th["symbol"], (th["catalyst"] or "")[:18])
        if wkey in _wseen or len(_wseen) >= 6:
            continue
        _wseen.add(wkey)
        feed.append({"ts": th["ts"], "kind": "watch", "side": "watch", "symbol": th["symbol"], "name": th["name"] or th["symbol"],
                     "qty": 0, "price": None, "pnl_pct": None, "confidence": th["confidence"], "catalyst": th["catalyst"] or "",
                     "buy_point": "", "narrative": th["narrative"] or "", "scores": _loadj(th["scores"] if "scores" in th.keys() else "", {}),
                     "thinking": _loadj(th["thinking"], []), "reason": ""})
    feed.sort(key=lambda x: x["ts"] or "", reverse=True)
    feed = feed[:50]
    # 最近一笔有「多空辩论推演」的买入 → 顶层暴露，前端可直接做精选展示位(不必扫 feed)
    latest_debate = next(({"symbol": f["symbol"], "name": f["name"], "ts": f["ts"], "debate": f["debate"]}
                          for f in feed if f["kind"] == "trade" and f.get("debate")), None)

    pos_syms = {p["symbol"] for p in pos_out}
    # 默认展示「催化剂最丰富」的持仓，让本站快讯/研报/文章直接体现在 K 线上
    with_ev = [p for p in pos_out if p.get("events")]
    if with_ev:
        featured = max(with_ev, key=lambda p: len(p["events"]))["symbol"]
    else:
        featured = next((f["symbol"] for f in feed if f["kind"] == "trade" and f["symbol"] in pos_syms), None) \
            or (pos_out[0]["symbol"] if pos_out else None)
    mood = _mood(stats, nav_pct)
    if info_driven:
        stats["info_driven"] = info_driven
    bench = _benchmark(st["started_at"], nav_pct)
    risk = _risk_metrics(history, started_nav, (bench or {}).get("series"))
    try:
        days_running = (datetime.now(timezone.utc) - datetime.fromisoformat(st["started_at"].replace("Z", "+00:00"))).days + 1
    except Exception:  # noqa: BLE001
        days_running = 1
    decisions_like = [{"tid": "", "side": f["side"], "narrative": f["narrative"]} for f in feed if f["kind"] == "trade" and f["narrative"]]
    commentary = (decisions_like[0]["narrative"] if decisions_like else "") or _commentary([], {}, mood, len(pos_out), nav_pct, stats, cfg)

    if not ifind_enabled():
        dq = {"level": "degraded", "label": "等待行情接入", "detail": "iFinD A股实时行情未在本环境配置，模拟盘暂以成本价估值、暂停交易。", "reasons": ["ifind_unconfigured"]}
    elif degraded:
        dq = {"level": "degraded", "label": "部分盯市降级", "detail": "个别持仓实时取价失败，暂以成本价估值。", "reasons": ["quote_partial"]}
    else:
        dq = {"level": "live", "label": "实时", "detail": "iFinD 行情 + 东财日线/资金流。", "reasons": []}

    return {
        "fund_id": fund_id, "started_at": st["started_at"], "started_nav": started_nav,
        "persona": {"name": cfg.name, "tag": cfg.tag, "emoji": cfg.emoji, "style": cfg.style, "blurb": cfg.blurb},
        # 策略进化(真·调教 + 真·不同算法)：打分模型 + 自学权重漂移 + AI 教练自评
        "strategy": {"model": cfg.model, "model_label": _MODEL_CN.get(cfg.model, cfg.model),
                     "weight_drift": ai_fund_evolve.weights_drift(cfg.weights, _loadj(_k("learned_weights"), {})),
                     "coach_note": _k("coach_note"),
                     # 当前市场态势下该流派的动态打法(牛市加仓/熊市防守/恐慌捡便宜…)
                     "market_stance": ai_fund_evolve.regime_adapt(cfg.style, _market_regime_now().get("regime"))["stance"],
                     "regime": _market_regime_now().get("regime")},
        "mood": mood, "stats": stats,
        "nav": round(nav, 2), "nav_unit": round(nav / started_nav, 4) if started_nav else 1.0, "nav_pct": round(nav_pct, 2),
        "cash": round(cash, 2), "market_value": round(market_value, 2), "position_count": len(pos_out),
        "max_positions": cfg.max_positions, "last_tick_at": st["last_tick_at"], "commentary": commentary,
        "featured_symbol": featured, "positions": pos_out, "feed": feed, "trades": [f for f in feed if f["kind"] == "trade"],
        "history": history, "data_quality": dq,
        "benchmark": (bench or {}).get("series", []), "benchmark_name": (bench or {}).get("name", "沪深300"),
        "alpha_pct": (bench or {}).get("alpha"), "bench_ret": (bench or {}).get("bench_ret"),
        "risk": risk,
        "days_running": days_running, "decisions_total": decisions_total, "scan": scan, "wire": _latest_wire(),
        # 权威交易日/时段状态（前端据此显示开闭市，不再自行按星期推算→节假日不再误显「撮合中」）
        "is_trading_day": _is_trading_day(), "in_session": _in_session(), "phase": _phase()[0], "phase_label": _phase()[1],
        "musings": musings, "thinking_total": thinking_total,
        "memory": memory, "memory_stats": memory_stats, "discipline": discipline, "review": review,
        "roster": [{"fund_id": c.fund_id, "name": c.name, "emoji": c.emoji, "style": c.style, "blurb": c.blurb} for c in ROSTER],
        "latest_debate": latest_debate,
        "disclaimer": "AI 模拟盘为投研能力演示，使用虚拟资金、不接入任何券商、不构成投资建议；历史表现不代表未来收益。",
    }


def _agent_nav(conn, cfg: AgentConfig):
    """单智能体当前净值/持仓盯市（轻量，行情走 iFinD 缓存；取不到价用成本价）。失败→None。"""
    st = _state(conn, cfg.fund_id)
    if not st:
        return None
    cash = float(st["cash"]); started_nav = float(st["started_nav"])
    mv = 0.0; pc = 0
    for p in _positions(conn, cfg.fund_id):
        q = _quote(p["symbol"]) if ifind_enabled() else None
        price = safe_float(q.get("latest")) if q else None
        if price is None:
            price = float(p["avg_cost"])
        mv += float(p["qty"]) * price; pc += 1
    nav = cash + mv
    # 最大回撤(从净值序列的历史峰值回落最深处)：给排行榜一个『收益高不等于稳』的风险对照
    max_dd = None
    try:
        navs = [float(r["nav"]) for r in conn.execute(
            "SELECT nav FROM aif_nav WHERE fund_id=? ORDER BY ts ASC", (cfg.fund_id,)).fetchall()]
        navs.append(nav)
        if len(navs) >= 2:
            peak = navs[0]; worst = 0.0
            for v in navs:
                peak = max(peak, v)
                if peak > 0:
                    worst = min(worst, (v - peak) / peak)
            max_dd = round(worst * 100, 2)
    except Exception:  # noqa: BLE001
        max_dd = None
    return {"nav": nav, "started_nav": started_nav, "cash": cash,
            "market_value": mv, "position_count": pc, "started_at": st["started_at"],
            "last_tick_at": st["last_tick_at"], "max_drawdown_pct": max_dd}


# 各流派在排行榜上的主题色（前端卡片描边/高亮用 s.color）
_STYLE_COLOR = {"balanced": "#7c3aed", "aggressive": "#f6465d", "value": "#06b6d4",
                "event": "#f59e0b", "contrarian": "#16c784"}


def _arena_consensus(conn) -> dict:
    """跨智能体共识/分歧：把 5 个 AI 对同一只票的态度聚成集体信号——
    『多数 AI 在拿的票』(共识) + 『有人拿有人躲的票』(分歧,最有看点)。
    态度来源=当前持仓(强看多) + aif_memory 里每个 agent 对该股最新 thesis 的 lean(score)。纯数据,绝不抛出。"""
    agg: dict[str, dict] = {}

    def _slot(sym, name):
        return agg.setdefault(sym, {"symbol": sym, "name": name or sym, "holders": [], "stances": {}})

    try:
        # ① 持仓 = 实锤看多
        for cfg in ROSTER:
            for p in _positions(conn, cfg.fund_id):
                _slot(p["symbol"], p["name"])["holders"].append(cfg.fund_id)
        # ② 每个 agent 对每只票的最新观点(thesis)的 score → 看多/观望/回避
        rows = conn.execute("SELECT fund_id, symbol, name, detail FROM aif_memory "
                            "WHERE mem_type='thesis' ORDER BY updated_at DESC").fetchall()
        seen = set()
        for r in rows:
            key = (r["fund_id"], r["symbol"])
            if key in seen or r["fund_id"] not in ROSTER_BY_ID:
                continue
            seen.add(key)
            sc = (_loadj(r["detail"], {}) or {}).get("score")
            _slot(r["symbol"], r["name"])["stances"][r["fund_id"]] = sc
    except Exception:  # noqa: BLE001
        return {"consensus": [], "divergence": []}

    def _tag(fid):
        c = cfg_for(fid)
        return {"fund_id": fid, "name": c.name, "emoji": c.emoji}

    consensus, divergence = [], []
    for sym, d in agg.items():
        holders = set(d["holders"])
        bulls = set(holders) | {f for f, s in d["stances"].items() if s is not None and s > 0.12}
        bears = {f for f, s in d["stances"].items() if s is not None and s < -0.12} - holders
        if len(holders) >= 2:  # 共识：≥2 个 AI 同时持有
            consensus.append({"symbol": sym, "name": d["name"], "hold_count": len(holders),
                              "holders": [_tag(f) for f in d["holders"]], "agent_total": len(ROSTER)})
        if bulls and bears:    # 分歧：有人看多/持有，有人看空/回避
            divergence.append({"symbol": sym, "name": d["name"],
                               "bulls": [_tag(f) for f in bulls], "bears": [_tag(f) for f in bears],
                               "split": len(bulls) + len(bears)})
    consensus.sort(key=lambda c: c["hold_count"], reverse=True)
    divergence.sort(key=lambda c: c["split"], reverse=True)
    top_div = divergence[:5]
    _attach_divergence_takes(top_div)   # 只读缓存挂上多空对话(无则不挂)，零请求路径开销
    return {"consensus": consensus[:5], "divergence": top_div}


# --------------------------------------------------------------------------- #
# 赛马分歧票·多空对话（后台预生成 + 请求路径只读缓存）
#   分歧票=「有人拿有人躲」最有看点：给代表性的多头与空头各生成一句人设解说，
#   让用户看到『同一只票,不同打法的人怎么想』。生成只在后台 tick 后跑、缓存复用,
#   get_arena 永远只读缓存——绝不在公开轮询接口里烧 token / 拖延迟。
# --------------------------------------------------------------------------- #

def _divergence_fp(div_item: dict) -> str:
    """分歧票指纹=票 + 多空两阵营 fund_id 组合。同阵营组合不变就复用缓存,变了才重算。"""
    bulls = "+".join(sorted(f["fund_id"] for f in div_item.get("bulls", [])))
    bears = "+".join(sorted(f["fund_id"] for f in div_item.get("bears", [])))
    return hashlib.md5(f"{div_item.get('symbol')}|{bulls}|{bears}".encode("utf-8")).hexdigest()


def _attach_divergence_takes(divergence: list[dict]) -> None:
    """给分歧票挂上已缓存的多空对话(只读 data_store,零 LLM)。无缓存则不挂,前端自然降级。"""
    if not DIV_TAKES:
        return
    for d in divergence:
        try:
            cached = data_store.latest("aif_div", _divergence_fp(d), max_age_seconds=DIV_TAKES_TTL)
            if isinstance(cached, dict) and (cached.get("bull") or cached.get("bear")):
                d["debate"] = cached
        except Exception:  # noqa: BLE001
            continue


def _latest_thesis_title(conn, fund_id: str, symbol: str) -> str:
    """某 agent 对某股最新 thesis 的标题——给分歧解说当事实锚点(不让 LLM 臆造理由)。"""
    try:
        r = conn.execute("SELECT title FROM aif_memory WHERE fund_id=? AND symbol=? AND mem_type='thesis' "
                         "ORDER BY updated_at DESC LIMIT 1", (fund_id, symbol)).fetchone()
        return (r["title"] if r else "") or ""
    except Exception:  # noqa: BLE001
        return ""


def _gen_divergence_take(div_item: dict) -> Optional[dict]:
    """给一只分歧票生成多空对话：代表性多头 + 空头各一句人设解说(锚定各自 thesis、不臆造)。"""
    bulls = sorted((div_item.get("bulls") or []), key=lambda f: f["fund_id"])
    bears = sorted((div_item.get("bears") or []), key=lambda f: f["fund_id"])
    if not bulls or not bears:
        return None
    bf, rf = bulls[0]["fund_id"], bears[0]["fund_id"]
    bull_cfg, bear_cfg = cfg_for(bf), cfg_for(rf)
    sym = div_item.get("symbol"); name = div_item.get("name") or sym
    with _connect() as conn:
        bull_thesis = _latest_thesis_title(conn, bf, sym)
        bear_thesis = _latest_thesis_title(conn, rf, sym)
    prompt = (
        f"同一只 A 股【{name}({sym})】，DeepFocus 终端里两个虚拟操盘智能体看法相反，给他们各写一句"
        "第一人称、口语、有锐度的解说，让用户秒懂『同一只票为什么不同打法的人分歧』：\n"
        f"· 多头「{bull_cfg.name}」打法『{bull_cfg.blurb}』"
        f"{('，它的观点锚点：' + bull_thesis) if bull_thesis else ''}——讲它为什么看多/在拿；\n"
        f"· 空头「{bear_cfg.name}」打法『{bear_cfg.blurb}』"
        f"{('，它的观点锚点：' + bear_thesis) if bear_thesis else ''}——讲它为什么看空/在躲；\n"
        "要求：各自从自己打法出发讲明白分歧点,别编锚点里没有的数字,别喊『推荐买/卖』,每句≤40字。"
        '只返回 JSON：{"bull":"多头那句","bear":"空头那句"}。'
    )
    try:
        from .compliance import neutralize_text
        from .llm import CloudResearchLLM
        data = asyncio.run(CloudResearchLLM().complete_json(prompt, max_tokens=500, timeout_seconds=25))
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(data, dict):
        return None
    bull = neutralize_text(str(data.get("bull") or "")[:80]).strip()
    bear = neutralize_text(str(data.get("bear") or "")[:80]).strip()
    if not (bull or bear):
        return None
    return {"bull": bull, "bear": bear,
            "bull_agent": {"fund_id": bf, "name": bull_cfg.name, "emoji": bull_cfg.emoji},
            "bear_agent": {"fund_id": rf, "name": bear_cfg.name, "emoji": bear_cfg.emoji}}


def refresh_divergence_takes(max_stocks: int = DIV_TAKES_MAX_STOCKS) -> int:
    """后台预生成分歧票多空对话并缓存。只在后台 tick 之后调用(在线程里跑,容 asyncio.run)。
    同阵营组合已有新鲜缓存则跳过(不重复烧 token)。返回本轮新生成条数；任何异常都安全吞掉。"""
    if not (TONE and DIV_TAKES):
        return 0
    try:
        init_ai_fund_db()
        with _connect() as conn:
            div = _arena_consensus(conn).get("divergence", [])
    except Exception:  # noqa: BLE001
        return 0
    made = 0
    for d in div[:max_stocks]:
        try:
            fp = _divergence_fp(d)
            if isinstance(data_store.latest("aif_div", fp, max_age_seconds=DIV_TAKES_TTL), dict):
                continue
            take = _gen_divergence_take(d)
            if take:
                data_store.record("aif_div", fp, take)
                made += 1
        except Exception:  # noqa: BLE001
            continue
    return made


def _nav_history(conn, fund_id: str, started_nav: float, points: int = 40) -> list[dict]:
    """单账户归一化净值序列(value=nav/started_nav，起点~1.0)，按 BJ 日去重——给排行榜火花线用。"""
    rows = conn.execute("SELECT ts, nav FROM aif_nav WHERE fund_id=? ORDER BY ts ASC", (fund_id,)).fetchall()
    if not started_nav:
        return []
    dedup = {}
    for r in rows:
        dedup[(r["ts"] or "")[:10]] = {"date": (r["ts"] or "")[:10], "value": round(float(r["nav"]) / started_nav, 4)}
    return list(dedup.values())[-points:]


def get_arena() -> dict[str, Any]:
    """赛马排行榜：名单内全部智能体的精简战绩卡，按累计收益率排名 + 共享沪深300算超额。
    一次拉全场，给前端「冠军 + 排行榜」用。绝不抛出（单个 agent 出错只跳过它）。
    输出字段对齐前端竞技场契约：strategies[]（含 nav_unit/color/rank）+ benchmark{name,nav_pct}。"""
    init_ai_fund_db()
    cards: list[dict] = []
    main_bench: dict = {}
    with _connect() as conn:
        for cfg in ROSTER:
            try:
                nv = _agent_nav(conn, cfg)
                if not nv:
                    continue
                started_nav = nv["started_nav"]
                nav_pct = (nv["nav"] - started_nav) / started_nav * 100 if started_nav else 0.0
                stats = _stats(conn, cfg.fund_id)
                brake = _performance_brake(stats)
                review = _retrospective(conn, cfg.fund_id, stats, brake, cfg)
                mood = _mood(stats, nav_pct)
                bench = _benchmark(nv["started_at"], nav_pct) or {}
                if cfg.fund_id == FUND_ID:
                    main_bench = bench
                lt = conn.execute("SELECT side,name,symbol,pnl_pct,ts FROM aif_trade WHERE fund_id=? ORDER BY ts DESC LIMIT 1",
                                  (cfg.fund_id,)).fetchone()
                last_action = None
                if lt:
                    last_action = {"side": lt["side"], "name": lt["name"] or lt["symbol"],
                                   "pnl_pct": round(float(lt["pnl_pct"]), 2) if lt["pnl_pct"] is not None else None,
                                   "ts": lt["ts"]}
                try:
                    days = (datetime.now(timezone.utc) - datetime.fromisoformat(nv["started_at"].replace("Z", "+00:00"))).days + 1
                except Exception:  # noqa: BLE001
                    days = 1
                cards.append({
                    "fund_id": cfg.fund_id, "name": cfg.name, "emoji": cfg.emoji, "style": cfg.style,
                    "blurb": cfg.blurb, "tag": cfg.tag, "color": _STYLE_COLOR.get(cfg.style, "#7c3aed"),
                    "nav_pct": round(nav_pct, 2), "nav": round(nv["nav"], 2),
                    "nav_unit": round(nv["nav"] / started_nav, 4) if started_nav else 1.0,
                    "alpha_pct": bench.get("alpha"), "bench_ret": bench.get("bench_ret"),
                    "win_rate": stats.get("win_rate"), "win_streak": stats.get("win_streak", 0),
                    "max_drawdown_pct": nv.get("max_drawdown_pct"),  # 最大回撤：收益高≠稳的风险对照
                    "closed": stats.get("closed", 0), "position_count": nv["position_count"],
                    "max_positions": cfg.max_positions, "mood": mood, "days_running": days,
                    "brain_mode": brake["key"], "brain_mode_label": brake["label"],
                    "threshold_add": brake["thr_delta"], "size_mult": brake["size_mult"],
                    "reentry_cooldown_days": cfg.reentry_cooldown_days, "daily_buy_cap": cfg.max_daily_buys,
                    "review": review,
                    "last_action": last_action, "last_tick_at": nv["last_tick_at"], "is_main": cfg.fund_id == FUND_ID,
                    "history": _nav_history(conn, cfg.fund_id, started_nav),   # 归一化净值火花线
                    "commentary": _commentary([], {}, mood, nv["position_count"], nav_pct, stats, cfg),  # 每张卡各自的直播一句话
                    "model_label": _MODEL_CN.get(cfg.model, cfg.model),
                })
            except Exception:  # noqa: BLE001
                continue
    cards.sort(key=lambda c: c["nav_pct"], reverse=True)
    for i, c in enumerate(cards):
        c["rank"] = i + 1
    champion = cards[0]["fund_id"] if cards else None
    spread = round(cards[0]["nav_pct"] - cards[-1]["nav_pct"], 2) if len(cards) > 1 else None
    benchmark = {"name": "沪深300", "nav_pct": main_bench.get("bench_ret"), "history": main_bench.get("series", [])}
    try:
        with _connect() as conn:
            crowd = _arena_consensus(conn)   # 跨智能体共识/分歧（集体信号，赛马的独特看点）
    except Exception:  # noqa: BLE001
        crowd = {"consensus": [], "divergence": []}
    return {
        # `strategies` 为前端竞技场主键；`agents` 同物保留（后端/测试别名）
        "strategies": cards, "agents": cards, "champion": champion, "spread": spread,
        "consensus": crowd["consensus"], "divergence": crowd["divergence"],
        "benchmark": benchmark, "benchmark_name": "沪深300", "ready": True,
        "is_trading_day": _is_trading_day(), "in_session": _in_session(),
        "phase": _phase()[0], "phase_label": _phase()[1],
        "disclaimer": "多智能体 AI 模拟盘为投研能力演示，虚拟资金、不接券商、不构成投资建议；历史表现不代表未来收益。",
    }
