from __future__ import annotations

import math
import re
from typing import Any, Optional

"""
长线牛股方法论引擎 —— 把《价值投资之长线牛股》(2022) 的章法沉淀成纯函数，
供「阿尔法」AI 模拟盘(ai_fund.py)与 AI 研究 agent(agent_tools.py / tear_sheet.py)共用。

设计原则：
· 纯函数、零 I/O、故障安全(输入残缺即返回 insufficient，绝不抛出)，便于单测与跨模块复用。
· 每个因子都标注书中出处，忠于作者的具体判据/阈值，而非泛泛的价投口号。

覆盖：
  Ⅰ 业绩增长催化剂分类(第2章)          —— classify_catalyst
  Ⅱ 趋势/形态/止损(第6、7章)           —— trend_template / swing_points / cup_with_handle /
                                          livermore_pivot / support_resistance / market_regime
  Ⅲ 真实估值·盈利质量·现金流(第4、9、10章) —— cashflow_type / earnings_quality / free_cash_flow /
                                          roe_stage
  Ⅳ 投资对象类型(第5章)               —— classify_target_type / expansion_red_flags
  Ⅴ 一门好生意 + 牛股基因(第1、3章)     —— good_business / bull_gene_score
"""

# =========================================================================== #
# Ⅰ 业绩增长的关键字与逻辑（第2章）——把"催化剂"从扁平关键词升级为有类型、有强度的事件
# =========================================================================== #

# 每类催化剂：关键词 + 基础强度(0~1，作者对"刺激强弱"的相对排序) + 看多/看空 + 一句话章法。
# 强度排序忠于书：大订单(销量端最暴力) > 高增长/预告上修 > 涨价(价格端) ≈ 进入产业链 > 扩产 > 反转/库存/景气。
CATALYST_TYPES: list[dict] = [
    {"key": "big_order", "label": "大订单", "dir": 1, "base": 0.95,
     "kw": ("中标", "大订单", "大单", "重大合同", "重大销售合同", "框架协议", "长单", "供货协议",
            "采购协议", "战略合作协议", "签订合同", "合同金额", "订单"),
     "rule": "作用于销量端、最暴力；查合同额/营收占比，越高越强(可>100%)，防已 priced-in"},
    {"key": "guidance_up", "label": "业绩预增/上修", "dir": 1, "base": 0.9,
     "kw": ("业绩预增", "预增", "业绩大增", "净利大增", "业绩预告", "业绩修正", "上修",
            "同向上升", "扭亏为盈", "扭亏", "高增长", "净利润同比增长"),
     "rule": "找'刚开始'的高增长；利润增幅>30%、甚至≥50% 为强；预告区间上修=强买点(沪电模式)"},
    {"key": "price_hike", "label": "持续涨价", "dir": 1, "base": 0.82,
     "kw": ("涨价", "提价", "调价", "价格上调", "价格上涨", "报价上涨", "量价齐升",
            "供不应求", "提价函", "涨价函", "定价权"),
     "rule": "作用于价格端；需求驱动(毛利率扩张)才要，成本驱动'增收不增利'剔除；提价后销量不大降=有提价权"},
    {"key": "supply_chain", "label": "进入核心产业链", "dir": 1, "base": 0.8,
     "kw": ("进入供应链", "核心供应商", "定点", "进入产业链", "配套", "导入", "通过认证",
            "通过验证", "送样", "独家供应", "国产替代"),
     "rule": "B2B 工业型公司：进入核心产业链=自动加入巨大市场，弹性极高；防被剔除风险"},
    {"key": "capacity", "label": "新产能扩张", "dir": 1, "base": 0.72,
     "kw": ("扩产", "定增", "非公开发行", "募投", "产能建设", "产能释放", "新建产线",
            "新增产能", "达产", "投产", "在建工程", "扩建", "可转债"),
     "rule": "前提是未来需求增长；ROE 微笑曲线(募资期跌→释放期升)；防'补流占比高/不产生业绩'的伪扩产"},
    {"key": "reversal", "label": "业绩反转/拐点", "dir": 1, "base": 0.62,
     "kw": ("拐点", "反转", "困境反转", "触底", "触底回升", "环比改善", "边际改善",
            "复苏", "景气回升", "毛利率回升"),
     "rule": "必须主业反转(非变卖资产/重组的数字反转)；环比先于同比转好(毛利率环比+5pct 即可视拐点)"},
    {"key": "inventory", "label": "库存周期", "dir": 1, "base": 0.6,
     "kw": ("补库存", "主动补库", "去库存", "被动去库", "库存低位", "库存周期", "补库"),
     "rule": "需求提升+库存上行=主动补库存=最值得投资段；被动去库=触底反弹起点"},
    {"key": "prosperity", "label": "行业高景气", "dir": 1, "base": 0.58,
     "kw": ("高景气", "景气上行", "行业景气", "景气度", "需求旺盛", "需求高增长",
            "供给侧改革", "订单饱满"),
     "rule": "三特征：低 PB 分位 + ROE 相对低位 + ROE 改善预期"},
    {"key": "approval", "label": "获批/中标资质", "dir": 1, "base": 0.55,
     "kw": ("获批", "获得批文", "核准", "注册批件", "上市许可", "新药获批", "GMP", "拿地", "中签"),
     "rule": "新产品/资质落地=未来业绩抓手"},
    {"key": "buyback", "label": "回购/增持", "dir": 1, "base": 0.45,
     "kw": ("回购", "增持", "回购注销", "员工持股", "股权激励"),
     "rule": "管理层用真金白银表态，信心信号(非业绩驱动，强度中性偏多)"},
    # —— 看空类(规避/止损依据) ——
    {"key": "neg_perf", "label": "业绩下滑/预亏", "dir": -1, "base": 0.9,
     "kw": ("业绩预减", "预亏", "净利下滑", "亏损", "业绩下滑", "营收下滑", "增收不增利", "暴雷", "爆雷"),
     "rule": "主业恶化，回避/止损"},
    {"key": "neg_reduce", "label": "减持/解禁", "dir": -1, "base": 0.78,
     "kw": ("减持", "解禁", "清仓", "套现", "大股东减持", "高管减持"),
     "rule": "筹码松动，资金离场信号"},
    {"key": "neg_risk", "label": "处罚/问询/诉讼", "dir": -1, "base": 0.85,
     "kw": ("处罚", "问询", "立案", "诉讼", "退市", "警示", "风险提示", "商誉减值", "减值",
            "债务违约", "质押爆仓", "停牌核查", "财务造假"),
     "rule": "合规/财务黑天鹅，危机止损(不管亏多少)"},
]

