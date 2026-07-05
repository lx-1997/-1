"""A股 AI 模拟盘「阿尔法」：催化剂方向、五维研判+买点确认、严谨卖出、心情/战绩、解盘流快照。"""
import pytest

from deepfocus_api import ai_fund

MD_UP = {"closes": [90, 91, 92, 93, 95, 96, 97, 98, 99, 100] * 3, "flow5": 3.0e8}  # 上升趋势 + 主力流入


@pytest.fixture
def fund(monkeypatch, tmp_path):
    monkeypatch.setattr(ai_fund, "_db_path", lambda: tmp_path / "ai_fund.sqlite3")
    monkeypatch.setattr(ai_fund, "_our_content", lambda name: [])
    monkeypatch.setattr(ai_fund, "_sentiment", lambda items: None)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {})
    monkeypatch.setattr(ai_fund, "_llm_narratives", lambda decisions, mood, cfg=None: {})
    monkeypatch.setattr(ai_fund, "_benchmark", lambda started, navpct: None)  # 不打外网
    monkeypatch.setattr(ai_fund, "_market_regime_now",
                        lambda: {"regime": "bull", "above_ma60": True, "ma60_rising": True})  # 大盘多空不打外网
    monkeypatch.setattr(ai_fund, "_latest_wire", lambda limit=16: [])  # 脑内独白弹药库不打外网
    monkeypatch.setattr(ai_fund, "_diverse_ammo", lambda per_type=3: [])  # 多样化弹药不打外网
    monkeypatch.setattr(ai_fund, "_free_quote", lambda symbol: None)  # 免费实时价兜底不打外网：未 _wire 的池内股票无行情
    monkeypatch.setattr(ai_fund, "_run_debate", lambda name, symbol, an, q: None)  # 多空辩论默认关(不打外网)
    monkeypatch.setattr(ai_fund, "_in_session", lambda now=None: True)  # 测试不受交易时段闸门影响
    ai_fund.init_ai_fund_db()


def _wire(monkeypatch, quotes):
    monkeypatch.setattr(ai_fund, "ifind_enabled", lambda: True)
    monkeypatch.setattr(ai_fund, "cached_single_quote",
                        lambda s: quotes.get((ai_fund.normalize_a_code(s) or "").split(".")[0]))


def _age_pos(symbol):
    """把持仓买入日改到过去，绕过 A股 T+1 当日锁，模拟隔日后才可卖出。"""
    with ai_fund._connect() as conn:
        conn.execute("UPDATE aif_position SET opened_at='2020-01-01T00:00:00+00:00' WHERE symbol=?", (symbol,))
        conn.commit()


def test_headline_direction():
    assert ai_fund._headline_dir([{"title": "中标大订单，业绩超预期", "severity": "success", "age_h": 2}]) > 0.3
    assert ai_fund._headline_dir([{"title": "遭处罚，股东减持", "severity": "warning", "age_h": 2}]) < -0.3
    assert ai_fund._headline_dir([{"title": "中标大单", "severity": "success", "age_h": 999}]) == 0.0


def test_techstats_trend():
    t = ai_fund._techstats(list(range(80, 100)), 100)  # 20 日上升
    assert t["has"] and t["ma5"] and t["ma20"] and t["high20"] == 100


def test_analyze_rigor_entry_gate():
    q = {"latest": 100, "pe_ttm": 22, "pb": 3, "changeRatio": 3.0, "turnoverRatio": 6, "high": 101, "low": 96}
    items = [{"title": "海外扩产、获大订单", "severity": "success", "age_h": 1, "id": "x", "src": "快讯"}]
    an = ai_fund._analyze("某股", q, items, MD_UP)
    assert an["entry_ok"] is True and an["trend_up"] is True
    assert "技术面" in an["scores"] and "资金面" in an["scores"] and "消息面" in an["scores"]
    assert an["buy_point"]


def test_analyze_blocks_downtrend():
    """无催化剂 + 跌破均线 → 买点不成立。"""
    q = {"latest": 80, "pe_ttm": 22, "pb": 3, "changeRatio": -2.0, "turnoverRatio": 6, "high": 95, "low": 79}
    md = {"closes": [100, 99, 98, 96, 94, 92, 90, 88, 86, 84] * 3, "flow5": -2.0e8}
    an = ai_fund._analyze("某股", q, [], md)
    assert an["entry_ok"] is False


def test_ifind_unavailable_failsafe(fund, monkeypatch):
    monkeypatch.setattr(ai_fund, "ifind_enabled", lambda: False)
    out = ai_fund.run_tick()
    assert out["ok"] is False and out["reason"] == "ifind_unavailable"
    snap = ai_fund.get_snapshot()
    assert snap["position_count"] == 0 and snap["data_quality"]["level"] == "degraded"
    assert snap["persona"]["name"] == "阿尔法" and "mood" in snap and "stats" in snap


