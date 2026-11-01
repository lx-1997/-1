"""Raw A-share market structure snapshot shared by market tools."""
from __future__ import annotations
import asyncio
import json
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
import httpx
from . import ifind_api
from .eastmoney_data import fetch_eastmoney_index

CN_TZ = timezone(timedelta(hours=8))

_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

_INDICES = [
    ("上证指数", "1.000001"),
    ("深证成指", "0.399001"),
    ("创业板指", "0.399006"),
    ("沪深300", "1.000300"),
    ("科创50", "1.000688"),
    ("北证50", "0.899050"),
]

_A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"

_BOARD_FS = "m:90+t:2"

_SINA_INDICES = [
    ("上证指数", "sh000001"), ("深证成指", "sz399001"), ("创业板指", "sz399006"),
    ("沪深300", "sh000300"), ("科创50", "sh000688"), ("北证50", "bj899050"),
]

_SINA_HQ = "http://hq.sinajs.cn/list="

_SINA_NODE = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"

_SINA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "http://finance.sina.com.cn/"}

_IFIND_INDICES = [
    ("上证指数", "000001.SH"), ("深证成指", "399001.SZ"), ("创业板指", "399006.SZ"),
    ("沪深300", "000300.SH"), ("科创50", "000688.SH"), ("北证50", "899050.BJ"),
]

_EM_CLIST_HOSTS = ("push2.eastmoney.com", "push2delay.eastmoney.com")

_SINA_KLINE_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "https://finance.sina.com.cn/"}

def cn_now() -> datetime:
    return datetime.now(CN_TZ)

def cn_today_str() -> str:
    return cn_now().strftime("%Y-%m-%d")

def _clist_url(fs: str, fields: str, fid: str, po: str, pn: int, pz: int, host: str = _EM_CLIST_HOSTS[0]) -> str:
    return (
        f"https://{host}/api/qt/clist/get"
        f"?pn={pn}&pz={pz}&po={po}&np=1&fltt=2&invt=2&fid={fid}"
        f"&fs={fs}&fields={fields}"
    )

async def _em_clist(fs: str, fields: str, *, fid: str = "f3", po: str = "1", pz: int = 60) -> list[dict]:
    """东财 clist 单页拉取（按 fid 排序，po=1 降序/0 升序）。直连绕代理；失败 → []。

    host 兜底链：push2 对生产 IP 间歇性直接断连（2026-09-10 实测 RemoteProtocolError，
    板块/涨跌家数整段空 → 复盘落了 breadth=None 的半成品）。push2delay 是同一接口的
    延时行情镜像，行情字段（f3/f62/f104/f105/f106）收盘后与 push2 一致，作为兜底。"""
    for host in _EM_CLIST_HOSTS:
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
                r = await client.get(_clist_url(fs, fields, fid, po, 1, pz, host), headers=_HEADERS)
            if r.status_code != 200:
                continue
            diff = (((r.json() or {}).get("data") or {}).get("diff")) or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            rows = [d for d in diff if isinstance(d, dict)]
            if rows:
                return rows
        except Exception:
            continue
    return []

def _num(v: Any) -> Optional[float]:
    try:
        if v in (None, "-", ""):
            return None
        return float(v)
    except (ValueError, TypeError):
        return None

async def _ifind_indices() -> list[dict]:
    """首选兜底：iFinD 指数实时（latest/preClose/changeRatio）。东财 push2his 指数被封时用。
    账号已实测有指数权限；未配置/失败 → []（继续回退新浪）。iFinD 客户端是同步 httpx，丢线程跑。"""
    try:
        from . import ifind_api
        if not ifind_api.enabled():
            return []
        codes = ",".join(c for _, c in _IFIND_INDICES)
        res = await asyncio.to_thread(
            ifind_api.real_time_quote, codes, "latest,preClose,changeRatio"
        )
    except Exception:
        return []
    if not res.get("ok"):
        return []
    by_code = {str(r.get("code")): r for r in (res.get("rows") or [])}
    out: list[dict] = []
    for name, code in _IFIND_INDICES:
        r = by_code.get(code)
        if not r:
            continue
        cur = _num(r.get("latest")); prev = _num(r.get("preClose")); chg = _num(r.get("changeRatio"))
        if cur is None:
            continue
        pct = round(chg, 2) if chg is not None else (round((cur / prev - 1) * 100, 2) if prev else None)
        out.append({"name": name, "close": round(cur, 2), "pct": pct, "date": cn_today_str()})
    return out