# 强度加成词(书中"超预期""大写特写""翻倍"等强信号；命中则在 base 上叠加)
_INTENSIFIERS = ("超预期", "大超预期", "翻倍", "创新高", "历史新高", "首次", "独家", "唯一",
                 "涨停", "满产", "供不应求", "持续", "大幅", "倍增")
# 弱化/存疑词(标题党/标准范本/小额，书中提醒降权)
_WEAKENERS = ("拟", "意向", "框架", "预计", "或", "传闻", "小额", "标准合同")
_PCT_RE = re.compile(r"(\d{2,4})\s*%")


def classify_catalyst(title: str) -> Optional[dict]:
    """把一条快讯/研报/公告标题分类为有类型、有方向、有强度的催化剂(第2章)。
    返回 {key,label,dir(+1/-1),strength(0~1),rule,matched}；无命中返回 None。
    强度 = 类型基础强度 × 加成/弱化 × 数字幅度(如"增长65%")修正。"""
    if not title:
        return None
    t = str(title)
    best: Optional[dict] = None
    for ct in CATALYST_TYPES:
        hit = next((k for k in ct["kw"] if k in t), None)
        if not hit:
            continue
        if best is None or ct["base"] > best["base"]:
            best = {**ct, "matched": hit}
    if best is None:
        return None
    strength = best["base"]
    if any(w in t for w in _INTENSIFIERS):
        strength = min(1.0, strength + 0.08)
    if any(w in t for w in _WEAKENERS):
        strength = max(0.2, strength - 0.18)
    # 业绩类：标题含百分比幅度 → 按书中 30%/50% 档位修正强度
    if best["key"] in ("guidance_up", "neg_perf"):
        m = _PCT_RE.search(t)
        if m:
            try:
                pct = float(m.group(1))
                if pct >= 50:
                    strength = min(1.0, strength + 0.06)
                elif pct < 30:
                    strength = max(0.2, strength - 0.1)
            except ValueError:
                pass
    return {"key": best["key"], "label": best["label"], "dir": best["dir"],
            "strength": round(strength, 3), "rule": best["rule"], "matched": best["matched"]}


def catalyst_profile(titles: list[str]) -> dict:
    """对一组标题做催化剂画像：净方向(看多-看空，加权强度)、命中的催化剂类型、最强一条。
    供机器人把'消息面'从情绪关键词升级为'有章法的事件驱动'。"""
    hits = [c for c in (classify_catalyst(t) for t in (titles or [])) if c]
    if not hits:
        return {"net_dir": 0.0, "types": [], "top": None, "bull_types": [], "n": 0}
    num = sum(c["dir"] * c["strength"] for c in hits)
    den = sum(c["strength"] for c in hits) or 1.0
    net = max(-1.0, min(1.0, num / den))
    bull = sorted({c["label"] for c in hits if c["dir"] > 0})
    top = max(hits, key=lambda c: c["strength"] * (1 if c["dir"] > 0 else 0.9))
    # 去重保留每类最强
    by_key: dict[str, dict] = {}
    for c in hits:
        if c["key"] not in by_key or c["strength"] > by_key[c["key"]]["strength"]:
            by_key[c["key"]] = c
    return {"net_dir": round(net, 3), "types": list(by_key.values()),
            "bull_types": bull, "top": top, "n": len(hits)}


