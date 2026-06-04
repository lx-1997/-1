from __future__ import annotations

from .shared_utils import num, safe_int, pct_change, fmt_usd, fmt_pct, safe_error

import asyncio
import copy
import re
from datetime import datetime, timezone
from typing import Any, Optional

import httpx


COMTRADE_PREVIEW_URL = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"
COMTRADE_AVAILABILITY_URL = "https://comtradeapi.un.org/public/v1/getDa/C/M/HS"
COMTRADE_HS_REFERENCE_URL = "https://comtradeapi.un.org/files/v1/app/reference/HS.json"
GACC_STATS_URL = "https://stats.customs.gov.cn/"
HS_DETAIL_CACHE_TTL_SECONDS = 900
HS_REFERENCE_CACHE_TTL_SECONDS = 7 * 24 * 3600
DEFAULT_DETAIL_MONTHS = 12

REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.7",
}

_HS_REFERENCE_CACHE: Optional[tuple[datetime, list[dict[str, Any]]]] = None
_HS_DETAIL_CACHE: dict[str, tuple[datetime, dict[str, Any]]] = {}
_AVAILABILITY_CACHE: Optional[tuple[datetime, str]] = None
_COMTRADE_CALL_LOCK = asyncio.Lock()
_COMTRADE_LAST_CALL_TS = 0.0


HS_DETAIL_ALIASES: dict[str, dict[str, Any]] = {
    "硝化棉": {
        "code": "391220",
        "name_zh": "初级形状的硝酸纤维素（包括棉胶）",
        "aliases": ["硝酸纤维素", "棉胶", "cellulose nitrates", "nitrocellulose"],
        "industry": "精细化工、涂料油墨、含能材料",
    },
    "硝酸纤维素": {
        "code": "391220",
        "name_zh": "初级形状的硝酸纤维素（包括棉胶）",
        "aliases": ["硝化棉", "棉胶", "cellulose nitrates", "nitrocellulose"],
        "industry": "精细化工、涂料油墨、含能材料",
    },
    "变压器": {
        "code": "850434",
        "name_zh": "额定容量超过500千伏安的其他变压器",
        "aliases": ["输变电设备", "大型变压器", "transformers"],
        "industry": "输变电设备",
    },
    "逆变器": {
        "code": "850440",
        "name_zh": "静止式变流器",
        "aliases": ["变流器", "电源转换器", "inverters", "static converters"],
        "industry": "光伏逆变器、储能变流器、电源设备",
    },
    "集成电路": {
        "code": "854239",
        "name_zh": "其他集成电路",
        "aliases": ["芯片", "integrated circuits", "semiconductor chips"],
        "industry": "半导体",
    },
    "处理器": {
        "code": "854231",
        "name_zh": "处理器及控制器集成电路",
        "aliases": ["控制器", "processors", "controllers"],
        "industry": "半导体、计算芯片",
    },
    "印刷电路": {
        "code": "853400",
        "name_zh": "印刷电路",
        "aliases": ["PCB", "printed circuits"],
        "industry": "电子零部件、PCB",
    },
    "光模块": {
        "code": "851762",
        "name_zh": "接收、转换并发送或再生声音、图像或其他数据的设备",
        "aliases": ["通信模块", "交换路由设备", "optical modules", "communication apparatus"],
        "industry": "通信设备、光通信",
    },
    "自动数据处理设备": {
        "code": "847150",
        "name_zh": "处理部件（自动数据处理设备）",
        "aliases": ["服务器", "计算服务器", "processing units"],
        "industry": "服务器、计算设备",
    },
    "服务器": {
        "code": "847150",
        "name_zh": "处理部件（自动数据处理设备）",
        "aliases": ["自动数据处理设备", "计算服务器", "processing units"],
        "industry": "服务器、计算设备",
    },
    "锂电池": {
        "code": "850760",
        "name_zh": "锂离子蓄电池",
        "aliases": ["锂离子电池", "lithium-ion accumulators"],
        "industry": "新能源电池",
    },
    "电机": {
        "code": "850152",
        "name_zh": "输出功率超过750瓦但不超过75千瓦的多相交流电动机",
        "aliases": ["交流电机", "electric motors"],
        "industry": "工业电机、自动化",
    },
}


