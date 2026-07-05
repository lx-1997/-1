"""东方财富 龙虎榜席位明细（游资席位）——买卖 top 营业部明细。

只取个股【最近一次上榜】（近30天内，否则返回 None）。给微信/终端 AI 智能体回答
「某某股近期龙虎榜/游资席位/谁在买谁在卖」这类高频问法，避免模型凭空编造席位。

直连绕代理（httpx trust_env=False，外部 403 是沙箱代理封的）。仅 A股（6 位数字代码）。
来源 datacenter-web.eastmoney.com：
  - RPT_DAILYBILLBOARD_DETAILSNEW  最近上榜日期/原因/净额（先定位最近 TRADE_DATE）
  - RPT_BILLBOARD_DAILYDETAILSBUY  当日买入 top 营业部明细
  - RPT_BILLBOARD_DAILYDETAILSSELL 当日卖出 top 营业部明细
缓存 1h。
"""
from __future__ import annotations

import re
import time
from datetime import datetime
from typing import Optional

import httpx

try:
    from deepfocus_api.shared_utils import safe_float
except Exception:  # pragma: no cover - 容错导入
    def safe_float(value, default=None):  # type: ignore
        try:
            if value is None or value == "":
                return default
            return float(value)
        except (TypeError, ValueError):
            return default


_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 3600.0  # 1h
_RECENT_DAYS = 30
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://data.eastmoney.com/",
}
_DATACENTER = "https://datacenter-web.eastmoney.com/api/data/v1/get"


def _parse_date(raw: Optional[str]) -> str:
    """东财日期形如 '2026-06-26 00:00:00' → '2026-06-26'。"""
    return (raw or "")[:10]


def _seat_rows(data: list, kind: str) -> list:
    """把营业部明细行规整成 {name, buy, sell, net}，按 net 绝对额排序（买榜降序/卖榜按卖额降序）。"""
    out = []
    for row in data or []:
        name = (row.get("OPERATEDEPT_NAME") or "").strip()
        if not name:
            continue
        out.append(
            {
                "name": name,
                "buy": safe_float(row.get("BUY")),
                "sell": safe_float(row.get("SELL")),
                "net": safe_float(row.get("NET")),
            }
        )
    if kind == "buy":
        out.sort(key=lambda s: (s.get("buy") or 0.0), reverse=True)
    else:
        out.sort(key=lambda s: (s.get("sell") or 0.0), reverse=True)
    return out


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


async def fetch_daily_billboard(date: str = "", limit: int = 100) -> Optional[dict]:
    """某交易日 A股龙虎榜【全榜单】（date 空 = 最近交易日）——/lhb 公开页 + 自选巡检共用。

    同一 datacenter 接口 RPT_DAILYBILLBOARD_DETAILSNEW，按 TRADE_DATE 取整日榜单，
    每行提取 代码/名称/涨跌幅/上榜原因/净买额（同股多原因多行 → 按代码去重保留净买额最大行）。

    返回（JSON-able dict）::

        {"date": "2026-07-02", "count": 87,
         "items": [{"code","name","change_rate","reason","net"}, ...],  # net 单位：元
         "provider": "eastmoney"}

    任何失败/该日无榜 → None（绝不抛异常，优雅降级）。缓存 1h（复用 _CACHE/_CACHE_TTL）。
    """
    d = (date or "").strip()[:10]
    if d and not _DATE_RE.match(d):
        return None
    lim = max(1, min(int(limit or 100), 500))

    cache_key = f"daily:{d}:{lim}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            trade_date = d
            if not trade_date:
                # 先按日期倒序取 1 条拿到最近交易日
                head_url = (
                    f"{_DATACENTER}?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageSize=1"
                    "&sortColumns=TRADE_DATE&sortTypes=-1"
                )
                r = await client.get(head_url, headers=_HEADERS)
                rows = ((r.json().get("result") or {}).get("data")) if r.status_code == 200 else []
                trade_date = _parse_date((rows[0] or {}).get("TRADE_DATE")) if rows else ""
            if not trade_date:
                _CACHE[cache_key] = (time.time(), None)
                return None

            day_url = (
                f"{_DATACENTER}?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL"
                f"&pageSize={lim}&sortColumns=BILLBOARD_NET_AMT&sortTypes=-1"
                f"&filter=(TRADE_DATE%3D%27{trade_date}%27)"
            )
            r = await client.get(day_url, headers=_HEADERS)
            data = ((r.json().get("result") or {}).get("data")) if r.status_code == 200 else []

            items: list = []
            seen: dict = {}  # code → index（同股多原因去重，保留净买额绝对值更大的一行）
            for row in data or []:
                if not isinstance(row, dict):
                    continue
                code = str(row.get("SECURITY_CODE") or "").strip()
                name = str(row.get("SECURITY_NAME_ABBR") or "").strip()
                if not code or not name:
                    continue
                item = {
                    "code": code,
                    "name": name,
                    "change_rate": safe_float(row.get("CHANGE_RATE")),
                    "reason": str(row.get("EXPLANATION") or row.get("EXPLAIN") or "").strip(),
                    "net": safe_float(row.get("BILLBOARD_NET_AMT")),
                }
                if code in seen:
                    prev = items[seen[code]]
                    if abs(item.get("net") or 0.0) > abs(prev.get("net") or 0.0):
                        items[seen[code]] = item
                    continue
                seen[code] = len(items)
                items.append(item)
            if items:
                result = {
                    "date": trade_date,
                    "count": len(items),
                    "items": items,
                    "provider": "eastmoney",
                }
    except Exception:
        result = None

    _CACHE[cache_key] = (time.time(), result)
    return result


