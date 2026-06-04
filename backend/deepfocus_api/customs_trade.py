from __future__ import annotations

from .shared_utils import num, pct_change, fmt_usd, fmt_pct, safe_error

import asyncio
import copy
import json
import re
from datetime import datetime, timezone
from html import unescape
from typing import Any, Optional
from urllib.parse import urljoin

import httpx


PRELIMINARY_URL = "http://english.customs.gov.cn/Statistics/Statistics"
DETAILED_URL = "http://english.customs.gov.cn/Statistics/Statistics?ColumnId=2"
MONTHLY_REPORT_URL = "http://english.customs.gov.cn/statics/report/monthly.html"
PRELIMINARY_REPORT_URL = "http://english.customs.gov.cn/statics/report/preliminary.html"
REQUEST_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.8,zh-CN;q=0.7,zh;q=0.6",
}
SOURCE_CACHE_TTL_SECONDS = 900
HISTORY_MONTH_COUNT = 12

_SNAPSHOT_CACHE: Optional[tuple[datetime, dict[str, Any]]] = None

MONTHS = {
    "jan": "01",
    "feb": "02",
    "mar": "03",
    "apr": "04",
    "may": "05",
    "jun": "06",
    "jul": "07",
    "aug": "08",
    "sep": "09",
    "oct": "10",
    "nov": "11",
    "dec": "12",
}

HS_CHAPTER_ZH = {
    "01": "活动物",
    "02": "肉及食用杂碎",
    "03": "鱼、甲壳动物、软体动物及其他水生无脊椎动物",
    "04": "乳品；蛋品；天然蜂蜜；其他食用动物产品",
    "05": "其他动物产品",
    "06": "活树及其他活植物；鳞茎、根及类似品；插花及装饰用簇叶",
    "07": "食用蔬菜、根及块茎",
    "08": "食用水果及坚果；柑橘属水果或甜瓜的果皮",
    "09": "咖啡、茶、马黛茶及调味香料",
    "10": "谷物",
    "11": "制粉工业产品；麦芽；淀粉；菊粉；面筋",
    "12": "油籽、含油果实；工业或药用植物；稻草及秸秆；饲料",
    "13": "虫胶；树胶、树脂及其他植物液汁",
    "14": "编结用植物材料；其他植物产品",
    "15": "动植物油脂及其分解产品；精制食用油脂；动植物蜡",
    "16": "肉、鱼、甲壳动物等制品",
    "17": "糖及糖食",
    "18": "可可及可可制品",
    "19": "谷物、粮食粉、淀粉或乳的制品；糕饼点心",
    "20": "蔬菜、水果、坚果或植物其他部分的制品",
    "21": "杂项食品",
    "22": "饮料、酒及醋",
    "23": "食品工业残渣及废料；配制的动物饲料",
    "24": "烟草及烟草代用品制品",
    "25": "盐；硫磺；土及石料；石膏料、石灰及水泥",
    "26": "矿砂、矿渣及矿灰",
    "27": "矿物燃料、矿物油及其蒸馏产品；沥青物质；矿物蜡",
    "28": "无机化学品；贵金属、稀土金属等化合物",
    "29": "有机化学品",
    "30": "药品",
    "31": "肥料",
    "32": "鞣料浸膏及染料浸膏；颜料、涂料等",
    "33": "精油及香膏；芳香料制品及化妆盥洗品",
    "34": "肥皂、有机表面活性剂、洗涤剂、蜡等",
    "35": "蛋白类物质；改性淀粉；胶；酶",
    "36": "炸药；烟火制品；火柴；引火合金等",
    "37": "照相及电影用品",
    "38": "杂项化学产品",
    "39": "塑料及其制品",
    "40": "橡胶及其制品",
    "41": "生皮及皮革（毛皮除外）",
    "42": "皮革制品；旅行用品、手提包等；动物肠线制品",
    "43": "毛皮、人造毛皮及其制品",
    "44": "木及木制品；木炭",
    "45": "软木及软木制品",
    "46": "稻草、秸秆等编结材料制品；篮筐及柳条制品",
    "47": "木浆或其他纤维素浆；回收纸或纸板",
    "48": "纸及纸板；纸浆、纸或纸板制品",
    "49": "书籍、报纸、印刷品；手稿、打字稿及设计图纸",
    "50": "蚕丝",
    "51": "羊毛、动物细毛或粗毛；马毛纱线及机织物",
    "52": "棉花",
    "53": "其他植物纺织纤维；纸纱线及其机织物",
    "54": "化学纤维长丝",
    "55": "化学纤维短纤",
    "56": "絮胎、毡呢及无纺织物；特种纱线；绳索及其制品",
    "57": "地毯及纺织材料铺地制品",
    "58": "特种机织物；簇绒织物；花边；装饰毯；刺绣品",
    "59": "浸渍、涂布、包覆或层压的纺织物；工业用纺织制品",
    "60": "针织物及钩编织物",
    "61": "针织或钩编的服装及衣着附件",
    "62": "非针织或非钩编的服装及衣着附件",
    "63": "其他纺织制成品；成套物品；旧衣着及旧纺织品；碎织物",
    "64": "鞋靴、护腿和类似品及其零件",
    "65": "帽类及其零件",
    "66": "雨伞、阳伞、手杖、鞭子及其零件",
    "67": "已加工羽毛、羽绒及其制品；人造花；人发制品",
    "68": "石料、石膏、水泥、石棉、云母等制品",
    "69": "陶瓷产品",
    "70": "玻璃及其制品",
    "71": "珠宝、贵金属及制品；仿首饰；硬币",
    "72": "钢铁",
    "73": "钢铁制品",
    "74": "铜及其制品",
    "75": "镍及其制品",
    "76": "铝及其制品",
    "78": "铅及其制品",
    "79": "锌及其制品",
    "80": "锡及其制品",
    "81": "其他贱金属、金属陶瓷及其制品",
    "82": "贱金属工具、器具、利口器、餐具及其零件",
    "83": "贱金属杂项制品",
    "84": "核反应堆、锅炉、机器、机械器具及零件",
    "85": "电机、电气设备及其零件；录音录像设备及零件",
    "86": "铁道及电车道机车车辆及其零件等",
    "87": "车辆及其零件、附件（铁道及电车道车辆除外）",
    "88": "航空器、航天器及其零件",
    "89": "船舶及浮动结构体",
    "90": "光学、照相、电影、计量、检验、医疗或外科仪器及设备；零附件",
    "91": "钟表及其零件",
    "92": "乐器及其零件、附件",
    "93": "武器、弹药及其零件、附件",
    "94": "家具；寝具、褥垫；灯具；活动房屋；发光标志等",
    "95": "玩具、游戏品、运动用品及其零件、附件",
    "96": "杂项制品",
    "97": "艺术品、收藏品及古物",
    "98": "特殊交易品及未分类商品",
    "99": "未另列明商品",
}

HS_SECTION_ZH = {
    "Ⅰ": "活动物及动物产品",
    "Ⅱ": "植物产品",
    "Ⅲ": "动植物油脂及蜡",
    "Ⅳ": "食品、饮料、酒、醋及烟草制品",
    "Ⅴ": "矿产品",
    "Ⅵ": "化工及相关工业产品",
    "Ⅶ": "塑料、橡胶及其制品",
    "Ⅷ": "生皮、皮革、毛皮及其制品；旅行用品等",
    "Ⅸ": "木及木制品；软木、草编等制品",
    "Ⅹ": "木浆、纸及纸制品",
    "Ⅺ": "纺织原料及纺织制品",
    "Ⅻ": "鞋帽、伞杖、羽毛制品、人造花等",
    "ⅩⅢ": "石料、水泥、陶瓷、玻璃及其制品",
    "ⅩⅣ": "珠宝、贵金属及其制品；仿首饰；硬币",
    "ⅩⅤ": "贱金属及其制品",
    "ⅩⅥ": "机器、机械器具、电气设备及其零件",
    "ⅩⅦ": "车辆、航空器、船舶及运输设备",
    "ⅩⅧ": "光学、计量、医疗仪器；钟表；乐器及零件",
    "ⅩⅨ": "武器、弹药及其零件",
    "ⅩⅩ": "杂项制品",
    "ⅩⅪ": "艺术品、收藏品及古物",
    "ⅩⅫ": "未按种类分类的商品",
}

