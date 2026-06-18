"""长线牛股方法论引擎(bull_playbook)：催化剂分类 / 趋势模板 / 杯柄 / 利弗莫尔 / 现金流八类 /
ROE 阶段 / 投资对象类型 / 有害扩张 / 好生意 / 牛股基因——忠于《价值投资之长线牛股》。"""
from deepfocus_api import bull_playbook as bp


# ---------------- Ⅰ 催化剂分类（第2章） ----------------

def test_classify_catalyst_types_and_direction():
    assert bp.classify_catalyst("公司中标12亿元大订单")["key"] == "big_order"
    assert bp.classify_catalyst("产品全面提价、量价齐升")["key"] == "price_hike"
    assert bp.classify_catalyst("业绩预增，净利同比增长65%")["key"] == "guidance_up"
    assert bp.classify_catalyst("拟定增募投扩产")["key"] == "capacity"
    assert bp.classify_catalyst("困境反转、毛利率环比改善")["key"] == "reversal"
    # 看空类方向为 -1
    assert bp.classify_catalyst("大股东拟减持")["dir"] == -1
    assert bp.classify_catalyst("收到证监会问询函")["dir"] == -1
    # 无催化剂
    assert bp.classify_catalyst("公司召开2023年度股东大会") is None
    assert bp.classify_catalyst("") is None


def test_catalyst_strength_modifiers():
    strong = bp.classify_catalyst("中标大订单，金额超预期翻倍")
    weak = bp.classify_catalyst("拟签订意向框架协议")
    assert strong["strength"] > weak["strength"]
    # 业绩增幅 <30% 弱于 ≥50%
    big = bp.classify_catalyst("业绩预增，净利增长80%")
    small = bp.classify_catalyst("业绩预增，净利增长12%")
    assert big["strength"] > small["strength"]


def test_catalyst_profile_net_direction():
    prof = bp.catalyst_profile(["中标大订单", "净利预增60%", "大股东减持"])
    assert prof["net_dir"] > 0 and prof["n"] == 3
    assert "大订单" in prof["bull_types"]
    assert prof["top"]["dir"] == 1
    assert bp.catalyst_profile([])["net_dir"] == 0.0


# ---------------- Ⅱ 趋势 / 形态（第6、7章） ----------------

def test_trend_template_uptrend_vs_downtrend():
    up = bp.trend_template([float(x) for x in range(50, 300)])      # 长期单调上行
    down = bp.trend_template([float(x) for x in range(300, 50, -1)])  # 单调下行
    assert up["score"] > 0.5 and up["stage"] == "advancing"
    assert down["score"] < 0 and down["stage"] == "declining"
    assert up["has"] and "ma60" in up


def test_trend_template_short_series_safe():
    out = bp.trend_template([1, 2, 3])
    assert out["has"] is False and out["score"] == 0.0


def test_swing_higher_low_high():
    # 抬高的波谷与波峰：10,5,12,8,15,11,18
    sw = bp.swing_points([10, 5, 12, 8, 15, 11, 18, 14, 20], left=1, right=1)
    assert sw["higher_high"] is True and sw["higher_low"] is True


def test_cup_with_handle_detection():
    # 构造杯柄：上涨→U形回落→回升至右缘→小幅回调(柄)
    left = [10 + i for i in range(10)]                 # 涨到 ~19
    down = [19 - i * 0.9 for i in range(10)]           # 跌到 ~10.9
    bottom = [10.8, 10.7, 10.9, 11.0]                  # U 形钝化底
    up = [11 + i * 0.9 for i in range(10)]             # 回升到 ~19
    handle = [19, 18.4, 18.0, 18.3, 18.8]              # 柄(回调 ~5%)
    closes = left + down + bottom + up + handle
    ohlc = [{"o": c, "h": c + 0.2, "l": c - 0.2, "c": c, "v": 1000} for c in closes]
    out = bp.cup_with_handle(ohlc)
    assert out["detected"] is True
    assert out["handle_pullback"] is not None and out["handle_pullback"] <= 0.33


def test_cup_with_handle_rejects_too_short():
    assert bp.cup_with_handle([{"c": 1}] * 5)["detected"] is False


def test_livermore_pivot():
    base = [{"o": 10, "h": 10.1, "l": 9.9, "c": 10, "v": 1000}] * 10
    trigger = {"o": 10, "h": 11.2, "l": 10, "c": 11.0, "v": 4000}     # 放量大涨(+10%)
    pull = [{"o": 11, "h": 11.05, "l": 10.6, "c": 10.8, "v": 1500},   # 缩量小回撤
            {"o": 10.8, "h": 10.95, "l": 10.55, "c": 10.7, "v": 1200}]
    out = bp.livermore_pivot(base + [trigger] + pull)
    assert out["detected"] is True and out["pivot"] is not None


