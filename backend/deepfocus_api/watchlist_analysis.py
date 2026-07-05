# -*- coding: utf-8 -*-
"""自选股纪律体检(白名单内测)。

对用户自选股逐只做**趋势状态 + 开仓四道闸 + 风险参考线 + 估值旗标**体检,
规则引擎来自 trading_discipline 方法论(右侧原则/均线移动线/估值陷阱识别)。

红线:输出一律用「信号区/参考线」等中性表述,不出现买卖指令;不依赖用户成本
(参考线均从最新收盘价与均线推导);每次返回带免责声明。
K线:A股新浪主源、港股东财(生产 IP 间歇不可达→诚实标注 data_gaps,不臆造)。
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from typing import Any, Optional

MAX_SYMBOLS = 12

STATE_STRONG = "右侧强势区"
STATE_HOLD = "站线震荡区"
STATE_TRIM = "移动止损触发区"
STATE_EXIT = "破位信号区"
STATE_NA = "数据不足"

_STATE_NOTE = {
    STATE_STRONG: "站上5/10日线且趋势闸通过——纪律上属『符合持有/关注条件』的形态;可用5日线作移动参考线。",
    STATE_HOLD: "站在均线上方但近20日无单边动量——不满足开仓四道闸的『趋势闸』,纪律上属观察区,不满足新开仓条件。",
    STATE_TRIM: "已跌破5日线但未破10日线——按『分批+移动』纪律属减仓信号区;10日线是下一道观察线。",
    STATE_EXIT: "已跌破10日线——按纪律属离场信号区;历史统计上,破位后『再等等』的胜率随时间递减。重新关注条件=收复10日线并站稳。",
    STATE_NA: "K线数据暂不可用,无法给出趋势判定(不臆造)。",
}


def _ma(closes: list[float], n: int) -> Optional[float]:
    if len(closes) < n:
        return None
    return sum(closes[-n:]) / n


_TX_CACHE: dict[str, tuple[float, list]] = {}
_TX_TTL = 6 * 3600.0


async def _fetch_tencent_ohlc(txcode: str, points: int = 80) -> list[dict]:
    """腾讯 ifzq 日线兜底 [{d,o,c,h,l}]——东财 push2his 对生产 IP 间歇不可达,腾讯走另一套基建。
    txcode 形如 hk03323 / usNVDA.OQ / usTSM.N。直连(trust_env=False)+缓存6h;失败 []。"""
    import time as _time
    import httpx
    key = f"tx:{txcode}"
    hit = _TX_CACHE.get(key)
    if hit and (_time.time() - hit[0]) < _TX_TTL:
        return hit[1]
    url = (f"https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?"
           f"param={txcode},day,,,{max(20, min(points, 320))},")
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url)
            d = (r.json().get("data") or {}).get(txcode) or {}
            rows = d.get("day") or d.get("qfqday") or []
            out = [{"d": x[0], "o": float(x[1]), "c": float(x[2]), "h": float(x[3]), "l": float(x[4])}
                   for x in rows if len(x) >= 5]
    except Exception:  # noqa: BLE001
        out = []
    if len(out) >= 5:  # 占位/残缺结果不进缓存
        _TX_CACHE[key] = (_time.time(), out)
    return out


async def _fetch_closes(symbol: str) -> tuple[list[dict], str]:
    """日线蜡烛(A股新浪主源;港股东财主源+腾讯兜底)。返回 (candles, source);失败 ([], '')。"""
    from .eastmoney_data import _em_stock_secid, fetch_eastmoney_ohlc, fetch_sina_ohlc

    sym = (symbol or "").strip().upper()
    candles: list = []
    source = ""
    if re.fullmatch(r"\d{6}", sym):
        try:
            candles = await fetch_sina_ohlc(sym, points=80)
            source = "sina" if candles else ""
        except Exception:  # noqa: BLE001
            candles = []
    if not candles:
        secid = None
        try:
            secid = _em_stock_secid(sym, None)
        except Exception:  # noqa: BLE001
            secid = None
        if secid:
            try:
                candles = await fetch_eastmoney_ohlc(secid, points=80)
                source = "eastmoney" if candles else ""
            except Exception:  # noqa: BLE001
                candles = []
    if not candles and re.fullmatch(r"\d{5}", sym):  # 港股兜底:腾讯
        candles = await _fetch_tencent_ohlc(f"hk{sym}", points=80)
        source = "tencent" if candles else ""
    if not candles and re.fullmatch(r"[A-Z][A-Z.]{0,5}", sym):  # 美股:腾讯(.OQ纳斯达克/.N纽交所)
        for suffix in (".OQ", ".N"):
            got = await _fetch_tencent_ohlc(f"us{sym}{suffix}", points=80)
            if len(got) >= 21:  # 腾讯对错误后缀会返回1条占位数据,须以足量K线为准
                candles = got
                source = "tencent"
                break
    return candles or [], source


async def _fetch_valuation(symbol: str) -> dict:
    try:
        from .agent_tools import execute_tool
        r = await execute_tool("get_valuation", {"symbol": symbol})
        data = r.get("data") if isinstance(r, dict) and "data" in r else r
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _valuation_flags(val: dict) -> list[str]:
    flags: list[str] = []
    pe = val.get("pe_ratio")
    pb = val.get("pb_ratio")
    try:
        if pe is not None and float(pe) < 0:
            flags.append("TTM亏损·PE失真(周期/困境股常见,『低价』≠低估)")
        elif pe is not None and float(pe) > 60:
            flags.append(f"PE {float(pe):.0f} 偏高·需高成长消化(高估值动量票需更严的移动线)")
    except Exception:  # noqa: BLE001
        pass
    try:
        if pb is not None and 0 < float(pb) < 0.5:
            flags.append(f"PB {float(pb):.2f} 深度破净·注意价值陷阱(便宜≠该拿,兑现以季/年计)")
    except Exception:  # noqa: BLE001
        pass
    return flags


async def _analyze_one(symbol: str, name: str) -> dict[str, Any]:
    candles, ksrc = await _fetch_closes(symbol)
    closes = [float(c.get("c") or 0) for c in candles if c.get("c")]
    item: dict[str, Any] = {
        "symbol": symbol,
        "name": name or "",
        "market": "港股" if re.fullmatch(r"\d{5}", symbol) else ("A股" if re.fullmatch(r"\d{6}", symbol) else ("美股" if re.fullmatch(r"[A-Z][A-Z.]{0,5}", symbol) else "其他")),
        "data_gaps": [],
    }
    if len(closes) < 21:
        item.update({"state": STATE_NA, "note": _STATE_NOTE[STATE_NA]})
        item["data_gaps"].append("日线K线不可用或长度不足")
        return item

    close = closes[-1]
    ma5, ma10, ma20 = _ma(closes, 5), _ma(closes, 10), _ma(closes, 20)
    hi60 = max(closes[-60:]) if len(closes) >= 5 else close
    pct5 = (close / closes[-6] - 1) * 100 if len(closes) >= 6 else None
    pct20 = (close / closes[-21] - 1) * 100
    off_high = (close / hi60 - 1) * 100 if hi60 else None
    trend_gate = (pct20 is not None and pct20 >= 15) or (hi60 and close >= hi60 * 0.98)

    if ma10 and close < ma10:
        state = STATE_EXIT
    elif ma5 and close < ma5:
        state = STATE_TRIM
    elif trend_gate:
        state = STATE_STRONG
    else:
        state = STATE_HOLD

    val = await _fetch_valuation(symbol)
    kdate = str(candles[-1].get("d") or "")
    item.update({
        "kline_date": kdate,
        "kline_source": ksrc,
        "close": round(close, 3),
        "ma5": round(ma5, 3) if ma5 else None,
        "ma10": round(ma10, 3) if ma10 else None,
        "ma20": round(ma20, 3) if ma20 else None,
        "pct_5d": round(pct5, 2) if pct5 is not None else None,
        "pct_20d": round(pct20, 2) if pct20 is not None else None,
        "off_high_60d": round(off_high, 2) if off_high is not None else None,
        "state": state,
        "note": _STATE_NOTE[state],
        "gates": {  # 开仓四道闸(时机闸是通用戒律,前端固定展示)
            "趋势闸(近20日≥15%或近60日新高)": bool(trend_gate),
            "位置闸(站10日线上方)": bool(ma10 and close >= ma10),
            "动量闸(站5日线上方)": bool(ma5 and close >= ma5),
        },
        "levels": {  # 与个人成本无关的通用参考线
            "5日线(移动参考线)": round(ma5, 3) if ma5 else None,
            "10日线(趋势分界线)": round(ma10, 3) if ma10 else None,
            "现价-5%(常用风险线)": round(close * 0.95, 3),
            "现价-8%(宽风险线)": round(close * 0.92, 3),
        },
        "valuation": {
            "pe": val.get("pe_ratio"),
            "pb": val.get("pb_ratio"),
            "market_cap_yi": val.get("market_cap_yi"),
            "currency": val.get("currency"),
            "flags": _valuation_flags(val),
        },
    })
    if not val:
        item["data_gaps"].append("估值数据暂不可用")
    if re.fullmatch(r"\d{5}", symbol):
        item["data_gaps"].append("港股K线为港元价;若与人民币结算成本对比需另乘汇率")
    elif re.fullmatch(r"[A-Z][A-Z.]{0,5}", symbol):
        item["data_gaps"].append("美股K线为美元价(腾讯源,收盘为美东时间)")
    return item


async def _resolve_token(token: str) -> tuple[Optional[str], str]:
    """把输入token归一成代码:代码原样;中文名→A股名称索引→腾讯smartbox(港/美)。返回(code,name)。"""
    t = (token or "").strip()
    if not t:
        return None, ""
    if re.fullmatch(r"\d{5,6}", t) or re.fullmatch(r"[A-Za-z][A-Za-z.]{0,5}", t):
        return t.upper(), ""
    try:  # A股中文名(站内索引,含除权前缀近似)
        from . import stock_name_index
        code = stock_name_index.resolve_to_code(t)
        if code:
            return str(code), t
    except Exception:  # noqa: BLE001
        pass
    try:  # 腾讯 smartbox 全市场联想(UTF-8;GBK编码反而查不到)
        import urllib.parse
        import httpx
        url = f"https://smartbox.gtimg.cn/s3/?v=2&q={urllib.parse.quote(t)}&t=all"
        async with httpx.AsyncClient(trust_env=False, timeout=8.0) as client:
            r = await client.get(url)
        body = r.content.decode("gbk", errors="ignore")
        m = re.search(r'"([^"]+)"', body)
        if m and m.group(1) != "N":
            first = m.group(1).split("^")[0].split("~")
            if len(first) >= 3:
                mk, code, nm = first[0], first[1], first[2]
                try:
                    nm = nm.encode("latin-1", "ignore").decode("unicode_escape")
                except Exception:  # noqa: BLE001
                    pass
                if mk in ("sz", "sh"):
                    return code, nm
                if mk == "hk":
                    return code.zfill(5), nm
                if mk == "us":
                    return code.split(".")[0].upper(), nm
    except Exception:  # noqa: BLE001
        pass
    return None, t


async def analyze_for_user(username: str, symbols_override: Optional[list[str]] = None,
                           user_id: Optional[str] = None) -> dict[str, Any]:
    syms: list[str] = []
    names: dict[str, str] = {}
    unresolved: list[str] = []
    if symbols_override:
        for tok in symbols_override:
            if not (tok or "").strip():
                continue
            code, nm = await _resolve_token(tok)
            if code:
                if code not in syms:
                    syms.append(code)
                if nm:
                    names[code] = nm
            else:
                unresolved.append(tok.strip())
        syms = syms[:MAX_SYMBOLS]
    else:
        try:
            from . import user_prefs
            # 自选股表按用户ID(uuid)为主键存(/api/me/watchlist 写的是 JWT sub);用户名仅兜底
            wl = None
            for key in (user_id, username):
                if key:
                    wl = user_prefs.get_watchlist(key)
                    if wl:
                        break
            wl = wl or {}
            syms = [str(s).strip().upper() for s in (wl.get("symbols") or [])][:MAX_SYMBOLS]
            names = wl.get("names") or {}
        except Exception:  # noqa: BLE001
            syms = []
    if not syms:
        note = ("未能识别:" + "、".join(unresolved) + "(试试标准代码,如 00148/300750/NVDA)") if unresolved             else "自选股为空——在终端里添加自选,或在输入框指定代码/名称(逗号分隔)。"
        return {
            "username": username, "items": [], "count": 0, "unresolved": unresolved,
            "note": note,
            "disclaimer": _DISCLAIMER,
        }

    sem = asyncio.Semaphore(4)
    _unresolved = unresolved if symbols_override else []

    async def _run(s: str) -> dict[str, Any]:
        async with sem:
            try:
                return await _analyze_one(s, names.get(s, ""))
            except Exception as exc:  # noqa: BLE001
                return {"symbol": s, "name": names.get(s, ""), "state": STATE_NA,
                        "note": _STATE_NOTE[STATE_NA], "data_gaps": [f"分析失败:{type(exc).__name__}"]}

    items = list(await asyncio.gather(*[_run(s) for s in syms]))
    order = {STATE_EXIT: 0, STATE_TRIM: 1, STATE_STRONG: 2, STATE_HOLD: 3, STATE_NA: 4}
    items.sort(key=lambda x: order.get(x.get("state"), 9))
    summary: dict[str, int] = {}
    for it in items:
        summary[it.get("state", STATE_NA)] = summary.get(it.get("state", STATE_NA), 0) + 1
    return {
        "username": username,
        "unresolved": _unresolved,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "count": len(items),
        "summary": summary,
        "items": items,
        "discipline": [
            "开仓四道闸:品种对/趋势已走出/时机对(避开周一上午与开盘30分钟)/写下计划",
            "1%风险规则:单笔最大亏损=总资金1%,由止损幅度倒推仓位上限;首仓只用目标仓位1/3",
            "只加浮盈的仓(≥3%且趋势未破),金字塔底大顶小;破位补仓禁止",
            "移动线纪律:破5日线=减仓信号区,破10日线=离场信号区;顶让均线判,不靠预测",
        ],
        "disclaimer": _DISCLAIMER,
    }


_DISCLAIMER = ("本页为交易纪律方法论的自动化体检(趋势状态/参考线/估值旗标均为通用规则推导),"
               "不构成投资建议,不针对任何证券给出买卖指令;数据可能延迟或缺失,以交易所行情为准。")
