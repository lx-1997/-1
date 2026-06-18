"""东方财富 A股财报数据——盈利质量（净利/营收同比 + EPS + ROE）。

直连绕代理（httpx trust_env=False，外部 403 是沙箱代理封的）。仅 A股（6 位数字代码）。
缓存 6h。来源 datacenter-web.eastmoney.com 业绩报表 RPT_LICO_FN_CPD。
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime
from typing import Optional

import httpx

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 6 * 3600.0
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}


async def fetch_eastmoney_earnings(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """A股/港股盈利质量：最新季报净利/营收同比 + EPS + ROE。

    A股（6位代码）走 RPT_LICO_FN_CPD；港股（market=HK 或非6位数字）走 RPT_HKF10_FN_MAININDICATOR
    （归母净利同比 HOLDER_PROFIT_YOY / 营收同比 OPERATE_INCOME_YOY）。失败/无数据返回 None（优雅降级）。
    """
    code = re.sub(r"\D", "", symbol or "")
    if not code:
        return None
    is_hk = (market or "").upper() == "HK" or len(code) != 6
    cache_key = ("hk:" if is_hk else "a:") + code
    hit = _CACHE.get(cache_key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    result: Optional[dict] = None
    try:
        if is_hk:
            secucode = f"{code.zfill(5)}.HK"
            url = (
                "https://datacenter-web.eastmoney.com/api/data/v1/get"
                "?reportName=RPT_HKF10_FN_MAININDICATOR&columns=ALL&pageSize=2"
                "&sortColumns=STD_REPORT_DATE&sortTypes=-1"
                f"&filter=(SECUCODE=%22{secucode}%22)"
            )
        else:
            url = (
                "https://datacenter-web.eastmoney.com/api/data/v1/get"
                "?reportName=RPT_LICO_FN_CPD&columns=ALL&pageSize=2"
                "&sortColumns=REPORTDATE&sortTypes=-1"
                f"&filter=(SECURITY_CODE%3D%22{code}%22)"
            )
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            data = ((r.json().get("result") or {}).get("data")) or []
            if data:
                r0 = data[0]
                if is_hk:
                    result = {
                        "report_date": (r0.get("REPORT_DATE") or "")[:10],
                        "eps": r0.get("BASIC_EPS"),
                        "revenue_yoy": r0.get("OPERATE_INCOME_YOY"),  # 营收同比 %
                        "profit_yoy": r0.get("HOLDER_PROFIT_YOY"),  # 归母净利同比 %
                        "roe": r0.get("ROE_AVG"),
                        "name": None,
                        "is_hk": True,
                        "code": code.zfill(5),
                    }
                else:
                    result = {
                        "report_date": (r0.get("REPORTDATE") or "")[:10],
                        "eps": r0.get("BASIC_EPS"),
                        "revenue_yoy": r0.get("YSTZ"),  # 营收同比 %
                        "profit_yoy": r0.get("SJLTZ"),  # 净利润同比 %
                        "roe": r0.get("WEIGHTAVG_ROE"),
                        "name": r0.get("SECURITY_NAME_ABBR"),
                        "is_hk": False,
                        "code": code,
                        # —— 盈利质量增量字段(长线牛股方法论用)：每股经营现金流/扣非EPS/毛利率/净利/营收 ——
                        "ocf_per_share": r0.get("MGJYXJJE"),     # 每股经营现金流 → 含金量 = ÷EPS
                        "deduct_eps": r0.get("DEDUCT_BASIC_EPS"),  # 扣非每股收益 → 扣非占比 = ÷EPS
                        "gross_margin": r0.get("XSMLL"),         # 销售毛利率 %
                        "net_income": r0.get("PARENT_NETPROFIT"),  # 归母净利润
                        "revenue": r0.get("TOTAL_OPERATE_INCOME"),  # 营业收入
                    }
    except Exception:
        result = None

    _CACHE[cache_key] = (time.time(), result)
    return result


def _a_secucode(code: str) -> str:
    """6 位 A股代码 → 东财 SECUCODE（带交易所后缀）。6→.SH，4/8/9(北交所)→.BJ，其余→.SZ。"""
    code = re.sub(r"\D", "", code or "")
    if code[:1] == "6":
        return f"{code}.SH"
    if code[:1] in ("4", "8") or code[:2] == "92":
        return f"{code}.BJ"
    return f"{code}.SZ"


async def fetch_eastmoney_cashflow(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """A股现金流量表(经营/投资/筹资活动现金流量净额 + 购建长期资产 capex)。直连绕代理、缓存 6h。
    给现金流八类型(第9.5)与自由现金流(第10章)用。失败/无数据→None(优雅降级)。"""
    code = re.sub(r"\D", "", symbol or "")
    if not code or len(code) != 6 or (market or "").upper() in ("HK", "US"):
        return None
    key = "cf:" + code
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    result: Optional[dict] = None
    try:
        url = (
            "https://datacenter.eastmoney.com/securities/api/data/v1/get"
            "?reportName=RPT_F10_FINANCE_GCASHFLOW&columns=ALL&pageSize=1"
            "&sortColumns=REPORT_DATE&sortTypes=-1"
            f"&filter=(SECUCODE=%22{_a_secucode(code)}%22)"
        )
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            data = ((r.json().get("result") or {}).get("data")) or []
            if data:
                r0 = data[0]
                result = {
                    "report_date": (r0.get("REPORT_DATE") or "")[:10],
                    "ocf": r0.get("NETCASH_OPERATE"),        # 经营活动现金流量净额
                    "icf": r0.get("NETCASH_INVEST"),         # 投资活动现金流量净额
                    "fcf_financing": r0.get("NETCASH_FINANCE"),  # 筹资活动现金流量净额
                    "capex": r0.get("CONSTRUCT_LONG_ASSET"),  # 购建固定/无形/长期资产支付的现金
                    "code": code,
                }
    except Exception:
        result = None
    _CACHE[key] = (time.time(), result)
    return result


async def fetch_eastmoney_balance(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """A股资产负债表关键项：应收账款 / 预收(合同负债) / 存货 / 归母净资产。直连绕代理、缓存 6h。
    给盈利质量(第4.3 还原应收预收)与好生意(第1.6 预收>应收=回款强)用。失败→None。"""
    code = re.sub(r"\D", "", symbol or "")
    if not code or len(code) != 6 or (market or "").upper() in ("HK", "US"):
        return None
    key = "bal:" + code
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    result: Optional[dict] = None
    try:
        url = (
            "https://datacenter.eastmoney.com/securities/api/data/v1/get"
            "?reportName=RPT_F10_FINANCE_GBALANCE&columns=ALL&pageSize=1"
            "&sortColumns=REPORT_DATE&sortTypes=-1"
            f"&filter=(SECUCODE=%22{_a_secucode(code)}%22)"
        )
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            data = ((r.json().get("result") or {}).get("data")) or []
            if data:
                r0 = data[0]
                # 预收：新准则记入「合同负债」，按多个候选键 best-effort 取第一个非空
                adv = next((r0.get(k) for k in ("CONTRACT_LIAB", "CONTRACT_LIABILITIES", "ADVANCE_RECEIVABLES")
                            if r0.get(k) is not None), None)
                result = {
                    "report_date": (r0.get("REPORT_DATE") or "")[:10],
                    "accounts_receivable": r0.get("ACCOUNTS_RECE"),  # 应收账款
                    "advance_receipts": adv,                          # 预收账款/合同负债
                    "inventory": r0.get("INVENTORY"),                 # 存货
                    "equity": r0.get("TOTAL_PARENT_EQUITY"),          # 归母净资产
                    "code": code,
                }
    except Exception:
        result = None
    _CACHE[key] = (time.time(), result)
    return result


async def fetch_eastmoney_index(secid: str, points: int = 140) -> list:
    """东财大盘指数日线 [(date, close)]：沪深300=1.000300 / 上证=1.000001 / 恒生=100.HSI。
    直连绕代理。缓存 6h。失败返回 []（优雅降级）。"""
    key = f"idx:{secid}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]

    out: list = []
    try:
        url = (
            f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
            f"&fields1=f1&fields2=f51,f53&klt=101&fqt=0&end=20300101&lmt={points}"
        )
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
            r = await client.get(url, headers=_HEADERS)
        if r.status_code == 200:
            kl = ((r.json().get("data") or {}).get("klines")) or []
            for line in kl:
                parts = line.split(",")
                if len(parts) >= 2:
                    try:
                        out.append((parts[0], float(parts[1])))
                    except ValueError:
                        pass
    except Exception:
        out = []

    _CACHE[key] = (time.time(), out)
    return out


def _em_stock_secid(symbol: str, market: Optional[str] = None) -> Optional[str]:
    """个股 → 东财 secid：A股 沪(6/9开头)=1.、深=0.；港股=116.（code 补 5 位）。美股 None。"""
    code = re.sub(r"\D", "", symbol or "")
    mkt = (market or "").upper()
    if mkt == "HK" or (code and len(code) != 6 and mkt != "CN" and not (symbol or "").isalpha()):
        return f"116.{code.zfill(5)}" if code else None
    if len(code) == 6:
        return f"1.{code}" if code[0] in ("6", "9") else f"0.{code}"
    return None


async def fetch_eastmoney_kline(secid: str, points: int = 160) -> list:
    """东财个股日线 [(date, close)]（前复权 fqt=1）。直连绕代理 + Referer + 重试。缓存 6h。失败 []。"""
    key = f"kline:{secid}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
        f"&fields1=f1&fields2=f51,f53&klt=101&fqt=1&end=20500101&lmt={points}"
    )
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    out: list = []
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
                r = await client.get(url, headers=headers)
            if r.status_code == 200:
                kl = ((r.json().get("data") or {}).get("klines")) or []
                for line in kl:
                    parts = line.split(",")
                    if len(parts) >= 2:
                        try:
                            out.append((parts[0], float(parts[1])))
                        except ValueError:
                            pass
                if out:
                    break
        except Exception:
            out = []
        if attempt < 3:
            await asyncio.sleep(0.5 * (attempt + 1))  # push2his 偶发断连，退避重试
    _CACHE[key] = (time.time(), out)
    return out


async def fetch_fund_flow(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """A股资金面（live）：主力近5日净流入(push2his fflow) + 最近龙虎榜净额(datacenter)。仅A股，否则 None。"""
    code = re.sub(r"\D", "", symbol or "")
    if ((market or "").upper() not in ("", "CN")) or len(code) != 6:
        return None
    key = f"flow:{code}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _CACHE_TTL:
        return hit[1]
    secid = f"1.{code}" if code[0] in ("6", "9") else f"0.{code}"
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    result: dict = {}
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=12.0) as c:
            # 主力净流入近5日（f51 日期 / f52 主力净流入额，元）
            for attempt in range(3):
                try:
                    r = await c.get(
                        f"https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get?secid={secid}"
                        "&fields1=f1&fields2=f51,f52&klt=101&lmt=5",
                        headers=headers,
                    )
                    if r.status_code == 200:
                        kl = ((r.json().get("data") or {}).get("klines")) or []
                        flows = []
                        for line in kl:
                            p = line.split(",")
                            if len(p) >= 2:
                                try:
                                    flows.append((p[0], float(p[1])))
                                except ValueError:
                                    pass
                        if flows:
                            result["main_flow_5d"] = sum(v for _, v in flows)
                            result["flow_days"] = len(flows)
                        break
                except Exception:
                    if attempt < 2:
                        await asyncio.sleep(0.5 * (attempt + 1))
            # 最近龙虎榜净额（datacenter，稳定）
            try:
                r = await c.get(
                    "https://datacenter-web.eastmoney.com/api/data/v1/get"
                    "?reportName=RPT_DAILYBILLBOARD_DETAILSNEW&columns=ALL&pageSize=1"
                    "&sortColumns=TRADE_DATE&sortTypes=-1"
                    f"&filter=(SECURITY_CODE%3D%22{code}%22)",
                    headers={**_HEADERS, "Referer": "https://data.eastmoney.com/"},
                )
                if r.status_code == 200:
                    data = ((r.json().get("result") or {}).get("data")) or []
                    if data:
                        lhb_date = data[0].get("TRADE_DATE") or ""
                        try:
                            recent = (datetime.now() - datetime.strptime(lhb_date[:10], "%Y-%m-%d")).days <= 30
                        except ValueError:
                            recent = False
                        if recent:  # 仅纳入近30天龙虎榜（历史久远的上榜对当前速判无参考意义）
                            result["lhb_net"] = data[0].get("BILLBOARD_NET_AMT")
                            result["lhb_date"] = lhb_date
            except Exception:
                pass
    except Exception:
        return None
    if result.get("main_flow_5d") is not None or result.get("lhb_net") is not None:
        result["provider"] = "eastmoney"
        _CACHE[key] = (time.time(), result)
        return result
    _CACHE[key] = (time.time(), None)
    return None