def test_buy_on_catalyst_then_hard_stop(fund, monkeypatch):
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "比亚迪海外大单、销量超预期", "severity": "success", "age_h": 1, "id": "n", "src": "快讯"}] if name == "比亚迪" else [])
    out = ai_fund.run_tick()
    assert "002594" in {t["symbol"] for t in out["traded"] if t["side"] == "buy"}
    snap = ai_fund.get_snapshot()
    byd = next(f for f in snap["feed"] if f["symbol"] == "002594" and f["side"] == "buy")
    assert byd["catalyst"] and byd["buy_point"] and byd["thinking"] and byd["narrative"]
    assert byd["scores"].get("消息面") is not None
    # 暴跌触发硬止损（先把买入日调到过去，绕过 T+1 当日锁）
    _age_pos("002594")
    q["002594"]["latest"] = 88
    out2 = ai_fund.run_tick()
    sells = [t for t in out2["traded"] if t["side"] == "sell" and t["symbol"] == "002594"]
    assert sells and "止损" in sells[0]["reason"]


def test_t_plus_one_no_same_day_sell(fund, monkeypatch):
    """A股 T+1 铁律：当日买入当日不可卖(含止损)；隔日才可卖。"""
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "比亚迪海外大单、销量超预期", "severity": "success", "age_h": 1, "id": "n", "src": "快讯"}] if name == "比亚迪" else [])
    ai_fund.run_tick()  # 当日建仓
    q["002594"]["latest"] = 80  # 同日暴跌 -20%，远超硬止损
    out = ai_fund.run_tick()
    assert not [t for t in out["traded"] if t["side"] == "sell" and t["symbol"] == "002594"], "T+1：当日买入当日不可卖"
    _age_pos("002594")          # 隔日
    out2 = ai_fund.run_tick()
    assert any(t["side"] == "sell" and t["symbol"] == "002594" for t in out2["traded"]), "隔日应可止损卖出"


def test_bull_playbook_dims_flow_through_tick(fund, monkeypatch):
    """长线牛股新维度(趋势模板/成长质量/大订单催化)端到端进入决策；大盘转空也敢做最强的上升段+强催化。"""
    monkeypatch.setattr(ai_fund, "_market_regime_now",
                        lambda: {"regime": "bear", "above_ma60": False, "ma60_rising": False})
    closes = [50 + i * 0.2 for i in range(250)]          # 一年上升日线
    ohlc = [{"o": c - 0.2, "h": c + 0.4, "l": c - 0.5, "c": c, "v": 1000} for c in closes]
    md = {"002594": {"closes": closes, "ohlc": ohlc, "flow5": 3.0e8,
                     "fundamentals": {"roe": 22.0, "revenue_yoy": 30.0, "profit_yoy": 40.0}}}
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: md)
    q = {"002594": {"latest": closes[-1], "pe_ttm": 24, "pb": 4, "changeRatio": 2.0, "turnoverRatio": 5}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "比亚迪中标15亿大订单、超预期", "severity": "success",
                                       "age_h": 1, "id": "n", "src": "快讯"}] if name == "比亚迪" else [])
    out = ai_fund.run_tick()
    assert out["regime"] == "bear"
    assert "002594" in {t["symbol"] for t in out["traded"] if t["side"] == "buy"}
    snap = ai_fund.get_snapshot()
    byd = next(f for f in snap["feed"] if f["symbol"] == "002594" and f["side"] == "buy")
    assert "趋势" in byd["scores"] and "成长质量" in byd["scores"]   # 新维度已计入综合分
    assert "大订单" in byd["catalyst"] and "上升段" in byd["buy_point"]


def test_thesis_voice_differentiated_per_style():
    """同一只票、同一方向，5 个流派的认知措辞各不相同(不再都像阿尔法)。"""
    src = "文章3条"
    bull = {st: ai_fund._thesis_title(st, "某股", src, 0.3)
            for st in ("balanced", "aggressive", "value", "event", "contrarian")}
    assert len(set(bull.values())) == 5, bull                 # 看多档 5 派全分化
    assert "重锤" in bull["aggressive"] and "估值" in bull["value"]
    assert "快进快出" in bull["event"] and "超跌" in bull["contrarian"]
    # 回避档也按流派分化
    avoid = {st: ai_fund._thesis_title(st, "某股", src, -0.3) for st in ("aggressive", "value", "contrarian")}
    assert len(set(avoid.values())) == 3


def test_snapshot_shape(fund, monkeypatch):
    _wire(monkeypatch, {"300750": {"latest": 200, "pe_ttm": 25, "pb": 4, "changeRatio": 3.0, "turnoverRatio": 4, "high": 202, "low": 196}})
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "宁德储能放量", "severity": "success", "age_h": 1, "id": "c", "src": "研报"}] if name == "宁德时代" else [])
    ai_fund.run_tick()
    snap = ai_fund.get_snapshot()
    for k in ("nav", "nav_unit", "commentary", "feed", "persona", "mood", "stats", "positions", "history", "data_quality", "risk"):
        assert k in snap
    assert "不构成投资建议" in snap["disclaimer"] and snap["feed"][0]["thinking"]
    assert "sample_days" in snap["risk"] and "max_drawdown_pct" in snap["risk"]