async def search_customs_hs_detail_products(query: str, *, limit: int = 20) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {
            "query": query,
            "source_status": "empty",
            "candidates": [],
            "sources": _detail_sources(),
            "warnings": ["请输入 HS 编码、中文商品名或英文商品名。"],
        }

    warnings: list[str] = []
    candidates = _alias_candidates(query)
    try:
        async with httpx.AsyncClient(timeout=18, follow_redirects=True, headers=REQUEST_HEADERS) as client:
            reference = await _fetch_hs_reference(client)
        candidates.extend(_reference_candidates(reference, query))
    except Exception:
        warnings.append("UN Comtrade HS 商品参考表读取失败，仅返回内置中文别名匹配。")

    deduped = _dedupe_candidates(candidates)
    return {
        "query": query,
        "source_status": "live" if deduped else "empty",
        "candidates": deduped[: max(1, min(limit, 50))],
        "sources": _detail_sources(),
        "warnings": warnings,
    }


async def fetch_customs_hs_detail_snapshot(
    *,
    query: Optional[str] = None,
    code: Optional[str] = None,
    months: int = DEFAULT_DETAIL_MONTHS,
) -> dict[str, Any]:
    clean_code = re.sub(r"\D", "", code or "")
    clean_query = (query or "").strip()
    months = max(3, min(months or DEFAULT_DETAIL_MONTHS, 24))
    cache_key = f"{clean_code}|{clean_query.lower()}|{months}"
    now = datetime.now(timezone.utc)
    if cache_key in _HS_DETAIL_CACHE:
        cached_at, cached = _HS_DETAIL_CACHE[cache_key]
        if (now - cached_at).total_seconds() < HS_DETAIL_CACHE_TTL_SECONDS:
            return copy.deepcopy(cached)

    warnings: list[str] = []
    async with httpx.AsyncClient(timeout=22, follow_redirects=True, headers=REQUEST_HEADERS) as client:
        try:
            product = await _resolve_product(client, clean_query, clean_code)
        except Exception as exc:
            warnings.append(f"UN Comtrade 商品参考表读取失败，仅用内置别名匹配（{safe_error(exc)}）")
            if clean_code:
                product = _merge_product({"code": clean_code, "name": ""}, _alias_by_code(clean_code))
            else:
                local_candidates = _alias_candidates(clean_query)
                product = local_candidates[0] if local_candidates else None
        if not product:
            search_result = await search_customs_hs_detail_products(clean_query or clean_code, limit=8)
            response = {
                "generated_at": now.isoformat(),
                "source_status": "empty",
                "query": clean_query,
                "code": clean_code,
                "currency": "USD",
                "unit": "USD",
                "coverage": "HS6",
                "product": None,
                "latest_period": None,
                "month_label": "",
                "monthly_points": [],
                "top_export_partners": [],
                "top_import_partners": [],
                "candidates": search_result.get("candidates") or [],
                "sources": _detail_sources(),
                "warnings": ["未找到匹配的 HS 商品编码，请尝试输入 6 位 HS 编码。", *warnings],
            }
            _HS_DETAIL_CACHE[cache_key] = (now, copy.deepcopy(response))
            return response

        try:
            latest_period = await _fetch_latest_available_period(client)
            periods = _recent_periods(latest_period, months)
            monthly_payloads = await _fetch_monthly_world_rows(client, product["code"], periods)
            monthly_points = _build_monthly_points(monthly_payloads)
            export_partners, import_partners = await asyncio.gather(
                _fetch_partner_rows(client, product["code"], latest_period, "X"),
                _fetch_partner_rows(client, product["code"], latest_period, "M"),
            )
        except Exception as exc:
            warnings.append(f"UN Comtrade HS6 明细/贸易伙伴数据读取失败，相关曲线暂缺（{safe_error(exc)}）")
            latest_period = None
            monthly_points = []
            export_partners, import_partners = [], []

    if latest_period and latest_period < _current_period():
        warnings.append(
            f"HS6细项来自 UN Comtrade 中国报告方公开数据，最新可用月份为 {latest_period[:4]}-{latest_period[4:]}，较海关英文快报可能滞后。"
        )
    if not monthly_points:
        warnings.append("该 HS 编码最近可用月份未返回世界合计行，可能是当期无贸易或编码口径变更。")

    response = {
        "generated_at": now.isoformat(),
        "source_status": "live" if monthly_points else "empty",
        "query": clean_query,
        "code": product["code"],
        "currency": "USD",
        "unit": "USD",
        "coverage": "HS6",
        "product": product,
        "latest_period": latest_period,
        "month_label": _period_label(latest_period),
        "monthly_points": monthly_points,
        "top_export_partners": export_partners[:12],
        "top_import_partners": import_partners[:12],
        "candidates": _dedupe_candidates([product, *_alias_candidates(clean_query or product["code"])])[:8],
        "sources": _detail_sources(),
        "warnings": warnings,
    }
    _HS_DETAIL_CACHE[cache_key] = (now, copy.deepcopy(response))
    return response


