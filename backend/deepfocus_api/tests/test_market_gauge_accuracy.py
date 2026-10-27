"""市场仪表准度：datacenter 翻页全量 + 盘中量能归一 + 时段判定。"""
import asyncio
from datetime import datetime, timedelta, timezone

from deepfocus_api import market_extras as mx
from deepfocus_api.cn_calendar import _fetch_rows


# ---------------- 盘中已交易占比 ----------------

def test_intraday_elapsed_ratio():
    BJ = timezone(timedelta(hours=8))
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 27, 10, 0, tzinfo=BJ)) == 0.125    # 开盘 30min
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 27, 12, 0, tzinfo=BJ)) == 0.5      # 午间(上午收满)
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 27, 13, 0, tzinfo=BJ)) == 0.5      # 下午开盘
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 27, 15, 0, tzinfo=BJ)) == 1.0      # 收盘
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 27, 16, 0, tzinfo=BJ)) is None     # 盘后
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 27, 9, 0, tzinfo=BJ)) is None      # 盘前
    assert mx._intraday_elapsed_ratio(datetime(2026, 10, 31, 10, 0, tzinfo=BJ)) is None     # 周六


def test_adj_heat_ratio():
    # 上午 10 点(raw 0.4，半日量) → 还原全日口径 0.4/0.15（clamp 下限 0.15：开盘一刻钟内放大有意封顶）
    assert mx._adj_heat_ratio(0.4, 0.125) == 0.4 / 0.15
    # 刚开盘 elapsed 极小 → clamp 到 0.15 防爆
    assert mx._adj_heat_ratio(0.06, 0.01) == 0.4
    # 收盘后/非交易时段 → 原样
    assert mx._adj_heat_ratio(1.086, None) == 1.086
    assert mx._adj_heat_ratio(1.086, 1.0) == 1.086


# ---------------- datacenter 翻页 ----------------

class _FakeResp:
    def __init__(self, payload, status=200):
        self._p = payload
        self.status_code = status

    def json(self):
        return self._p


class _FakeClient:
    """按 ?page= 返回预设页；记录调用序列。"""
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get(self, url, headers=None):
        page = 1
        if "&page=" in url:
            page = int(url.split("&page=")[1].split("&")[0])
        self.calls.append(page)
        return _FakeResp(self.pages.get(page, {"success": True, "result": {"data": [], "pages": page}}))


def test_fetch_rows_follows_pages():
    rows_a = [{"i": i} for i in range(100)]
    rows_b = [{"i": 100 + i} for i in range(37)]
    client = _FakeClient({
        1: {"success": True, "result": {"data": rows_a, "pages": 2}},
        2: {"success": True, "result": {"data": rows_b, "pages": 2}},
    })
    out = asyncio.run(_fetch_rows(client, "RPT_LIFT_STAGE", "(x)", "FREE_DATE"))
    assert len(out) == 137 and client.calls == [1, 2]


def test_fetch_rows_stops_when_no_more_pages_field():
    client = _FakeClient({1: {"success": True, "result": {"data": [{"i": 1}]}}})   # 无 pages 字段 → 默认 1 页
    out = asyncio.run(_fetch_rows(client, "R", "(x)", "D"))
    assert len(out) == 1 and client.calls == [1]


def test_fetch_rows_failure_returns_empty():
    client = _FakeClient({1: {"success": False, "result": {}}})
    out = asyncio.run(_fetch_rows(client, "R", "(x)", "D"))
    assert out == []