PARTNER_ZH = {
    "APEC": "亚太经合组织",
    "Asia": "亚洲",
    "BRI": "共建“一带一路”国家",
    "RCEP": "区域全面经济伙伴关系成员",
    "Europe": "欧洲",
    "ASEAN": "东盟",
    "EU": "欧盟",
    "European Union": "欧盟",
    "North America": "北美洲",
    "Latin America": "拉丁美洲",
    "United States": "美国",
    "United States (US)": "美国",
    "Hong Kong, China": "中国香港",
    "Republic of Korea": "韩国",
    "Africa": "非洲",
    "Taiwan, China": "中国台湾",
    "Japan": "日本",
    "Viet Nam": "越南",
    "Oceania": "大洋洲",
    "Russia": "俄罗斯",
    "Australia": "澳大利亚",
    "Germany": "德国",
    "Malaysia": "马来西亚",
    "Indonesia": "印度尼西亚",
    "Brazil": "巴西",
    "Thailand": "泰国",
    "China": "中国",
    "India": "印度",
    "Singapore": "新加坡",
    "Switzerland": "瑞士",
    "Netherlands": "荷兰",
    "Mexico": "墨西哥",
    "United Kingdom": "英国",
    "Saudi Arabia": "沙特阿拉伯",
}

MAJOR_EXPORT_ZH = {
    "Mechanical and electrical products": "机电产品",
    "Hi-tech products": "高新技术产品",
    "Electronic integrated circuits": "集成电路",
    "Integrated circuits": "集成电路",
    "Automatic data processing machines and parts thereof": "自动数据处理设备及其零部件",
    "Automatic data processing equipment and parts thereof": "自动数据处理设备及其零部件",
    "Motor vehicles（including chassis fitted with engines)": "汽车（含装有发动机的底盘）",
    "Textile yarn, fabrics and articles thereof": "纺织纱线、织物及制品",
    "Garments and clothing accessories": "服装及衣着附件",
    "Plastic articles": "塑料制品",
    "Mobile phones": "手机",
    "Agriculture products": "农产品",
    "Electric appliances of household type": "家用电器",
    "Household appliances": "家用电器",
    "Parts and accessories of vehicle": "汽车零配件",
    "Automotive components and parts": "汽车零配件",
    "Products, of steel or iron": "钢铁制品",
    "Steel products": "钢材",
    "General machines": "通用机械设备",
    "General machinery": "通用机械设备",
    "Furniture and parts thereof": "家具及其零件",
    "Ships": "船舶",
    "Audio or video devices and parts thereof": "音视频设备及其零件",
    "Footwear": "鞋靴",
    "Refined petroleum products": "成品油",
    "Lamps and lighting fittings and parts thereof": "灯具、照明装置及其零件",
    "Lamps and light fittings and parts thereof": "灯具、照明装置及其零件",
    "Flat panel display modules of liquid crystals": "液晶平板显示模组",
    "LCD panels": "液晶面板",
    "Suit-cases, hand bags and similar containers": "箱包及类似容器",
    "Suitcases, handbags and similar containers": "箱包及类似容器",
    "Toys": "玩具",
    "Medical and pharmaceutical products": "医药品",
    "Unwrought aluminium and aluminium products": "未锻轧铝及铝材",
    "Medical or surgical instruments and apparatuses": "医疗仪器及器械",
    "Aquatic products": "水产品",
    "Ceramic products": "陶瓷产品",
}

MAJOR_IMPORT_ZH = {
    **MAJOR_EXPORT_ZH,
    "Crude oil": "原油",
    "Iron ore and concentrates": "铁矿砂及其精矿",
    "Natural gas": "天然气",
    "Soybeans": "大豆",
    "Copper ore and concentrates": "铜矿砂及其精矿",
    "Coal and lignite": "煤及褐煤",
    "Refined petroleum products": "成品油",
    "Unwrought copper and copper products": "未锻轧铜及铜材",
    "Semiconductor manufacturing equipment": "半导体制造设备",
    "Diodes and similar semiconductor devices": "二极管及类似半导体器件",
    "Aircraft": "航空器",
    "Passenger cars": "小客车",
    "Pharmaceutical products": "医药品",
    "Medical instruments and apparatus": "医疗仪器及器械",
    "Measuring or checking instruments and apparatuses": "计量或检测仪器及器具",
    "Liquid crystal display panels": "液晶显示面板",
}


