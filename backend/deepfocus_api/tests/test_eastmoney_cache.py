"""eastmoney_data 缓存分层 + 数据时点标注。

覆盖：①盘中/休市 TTL 选择（行情类盘中 15min、休市 6h；TTL 在读取时评估——早晨写入的缓存
一开盘即按盘中口径过期）②OHLC 最新蜡烛 fetched_at / fund_flow as_of 字段（mock HTTP 不打真网络）
③财报等慢数据不受盘中短 TTL 影响仍走 6h。
"""
import asyncio
import re
import time
from datetime import datetime

from deepfocus_api import eastmoney_data


def _bj(y, m, d, hh, mm):
    return datetime(y, m, d, hh, mm, tzinfo=eastmoney_data._BJ_TZ)


# ---------------------------------------------------------------- mock HTTP 基建

class _FakeResp:
    def __init__(self, text, status=200):
        self.status_code = status
        self.text = text

    def json(self):
        import json
        return json.loads(self.text)


class _FakeClient:
    """所有 GET 返回同一响应，并计数（验证是否真的打了'网络'）。"""

    def __init__(self, resp, counter=None):
        self._resp = resp
        self._counter = counter if counter is not None else {"n": 0}

    def __call__(self, *a, **k):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, *a, **k):
        self._counter["n"] += 1
        return self._resp


class _RouteClient(_FakeClient):
    """按 URL 子串分发响应（fund_flow 一个 client 打两个不同端点）。"""

    def __init__(self, routes, counter=None):
        super().__init__(None, counter)
        self._routes = routes  # [(url 子串, _FakeResp)]

    async def get(self, url, *a, **k):
        self._counter["n"] += 1
        for sub, resp in self._routes:
            if sub in url:
                return resp
        return _FakeResp("{}", 404)


# ---------------------------------------------------------------- ① 盘中判定 + TTL 选择

def test_in_cn_session_boundaries():
    # 2026-07-01 是周三、2026-07-04 是周六
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 1, 10, 30)) is True    # 周三盘中
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 1, 9, 15)) is True     # 下边界含
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 1, 15, 5)) is True     # 上边界含
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 1, 9, 14)) is False    # 开盘前
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 1, 15, 6)) is False    # 收盘后
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 1, 20, 0)) is False    # 夜间
    assert eastmoney_data._in_cn_session(_bj(2026, 7, 4, 10, 30)) is False   # 周六


def test_market_ttl_intraday_vs_offsession(monkeypatch):
    monkeypatch.setattr(eastmoney_data, "_in_cn_session", lambda now=None: True)
    assert eastmoney_data._market_ttl() == 900.0
    monkeypatch.setattr(eastmoney_data, "_in_cn_session", lambda now=None: False)
    assert eastmoney_data._market_ttl() == 6 * 3600.0


def test_ohlc_cache_expires_faster_intraday(monkeypatch):
    """同一条 1000s 前的缓存：休市命中（<6h 不打网络），盘中过期（>900s 重新取数）。"""
    counter = {"n": 0}
    resp = _FakeResp('{"data":{"klines":["2026-06-25,10.00,10.50,10.80,9.90,12345"]}}')
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient", _FakeClient(resp, counter))
    eastmoney_data._CACHE.clear()
    stale = [{"d": "2026-06-24", "o": 1.0, "c": 1.0, "h": 1.0, "l": 1.0, "v": 0.0}]
    eastmoney_data._CACHE["ohlc:1.600519"] = (time.time() - 1000.0, stale)

    monkeypatch.setattr(eastmoney_data, "_in_cn_session", lambda now=None: False)  # 休市 → 命中
    rows = asyncio.run(eastmoney_data.fetch_eastmoney_ohlc("1.600519", points=5))
    assert rows == stale and counter["n"] == 0

    monkeypatch.setattr(eastmoney_data, "_in_cn_session", lambda now=None: True)   # 盘中 → 过期重取
    rows = asyncio.run(eastmoney_data.fetch_eastmoney_ohlc("1.600519", points=5))
    assert counter["n"] >= 1
    assert rows[0]["c"] == 10.5  # 拿到的是新数据非 stale


def test_slow_data_keeps_6h_ttl_even_intraday(monkeypatch):
    """财报（慢数据）盘中不受 15min 短 TTL 影响：1000s 前的缓存仍命中、不打网络。"""
    counter = {"n": 0}
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient",
                        _FakeClient(_FakeResp('{"result":{"data":[]}}'), counter))
    monkeypatch.setattr(eastmoney_data, "_in_cn_session", lambda now=None: True)
    eastmoney_data._CACHE.clear()
    cached = {"report_date": "2026-03-31", "eps": 1.0}
    eastmoney_data._CACHE["a:600519"] = (time.time() - 1000.0, cached)
    out = asyncio.run(eastmoney_data.fetch_eastmoney_earnings("600519"))
    assert out == cached and counter["n"] == 0


# ---------------------------------------------------------------- ② as_of / fetched_at

_ISO_BJ = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+08:00$")


def test_eastmoney_ohlc_fetched_at_on_latest_candle(monkeypatch):
    resp = _FakeResp('{"data":{"klines":["2026-06-25,10.00,10.50,10.80,9.90,12345",'
                     '"2026-06-26,10.50,10.20,10.60,10.10,9876"]}}')
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient", _FakeClient(resp))
    eastmoney_data._CACHE.clear()
    rows = asyncio.run(eastmoney_data.fetch_eastmoney_ohlc("1.600519", points=5))
    assert "fetched_at" not in rows[0]                # 历史蜡烛不加（向后兼容 + 省载荷）
    assert _ISO_BJ.match(rows[-1]["fetched_at"])      # 最新一根带北京时间 ISO 时点
    # 既有字段一个不动
    assert rows[-1]["d"] == "2026-06-26" and rows[-1]["c"] == 10.2 and rows[-1]["v"] == 9876.0


def test_sina_ohlc_fetched_at_on_latest_candle(monkeypatch):
    resp = _FakeResp('[{"day":"2026-06-25","open":"10.00","high":"10.80","low":"9.90","close":"10.50","volume":"12345"},'
                     '{"day":"2026-06-26","open":"10.50","high":"10.60","low":"10.10","close":"10.20","volume":"9876"}]')
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient", _FakeClient(resp))
    eastmoney_data._CACHE.clear()
    rows = asyncio.run(eastmoney_data.fetch_sina_ohlc("600519", points=5))
    assert "fetched_at" not in rows[0]
    assert _ISO_BJ.match(rows[-1]["fetched_at"])
    assert rows[-1]["d"] == "2026-06-26" and rows[-1]["c"] == 10.2


def test_fund_flow_has_as_of(monkeypatch):
    routes = [
        ("fflow/daykline", _FakeResp('{"data":{"klines":["2026-06-24,1000000","2026-06-25,2000000"]}}')),
        ("datacenter-web", _FakeResp('{"result":{"data":[]}}')),  # 无龙虎榜
    ]
    monkeypatch.setattr(eastmoney_data.httpx, "AsyncClient", _RouteClient(routes))
    eastmoney_data._CACHE.clear()
    out = asyncio.run(eastmoney_data.fetch_fund_flow("600519"))
    assert out is not None
    assert _ISO_BJ.match(out["as_of"])
    # 既有字段不变（向后兼容）
    assert out["main_flow_5d"] == 3000000.0 and out["flow_days"] == 2
    assert out["provider"] == "eastmoney" and out["unit"] == "元"
    assert out["main_flow_5d_yi"] == 0.03
