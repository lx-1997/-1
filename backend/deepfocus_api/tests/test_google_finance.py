"""Google Finance 行情解析单测（纯函数，不触网）。

样本取自真实页面的内嵌 JSON 片段，验证正则对两种 exchange 格式
（"NASDAQ" 与 ["NYSE"]）、unicode 转义名、多市场代码均稳健。
"""
from deepfocus_api.google_finance import _clean_symbol, _guess_exchanges, _parse


# 真实页面片段（AAPL：exchange 不带括号）
_AAPL = '...["AAPL","NASDAQ"],"Apple Inc",0,"USD",[311.23,0.9700012,0.31264138,2,2,2],null,310.26,"#666666","US"...'
# 真实页面片段（JPM：exchange 带括号 + name 含 \\u0026）
_JPM = '["JPM",["NYSE"]],"JPMorgan Chase \\u0026 Co",0,"USD",[310.89,10.04,3.33,2,2,2],null,300.85,"#666"'


def test_parse_extracts_real_quote():
    q = _parse(_AAPL, "AAPL")
    assert q is not None
    assert q["price"] == 311.23
    assert q["previous_close"] == 310.26
    assert round(q["change"], 2) == 0.97
    assert round(q["change_percent"], 2) == 0.31
    assert q["currency"] == "USD"
    assert q["name"] == "Apple Inc"
    assert q["provider"] == "google-finance"


def test_parse_handles_bracket_exchange_and_unicode_name():
    q = _parse(_JPM, "JPM")
    assert q is not None
    assert q["price"] == 310.89
    assert "&" in q["name"]  # & 解码为 &
    assert q["change"] > 0  # 310.89 > 300.85，上涨


def test_parse_none_when_no_data():
    assert _parse("nothing relevant here", "AAPL") is None


def test_guess_exchanges_multi_market():
    assert _guess_exchanges("AAPL") == ["NASDAQ", "NYSE"]
    assert _guess_exchanges("0700", "HK") == ["HKG"]
    assert _guess_exchanges("600519", "CN") == ["SHA"]
    assert _guess_exchanges("000001", "CN") == ["SHE"]
    assert _guess_exchanges("600519") == ["SHA"]  # 6 开头自动判 A股上海


def test_clean_symbol_strips_suffix():
    assert _clean_symbol("AAPL.US") == "AAPL"
    assert _clean_symbol("0700.HK") == "0700"
    assert _clean_symbol(" tsla ") == "TSLA"