async def fetch_customs_trade_snapshot() -> dict[str, Any]:
    global _SNAPSHOT_CACHE
    now = datetime.now(timezone.utc)
    if _SNAPSHOT_CACHE and (now - _SNAPSHOT_CACHE[0]).total_seconds() < SOURCE_CACHE_TTL_SECONDS:
        return copy.deepcopy(_SNAPSHOT_CACHE[1])

    warnings: list[str] = []
    async with httpx.AsyncClient(
        timeout=16,
        follow_redirects=True,
        headers=REQUEST_HEADERS,
    ) as client:
        preliminary_response, detailed_response = await asyncio.gather(
            client.get(PRELIMINARY_URL),
            client.get(DETAILED_URL),
            return_exceptions=True,
        )
        try:
            if isinstance(preliminary_response, Exception):
                raise preliminary_response
            preliminary_response.raise_for_status()
            preliminary_links = _extract_links(preliminary_response.text, PRELIMINARY_URL)
        except Exception as exc:
            warnings.append(f"海关英文站总量/重点商品快报读取失败，相关数据暂缺（{safe_error(exc)}）")
            preliminary_links = {}
        try:
            if isinstance(detailed_response, Exception):
                raise detailed_response
            detailed_response.raise_for_status()
            detailed_links = _extract_links(detailed_response.text, DETAILED_URL)
        except Exception as exc:
            warnings.append(f"海关英文站明细页（分国别/HS章节）读取失败，相关数据暂缺（{safe_error(exc)}）")
            detailed_links = {}
        monthly_report_links: dict[str, dict[str, str]] = {}
        try:
            monthly_index_response = await client.get(MONTHLY_REPORT_URL)
            monthly_index_response.raise_for_status()
            report_year = _extract_monthly_report_year(monthly_index_response.text)
            monthly_report_links = _merge_report_links(
                monthly_report_links,
                _extract_monthly_report_links(
                    monthly_index_response.text,
                    MONTHLY_REPORT_URL,
                    report_year,
                ),
            )
            previous_year = report_year - 1 if report_year else None
            if previous_year:
                try:
                    previous_index_url = _monthly_report_index_url(previous_year, report_year)
                    previous_index_response = await client.get(previous_index_url)
                    previous_index_response.raise_for_status()
                    monthly_report_links = _merge_report_links(
                        monthly_report_links,
                        _extract_monthly_report_links(
                            previous_index_response.text,
                            previous_index_url,
                            previous_year,
                        ),
                    )
                except Exception:
                    warnings.append("海关英文站上一年度月报索引读取失败，最近12个月曲线可能不完整")
        except Exception:
            warnings.append("海关英文站月报索引读取失败，环比数据暂缺")
        try:
            preliminary_index_response = await client.get(PRELIMINARY_REPORT_URL)
            preliminary_index_response.raise_for_status()
            preliminary_year = _extract_monthly_report_year(preliminary_index_response.text)
            monthly_report_links = _merge_report_links(
                monthly_report_links,
                _extract_monthly_report_links(
                    preliminary_index_response.text,
                    PRELIMINARY_REPORT_URL,
                    preliminary_year,
                ),
            )
            previous_preliminary_year = preliminary_year - 1 if preliminary_year else None
            if previous_preliminary_year:
                try:
                    previous_preliminary_url = _preliminary_report_index_url(
                        previous_preliminary_year,
                        preliminary_year,
                    )
                    previous_preliminary_response = await client.get(previous_preliminary_url)
                    previous_preliminary_response.raise_for_status()
                    monthly_report_links = _merge_report_links(
                        monthly_report_links,
                        _extract_monthly_report_links(
                            previous_preliminary_response.text,
                            previous_preliminary_url,
                            previous_preliminary_year,
                        ),
                    )
                except Exception:
                    warnings.append("海关英文站上一年度快报索引读取失败，重点商品美元曲线可能不完整")
        except Exception:
            warnings.append("海关英文站快报索引读取失败，重点商品美元曲线可能不完整")
        report_latest_month = _latest_report_month(monthly_report_links)
        report_previous_month = _previous_report_month(report_latest_month)
        history_months = _recent_report_months(monthly_report_links, report_latest_month, HISTORY_MONTH_COUNT)

        total_url = _find_link(
            preliminary_links,
            lambda title: "Total Export" in title and "Import Values" in title and "in USD" in title,
        )
        major_exports_url = _find_link(
            preliminary_links,
            lambda title: "Major Exports by Quantity and Value" in title and "in USD" in title,
        ) or _monthly_report_url(monthly_report_links, "major_exports", report_latest_month or "")
        major_imports_url = _monthly_report_url(monthly_report_links, "major_imports", report_latest_month or "")
        monthly_url = _find_link(
            detailed_links,
            lambda title: "Summary of Imports and Exports" in title and "Monthly" in title,
        )
        partners_url = _find_link(
            detailed_links,
            lambda title: "Imports and Exports by Country" in title,
        )
        hs_url = _find_link(
            detailed_links,
            lambda title: "Imports and Exports by HS Section and Division" in title,
        )

        page_specs: list[tuple[str, Optional[str]]] = [
            ("total", total_url),
            ("monthly", monthly_url),
            ("partners", partners_url),
            ("hs", hs_url),
            ("major_exports", major_exports_url),
            ("major_imports", major_imports_url),
        ]
        if report_previous_month:
            page_specs.extend(
                [
                    ("monthly_previous", _monthly_report_url(monthly_report_links, "monthly", report_previous_month)),
                    ("partners_previous", _monthly_report_url(monthly_report_links, "partners", report_previous_month)),
                    ("hs_previous", _monthly_report_url(monthly_report_links, "hs", report_previous_month)),
                    (
                        "major_exports_previous",
                        _monthly_report_url(monthly_report_links, "major_exports", report_previous_month),
                    ),
                    (
                        "major_imports_previous",
                        _monthly_report_url(monthly_report_links, "major_imports", report_previous_month),
                    ),
                ]
            )
        for month in history_months:
            page_specs.extend(
                [
                    (f"hs_history_{month}", _monthly_report_url(monthly_report_links, "hs", month)),
                    (
                        f"major_exports_history_{month}",
                        _monthly_report_url(monthly_report_links, "major_exports", month),
                    ),
                    (
                        f"major_imports_history_{month}",
                        _monthly_report_url(monthly_report_links, "major_imports", month),
                    ),
                ]
            )
        missing = [key for key, url in page_specs if not url]
        if missing:
            warnings.append(f"海关英文站列表缺少表格链接：{', '.join(missing)}")

        fetched_pages = await asyncio.gather(
            *[
                _fetch_optional_page(client, key, url)
                for key, url in page_specs
                if url
            ]
        )

    pages = {key: page for key, page in fetched_pages if page}
    if failed := [key for key, page in fetched_pages if page is None]:
        warnings.append(f"部分海关表格读取失败：{', '.join(failed)}")

    total_table = _parse_total_table(pages.get("total"))
    monthly_trend = _enrich_monthly_mom(_parse_monthly_summary(pages.get("monthly")))
    partners = _enrich_partner_mom(
        _parse_partner_table(pages.get("partners")),
        _parse_partner_table(pages.get("partners_previous")),
    )
    hs_sections = _enrich_hs_mom(
        _parse_hs_table(pages.get("hs")),
        _parse_hs_table(pages.get("hs_previous")),
    )
    major_exports = _enrich_major_commodity_mom(
        _parse_major_commodities_table(pages.get("major_exports"), "export"),
        _parse_major_commodities_table(pages.get("major_exports_previous"), "export"),
    )
    major_imports = _enrich_major_commodity_mom(
        _parse_major_commodities_table(pages.get("major_imports"), "import"),
        _parse_major_commodities_table(pages.get("major_imports_previous"), "import"),
    )
    hs_trends = _build_hs_trends(pages, history_months)
    commodity_trends = _build_major_commodity_trends(pages, history_months)

    latest_month = _latest_month(monthly_trend) or _month_from_title(total_table.get("title", ""))
    if latest_month and total_table.get("rows"):
        latest_month = _month_from_title(total_table.get("title", "")) or latest_month

    response = {
        "generated_at": now.isoformat(),
        "source_status": "live" if not warnings else "partial",
        "observed_month": latest_month,
        "month_label": _month_label(latest_month) if latest_month else "",
        "currency": "USD",
        "unit": "USD million",
        "total": total_table,
        "monthly_trend": monthly_trend[-18:],
        "partners": _top_rows(partners, "ytd_total_usd_mn", 32),
        "hs_sections": _top_rows([row for row in hs_sections if row.get("is_section")], "ytd_trade_usd_mn", 24),
        "hs_chapters": _top_rows([row for row in hs_sections if not row.get("is_section")], "ytd_trade_usd_mn", 120),
        "major_exports": _top_rows(major_exports, "ytd_value_usd_mn", 28),
        "major_imports": _top_rows(major_imports, "ytd_value_usd_mn", 40),
        "hs_trends": hs_trends,
        "commodity_trends": commodity_trends,
        "history_months": history_months,
        "sources": [
            {
                "name": "GACC Preliminary Release",
                "url": PRELIMINARY_URL,
                "type": "official_preliminary",
                "note": "海关总署英文站月度快报，提供最新总量、国别/地区及主要出口商品快报。",
            },
            {
                "name": "GACC China Customs Statistics",
                "url": DETAILED_URL,
                "type": "official_monthly_statistics",
                "note": "海关总署英文站正式统计表，提供月度历史、贸易伙伴、HS 类章等结构化表格。",
            },
            {
                "name": "GACC Monthly Bulletin Index",
                "url": MONTHLY_REPORT_URL,
                "type": "official_monthly_archive",
                "note": "海关总署英文站月报索引，用于定位上一月及最近12个月同表数据。",
            },
        ],
        "warnings": warnings,
    }
    _SNAPSHOT_CACHE = (now, copy.deepcopy(response))
    return response