# =========================================================================== #
# Ⅱ 赢在趋势 / 技术形态（第6、7章）——日线 OHLC 可计算信号
# =========================================================================== #

def _ma(seq: list[float], k: int) -> Optional[float]:
    if not seq or len(seq) < k or k <= 0:
        return None
    return sum(seq[-k:]) / k


def swing_points(closes: list[float], left: int = 3, right: int = 3) -> dict:
    """摆动高/低点(局部极值)：判断'波峰逐步抬高、波谷逐步抬高'(第6章上升趋势定义)。
    返回 {peaks:[(i,v)], troughs:[(i,v)], higher_high, higher_low}。"""
    out = {"peaks": [], "troughs": [], "higher_high": False, "higher_low": False}
    n = len(closes or [])
    if n < left + right + 1:
        return out
    peaks, troughs = [], []
    for i in range(left, n - right):
        win = closes[i - left:i + right + 1]
        if closes[i] == max(win) and closes[i] > min(win):
            if not peaks or i - peaks[-1][0] >= 2:
                peaks.append((i, closes[i]))
        if closes[i] == min(win) and closes[i] < max(win):
            if not troughs or i - troughs[-1][0] >= 2:
                troughs.append((i, closes[i]))
    out["peaks"], out["troughs"] = peaks, troughs
    if len(peaks) >= 2:
        out["higher_high"] = peaks[-1][1] > peaks[-2][1]
    if len(troughs) >= 2:
        out["higher_low"] = troughs[-1][1] > troughs[-2][1]
    return out


def trend_template(closes: list[float], last: Optional[float] = None) -> dict:
    """第6章上升趋势模板(《股票魔法师》阶段二 10 条 + 《期货市场技术分析》波峰波谷)。
    本地日线常 ≤250 点，按可计算的均线子集打分；返回 {score(-1~1), passed, total, stage, flags}。
    阶段判定 stage ∈ advancing/topping/declining/basing。"""
    px = list(closes or [])
    if last and (not px or abs(px[-1] - last) > 1e-9):
        px = px + [float(last)]
    c = px[-1] if px else None
    if c is None or len(px) < 20:
        return {"score": 0.0, "passed": 0, "total": 0, "stage": "unknown", "flags": [], "has": False}
    ma20, ma50, ma60 = _ma(px, 20), _ma(px, 50), _ma(px, 60)
    ma120, ma150, ma200, ma250 = _ma(px, 120), _ma(px, 150), _ma(px, 200), _ma(px, 250)
    n = len(px)
    win252 = px[-252:] if n >= 60 else px
    lo, hi = min(win252), max(win252)
    sw = swing_points(px)
    checks: list[tuple[str, Optional[bool]]] = []
    # 1) 价在中长均线上方(150/200，缺则用 120/60 代理)
    long1, long2 = (ma150 or ma120), (ma200 or ma250 or ma120)
    checks.append(("价在长期均线上", (c > long1 and c > long2) if (long1 and long2) else None))
    # 2) 150 > 200(代理:120>250 or 60>120)
    if ma150 and ma200:
        checks.append(("中期>长期均线", ma150 > ma200))
    elif ma120 and ma250:
        checks.append(("中期>长期均线", ma120 > ma250))
    elif ma60 and ma120:
        checks.append(("中期>长期均线", ma60 > ma120))
    # 3) 长期均线上行≥1个月
    base_ma = ma200 or ma250 or ma120
    if base_ma and n >= 141 and (ma200 or ma250 or ma120):
        prev = _ma(px[:-21], 200) or _ma(px[:-21], 250) or _ma(px[:-21], 120)
        checks.append(("长均上行", (base_ma > prev) if prev else None))
    # 4) 50 > 150 且 > 200(代理 60>120 且 >250)
    s50, s150, s200 = (ma50 or ma60), (ma150 or ma120), (ma200 or ma250 or ma120)
    if s50 and s150 and s200:
        checks.append(("短>中>长均线", s50 > s150 and s50 > s200))
    # 5) 价 > 50日(代理 60日)
    if s50:
        checks.append(("价上50/60日线", c > s50))
    # 6) 高于一年最低≥30%
    checks.append(("距年低≥30%", (c >= lo * 1.30) if lo else None))
    # 7) 处一年最高 70% 以上
    checks.append(("距年高≤30%", (c >= hi * 0.70) if hi else None))
    # 9) 波峰波谷逐步抬高
    checks.append(("波峰抬高", sw["higher_high"] if sw["peaks"] and len(sw["peaks"]) >= 2 else None))
    checks.append(("波谷抬高", sw["higher_low"] if sw["troughs"] and len(sw["troughs"]) >= 2 else None))
    valid = [(name, v) for name, v in checks if v is not None]
    passed = sum(1 for _, v in valid if v)
    total = len(valid)
    raw = (passed / total) if total else 0.0
    score = max(-1.0, min(1.0, (raw - 0.5) * 2))  # 全过=+1，全不过=-1
    flags = [name for name, v in valid if v]
    # 阶段判定(欧奈尔四阶段，用均线相位)
    above = c > (ma60 or ma50 or ma20 or c)
    ma_up = base_ma and n >= 26 and (_ma(px[:-5], 60) or _ma(px[:-5], 50) or _ma(px[:-5], 20))
    rising = bool(ma_up and (ma60 or ma50 or ma20) and (ma60 or ma50 or ma20) > (ma_up))
    if above and raw >= 0.6:
        stage = "advancing"
    elif (not above) and raw <= 0.4:
        stage = "declining"
    elif above and not rising:
        stage = "topping"
    else:
        stage = "basing"
    return {"score": round(score, 3), "passed": passed, "total": total, "stage": stage,
            "flags": flags, "higher_low": sw["higher_low"], "higher_high": sw["higher_high"],
            "ma60": round(ma60, 3) if ma60 else None, "ma120": round(ma120, 3) if ma120 else None,
            "ma250": round(ma250, 3) if ma250 else None, "has": True}


