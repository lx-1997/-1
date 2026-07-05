"""东方财富 A股财报数据——盈利质量（净利/营收同比 + EPS + ROE）。

直连绕代理（httpx trust_env=False，外部 403 是沙箱代理封的）。仅 A股（6 位数字代码）。
缓存分层：财报/现金流/资产负债等慢数据 6h；行情类(K线/OHLC/指数/资金流)盘中 15min、休市 6h
（TTL 在读取时评估——早晨休市写入的行情缓存,一开盘即按 15min 口径过期,AI 盘中不再引用数小时前的旧数）。
来源 datacenter-web.eastmoney.com 业绩报表 RPT_LICO_FN_CPD。
"""
from __future__ import annotations

import asyncio
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_CACHE_TTL = 6 * 3600.0            # 慢数据（财报/现金流/资产负债）——一天最多几更，6h 足够新
_CACHE_TTL_INTRADAY = 900.0        # 行情类（K线/OHLC/指数/资金流）盘中 15min——盘中在变，6h 会让 AI 引用早间旧数
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_BJ_TZ = timezone(timedelta(hours=8))  # 北京时间（A股时段判定 + as_of/fetched_at 数据时点标注）


def _bj_now_iso() -> str:
    """取数时刻（北京时间 ISO 字符串），供 as_of/fetched_at 数据时点标注。"""
    return datetime.now(_BJ_TZ).isoformat(timespec="seconds")


def _in_cn_session(now: Optional[datetime] = None) -> bool:
    """轻量 A股盘中判定：周一~五 且 北京时间 09:15–15:05（含集合竞价与收盘缓冲）。

    刻意不 import ai_fund._in_session/_is_trading_day——ai_fund 是重量级模块（sqlite+lifespan 任务），
    且它反向 import 本模块（fetch_eastmoney_index），存在循环依赖风险。也不判节假日：节假日被误判成
    “盘中”只是把 TTL 收短、多刷几次（上游数据不变，结果一致），无正确性代价，不值得为此在本模块
    维护第二份节假日表。"""
    now = now or datetime.now(_BJ_TZ)
    if now.weekday() >= 5:  # 周六/周日
        return False
    hm = now.hour * 60 + now.minute
    return 555 <= hm <= 905  # 09:15(=555) – 15:05(=905)


