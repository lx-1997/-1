from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Optional

from . import bull_playbook
from .shared_utils import safe_float

"""个股财务报表多源取数 + 声明式回退链 —— 给《价值投资之长线牛股》方法论(bull_playbook)喂 ground-truth。

提供：经营/投资/筹资活动现金流量净额、capex、自由现金流、营收/归母净利、每股经营现金流、扣非EPS、毛利率、ROE。
据此可跑「现金流八类型(第9.5)」「自由现金流(第10章)」「盈利质量含金量/扣非(第4章)」——之前缺数据的'半成品'函数全部跑起来。

回退链(同 market_data.QUOTE_PROVIDERS 思路，新增源=表里加一行)：
  ① iFinD(权威，需 env DEEPFOCUS_IFIND_STATEMENTS=1 开启 + 指标 ID 在有 token 的服务器实测调优)
  ② 东财(已实测可用：业绩报表 + 现金流量表)——默认主力兜底
任一源拿到关键字段(现金流或净利或 ROE)即返回；全失败→None(优雅降级，绝不臆造)。
"""

_CACHE: dict[str, tuple[float, Optional[dict]]] = {}
_TTL = 12 * 3600.0    # 季报数据慢变，12h 缓存够新又省配额

# iFinD basic_data 指标映射(缺省为常见 ID；可经 env DEEPFOCUS_IFIND_FIN_INDICATORS=<json> 覆盖)。
# ⚠️不同账号/版本 ID 可能不同，故 iFinD 源默认关闭，开启后在服务器按真实返回校准。
_IFIND_FIN_DEFAULT = {
    "ocf": "ths_ncf_from_oa_stock",            # 经营活动现金流量净额
    "icf": "ths_ncf_from_ia_stock",            # 投资活动现金流量净额
    "fcf_financing": "ths_ncf_from_fa_stock",  # 筹资活动现金流量净额
    "capex": "ths_cash_paid_assets_stock",     # 购建固定/无形/长期资产支付的现金
    "revenue": "ths_revenue_stock",            # 营业收入
    "net_income": "ths_np_atsopc_stock",       # 归母净利润
    "deduct_np": "ths_np_atsopc_dednr_stock",  # 扣非归母净利润
    "gross_margin": "ths_gross_selling_rate_stock",  # 销售毛利率
    "roe": "ths_roe_avg_stock",                # 加权 ROE
}


def _normalize(*, ocf=None, icf=None, fcf_financing=None, capex=None, revenue=None, net_income=None,
               eps=None, deduct_eps=None, deduct_np=None, ocf_per_share=None, gross_margin=None,
               roe=None, revenue_yoy=None, profit_yoy=None, accounts_receivable=None,
               advance_receipts=None, inventory=None, equity=None, report_date=None, name=None) -> dict:
    ocf, capex = safe_float(ocf), safe_float(capex)
    return {
        "ocf": ocf, "icf": safe_float(icf), "fcf_financing": safe_float(fcf_financing),
        "capex": capex, "fcf": bull_playbook.free_cash_flow(ocf, capex),
        "revenue": safe_float(revenue), "net_income": safe_float(net_income),
        "eps": safe_float(eps), "deduct_eps": safe_float(deduct_eps), "deduct_np": safe_float(deduct_np),
        "ocf_per_share": safe_float(ocf_per_share), "gross_margin": safe_float(gross_margin),
        "roe": safe_float(roe), "revenue_yoy": safe_float(revenue_yoy), "profit_yoy": safe_float(profit_yoy),
        "accounts_receivable": safe_float(accounts_receivable), "advance_receipts": safe_float(advance_receipts),
        "inventory": safe_float(inventory), "equity": safe_float(equity),
        "report_date": report_date, "name": name,
    }


async def _eastmoney_statements(symbol: str, market: Optional[str]) -> Optional[dict]:
    """东财(已实测)：业绩报表(每股经营现金流/扣非EPS/毛利率/ROE/同比) + 现金流量表(经营/投资/筹资净额/capex)。"""
    from .eastmoney_data import (fetch_eastmoney_balance, fetch_eastmoney_cashflow,
                                 fetch_eastmoney_earnings)
    perf = await fetch_eastmoney_earnings(symbol, market) or {}
    cf = await fetch_eastmoney_cashflow(symbol, market) or {}
    bal = await fetch_eastmoney_balance(symbol, market) or {}
    if not perf and not cf and not bal:
        return None
    return _normalize(
        ocf=cf.get("ocf"), icf=cf.get("icf"), fcf_financing=cf.get("fcf_financing"), capex=cf.get("capex"),
        revenue=perf.get("revenue"), net_income=perf.get("net_income"),
        eps=perf.get("eps"), deduct_eps=perf.get("deduct_eps"), ocf_per_share=perf.get("ocf_per_share"),
        gross_margin=perf.get("gross_margin"), roe=perf.get("roe"),
        revenue_yoy=perf.get("revenue_yoy"), profit_yoy=perf.get("profit_yoy"),
        accounts_receivable=bal.get("accounts_receivable"), advance_receipts=bal.get("advance_receipts"),
        inventory=bal.get("inventory"), equity=bal.get("equity"),
        report_date=(cf.get("report_date") or perf.get("report_date") or bal.get("report_date")),
        name=perf.get("name"))