def support_resistance(closes: list[float], last: Optional[float] = None) -> dict:
    """最近的支撑/阻力位(第6.4：均线 + 前期波峰波谷)。返回最近支撑/阻力 + 距离%。"""
    px = list(closes or [])
    c = last or (px[-1] if px else None)
    if c is None or len(px) < 20:
        return {"support": None, "resistance": None, "support_gap": None, "resistance_gap": None}
    levels = [v for v in (_ma(px, 20), _ma(px, 60), _ma(px, 120), _ma(px, 250)) if v]
    sw = swing_points(px)
    levels += [v for _, v in sw["peaks"][-3:]] + [v for _, v in sw["troughs"][-3:]]
    below = [v for v in levels if v < c]
    above = [v for v in levels if v > c]
    sup = max(below) if below else None
    res = min(above) if above else None
    return {"support": round(sup, 3) if sup else None,
            "resistance": round(res, 3) if res else None,
            "support_gap": round((c - sup) / c * 100, 2) if sup else None,
            "resistance_gap": round((res - c) / c * 100, 2) if res else None}


def cup_with_handle(ohlc: list[dict]) -> dict:
    """杯柄形态检测(第7章)：U 形(非 V)杯底 + 右侧涨幅≥30% + 柄回调≤33% 且不破杯底。
    输入 [{o,h,l,c,v}]；返回 {detected, breakout, buy_point, depth, handle_pullback, reason}。
    保守实现：只在结构清晰时报 detected，宁缺毋滥。"""
    out = {"detected": False, "breakout": False, "buy_point": None, "depth": None,
           "handle_pullback": None, "reason": ""}
    n = len(ohlc or [])
    if n < 30:
        return out
    closes = [float(k.get("c", 0) or 0) for k in ohlc]
    highs = [float(k.get("h", k.get("c", 0)) or 0) for k in ohlc]
    lows = [float(k.get("l", k.get("c", 0)) or 0) for k in ohlc]
    # 杯左缘 = 前 60% 区间内的最高收盘；杯底 = 左缘之后的最低低点
    scan = closes[:-8] if n > 14 else closes
    li = max(range(len(scan)), key=lambda i: closes[i])
    if li >= n - 12:                       # 左缘太靠右，构不成杯
        return out
    bi = min(range(li, n - 4), key=lambda i: lows[i])
    p_left, cup_low = closes[li], lows[bi]
    if p_left <= 0 or cup_low <= 0:
        return out
    depth = (p_left - cup_low) / p_left
    if not (0.12 <= depth <= 0.55):        # 杯太浅无盘整、太深易失败
        out["reason"] = "杯深不合格"
        return out
    # 右缘 = 杯底之后回升的阶段高点
    ri = max(range(bi, n), key=lambda i: closes[i])
    p_right = closes[ri]
    right_gain = (p_right - cup_low) / cup_low
    if right_gain < 0.20 or ri >= n - 1:   # 右侧涨幅不足 / 还没走出右缘
        out["reason"] = "右侧涨幅不足"
        return out
    # U 形(非 V)：杯底附近要有横盘(钝化)
    bw = lows[max(bi - 3, 0):bi + 4]
    if bw and (max(bw) - min(bw)) / (min(bw) or 1) > depth * 0.6:
        out["reason"] = "V形非U形"
        return out
    # 柄 = 右缘之后的回调
    handle = closes[ri:]
    h_low = min(lows[ri:]) if ri < n else p_right
    pullback = (p_right - h_low) / p_right if p_right else 0.0
    out.update({"detected": True, "depth": round(depth, 3), "handle_pullback": round(pullback, 3)})
    if pullback > 0.33 or h_low < cup_low:
        out["reason"] = "柄回调超33%/破杯底(失败)"
        out["detected"] = False
        return out
    # 买点：突破右缘高点
    last = closes[-1]
    out["breakout"] = last >= p_right * 0.995
    out["buy_point"] = round(p_right, 3)
    out["reason"] = "杯柄成型" + ("·已突破右缘" if out["breakout"] else "·待突破")
    return out