def test_phase_buckets():
    from datetime import datetime
    bj = ai_fund.BJ_TZ
    assert ai_fund._phase(datetime(2026, 6, 12, 9, 10, tzinfo=bj))[0] == "preopen"   # 周五盘前(非节假日)
    assert ai_fund._phase(datetime(2026, 6, 12, 12, 0, tzinfo=bj))[0] == "noon"      # 午间
    assert ai_fund._phase(datetime(2026, 6, 12, 16, 0, tzinfo=bj))[0] == "postclose" # 盘后
    assert ai_fund._phase(datetime(2026, 6, 12, 22, 0, tzinfo=bj))[0] == "evening"   # 夜间
    assert ai_fund._phase(datetime(2026, 6, 13, 11, 0, tzinfo=bj))[0] == "weekend"   # 周六
    assert ai_fund._phase(datetime(2026, 6, 19, 9, 10, tzinfo=bj))[0] == "weekend"   # 端午节休市


def test_musing_thinking_log_24h(fund, monkeypatch):
    """收盘/任意时段每跑一轮都『沉淀思考』：脑内独白入库且独立于观察流保留，snapshot 暴露 musings。"""
    _wire(monkeypatch, {"300750": {"latest": 200, "pe_ttm": 25, "pb": 4, "changeRatio": 1.0, "turnoverRatio": 4, "high": 202, "low": 196}})
    ai_fund.run_tick(trade=False)  # 收盘只观察也要思考
    snap = ai_fund.get_snapshot()
    assert "musings" in snap and snap["musings"], "脑内独白应已入库"
    m = snap["musings"][0]
    assert m["text"] and m["phase"] and "refs" in m
    assert snap.get("thinking_total", 0) >= 1
    assert isinstance(snap["decisions_total"], int)  # 独白不计入「出手次数」


def test_memory_learns_from_trades(fund, monkeypatch):
    """像人一样持续记忆/学习：本站催化剂建仓→形成观点(thesis)，平仓止损→沉淀教训(lesson)，可召回。"""
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "比亚迪海外大单、销量超预期", "severity": "success", "age_h": 1, "id": "n", "src": "快讯", "date": "2026-06-18", "url": ""}] if name == "比亚迪" else [])
    ai_fund.run_tick()  # 建仓 + 形成 thesis
    snap = ai_fund.get_snapshot()
    assert "memory" in snap and "memory_stats" in snap
    assert snap["memory_stats"]["theses"] >= 1, "应形成个股观点 thesis"
    # 暴跌触发硬止损 → 沉淀亏损教训（先绕过 T+1 当日锁）
    _age_pos("002594")
    q["002594"]["latest"] = 88
    ai_fund.run_tick()
    snap2 = ai_fund.get_snapshot()
    assert snap2["memory_stats"]["losses"] >= 1, "止损应沉淀 trade_loss 教训"
    recalled = ai_fund._recall_memories(["002594"], limit=4)
    assert recalled and any(m["mem_type"] == "trade_loss" for m in recalled)
    # 独白能召回记忆并暴露给前端
    assert "recalled" in snap2["musings"][0]


def test_memory_decay_idempotent(fund):
    """每日衰减节流:同日重复调用不应反复衰减。"""
    ai_fund.run_tick()
    with ai_fund._connect() as conn:
        ai_fund._decay_memory(conn); conn.commit()
        before = conn.execute("SELECT COALESCE(SUM(weight),0) AS w FROM aif_memory WHERE fund_id=?", (ai_fund.FUND_ID,)).fetchone()["w"]
        ai_fund._decay_memory(conn); conn.commit()  # 同日再调 → 不应再衰减
        after = conn.execute("SELECT COALESCE(SUM(weight),0) AS w FROM aif_memory WHERE fund_id=?", (ai_fund.FUND_ID,)).fetchone()["w"]
    assert abs(before - after) < 1e-9


def test_risk_metrics_sample_gating():
    """诚实纪律：<2点不算；2~4点只给最大回撤；>=5点才出夏普/Beta/alpha。"""
    # 不足 2 点：什么都不算
    r0 = ai_fund._risk_metrics([{"date": "2026-01-01", "value": 1.0}], 1_000_000, None)
    assert r0["sufficient"] is False and r0["max_drawdown_pct"] is None and r0["sharpe"] is None
    # 2~4 点：给最大回撤，年化比率仍 None（短样本不瞎算）
    hist4 = [{"date": f"2026-01-0{i}", "value": v} for i, v in enumerate([1.0, 1.05, 0.98, 1.02], start=1)]
    r4 = ai_fund._risk_metrics(hist4, 1_000_000, None)
    assert r4["max_drawdown_pct"] is not None and r4["max_drawdown_pct"] > 0
    assert r4["sharpe"] is None and r4["sufficient"] is False
    # 够样本(>=5) + 基准：年化比率 + Beta/alpha 全出
    vals = [1.0, 1.02, 1.01, 1.05, 1.04, 1.08, 1.06]
    hist = [{"date": f"2026-01-{i:02d}", "value": v} for i, v in enumerate(vals, start=1)]
    bench = [{"date": f"2026-01-{i:02d}", "value": v}
             for i, v in enumerate([1.0, 1.01, 1.005, 1.02, 1.015, 1.03, 1.02], start=1)]
    r = ai_fund._risk_metrics(hist, 1_000_000, bench)
    assert r["sufficient"] is True
    assert r["sharpe"] is not None and r["annualized_vol"] is not None
    assert r["beta"] is not None and r["alpha_annual"] is not None
    assert r["max_drawdown_pct"] is not None