async def _ifind_statements(symbol: str, market: Optional[str]) -> Optional[dict]:
    """iFinD(权威，env 开关 + 指标可配)：默认关闭；开启后按 basic_data 指标映射取三表。失败→None 落到东财。"""
    if os.getenv("DEEPFOCUS_IFIND_STATEMENTS", "0") != "1":
        return None
    try:
        from . import ifind_api
    except Exception:  # noqa: BLE001
        return None
    if not (ifind_api.enabled() and ifind_api.normalize_a_code(symbol)):
        return None
    indi = dict(_IFIND_FIN_DEFAULT)
    override = os.getenv("DEEPFOCUS_IFIND_FIN_INDICATORS")
    if override:
        try:
            indi.update(json.loads(override))
        except Exception:  # noqa: BLE001
            pass
    res = await asyncio.to_thread(ifind_api.basic_data, symbol, ",".join(indi.values()))
    if not (res and res.get("ok") and res.get("rows")):
        return None
    row = res["rows"][0]
    g = lambda key: safe_float(row.get(indi[key]))  # noqa: E731  指标名→值
    return _normalize(ocf=g("ocf"), icf=g("icf"), fcf_financing=g("fcf_financing"), capex=g("capex"),
                      revenue=g("revenue"), net_income=g("net_income"), deduct_np=g("deduct_np"),
                      gross_margin=g("gross_margin"), roe=g("roe"))


# 声明式回退表：(名, async handler)。新增源=加一行。
STATEMENT_PROVIDERS = [("ifind", _ifind_statements), ("eastmoney", _eastmoney_statements)]


async def fetch_statements(symbol: str, market: Optional[str] = None) -> Optional[dict]:
    """按回退链取财务报表，返回归一化 dict(含 provider/cashflow_pattern/quality)。全失败→None。缓存 12h。"""
    code = (symbol or "").strip()
    key = f"{code}:{(market or '').upper()}"
    hit = _CACHE.get(key)
    if hit and (time.time() - hit[0]) < _TTL:
        return hit[1]
    out: Optional[dict] = None
    for name, fn in STATEMENT_PROVIDERS:
        try:
            data = await fn(symbol, market)
        except Exception:  # noqa: BLE001
            data = None
        if data and any(data.get(k) is not None for k in ("ocf", "net_income", "roe")):
            data["provider"] = name
            out = data
            break
    if out is not None:
        out.update(assess_quality(out))
    _CACHE[key] = (time.time(), out)
    return out


def assess_quality(stmt: dict) -> dict:
    """把归一化报表喂进 bull_playbook，跑现金流八类型 + 自由现金流 + 盈利质量(含金量/扣非占比)。"""
    ocf, icf, fin = stmt.get("ocf"), stmt.get("icf"), stmt.get("fcf_financing")
    cf_type = bull_playbook.cashflow_type(ocf, icf, fin)
    eps, ocfps, deps = stmt.get("eps"), stmt.get("ocf_per_share"), stmt.get("deduct_eps")
    # 含金量(经营现金流/净利)与扣非占比：每股口径相除等价于总额相除
    ni, ocf_for_q, nonrec = None, None, None
    if eps:
        ni = eps
        ocf_for_q = ocfps                                  # 每股经营现金流
        if deps is not None:
            nonrec = eps - deps                            # 非经常性损益(每股)
    # 还原应收/预收(第4.3)：应收/营收越小越好、预收/营收越大越好
    eq = bull_playbook.earnings_quality(net_income=ni, ocf=ocf_for_q, non_recurring=nonrec,
                                        revenue=stmt.get("revenue"),
                                        accounts_receivable=stmt.get("accounts_receivable"),
                                        advance_receipts=stmt.get("advance_receipts"))
    ar, adv = stmt.get("accounts_receivable"), stmt.get("advance_receipts")
    gb = bull_playbook.good_business(roe=stmt.get("roe"), gross_margin=stmt.get("gross_margin"),
                                     ocf_to_ni=(eq["detail"].get("cfo_ratio") if eq.get("detail") else None),
                                     advance_over_receivable=(adv > ar if (adv is not None and ar is not None) else None))
    return {"cashflow_type": cf_type, "earnings_quality": eq, "good_business": gb,
            "fcf": stmt.get("fcf")}