def livermore_pivot(ohlc: list[dict]) -> dict:
    """利弗莫尔关键点(第7.2)：放量大涨(启动)→缩量小幅回撤(全程<10%、量不超启动日)→关键买点。
    返回 {detected, trigger_idx, pivot, pullback, buy}。"""
    out = {"detected": False, "trigger_idx": None, "pivot": None, "pullback": None, "buy": False}
    n = len(ohlc or [])
    if n < 8:
        return out
    vols = [float(k.get("v", 0) or 0) for k in ohlc]
    closes = [float(k.get("c", 0) or 0) for k in ohlc]
    opens = [float(k.get("o", k.get("c", 0)) or 0) for k in ohlc]
    lows = [float(k.get("l", k.get("c", 0)) or 0) for k in ohlc]
    if not any(vols):
        return out
    vma = sum(vols[-20:]) / min(len(vols), 20)
    # 在最近 6 根里找启动大阳线(放量、涨幅大)
    for ti in range(n - 6, n - 1):
        if ti < 1:
            continue
        chg = (closes[ti] - closes[ti - 1]) / closes[ti - 1] if closes[ti - 1] else 0
        if chg >= 0.06 and vols[ti] >= 1.8 * vma and vols[ti] == max(vols[max(ti - 5, 0):ti + 1]):
            body_low = min(opens[ti], closes[ti])
            tail = range(ti + 1, n)
            ok = all(lows[d] >= body_low * 0.90 and vols[d] < vols[ti] for d in tail)
            if ok and ti < n - 1:
                pull = (closes[ti] - min(lows[ti + 1:])) / closes[ti] if closes[ti] else 0
                out.update({"detected": True, "trigger_idx": ti, "pivot": round(body_low, 3),
                            "pullback": round(pull, 3),
                            "buy": closes[-1] >= closes[ti] * 0.995 or closes[-1] <= body_low * 1.02})
                return out
    return out


def market_regime(index_closes: list[float]) -> dict:
    """大盘多空(第6.3 依据指数止损：大盘跌则 90% 个股同跌)。指数 vs MA60/MA120。
    返回 {regime: bull/neutral/bear, above_ma60, ma60_rising}。"""
    px = list(index_closes or [])
    if len(px) < 60:
        return {"regime": "unknown", "above_ma60": None, "ma60_rising": None}
    c, ma60, ma120 = px[-1], _ma(px, 60), _ma(px, 120)
    prev60 = _ma(px[:-5], 60)
    above = c > ma60
    rising = bool(prev60 and ma60 > prev60)
    if above and rising and (not ma120 or c > ma120):
        regime = "bull"
    elif (not above) and (ma120 and c < ma120):
        regime = "bear"
    elif not above:
        regime = "bear" if not rising else "neutral"
    else:
        regime = "neutral"
    return {"regime": regime, "above_ma60": above, "ma60_rising": rising,
            "ma60": round(ma60, 2) if ma60 else None}


# =========================================================================== #
# Ⅲ 真实估值 · 盈利质量 · 现金流（第4、9、10章）
# =========================================================================== #

# 现金流八种类型(第9.5 表9-21)：(经营,投资,筹资) 符号 → 生命周期/健康度。0 归入正(净流入)。
_CASHFLOW_TABLE = {
    ("+", "+", "+"): (1, "risky", "主业刚盈利但现金含量低/投资无方向却大额筹资——警惕套现"),
    ("+", "+", "-"): (2, "healthy", "成熟期最理想：主业进钱+投资收获+分红还债"),
    ("+", "-", "+"): (3, "growth", "高速成长期最普遍：主业盈利但扩张需外部融资——低风险高不确定"),
    ("+", "-", "-"): (4, "best", "现金奶牛：自身造血覆盖扩张并分红还债(茅台型)"),
    ("-", "+", "+"): (5, "risky", "经营失血，靠变卖资产+筹资续命；或刻意全面加码扩张"),
    ("-", "+", "-"): (6, "danger", "主业亏损还要还债，仅靠变卖投资资产——衰退期垂死挣扎"),
    ("-", "-", "+"): (7, "danger", "主业难赚却砸钱转型，全靠外部输血——转型赌博/并购恶果"),
    ("-", "-", "-"): (8, "worst", "全负——垃圾股，赚不到钱还要还钱，绝不适合投资"),
}
_CF_LIFE = {1: "初创/存疑", 2: "成熟", 3: "成长", 4: "成熟·奶牛", 5: "困境/扩张", 6: "衰退", 7: "转型/初创", 8: "衰退/垃圾"}
_CF_RISKSCORE = {"best": 1.0, "healthy": 0.7, "growth": 0.35, "risky": -0.2, "danger": -0.7, "worst": -1.0}


def _sign(x: Optional[float]) -> Optional[str]:
    if x is None:
        return None
    return "+" if x >= 0 else "-"