def build_customs_trade_analysis_text(
    snapshot: dict[str, Any],
    focus: Optional[str] = None,
    *,
    selected_tab: Optional[str] = None,
    focus_key: Optional[str] = None,
) -> str:
    """Condense the customs snapshot into a compact model-readable Chinese briefing."""
    total_items = (snapshot.get("total") or {}).get("items") or {}
    total = total_items.get("total") or {}
    export = total_items.get("export") or {}
    import_row = total_items.get("import") or {}
    balance = total_items.get("balance") or {}

    lines = [
        "中国海关进出口 AI 快速事实包。请基于事实判断外贸动能、产业链传导、代表研究样本和反证风险。",
        "",
        f"数据月份：{snapshot.get('month_label') or snapshot.get('observed_month') or '未知'}",
        f"统计单位：{snapshot.get('unit') or 'USD million'}",
        f"当前前端关注：{focus or '全局海关进出口快照'}",
        f"当前视图：{selected_tab or '未指定'}",
        "",
        "投资映射候选池：",
        "- 电力设备出海：思源电气(002028)、特变电工(600089)、金盘科技(688676)、中国西电(601179)。",
        "- AI硬件/PCB/光模块：工业富联(601138)、沪电股份(002463)、中际旭创(300308)、新易盛(300502)。",
        "- 电子零部件：立讯精密(002475)、歌尔股份(002241)、鹏鼎控股(002938)。",
        "- 传统出口观察：申洲国际(02313.HK)、华利集团(300979)、顾家家居(603816)。",
        "",
        "一、总量指标",
        _analysis_total_line("当月进出口", total.get("current_usd_mn"), total.get("mom_pct"), total.get("yoy_current_pct")),
        _analysis_total_line("当月出口", export.get("current_usd_mn"), export.get("mom_pct"), export.get("yoy_current_pct")),
        _analysis_total_line("当月进口", import_row.get("current_usd_mn"), import_row.get("mom_pct"), import_row.get("yoy_current_pct")),
        _analysis_total_line("当月差额", balance.get("current_usd_mn"), balance.get("mom_pct"), balance.get("yoy_current_pct"), signed=True),
        _analysis_total_line("累计出口", export.get("ytd_usd_mn"), None, export.get("yoy_ytd_pct")),
        _analysis_total_line("累计进口", import_row.get("ytd_usd_mn"), None, import_row.get("yoy_ytd_pct")),
    ]

    monthly_rows = snapshot.get("monthly_trend") or []
    if monthly_rows:
        recent_months = monthly_rows[-6:]
        first = recent_months[0]
        latest = recent_months[-1]
        lines.extend(
            [
                "",
                "二、最近6个月总量趋势（用于AI快速判断）",
                (
                    f"{first.get('month')} 至 {latest.get('month')}："
                    f"出口 {fmt_usd(first.get('export_usd_mn'))} -> {fmt_usd(latest.get('export_usd_mn'))}，"
                    f"进口 {fmt_usd(first.get('import_usd_mn'))} -> {fmt_usd(latest.get('import_usd_mn'))}，"
                    f"差额 {fmt_usd(first.get('balance_usd_mn'), signed=True)} -> {fmt_usd(latest.get('balance_usd_mn'), signed=True)}。"
                ),
                "近6个月当月进出口明细：",
                *_analysis_monthly_lines(recent_months),
            ]
        )

    lines.extend(
        [
            "",
            "三、当前选中项近6个月曲线",
            *_analysis_focus_trend_lines(snapshot, selected_tab=selected_tab, focus_key=focus_key)[-6:],
            "",
            "四、HS2商品章结构 Top 6（按累计贸易额排序）",
            *_analysis_hs_lines(snapshot.get("hs_chapters") or [], limit=6),
            "",
            "五、HS2头部商品动能摘要 Top 4",
            *_analysis_hs_trend_summary(snapshot, snapshot.get("hs_chapters") or [], limit=4),
            "",
            "六、重点出口商品 Top 6（按累计金额排序）",
            *_analysis_commodity_lines(snapshot.get("major_exports") or [], limit=6),
            "",
            "七、重点出口商品动能摘要 Top 4",
            *_analysis_commodity_trend_summary(snapshot, snapshot.get("major_exports") or [], limit=4),
            "",
            "八、重点进口商品 Top 6（按累计金额排序）",
            *_analysis_commodity_lines(snapshot.get("major_imports") or [], limit=6),
            "",
            "九、重点进口商品动能摘要 Top 4",
            *_analysis_commodity_trend_summary(snapshot, snapshot.get("major_imports") or [], limit=4),
            "",
            "十、主要贸易伙伴 Top 6（按累计总额排序）",
            *_analysis_partner_lines(snapshot.get("partners") or [], limit=6),
        ]
    )

    source_lines = [
        f"- {source.get('name')}: {source.get('url')}"
        for source in (snapshot.get("sources") or [])
        if source.get("url")
    ]
    if source_lines:
        lines.extend(["", "十一、数据来源", *source_lines])
    if snapshot.get("warnings"):
        lines.extend(["", "十二、数据源提示", *(f"- {warning}" for warning in snapshot.get("warnings") or [])])

    return "\n".join(line for line in lines if line is not None)


def build_customs_trade_ai_snapshot(
    snapshot: dict[str, Any],
    focus: Optional[str] = None,
    *,
    selected_tab: Optional[str] = None,
    focus_key: Optional[str] = None,
) -> str:
    """Build a tiny JSON fact pack for button-level cloud AI analysis."""
    total_items = (snapshot.get("total") or {}).get("items") or {}
    monthly_rows = snapshot.get("monthly_trend") or []
    partners = [row for row in (snapshot.get("partners") or []) if isinstance(row, dict) and not row.get("is_region_header")]
    payload = {
        "m": snapshot.get("month_label") or snapshot.get("observed_month"),
        "unit": "USD mn",
        "focus": focus or "全局海关进出口快照",
        "tab": selected_tab,
        "key": focus_key,
        "sum": {
            "trade": _ai_total_item(total_items.get("total")),
            "export": _ai_total_item(total_items.get("export")),
            "import": _ai_total_item(total_items.get("import")),
            "balance": _ai_total_item(total_items.get("balance")),
        },
        "stocks": {
            "电力设备": ["思源电气(002028)", "特变电工(600089)", "金盘科技(688676)"],
            "AI硬件": ["工业富联(601138)", "沪电股份(002463)", "中际旭创(300308)"],
            "电子零部件": ["立讯精密(002475)", "歌尔股份(002241)", "鹏鼎控股(002938)"],
            "传统出口": ["申洲国际(02313.HK)", "华利集团(300979)", "顾家家居(603816)"],
        },
        "hs": [_ai_hs_item(row) for row in (snapshot.get("hs_chapters") or [])[:3] if isinstance(row, dict)],
        "exports": [_ai_commodity_item(row) for row in (snapshot.get("major_exports") or [])[:3] if isinstance(row, dict)],
        "imports": [_ai_commodity_item(row) for row in (snapshot.get("major_imports") or [])[:2] if isinstance(row, dict)],
        "partners": [_ai_partner_item(row) for row in partners[:3]],
        "months": [
            {
                "m": row.get("month"),
                "ex": row.get("export_usd_mn"),
                "im": row.get("import_usd_mn"),
                "bal": row.get("balance_usd_mn"),
                "ex_mom": row.get("export_mom_pct"),
                "im_mom": row.get("import_mom_pct"),
            }
            for row in monthly_rows[-3:]
            if isinstance(row, dict)
        ],
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _ai_total_item(row: Optional[dict[str, Any]]) -> dict[str, Any]:
    row = row or {}
    return {
        "cur": row.get("current_usd_mn"),
        "ytd": row.get("ytd_usd_mn"),
        "mom": row.get("mom_pct"),
        "yoy": row.get("yoy_current_pct"),
        "ytd_yoy": row.get("yoy_ytd_pct"),
    }


def _ai_hs_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "code": row.get("code"),
        "name": row.get("description_zh") or row.get("name_zh") or row.get("description") or row.get("name"),
        "ytd": row.get("ytd_trade_usd_mn"),
        "ex_yoy": row.get("yoy_export_pct"),
        "im_yoy": row.get("yoy_import_pct"),
    }


def _ai_commodity_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row.get("commodity_zh") or row.get("commodity"),
        "ytd": row.get("ytd_value_usd_mn"),
        "yoy": row.get("value_yoy_pct"),
        "mom": row.get("value_mom_pct"),
        "qty_mom": row.get("quantity_mom_pct"),
    }


def _ai_partner_item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": row.get("name_zh") or row.get("name"),
        "ytd": row.get("ytd_total_usd_mn"),
        "yoy": row.get("yoy_total_pct"),
        "ex_yoy": row.get("yoy_export_pct"),
        "im_yoy": row.get("yoy_import_pct"),
    }


async def _fetch_optional_page(
    client: httpx.AsyncClient,
    key: str,
    url: Optional[str],
) -> tuple[str, Optional[dict[str, Any]]]:
    if not url:
        return key, None
    try:
        response = await client.get(url)
        response.raise_for_status()
        return key, {
            "url": url,
            "title": _page_title(response.text),
            "rows": _extract_table_rows(response.text),
            "download_url": _extract_download_url(response.text, url),
        }
    except Exception:
        return key, None


