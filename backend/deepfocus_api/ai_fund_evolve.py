from __future__ import annotations

import math
from typing import Optional

"""阿尔法竞技场的「进化内核」——纯函数、零 I/O、可单测。两件事：

① 真·调教(学习回路)：每个角色按自己实盘盈亏，把成败归因到当初驱动买入的维度，自动微调权重
   (带学习率 + 向基线衰减 + 夹界，防过拟合噪声)。确定性数学，LLM 只当教练讲解(在 ai_fund 里接)。

② 真·不同算法(可插拔因子模型)：value(DCF/自由现金流内在价值 + 质量) 与 reversion(均值回归)
   两套与「多因子催化动量」根本不同的打分法，供磐石/磁极用。返回与多因子同构的 {score, scores, reasons}。
"""

# =========================================================================== #
# ① 学习回路：从实盘盈亏微调维度权重
# =========================================================================== #

LEARN_LR = 0.06          # 学习率(每笔最多挪这么多)
LEARN_LR_HOLD = 0.02     # 持仓期(浮盈浮亏)学习率——比平仓更轻，因未落袋、防被盘中噪声带偏
LEARN_DECAY = 0.03       # 每次向基线(乘子=1)回归，正则化、防漂太远
LEARN_LO, LEARN_HI = 0.45, 1.7   # 乘子夹界：任一维度权重最多缩到 0.45× / 放大到 1.7×


def learn_weights(learned_mult: dict, buy_scores: dict, pnl_pct: Optional[float],
                  lr: float = LEARN_LR) -> dict:
    """一笔平仓后更新该角色的「学到的权重乘子」。
    信用分配：某维度当初打分越高(越驱动这笔买入)，这笔的盈亏就越算它的账——
    赚了→抬高该维度乘子、亏了→压低。tanh 压缩极端盈亏，向基线衰减做正则。返回新的乘子 dict。"""
    out = dict(learned_mult or {})
    if pnl_pct is None or not buy_scores:
        return out
    signal = math.tanh(pnl_pct / 8.0)            # 盈亏→[-1,1]，±8% 约到 ±0.76
    for dim, sc in buy_scores.items():
        cur = float(out.get(dim, 1.0))
        cur = 1.0 + (cur - 1.0) * (1.0 - LEARN_DECAY)        # 先向基线回归一点
        cur += lr * signal * max(-1.0, min(1.0, float(sc)))  # 再按 盈亏×该维贡献 调整
        out[dim] = round(max(LEARN_LO, min(LEARN_HI, cur)), 4)
    return out


def effective_weights(base_weights: dict, learned_mult: dict) -> dict:
    """基线权重 × 学到的乘子 → 归一化后的有效权重。learned_mult 缺省维度按 1.0(没学到=用基线)。"""
    eff = {d: max(0.0, float(w) * float((learned_mult or {}).get(d, 1.0)))
           for d, w in (base_weights or {}).items()}
    tot = sum(eff.values())
    if tot <= 0:
        return dict(base_weights or {})
    return {d: round(w / tot, 4) for d, w in eff.items()}


def learn_from_holdings(learned_mult: dict, holdings: list, lr: float = LEARN_LR_HOLD) -> dict:
    """持仓期学习(每日一次)：对每个未平仓持仓，按其『当前浮动盈亏%』把成败归因到当初买入维度，
    轻量微调权重——让角色在卖出落袋之前就持续适应『当下市场什么因子有效』，不必死等稀有的平仓。
    holdings: [{"buy_scores": {...}, "unrealized_pct": float}]。复用 learn_weights 的信用分配/夹界/正则。"""
    out = dict(learned_mult or {})
    for h in holdings or []:
        bs = h.get("buy_scores") or {}
        up = h.get("unrealized_pct")
        if bs and up is not None:
            out = learn_weights(out, bs, float(up), lr=lr)
    return out


def weights_drift(base_weights: dict, learned_mult: dict) -> list[dict]:
    """把「学到的偏移」总结成人话(给前端/教练解说)：哪几维被自己调高/调低了多少。"""
    out = []
    for d, m in (learned_mult or {}).items():
        if d in (base_weights or {}) and abs(float(m) - 1.0) >= 0.06:
            out.append({"dim": d, "mult": round(float(m), 2),
                        "pct": round((float(m) - 1.0) * 100)})
    return sorted(out, key=lambda x: -abs(x["pct"]))