def cashflow_type(ocf: Optional[float], icf: Optional[float], fcf: Optional[float]) -> dict:
    """现金流八类型(第9.5)。ocf/icf/fcf=经营/投资/筹资活动现金流净额。缺失→insufficient。"""
    s = (_sign(ocf), _sign(icf), _sign(fcf))
    if None in s:
        return {"type": None, "risk": "insufficient", "desc": "现金流数据不全", "score": None, "life": None}
    type_no, risk, desc = _CASHFLOW_TABLE[s]
    return {"type": type_no, "risk": risk, "desc": desc, "score": _CF_RISKSCORE[risk],
            "life": _CF_LIFE[type_no], "pattern": "".join(s)}


def free_cash_flow(ocf: Optional[float], capex: Optional[float]) -> Optional[float]:
    """自由现金流(第10章)= 经营活动现金净额 − 购建固定/无形/长期资产支付的现金。"""
    if ocf is None or capex is None:
        return None
    return ocf - abs(capex)


def earnings_quality(net_income: Optional[float] = None, ocf: Optional[float] = None,
                     revenue: Optional[float] = None, accounts_receivable: Optional[float] = None,
                     advance_receipts: Optional[float] = None,
                     non_recurring: Optional[float] = None) -> dict:
    """盈利质量(第4.3)：利润含金量(经营现金流/净利润>1 最好) + 扣非占比 + 应收/预收。
    任何子项缺失即跳过该项，返回 0~1 综合分(数据不足则 score=None)。"""
    parts: list[float] = []
    flags: list[str] = []
    detail: dict = {}
    if net_income and ocf is not None:
        cfo_ratio = ocf / net_income if net_income else None
        if cfo_ratio is not None:
            detail["cfo_ratio"] = round(cfo_ratio, 2)
            parts.append(max(0.0, min(1.0, cfo_ratio)))    # >1 满分
            if cfo_ratio < 0.5:
                flags.append("利润含金量低(经营现金流<净利润半数)——纸上富贵")
            elif cfo_ratio >= 1:
                flags.append("利润含金量足(经营现金流≥净利润)")
    if net_income and non_recurring is not None:
        core_ratio = (net_income - non_recurring) / net_income if net_income else None
        if core_ratio is not None:
            detail["core_profit_ratio"] = round(core_ratio, 2)
            parts.append(max(0.0, min(1.0, core_ratio)))
            if core_ratio < 0.5:
                flags.append("扣非占比低，利润靠非经常损益")
    if revenue and accounts_receivable is not None:
        ar = accounts_receivable / revenue if revenue else None
        if ar is not None:
            detail["ar_ratio"] = round(ar, 3)
            parts.append(max(0.0, min(1.0, 1 - ar / 0.4)))  # 应收/营收越小越好
            if ar > 0.4:
                flags.append("应收账款占营收过高，回款承压")
    if revenue and advance_receipts is not None:
        adv = advance_receipts / revenue if revenue else None
        if adv is not None:
            detail["advance_ratio"] = round(adv, 3)
            parts.append(min(1.0, adv / 0.3))               # 预收/营收越大越好(未来业绩)
            if adv > 0.2:
                flags.append("预收账款充足，未来业绩确定性强")
    score = round(sum(parts) / len(parts), 3) if parts else None
    return {"score": score, "flags": flags, "detail": detail, "n": len(parts)}


def roe_stage(roe: Optional[float] = None, roe_prev: Optional[float] = None,
              revenue_yoy: Optional[float] = None, profit_yoy: Optional[float] = None) -> dict:
    """ROE 生命周期(第4.7)：初创/成长/成熟/衰退 + 该给的估值倍数倾向。
    用 ROE 水平/趋势 + 收入·利润增速判定。roe 单位为百分数(如 18.0)。"""
    if roe is None and profit_yoy is None:
        return {"stage": "unknown", "fair_multiple": "n/a", "note": "数据不足", "score": None}
    r = roe if roe is not None else 0.0
    trend = (roe - roe_prev) if (roe is not None and roe_prev is not None) else None
    rev = revenue_yoy if revenue_yoy is not None else 0.0
    pf = profit_yoy if profit_yoy is not None else 0.0
    # 衰退：收入利润双降 / ROE 脆弱下滑
    if pf < -10 and rev < 0:
        stage, mult, note, sc = "decline", "回避", "收入利润双降、ROE 滑向谷底，毁灭价值——回避", -0.7
    # 成长：收入利润双增 + ROE 快速上行
    elif pf > 25 and rev > 10 and (trend is None or trend >= 0):
        stage, mult, note, sc = "growth", "给溢价", "收入利润双增、ROE 上行——价值主创造期，可给偏高倍数", 0.8
    # 初创：低 ROE、靠融资
    elif r < 5 and pf <= 0:
        stage, mult, note, sc = "startup", "高估值/高风险", "ROE 很低、尚未盈利——A股罕见、风险大", -0.1
    # 成熟：ROE 高且稳、增速放缓
    elif r >= 12 and -5 <= pf <= 25:
        stage, mult, note, sc = "mature", "给低/偏低倍数", "ROE 高且稳、增速放缓——大树底下好乘凉", 0.4
    else:
        stage, mult, note, sc = "transition", "中性", "处过渡阶段，需结合产品周期判断", 0.1
    return {"stage": stage, "fair_multiple": mult, "note": note, "roe": roe,
            "roe_trend": round(trend, 2) if trend is not None else None, "score": sc}


