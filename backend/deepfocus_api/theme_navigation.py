"""A股题材/产业链导航——概念板块榜 + 板块成分 + 个股所属题材。

「顺藤摸瓜找受益股」是 A股散户最高频需求,我们此前完全空白。用东财**概念板块**的
确定性题材→个股关系填补:非荐股、非研判,合规低风险。

数据源:push2.eastmoney.com clist（**非** push2his——后者对生产 IP 间歇空回 code=000,
push2 实测生产可达 200）。直连绕代理（trust_env=False）。各函数独立缓存 + 失败优雅降级 []/{}。
"""
from __future__ import annotations

import asyncio
import re
import time
from typing import Optional

import httpx

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://quote.eastmoney.com/",
}

_CACHE: dict = {}
_RANK_TTL = 180.0            # 概念榜盘中会动 → 3min
_MEMBER_TTL = 1800.0        # 板块成分相对稳 → 30min
_THEME_TTL = 6 * 3600.0     # 个股所属行业/板块 → 6h
_ZT_TTL = 120.0             # 涨停池盘中变化快 → 2min

_CONCEPT_FS = "m:90+t:3"    # 概念板块
_INDUSTRY_FS = "m:90+t:2"   # 行业板块（白酒/半导体/银行…——名称检索时与概念合并搜）
_BOARD_FIELDS = "f12,f14,f3,f128,f136,f140"


def _num(v) -> Optional[float]:
    try:
        f = float(v)
        return f if f == f else None  # NaN 防护（东财无数据回 "-"→ValueError 已挡）
    except (TypeError, ValueError):
        return None


def _clist_url(fs: str, fields: str, fid: str, po: str, pz: int) -> str:
    return (
        "https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn=1&pz={pz}&po={po}&np=1&fltt=2&invt=2&fid={fid}&fs={fs}&fields={fields}"
    )


async def _clist(fs: str, fields: str, *, fid: str = "f3", po: str = "1", pz: int = 60) -> list[dict]:
    """东财 clist 单页（按 fid 排序，po=1 降序）。直连绕代理；失败/非200 → []。"""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(_clist_url(fs, fields, fid, po, pz), headers=_HEADERS)
        if r.status_code != 200:
            return []
        diff = (((r.json() or {}).get("data") or {}).get("diff")) or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        return [d for d in diff if isinstance(d, dict)]
    except Exception:  # noqa: BLE001
        return []


async def _fetch_board_list(fs: str, cache_key: str, ttl: float) -> list[dict]:
    """板块榜通用拉取（概念/行业共用）。按涨跌幅降序 → [{code,name,pct,leader,leader_code,leader_pct}]。"""
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    # f12 板块代码 / f14 名称 / f3 涨跌幅 / f128 领涨股名 / f140 领涨股代码 / f136 领涨股涨幅
    rows = await _clist(fs, _BOARD_FIELDS, fid="f3", po="1", pz=200)
    out: list[dict] = []
    for d in rows:
        code = str(d.get("f12") or "").strip()
        if not code:
            continue
        out.append({
            "code": code,
            "name": d.get("f14") or "",
            "pct": _num(d.get("f3")),
            "leader": d.get("f128") or "",
            "leader_code": str(d.get("f140") or "").strip(),
            "leader_pct": _num(d.get("f136")),
        })
    if out:
        _CACHE[cache_key] = (time.time(), out)
    return out


async def fetch_concept_boards(limit: int = 40) -> list[dict]:
    """概念板块榜（按当日涨跌幅降序）：[{code,name,pct,leader,leader_code,leader_pct}]。缓存 3min。

    code 形如 'BK1175'，可喂 fetch_board_stocks 取成分。涵盖『昨日连板/打二板』等情绪/题材聚合板块。"""
    return (await _fetch_board_list(_CONCEPT_FS, "concept_rank", _RANK_TTL))[:limit]


async def fetch_board_stocks(board_code: str, limit: int = 50) -> list[dict]:
    """概念板块成分股（按涨跌幅降序）：[{code,name,price,pct}]。缓存 30min。board_code 形如 'BK1175'。"""
    code = (board_code or "").strip().upper()
    if not re.fullmatch(r"BK\d+", code):
        return []
    key = f"board:{code}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _MEMBER_TTL:
        return hit[1][:limit]
    rows = await _clist(f"b:{code}+f:!50", "f12,f14,f2,f3", fid="f3", po="1", pz=250)
    out: list[dict] = []
    for d in rows:
        sym = str(d.get("f12") or "").strip()
        if not sym:
            continue
        out.append({"code": sym, "name": d.get("f14") or "", "price": _num(d.get("f2")), "pct": _num(d.get("f3"))})
    if out:
        _CACHE[key] = (time.time(), out)
    return out[:limit]


async def _clist_paged(fs: str, fields: str, *, max_pages: int = 6) -> list[dict]:
    """东财 clist 全量分页（pz=100/页）。用于板块名称索引（拿全集，非只top-N）。

    **并发**拉 max_pages 页（冷缓存时 12 页串行会超时 → 并发把 ~10s 压到 ~1.5s）；
    单页失败/空仅丢该页，不拖垮整体。失败→已取到的。"""
    out: list[dict] = []
    urls = [
        "https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2&fid=f3&fs={fs}&fields={fields}"
        for pn in range(1, max_pages + 1)
    ]
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            results = await asyncio.gather(
                *[client.get(u, headers=_HEADERS) for u in urls], return_exceptions=True
            )
        for r in results:
            if isinstance(r, Exception) or r.status_code != 200:
                continue
            diff = (((r.json() or {}).get("data") or {}).get("diff")) or []
            if isinstance(diff, dict):
                diff = list(diff.values())
            out += [d for d in diff if isinstance(d, dict)]
    except Exception:  # noqa: BLE001
        pass
    return out