# =========================================================================== #
# ①' 市场态势自适应：各流派按大盘多空(regime)动态调整打法 —— 先进的"择时/战术配置"层
#    趋势派(猛犸)牛市加仓追强、熊市降仓防守(趋势跟随)；价值派(磐石)熊市恐慌里捡便宜(逆向);
#    逆向派(磁极)杀跌中抄超跌；事件派(游隼)牛市催化扩散快打。让"同一套模型"在不同市道有不同攻防。
# =========================================================================== #

# style -> regime -> (买入门槛增量, 仓位系数, 立场说明)。门槛+=更挑剔、-=更敢出手；仓位>1加仓、<1减仓。
_REGIME_TILT: dict[str, dict[str, tuple[float, float, str]]] = {
    "aggressive": {"bull": (-0.03, 1.25, "🔥 牛市顺势·加仓追强"),
                   "bear": (0.07, 0.55, "🛡 熊市降仓·只打最强突破"),
                   "neutral": (0.0, 1.0, "震荡观望·等突破")},
    "value": {"bull": (0.03, 0.9, "牛市估值贵·更挑剔"),
              "bear": (-0.05, 1.15, "💎 熊市恐慌·捡便宜好货"),
              "neutral": (0.0, 1.0, "耐心等便宜")},
    "contrarian": {"bull": (0.04, 0.85, "牛市超跌少·谨慎"),
                   "bear": (-0.04, 1.1, "🪃 恐慌杀跌·抄超跌"),
                   "neutral": (0.0, 1.0, "盯被错杀的")},
    "event": {"bull": (-0.02, 1.1, "牛市催化扩散·快打"),
              "bear": (0.04, 0.8, "熊市催化易夭折·更快进快出"),
              "neutral": (0.0, 1.0, "闻讯而动")},
    "balanced": {"bull": (-0.02, 1.1, "牛市顺势"),
                 "bear": (0.05, 0.7, "🛡 熊市收紧·依指数止损"),
                 "neutral": (0.0, 1.0, "稳中找机会")},
}


def regime_adapt(style: str, regime: Optional[str]) -> dict:
    """大盘态势 → 该流派的动态打法调整。regime∈bull/bear/neutral/unknown(unknown 按 neutral)。
    返回 {thr_delta(买入门槛增量), size_mult(仓位系数), stance(立场说明), regime}。"""
    tilt = _REGIME_TILT.get(style, _REGIME_TILT["balanced"])
    r = regime if regime in ("bull", "bear", "neutral") else "neutral"
    thr_d, size_m, stance = tilt[r]
    return {"thr_delta": thr_d, "size_mult": size_m, "stance": stance, "regime": r}


# =========================================================================== #
# ② 不同算法之一：value —— DCF/自由现金流内在价值 + 盈利质量(磐石)
# =========================================================================== #

def value_score(*, fcf: Optional[float] = None, market_cap: Optional[float] = None,
                pe: Optional[float] = None, roe: Optional[float] = None,
                gross_margin: Optional[float] = None, earnings_quality: Optional[float] = None,
                profit_yoy: Optional[float] = None, cashflow_type: Optional[int] = None,
                learned_mult: Optional[dict] = None) -> dict:
    """价值派打分(与催化动量根本不同)：先估「便宜不便宜」(自由现金流收益率 + 盈余收益率)，
    再叠「生意质量」(ROE/含金量/毛利/现金流八类)，少量「成长」。返回 {score(-1~1), scores, reasons}。
    learned_mult：真·调教——磐石按自己盈亏微调「估值/质量/成长」三者的偏重。数据不足→score=None。"""
    scores: dict = {}
    reasons: list[str] = []

    # —— 估值(便宜度)：自由现金流收益率 FCF/市值 + 盈余收益率 1/PE，对标 6% 的要求回报 ——
    val_parts = []
    if fcf is not None and market_cap and market_cap > 0:
        fcf_yield = fcf / market_cap
        val_parts.append(max(-1.0, min(1.0, (fcf_yield - 0.04) / 0.06)))  # 4% 中性、10% 满分、<0 扣分
        reasons.append(f"自由现金流收益率 {fcf_yield*100:.1f}%")
    if pe is not None and pe > 0:
        ey = 1.0 / pe
        val_parts.append(max(-1.0, min(1.0, (ey - 0.03) / 0.05)))         # PE 越低盈余收益率越高
        reasons.append(f"PE {pe:.0f}(盈余收益率 {ey*100:.1f}%)")
    elif pe is not None and pe <= 0:
        val_parts.append(-0.6); reasons.append("尚未盈利")
    if val_parts:
        scores["估值"] = round(sum(val_parts) / len(val_parts), 3)

    # —— 质量：ROE + 利润含金量 + 毛利 + 现金流八类型 ——
    q_parts = []
    if roe is not None:
        q_parts.append(max(-1.0, min(1.0, (roe - 8.0) / 12.0)))   # ROE 8% 中性、20% 满分
        reasons.append(f"ROE {roe:.0f}%")
    if earnings_quality is not None:
        q_parts.append(max(-1.0, min(1.0, earnings_quality * 2 - 1)))  # 0~1 → -1~1
    if gross_margin is not None:
        q_parts.append(max(-1.0, min(1.0, (gross_margin - 25.0) / 35.0)))
    if cashflow_type is not None:
        cf_map = {4: 1.0, 2: 0.7, 3: 0.3, 1: -0.3, 5: -0.4, 6: -0.8, 7: -0.7, 8: -1.0}
        q_parts.append(cf_map.get(int(cashflow_type), 0.0))
    if q_parts:
        scores["质量"] = round(sum(q_parts) / len(q_parts), 3)

    # —— 成长(次要)：净利同比，温和正向即可，不追高增速 ——
    if profit_yoy is not None:
        scores["成长"] = round(max(-1.0, min(1.0, profit_yoy / 30.0)), 3)
        reasons.append(f"净利同比 {profit_yoy:+.0f}%")

    if not scores:
        return {"score": None, "scores": {}, "reasons": ["无财务数据，价值模型无法评估"]}
    w = effective_weights({"估值": 0.45, "质量": 0.40, "成长": 0.15}, learned_mult)  # 可自学偏重
    tw = sum(w.get(k, 0.0) for k in scores)
    score = sum(scores[k] * w.get(k, 0.0) for k in scores) / tw if tw else 0.0
    return {"score": round(max(-1.0, min(1.0, score)), 3), "scores": scores, "reasons": reasons}


