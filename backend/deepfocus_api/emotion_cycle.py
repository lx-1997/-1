from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional

import httpx

from .shared_utils import safe_float

"""AI 模拟盘·情绪周期层：把超短「情绪周期四阶段」打法规则化成确定性闸门(零 LLM、可单测)。

四阶段与操作映射(打法口径为公开的超短方法论,阈值按 A股常态校准、均为模块常量可调)：
- freeze  冰点试错：涨停少+连板高度被压 → 小仓试错、只做新题材首板、不追高位
- attack  发酵主升：涨停放量+高度打开+梯队完整+昨涨停溢价为正 → 重仓聚焦主线龙头
- benign  高潮分歧·良性：溢价未破+首板活口仍在 → 只低吸龙头、不接力跟风、仓位减半
- evil    高潮分歧·恶性：溢价转弱/龙头炸板+跌停升温 → 停止进攻、进入防守
- ebb     全面退潮：昨涨停溢价显著为负+跌停批量 → 空仓保命、停止一切买入
- neutral 温和/数据不足：不改打法(绝不因数据缺失误杀交易)

数据底座(公开市场事实,进程内缓存 15min,6 个 agent 的 tick 共享)：
- 东财涨停池 push2ex getTopicZTPool(生产可达,theme_navigation 同款)：涨停家数/连板数 lbc/炸板次数 zbc
- 同族跌停池 getTopicDTPool：跌停家数
- 昨日涨停今日溢价：上一交易日涨停池成分(按连板数取前 50)今日平均涨跌幅,经 quote_fn 取价
"""

_BJ = timezone(timedelta(hours=8))
_CYCLE_TTL = 900.0
_POOL_TIMEOUT = 12.0
_POOL_URL = ("https://push2ex.eastmoney.com/getTopic{kind}Pool"
             "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=500"
             "&sort=fbt%3Aasc&date={date}")
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"}

# —— 阈值(A股常态校准,均可调) ——
ZT_EBB_MAX = 22        # 涨停家数≤此值=情绪冰点区
ZT_ATTACK_MIN = 70     # 涨停家数≥此值且梯队完整=发酵主升
DT_EBB_MIN = 10        # 跌停≥此值=亏钱效应批量扩散
PREMIUM_EBB = -1.5     # 昨日涨停今日平均溢价≤此值=接力资金显著亏损
PREMIUM_SOFT = 0.2     # 溢价≤此值且封板质量恶化=分歧转恶
BROKE_EVIL = 0.9       # 池内炸板次数均值≥此值=封板质量恶化
BROKE_TOP_EVIL = 3     # 最高板个股炸板≥此值=龙头承接出问题
PREMIUM_SAMPLE = 50    # 溢价采样：昨日池按连板数取前 N(龙头权重最高,控制取价成本)

_STAGE_META = {
    "freeze": {"label": "冰点试错", "stance": "小仓试错：只做新题材首板，不追高位"},
    "attack": {"label": "发酵主升", "stance": "重仓聚焦主线龙头：半路/低吸/打板皆可"},
    "benign": {"label": "高潮分歧·良性", "stance": "只低吸龙头、不接力跟风，仓位减半"},
    "evil": {"label": "高潮分歧·恶性", "stance": "停止进攻：高位承接失败，转入防守"},
    "ebb": {"label": "全面退潮", "stance": "空仓保命：停止一切买入，持仓果断止损"},
    "neutral": {"label": "温和轮动", "stance": "常态打法，按各自流派规则执行"},
}