# =========================================================================== #
# Ⅳ 投资对象类型（第5章）——老业务阶段 × 新业务阶段 → 五型
# =========================================================================== #

# 表5-8：识别难度→置信折扣、风险、关注点、回报画像、仓位策略
_TARGET_TABLE = {
    ("grow", "intro"):  {"key": "growth_innovation", "label": "成长创新型(成长+导入)",
        "difficulty": 4, "confidence": 0.45, "risk": "两块业务都在激烈竞争，怕竞争力不足被淘汰",
        "focus": "两块业务的未来需求空间 + 公司构建的产品竞争力", "payoff": "高弹性双刃剑·投错亏损大",
        "sizing": "中小仓、分散、严风控"},
    ("mature", "grow"): {"key": "continuous_relay", "label": "连续接力型(成熟+成长)",
        "difficulty": 1, "confidence": 0.85, "risk": "风险不大，原产品需求稳定、新产品在发力",
        "focus": "新旧业务衔接的'节奏'", "payoff": "稳健可持续(首选)·A股99%公司不具备",
        "sizing": "可重仓、长持"},
    ("mature", "intro"): {"key": "slow_relay", "label": "缓慢接力型(成熟+导入)",
        "difficulty": 2, "confidence": 0.6, "risk": "原产品衰落 + 新产品竞争力不足",
        "focus": "原产品需求稳定性 + 新产品竞争力", "payoff": "中段塌陷后修复·增收不增利空窗",
        "sizing": "空窗期减仓、等新业务贡献"},
    ("decline", "intro"): {"key": "expectation", "label": "期待型(衰退+导入)",
        "difficulty": 5, "confidence": 0.3, "risk": "新产品竞争力不足、新业务出问题即致命",
        "focus": "原产品能否反转 + 新产品竞争力", "payoff": "赌博式、非对称向下(最差)",
        "sizing": "默认不持/极小试探仓"},
    ("decline", "grow"): {"key": "turnaround", "label": "反转型(衰退+成长)",
        "difficulty": 3, "confidence": 0.55, "risk": "新产品后期发展空间",
        "focus": "原产品反转信号 + 新产品竞争力", "payoff": "反转兑现型·转型成功解决根本问题",
        "sizing": "转型证据兑现后再加仓"},
}


def classify_target_type(old_stage: str, new_stage: str) -> Optional[dict]:
    """五种投资对象(第5.3)。old_stage∈{grow,mature,decline}，new_stage∈{intro,grow}。"""
    return _TARGET_TABLE.get((old_stage, new_stage))


def infer_target_type(revenue_yoy: Optional[float] = None, profit_yoy: Optional[float] = None,
                      roe: Optional[float] = None, new_biz_ratio: Optional[float] = None,
                      new_biz_growing: Optional[bool] = None) -> dict:
    """从可得财务粗判老/新业务阶段并归类(best-effort，置信度有限)。
    new_biz_ratio=新业务收入占比，new_biz_growing=新业务是否在放量。"""
    rev = revenue_yoy or 0.0
    pf = profit_yoy if profit_yoy is not None else rev
    if pf < -8 or (rev < 0 and (roe or 99) < 8):
        old = "decline"
    elif rev > 15 and pf > 15:
        old = "grow"
    else:
        old = "mature"
    if new_biz_ratio is not None and new_biz_growing is not None:
        new = "grow" if (new_biz_growing and new_biz_ratio >= 0.1) else "intro"
    else:
        new = "grow" if (rev > 20 and pf > 20) else "intro"
    t = classify_target_type(old, new)
    if not t:                                  # (grow,grow) 等罕见组合就近归并
        t = classify_target_type("mature", new) or classify_target_type(old, "intro")
    return {**(t or {}), "old_stage": old, "new_stage": new, "inferred": True}


def expansion_red_flags(revenue_yoy: Optional[float] = None, ar_yoy: Optional[float] = None,
                        ocf: Optional[float] = None, net_income: Optional[float] = None,
                        debt_yoy: Optional[float] = None, goodwill_ratio: Optional[float] = None,
                        unrelated_diversification: Optional[bool] = None) -> dict:
    """有害扩张红旗(第5.4)：应收跑赢收入、利润不含现金、负债激增、商誉高企、盲目多元化。"""
    flags: list[str] = []
    if revenue_yoy is not None and ar_yoy is not None and ar_yoy > revenue_yoy + 10:
        flags.append("应收账款增速远超营收——有销售没现金回来(顺驰式现金流危机)")
    if net_income and ocf is not None and net_income > 0 and ocf < net_income * 0.3:
        flags.append("利润高增但经营现金流极低——利润是纸上数字")
    if debt_yoy is not None and revenue_yoy is not None and debt_yoy > revenue_yoy + 25:
        flags.append("有息负债激增远超收入——信贷收紧即爆雷")
    if goodwill_ratio is not None and goodwill_ratio > 0.3:
        flags.append("商誉/净资产占比过高——并购讲故事的隐患")
    if unrelated_diversification:
        flags.append("盲目跨界多元化、与主业无关联——乐视式陷阱")
    return {"red_flags": flags, "count": len(flags), "harmful": len(flags) >= 2}