def build_customs_hs_detail_analysis_text(detail: dict[str, Any]) -> str:
    product = detail.get("product") or {}
    lines = [
        "",
        "十三、HS6细商品明细（当前选中项，必须纳入分析）",
        f"商品：{product.get('code') or detail.get('code')} {product.get('name_zh') or product.get('name') or '未知'}",
        f"英文名：{product.get('name') or ''}",
        f"行业提示：{product.get('industry') or '未标注'}",
        f"数据覆盖：{detail.get('coverage') or 'HS6'}，最新月份：{detail.get('month_label') or detail.get('latest_period') or '未知'}，单位：{detail.get('unit') or 'USD'}。",
        "近12个月明细：",
    ]
    for row in detail.get("monthly_points") or []:
        lines.append(
            f"- {row.get('month')}: 出口{fmt_usd(row.get('export_value_usd'))}、进口{fmt_usd(row.get('import_value_usd'))}、"
            f"差额{fmt_usd(row.get('balance_value_usd'), signed=True)}；"
            f"出口数量{_fmt_quantity(row.get('export_quantity'), row.get('export_quantity_unit'))}、"
            f"进口数量{_fmt_quantity(row.get('import_quantity'), row.get('import_quantity_unit'))}；"
            f"出口环比{fmt_pct(row.get('export_mom_pct'))}、进口环比{fmt_pct(row.get('import_mom_pct'))}。"
        )
    if detail.get("top_export_partners"):
        lines.append("最新月出口目的地：")
        lines.extend(
            f"- {row.get('partner_zh') or row.get('partner')}: {fmt_usd(row.get('value_usd'))}，数量{_fmt_quantity(row.get('quantity'), row.get('quantity_unit'))}"
            for row in (detail.get("top_export_partners") or [])[:8]
        )
    if detail.get("top_import_partners"):
        lines.append("最新月进口来源地：")
        lines.extend(
            f"- {row.get('partner_zh') or row.get('partner')}: {fmt_usd(row.get('value_usd'))}，数量{_fmt_quantity(row.get('quantity'), row.get('quantity_unit'))}"
            for row in (detail.get("top_import_partners") or [])[:8]
        )
    if detail.get("warnings"):
        lines.append("HS6数据提示：")
        lines.extend(f"- {warning}" for warning in detail.get("warnings") or [])
    return "\n".join(line for line in lines if line is not None)


async def _resolve_product(
    client: httpx.AsyncClient,
    query: str,
    code: str,
) -> Optional[dict[str, Any]]:
    if code:
        alias = _alias_by_code(code)
        reference = await _fetch_hs_reference(client)
        from_reference = next((row for row in _reference_candidates(reference, code) if row.get("code") == code), None)
        return _merge_product(from_reference or {"code": code, "name": ""}, alias)

    alias_candidates = _alias_candidates(query)
    if alias_candidates:
        reference = await _fetch_hs_reference(client)
        from_reference = next(
            (row for row in _reference_candidates(reference, alias_candidates[0]["code"]) if row.get("code") == alias_candidates[0]["code"]),
            None,
        )
        return _merge_product(from_reference or alias_candidates[0], alias_candidates[0])

    reference = await _fetch_hs_reference(client)
    candidates = _reference_candidates(reference, query)
    return candidates[0] if candidates else None