async def _sina_indices() -> list[dict]:
    """备用：新浪指数快照（current/prevclose 算 pct）。东财指数失败时兜底。"""
    out: list[dict] = []
    codes = ",".join(c for _, c in _SINA_INDICES)
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(_SINA_HQ + codes, headers=_SINA_HEADERS)
        text = r.text if r.status_code == 200 else ""
    except Exception:
        text = ""
    by_code = {}
    for line in text.splitlines():
        m = re.search(r'hq_str_(\w+)="([^"]*)"', line)
        if m:
            by_code[m.group(1)] = m.group(2).split(",")
    for name, code in _SINA_INDICES:
        parts = by_code.get(code) or []
        # 指数格式：名称,今开,昨收,现价,最高,最低,...
        if len(parts) >= 4:
            try:
                cur = float(parts[3]); prev = float(parts[2])
                pct = round((cur / prev - 1) * 100, 2) if prev else None
                out.append({"name": name, "close": round(cur, 2), "pct": pct, "date": cn_today_str()})
            except (ValueError, IndexError):
                pass
    return out

def _parse_sina_movers(arr: Any) -> list[dict]:
    out: list[dict] = []
    if not isinstance(arr, list):
        return out
    for d in arr:
        if not isinstance(d, dict):
            continue
        code = str(d.get("code") or "")
        pct = _num(d.get("changepercent"))
        if not code or pct is None:
            continue
        out.append({"code": code, "name": str(d.get("name") or ""), "pct": round(pct, 2), "price": _num(d.get("trade"))})
    return out

async def _sina_movers() -> dict:
    """备用：新浪涨跌幅榜（沪深A）。东财 clist 限流时兜底。各取前 100。"""
    async def node(asc: int) -> list[dict]:
        url = f"{_SINA_NODE}?page=1&num=100&sort=changepercent&asc={asc}&node=hs_a&symbol=&_s_r_a=page"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
                r = await client.get(url, headers=_SINA_HEADERS)
            return _parse_sina_movers(r.json()) if r.status_code == 200 else []
        except Exception:
            return []
    gainers = await node(0)
    await asyncio.sleep(0.3)
    losers = await node(1)
    lu = sum(1 for g in gainers if g["pct"] >= _limit_threshold(g["code"]))
    ld = sum(1 for x in losers if x["pct"] <= -_limit_threshold(x["code"]))
    return {"gainers": gainers[:18], "losers": losers[:18], "limit_up": lu, "limit_down": ld, "source": "sina"}

async def _gather_indices() -> list[dict]:
    """主要指数收盘点位 + 涨跌幅（日线最后两根算 pct）。东财失败 → 新浪兜底。"""
    out: list[dict] = []
    for name, sid in _INDICES:
        try:
            kl = await fetch_eastmoney_index(sid, points=3)
        except Exception:
            kl = []
        if kl:
            close = kl[-1][1]
            prev = kl[-2][1] if len(kl) >= 2 else None
            pct = round((close / prev - 1) * 100, 2) if prev else None
            out.append({"name": name, "close": round(close, 2), "pct": pct, "date": kl[-1][0]})
        await asyncio.sleep(0.25)  # 温柔：避免对东财突发并发（曾因突发被限流）
    if not out:  # 东财指数失败（push2his 曾被封）→ iFinD 首选兜底
        out = await _ifind_indices()
    if not out:  # iFinD 也不可用 → 新浪末位兜底
        out = await _sina_indices()
    return out

def _limit_threshold(code: str) -> float:
    """按代码判断涨停阈值：创业板(30)/科创(688)=20%，北交(4/8)=30%，其余主板=10%。"""
    if code.startswith(("30", "688")):
        return 19.5
    if code.startswith(("8", "4", "92")):
        return 29.0
    return 9.8

def _parse_quote_rows(rows: list[dict]) -> list[dict]:
    out: list[dict] = []
    for d in rows:
        code = str(d.get("f12") or "")
        pct = _num(d.get("f3"))
        if not code or pct is None:
            continue
        out.append({"code": code, "name": str(d.get("f14") or ""), "pct": round(pct, 2), "price": _num(d.get("f2"))})
    return out

async def _gather_movers() -> dict:
    """涨幅/跌幅榜 + 涨跌停数：仅 2 个单页请求（各取前 100），温柔不触发限流。
    涨停均在涨幅榜顶部、跌停在跌幅榜顶部，前 100 足以覆盖（极端情形可能截断，capped 标注）。"""
    gain_rows = await _em_clist(_A_FS, "f12,f14,f2,f3", fid="f3", po="1", pz=100)
    await asyncio.sleep(0.4)
    lose_rows = await _em_clist(_A_FS, "f12,f14,f2,f3", fid="f3", po="0", pz=100)
    gainers = _parse_quote_rows(gain_rows)
    losers = _parse_quote_rows(lose_rows)
    if not gainers:  # 东财 clist 限流/失败 → 新浪兜底
        return await _sina_movers()
    lu = sum(1 for g in gainers if g["pct"] >= _limit_threshold(g["code"]))
    ld = sum(1 for x in losers if x["pct"] <= -_limit_threshold(x["code"]))
    return {
        "gainers": gainers[:18],
        "losers": losers[:18],
        "limit_up": lu, "limit_up_capped": len(gainers) >= 100 and gainers[-1]["pct"] >= 9.8,
        "limit_down": ld, "limit_down_capped": len(losers) >= 100 and losers[-1]["pct"] <= -9.8,
    }