_NAME_INDEX_TTL = 24 * 3600.0  # 板块 universe（哪些板块存在）极稳 → 24h


async def _board_name_pairs() -> list[dict]:
    """全量概念+行业板块 [{code,name}]（名称解析用，universe 稳定，缓存 24h）。"""
    key = "board_name_pairs"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _NAME_INDEX_TTL:
        return hit[1]
    rows = await _clist_paged(_CONCEPT_FS, "f12,f14") + await _clist_paged(_INDUSTRY_FS, "f12,f14")
    out: list[dict] = []
    seen: set = set()
    for d in rows:
        code = str(d.get("f12") or "").strip()
        nm = (d.get("f14") or "").strip()
        if code and nm and code not in seen:
            seen.add(code)
            out.append({"code": code, "name": nm})
    if out:
        _CACHE[key] = (time.time(), out)
    return out


_SUFFIX_RE = re.compile(r"[Ⅰ-Ⅹ概念板块指数]+$")


async def find_board_by_name(name: str) -> Optional[dict]:
    """按题材/行业名（模糊）找板块：全量概念(t:3)+行业(t:2)名称索引里匹配。返回 {code,name} 或 None。

    支持『白酒』『半导体』『人工智能』『机器人』等散户口语题材（不再受 top-N 排名遗漏）。
    优先级：精确 > 去后缀精确（白酒Ⅱ→白酒）> 含子串（短名优先，避免『非白酒』压过『白酒』）。"""
    q = (name or "").strip()
    if not q:
        return None
    boards = await _board_name_pairs()
    ql = q.lower()
    norm = lambda s: _SUFFIX_RE.sub("", (s or "")).strip().lower()
    qn = norm(q)
    exact = [b for b in boards if b["name"] == q]
    if exact:
        return exact[0]
    deburred = [b for b in boards if norm(b["name"]) == qn]
    if deburred:
        return min(deburred, key=lambda b: len(b["name"]))  # 白酒 优于 白酒Ⅱ/白酒Ⅲ
    contains = [b for b in boards if qn and (qn in norm(b["name"]) or norm(b["name"]) in qn)]
    return min(contains, key=lambda b: len(b["name"])) if contains else None


async def fetch_stock_themes(symbol: str) -> dict:
    """个股所属行业/板块（东财 stock/get f127 行业 / f128 板块）。缓存 6h。失败 {}。仅 A股 6 位。"""
    code = re.sub(r"\D", "", symbol or "")
    if len(code) != 6:
        return {}
    secid = f"1.{code}" if code[0] in ("6", "9") else f"0.{code}"
    key = f"theme:{secid}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _THEME_TTL:
        return hit[1]
    out: dict = {}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(
                f"https://push2.eastmoney.com/api/qt/stock/get?secid={secid}&fields=f57,f58,f127,f128",
                headers=_HEADERS,
            )
        if r.status_code == 200:
            d = (r.json() or {}).get("data") or {}
            ind = (d.get("f127") or "").strip()
            brd = (d.get("f128") or "").strip()
            if ind or brd:
                out = {"symbol": code, "name": (d.get("f58") or "").strip(), "industry": ind, "board": brd}
    except Exception:  # noqa: BLE001
        out = {}
    if out:
        _CACHE[key] = (time.time(), out)
    return out


async def fetch_limit_up_ladder(limit: int = 60) -> dict:
    """A股涨停天梯（连板梯队）：今日/最近交易日涨停池，按连板数(lbc)降序 → 高度榜。缓存 2min。

    返回 {date, count, ladder:[{code,name,pct,boards,days_ct,industry,broke}]}。东财涨停池(push2ex,
    生产可达)。date=今天(北京)时 API 自动回落到最近交易日(周末取上个交易日)。纯事实(谁涨停/几连板)，非荐股。"""
    from datetime import datetime, timedelta, timezone

    date = datetime.now(timezone(timedelta(hours=8))).strftime("%Y%m%d")
    key = "zt_ladder"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _ZT_TTL:
        c = hit[1]
        return {**c, "ladder": c["ladder"][:limit]}
    url = (
        "https://push2ex.eastmoney.com/getTopicZTPool"
        "?ut=7eea3edcaed734bea9cbfc24409ed989&dpt=wz.ztzt&Pageindex=0&pagesize=300&sort=fbt%3Aasc"
        f"&date={date}"
    )
    out: dict = {"date": "", "count": 0, "ladder": []}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or {}
            ladder: list[dict] = []
            for it in (data.get("pool") or []):
                code = str(it.get("c") or "").strip()
                if not code:
                    continue
                z = it.get("zttj") or {}
                days, ct = z.get("days"), z.get("ct")
                p = _num(it.get("zdp"))
                ladder.append({
                    "code": code,
                    "name": it.get("n") or "",
                    "pct": round(p, 2) if p is not None else None,
                    "boards": int(it.get("lbc") or 0),                 # 连板数
                    "days_ct": (f"{days}天{ct}板" if ct else ""),       # N天M板
                    "industry": it.get("hybk") or "",                  # 所属行业（链回题材）
                    "broke": int(it.get("zbc") or 0),                  # 炸板次数
                })
            ladder.sort(key=lambda x: x["boards"], reverse=True)
            out = {"date": str(data.get("qdate") or ""), "count": len(ladder), "ladder": ladder}
    except Exception:  # noqa: BLE001
        out = {"date": "", "count": 0, "ladder": []}
    if out["ladder"]:
        _CACHE[key] = (time.time(), out)
    return {**out, "ladder": out["ladder"][:limit]}