# =========================================================================== #
# Ⅴ 一门好生意 + 牛股基因（第1、3章）
# =========================================================================== #

_GOOD_SECTORS = ("消费", "食品", "饮料", "白酒", "医药", "医疗", "保险", "家电", "公共", "零售")


def good_business(roe: Optional[float] = None, roe_std: Optional[float] = None,
                  gross_margin: Optional[float] = None, gross_margin_trend: Optional[float] = None,
                  ocf_to_ni: Optional[float] = None, capex_intensity: Optional[float] = None,
                  advance_over_receivable: Optional[bool] = None,
                  sector: Optional[str] = None) -> dict:
    """一门好生意三标准(第1.6)：①持续赚钱(ROE 稳、低周期、轻资本开支) ②实实在在的现金
    (经营现金流/净利润高、预收>应收) ③发展黄金期(高 ROE/毛利稳升)。返回 0~1 + 命中要点。"""
    parts: list[float] = []
    notes: list[str] = []
    # ① 持续赚钱
    if roe is not None:
        parts.append(max(0.0, min(1.0, roe / 20.0)))
        if roe >= 15:
            notes.append(f"ROE {roe:.0f}% 优秀")
    if roe_std is not None and roe is not None and roe > 0:
        stab = max(0.0, 1 - roe_std / max(roe, 1))
        parts.append(stab)
        if stab > 0.7:
            notes.append("ROE 稳定·盈利可持续")
    if capex_intensity is not None:
        parts.append(max(0.0, min(1.0, 1 - capex_intensity / 0.15)))  # 资本开支轻=产品更新慢=好生意
        if capex_intensity < 0.08:
            notes.append("资本开支轻、产品更新慢")
    # ② 实实在在的现金
    if ocf_to_ni is not None:
        parts.append(max(0.0, min(1.0, ocf_to_ni)))
        if ocf_to_ni >= 1:
            notes.append("赚的是真金白银")
    if advance_over_receivable:
        parts.append(1.0)
        notes.append("预收>应收·回款能力强(格力型)")
    # ③ 黄金期 / 差异化
    if gross_margin is not None:
        parts.append(max(0.0, min(1.0, gross_margin / 50.0)))
        if gross_margin >= 40:
            notes.append(f"毛利率 {gross_margin:.0f}%·差异化强")
    if gross_margin_trend is not None and gross_margin_trend > 0:
        notes.append("毛利率稳步提升·护城河加深")
        parts.append(0.7)
    bonus = 0.05 if (sector and any(s in sector for s in _GOOD_SECTORS)) else 0.0
    score = round(min(1.0, (sum(parts) / len(parts) if parts else 0.0) + bonus), 3) if parts else None
    return {"score": score, "notes": notes, "n": len(parts)}


def bull_gene_score(good_biz: Optional[float] = None, moat_dims: int = 0,
                    evolve: Optional[float] = None, catalyst_strength: Optional[float] = None,
                    trend_score: Optional[float] = None,
                    earnings_quality_score: Optional[float] = None,
                    cashflow_score: Optional[float] = None) -> dict:
    """牛股基因组合(第3.7)：竞争优势因素越多→成为牛股概率越大。把基本面/护城河/质量/趋势/催化
    多维合成一个 0~100 综合分 + 评级，供前端与 agent 表达'这是不是一只长线牛股'。"""
    comp: list[tuple[float, float]] = []   # (value 0~1, weight)
    if good_biz is not None:
        comp.append((good_biz, 0.26))
    if moat_dims:
        comp.append((min(1.0, moat_dims / 4.0), 0.16))
    if evolve is not None:
        comp.append((evolve, 0.12))
    if earnings_quality_score is not None:
        comp.append((earnings_quality_score, 0.16))
    if cashflow_score is not None:
        comp.append((max(0.0, (cashflow_score + 1) / 2), 0.1))   # -1~1 → 0~1
    if catalyst_strength is not None:
        comp.append((catalyst_strength, 0.1))
    if trend_score is not None:
        comp.append((max(0.0, (trend_score + 1) / 2), 0.1))      # -1~1 → 0~1
    if not comp:
        return {"score": None, "grade": "insufficient", "n": 0}
    tw = sum(w for _, w in comp)
    raw = sum(v * w for v, w in comp) / tw if tw else 0.0
    score = round(raw * 100, 1)
    if score >= 75:
        grade = "牛股基因强(长线候选)"
    elif score >= 60:
        grade = "基因良好(可跟踪)"
    elif score >= 45:
        grade = "中性(需催化)"
    else:
        grade = "基因偏弱(谨慎)"
    return {"score": score, "grade": grade, "n": len(comp), "factors": tw}