# --------------------------------------------------------------------------- #
# 多智能体赛马（Alpha Arena）：账户隔离、流派分化、排行榜
# --------------------------------------------------------------------------- #

def test_arena_isolation_and_leaderboard(fund, monkeypatch):
    """两个智能体各跑一轮，持仓/快照按 fund_id 隔离；排行榜一次拉全场并排名。"""
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP})
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "比亚迪中标大订单、业绩超预期", "severity": "success",
                                       "age_h": 1, "id": "n", "src": "快讯"}] if name == "比亚迪" else [])
    out_main = ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    out_mam = ai_fund.run_tick(cfg=ai_fund.cfg_for("mammoth"))
    assert "002594" in {t["symbol"] for t in out_main["traded"] if t["side"] == "buy"}
    assert "002594" in {t["symbol"] for t in out_mam["traded"] if t["side"] == "buy"}

    snap_main = ai_fund.get_snapshot("main")
    snap_mam = ai_fund.get_snapshot("mammoth")
    assert snap_main["persona"]["name"] == "阿尔法" and snap_main["fund_id"] == "main"
    assert snap_mam["persona"]["name"] == "猛犸" and snap_mam["persona"]["emoji"] == "🦣"
    assert snap_main["position_count"] == 1 and snap_mam["position_count"] == 1
    # 猛犸单仓更重（pos_size_mult 1.35 + 集中）→ 仓位权重高于阿尔法
    assert snap_mam["positions"][0]["weight"] >= snap_main["positions"][0]["weight"]

    arena = ai_fund.get_arena()
    # 前端竞技场契约：strategies[] + benchmark{name,nav_pct}；strategies 与 agents 同物
    assert arena["strategies"] is arena["agents"]
    assert len(arena["strategies"]) == len(ai_fund.ROSTER)
    assert arena["champion"] in {c.fund_id for c in ai_fund.ROSTER}
    assert "name" in arena["benchmark"] and "nav_pct" in arena["benchmark"]
    ranks = [a["rank"] for a in arena["strategies"]]
    assert ranks == sorted(ranks)  # 已按收益率排名
    navs = [a["nav_pct"] for a in arena["strategies"]]
    assert navs == sorted(navs, reverse=True)
    main_card = next(a for a in arena["strategies"] if a["fund_id"] == "main")
    assert main_card["is_main"] is True and "mood" in main_card and "blurb" in main_card
    for c in arena["strategies"]:  # 每张卡含前端要的展示字段
        assert "nav_unit" in c and "color" in c and "rank" in c and "emoji" in c


def test_style_divergence_aggressive_and_value():
    """同一只票，不同流派给出不同决策：激进追涨停 / 价值拒绝高估值。"""
    strong = [{"title": "中标15亿大订单、业绩超预期、扩产提价", "severity": "success", "age_h": 1, "id": "x", "src": "快讯"}]
    # 涨停板（changeRatio 9.5）：阿尔法不追(overbought 闸门)、猛犸敢追(chase_ok)
    q_ob = {"latest": 100, "pe_ttm": 22, "pb": 3, "changeRatio": 9.5, "turnoverRatio": 6, "high": 101, "low": 96}
    an_main = ai_fund._analyze("某股", q_ob, strong, MD_UP, ai_fund.MAIN_CFG)
    an_mam = ai_fund._analyze("某股", q_ob, strong, MD_UP, ai_fund.cfg_for("mammoth"))
    assert an_main["overbought"] is True and an_main["entry_ok"] is False
    assert an_mam["overbought"] is False and an_mam["entry_ok"] is True
    # 高估值（PE 60）：阿尔法可买、磐石(max_pe=40)拒绝；便宜票(PE 18)磐石才出手
    q_pe60 = {"latest": 100, "pe_ttm": 60, "pb": 3, "changeRatio": 3.0, "turnoverRatio": 6, "high": 101, "low": 96}
    assert ai_fund._analyze("某股", q_pe60, strong, MD_UP, ai_fund.MAIN_CFG)["entry_ok"] is True
    assert ai_fund._analyze("某股", q_pe60, strong, MD_UP, ai_fund.cfg_for("rock"))["entry_ok"] is False
    q_cheap = {"latest": 100, "pe_ttm": 18, "pb": 3, "changeRatio": 3.0, "turnoverRatio": 6, "high": 101, "low": 96}
    assert ai_fund._analyze("某股", q_cheap, strong, MD_UP, ai_fund.cfg_for("rock"))["entry_ok"] is True


def test_contrarian_oversold_bonus():
    """逆向(磁极)在超跌票上比阿尔法给更高分（跌得越深、反弹空间分越高）。"""
    md_dn = {"closes": [100, 99, 98, 96, 94, 92, 90, 88, 86, 84] * 3, "flow5": -0.5e8}
    q_dn = {"latest": 84, "pe_ttm": 22, "pb": 3, "changeRatio": -1.0, "turnoverRatio": 6, "high": 100, "low": 83}
    cat = [{"title": "签约战略合作、产品获批", "severity": "success", "age_h": 2, "id": "y", "src": "快讯"}]
    s_main = ai_fund._analyze("某股", q_dn, cat, md_dn, ai_fund.MAIN_CFG)["score"]
    s_contra = ai_fund._analyze("某股", q_dn, cat, md_dn, ai_fund.cfg_for("contra"))["score"]
    assert s_contra > s_main