async def _gather_sectors_breadth() -> tuple[dict, dict, dict]:
    """行业板块涨跌（领涨/领跌 + 主力净额 + 领涨股）+ 全市场涨跌家数 + 全部板块 name→pct 映射。
    涨跌家数 = 各行业板块成分股涨/跌家数(f104/f105/f106)汇总——一次请求拿全，免全量翻页（曾因翻页被限流）。
    第三个返回值 board_pcts 供「昨日回看」对照昨日主线今日表现——复用同一响应，⚠️零新增东财请求。"""
    rows = await _em_clist(_BOARD_FS, "f12,f14,f3,f62,f104,f105,f106,f128", fid="f3", po="1", pz=90)
    boards: list[dict] = []
    adv = dec = flat = 0
    for d in rows:
        pct = _num(d.get("f3"))
        if pct is None:
            continue
        boards.append({
            "name": str(d.get("f14") or ""),
            "pct": round(pct, 2),
            "main_flow": _num(d.get("f62")),  # 主力净流入(元)
            "leader": str(d.get("f128") or ""),  # 领涨股
        })
        adv += int(_num(d.get("f104")) or 0)
        dec += int(_num(d.get("f105")) or 0)
        flat += int(_num(d.get("f106")) or 0)
    boards.sort(key=lambda x: x["pct"], reverse=True)
    sectors = {"top": boards[:8], "bottom": sorted(boards[-5:], key=lambda x: x["pct"]) if len(boards) > 5 else []}
    total = adv + dec + flat
    breadth = {
        "advancers": adv or None, "decliners": dec or None,
        "flat": flat or None, "total": total or None,
    }
    board_pcts = {b["name"]: b["pct"] for b in boards if b.get("name")}
    return sectors, breadth, board_pcts

def _fmt_flow(v: Optional[float]) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if a >= 1e4:
        return f"{v / 1e4:+.0f}万"
    return f"{v:+.0f}"

async def _sina_turnover(symbol: str = "sh000001", days: int = 2) -> list[dict]:
    """指数日线（含成交额，单位元）：[{date, close, amount}]。

    主源：腾讯 newfqkline（[date,open,close,high,low,vol,{},换手,amount万元,...]，
    有历史成交额 → 支撑「对比昨日」）；兜底：新浪 kline + 新浪 hq 快照（只有当日）。
    东财 push2his 对生产 IP 间歇断连、新浪 kline 无 amount 字段，故腾讯为首选。
    失败 → []。"""
    tencent_symbol = symbol.replace("sh", "sh").replace("sz", "sz")
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(
                "https://proxy.finance.qq.com/ifzqgtimg/appstock/app/newfqkline/get",
                params={"param": f"{tencent_symbol},day,,,{days},qfq"},
                headers=_SINA_KLINE_HEADERS,
            )
        d = ((r.json() or {}).get("data") or {}).get(tencent_symbol) or {}
        for row in (d.get("day") or []):
            if len(row) < 3:
                continue
            amt_wan = _num(row[8]) if len(row) > 8 else None
            out.append({
                "date": str(row[0]),
                "close": _num(row[2]),
                "amount": (amt_wan * 1e4) if amt_wan is not None else None,
            })
    except Exception:
        out = []
    if out:
        return out
    # 兜底：新浪 kline（无 amount）+ 新浪 hq 快照第 10 列补当日成交额
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(
                f"https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20d=/CN_MarketDataService.getKLineData"
                f"?symbol={symbol}&scale=240&ma=no&datalen={days}",
                headers=_SINA_KLINE_HEADERS,
            )
        m = re.search(r"\((\[.*\])\)", r.text, re.S)
        rows = json.loads(m.group(1)) if m else []
        out = [{"date": d.get("day"), "close": _num(d.get("close")), "amount": _num(d.get("amount"))} for d in rows]
    except Exception:
        out = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            hq = await client.get(f"https://hq.sinajs.cn/list={symbol}", headers=_SINA_KLINE_HEADERS)
        m = re.search(r'="([^"]*)"', hq.text or "")
        if m:
            parts = m.group(1).split(",")
            if len(parts) >= 11:
                amt = _num(parts[9])
                if out and amt:
                    out[-1] = {**out[-1], "amount": out[-1].get("amount") or amt}
                elif not out and amt:
                    out = [{"date": None, "close": _num(parts[3]), "amount": amt}]
    except Exception:
        pass
    return [d for d in out if d.get("date")]

