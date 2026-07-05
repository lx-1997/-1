"""个股日线 K 线端点 + 东财/新浪 OHLC 解析（真 K 线接管子，取代 KlineChart 假数据）。

A股 K 线主源=新浪（push2his 对生产 IP 间歇空回），东财兜底；港股东财；美股暂不接入。
"""
import asyncio

from fastapi.testclient import TestClient

from deepfocus_api import eastmoney_data
from deepfocus_api.auth import is_public_path
from deepfocus_api.main import app

client = TestClient(app)

_FAKE = [
    {"d": "2026-06-25", "o": 10.0, "c": 10.5, "h": 10.8, "l": 9.9, "v": 12345.0},
    {"d": "2026-06-26", "o": 10.5, "c": 10.2, "h": 10.6, "l": 10.1, "v": 9876.0},
]


def test_kline_ashare_prefers_sina(monkeypatch):
    async def fake_sina(symbol, points=160):
        assert symbol == "600519"
        return _FAKE

    async def fake_em(secid, points=160):
        raise AssertionError("新浪已成功就不该再走东财")

    monkeypatch.setattr(eastmoney_data, "fetch_sina_ohlc", fake_sina)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_ohlc", fake_em)
    r = client.get("/api/market/kline", params={"symbol": "600519", "market": "CN"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "sina"
    assert body["is_realtime"] is False
    assert body["points"] == 2
    assert body["candles"][0]["c"] == 10.5


def test_kline_falls_back_to_eastmoney(monkeypatch):
    async def fake_sina(symbol, points=160):
        return []  # 新浪空回（被封/限流）

    async def fake_em(secid, points=160):
        assert secid == "1.600519"
        return _FAKE

    monkeypatch.setattr(eastmoney_data, "fetch_sina_ohlc", fake_sina)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_ohlc", fake_em)
    r = client.get("/api/market/kline", params={"symbol": "600519", "market": "CN"})
    body = r.json()
    assert body["source"] == "eastmoney"
    assert body["points"] == 2


def test_kline_us_unsupported_skips_both(monkeypatch):
    called = {"sina": 0, "em": 0}

    async def fake_sina(symbol, points=160):
        called["sina"] += 1
        return _FAKE

    async def fake_em(secid, points=160):
        called["em"] += 1
        return _FAKE

    monkeypatch.setattr(eastmoney_data, "fetch_sina_ohlc", fake_sina)
    monkeypatch.setattr(eastmoney_data, "fetch_eastmoney_ohlc", fake_em)
    r = client.get("/api/market/kline", params={"symbol": "AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "unsupported"
    assert body["candles"] == []
    assert called == {"sina": 0, "em": 0}  # 美股不打任何 A股源


def test_kline_empty_symbol_400():
    r = client.get("/api/market/kline", params={"symbol": "   "})
    assert r.status_code == 400


def test_kline_is_public_freemium():
    # 终端免费层须能直接拉 K 线（精确放行，非前缀）
    assert is_public_path("/api/market/kline") is True


class _FakeResp:
    def __init__(self, text, status=200):
        self.status_code = status
        self.text = text

    def json(self):
        import json
        return json.loads(self.text)


class _FakeClient:
    def __init__(self, resp):
        self._resp = resp

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        return self._resp


def test_eastmoney_ohlc_field_order_d_o_c_h_l_v(monkeypatch):
    """锁死东财 fields2=f51,f52,f53,f54,f55,f56 = 日期/开/收/高/低/量 的解析顺序。"""
    resp = _FakeResp('{"data": {"klines": ["2026-06-25,10.00,10.50,10.80,9.90,12345","2026-06-26,10.50,10.20,10.60,10.10,9876"]}}')
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient", _FakeClient(resp))
    eastmoney_data._CACHE.clear()
    rows = asyncio.run(eastmoney_data.fetch_eastmoney_ohlc("1.600519", points=5))
    assert rows[0] == {"d": "2026-06-25", "o": 10.0, "c": 10.5, "h": 10.8, "l": 9.9, "v": 12345.0}
    assert rows[1]["c"] == 10.2 and rows[1]["v"] == 9876.0


def test_sina_ohlc_parses_day_open_high_low_close_volume(monkeypatch):
    """新浪 getKLineData 解析：day/open/high/low/close/volume → {d,o,c,h,l,v}。"""
    resp = _FakeResp('[{"day":"2026-06-25","open":"10.00","high":"10.80","low":"9.90","close":"10.50","volume":"12345"},'
                     '{"day":"2026-06-26","open":"10.50","high":"10.60","low":"10.10","close":"10.20","volume":"9876"}]')
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient", _FakeClient(resp))
    eastmoney_data._CACHE.clear()
    rows = asyncio.run(eastmoney_data.fetch_sina_ohlc("600519", points=5))
    assert rows[0] == {"d": "2026-06-25", "o": 10.0, "c": 10.5, "h": 10.8, "l": 9.9, "v": 12345.0}
    assert len(rows) == 2

    # 非 6 位（港美股）不走新浪
    assert asyncio.run(eastmoney_data.fetch_sina_ohlc("AAPL")) == []