# --------------------------------------------------------------------------- #
# 多空辩论推演（思考从打分质变成推演）：主账户买入触发、非主账户跳过、同股去重、快照暴露
# --------------------------------------------------------------------------- #

def test_evidence_pack_is_grounded_json():
    import json as _json
    an = {"scores": {"消息面": 0.5}, "score": 0.3, "confidence": 0.6, "catalyst": "【快讯】大单",
          "buy_point": "突破", "tech": {"ma20": 95, "ma5": 98, "ret5": 4.2},
          "thinking": [{"text": "站上20日线"}], "trend_template": {"stage": "advancing"}}
    q = {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 3.0}
    pack = _json.loads(ai_fund._evidence_pack("比亚迪", "002594", an, q))
    assert pack["标的"] == "比亚迪(002594)" and pack["综合分"] == 0.3
    assert pack["五维打分"]["消息面"] == 0.5 and pack["均线"]["MA20"] == 95


def _byd_catalyst(monkeypatch):
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP})
    monkeypatch.setattr(ai_fund, "_our_content",
                        lambda name: [{"title": "比亚迪中标大订单、业绩超预期", "severity": "success",
                                       "age_h": 1, "id": "n", "src": "快讯"}] if name == "比亚迪" else [])
    return q


def test_debate_runs_on_main_buy_and_surfaces_in_snapshot(fund, monkeypatch):
    _byd_catalyst(monkeypatch)
    canned = {"bull": {"thesis": "强催化+趋势在上"}, "rebuttal": {"net_lean": "偏多"},
              "verdict": {"decision": "建仓", "conviction": 0.7, "invalidation": "跌破MA20止损",
                          "edge_reason": "本站领先点名", "key_risk": "短期追高"}}
    calls = []
    monkeypatch.setattr(ai_fund, "_run_debate", lambda n, s, a, qq: (calls.append(s) or canned))
    out = ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    assert "002594" in {t["symbol"] for t in out["traded"] if t["side"] == "buy"}
    assert calls == ["002594"]  # 主账户买入触发了一次辩论
    snap = ai_fund.get_snapshot("main")
    byd = next(f for f in snap["feed"] if f["symbol"] == "002594" and f["side"] == "buy")
    assert byd["debate"] and byd["debate"]["verdict"]["decision"] == "建仓"
    assert byd["debate"]["verdict"]["invalidation"]  # 含止损/认错位


def test_debate_skipped_for_non_main_and_deduped(fund, monkeypatch):
    q = _byd_catalyst(monkeypatch)
    calls = []
    monkeypatch.setattr(ai_fund, "_run_debate", lambda n, s, a, qq: (calls.append(s) or {"verdict": {"decision": "建仓"}}))
    ai_fund.run_tick(cfg=ai_fund.cfg_for("mammoth"))   # 猛犸 debate=False → 不触发
    assert calls == []
    # 同股 24h 去重：连调两次只真跑一次
    ai_fund._maybe_debate("main", "tid1", "002594", "比亚迪", {}, q["002594"])
    ai_fund._maybe_debate("main", "tid2", "002594", "比亚迪", {}, q["002594"])
    assert calls == ["002594"]


# --------------------------------------------------------------------------- #
# 跨智能体共识/分歧（赛马的独特集体信号）+ 顶层精选辩论
# --------------------------------------------------------------------------- #

def test_arena_consensus_and_divergence(fund):
    import json as _json
    now = ai_fund.utc_now_iso()
    pos_sql = ("INSERT INTO aif_position (fund_id,symbol,name,qty,avg_cost,opened_at,updated_at,high_water)"
               " VALUES (?,?,?,?,?,?,?,?)")
    with ai_fund._connect() as conn:
        # 共识：main + 猛犸 同持 002594
        conn.execute(pos_sql, ("main", "002594", "比亚迪", 100, 100, now, now, 100))
        conn.execute(pos_sql, ("mammoth", "002594", "比亚迪", 100, 100, now, now, 100))
        # 分歧：磐石持 600519(实锤看多)，磁极对 600519 看空(thesis score=-0.3)
        conn.execute(pos_sql, ("rock", "600519", "贵州茅台", 100, 100, now, now, 100))
        conn.execute("INSERT INTO aif_memory (id,fund_id,symbol,name,mem_type,ts,updated_at,title,detail,confidence,weight)"
                     " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     ("m1", "contra", "600519", "贵州茅台", "thesis", now, now, "回避",
                      _json.dumps({"score": -0.3}), 0.5, 0.5))
        conn.commit()
        crowd = ai_fund._arena_consensus(conn)
    byd = next(c for c in crowd["consensus"] if c["symbol"] == "002594")
    assert byd["hold_count"] == 2 and {h["fund_id"] for h in byd["holders"]} == {"main", "mammoth"}
    mao = next(d for d in crowd["divergence"] if d["symbol"] == "600519")
    assert "rock" in {b["fund_id"] for b in mao["bulls"]}
    assert "contra" in {b["fund_id"] for b in mao["bears"]}