def _extract_links(html: str, base_url: str) -> list[dict[str, str]]:
    links: list[dict[str, str]] = []
    for href, title in re.findall(r'<a\s+[^>]*href="([^"]+)"[^>]*>(.*?)</a>', html, re.I | re.S):
        clean_title = _clean_cell(title)
        if not clean_title:
            continue
        links.append({"title": clean_title, "url": urljoin(base_url, href.replace("\\", "/"))})
    return links


def _find_link(links: list[dict[str, str]], predicate: Any) -> Optional[str]:
    for link in links:
        if predicate(link["title"]):
            return link["url"]
    return None


def _extract_monthly_report_links(
    html: str,
    base_url: str,
    year: Optional[int],
) -> dict[str, dict[str, str]]:
    categories: dict[str, dict[str, str]] = {
        "monthly": {},
        "partners": {},
        "hs": {},
        "major_exports": {},
        "major_imports": {},
    }
    for row_html in re.findall(r"<tr\b.*?</tr>", html, re.I | re.S):
        cells = re.findall(r"<td\b[^>]*>(.*?)</td>", row_html, re.I | re.S)
        if len(cells) < 2:
            continue
        title = _clean_cell(cells[0])
        category = _monthly_report_category(title)
        if not category:
            continue
        for href, month_label in re.findall(
            r'<a\s+[^>]*href\s*=\s*["\']?([^"\'\s>]+)["\']?[^>]*>(.*?)</a>',
            cells[1],
            re.I | re.S,
        ):
            month = _month_number_from_label(_clean_cell(month_label))
            if month:
                key = f"{year}-{month}" if year else month
                categories[category][key] = urljoin(base_url, href.replace("\\", "/"))
    return categories


