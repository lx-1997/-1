"""Yahoo Finance symbol 映射单测（纯函数，不触网）。"""
from deepfocus_api.yahoo_finance import _to_yahoo_symbol


def test_to_yahoo_symbol_multi_market():
    assert _to_yahoo_symbol("AAPL") == "AAPL"
    assert _to_yahoo_symbol("AAPL.US") == "AAPL"
    assert _to_yahoo_symbol("0700", "HK") == "0700.HK"
    assert _to_yahoo_symbol("600519", "CN") == "600519.SS"  # 6 开头 → 沪
    assert _to_yahoo_symbol("000001", "CN") == "000001.SZ"  # 0 开头 → 深
    assert _to_yahoo_symbol("600519") == "600519.SS"  # 6 位数字自动判 A股
    assert _to_yahoo_symbol("0700.HK") == "0700.HK"  # 已带后缀原样
    assert _to_yahoo_symbol(" tsla ") == "TSLA"