def test_latest_debate_surfaced_top_level(fund, monkeypatch):
    _byd_catalyst(monkeypatch)
    monkeypatch.setattr(ai_fund, "_run_debate",
                        lambda n, s, a, qq: {"verdict": {"decision": "建仓", "invalidation": "跌破MA20"}})
    ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    snap = ai_fund.get_snapshot("main")
    assert snap["latest_debate"] and snap["latest_debate"]["symbol"] == "002594"
    assert snap["latest_debate"]["debate"]["verdict"]["decision"] == "建仓"


def test_buy_includes_why_this_over_that(fund, monkeypatch):
    """买入解释含『优选』横向对比步骤：为什么挑中它而非最强备选(纯确定性，无 LLM)。"""
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96},
         "300750": {"latest": 200, "pe_ttm": 25, "pb": 4, "changeRatio": 1.0, "turnoverRatio": 4, "high": 202, "low": 196}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP, "300750": MD_UP})
    monkeypatch.setattr(ai_fund, "_our_content",
        lambda name: ([{"title": "比亚迪中标大订单、业绩超预期、扩产提价", "severity": "success", "age_h": 1, "id": "a", "src": "快讯"}] if name == "比亚迪"
                      else [{"title": "宁德时代签约合作", "severity": "info", "age_h": 5, "id": "b", "src": "快讯"}] if name == "宁德时代" else []))
    out = ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    assert "002594" in {t["symbol"] for t in out["traded"] if t["side"] == "buy"}
    snap = ai_fund.get_snapshot("main")
    buys = [f for f in snap["feed"] if f["kind"] == "trade" and f["side"] == "buy"]
    opt_steps = [s for f in buys for s in f["thinking"] if s.get("label") == "优选"]
    assert opt_steps, "至少一笔买入应含『优选』横向对比步骤"
    # 优选步骤点名了另一只候选 + 给出综合分对比
    assert any(("比亚迪" in s["text"] or "宁德时代" in s["text"]) and "综合" in s["text"] for s in opt_steps)


# ── 直播独白截断：绝不断在半句（修「财政部 这预算数据」式硬切）────────────────
def test_musing_trim_never_cuts_mid_sentence():
    full = "收盘了，泡壶茶复盘。光模块我继续熬。英特尔盘后又下滑，跟咱A股不搭边。财政部这预算数据值得琢磨。"
    assert ai_fund._trim_to_sentence(full, 999) == full          # 不超长→原样
    out = ai_fund._trim_to_sentence(full, 24)                     # 超长→落在句尾，不半句
    assert out[-1] in "。！？…" and len(out) <= 24 + 1
    assert out == "收盘了，泡壶茶复盘。光模块我继续熬。"            # 截到最后一个完整句
    # 前段无句尾标点→软省略号收尾，绝不硬切到半个词
    hard = ai_fund._trim_to_sentence("一二三四五六七八九十一二三四五六七八九十", 8)
    assert hard.endswith("…")


# ── 基准修复回归：开赛日基线用开盘价，bench_ret 不再恒 0 ──────────────────────
def _seed_bench(monkeypatch, klines):
    import time as _t
    monkeypatch.setattr(ai_fund, "_BENCH_CACHE", {"csi300": (_t.time(), klines)})


def test_benchmark_inception_day_uses_open_not_zero(monkeypatch):
    """开赛当天只有一根今日K：旧逻辑 start_close==last_close→bench_ret 恒 0(bug)。
    修复后基线=开盘价 4000、最新 4080 → bench_ret=+2.0，alpha 有意义。"""
    _seed_bench(monkeypatch, [{"day": "2026-06-22", "open": 4000.0, "close": 4080.0}])
    b = ai_fund._benchmark("2026-06-22T01:35:00+00:00", nav_pct=0.5)
    assert b is not None
    assert b["bench_ret"] == 2.0, "开赛日必须按开盘价算出 +2%，而非旧 bug 的 0%"
    assert b["alpha"] == round(0.5 - 2.0, 2)   # 基金 +0.5% 实则跑输大盘 2%
    assert b["series"][0] == {"date": "2026-06-22", "value": 1.0}  # 曲线以开盘锚定 1.0


def test_benchmark_multiday_baseline_from_inception_open(monkeypatch):
    """多日基金：基线=开赛日开盘价，含开赛日当日涨幅。"""
    _seed_bench(monkeypatch, [
        {"day": "2026-06-18", "open": 3900.0, "close": 3950.0},   # 开赛前，忽略
        {"day": "2026-06-20", "open": 4000.0, "close": 4100.0},   # 开赛日：基线=4000
        {"day": "2026-06-22", "open": 4150.0, "close": 4200.0},   # 最新
    ])
    b = ai_fund._benchmark("2026-06-20T01:35:00+00:00", nav_pct=3.0)
    assert b["bench_ret"] == 5.0   # 4200/4000-1 = +5%
    assert b["alpha"] == -2.0      # 基金 +3% vs 大盘 +5%