async def fetch_dragon_tiger(symbol: str, market: Optional[str] = None, limit: int = 5) -> Optional[dict]:
    """A股个股最近一次龙虎榜席位明细（近30天内，否则 None）。

    返回（JSON-able dict，给模型看）::

        {
          "code": "301526", "name": "国际复材", "date": "2026-06-26",
          "reason": "日涨幅偏离值达到7%...", "change_rate": 11.3,
          "net": 1234.5,            # 龙虎榜买卖净额（万元下游可自行换算，这里原始为元）
          "buy_amount": ..., "sell_amount": ...,
          "buy_seats":  [{"name","buy","sell","net"}, ...],   # 买入 top 营业部
          "sell_seats": [{"name","buy","sell","net"}, ...],   # 卖出 top 营业部
          "summary": "国际复材 2026-06-26 上榜……（截断摘要）",
          "provider": "eastmoney"
        }

    任何失败/无近期上榜 → None（绝不抛异常，优雅降级）。
    """
    code = re.sub(r"\D", "", symbol or "")
    # 仅 A股 6 位数字代码（含可转债 12x/11x 也是 6 位，东财同源可查；非6位直接放弃）
    if not code or len(code) != 6:
        return None

    cache_key = f"{code}:{int(limit)}"
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            # 1) 定位最近一次上榜（日期/原因/净额/名称）
            head_url = (
                f"{_DATACENTER}?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageSize=1"
                "&sortColumns=TRADE_DATE&sortTypes=-1"
                f"&filter=(SECURITY_CODE%3D%22{code}%22)"
            )
            r = await client.get(head_url, headers=_HEADERS)
            head = None
            if r.status_code == 200:
                rows = ((r.json().get("result") or {}).get("data")) or []
                head = rows[0] if rows else None
            if not head:
                _CACHE[cache_key] = (time.time(), None)
                return None

            trade_date = _parse_date(head.get("TRADE_DATE"))
            try:
                recent = (datetime.now() - datetime.strptime(trade_date, "%Y-%m-%d")).days <= _RECENT_DAYS
            except ValueError:
                recent = False
            if not recent:
                # 上榜过于久远，对当前研判无参考意义 → 不返回席位（防过期数据误导）
                _CACHE[cache_key] = (time.time(), None)
                return None

            name = head.get("SECURITY_NAME_ABBR")
            reason = head.get("EXPLANATION") or head.get("EXPLAIN")
            net = safe_float(head.get("BILLBOARD_NET_AMT"))
            buy_amount = safe_float(head.get("BILLBOARD_BUY_AMT"))
            sell_amount = safe_float(head.get("BILLBOARD_SELL_AMT"))
            change_rate = safe_float(head.get("CHANGE_RATE"))

            # 2) 当日买/卖席位明细
            date_filter = f"(SECURITY_CODE%3D%22{code}%22)(TRADE_DATE%3D%27{trade_date}%27)"
            buy_url = (
                f"{_DATACENTER}?reportName=RPT_BILLBOARD_DAILYDETAILSBUY&columns=ALL"
                f"&pageSize=50&filter={date_filter}"
            )
            sell_url = (
                f"{_DATACENTER}?reportName=RPT_BILLBOARD_DAILYDETAILSSELL&columns=ALL"
                f"&pageSize=50&filter={date_filter}"
            )
            rb = await client.get(buy_url, headers=_HEADERS)
            rs = await client.get(sell_url, headers=_HEADERS)
            buy_data = ((rb.json().get("result") or {}).get("data")) if rb.status_code == 200 else []
            sell_data = ((rs.json().get("result") or {}).get("data")) if rs.status_code == 200 else []

            lim = max(1, int(limit or 5))
            buy_seats = _seat_rows(buy_data or [], "buy")[:lim]
            sell_seats = _seat_rows(sell_data or [], "sell")[:lim]

            # 没拿到任何席位明细 → 仍返回头部信息（净额/原因），席位空列表（诚实降级）
            top_buy = buy_seats[0]["name"] if buy_seats else "—"
            top_sell = sell_seats[0]["name"] if sell_seats else "—"
            summary = (
                f"{name or code} {trade_date} 龙虎榜上榜"
                + (f"（{reason}）" if reason else "")
                + f"，买卖净额约 {(net or 0) / 1e8:.2f} 亿元；"
                + f"买一席位「{top_buy}」、卖一席位「{top_sell}」。"
            )

            result = {
                "code": code,
                "name": name,
                "date": trade_date,
                "reason": reason,
                "change_rate": change_rate,
                "net": net,
                "buy_amount": buy_amount,
                "sell_amount": sell_amount,
                "buy_seats": buy_seats,
                "sell_seats": sell_seats,
                "summary": summary[:300],
                "provider": "eastmoney",
            }
    except Exception:
        result = None

    _CACHE[cache_key] = (time.time(), result)
    return result
