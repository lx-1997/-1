"""泄密护栏回归守卫:工具结果剥源(provider/source) + 输出文本剥内部标识(数据源/工具名/密钥)。

红线:微信/web AI 回答绝不透露数据服务商名、内部工具名、密钥。
保守原则:不误伤与股票名重名的词(东方财富/同花顺/雪球 这类不剥)。
"""
from __future__ import annotations

from deepfocus_api import privacy_guard as pg


# ---------- 工具结果剥源(回灌模型前) ----------

def test_scrub_internal_fields_strips_provider_source_recursive():
    raw = {
        "ok": True,
        "data": {
            "pe_ratio": 25, "provider": "ifind", "source": "同花顺 iFinD 实时",
            "quotes": [{"symbol": "AAPL", "price": 150, "provider_name": "同花顺 iFinD 实时"}],
            "cashflow": {"ocf": 100, "source": "ifind"},
        },
    }
    out = pg.scrub_internal_fields(raw)
    flat = str(out)
    assert "ifind" not in flat.lower() and "同花顺" not in flat
    # 数据值保留
    assert out["data"]["pe_ratio"] == 25 and out["data"]["quotes"][0]["price"] == 150
    assert out["ok"] is True


def test_scrub_internal_fields_keeps_source_name():
    # source_name = 新闻出处(路透/彭博)，是合法引用，必须保留
    raw = {"data": {"voices": [{"title": "x", "source_name": "路透", "url": "http://e"}]}}
    out = pg.scrub_internal_fields(raw)
    assert out["data"]["voices"][0]["source_name"] == "路透"


# ---------- 输出文本剥内部标识 ----------

def test_scrub_text_strips_data_sources():
    s = "据 同花顺 iFinD 实时数据，PE 18.8；研报来自东方财富研报库；知识星球海外投行。"
    out = pg.scrub_internal_text(s)
    assert "iFinD" not in out and "东方财富研报库" not in out and "知识星球" not in out
    assert "公开" in out  # 替换成了中性词


def test_scrub_text_strips_tool_names_and_secrets():
    s = "我调用了 get_market_quote 和 assess_long_term_bull；api_key=sk_live_abc123def456。"
    out = pg.scrub_internal_text(s)
    assert "get_market_quote" not in out and "assess_long_term_bull" not in out
    assert "sk_live_abc123def456" not in out and "[已隐去]" in out


def test_scrub_text_idempotent_and_preserves_normal():
    # 保守:不误伤与股票名重名的词(东方财富/同花顺 作为公司名应保留)
    s = "贵州茅台 PE 18.8，护城河深厚，长线底仓。"
    assert pg.scrub_internal_text(s) == s
    once = pg.scrub_internal_text("据 iFinD 数据")
    assert pg.scrub_internal_text(once) == once  # 幂等