# =========================================================================== #
# ② 不同算法之二：reversion —— 均值回归(磁极)
# =========================================================================== #

def reversion_score(closes: list[float], last: Optional[float] = None,
                    learned_mult: Optional[dict] = None) -> dict:
    """均值回归打分(与趋势跟随反着来)：价格跌破均值越深(z 越负)且开始企稳→分越高(抄超跌)；
    站上/远高于均值→不追(分低甚至负)。learned_mult：磁极按盈亏自调「超跌度/回撤空间/企稳」偏重。
    返回 {score(-1~1), scores, reasons, z, stabilizing}。"""
    px = list(closes or [])
    c = last if last else (px[-1] if px else None)
    if c is None or len(px) < 20:
        return {"score": None, "scores": {}, "reasons": ["日线不足，均值回归无法评估"]}
    seq = px + [c] if abs(px[-1] - c) > 1e-9 else px
    win = seq[-20:]
    ma20 = sum(win) / len(win)
    var = sum((x - ma20) ** 2 for x in win) / len(win)
    std = var ** 0.5
    z = (c - ma20) / std if std > 1e-9 else 0.0
    hi = max(seq[-40:]) if len(seq) >= 40 else max(seq)
    drawdown = (hi - c) / hi if hi else 0.0
    recent_low = min(seq[-4:])
    stabilizing = c >= recent_low * 1.005          # 不再创新低、略反弹=企稳
    scores: dict = {}
    reasons: list[str] = []
    # 超跌分：z 越负越高(z=-2 满分、z=0 零、z>0 负)
    over = max(-1.0, min(1.0, -z / 2.0))
    scores["超跌度"] = round(over, 3)
    reasons.append(f"偏离20日均线 {z:+.1f}σ")
    # 回撤分：自高点回撤越深、潜在反弹空间越大(但封顶，太深可能是基本面崩)
    scores["回撤空间"] = round(max(0.0, min(1.0, drawdown / 0.3)) * (1 if drawdown < 0.5 else 0.3), 3)
    if drawdown > 0.05:
        reasons.append(f"自高点回撤 {drawdown*100:.0f}%")
    # 企稳分：止跌企稳才敢抄，否则接飞刀
    scores["企稳"] = 0.6 if stabilizing else -0.4
    reasons.append("已企稳" if stabilizing else "仍在下跌(不接飞刀)")
    w = effective_weights({"超跌度": 0.5, "回撤空间": 0.2, "企稳": 0.3}, learned_mult)  # 可自学偏重
    tw = sum(w.get(k, 0.0) for k in scores)
    score = sum(scores[k] * w.get(k, 0.0) for k in scores) / tw if tw else 0.0
    return {"score": round(max(-1.0, min(1.0, score)), 3), "scores": scores,
            "reasons": reasons, "z": round(z, 2), "stabilizing": stabilizing,
            "drawdown": round(drawdown, 3)}