def _market_ttl() -> float:
    """行情类缓存 TTL：盘中 15 分钟（数据在变），休市 6 小时（数据不变，省请求）。读取时评估。"""
    return _CACHE_TTL_INTRADAY if _in_cn_session() else _CACHE_TTL


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
                        "name": r0.get("SECURITY_NAME_ABBR"),  # 东财同行有返名，原硬编码 None 丢失
                        "currency": r0.get("CURRENCY") or "HKD",  # 港股财报币种(EPS等)，与A股CNY区分
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
    直连绕代理。缓存 盘中15min/休市6h。失败返回 []（优雅降级）。"""
    key = f"idx:{secid}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _market_ttl():
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
    """东财个股日线 [(date, close)]（前复权 fqt=1）。直连绕代理 + Referer + 重试。缓存 盘中15min/休市6h。失败 []。"""
    key = f"kline:{secid}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _market_ttl():
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


async def fetch_eastmoney_ohlc(secid: str, points: int = 160) -> list:
    """东财个股日线 OHLC+量 [{d,o,c,h,l,v}]（前复权 fqt=1）。直连绕代理 + Referer + 重试。缓存 盘中15min/休市6h。失败 []。

    供终端蜡烛图。fields2 顺序 f51,f52,f53,f54,f55,f56 = 日期/开/收/高/低/量(手)。
    只缓存成功结果（不把瞬时失败的 [] 长缓存，避免误锁）。最新一根蜡烛带 fetched_at（取数时刻·北京时间）。"""
    key = f"ohlc:{secid}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _market_ttl():
        return hit[1]
    url = (
        f"https://push2his.eastmoney.com/api/qt/stock/kline/get?secid={secid}"
        f"&fields1=f1&fields2=f51,f52,f53,f54,f55,f56&klt=101&fqt=1&end=20500101&lmt={points}"
    )
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    out: list = []
    for attempt in range(4):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
                r = await client.get(url, headers=headers)
            if r.status_code == 200:
                kl = ((r.json().get("data") or {}).get("klines")) or []
                rows: list = []
                for line in kl:
                    parts = line.split(",")
                    if len(parts) >= 5:
                        try:
                            rows.append({
                                "d": parts[0],
                                "o": float(parts[1]),
                                "c": float(parts[2]),
                                "h": float(parts[3]),
                                "l": float(parts[4]),
                                "v": float(parts[5]) if len(parts) > 5 else 0.0,
                            })
                        except ValueError:
                            pass
                if rows:
                    # 数据时点（北京时间）——只挂最新一根（历史蜡烛已定型无需时点；只加字段，向后兼容）
                    rows[-1]["fetched_at"] = _bj_now_iso()
                    out = rows
                    break
        except Exception:
            out = []
        if attempt < 3:
            await asyncio.sleep(0.5 * (attempt + 1))  # push2his 偶发断连，退避重试
    if out:
        _CACHE[key] = (time.time(), out)
    return out


async def fetch_intraday_trend(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """A股/港股当日分时（东财 push2 trends2：分钟级 价格/均价/量）→ {pre_close, points:[{t,p,avg,v}]}。

    ⭐用 push2 网关而非 push2his——theme_navigation 已验证 push2 对生产 IP 可达（push2his 间歇被封）。
    分时是 A股散户交易时段的默认视图，没有它终端在盘中就不是"看盘工具"。
    缓存：盘中 60s（分钟级数据）、休市 10min。失败 None（前端回落日K）。"""
    secid = _em_stock_secid(symbol, market)
    if not secid:
        return None
    key = f"trend:{secid}"
    hit = _CACHE.get(key)
    ttl = 60.0 if _in_cn_session() else 600.0
    if hit and (time.time() - hit[0]) < ttl:
        return hit[1]
    url = (
        f"https://push2.eastmoney.com/api/qt/stock/trends2/get?secid={secid}"
        "&fields1=f1,f2,f3,f7,f8,f17&fields2=f51,f52,f53,f54,f55,f56,f57,f58&iscr=0&ndays=1"
    )
    headers = {**_HEADERS, "Referer": "https://quote.eastmoney.com/"}
    result: Optional[dict] = None
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
            r = await client.get(url, headers=headers)
        if r.status_code == 200:
            data = (r.json() or {}).get("data") or {}
            trends = data.get("trends") or []
            points: list = []
            for line in trends:
                parts = str(line).split(",")
                # fields2 顺序 f51..f58 = 时间,开,收(现价),高,低,量,额,均价
                if len(parts) >= 8:
                    try:
                        points.append({
                            "t": parts[0][-5:],              # "HH:MM"
                            "p": float(parts[2]),
                            "avg": float(parts[7]),
                            "v": float(parts[5]),
                        })
                    except ValueError:
                        pass
            if points:
                pre = data.get("prePrice") or data.get("preClose")
                result = {
                    "pre_close": float(pre) if pre is not None else None,
                    "points": points,
                    "fetched_at": _bj_now_iso(),
                }
    except Exception:
        result = None
    if result:
        _CACHE[key] = (time.time(), result)
    return result


async def fetch_sina_ohlc(symbol: str, points: int = 160) -> list:
    """新浪财经 A股日线 OHLC+量 [{d,o,c,h,l,v}]（不复权）。直连绕代理 + 缓存 盘中15min/休市6h。失败 []。

    A股 K 线**主源**——push2his（东财）对生产 IP 间歇性空回/被封，新浪走另一套基建更稳。
    symbol 形如 '600519'(沪 sh) / '000858'(深 sz)；非 6 位返回 []（港美股不走此源）。
    最新一根蜡烛带 fetched_at（取数时刻·北京时间）。"""
    code = re.sub(r"\D", "", symbol or "")
    if len(code) != 6:
        return []
    prefix = "sh" if code[0] in ("6", "9") else "sz"
    key = f"sina_ohlc:{prefix}{code}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _market_ttl():
        return hit[1]
    url = (
        "https://money.finance.sina.com.cn/quotes_service/api/json_v2.php/"
        f"CN_MarketData.getKLineData?symbol={prefix}{code}&scale=240&ma=no&datalen={max(1, min(points, 1000))}"
    )
    out: list = []
    for attempt in range(3):
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=12.0) as client:
                r = await client.get(url, headers=_HEADERS)
            if r.status_code == 200 and r.text.strip().startswith("["):
                for it in (r.json() or []):
                    try:
                        out.append({
                            "d": str(it["day"])[:10],
                            "o": float(it["open"]),
                            "c": float(it["close"]),
                            "h": float(it["high"]),
                            "l": float(it["low"]),
                            "v": float(it.get("volume") or 0),
                        })
                    except (ValueError, KeyError, TypeError):
                        pass
                if out:
                    # 数据时点（北京时间）——只挂最新一根（历史蜡烛已定型无需时点；只加字段，向后兼容）
                    out[-1]["fetched_at"] = _bj_now_iso()
                    break
        except Exception:
            out = []
        if attempt < 2:
            await asyncio.sleep(0.5 * (attempt + 1))
    if out:
        _CACHE[key] = (time.time(), out)
    return out


async def fetch_fund_flow(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """A股资金面（live）：主力近5日净流入(push2his fflow) + 最近龙虎榜净额(datacenter)。仅A股，否则 None。
    缓存 盘中15min/休市6h；成功结果带 as_of（取数时刻·北京时间）。"""
    code = re.sub(r"\D", "", symbol or "")
    if ((market or "").upper() not in ("", "CN")) or len(code) != 6:
        return None
    key = f"flow:{code}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _market_ttl():
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
        result["unit"] = "元"  # 主力净流入/龙虎榜净额单位是元
        result["as_of"] = _bj_now_iso()  # 取数时刻（北京时间）——资金流盘中变化剧烈，AI 引用须知数据时点
        for _k in ("main_flow_5d", "lhb_net"):
            if result.get(_k) is not None:
                try:
                    result[f"{_k}_yi"] = round(float(result[_k]) / 1e8, 2)  # 换算成亿元，防模型掉量级
                except (TypeError, ValueError):
                    pass
        _CACHE[key] = (time.time(), result)
        return result
    _CACHE[key] = (time.time(), None)
    return None