# stage → (是否禁买, 买入门槛增量, 仓位系数, 备注)。禁买=True 时买入闸门直接关闭(空仓也是交易)。
_CYCLE_GATE_DEFAULT = {
    "ebb": (False, 0.03, 0.7, "全面退潮：收紧仓位防守，仅保留最强逻辑"),
    "evil": (False, 0.02, 0.8, "高潮分歧转恶：防守为主，只看不动手"),
    "benign": (False, 0.0, 0.9, "高潮良性分歧：适度收缩，等分歧转一致"),
    "freeze": (False, 0.01, 0.85, "情绪冰点：降低仓位等待放量修复"),
    "attack": (False, 0.0, 1.0, "情绪温和向好"),
    "neutral": (False, 0.0, 1.0, ""),
}
_CYCLE_GATES: dict[str, dict[str, tuple[bool, float, float, str]]] = {
    # 情绪周期流派(逐流)：严格执行四阶段仓位纪律
    "cycle": {
        "ebb": (True, 0.20, 0.0, "全面退潮：亏钱效应扩散，空仓保命，停止一切买入"),
        "evil": (True, 0.10, 0.0, "高潮恶性分歧：龙头失去承接，停止进攻进入防守"),
        "benign": (False, 0.03, 0.5, "高潮良性分歧：只低吸龙头、绝不接力跟风，仓位减半"),
        "freeze": (False, 0.02, 0.5, "冰点试错：小仓试新主线首板，不追高位"),
        "attack": (False, -0.02, 1.25, "发酵主升：主线确立，聚焦龙头大胆吃肉"),
        "neutral": (False, 0.0, 1.0, ""),
    },
    # 激进/事件派：同为情绪敏感型，退潮与恶性分歧禁买，主升加成
    "aggressive": {
        "ebb": (True, 0.15, 0.0, "全面退潮：追高必被埋，停止买入"),
        "evil": (True, 0.08, 0.0, "高潮恶性分歧：连板梯队崩塌，停止进攻"),
        "benign": (False, 0.02, 0.6, "高潮良性分歧：只碰辨识度最高者，仓位收缩"),
        "freeze": (False, 0.02, 0.7, "情绪冰点：试错仓位减半"),
        "attack": (False, -0.02, 1.15, "发酵主升：赚钱效应扩散，顺势加码"),
        "neutral": (False, 0.0, 1.0, ""),
    },
    "event": {
        "ebb": (True, 0.15, 0.0, "全面退潮：催化剂全然失效，停止买入"),
        "evil": (True, 0.08, 0.0, "高潮恶性分歧：利好不复述，停止进攻"),
        "benign": (False, 0.02, 0.6, "高潮良性分歧：只做最强催化，仓位收缩"),
        "freeze": (False, 0.02, 0.7, "情绪冰点：催化易夭折，仓位减半"),
        "attack": (False, -0.02, 1.1, "发酵主升：催化扩散加速，顺势快打"),
        "neutral": (False, 0.0, 1.0, ""),
    },
    # 逆向派：退潮期的超跌反弹恰是其猎场，但只给极小仓位、做完即走
    "contrarian": {
        "ebb": (False, 0.03, 0.35, "退潮期：仅极小仓位博弈超跌反弹，不恋战"),
        "evil": (False, 0.02, 0.5, "恶性分歧：恐慌杀跌里等错杀机会，仓位减半"),
        "benign": (False, 0.0, 0.9, "高潮良性分歧：超跌票修复窗口收窄"),
        "freeze": (False, -0.01, 1.05, "情绪冰点：被错杀标的最多，逆向猎场"),
        "attack": (False, 0.02, 0.85, "发酵主升：超跌票稀少，谨慎出手"),
        "neutral": (False, 0.0, 1.0, ""),
    },
    # 价值派：不受情绪周期摆布，仅退潮期防流动性收缩
    "value": {
        "ebb": (False, 0.02, 0.8, "全面退潮：防流动性收缩，买入更挑剔"),
        "evil": (False, 0.0, 0.95, "高潮分歧：按估值锚不动摇"),
        "benign": (False, 0.0, 1.0, ""),
        "freeze": (False, -0.01, 1.05, "情绪冰点：便宜货更多，价值猎场"),
        "attack": (False, 0.02, 0.9, "情绪亢奋：估值变贵，更挑剔"),
        "neutral": (False, 0.0, 1.0, ""),
    },
}

_CACHE: dict[str, tuple[float, dict]] = {}


def _bj_today() -> str:
    return datetime.now(_BJ).strftime("%Y%m%d")


def _prev_trading_day(holidays: Optional[set] = None) -> Optional[str]:
    """上一交易日(北京日期 yyyymmdd)。周末排除；节假日表由调用方传入(ai_fund.CN_MARKET_HOLIDAYS)，
    未传时只排周末——节假日误判仅令溢价数据缺失，分类自动降级、不致错档。"""
    holidays = holidays or set()
    d = datetime.now(_BJ).date() - timedelta(days=1)
    for _ in range(10):
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") not in holidays:
            return d.strftime("%Y%m%d")
        d -= timedelta(days=1)
    return None


async def _fetch_pool(kind: str, date: str) -> list[dict]:
    """拉东财涨停/跌停池(kind∈ZT/DT)。失败返回空表(由上层判 collected=False 降级)。"""
    rows: list[dict] = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=_POOL_TIMEOUT, headers=_HEADERS) as client:
            r = await client.get(_POOL_URL.format(kind=kind, date=date))
        if r.status_code == 200:
            for it in ((r.json() or {}).get("data") or {}).get("pool") or []:
                code = str(it.get("c") or "").strip()
                if not code:
                    continue
                rows.append({"code": code,
                             "boards": int(it.get("lbc") or 0),   # 连板数
                             "broke": int(it.get("zbc") or 0)})   # 炸板次数
    except Exception:  # noqa: BLE001
        return []
    return rows


def _pool_metrics(rows: list[dict]) -> dict:
    boards = [r["boards"] for r in rows]
    top = max(boards) if boards else 0
    return {"max_boards": top,
            "high_boards": sum(1 for b in boards if b >= 3),
            "first_boards": sum(1 for b in boards if b <= 1),
            "broke_avg": round(sum(r["broke"] for r in rows) / len(rows), 2) if rows else 0.0,
            "broke_top": next((r["broke"] for r in rows if r["boards"] == top), 0)}