# ── A股交易规则硬闸门：涨跌停 + T+1（别让人笑话）────────────────────────────
def test_price_limit_by_board_and_seal():
    """涨跌停幅度按板块/ST；封板成交闸门。"""
    assert ai_fund._price_limit_pct("600519") == 10.0      # 主板
    assert ai_fund._price_limit_pct("300750") == 20.0      # 创业板
    assert ai_fund._price_limit_pct("688981") == 20.0      # 科创板
    assert ai_fund._price_limit_pct("830799") == 30.0      # 北交所
    assert ai_fund._price_limit_pct("600519", "ST皇庭") == 5.0  # ST
    assert ai_fund._at_upper_limit("600519", "", 9.9) is True   # 主板 +9.9%≈涨停封板
    assert ai_fund._at_upper_limit("600519", "", 8.0) is False
    assert ai_fund._at_upper_limit("300750", "", 9.9) is False  # 创业板 +9.9% 还没到 20%
    assert ai_fund._at_lower_limit("600519", "", -9.9) is True  # 跌停封板
    assert ai_fund._at_lower_limit("600519", "", None) is False


def _byd_strong(monkeypatch, extra_q=None):
    """让比亚迪 002594 形成强买入信号的通用布置。"""
    q = {"002594": {"latest": 100, "changeRatio": 4.0, "pe_ttm": 20, "pb": 3, "turnoverRatio": 5, "high": 101, "low": 96}}
    if extra_q:
        q["002594"].update(extra_q)
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP})
    monkeypatch.setattr(ai_fund, "_our_content",
        lambda name: [{"title": "比亚迪中标大订单、业绩超预期、扩产提价", "severity": "success", "age_h": 1, "id": "a", "src": "快讯"}] if name == "比亚迪" else [])
    return q


def test_upper_limit_blocks_buy(fund, monkeypatch):
    """涨停封板买不进——不模拟『买在涨停板』。"""
    _byd_strong(monkeypatch, {"changeRatio": 9.9})   # 主板涨停封板
    out = ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    assert not [t for t in out["traded"] if t["side"] == "buy" and t["symbol"] == "002594"]


def test_t1_blocks_same_day_sell(fund, monkeypatch):
    """T+1：当日买入当日不可卖出，硬止损也得等下一交易日。"""
    q = _byd_strong(monkeypatch)
    ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)            # 建仓 @100（opened_at=今天）
    q["002594"]["latest"] = 80; q["002594"]["changeRatio"] = -5.0   # 暴跌 -20%(触发硬止损信号)，但非跌停
    out = ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    assert not [t for t in out["traded"] if t["side"] == "sell" and t["symbol"] == "002594"]
    with ai_fund._connect() as conn:
        assert "002594" in {r["symbol"] for r in ai_fund._positions(conn, "main")}   # 仍持有


def test_lower_limit_blocks_sell(fund, monkeypatch):
    """跌停封板卖不出——隔日的持仓即便触发止损也只能等。"""
    q = _byd_strong(monkeypatch, {"latest": 80, "changeRatio": -9.9})   # 主板跌停封板 + 浮亏触发止损
    with ai_fund._connect() as conn:           # 注入一笔『昨天』买入的持仓(绕过 T+1)
        conn.execute("INSERT INTO aif_position (fund_id,symbol,name,qty,avg_cost,opened_at,updated_at,high_water) VALUES (?,?,?,?,?,?,?,?)",
                     ("main", "002594", "比亚迪", 100, 100.0, "2026-06-01T01:00:00Z", "2026-06-01T01:00:00Z", 100.0))
        conn.commit()
    out = ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)
    assert not [t for t in out["traded"] if t["side"] == "sell" and t["symbol"] == "002594"]   # 跌停卖不出
    with ai_fund._connect() as conn:
        assert "002594" in {r["symbol"] for r in ai_fund._positions(conn, "main")}


def test_t1_blocks_rotation_of_today_position(fund, monkeypatch):
    """换仓路径同样守 T+1：当日新仓不会被『卖最弱腾仓位』挤掉。"""
    import dataclasses
    cfg1 = dataclasses.replace(ai_fund.MAIN_CFG, max_positions=1)   # 满仓=1，方便触发换仓
    # 第一天：比亚迪强 → 建仓(占满 1 个仓位)
    q = {"002594": {"latest": 100, "changeRatio": 4.0, "pe_ttm": 20, "pb": 3, "turnoverRatio": 5, "high": 101, "low": 96},
         "300750": {"latest": 200, "changeRatio": 0.2, "pe_ttm": 25, "pb": 4, "turnoverRatio": 2, "high": 201, "low": 199}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP, "300750": {"closes": [200]*30, "flow5": 0.0}})
    monkeypatch.setattr(ai_fund, "_our_content",
        lambda name: [{"title": "比亚迪中标大订单、业绩超预期、扩产提价", "severity": "success", "age_h": 1, "id": "a", "src": "快讯"}] if name == "比亚迪" else [])
    ai_fund.run_tick(cfg=cfg1)
    with ai_fund._connect() as conn:
        assert "002594" in {r["symbol"] for r in ai_fund._positions(conn, "main")}
    # 同一天：宁德转强、比亚迪转弱 → 换仓欲卖比亚迪，但它当日建仓(T+1)→不可卖
    q["300750"]["changeRatio"] = 5.0
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": {"closes": [100]*30, "flow5": 0.0}, "300750": MD_UP})
    monkeypatch.setattr(ai_fund, "_our_content",
        lambda name: [{"title": "宁德时代储能大单、业绩超预期、扩产", "severity": "success", "age_h": 1, "id": "b", "src": "快讯"}] if name == "宁德时代" else [])
    out = ai_fund.run_tick(cfg=cfg1)
    assert not [t for t in out["traded"] if t["side"] == "sell" and t["symbol"] == "002594"]   # 当日新仓没被换掉
    with ai_fund._connect() as conn:
        assert "002594" in {r["symbol"] for r in ai_fund._positions(conn, "main")}