def test_market_regime():
    bull = bp.market_regime([float(x) for x in range(100, 300)])
    bear = bp.market_regime([float(x) for x in range(300, 100, -1)])
    assert bull["regime"] == "bull" and bull["above_ma60"] is True
    assert bear["regime"] == "bear"
    assert bp.market_regime([1, 2, 3])["regime"] == "unknown"


def test_support_resistance():
    sr = bp.support_resistance([float(x) for x in range(50, 110)], last=95)
    assert sr["support"] is not None and sr["support"] < 95


# ---------------- Ⅲ 现金流 / 估值 / 质量（第4、9、10章） ----------------

def test_cashflow_eight_types():
    assert bp.cashflow_type(10, -5, -5)["type"] == 4    # +--  现金奶牛
    assert bp.cashflow_type(10, -5, 5)["type"] == 3     # +-+  成长
    assert bp.cashflow_type(10, 5, -5)["type"] == 2     # ++-  成熟最理想
    assert bp.cashflow_type(-10, -5, 5)["type"] == 7    # --+  转型输血
    assert bp.cashflow_type(-10, -5, -5)["type"] == 8   # ---  垃圾
    assert bp.cashflow_type(10, -5, -5)["score"] > bp.cashflow_type(-10, -5, -5)["score"]
    assert bp.cashflow_type(None, 1, 1)["risk"] == "insufficient"


def test_free_cash_flow():
    assert bp.free_cash_flow(100, 30) == 70
    assert bp.free_cash_flow(100, -30) == 70   # capex 取绝对值
    assert bp.free_cash_flow(None, 30) is None


def test_earnings_quality():
    good = bp.earnings_quality(net_income=100, ocf=130, revenue=1000,
                               accounts_receivable=20, advance_receipts=250, non_recurring=5)
    bad = bp.earnings_quality(net_income=100, ocf=20, revenue=1000,
                              accounts_receivable=600, advance_receipts=0, non_recurring=70)
    assert good["score"] > bad["score"]
    assert any("含金量" in f for f in good["flags"])
    assert bp.earnings_quality()["score"] is None


def test_roe_stage():
    growth = bp.roe_stage(roe=18, roe_prev=12, revenue_yoy=30, profit_yoy=40)
    mature = bp.roe_stage(roe=25, roe_prev=24, revenue_yoy=5, profit_yoy=8)
    decline = bp.roe_stage(roe=4, roe_prev=10, revenue_yoy=-15, profit_yoy=-30)
    assert growth["stage"] == "growth" and growth["score"] > mature["score"]
    assert mature["stage"] == "mature"
    assert decline["stage"] == "decline" and decline["score"] < 0


# ---------------- Ⅳ 投资对象类型（第5章） ----------------

def test_classify_target_type_table():
    assert bp.classify_target_type("mature", "grow")["key"] == "continuous_relay"
    assert bp.classify_target_type("decline", "intro")["key"] == "expectation"
    # 连续接力置信度最高、期待型最低
    assert bp.classify_target_type("mature", "grow")["confidence"] > \
           bp.classify_target_type("decline", "intro")["confidence"]


def test_infer_target_type():
    t = bp.infer_target_type(revenue_yoy=4, profit_yoy=6, roe=20, new_biz_ratio=0.2, new_biz_growing=True)
    assert t["key"] == "continuous_relay" and t["old_stage"] == "mature"


def test_expansion_red_flags():
    bad = bp.expansion_red_flags(revenue_yoy=20, ar_yoy=60, ocf=5, net_income=100,
                                 debt_yoy=80, goodwill_ratio=0.5, unrelated_diversification=True)
    assert bad["harmful"] is True and bad["count"] >= 2
    good = bp.expansion_red_flags(revenue_yoy=20, ar_yoy=15, ocf=120, net_income=100)
    assert good["count"] == 0


# ---------------- Ⅴ 好生意 / 牛股基因（第1、3章） ----------------

def test_good_business():
    great = bp.good_business(roe=25, roe_std=2, gross_margin=60, gross_margin_trend=1,
                             ocf_to_ni=1.2, capex_intensity=0.04, advance_over_receivable=True,
                             sector="白酒")
    poor = bp.good_business(roe=4, roe_std=8, gross_margin=12, ocf_to_ni=0.3, capex_intensity=0.3)
    assert great["score"] > poor["score"]
    assert any("真金白银" in n for n in great["notes"])


def test_bull_gene_score():
    strong = bp.bull_gene_score(good_biz=0.85, moat_dims=4, evolve=0.8, catalyst_strength=0.9,
                                trend_score=0.8, earnings_quality_score=0.85, cashflow_score=1.0)
    weak = bp.bull_gene_score(good_biz=0.3, moat_dims=1, evolve=0.2, catalyst_strength=0.3,
                              trend_score=-0.5, earnings_quality_score=0.2, cashflow_score=-1.0)
    assert strong["score"] > weak["score"]
    assert "牛股基因强" in strong["grade"]
    assert bp.bull_gene_score()["grade"] == "insufficient"
