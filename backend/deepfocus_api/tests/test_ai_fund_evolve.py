"""阿尔法竞技场进化内核：学习回路(从盈亏调权) + value/reversion 两套不同算法。纯函数单测。"""
from deepfocus_api import ai_fund_evolve as ev


# ---------------- ① 学习回路 ----------------

def test_learn_weights_win_raises_loss_lowers():
    base = {"技术面": 1.0, "消息面": 1.0}
    # 主要靠技术面买入(技术面 score 高)，结果大赚 → 技术面乘子上调
    won = ev.learn_weights({}, {"技术面": 0.9, "消息面": 0.1}, pnl_pct=12.0)
    assert won["技术面"] > 1.0 and won["技术面"] > won["消息面"]
    # 同样靠技术面买入，结果大亏 → 技术面乘子下调
    lost = ev.learn_weights({}, {"技术面": 0.9, "消息面": 0.1}, pnl_pct=-12.0)
    assert lost["技术面"] < 1.0


def test_learn_weights_clamped_and_safe():
    m = {}
    for _ in range(50):                       # 连亏 50 次也不会无限下探
        m = ev.learn_weights(m, {"技术面": 1.0}, pnl_pct=-15.0)
    assert m["技术面"] >= ev.LEARN_LO
    assert ev.learn_weights({}, {}, None) == {}          # 缺数据安全返回
    assert ev.learn_weights({}, {"x": 0.5}, None) == {}  # pnl None 不动


def test_effective_weights_normalizes_and_shifts():
    base = {"技术面": 0.5, "消息面": 0.5}
    eff = ev.effective_weights(base, {"技术面": 1.6})
    assert abs(sum(eff.values()) - 1.0) < 1e-6      # 归一化
    assert eff["技术面"] > eff["消息面"]            # 被学高的维度占比更大
    assert ev.effective_weights(base, {}) == {"技术面": 0.5, "消息面": 0.5}


def test_weights_drift_reports():
    d = ev.weights_drift({"技术面": 0.5, "消息面": 0.5}, {"技术面": 0.7, "消息面": 1.02})
    keys = {x["dim"] for x in d}
    assert "技术面" in keys and "消息面" not in keys   # 仅显著漂移(技术面 -30%)


# ---------------- ② value 模型(DCF/质量) ----------------

def test_value_score_cheap_quality_beats_expensive_junk():
    cheap = ev.value_score(fcf=2.6e10, market_cap=2.0e11, pe=15, roe=25,
                           gross_margin=60, earnings_quality=0.95, profit_yoy=12, cashflow_type=4)
    pricey = ev.value_score(fcf=-1e9, market_cap=2.0e11, pe=120, roe=3,
                            gross_margin=12, earnings_quality=0.3, profit_yoy=-20, cashflow_type=7)
    assert cheap["score"] > 0.4 and pricey["score"] < 0
    assert "估值" in cheap["scores"] and "质量" in cheap["scores"]
    assert ev.value_score()["score"] is None            # 无数据→insufficient


def test_value_score_unprofitable_penalized():
    s = ev.value_score(pe=-5, roe=-2, cashflow_type=8)
    assert s["score"] is not None and s["score"] < 0


# ---------------- ② reversion 模型(均值回归) ----------------

def test_reversion_oversold_stabilizing_high():
    # 一路下跌后最后一根略反弹(企稳) → 抄超跌，分高
    closes = [100 - i for i in range(25)] + [76.5]   # 跌到 75 后反弹到 76.5
    s = ev.reversion_score(closes)
    assert s["score"] > 0.2 and s["z"] < 0 and s["stabilizing"] is True


def test_reversion_above_mean_not_chased():
    closes = [50 + i for i in range(25)]   # 一路上涨、远高于均值
    s = ev.reversion_score(closes, last=75)
    assert s["score"] < 0.2                # 均值回归不追高
    assert ev.reversion_score([1, 2, 3])["score"] is None   # 数据不足