def _gather_metrics(holidays: Optional[set], quote_fn: Optional[Callable[[str], Optional[dict]]]) -> dict:
    today = _bj_today()

    async def _job():
        zt, dt = await asyncio.gather(_fetch_pool("ZT", today), _fetch_pool("DT", today))
        return zt, dt

    try:
        zt, dt = asyncio.run(_job())
    except Exception:  # noqa: BLE001
        return {"collected": False}
    if not zt:
        return {"collected": False}
    m: dict = {"collected": True, "zt_count": len(zt), "dt_count": len(dt), **_pool_metrics(zt)}
    prev = _prev_trading_day(holidays)
    if prev and prev != today and quote_fn:
        try:
            prev_rows = asyncio.run(_fetch_pool("ZT", prev))
        except Exception:  # noqa: BLE001
            prev_rows = []
        codes = [r["code"] for r in sorted(prev_rows, key=lambda x: -x["boards"])][:PREMIUM_SAMPLE]
        pct: list[float] = []
        for code in codes:
            try:
                v = safe_float(((quote_fn(code) or {}).get("changeRatio")))
                if v is not None:
                    pct.append(v)
            except Exception:  # noqa: BLE001
                continue
        if pct:
            m["premium"] = round(sum(pct) / len(pct), 2)
    return m


def classify(m: dict) -> dict:
    """情绪指标 → 四阶段判定。纯函数、有序规则、数据缺失一律 neutral(不误杀)。"""
    meta = dict(_STAGE_META["neutral"])
    out = {"stage": "neutral", "label": meta["label"], "stance": meta["stance"],
           "reasons": [], "metrics": {k: v for k, v in m.items() if k != "collected"}}
    if not m.get("collected") or m.get("zt_count") is None:
        out["reasons"] = ["情绪数据暂缺，维持常态打法(不因缺数据误杀交易)"]
        return out
    zt = int(m["zt_count"])
    dt = int(m.get("dt_count") or 0)
    mb = int(m.get("max_boards") or 0)
    high3 = int(m.get("high_boards") or 0)
    first1 = int(m.get("first_boards") or 0)
    bavg = float(m.get("broke_avg") or 0.0)
    btop = int(m.get("broke_top") or 0)
    prem = m.get("premium")
    reasons = [f"涨停 {zt} 家 / 跌停 {dt} 家", f"最高 {mb} 板（≥3板 {high3} 只、首板 {first1} 只）",
               f"封板炸板均值 {bavg:.1f} 次"]
    if prem is not None:
        reasons.append(f"昨涨停今溢价 {prem:+.1f}%")

    def _pick(stage: str) -> dict:
        meta = _STAGE_META[stage]
        return {"stage": stage, "label": meta["label"], "stance": meta["stance"],
                "reasons": reasons, "metrics": out["metrics"]}

    # 1) 全面退潮：接力资金显著亏损+跌停批量，或涨停冰点+跌停批量
    if (prem is not None and prem <= PREMIUM_EBB and dt >= 5) or (zt <= ZT_EBB_MAX and dt >= DT_EBB_MIN):
        return _pick("ebb")
    # 2) 冰点试错：涨停少+高度被压制(跌停未成批量)
    if zt <= ZT_EBB_MAX + 6 and mb <= 3:
        return _pick("freeze")
    # 3) 恶性分歧：溢价转弱+封板质量恶化，或龙头炸板+跌停升温
    if (prem is not None and prem <= PREMIUM_SOFT and bavg >= BROKE_EVIL and zt < 45) \
            or (mb >= 4 and btop >= BROKE_TOP_EVIL and dt >= 6):
        return _pick("evil")
    # 4) 发酵主升：涨停放量+高度打开+梯队完整+溢价为正+封板质量好
    if zt >= ZT_ATTACK_MIN and mb >= 5 and high3 >= 4 and (prem is None or prem >= 1.0) and bavg <= 0.6:
        return _pick("attack")
    # 5) 良性分歧：家数健康+首板活口仍在+溢价未破
    if zt >= 35 and first1 >= max(10, int(zt * 0.35)) and (prem is None or prem >= 0.0):
        return _pick("benign")
    return _pick("neutral")


def gate(style: str, stage: Optional[str]) -> dict:
    """流派 × 阶段 → 买入闸门动作。未知流派/阶段回退默认档(温和、绝不误封)。"""
    stage = stage if stage in _STAGE_META else "neutral"
    row = _CYCLE_GATES.get(style, {}).get(stage) or _CYCLE_GATE_DEFAULT[stage]
    blocked, thr_delta, size_mult, note = row
    return {"key": stage, "label": _STAGE_META[stage]["label"], "blocked": bool(blocked),
            "thr_delta": float(thr_delta), "size_mult": float(size_mult), "note": note or ""}


def current_cycle(holidays: Optional[set] = None,
                  quote_fn: Optional[Callable[[str], Optional[dict]]] = None) -> dict:
    """当前情绪周期(进程缓存 15min)。任何异常 → neutral,绝不抛出。"""
    hit = _CACHE.get("cycle")
    if hit and (time.time() - hit[0]) < _CYCLE_TTL:
        return hit[1]
    try:
        out = classify(_gather_metrics(holidays, quote_fn))
    except Exception:  # noqa: BLE001
        out = classify({"collected": False})
    _CACHE["cycle"] = (time.time(), out)
    return out