async def _fetch_hs_reference(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    global _HS_REFERENCE_CACHE
    now = datetime.now(timezone.utc)
    if _HS_REFERENCE_CACHE and (now - _HS_REFERENCE_CACHE[0]).total_seconds() < HS_REFERENCE_CACHE_TTL_SECONDS:
        return copy.deepcopy(_HS_REFERENCE_CACHE[1])

    response = await client.get(COMTRADE_HS_REFERENCE_URL)
    response.raise_for_status()
    payload = response.json()
    rows = payload.get("results") or []
    parsed: list[dict[str, Any]] = []
    for row in rows:
        code = str(row.get("id") or "").strip()
        if not re.match(r"^\d{2,6}$", code):
            continue
        text = str(row.get("text") or "")
        name = re.sub(r"^\d{2,6}\s*-\s*", "", text).strip()
        parsed.append(
            {
                "code": code,
                "name": name,
                "name_zh": None,
                "aggr_level": safe_int(row.get("aggrLevel")),
                "is_leaf": str(row.get("isLeaf")) == "1",
                "quantity_unit": row.get("standardUnitAbbr"),
                "source": "UN Comtrade HS reference",
            }
        )
    _HS_REFERENCE_CACHE = (now, copy.deepcopy(parsed))
    return parsed


async def _fetch_latest_available_period(client: httpx.AsyncClient) -> str:
    global _AVAILABILITY_CACHE
    now = datetime.now(timezone.utc)
    if _AVAILABILITY_CACHE and (now - _AVAILABILITY_CACHE[0]).total_seconds() < HS_DETAIL_CACHE_TTL_SECONDS:
        return _AVAILABILITY_CACHE[1]

    response = await client.get(COMTRADE_AVAILABILITY_URL, params={"reportercode": "156"})
    response.raise_for_status()
    payload = response.json()
    periods = [
        str(row.get("period"))
        for row in payload.get("data") or []
        if row.get("classificationSearchCode") == "HS" and str(row.get("classificationCode") or "").startswith("H")
    ]
    latest = max(periods) if periods else _shift_period(_current_period(), -18)
    _AVAILABILITY_CACHE = (now, latest)
    return latest


async def _fetch_monthly_world_rows(
    client: httpx.AsyncClient,
    code: str,
    periods: list[str],
) -> dict[tuple[str, str], Optional[dict[str, Any]]]:
    period_param = ",".join(periods)
    export_rows, import_rows = await asyncio.gather(
        _fetch_comtrade_rows(client, code, period_param, "X", partner_code=0, max_records=max(50, len(periods) + 5)),
        _fetch_comtrade_rows(client, code, period_param, "M", partner_code=0, max_records=max(50, len(periods) + 5)),
    )
    result: dict[tuple[str, str], Optional[dict[str, Any]]] = {}
    for flow, rows in (("X", export_rows), ("M", import_rows)):
        by_period = {
            str(row.get("period")): row
            for row in rows
            if safe_int(row.get("partnerCode")) == 0
        }
        for period in periods:
            result[(period, flow)] = by_period.get(period)
    return result


async def _fetch_partner_rows(
    client: httpx.AsyncClient,
    code: str,
    period: str,
    flow: str,
) -> list[dict[str, Any]]:
    rows = await _fetch_comtrade_rows(client, code, period, flow, partner_code=None, max_records=500)
    parsed = [
        _parse_partner_row(row, flow)
        for row in rows
        if safe_int(row.get("partnerCode")) not in (None, 0) and not row.get("isAggregate")
    ]
    return sorted(parsed, key=lambda row: row.get("value_usd") or 0, reverse=True)


async def _fetch_comtrade_rows(
    client: httpx.AsyncClient,
    code: str,
    period: str,
    flow: str,
    *,
    partner_code: Optional[int],
    max_records: int,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {
        "reporterCode": "156",
        "period": period,
        "cmdCode": code,
        "flowCode": flow,
        "includeDesc": "true",
        "maxRecords": str(max_records),
        "format": "JSON",
        "breakdownMode": "classic",
    }
    if partner_code is not None:
        params["partnerCode"] = str(partner_code)
    for attempt in range(4):
        await _wait_for_comtrade_slot()
        response = await client.get(COMTRADE_PREVIEW_URL, params=params)
        if response.status_code == 429:
            await asyncio.sleep(4.0 + attempt * 4.0)
            continue
        response.raise_for_status()
        payload = response.json()
        return payload.get("data") or []
    return []


async def _wait_for_comtrade_slot() -> None:
    global _COMTRADE_LAST_CALL_TS
    async with _COMTRADE_CALL_LOCK:
        loop = asyncio.get_running_loop()
        elapsed = loop.time() - _COMTRADE_LAST_CALL_TS
        wait_seconds = max(0.0, 0.9 - elapsed)
        if wait_seconds:
            await asyncio.sleep(wait_seconds)
        _COMTRADE_LAST_CALL_TS = loop.time()


def _build_monthly_points(rows_by_key: dict[tuple[str, str], Optional[dict[str, Any]]]) -> list[dict[str, Any]]:
    periods = sorted({period for period, _flow in rows_by_key})
    points: list[dict[str, Any]] = []
    previous: Optional[dict[str, Any]] = None
    for period in periods:
        export_row = rows_by_key.get((period, "X")) or {}
        import_row = rows_by_key.get((period, "M")) or {}
        export_value = num(export_row.get("primaryValue"))
        import_value = num(import_row.get("primaryValue"))
        point = {
            "period": period,
            "month": f"{period[:4]}-{period[4:]}",
            "export_value_usd": export_value,
            "import_value_usd": import_value,
            "trade_value_usd": _sum_optional(export_value, import_value),
            "balance_value_usd": _subtract(export_value, import_value),
            "export_quantity": num(export_row.get("qty")),
            "export_quantity_unit": export_row.get("qtyUnitAbbr") or export_row.get("altQtyUnitAbbr"),
            "import_quantity": num(import_row.get("qty")),
            "import_quantity_unit": import_row.get("qtyUnitAbbr") or import_row.get("altQtyUnitAbbr"),
            "export_unit_value_usd": _unit_value(export_value, num(export_row.get("qty"))),
            "import_unit_value_usd": _unit_value(import_value, num(import_row.get("qty"))),
            "cmd_desc": export_row.get("cmdDesc") or import_row.get("cmdDesc"),
        }
        point["export_mom_pct"] = pct_change(
            point.get("export_value_usd"),
            previous.get("export_value_usd") if previous else None,
        )
        point["import_mom_pct"] = pct_change(
            point.get("import_value_usd"),
            previous.get("import_value_usd") if previous else None,
        )
        point["trade_mom_pct"] = pct_change(
            point.get("trade_value_usd"),
            previous.get("trade_value_usd") if previous else None,
        )
        points.append(point)
        previous = point
    return points


def _parse_partner_row(row: dict[str, Any], flow: str) -> dict[str, Any]:
    value = num(row.get("primaryValue"))
    quantity = num(row.get("qty"))
    return {
        "direction": "export" if flow == "X" else "import",
        "partner": row.get("partnerDesc") or "",
        "partner_zh": _partner_zh(str(row.get("partnerDesc") or "")),
        "partner_iso": row.get("partnerISO"),
        "value_usd": value,
        "quantity": quantity,
        "quantity_unit": row.get("qtyUnitAbbr") or row.get("altQtyUnitAbbr"),
        "unit_value_usd": _unit_value(value, quantity),
    }


def _alias_candidates(query: str) -> list[dict[str, Any]]:
    query_norm = _normalize_query(query)
    candidates: list[dict[str, Any]] = []
    for keyword, info in HS_DETAIL_ALIASES.items():
        values = [keyword, info.get("code", ""), info.get("name_zh", ""), *(info.get("aliases") or [])]
        if any(query_norm and query_norm in _normalize_query(str(value)) for value in values):
            candidates.append(
                {
                    "code": info["code"],
                    "name": "",
                    "name_zh": info.get("name_zh"),
                    "aliases": info.get("aliases") or [],
                    "industry": info.get("industry"),
                    "aggr_level": len(info["code"]),
                    "is_leaf": len(info["code"]) >= 6,
                    "source": "内置中文别名",
                    "matched_by": keyword,
                }
            )
    if re.match(r"^\d{2,10}$", query_norm):
        alias = _alias_by_code(query_norm)
        if alias:
            candidates.insert(0, alias)
    return candidates


def _alias_by_code(code: str) -> Optional[dict[str, Any]]:
    for keyword, info in HS_DETAIL_ALIASES.items():
        if info.get("code") == code:
            return {
                "code": code,
                "name": "",
                "name_zh": info.get("name_zh"),
                "aliases": [keyword, *(info.get("aliases") or [])],
                "industry": info.get("industry"),
                "aggr_level": len(code),
                "is_leaf": len(code) >= 6,
                "source": "内置中文别名",
                "matched_by": keyword,
            }
    return None


def _reference_candidates(reference: list[dict[str, Any]], query: str) -> list[dict[str, Any]]:
    query_norm = _normalize_query(query)
    if not query_norm:
        return []
    candidates: list[dict[str, Any]] = []
    is_code = bool(re.match(r"^\d{2,10}$", query_norm))
    for row in reference:
        code = str(row.get("code") or "")
        name = str(row.get("name") or "")
        if is_code:
            matched = code.startswith(query_norm[:6])
        else:
            matched = query_norm in _normalize_query(name)
        if matched:
            alias = _alias_by_code(code)
            candidates.append(_merge_product(row, alias))
    return sorted(
        candidates,
        key=lambda row: (
            0 if row.get("code") == query_norm else 1,
            0 if row.get("is_leaf") else 1,
            len(str(row.get("code") or "")),
            str(row.get("code") or ""),
        ),
    )


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        code = str(candidate.get("code") or "")
        if not code:
            continue
        if code not in merged:
            merged[code] = dict(candidate)
            continue
        existing = merged[code]
        for key in ("name", "name_zh", "industry", "quantity_unit"):
            if not existing.get(key) and candidate.get(key):
                existing[key] = candidate[key]
        existing_aliases = list(existing.get("aliases") or [])
        for alias in candidate.get("aliases") or []:
            if alias not in existing_aliases:
                existing_aliases.append(alias)
        existing["aliases"] = existing_aliases
    return list(merged.values())


def _merge_product(base: Optional[dict[str, Any]], alias: Optional[dict[str, Any]]) -> dict[str, Any]:
    product = dict(base or {})
    if alias:
        for key in ("name_zh", "aliases", "industry", "matched_by"):
            if alias.get(key):
                product[key] = alias[key]
        product["source"] = f"{product.get('source') or 'UN Comtrade'} + 内置中文别名"
    product.setdefault("name_zh", None)
    product.setdefault("aliases", [])
    product.setdefault("industry", None)
    return product


def _detail_sources() -> list[dict[str, str]]:
    return [
        {
            "name": "UN Comtrade public preview API",
            "url": "https://comtradeplus.un.org/",
            "type": "hs6_monthly_supplement",
            "note": "联合国商品贸易数据库公开预览接口，使用中国作为报告方的 HS6 月度商品数据，适合补充细商品层级。",
        },
        {
            "name": "GACC Customs Statistics Online Query",
            "url": GACC_STATS_URL,
            "type": "official_hs_query_reference",
            "note": "中国海关统计数据在线查询平台，可查询更细商品编码；当前服务端直连受证书/云防护影响，作为官方口径入口保留。",
        },
    ]


def _recent_periods(latest_period: str, count: int) -> list[str]:
    return [_shift_period(latest_period, offset) for offset in range(-(count - 1), 1)]


def _shift_period(period: str, month_delta: int) -> str:
    year = int(period[:4])
    month = int(period[4:])
    month_index = year * 12 + month - 1 + month_delta
    next_year = month_index // 12
    next_month = month_index % 12 + 1
    return f"{next_year}{next_month:02d}"


def _current_period() -> str:
    now = datetime.now(timezone.utc)
    return f"{now.year}{now.month:02d}"


def _period_label(period: Optional[str]) -> str:
    if not period or len(period) != 6:
        return ""
    return f"{period[:4]}-{period[4:]}"


def _normalize_query(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _subtract(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None or right is None:
        return None
    return round(left - right, 6)


def _sum_optional(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None and right is None:
        return None
    return round((left or 0) + (right or 0), 6)


def _unit_value(value: Optional[float], quantity: Optional[float]) -> Optional[float]:
    if value is None or quantity in (None, 0):
        return None
    return round(value / quantity, 6)
def _fmt_quantity(value: Optional[float], unit: Optional[str]) -> str:
    if value is None:
        return "—"
    return f"{value:,.0f}{unit or ''}"
def _partner_zh(name: str) -> Optional[str]:
    mapping = {
        "World": "全球",
        "Viet Nam": "越南",
        "Rep. of Korea": "韩国",
        "Republic of Korea": "韩国",
        "United States of America": "美国",
        "USA": "美国",
        "Japan": "日本",
        "Germany": "德国",
        "France": "法国",
        "Thailand": "泰国",
        "Indonesia": "印度尼西亚",
        "India": "印度",
        "Türkiye": "土耳其",
        "Turkey": "土耳其",
        "Mexico": "墨西哥",
        "Malaysia": "马来西亚",
        "Singapore": "新加坡",
        "Brazil": "巴西",
        "Pakistan": "巴基斯坦",
        "South Africa": "南非",
        "Spain": "西班牙",
        "Saudi Arabia": "沙特阿拉伯",
        "Algeria": "阿尔及利亚",
        "Nigeria": "尼日利亚",
        "United Arab Emirates": "阿联酋",
        "Hong Kong, China": "中国香港",
        "China, Hong Kong SAR": "中国香港",
    }
    return mapping.get(name)