def _merge_report_links(
    base: dict[str, dict[str, str]],
    extra: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    merged = {category: dict(links) for category, links in base.items()}
    for category, links in extra.items():
        merged.setdefault(category, {}).update(links)
    return merged


def _extract_monthly_report_year(html: str) -> Optional[int]:
    match = re.search(r'<option\s+value=["\']?(20\d{2})["\']?', html, re.I)
    return int(match.group(1)) if match else None


def _monthly_report_index_url(year: int, current_year: Optional[int]) -> str:
    if current_year and year == current_year:
        return MONTHLY_REPORT_URL
    return f"http://english.customs.gov.cn/statics/report/monthly{year}.html"


def _preliminary_report_index_url(year: int, current_year: Optional[int]) -> str:
    if current_year and year == current_year:
        return PRELIMINARY_REPORT_URL
    return f"http://english.customs.gov.cn/statics/report/preliminary{year}.html"


def _monthly_report_category(title: str) -> Optional[str]:
    if "in CNY" in title or "in RMB" in title:
        return None
    if "Summary of Imports and Exports" in title and "Monthly" in title:
        return "monthly"
    if "Imports and Exports by Country" in title:
        return "partners"
    if "Imports and Exports by HS Section and Division" in title:
        return "hs"
    if (
        ("Major Export Commodities" in title or "Major Exports" in title)
        and "Quantity and Value" in title
    ):
        return "major_exports"
    if (
        ("Major Import Commodities" in title or "Major Imports" in title)
        and "Quantity and Value" in title
    ):
        return "major_imports"
    return None


def _latest_report_month(report_links: dict[str, dict[str, str]]) -> Optional[str]:
    months = {
        month
        for category_links in report_links.values()
        for month, url in category_links.items()
        if url
    }
    return sorted(months)[-1] if months else None


def _previous_report_month(month: Optional[str]) -> Optional[str]:
    if not month:
        return None
    if re.match(r"^20\d{2}-\d{2}$", month):
        year, month_value = (int(part) for part in month.split("-", 1))
        if month_value <= 1:
            return f"{year - 1}-12"
        return f"{year}-{month_value - 1:02d}"
    try:
        month_value = int(month)
    except ValueError:
        return None
    if month_value <= 1:
        return None
    return f"{month_value - 1:02d}"


def _recent_report_months(
    report_links: dict[str, dict[str, str]],
    latest_month: Optional[str],
    count: int,
) -> list[str]:
    months = {
        month
        for category in ("hs", "major_exports", "major_imports")
        for month, url in report_links.get(category, {}).items()
        if url and re.match(r"^20\d{2}-\d{2}$", month)
    }
    if latest_month:
        months = {month for month in months if month <= latest_month}
    return sorted(months)[-count:]


def _monthly_report_url(
    report_links: dict[str, dict[str, str]],
    category: str,
    month: str,
) -> Optional[str]:
    return report_links.get(category, {}).get(month)


def _month_number_from_label(label: str) -> Optional[str]:
    return MONTHS.get(label.strip().lower()[:3])


def _page_title(html: str) -> str:
    match = re.search(r"<title>(.*?)</title>", html, re.I | re.S)
    return _clean_cell(match.group(1)) if match else ""


def _extract_download_url(html: str, base_url: str) -> Optional[str]:
    for href in re.findall(r'href\s*=\s*[\'"]([^\'"]+\.(?:xls|xlsx|csv)[^\'"]*)[\'"]', html, re.I):
        return urljoin(base_url, href.replace("\\", "/"))
    return None


def _extract_table_rows(html: str) -> list[list[str]]:
    table_match = re.search(r"<table\b.*?</table>", html, re.I | re.S)
    if not table_match:
        return []
    rows: list[list[str]] = []
    for row_html in re.findall(r"<tr\b.*?</tr>", table_match.group(0), re.I | re.S):
        cells = re.findall(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", row_html, re.I | re.S)
        row = [_clean_cell(cell) for cell in cells]
        if any(row):
            rows.append(row)
    return rows


def _clean_cell(value: str) -> str:
    text = re.sub(r"<br\s*/?>", " ", value, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = unescape(text).replace("\xa0", " ").replace("\u3000", " ")
    return re.sub(r"\s+", " ", text).strip()


def _parse_total_table(page: Optional[dict[str, Any]]) -> dict[str, Any]:
    if not page:
        return {"title": "", "source_url": "", "download_url": None, "rows": [], "items": {}}

    rows = page.get("rows") or []
    scale = _unit_scale(rows, default=1.0)
    parsed_rows: list[dict[str, Any]] = []
    items: dict[str, dict[str, Any]] = {}
    for row in rows:
        if len(row) < 6:
            continue
        item = row[0]
        key = _total_item_key(item)
        if not key:
            continue
        record = {
            "key": key,
            "item": item,
            "current_usd_mn": num(row[1], scale),
            "ytd_usd_mn": num(row[2], scale),
            "mom_pct": num(row[3]),
            "yoy_current_pct": num(row[4]),
            "yoy_ytd_pct": num(row[5]),
        }
        parsed_rows.append(record)
        items[key] = record

    return {
        "title": page.get("title") or _first_title_row(rows),
        "source_url": page.get("url") or "",
        "download_url": page.get("download_url"),
        "rows": parsed_rows,
        "items": items,
    }


def _parse_monthly_summary(page: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not page:
        return []
    scale = _unit_scale(page.get("rows") or [], default=1.0)
    parsed: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        if len(row) < 9 or not re.match(r"^20\d{2}\.\d{2}$", row[0]):
            continue
        month = row[0].replace(".", "-")
        parsed.append(
            {
                "month": month,
                "total_usd_mn": num(row[1], scale),
                "export_usd_mn": num(row[2], scale),
                "import_usd_mn": num(row[3], scale),
                "balance_usd_mn": num(row[4], scale),
                "ytd_total_usd_mn": num(row[5], scale),
                "ytd_export_usd_mn": num(row[6], scale),
                "ytd_import_usd_mn": num(row[7], scale),
                "ytd_balance_usd_mn": num(row[8], scale),
            }
        )
    return sorted(parsed, key=lambda row: row["month"])


def _parse_partner_table(page: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not page:
        return []
    scale = _unit_scale(page.get("rows") or [], default=0.001)
    parsed: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        if len(row) < 10 or row[0].lower().startswith("country"):
            continue
        name = row[0]
        display_name = name.rstrip(":")
        if name.upper() == "TOTAL" or not num(row[1]):
            continue
        ytd_export = num(row[4], scale)
        ytd_import = num(row[6], scale)
        parsed.append(
            {
                "name": display_name,
                "name_zh": PARTNER_ZH.get(display_name),
                "is_region_header": name.endswith(":"),
                "current_total_usd_mn": num(row[1], scale),
                "ytd_total_usd_mn": num(row[2], scale),
                "current_export_usd_mn": num(row[3], scale),
                "ytd_export_usd_mn": ytd_export,
                "current_import_usd_mn": num(row[5], scale),
                "ytd_import_usd_mn": ytd_import,
                "ytd_balance_usd_mn": _subtract(ytd_export, ytd_import),
                "yoy_total_pct": num(row[7]),
                "yoy_export_pct": num(row[8]),
                "yoy_import_pct": num(row[9]),
            }
        )
    return parsed


def _parse_hs_table(page: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    if not page:
        return []
    scale = _unit_scale(page.get("rows") or [], default=0.001)
    parsed: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        if len(row) < 5 or row[0].lower().startswith("hs section"):
            continue
        name = row[0]
        if name.upper() == "TOTAL" or name.strip().isdigit() or not num(row[1]):
            continue
        hs_code, description = _split_hs_name(name)
        section_key = _section_key(name)
        name_zh = HS_CHAPTER_ZH.get(hs_code or "") or HS_SECTION_ZH.get(section_key or "")
        if len(row) >= 7:
            current_export = num(row[1], scale)
            ytd_export = num(row[2], scale)
            current_import = num(row[3], scale)
            ytd_import = num(row[4], scale)
            yoy_export = num(row[5])
            yoy_import = num(row[6])
        else:
            current_export = num(row[1], scale)
            ytd_export = current_export
            current_import = num(row[2], scale)
            ytd_import = current_import
            yoy_export = num(row[3])
            yoy_import = num(row[4])
        record = {
            "name": name,
            "name_zh": name_zh,
            "code": hs_code,
            "description": description,
            "description_zh": HS_CHAPTER_ZH.get(hs_code or ""),
            "is_section": _looks_hs_section(name),
            "current_export_usd_mn": current_export,
            "ytd_export_usd_mn": ytd_export,
            "current_import_usd_mn": current_import,
            "ytd_import_usd_mn": ytd_import,
            "current_trade_usd_mn": _sum_optional(current_export, current_import),
            "current_balance_usd_mn": _subtract(current_export, current_import),
            "ytd_trade_usd_mn": _sum_optional(ytd_export, ytd_import),
            "ytd_balance_usd_mn": _subtract(ytd_export, ytd_import),
            "yoy_export_pct": yoy_export,
            "yoy_import_pct": yoy_import,
        }
        record["trend_key"] = _hs_key(record)
        parsed.append(record)
    return parsed


def _parse_major_commodities_table(
    page: Optional[dict[str, Any]],
    direction: str,
) -> list[dict[str, Any]]:
    if not page:
        return []
    if _table_currency(page.get("rows") or []) == "CNY":
        return []
    value_scale = _unit_scale(page.get("rows") or [], default=1.0)
    parsed: list[dict[str, Any]] = []
    for row in page.get("rows") or []:
        if len(row) < 6 or row[0].lower() in {"commodity", "quantity"}:
            continue
        commodity = row[0].replace("*", "").strip()
        current_quantity = num(row[2])
        current_value = num(row[3], value_scale)
        if len(row) >= 8:
            ytd_quantity = num(row[4])
            ytd_value = num(row[5], value_scale)
            has_previous_ytd_columns = len(row) >= 10
            previous_ytd_quantity = num(row[6]) if has_previous_ytd_columns else None
            previous_ytd_value = num(row[7], value_scale) if has_previous_ytd_columns else None
            quantity_yoy = num(row[8]) if has_previous_ytd_columns else num(row[6])
            value_yoy = num(row[9]) if has_previous_ytd_columns else num(row[7])
        else:
            ytd_quantity = current_quantity
            ytd_value = current_value
            previous_ytd_quantity = None
            previous_ytd_value = None
            quantity_yoy = num(row[4])
            value_yoy = num(row[5])
        if not commodity or ytd_value is None:
            continue
        zh_map = MAJOR_IMPORT_ZH if direction == "import" else MAJOR_EXPORT_ZH
        record = {
            "direction": direction,
            "commodity": commodity,
            "commodity_zh": zh_map.get(commodity) or MAJOR_EXPORT_ZH.get(commodity) or MAJOR_IMPORT_ZH.get(commodity),
            "quantity_unit": row[1],
            "current_quantity": current_quantity,
            "current_value_usd_mn": current_value,
            "ytd_quantity": ytd_quantity,
            "ytd_value_usd_mn": ytd_value,
            "previous_ytd_quantity": previous_ytd_quantity,
            "previous_ytd_value_usd_mn": previous_ytd_value,
            "quantity_yoy_pct": quantity_yoy,
            "value_yoy_pct": value_yoy,
        }
        record["trend_key"] = _commodity_key(record)
        parsed.append(
            record
        )
    return parsed


def _parse_major_exports_table(page: Optional[dict[str, Any]]) -> list[dict[str, Any]]:
    return _parse_major_commodities_table(page, "export")


def _unit_scale(rows: list[list[str]], *, default: float) -> float:
    unit_text = " ".join(" ".join(row) for row in rows[:4]).lower()
    if "100 million" in unit_text:
        return 100.0
    if "us$1,000" in unit_text or "usd1,000" in unit_text or "usd 1,000" in unit_text:
        return 0.001
    if "us$ million" in unit_text or "usd1 million" in unit_text or "usd 1 million" in unit_text:
        return 1.0
    return default


def _table_currency(rows: list[list[str]]) -> str:
    unit_text = " ".join(" ".join(row) for row in rows[:4]).lower()
    if "rmb" in unit_text or "cny" in unit_text or "￥" in unit_text:
        return "CNY"
    if "us$" in unit_text or "usd" in unit_text:
        return "USD"
    return ""
def _subtract(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None or right is None:
        return None
    return round(left - right, 3)


def _sum_optional(left: Optional[float], right: Optional[float]) -> Optional[float]:
    if left is None and right is None:
        return None
    return round((left or 0) + (right or 0), 3)


def _enrich_monthly_mom(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(rows, key=lambda row: row["month"])
    previous: Optional[dict[str, Any]] = None
    for row in ordered:
        row["total_mom_pct"] = pct_change(
            row.get("total_usd_mn"),
            previous.get("total_usd_mn") if previous else None,
        )
        row["export_mom_pct"] = pct_change(
            row.get("export_usd_mn"),
            previous.get("export_usd_mn") if previous else None,
        )
        row["import_mom_pct"] = pct_change(
            row.get("import_usd_mn"),
            previous.get("import_usd_mn") if previous else None,
        )
        row["balance_mom_pct"] = pct_change(
            row.get("balance_usd_mn"),
            previous.get("balance_usd_mn") if previous else None,
        )
        previous = row
    return ordered


def _enrich_partner_mom(
    rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_key = {_partner_key(row): row for row in previous_rows if _partner_key(row)}
    for row in rows:
        previous = previous_by_key.get(_partner_key(row))
        row["mom_total_pct"] = pct_change(
            row.get("current_total_usd_mn"),
            previous.get("current_total_usd_mn") if previous else None,
        )
        row["mom_export_pct"] = pct_change(
            row.get("current_export_usd_mn"),
            previous.get("current_export_usd_mn") if previous else None,
        )
        row["mom_import_pct"] = pct_change(
            row.get("current_import_usd_mn"),
            previous.get("current_import_usd_mn") if previous else None,
        )
    return rows


def _enrich_hs_mom(
    rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_key = {_hs_key(row): row for row in previous_rows if _hs_key(row)}
    for row in rows:
        previous = previous_by_key.get(_hs_key(row))
        row["mom_trade_pct"] = pct_change(
            row.get("current_trade_usd_mn"),
            previous.get("current_trade_usd_mn") if previous else None,
        )
        row["mom_export_pct"] = pct_change(
            row.get("current_export_usd_mn"),
            previous.get("current_export_usd_mn") if previous else None,
        )
        row["mom_import_pct"] = pct_change(
            row.get("current_import_usd_mn"),
            previous.get("current_import_usd_mn") if previous else None,
        )
    return rows


def _enrich_major_commodity_mom(
    rows: list[dict[str, Any]],
    previous_rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    previous_by_key = {_commodity_key(row): row for row in previous_rows if _commodity_key(row)}
    for row in rows:
        previous = previous_by_key.get(_commodity_key(row))
        row["quantity_mom_pct"] = pct_change(
            row.get("current_quantity"),
            previous.get("current_quantity") if previous else None,
        )
        row["value_mom_pct"] = pct_change(
            row.get("current_value_usd_mn"),
            previous.get("current_value_usd_mn") if previous else None,
        )
    return rows


def _build_hs_trends(
    pages: dict[str, dict[str, Any]],
    months: list[str],
) -> dict[str, list[dict[str, Any]]]:
    trends: dict[str, list[dict[str, Any]]] = {}
    for month in months:
        rows = _parse_hs_table(pages.get(f"hs_history_{month}"))
        for row in rows:
            key = row.get("trend_key") or _hs_key(row)
            if not key:
                continue
            trends.setdefault(key, []).append(
                {
                    "month": month,
                    "trade_usd_mn": row.get("current_trade_usd_mn"),
                    "export_usd_mn": row.get("current_export_usd_mn"),
                    "import_usd_mn": row.get("current_import_usd_mn"),
                    "balance_usd_mn": row.get("current_balance_usd_mn"),
                }
            )
    return {key: sorted(points, key=lambda point: point["month"]) for key, points in trends.items()}


def _build_major_commodity_trends(
    pages: dict[str, dict[str, Any]],
    months: list[str],
) -> dict[str, list[dict[str, Any]]]:
    trends: dict[str, list[dict[str, Any]]] = {}
    for month in months:
        for direction in ("export", "import"):
            page_key = f"major_{direction}s_history_{month}"
            rows = _parse_major_commodities_table(pages.get(page_key), direction)
            for row in rows:
                key = row.get("trend_key") or _commodity_key(row)
                if not key:
                    continue
                trends.setdefault(key, []).append(
                    {
                        "month": month,
                        "direction": direction,
                        "value_usd_mn": row.get("current_value_usd_mn"),
                        "quantity": row.get("current_quantity"),
                    }
                )
    return {key: sorted(points, key=lambda point: point["month"]) for key, points in trends.items()}
def _partner_key(row: dict[str, Any]) -> str:
    return str(row.get("name") or "").rstrip(":").strip().lower()


def _hs_key(row: dict[str, Any]) -> str:
    code = row.get("code")
    if code:
        return f"chapter:{code}"
    return f"section:{_section_key(str(row.get('name') or '')) or row.get('name')}"


def _commodity_key(row: dict[str, Any]) -> str:
    direction = str(row.get("direction") or "export").strip().lower()
    commodity = _normalize_commodity_name(str(row.get("commodity") or ""))
    return f"{direction}:{commodity}" if commodity else ""


def _normalize_commodity_name(value: str) -> str:
    normalized = (
        value.replace("*", "")
        .replace("\u00a0", " ")
        .replace("（", "(")
        .replace("）", ")")
        .strip()
        .lower()
    )
    normalized = re.sub(r"\s+", " ", normalized)
    normalized = normalized.replace("( ", "(").replace(" )", ")")
    aliases = {
        "electronic integrated circuits": "integrated circuits",
        "automatic data processing machines and parts thereof": "automatic data processing equipment and parts thereof",
        "electric appliances of household type": "household appliances",
        "parts and accessories of vehicle": "automotive components and parts",
        "products, of steel or iron": "steel products",
        "general machines": "general machinery",
        "lamps and lighting fittings and parts thereof": "lamps and light fittings and parts thereof",
        "flat panel display modules of liquid crystals": "lcd panels",
        "suit-cases, hand bags and similar containers": "suitcases, handbags and similar containers",
        "motor vehicles(including chassis fitted with engines)": "motor vehicles (including chassis fitted with engines)",
    }
    return aliases.get(normalized, normalized)


def _total_item_key(item: str) -> Optional[str]:
    normalized = item.lower().replace("&", "and")
    if "export" in normalized and "import" in normalized and "balance" not in normalized:
        return "total"
    if normalized == "total export":
        return "export"
    if normalized == "total import":
        return "import"
    if "balance" in normalized:
        return "balance"
    return None


def _looks_hs_section(name: str) -> bool:
    first = _section_key(name) or ""
    if not first or first[:2].isdigit():
        return False
    return bool(re.match(r"^[IVXLCDM]+$", first, re.I) or re.match(r"^[\u2160-\u217F]+$", first))


def _section_key(name: str) -> Optional[str]:
    if not name:
        return None
    return name.split(" ", 1)[0].strip()


def _split_hs_name(name: str) -> tuple[Optional[str], str]:
    match = re.match(r"^(\d{2})\s+(.+)$", name.strip())
    if match:
        return match.group(1), match.group(2).strip()
    return None, name.strip()


def _top_rows(rows: list[dict[str, Any]], sort_key: str, limit: int) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: row.get(sort_key) if row.get(sort_key) is not None else -1,
        reverse=True,
    )[:limit]


def _analysis_hs_lines(rows: list[dict[str, Any]], limit: int) -> list[str]:
    lines: list[str] = []
    for row in rows[:limit]:
        name = row.get("description_zh") or row.get("name_zh") or row.get("description") or row.get("name")
        code = row.get("code") or "大类"
        lines.append(
            "- "
            f"{code} {name}："
            f"累计贸易 {fmt_usd(row.get('ytd_trade_usd_mn'))}，"
            f"累计出口 {fmt_usd(row.get('ytd_export_usd_mn'))}，"
            f"累计进口 {fmt_usd(row.get('ytd_import_usd_mn'))}，"
            f"差额 {fmt_usd(row.get('ytd_balance_usd_mn'), signed=True)}，"
            f"贸易环比 {fmt_pct(row.get('mom_trade_pct'))}，"
            f"出口同比 {fmt_pct(row.get('yoy_export_pct'))}，"
            f"进口同比 {fmt_pct(row.get('yoy_import_pct'))}。"
        )
    return lines or ["- 暂无HS结构数据。"]


def _analysis_commodity_lines(rows: list[dict[str, Any]], limit: int) -> list[str]:
    lines: list[str] = []
    for row in rows[:limit]:
        name = row.get("commodity_zh") or row.get("commodity")
        lines.append(
            "- "
            f"{name}："
            f"累计金额 {fmt_usd(row.get('ytd_value_usd_mn'))}，"
            f"当月金额 {fmt_usd(row.get('current_value_usd_mn'))}，"
            f"金额环比 {fmt_pct(row.get('value_mom_pct'))}，"
            f"金额同比 {fmt_pct(row.get('value_yoy_pct'))}，"
            f"数量环比 {fmt_pct(row.get('quantity_mom_pct'))}，"
            f"单位 {row.get('quantity_unit') or '-'}。"
        )
    return lines or ["- 暂无重点商品数据。"]


def _analysis_partner_lines(rows: list[dict[str, Any]], limit: int) -> list[str]:
    lines: list[str] = []
    partners = [row for row in rows if not row.get("is_region_header")][:limit]
    for row in partners:
        name = row.get("name_zh") or row.get("name")
        lines.append(
            "- "
            f"{name}："
            f"累计总额 {fmt_usd(row.get('ytd_total_usd_mn'))}，"
            f"累计出口 {fmt_usd(row.get('ytd_export_usd_mn'))}，"
            f"累计进口 {fmt_usd(row.get('ytd_import_usd_mn'))}，"
            f"差额 {fmt_usd(row.get('ytd_balance_usd_mn'), signed=True)}，"
            f"总额环比 {fmt_pct(row.get('mom_total_pct'))}，"
            f"总额同比 {fmt_pct(row.get('yoy_total_pct'))}。"
        )
    return lines or ["- 暂无伙伴结构数据。"]


def _analysis_monthly_lines(rows: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for row in rows:
        lines.append(
            "- "
            f"{row.get('month')}："
            f"进出口 {fmt_usd(row.get('total_usd_mn'))}"
            f"（环比 {fmt_pct(row.get('total_mom_pct'))}），"
            f"出口 {fmt_usd(row.get('export_usd_mn'))}"
            f"（环比 {fmt_pct(row.get('export_mom_pct'))}），"
            f"进口 {fmt_usd(row.get('import_usd_mn'))}"
            f"（环比 {fmt_pct(row.get('import_mom_pct'))}），"
            f"差额 {fmt_usd(row.get('balance_usd_mn'), signed=True)}。"
        )
    return lines or ["- 暂无近12个月总量数据。"]


def _analysis_focus_trend_lines(
    snapshot: dict[str, Any],
    *,
    selected_tab: Optional[str],
    focus_key: Optional[str],
) -> list[str]:
    if not focus_key:
        return ["- 未传入选中项 trend_key，本次按全局结构分析。"]
    if selected_tab == "exports":
        points = (snapshot.get("commodity_trends") or {}).get(focus_key) or []
        if not points:
            return [f"- 选中重点商品 {focus_key} 暂无近12个月曲线。"]
        return [
            "- "
            f"{point.get('month')}：金额 {fmt_usd(point.get('value_usd_mn'))}，"
            f"数量 {_fmt_quantity(point.get('quantity'))}。"
            for point in points[-12:]
        ]
    points = (snapshot.get("hs_trends") or {}).get(focus_key) or []
    if not points:
        return [f"- 选中HS项 {focus_key} 暂无近12个月曲线。"]
    return [
        "- "
        f"{point.get('month')}：贸易额 {fmt_usd(point.get('trade_usd_mn'))}，"
        f"出口 {fmt_usd(point.get('export_usd_mn'))}，"
        f"进口 {fmt_usd(point.get('import_usd_mn'))}，"
        f"差额 {fmt_usd(point.get('balance_usd_mn'), signed=True)}。"
        for point in points[-12:]
    ]


def _analysis_hs_trend_summary(
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
    limit: int,
) -> list[str]:
    trends = snapshot.get("hs_trends") or {}
    lines: list[str] = []
    for row in rows[:limit]:
        key = row.get("trend_key") or _hs_key(row)
        points = trends.get(key) or []
        if not points:
            continue
        name = row.get("description_zh") or row.get("name_zh") or row.get("description") or row.get("name")
        lines.append(
            "- "
            f"{row.get('code') or '大类'} {name}："
            f"贸易额{_trend_summary(points, 'trade_usd_mn')}；"
            f"出口{_trend_summary(points, 'export_usd_mn')}；"
            f"进口{_trend_summary(points, 'import_usd_mn')}。"
        )
    return lines or ["- 暂无HS近12个月动能摘要。"]


def _analysis_commodity_trend_summary(
    snapshot: dict[str, Any],
    rows: list[dict[str, Any]],
    limit: int,
) -> list[str]:
    trends = snapshot.get("commodity_trends") or {}
    lines: list[str] = []
    for row in rows[:limit]:
        key = row.get("trend_key") or _commodity_key(row)
        points = trends.get(key) or []
        if not points:
            continue
        name = row.get("commodity_zh") or row.get("commodity")
        lines.append(
            "- "
            f"{name}："
            f"金额{_trend_summary(points, 'value_usd_mn')}，"
            f"数量{_quantity_trend_summary(points)}。"
        )
    return lines or ["- 暂无重点商品近12个月动能摘要。"]


def _analysis_total_line(
    label: str,
    value: Optional[float],
    mom: Optional[float],
    yoy: Optional[float],
    *,
    signed: bool = False,
) -> str:
    pieces = [f"{label}：{fmt_usd(value, signed=signed)}"]
    if mom is not None:
        pieces.append(f"环比 {fmt_pct(mom)}")
    if yoy is not None:
        pieces.append(f"同比 {fmt_pct(yoy)}")
    return "，".join(pieces) + "。"


def _analysis_series(rows: list[dict[str, Any]], key: str) -> str:
    return "；".join(f"{row.get('month')} {fmt_usd(row.get(key))}" for row in rows)


def _trend_summary(points: list[dict[str, Any]], key: str) -> str:
    recent = sorted(points, key=lambda point: str(point.get("month") or ""))[-12:]
    values = [point.get(key) for point in recent if point.get(key) is not None]
    if not recent or not values:
        return "缺失"
    latest = _last_number(recent, key)
    previous = _previous_number(recent, key)
    last3 = _avg_tail(values, 3)
    prev3 = _avg_previous(values, 3, 3)
    return (
        f"最新 {fmt_usd(latest)}，"
        f"月环比 {fmt_pct(pct_change(latest, previous))}，"
        f"近3月均值较前3月 {fmt_pct(pct_change(last3, prev3))}，"
        f"12个月区间 {fmt_usd(min(values))} 至 {fmt_usd(max(values))}"
    )


def _quantity_trend_summary(points: list[dict[str, Any]]) -> str:
    recent = sorted(points, key=lambda point: str(point.get("month") or ""))[-12:]
    values = [point.get("quantity") for point in recent if point.get("quantity") is not None]
    if not values:
        return "缺失"
    latest = _last_number(recent, "quantity")
    previous = _previous_number(recent, "quantity")
    last3 = _avg_tail(values, 3)
    prev3 = _avg_previous(values, 3, 3)
    return (
        f"最新 {_fmt_quantity(latest)}，"
        f"月环比 {fmt_pct(pct_change(latest, previous))}，"
        f"近3月均值较前3月 {fmt_pct(pct_change(last3, prev3))}"
    )


def _last_number(points: list[dict[str, Any]], key: str) -> Optional[float]:
    for point in reversed(points):
        value = point.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _previous_number(points: list[dict[str, Any]], key: str) -> Optional[float]:
    found_latest = False
    for point in reversed(points):
        value = point.get(key)
        if not isinstance(value, (int, float)):
            continue
        if not found_latest:
            found_latest = True
            continue
        return float(value)
    return None


def _avg_tail(values: list[float], count: int) -> Optional[float]:
    tail = [float(value) for value in values[-count:] if isinstance(value, (int, float))]
    return round(sum(tail) / len(tail), 3) if tail else None


def _avg_previous(values: list[float], count: int, offset: int) -> Optional[float]:
    previous = [float(value) for value in values[-count - offset:-offset] if isinstance(value, (int, float))]
    return round(sum(previous) / len(previous), 3) if previous else None
def _fmt_quantity(value: Optional[float]) -> str:
    if value is None:
        return "缺失"
    abs_value = abs(value)
    if abs_value >= 100_000_000:
        return f"{value / 100_000_000:.2f}亿"
    if abs_value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return f"{value:.1f}"


def _latest_month(rows: list[dict[str, Any]]) -> Optional[str]:
    months = sorted(row["month"] for row in rows if row.get("month"))
    return months[-1] if months else None


def _month_from_title(title: str) -> Optional[str]:
    match = re.search(r"\b([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", title)
    if not match:
        match = re.search(r"\b([A-Za-z]{3})[a-z]*\.?,?\s*(\d{4})", title)
    if not match:
        return None
    month = MONTHS.get(match.group(1).lower()[:3])
    return f"{match.group(2)}-{month}" if month else None


def _month_label(month: Optional[str]) -> str:
    if not month:
        return ""
    try:
        parsed = datetime.strptime(month, "%Y-%m")
    except ValueError:
        return month
    return parsed.strftime("%Y-%m")


def _first_title_row(rows: list[list[str]]) -> str:
    return next((" ".join(row).strip() for row in rows if row and row[0]), "")