def test_get_snapshot_unknown_strategy_falls_back_to_main(fund):
    """未知 strategy 不崩、归一到主账户(修 _state(None)→st['cash'] TypeError)。"""
    snap = ai_fund.get_snapshot("does-not-exist-xyz")
    assert isinstance(snap, dict) and snap.get("nav_unit") is not None
    assert snap.get("fund_id") == "main"


def test_rotation_skips_when_incoming_at_upper_limit(fund, monkeypatch):
    """换仓接盘股涨停封板时不换仓——避免「卖了最弱却买不进」的空仓换仓。"""
    import dataclasses
    cfg1 = dataclasses.replace(ai_fund.MAIN_CFG, max_positions=1)
    with ai_fund._connect() as conn:   # 注入一笔『昨天』的持仓占满仓位(可被换)
        conn.execute("INSERT INTO aif_position (fund_id,symbol,name,qty,avg_cost,opened_at,updated_at,high_water) VALUES (?,?,?,?,?,?,?,?)",
                     ("main", "300750", "宁德时代", 100, 200.0, "2026-06-01T01:00:00Z", "2026-06-01T01:00:00Z", 200.0))
        conn.commit()
    # 比亚迪强信号但涨停封板(+9.9%)
    q = {"002594": {"latest": 100, "changeRatio": 9.9, "pe_ttm": 20, "pb": 3, "turnoverRatio": 5, "high": 101, "low": 96},
         "300750": {"latest": 200, "changeRatio": -1.0, "pe_ttm": 25, "pb": 4, "turnoverRatio": 2, "high": 201, "low": 199}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP, "300750": {"closes": [200]*30, "flow5": 0.0}})
    monkeypatch.setattr(ai_fund, "_our_content",
        lambda name: [{"title": "比亚迪中标大订单、业绩超预期、扩产提价", "severity": "success", "age_h": 1, "id": "a", "src": "快讯"}] if name == "比亚迪" else [])
    out = ai_fund.run_tick(cfg=cfg1)
    assert not [t for t in out["traded"] if t["side"] == "sell"]            # 没卖宁德(接盘的比亚迪买不进)
    assert "002594" not in {t["symbol"] for t in out["traded"] if t["side"] == "buy"}  # 涨停也没买进
    with ai_fund._connect() as conn:
        assert "300750" in {r["symbol"] for r in ai_fund._positions(conn, "main")}   # 宁德仍在


def test_holding_period_learning_adapts_without_close(fund, monkeypatch):
    """持仓期自适应集成：买入后浮盈，不平仓也会触发每日学习、抬高驱动维度乘子并落每日标记。"""
    q = {"002594": {"latest": 100, "pe_ttm": 20, "pb": 3, "changeRatio": 4.0, "turnoverRatio": 5, "high": 101, "low": 96}}
    _wire(monkeypatch, q)
    monkeypatch.setattr(ai_fund, "_market_data", lambda codes, priority=None: {"002594": MD_UP})
    monkeypatch.setattr(ai_fund, "_our_content",
        lambda name: [{"title": "比亚迪中标大订单、业绩超预期、扩产提价", "severity": "success", "age_h": 1, "id": "a", "src": "快讯"}] if name == "比亚迪" else [])
    ai_fund.run_tick(cfg=ai_fund.MAIN_CFG)                 # 第一次：建仓 002594 @100(浮盈≈0)
    # 制造浮盈 +10% + 把每日标记倒回，模拟次日仍持有
    q["002594"]["latest"] = 110
    with ai_fund._connect() as conn:
        conn.execute("UPDATE aif_state SET hold_learn_date=NULL WHERE fund_id=?", ("main",)); conn.commit()
    ai_fund.run_tick(trade=False, cfg=ai_fund.MAIN_CFG)    # 第二次：不交易、仅持仓期学习
    with ai_fund._connect() as conn:
        row = conn.execute("SELECT learned_weights, hold_learn_date FROM aif_state WHERE fund_id=?", ("main",)).fetchone()
    import datetime as _dt
    assert row["hold_learn_date"] == _dt.datetime.now(ai_fund.BJ_TZ).strftime("%Y-%m-%d")  # 已落每日标记
    learned = ai_fund._loadj(row["learned_weights"], {})
    assert learned and max(learned.values()) > 1.0        # 浮盈→至少一个驱动维度乘子被抬高