def _yi(amount: Any) -> Optional[float]:
    """元 → 亿元（保留整数）。"""
    f = _num(amount)
    return round(f / 1e8, 0) if f is not None else None

async def gather_market_structure() -> dict:
    """市场结构硬数据一屏（指数+涨跌幅+收盘点位 / 两市成交额及对比昨日 / 涨跌家数 /
    涨停跌停家数 / 行业板块领涨领跌+主力净额 / 连板梯队高度）。

    各部分独立降级（缺哪块就置 None，绝不因单源失败整体为空）。
    板块/涨跌家数走 _gather_sectors_breadth（已带 push2delay 兜底）；
    指数走 _gather_indices（东财/新浪多级兜底）；成交额走新浪 kline
    （上证 sh000001 + 深证综指 sz399106 之和 = 两市成交额）。"""
    now = cn_now()
    # 顺序跑三组采集（各 _gather_* 内部已做温柔间隔；对外部源不并发轰炸）
    indices = await _gather_indices()
    sectors, breadth, board_pcts = await _gather_sectors_breadth()
    await asyncio.sleep(0.2)
    movers = await _gather_movers()
    breadth["limit_up"] = movers.get("limit_up")
    breadth["limit_down"] = movers.get("limit_down")

    # 两市成交额：上证 + 深证综指（新浪 kline，稳定）；无则置 None
    sse_kl = await _sina_turnover("sh000001", days=3)
    sz_kl = await _sina_turnover("sz399106", days=3)
    turnover: Optional[dict] = None
    try:
        if sse_kl and sz_kl:
            today_sse, today_sz = sse_kl[-1], sz_kl[-1]
            prev_sse = sse_kl[-2] if len(sse_kl) >= 2 else None
            prev_sz = sz_kl[-2] if len(sz_kl) >= 2 else None
            amt = (today_sse.get("amount") or 0) + (today_sz.get("amount") or 0)
            if amt:
                turnover = {"date": today_sse.get("date"), "amount_yi": _yi(amt)}
                if prev_sse and prev_sz and (prev_sse.get("amount") or prev_sz.get("amount")):
                    prev_amt = (prev_sse.get("amount") or 0) + (prev_sz.get("amount") or 0)
                    if prev_amt:
                        turnover["prev_date"] = prev_sse.get("date")
                        turnover["prev_amount_yi"] = _yi(prev_amt)
                        turnover["delta_pct"] = round((amt / prev_amt - 1) * 100, 1)
    except Exception:
        turnover = None

    # 连板梯队（东财 push2ex 涨停池；失败降级为空）
    ladder_summary: Optional[dict] = None
    try:
        from .theme_navigation import fetch_limit_up_ladder
        d = await fetch_limit_up_ladder(limit=12)
        if d.get("ladder"):
            ladder_summary = {
                "date": d.get("date"), "limit_up_total": d.get("count"),
                "max_boards": max((s.get("boards") or 0) for s in d["ladder"]),
                "top": [
                    {"name": s.get("name"), "boards": s.get("boards"), "days_ct": s.get("days_ct"), "industry": s.get("industry")}
                    for s in d["ladder"][:6]
                ],
            }
    except Exception:
        ladder_summary = None

    def _fmt_flow(v: Any) -> Optional[float]:
        f = _num(v)
        return round(f / 1e8, 1) if f is not None else None

    return {
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "as_of": f"{now.strftime('%H:%M')} 北京时间（盘中为当时快照，收盘后为收盘数据）",
        "indices": [
            {"name": i.get("name"), "close": i.get("close"), "pct": i.get("pct")}
            for i in (indices or [])
        ],
        "turnover": turnover,
        "breadth": {k: breadth.get(k) for k in ("advancers", "decliners", "flat", "limit_up", "limit_down", "total")},
        "sectors_top": [
            {"name": b.get("name"), "pct": b.get("pct"), "main_net_inflow_yi": _fmt_flow(b.get("main_flow")), "leader": b.get("leader")}
            for b in ((sectors or {}).get("top") or [])[:8]
        ],
        "sectors_bottom": [
            {"name": b.get("name"), "pct": b.get("pct"), "main_net_inflow_yi": _fmt_flow(b.get("main_flow"))}
            for b in ((sectors or {}).get("bottom") or [])[:5]
        ],
        "ladder": ladder_summary,
        "note": (
            "数据源：东财 push2/push2delay + 新浪（指数收盘点位与涨跌幅、两市成交额=上证+深证综指、"
            "行业板块涨跌与主力净额、全市场涨跌家数、涨停池连板梯队）。全部为官方行情原始数字，"
            "引用时请按本结构照实陈述；缺项为 None 时如实说『暂缺』，不得编造。"
        ),
    }
