"""每日 A股收盘复盘：把大盘 / 板块 / 个股表现 与「我们网站的快讯·文章·研报」串起来，
体现信息价值——把今日涨跌归因到我们提前发现的资讯。

数据：东财 push2(直连绕代理) 取指数 / 行业板块 / 全市场涨跌；交叉比对 realtime_messages + 研报；
叙述：MiniMax 合成（失败回退确定性模板，narrative_provider 诚实标注）。
持久化：复用 data_store（kind=ashare_review, symbol=DAILY），history 即历史复盘。
设计原则：纯加性、绝不拖垮主流程；所有外部取数失败优雅降级。
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

import httpx

from . import data_store
from .eastmoney_data import fetch_eastmoney_index
from .llm import CloudResearchLLM, _extract_json
from .realtime_messages import list_realtime_messages

CN_TZ = timezone(timedelta(hours=8))
_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
_STORE_KIND = "ashare_review"
_STORE_SYM = "DAILY"

# 复盘要看的主要指数（东财 secid）
_INDICES = [
    ("上证指数", "1.000001"),
    ("深证成指", "0.399001"),
    ("创业板指", "0.399006"),
    ("沪深300", "1.000300"),
    ("科创50", "1.000688"),
    ("北证50", "0.899050"),
]
# 沪深京 A 股 clist 市场过滤
_A_FS = "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23,m:0+t:81+s:2048"
# 行业板块
_BOARD_FS = "m:90+t:2"
# 备用源（新浪）：东财限流/失败时兜底。指数 list 代码 + 名称
_SINA_INDICES = [
    ("上证指数", "sh000001"), ("深证成指", "sz399001"), ("创业板指", "sz399006"),
    ("沪深300", "sh000300"), ("科创50", "sh000688"), ("北证50", "bj899050"),
]
_SINA_HQ = "http://hq.sinajs.cn/list="
_SINA_NODE = "http://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
_SINA_HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Referer": "http://finance.sina.com.cn/"}
# iFinD 指数代码（首选指数兜底源：东财 push2his 指数被封时用，账号已实测有指数权限）
_IFIND_INDICES = [
    ("上证指数", "000001.SH"), ("深证成指", "399001.SZ"), ("创业板指", "399006.SZ"),
    ("沪深300", "000300.SH"), ("科创50", "000688.SH"), ("北证50", "899050.BJ"),
]


def cn_now() -> datetime:
    return datetime.now(CN_TZ)


def cn_today_str() -> str:
    return cn_now().strftime("%Y-%m-%d")


_SESSION_LABEL = {"midday": "午盘复盘", "close": "收盘复盘"}


def current_session(now: Optional[datetime] = None) -> str:
    """按北京时间判定复盘场次：<15:00 = 午盘(midday)、>=15:00 = 收盘(close)。
    A股 15:00 收盘，故 15 点前生成的是盘中（午盘）数据、15 点后才是收盘定稿。"""
    now = now or cn_now()
    return "close" if now.hour >= 15 else "midday"


def session_label(session: str) -> str:
    return _SESSION_LABEL.get(session, "复盘")


def _clist_url(fs: str, fields: str, fid: str, po: str, pn: int, pz: int) -> str:
    return (
        "https://push2.eastmoney.com/api/qt/clist/get"
        f"?pn={pn}&pz={pz}&po={po}&np=1&fltt=2&invt=2&fid={fid}"
        f"&fs={fs}&fields={fields}"
    )


async def _em_clist(fs: str, fields: str, *, fid: str = "f3", po: str = "1", pz: int = 60) -> list[dict]:
    """东财 clist 单页拉取（按 fid 排序，po=1 降序/0 升序）。直连绕代理；失败 → []。"""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(_clist_url(fs, fields, fid, po, 1, pz), headers=_HEADERS)
        if r.status_code != 200:
            return []
        diff = (((r.json() or {}).get("data") or {}).get("diff")) or []
        if isinstance(diff, dict):
            diff = list(diff.values())
        return [d for d in diff if isinstance(d, dict)]
    except Exception:
        return []


async def _em_clist_all(fs: str, fields: str, *, fid: str = "f3", po: str = "1", max_rows: int = 6000) -> list[dict]:
    """东财 clist 全量分页拉取（单页上限 100，按 total 并发翻页）。用于全市场涨跌统计。失败 → 已取到的。"""
    page_sz = 100
    out: list[dict] = []
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=15.0) as client:
            r = await client.get(_clist_url(fs, fields, fid, po, 1, page_sz), headers=_HEADERS)
            data = ((r.json() or {}).get("data") or {})
            total = int(data.get("total") or 0)
            first = data.get("diff") or []
            if isinstance(first, dict):
                first = list(first.values())
            out.extend([d for d in first if isinstance(d, dict)])
            pages = min((min(total, max_rows) + page_sz - 1) // page_sz, 80)
            # 其余页并发拉（分批 12，避免触发限频）
            rest = list(range(2, pages + 1))
            for i in range(0, len(rest), 12):
                batch = rest[i:i + 12]
                results = await asyncio.gather(
                    *[client.get(_clist_url(fs, fields, fid, po, pn, page_sz), headers=_HEADERS) for pn in batch],
                    return_exceptions=True,
                )
                for resp in results:
                    if isinstance(resp, Exception):
                        continue
                    try:
                        diff = (((resp.json() or {}).get("data") or {}).get("diff")) or []
                        if isinstance(diff, dict):
                            diff = list(diff.values())
                        out.extend([d for d in diff if isinstance(d, dict)])
                    except Exception:
                        continue
    except Exception:
        pass
    return out


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


async def _sina_index_date(code: str = "sh000001") -> Optional[str]:
    """新浪指数最后成交日期（YYYY-MM-DD）。节假日冻结在上一交易日，故可靠判交易日。失败 → None。"""
    try:
        async with httpx.AsyncClient(trust_env=False, timeout=10.0) as client:
            r = await client.get(_SINA_HQ + code, headers=_SINA_HEADERS)
        if r.status_code != 200:
            return None
        m = re.search(r'="([^"]*)"', r.text)
        if not m:
            return None
        md = re.search(r"\d{4}-\d{2}-\d{2}", m.group(1))
        return md.group(0) if md else None
    except Exception:
        return None


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


async def _gather_sectors_breadth() -> tuple[dict, dict]:
    """行业板块涨跌（领涨/领跌 + 主力净额 + 领涨股）+ 全市场涨跌家数。
    涨跌家数 = 各行业板块成分股涨/跌家数(f104/f105/f106)汇总——一次请求拿全，免全量翻页（曾因翻页被限流）。"""
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
    return sectors, breadth


def _age_label(created_at: str, now: datetime) -> tuple[float, str]:
    """资讯距今小时数 + 「盘前提前/盘中同日/数日前」标签（体现领先性）。"""
    try:
        dt = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except Exception:
        return (0.0, "")
    hours = round((now - dt).total_seconds() / 3600, 1)
    dt_cn = dt.astimezone(CN_TZ)
    today_open = now.astimezone(CN_TZ).replace(hour=9, minute=30, second=0, microsecond=0)
    if dt_cn < today_open - timedelta(days=1):
        lead = "数日前"
    elif dt_cn < today_open:
        lead = "今日盘前"
    else:
        lead = "盘中"
    return (hours, lead)


# 去公司/板块后缀，得到更易命中的检索词（隆基绿能→隆基、半导体材料→半导体）
_STOCK_SUFFIX = re.compile(
    r"(绿能|科技|股份|集团|能源|新材料|材料|新材|电子|生物|医药|制药|地产|银行|证券|保险|国际|实业|发展|"
    r"半导体|信息|通信|网络|软件|智能|环保|化工|机械|重工|电力|电气|食品|乳业|汽车|股份有限公司|有限公司|公司)$"
)
_SECTOR_SUFFIX = re.compile(r"(主材|材料|设备|概念|板块|行业|指数|Ⅱ|II)$")


def _event_signature(title: str) -> str:
    """事件签名：去掉数字/价格/标点后取前缀，用于折叠「同一事件的不同报价更新」
    （如『现货黄金 下跌2%至4236美元』与『现货黄金 下跌近1%至4220美元』视为同一条）。"""
    base = re.sub(r"[0-9０-９.,%％\s·:：/\\\-—－()（）【】\[\]\"'《》→↑↓+~、]+", "", title or "")
    return base[:7]


_UP_WORDS = re.compile(r"涨|飙|拉升|走高|新高|反弹|冲高|爆发|领涨|攀升|上扬|走强|回升|大增|利好")
_DOWN_WORDS = re.compile(r"跌|重挫|下挫|走低|回落|杀跌|跳水|新低|领跌|下行|承压|走弱|回调|大降|利空|爆雷")


def _title_opposes(title: str, up_today: bool) -> bool:
    """标题方向是否与今日走势相反（剔除方向不符的旧资讯）。中性/双向 → 不算相反、保留。"""
    t = title or ""
    up = bool(_UP_WORDS.search(t))
    down = bool(_DOWN_WORDS.search(t))
    if up == down:
        return False
    return down if up_today else up


def _search_signals(key: str, since: str, now: datetime, limit: int = 20) -> list[dict]:
    if not key or len(key) < 2:
        return []
    try:
        msgs = list_realtime_messages(anyq=key, since=since, limit=limit)
    except Exception:
        return []
    sigs = []
    for m in msgs:
        topic = getattr(m, "topic", "") or ""
        if topic not in ("快讯", "文章", "研报"):
            continue
        hours, lead = _age_label(getattr(m, "created_at", "") or "", now)
        title = (getattr(m, "title", "") or "")[:60]
        content = re.sub(r"https?://\S+", "", getattr(m, "content", "") or "").strip()
        snippet = content[:90] if content and content != title else ""
        sigs.append({
            "id": getattr(m, "id", "") or "",
            "url": getattr(m, "url", "") or "",
            "title": title,
            "snippet": snippet,  # 供 AI 判定相关性（不入前端）
            "topic": topic,
            "created_at": getattr(m, "created_at", ""),
            "age_hours": hours,
            "lead": lead,
        })
    sigs.sort(key=lambda s: s["age_hours"], reverse=True)
    return sigs


def _gather_our_signals(sectors: dict, now: datetime, *, movers: Optional[dict] = None, days: int = 7) -> list[dict]:
    """核心差异化：今日领涨/领跌【板块】+ 今日异动【个股】 × 我们近 N 天的快讯/文章/研报 交叉比对，
    挑出「我们提前覆盖」的主线，并体现领先时长（突出快讯实时性）。
    返回 [{kind:'sector'|'stock',name,theme,pct,direction,lead_hours,signals:[...]}]，按领先时长排序。"""
    since = (now - timedelta(days=days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    out: list[dict] = []
    seen_keys: set[str] = set()
    # 候选阶段只要 ≥1 条即收（放宽召回，多读我们的内容）；真伪/方向由后续 _judge_linkages 的 AI 判定层把关
    MIN_SIGNALS = 1

    def _collect(theme: str, name: str) -> list[dict]:
        """主题词 + 全名检索，按 id 去重合并；再按「事件签名」折叠同一事件的重复报价更新；按领先时长降序。"""
        sigs = _search_signals(theme, since, now)
        if theme != name:
            seen = {(s.get("id") or s.get("title")) for s in sigs}
            for s in _search_signals(name, since, now):
                k = s.get("id") or s.get("title")
                if k and k not in seen:
                    seen.add(k)
                    sigs.append(s)
        # 事件签名去重：同一事件的多条报价更新只保留最早一条（最大 age_hours）
        sigs.sort(key=lambda s: s.get("age_hours") or 0, reverse=True)
        dedup: list[dict] = []
        seen_sig: set[str] = set()
        for s in sigs:
            sg = _event_signature(s.get("title", ""))
            if sg and sg in seen_sig:
                continue
            if sg:
                seen_sig.add(sg)
            dedup.append(s)
        return dedup

    def _push(kind: str, name: str, theme: str, pct: Any) -> None:
        key = f"{kind}:{name}"
        if key in seen_keys:
            return
        sigs = _collect(theme, name)
        # ⭐方向一致性过滤：今日上涨的，剔除明显写「跌/回落」的旧资讯；今日下跌的，剔除写「涨/飙」的。
        # 方向不符不算「我们提前发现」——宁可不显示，也不硬塞、不写勉强的辩解。
        up_today = (pct or 0) >= 0
        sigs = [s for s in sigs if not _title_opposes(s.get("title", ""), up_today)]
        if len(sigs) < MIN_SIGNALS:  # 过滤后佐证不足 → 不收
            return
        seen_keys.add(key)
        out.append({
            "kind": kind, "name": name, "theme": theme, "pct": pct,
            "direction": "up" if (pct or 0) >= 0 else "down",
            "evidence": len(sigs),  # 佐证条数（前端可显示「N 条佐证」）
            # 领先时长：最早一条相关资讯距今多少小时（越大=我们越早发现）
            "lead_hours": max((s.get("age_hours") or 0) for s in sigs),
            "signals": sigs[:4],
        })

    # 1) 板块级
    for b in (sectors.get("top") or [])[:8] + (sectors.get("bottom") or [])[:3]:
        name = (b.get("name") or "").strip()
        theme = _SECTOR_SUFFIX.sub("", name) or name
        _push("sector", name, theme, b.get("pct"))

    # 2) 个股级：今日涨幅榜 + 【跌幅榜】各前 6 做候选——涨的=提前发现机会、跌的=提前预警风险，
    #    两端都纳入比对(消除"只报涨"的幸存者偏差)；方向真伪仍由后续 AI 判定层把关。
    if movers:
        for s in (movers.get("gainers") or [])[:6] + (movers.get("losers") or [])[:6]:
            name = (s.get("name") or "").strip()
            if not name:
                continue
            theme = _STOCK_SUFFIX.sub("", name) or name
            _push("stock", name, theme, s.get("pct"))

    out.sort(key=lambda x: (x.get("evidence") or 0, x.get("lead_hours") or 0), reverse=True)
    return out


# 判定标准（agentic 与 judge 共用）：什么才算「我们提前覆盖了驱动逻辑」
_LINK_RULES = (
    "判断标准：哪些异动是我们【真正提前覆盖了它的驱动逻辑或催化事件】——即我们的内容确实在解释这个方向"
    "『为什么会动』。【涨、跌两端都算】：今日上涨的=我们提前发现了利好/机会；今日下跌的=我们提前覆盖了利空/风险"
    "（提示风险同样是价值，但措辞要中性，只说『提前覆盖了该风险/利空驱动』，不要写成『叫人卖出』）。"
    "以下情况一律剔除：① 仅标题出现同一个词（关键词巧合）；② 只是该品种的价格波动快讯"
    "（如『现货黄金下跌2%』这类纯报价跳动），与今日方向无逻辑因果；③ 我们内容的立场与今日走势相反、又讲不出"
    "转向逻辑（如今日大跌、我们之前却在唱多且无风险提示）；④ 同一事件的重复推送。宁缺毋滥：没有真正逻辑关联就不要保留。\n"
)


def _candidate_catalog(candidates: list[dict]) -> str:
    """把候选异动+其关键词召回的内容子条目，编号成喂给模型的清单。"""
    lines: list[str] = []
    for i, c in enumerate(candidates):
        pct = c.get("pct")
        pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        kind = "板块" if c.get("kind") == "sector" else "个股"
        head = f"[{i}] {kind} {c.get('name')} {pct_s}（今日{'上涨' if (pct or 0) >= 0 else '下跌'}）"
        subs = []
        for j, s in enumerate((c.get("signals") or [])[:5]):
            sn = f"／{s['snippet']}" if s.get("snippet") else ""
            subs.append(f"    ({j}) [{s.get('topic')}·约{(s.get('age_hours') or 0):.0f}h前] {s.get('title')}{sn}")
        lines.append(head + ("\n" + "\n".join(subs) if subs else ""))
    return "\n".join(lines)


def _conservative_keep(candidates: list[dict]) -> list[dict]:
    keep = [dict(c) for c in candidates if len(c.get("signals") or []) >= 2]
    for c in keep:
        c.setdefault("reason", "")
    keep.sort(key=lambda x: (x.get("evidence") or 0, x.get("lead_hours") or 0), reverse=True)
    return keep[:8]


def _apply_keep(candidates: list[dict], keep: Any) -> list[dict]:
    """把模型的 keep 决策映射回候选 → 带 reason/evidence/lead_hours 的 our_edge。"""
    out: list[dict] = []
    for k in (keep or []):
        if not isinstance(k, dict):
            continue
        try:
            ci = int(k.get("i"))
        except (TypeError, ValueError):
            continue
        if not (0 <= ci < len(candidates)):
            continue
        c = dict(candidates[ci])
        sigs = list(c.get("signals") or [])
        subs = k.get("subs")
        if isinstance(subs, list) and subs:
            picked = []
            for sj in subs:
                try:
                    j = int(sj)
                except (TypeError, ValueError):
                    continue
                if 0 <= j < len(sigs):
                    picked.append(sigs[j])
            if picked:
                sigs = picked
        if not sigs:
            continue
        c["signals"] = sigs
        c["reason"] = str(k.get("reason") or "").strip()
        c["evidence"] = len(sigs)
        c["lead_hours"] = max((s.get("age_hours") or 0) for s in sigs)
        out.append(c)
    out.sort(key=lambda x: (x.get("evidence") or 0, x.get("lead_hours") or 0), reverse=True)
    return out[:8]


def _extract_keep(text: Optional[str]) -> Optional[Any]:
    try:
        data = _extract_json(text or "")
    except Exception:
        data = None
    if isinstance(data, dict) and "keep" in data:
        return data.get("keep")
    return None


async def _agentic_linkages(candidates: list[dict], now: datetime) -> Optional[list[dict]]:
    """⭐工程最优路径：用平台 tool-use agent（search_our_content 工具）让模型【自主多轮检索我们的内容库】，
    换更精准的关键词核实真正的驱动逻辑后，给出结构化判断。失败/不支持 → 返回 None，由上层回退 _judge_linkages。"""
    if not candidates:
        return []
    try:
        from .agent_tools import TOOL_REGISTRY, execute_tool
        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return None
        tool = TOOL_REGISTRY.get("search_our_content")
        if tool is None:
            return None
        tool_specs = [tool.openai_spec()]
        system = (
            "你是严谨的投研编辑，具备检索工具。任务：判断今天A股的哪些异动，是我们平台『提前覆盖了其驱动逻辑/催化事件』。\n"
            "下面给出今日异动清单与每个异动已初步关键词召回的候选内容（带编号）。你应当主动调用 search_our_content 工具，"
            "换更精准的关键词（行业驱动、催化事件、政策、龙头公司名等）再深挖我们的内容库，核实候选是不是只是报价跳动、"
            "或找到更能说明驱动的内容；可多次检索。\n" + _LINK_RULES +
            "核实完成后，只输出一个 JSON object（不要解释、不要 Markdown、不要思考过程）："
            "{\"keep\":[{\"i\":条目号,\"subs\":[该条目已给候选里真正相关的子条目号...],\"reason\":\"一句话说明为何算提前覆盖，可引用你检索到的内容\"}]}。"
            "没有任何成立的就输出 {\"keep\":[]}。"
        )
        user = "【今日异动与候选】\n" + _candidate_catalog(candidates)
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        client = llm._client()
        for _round in range(4):
            resp = await asyncio.wait_for(
                client.chat.completions.create(
                    model=llm.model, messages=messages, tools=tool_specs,
                    tool_choice="auto", max_tokens=1500,
                ),
                timeout=45.0,
            )
            msg = resp.choices[0].message
            tcs = list(getattr(msg, "tool_calls", None) or [])
            if not tcs:
                parsed = _extract_keep(msg.content)
                return _apply_keep(candidates, parsed) if parsed is not None else None
            messages.append({
                "role": "assistant", "content": msg.content or "",
                "tool_calls": [{
                    "id": tc.id, "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments or "{}"},
                } for tc in tcs],
            })
            for tc in tcs:
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except (ValueError, TypeError):
                    args = {}
                result = await execute_tool(tc.function.name, args)
                content = json.dumps(result, ensure_ascii=False)
                if len(content) > 3000:
                    content = content[:3000] + "…(截断)"
                messages.append({
                    "role": "tool", "tool_call_id": tc.id,
                    "name": tc.function.name, "content": content,
                })
        # 用满轮次仍未给 JSON → 去掉工具逼一次最终结构化结论
        final = await asyncio.wait_for(
            client.chat.completions.create(
                model=llm.model,
                messages=messages + [{"role": "user", "content": "基于以上检索，现在只输出最终 JSON：{\"keep\":[...]}。不要解释。"}],
                max_tokens=1200,
            ),
            timeout=45.0,
        )
        parsed = _extract_keep(final.choices[0].message.content)
        return _apply_keep(candidates, parsed) if parsed is not None else None
    except Exception:
        return None


async def _judge_linkages(candidates: list[dict], now: datetime) -> list[dict]:
    """无工具的结构化判定（agentic 失败时的回退）：一次性把候选喂给模型判真伪，带 3 次重试，再失败 → 关键词兜底。"""
    if not candidates:
        return []
    prompt = (
        "你是严谨的投研编辑。下面每个条目是今天A股的一个异动（板块或个股，标了今日涨/跌方向），"
        "其下缩进的是我们近几天发布、关键词上沾边的内容候选。\n" + _LINK_RULES +
        "每个保留项，从其候选里挑出真正相关的子条目编号，并给一句话 reason 说明为何算『提前覆盖』。\n\n"
        + _candidate_catalog(candidates) +
        "\n\n只输出 JSON object：{\"keep\":[{\"i\":条目号,\"subs\":[子条目号...],\"reason\":\"一句话\"}]}。"
        "没有任何成立的就输出 {\"keep\":[]}。"
    )
    data = None
    for _ in range(3):
        try:
            data = await CloudResearchLLM().complete_json(prompt, max_tokens=1200, timeout_seconds=75)
        except Exception:
            data = None
        if isinstance(data, dict) and "keep" in data:
            break
    if not isinstance(data, dict) or "keep" not in data:
        return _conservative_keep(candidates)
    return _apply_keep(candidates, data.get("keep"))


def _fmt_flow(v: Optional[float]) -> str:
    if v is None:
        return "—"
    a = abs(v)
    if a >= 1e8:
        return f"{v / 1e8:+.2f}亿"
    if a >= 1e4:
        return f"{v / 1e4:+.0f}万"
    return f"{v:+.0f}"


def _template_narrative(snap: dict) -> dict:
    """LLM 不可用时的确定性叙述（保证复盘永远有内容）。"""
    idx = snap.get("indices") or []
    br = snap.get("breadth") or {}
    sec = snap.get("sectors") or {}
    edge = snap.get("our_edge") or []
    idx_txt = "；".join(f"{i['name']} {i['close']}（{i['pct']:+.2f}%）" for i in idx if i.get("pct") is not None) or "指数数据暂缺"
    top = "、".join(f"{b['name']}({b['pct']:+.2f}%)" for b in (sec.get("top") or [])[:4]) or "—"
    adv = br.get('advancers'); dec = br.get('decliners')
    breadth_txt = f"全市场涨 {adv} 跌 {dec}，" if (adv is not None and dec is not None) else ""
    market = f"今日 {idx_txt}。{breadth_txt}涨停 {br.get('limit_up') or '—'} 家、跌停 {br.get('limit_down') or '—'} 家。"
    sectors = f"领涨板块：{top}。"
    # 导读（模板版）：用确定性基调，专业但通俗地概括盘面
    adv_n = br.get("advancers"); dec_n = br.get("decliners")
    _vt = (snap.get("verdict") or {}).get("tone") or ""
    tone = {"偏强": "市场情绪偏暖、做多意愿占优", "偏弱": "市场承压、情绪偏谨慎",
            "结构分化": "指数分化、结构性行情明显", "中性震荡": "指数窄幅震荡、观望情绪浓"}.get(_vt, "盘面表现平稳")
    breadth_hint = f"涨跌家数 {adv_n}:{dec_n}，" if (adv_n and dec_n) else ""
    plain = (
        f"今日 A股{tone}，{breadth_hint}涨停 {br.get('limit_up') or '—'} 家、跌停 {br.get('limit_down') or '—'} 家，"
        f"赚钱效应{'尚可' if (br.get('limit_up') or 0) >= (br.get('limit_down') or 0) else '偏弱'}。"
        f"资金主要聚焦 {top} 等方向。"
    )
    if edge:
        names = "、".join(e["name"] for e in edge[:5])
        lead = max((e.get("lead_hours") or 0) for e in edge)
        lead_s = f"，其中最早领先大盘约 {lead:.0f} 小时" if lead else ""
        our = (
            f"今天异动的 {names} 等方向，DeepFocus近期的快讯/文章/研报已提前覆盖{lead_s}"
            f"——点开下方蓝色【标题】可看当时发布的原内容。"
        )
    else:
        our = ""  # 无线索整块隐藏
    return {
        "one_liner": market,
        "plain": plain,
        "market": market,
        "sectors": sectors,
        "funds": "",
        "our_value": our,
        "tomorrow": "",
    }


def _gather_content_items(now: datetime, *, days: int = 2) -> list[dict]:
    """近 N 天本站快讯/文章/研报条目（含 id/topic/url，供前端做来源超链接）。"""
    since = (now - timedelta(days=days)).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    try:
        msgs = list_realtime_messages(since=since, limit=400)
    except Exception:
        msgs = []
    counts = {"快讯": 0, "文章": 0, "研报": 0}
    caps = {"快讯": 80, "文章": 40, "研报": 25}
    items: list[dict] = []
    for m in msgs:
        topic = getattr(m, "topic", "") or ""
        if topic not in caps or counts[topic] >= caps[topic]:
            continue
        counts[topic] += 1
        items.append({
            "id": getattr(m, "id", ""),
            "topic": topic,
            "title": (getattr(m, "title", "") or "").strip(),
            "url": getattr(m, "url", "") or "",
            "content": (getattr(m, "content", "") or ""),
            "created_at": getattr(m, "created_at", ""),
        })
    return items


def _digest_text(items: list[dict], now: datetime) -> str:
    """把条目格式化成喂给 LLM 的清单文本（带时间戳/领先标签）。"""
    def _fmt(it: dict, with_content: bool = False) -> str:
        _, lead = _age_label(it.get("created_at", "") or "", now)
        try:
            dt_cn = datetime.fromisoformat((it.get("created_at", "") or "").replace("Z", "+00:00"))
            if dt_cn.tzinfo is None:
                dt_cn = dt_cn.replace(tzinfo=timezone.utc)
            ts = dt_cn.astimezone(CN_TZ).strftime("%m-%d %H:%M")
        except Exception:
            ts = ""
        title = (it.get("title") or "")[:64]
        extra = ""
        if with_content:
            c = re.sub(r"https?://\S+", "", it.get("content") or "")[:80].strip()
            if c and c != title:
                extra = f"｜{c}"
        return f"  [{ts}/{lead}] {title}{extra}"

    kx = [i for i in items if i["topic"] == "快讯"]
    wz = [i for i in items if i["topic"] == "文章"]
    yb = [i for i in items if i["topic"] == "研报"]
    lines: list[str] = []
    if kx:
        lines.append("〔本站快讯〕"); lines += [_fmt(i) for i in kx]
    if wz:
        lines.append("〔本站文章〕"); lines += [_fmt(i, with_content=True) for i in wz]
    if yb:
        lines.append("〔本站研报〕"); lines += [_fmt(i) for i in yb]
    return "\n".join(lines)


async def _llm_narrative(snap: dict, now: Optional[datetime] = None, items: Optional[list[dict]] = None,
                         feedback: Optional[list[str]] = None) -> Optional[dict]:
    """MiniMax 合成买方视角复盘叙述：通读近 2 天本站快讯/文章/研报，把今日涨跌【归因】到我们发过的具体内容。
    feedback：批评家/数字校验给出的问题清单 → 据此修订重写（Critic-Reviser 回路）。
    失败/未配置 → None（上层回退模板）。"""
    now = now or cn_now()
    if items is None:
        items = _gather_content_items(now)
    idx = snap.get("indices") or []
    br = snap.get("breadth") or {}
    sec = snap.get("sectors") or {}
    mv = snap.get("movers") or {}
    edge = snap.get("our_edge") or []
    idx_lines = "\n".join(f"- {i['name']}: 收 {i['close']}，{i['pct']:+.2f}%" for i in idx if i.get("pct") is not None)
    top_lines = "\n".join(f"- {b['name']}: {b['pct']:+.2f}%，主力{_fmt_flow(b.get('main_flow'))}，领涨{b.get('leader','')}" for b in (sec.get("top") or [])[:6]) or "（板块数据暂缺）"
    bot_lines = "\n".join(f"- {b['name']}: {b['pct']:+.2f}%" for b in (sec.get("bottom") or [])[:4]) or "（暂缺）"
    edge_lines = ""
    for e in edge[:12]:
        sl = "；".join(f"[{s['topic']}/{s['lead']}/约{s['age_hours']}h前]{s['title']}" for s in e["signals"][:3])
        tag = "板块" if e.get("kind") == "sector" else "个股"
        pct = e.get("pct")
        pct_s = f"{pct:+.2f}%" if isinstance(pct, (int, float)) else ""
        lead_h = e.get("lead_hours")
        lead_s = f"（最早领先约{lead_h:.0f}小时）" if isinstance(lead_h, (int, float)) and lead_h else ""
        reason = e.get("reason")
        reason_s = f"〔关联理由：{reason}〕" if reason else ""
        edge_lines += f"- [{tag}]{e.get('name','')} {pct_s}{lead_s}{reason_s}：{sl}\n"
    content_digest = _digest_text(items, now)
    _fr = snap.get("fund_rank") or {}
    inflow_s = "、".join(f"{x['name']}{(x['flow'] or 0) / 1e8:+.1f}亿" for x in (_fr.get("inflow") or [])) or "—"
    outflow_s = "、".join(f"{x['name']}{(x['flow'] or 0) / 1e8:+.1f}亿" for x in (_fr.get("outflow") or [])) or "—"
    sess = snap.get("session") or current_session(now)
    if sess == "midday":
        sess_intro = (
            f"为今日（{snap.get('date')}）撰写一份【午盘复盘】——A股上午收盘（11:30）后、下午盘前的盘中复盘，"
            "数据为【盘中半日】口径，措辞需体现『上午/盘中/截至午间』、不要说成全天收盘定论，可对下午做客观提示。"
        )
    else:
        sess_intro = f"为今日（{snap.get('date')}）撰写一份【收盘复盘】——A股全天收盘（15:00）后的定稿复盘，数据为全天口径。"
    prompt = (
        f"今天是{now.strftime('%Y-%m-%d')} {('周一','周二','周三','周四','周五','周六','周日')[now.weekday()]}。"
        f"你是「DeepFocus」终端的首席策略分析师，{sess_intro}面向【有一定基础的普通投资者】，"
        "要专业、有信息量、有逻辑，同时表达通俗清晰——像一位资深分析师在跟朋友讲盘，"
        "客观、不吹票、不构成投资建议。\n\n"
        "【写作要求】\n"
        "1. 专业但不堆黑话：可以正常使用专业概念（主力净流入、北向资金、量能、估值切换等），"
        "但讲到稍冷门或容易误解的概念时，顺带半句话点明含义即可；不要逐词科普、不要幼稚化、不要『小学生造句』式解释。\n"
        "2. 体现平台的信息价值——这是本复盘的灵魂：今天异动的板块/个股里，凡是DeepFocus近几天用【快讯/文章/研报】提前提到过的，"
        "就在行文中用【】把它的标题原样括进句子（例：『半导体走强，两天前DeepFocus的【中芯国际产能…】快讯就提示了这条线』）。"
        "快讯尤其要突出领先时间——下方清单标了每条领先小时数，自然地点出『领先约X小时/盘前就发了』。"
        "标题须与下方清单一字不差，不可编造；只在确有关联时引用。\n"
        "3. ⭐【重要·宁缺毋滥】只有当某个方向有【两条及以上】本站相关内容能互相印证时，才作为『提前覆盖』来强调；"
        "若只有孤零零一条、或关联明显牵强（只是关键词碰巧撞上），就直接不写——可信度比数量重要。"
        "下方清单已替你筛过（每个方向都≥2条），但你仍要判断关联是否真实成立，不成立的就不写。\n"
        "4. 不要单列一段空洞自夸，把『提前覆盖』织进对盘面的解释里，再用 our_value 字段做克制的总结。\n"
        "4b. ⛔【文风纪律·必须遵守】正文是给读者看的成品，不是编辑室笔记：\n"
        "   - 全文禁止第一人称（『我们』『我』『咱们』一律不准出现），提到平台一律用『DeepFocus』或『本站』。\n"
        "   - 禁止把写作过程中的判断词写进正文：『归因』『佐证』『印证』『匹配』『筛选』『盘点下来』『不勉强』『强佐证』『关联』这类元话语一个都不许出现——"
        "读者只需要结论本身，不需要看到你是怎么核实的。\n"
        "   - 某方向没有提前覆盖时【直接不提】，严禁写『暂无明确方向形成佐证』『这一项不勉强』之类的空转辩解句。\n"
        "5. ⛔【严禁臆造因果·最重要】只陈述数据支持的关系，绝不凭空编因果。涨跌方向必须自洽：\n"
        "   - 下跌的板块/个股，绝不能说成『支撑/带动了大盘上涨』；上涨的也不能说成拖累下跌。\n"
        "   - 把某条资讯关联到某个涨跌时，方向必须一致：利好性资讯只能对应『上涨』的标的，利空性资讯只能对应『下跌』的标的；方向对不上就不要关联。\n"
        "   - 凡是『A 导致 B 涨/跌』这类因果，只有数据或资讯明确支持才写；否则改为客观并列陈述（如『X 走强，同时 Y 回落』），不要硬编谁导致谁。\n"
        "   - 下方『我们提前覆盖』清单已按方向一致性筛过；若某方向为空，就大方地不提我们的资讯，绝不要为凑『信息价值』硬找或写勉强的辩解（如『虽然当时方向相反但我们一直在跟踪』这类话一律不准出现）。\n"
        "6. 数字纪律：资金、北向、量能、估值等只用下方给到的数据，没给到的具体数值不要编。\n\n"
        "7. ⭐【以板块/主线为主、少谈个股】这是一份机构买方视角的市场复盘，重心是大盘基调、板块主线、资金切换、宏观/政策背景；"
        "个股最多顺带点 1-2 个最具代表性的领涨股佐证主线即可，不要罗列一堆个股名。\n\n"
        f"【今日基调·系统按真实数据判定（你的全部叙述必须与此一致，不得自相矛盾）】{snap.get('verdict', {}).get('tone', '')}（{snap.get('verdict', {}).get('basis', '')}）\n\n"
        f"【资金面·板块主力净流入榜（亿元，正=净流入/负=净流出，funds 段据此写资金切换主线）】\n"
        f"净流入：{inflow_s}\n净流出：{outflow_s}\n\n"
        f"【指数】\n{idx_lines}\n\n"
        f"【涨跌家数】涨{br.get('advancers')}/跌{br.get('decliners')}，涨停{br.get('limit_up')}、跌停{br.get('limit_down')}\n\n"
        f"【领涨板块】\n{top_lines}\n\n【领跌板块】\n{bot_lines}\n\n"
        f"【★今日异动中 我们提前覆盖过的板块/个股（含领先时长，请优先在 our_value 与正文里引用）】\n{edge_lines or '（今日暂无匹配）'}\n\n"
        f"【★本站近两日快讯/文章/研报清单（用于在行文中【】引用，标题须一字不差）】\n{content_digest or '（暂无）'}\n\n"
        "只输出 JSON object，字段如下（每句尽量独立成意、便于分行展示；不要 Markdown、不要多余解释）：\n"
        "one_liner: 一句话总结今日盘面，≤40字，口语化；\n"
        "plain: 『导读』，用清晰通俗、但专业有料的话讲清今天盘面的核心逻辑与驱动（发生了什么、背后为什么），3-5句，"
        "点到为止、有洞察，不要幼稚化也不要逐词科普；\n"
        "market: 大盘表现，2-3句，含指数与市场情绪，相关处自然【】引用本站内容；\n"
        "sectors: 板块与个股主线，3-5句，点名领涨领跌主线及驱动，相关处自然【】引用本站内容；\n"
        "funds: 资金面，1-2句，主力资金流向哪些板块、流出哪些，并用一句话解释『主力』指什么；\n"
        "our_value: 『提前发现』价值总结，1-3句，自信但不夸张地点出今天哪些异动DeepFocus用快讯/文章/研报提前发过、最早领先多久（如『今日X个方向DeepFocus提前提示，快讯最早领先约X小时』）；"
        "若下方『提前覆盖』清单为空，our_value 直接输出空字符串 \"\"——不要写任何『保持敏锐/持续跟踪/前瞻布局』式的空话；\n"
        "tomorrow: 下一交易日值得关注的点，1-2句，只做客观提示、不构成建议；"
        "表述里不要写『明日/明天』——今天若是周五或节前，下一交易日是下周一，统一写『下一交易日』或『下周一』。"
    )
    if feedback:
        fb = "\n".join(f"- {x}" for x in feedback[:8])
        prompt += (
            "\n\n⚠【上一稿被审稿驳回，必须按以下意见逐条修正后重写，其余保持质量】：\n"
            f"{fb}\n"
            "修正后重新输出完整 JSON（同样的字段），不要保留被指出的问题。"
        )
    try:
        data = await CloudResearchLLM().complete_json(prompt, max_tokens=2600, timeout_seconds=90)
        if isinstance(data, dict) and (data.get("market") or data.get("one_liner") or data.get("plain")):
            return {k: data.get(k, "") for k in ("one_liner", "plain", "market", "sectors", "funds", "our_value", "tomorrow")}
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# 质量回路（先进 agent 工程）：确定性校验算法 + Critic–Reviser 对抗回路
#   ① _numeric_suspects：纯算法抽出叙述里的所有数字，逐个核对是否真来自我们喂的数据——编的数字抓现行；
#   ② _citation_suspects：纯算法核对叙述里每个【标题】是否真存在于来源清单——编的引用抓现行；
#   ③ _critic_narrative：红队批评家 agent 拿事实表挑刺（方向矛盾/无依据因果/强行归因）；
#   ④ _revise_narrative：带着问题清单重写一遍（self-refine）。任何一环失败都优雅放过，不阻断出稿。
# --------------------------------------------------------------------------- #
_NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def _fund_rank(sectors: dict) -> dict:
    """资金面：把所有板块按主力净流入排序，取净流入/净流出各前 5（资金切换主线）。"""
    boards = [b for b in ((sectors.get("top") or []) + (sectors.get("bottom") or [])) if isinstance(b.get("main_flow"), (int, float))]
    seen, uniq = set(), []
    for b in boards:
        n = b.get("name")
        if n and n not in seen:
            seen.add(n)
            uniq.append(b)
    uniq.sort(key=lambda b: b.get("main_flow") or 0, reverse=True)
    inflow = [{"name": b["name"], "flow": b["main_flow"], "pct": b.get("pct")} for b in uniq if (b.get("main_flow") or 0) > 0][:5]
    outflow = [{"name": b["name"], "flow": b["main_flow"], "pct": b.get("pct")} for b in reversed(uniq) if (b.get("main_flow") or 0) < 0][:5]
    return {"inflow": inflow, "outflow": outflow}


def _market_verdict(indices: list, breadth: dict) -> dict:
    """确定性『今日基调』：纯规则按真实数据判定，作为 LLM 叙述不可篡改的锚（杜绝随意联想/方向矛盾）。
    返回 {tone, score, basis, diverge}。"""
    pcts = [i["pct"] for i in (indices or []) if isinstance(i.get("pct"), (int, float))]
    avg = sum(pcts) / len(pcts) if pcts else 0.0
    adv = breadth.get("advancers")
    dec = breadth.get("decliners")
    lu = breadth.get("limit_up")
    ld = breadth.get("limit_down")
    ups = sum(1 for p in pcts if p > 0)
    downs = sum(1 for p in pcts if p < 0)
    diverge = ups > 0 and downs > 0 and (max(pcts) - min(pcts)) >= 1.5  # 指数涨跌分化(如沪强创弱)
    score = 0.0
    if isinstance(adv, int) and isinstance(dec, int) and (adv + dec) > 0:
        score += ((adv / (adv + dec)) - 0.5) * 120  # 涨跌家数主导 ±60
    score += max(-30.0, min(30.0, avg * 15))         # 指数均幅 ±30
    if isinstance(lu, int) and isinstance(ld, int):
        score += max(-15.0, min(15.0, (lu - ld) * 0.5))  # 涨跌停净额 ±15
    score = int(max(-100, min(100, score)))
    if diverge:
        tone = "结构分化"
    elif score >= 25:
        tone = "偏强"
    elif score <= -25:
        tone = "偏弱"
    else:
        tone = "中性震荡"
    basis = f"指数均幅{avg:+.2f}%、涨{adv if adv is not None else '—'}跌{dec if dec is not None else '—'}、涨停{lu if lu is not None else '—'}/跌停{ld if ld is not None else '—'}"
    return {"tone": tone, "score": score, "basis": basis, "diverge": diverge}


def _allowed_numbers(snap: dict) -> set[float]:
    """喂给 LLM 的所有"真实数字"集合（百分比/亿元/家数/领先时长），用于校验叙述里的数字是否凭空捏造。"""
    nums: set[float] = set()

    def _add(v: Any) -> None:
        try:
            if v is None:
                return
            f = round(float(v), 2)
            nums.add(f)
            nums.add(round(abs(f), 2))
        except (TypeError, ValueError):
            pass

    for i in (snap.get("indices") or []):
        _add(i.get("pct")); _add(i.get("close"))
    br = snap.get("breadth") or {}
    for k in ("advancers", "decliners", "flat", "limit_up", "limit_down", "total"):
        _add(br.get(k))
    sec = snap.get("sectors") or {}
    for b in (sec.get("top") or []) + (sec.get("bottom") or []):
        _add(b.get("pct"))
        mf = b.get("main_flow")
        if isinstance(mf, (int, float)):
            _add(round(mf / 1e8, 1)); _add(round(mf / 1e8, 2))
    fr = snap.get("fund_rank") or {}
    for x in (fr.get("inflow") or []) + (fr.get("outflow") or []):
        fl = x.get("flow")
        if isinstance(fl, (int, float)):
            _add(round(fl / 1e8, 1)); _add(round(fl / 1e8, 2))
        _add(x.get("pct"))
    for e in (snap.get("our_edge") or []):
        _add(e.get("pct")); _add(e.get("lead_hours"))
        for s in (e.get("signals") or []):  # 引用我们资讯标题里的数字（合法引用，非编造）
            for m in re.findall(r"\d+(?:\.\d+)?", s.get("title", "") or ""):
                _add(m)
    # 来源清单标题里的数字也算合法（AI 原样引用我们快讯/文章/研报里的数据，不是凭空捏造）
    for src in (snap.get("sources") or []):
        for m in re.findall(r"\d+(?:\.\d+)?", src.get("title", "") or ""):
            _add(m)
    return nums


_NUM_RE = re.compile(r"(?<![\d.])(\d{1,5}(?:\.\d+)?)\s*(%|％|亿|万亿)")


def _citation_violations(narrative: dict, items: Optional[list[dict]]) -> list[str]:
    """确定性引用校验：正文【】里引用的标题必须真实存在于本站内容清单（容忍截断/省略号），
    否则视为编造引用打回重写。清单为空时跳过（无从校验）。"""
    if not items:
        return []
    real = [str(it.get("title") or "").strip() for it in items if it.get("title")]
    out = []
    for field in ("plain", "market", "sectors", "funds", "our_value", "tomorrow"):
        for q in re.findall(r"[【《](.+?)[】》]", str(narrative.get(field) or "")):
            frag = q.rstrip("….").strip()[:12]  # 引用可能截断，取前段做包含匹配
            if len(frag) < 4:
                continue
            if not any(frag in t for t in real):
                out.append(f"{field} 段引用的【{q}】在本站内容清单中不存在——严禁编造标题，删除该引用或换成清单里真实存在的标题")
    return out


_STYLE_BANNED = ("我们", "我方", "咱们", "道财经", "归因", "佐证", "印证", "盘点下来", "不勉强", "强佐证",
                 "无直接对应", "关联度有限", "暂无明确", "未能提前", "无匹配")


def _style_violations(narrative: dict) -> list[str]:
    """确定性文风校验：正文禁第一人称与编辑室元话语（读者只要结论，不要看到核实过程）。"""
    out = []
    for field in ("one_liner", "plain", "market", "sectors", "funds", "our_value", "tomorrow"):
        text = str(narrative.get(field) or "")
        for w in _STYLE_BANNED:
            if w in text:
                out.append(f"{field} 段出现「{w}」——正文禁止第一人称与『归因/佐证/盘点』类元话语，平台一律称『DeepFocus/本站』，没有提前覆盖就直接不提")
                break
    return out


def _number_violations(narrative: dict, snap: dict) -> list[str]:
    """确定性数字校验（零 LLM）：抽出叙述里的 百分比/亿 数字，凡不在真实数据集合里的（容差 0.15）= 疑似编造。"""
    allowed = _allowed_numbers(snap)
    text = " ".join(str(narrative.get(k) or "") for k in ("plain", "market", "sectors", "funds", "our_value", "tomorrow"))
    bad: list[str] = []
    for m in _NUM_RE.finditer(text):
        try:
            val = round(float(m.group(1)), 2)
        except ValueError:
            continue
        unit = m.group(2)
        if unit in ("%", "％") and val == 0:
            continue
        if not any(abs(val - a) <= 0.15 for a in allowed):
            seg = text[max(0, m.start() - 12):m.end() + 4].strip()
            bad.append(f"{m.group(1)}{unit}（…{seg}…）")
    # 去重，最多报 6 个
    seen, out = set(), []
    for b in bad:
        key = b.split("（")[0]
        if key not in seen:
            seen.add(key); out.append(b)
    return out[:6]


async def _critic_issues(narrative: dict, snap: dict) -> list[str]:
    """批评家 agent（红队）：拿真实事实表逐条审稿，挑出方向矛盾/编造数字/无依据因果/强行归因/夸大。
    返回问题清单（空=通过）。LLM 失败 → []（不阻断，靠数字校验兜底）。"""
    draft = "\n".join(f"【{k}】{narrative.get(k) or ''}" for k in ("plain", "market", "sectors", "funds", "our_value", "tomorrow"))
    verdict = snap.get("verdict") or {}
    idx_s = "；".join(f"{i['name']}{i.get('close','')}点{i['pct']:+.2f}%" for i in (snap.get("indices") or []) if i.get("pct") is not None)
    fund_s = "净流入：" + ("、".join(f"{x['name']}{(x['flow'] or 0)/1e8:+.1f}亿" for x in ((snap.get('fund_rank') or {}).get('inflow') or [])) or "—")
    sec = snap.get("sectors") or {}
    up_s = "、".join(f"{b['name']}{b['pct']:+.2f}%" for b in (sec.get("top") or [])[:6])
    dn_s = "、".join(f"{b['name']}{b['pct']:+.2f}%" for b in (sec.get("bottom") or [])[:4])
    edge_s = "、".join(f"{e.get('name')}({'涨' if (e.get('pct') or 0) >= 0 else '跌'})" for e in (snap.get("our_edge") or [])) or "（无）"
    prompt = (
        "你是极严苛的投研复盘审稿人（红队）。下面是一份A股复盘草稿，以及它必须依据的【真实事实表】。"
        "请逐句核对，只挑出确凿的问题，给出可操作的修改要求。重点查：\n"
        "① 涨跌方向矛盾（把下跌说成支撑上涨、或归因方向反了）；\n"
        "② 编造数字（出现事实表里没有的百分比/资金额/点位）；\n"
        "③ 无依据的因果（事实表/资讯撑不起的『A 导致 B』）；\n"
        "④ 强行归因或夸大我们的信息价值（事实表『我们提前覆盖』里没有的方向，却说我们提示了）；\n"
        "⑤ 与系统基调自相矛盾。\n\n"
        f"【系统基调】{verdict.get('tone')}（{verdict.get('basis')}）\n"
        f"【指数】{idx_s}\n【领涨板块】{up_s}\n【领跌板块】{dn_s}\n【资金面】{fund_s}\n"
        f"【我们确实提前覆盖的方向】{edge_s}\n\n"
        "注意：以上事实表已给出的点位/涨跌幅/资金额都是真实的，不要把它们当编造；只标事实表里【没有】的数字。\n"
        f"【复盘草稿】\n{draft}\n\n"
        "只输出 JSON：{\"issues\":[\"每条一句话、指明问题在哪段+怎么改\"]}。完全没问题就 {\"issues\":[]}。不要表扬、不要解释。"
    )
    try:
        data = await CloudResearchLLM().complete_json(prompt, max_tokens=900, timeout_seconds=60)
        if isinstance(data, dict) and isinstance(data.get("issues"), list):
            return [str(x).strip() for x in data["issues"] if str(x).strip()][:8]
    except Exception:
        return []
    return []


async def build_review(date_str: Optional[str] = None) -> dict:
    """汇集数据 + 交叉比对 + 合成叙述，返回完整复盘 dict（不落库，由调用方决定）。"""
    now = cn_now()
    date_str = date_str or now.strftime("%Y-%m-%d")
    session = current_session(now)  # 午盘(midday)/收盘(close)
    # 顺序取数 + 间隔，温柔对待东财（曾因突发并发被限流）
    indices = await _gather_indices()
    await asyncio.sleep(0.4)
    sectors, breadth = await _gather_sectors_breadth()
    await asyncio.sleep(0.4)
    movers = await _gather_movers()  # 仅用于大盘涨停/跌停家数（不展示个股榜）
    breadth["limit_up"] = movers.get("limit_up")
    breadth["limit_down"] = movers.get("limit_down")
    edge_candidates = _gather_our_signals(sectors, now, movers=movers)  # 关键词召回候选（放宽、去重）
    # ⭐工程最优：先用 tool-use agent（自主多轮检索内容库）判真伪；失败 → 无工具判定层 → 关键词兜底
    our_edge = await _agentic_linkages(edge_candidates, now)
    if our_edge is None:
        our_edge = await _judge_linkages(edge_candidates, now)
    for _e in our_edge:                                                # 去掉内部用的 snippet（不入库/不入前端）
        for _s in (_e.get("signals") or []):
            _s.pop("snippet", None)
    content_items = _gather_content_items(now)     # 本站近两日资讯条目（归因素材 + 来源超链接）
    snap = {
        "date": date_str,
        "session": session,
        "session_label": session_label(session),
        "generated_at": now.isoformat(),
        "indices": indices,
        "breadth": {k: breadth.get(k) for k in ("advancers", "decliners", "flat", "limit_up", "limit_down", "total")},
        "sectors": sectors,
        "our_edge": our_edge,
        "verdict": _market_verdict(indices, breadth),  # 确定性基调锚（LLM 不可篡改）
        "fund_rank": _fund_rank(sectors),              # 资金面：按板块主力净流入排（净流入/净流出 top）
        # 来源（供前端把复盘里【标题】变成可点链接，点开看原文）
        "sources": [{k: it.get(k, "") for k in ("id", "topic", "title", "url", "created_at")} for it in content_items],
    }
    narrative = None
    for _ in range(3):  # LLM 偶发超时/空 → 重试，尽量拿到 AI 归因而非弱模板
        narrative = await _llm_narrative(snap, now, content_items)
        if narrative and narrative.get("market"):
            break
    # ⭐Critic–Reviser 对抗回路：批评家红队 + 确定性数字校验 → 有问题就带意见修订一次（降幻觉）
    if narrative and narrative.get("market"):
        try:
            critic = await _critic_issues(narrative, snap)
            numviol_before = _number_violations(narrative, snap)
            issues = (critic or []) + [f"数字「{v}」在真实数据里查无、疑似编造，请删掉或改为真实值" for v in numviol_before]
            issues += _style_violations(narrative)
            issues += _citation_violations(narrative, content_items)
            revised_flag = False
            if issues:
                revised = await _llm_narrative(snap, now, content_items, feedback=issues)
                if (revised and revised.get("market")
                        and len(_number_violations(revised, snap)) <= len(numviol_before)
                        and not _citation_violations(revised, content_items)
                        and not _style_violations(revised)):
                    narrative = revised  # 修订稿数字未变多才采用（避免越改越差）
                    revised_flag = True
                snap["review_issues"] = issues  # 审计：本次审稿提了哪些问题
            numviol_after = _number_violations(narrative, snap)
            # 质量可观测性：每次生成落一条 eval 记录（初稿问题数/数字违规/是否修订/残留），看板出趋势
            _record_review_quality(snap, len(critic or []), len(numviol_before), len(numviol_after), revised_flag, provider="ai")
        except Exception:  # noqa: BLE001 - 审稿失败不阻断出稿
            pass
    if narrative and not (snap.get("our_edge") or []):
        narrative["our_value"] = ""  # 无提前覆盖就整块不显示，绝不填「保持敏锐/持续跟踪」式空话
    snap["narrative"] = narrative or _template_narrative(snap)
    snap["narrative_provider"] = "ai" if narrative else "template"
    return snap


_QUALITY_KIND = "review_quality"


def _record_review_quality(snap: dict, critic_issues: int, numviol_before: int, numviol_after: int, revised: bool, provider: str = "ai") -> None:
    """LLM 质量 eval 落库：记录每次复盘生成的初稿问题数/数字违规/是否修订/残留，供质量趋势看板。失败静默。"""
    try:
        data_store.record(_QUALITY_KIND, _STORE_SYM, {
            "date": snap.get("date"), "session": snap.get("session"),
            "provider": provider,
            "critic_issues": int(critic_issues),         # 批评家在初稿挑出的问题数
            "numviol_before": int(numviol_before),       # 初稿编造数字数
            "numviol_after": int(numviol_after),         # 修订后残留编造数字数（理想=0）
            "revised": bool(revised),                    # 是否触发了修订
            "generated_at": snap.get("generated_at") or cn_now().isoformat(),
        })
    except Exception:  # noqa: BLE001
        pass


def review_quality_stats(limit: int = 60) -> dict:
    """质量趋势：近 N 次生成的均值(初稿问题/数字违规/修订率/残留违规) + 明细。供内部质量看板。"""
    rows = data_store.history(_QUALITY_KIND, _STORE_SYM, limit=max(1, min(limit, 300)))
    recent = []
    for r in rows:
        p = r.get("payload") or {}
        recent.append({
            "date": p.get("date", ""), "session": p.get("session", ""),
            "critic_issues": p.get("critic_issues", 0), "numviol_before": p.get("numviol_before", 0),
            "numviol_after": p.get("numviol_after", 0), "revised": p.get("revised", False),
            "provider": p.get("provider", ""), "generated_at": p.get("generated_at", r.get("recorded_at", "")),
        })
    n = len(recent)

    def _avg(key: str) -> float:
        return round(sum((x.get(key) or 0) for x in recent) / n, 2) if n else 0.0

    # 环比：近 7 天 vs 前 7 天（按 generated_at）。指标越低越好 → 下降=改善(good)。
    now = cn_now()
    cutoff_cur = (now - timedelta(days=7))
    cutoff_prev = (now - timedelta(days=14))

    def _parse(ts: str) -> Optional[datetime]:
        try:
            d = datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except Exception:
            return None

    cur_b, prev_b = [], []
    for x in recent:
        d = _parse(x.get("generated_at", ""))
        if d is None:
            continue
        if d >= cutoff_cur:
            cur_b.append(x)
        elif d >= cutoff_prev:
            prev_b.append(x)

    def _bavg(bucket: list, key: str) -> Optional[float]:
        return round(sum((y.get(key) or 0) for y in bucket) / len(bucket), 2) if bucket else None

    def _trend(key: str, higher_better: bool = False) -> dict:
        cur = _bavg(cur_b, key)
        prev = _bavg(prev_b, key)
        direction, good = "flat", None
        if cur is not None and prev is not None:
            if cur > prev + 0.005:
                direction, good = "up", higher_better       # 上升：升好则改善
            elif cur < prev - 0.005:
                direction, good = "down", (not higher_better)  # 下降：越低越好则改善
        return {"cur": cur, "prev": prev, "dir": direction, "good": good,
                "delta": (round(cur - prev, 2) if (cur is not None and prev is not None) else None)}

    def _rate_bucket(bucket: list) -> Optional[float]:
        return round(sum(1 for y in bucket if (y.get("numviol_after") or 0) == 0) / len(bucket), 2) if bucket else None

    clean_cur, clean_prev = _rate_bucket(cur_b), _rate_bucket(prev_b)
    clean_dir, clean_good = "flat", None
    if clean_cur is not None and clean_prev is not None:
        if clean_cur > clean_prev + 0.005:
            clean_dir, clean_good = "up", True       # 干净率上升 = 改善（好）
        elif clean_cur < clean_prev - 0.005:
            clean_dir, clean_good = "down", False     # 下降 = 变差

    return {
        "count": n,
        "avg_critic_issues": _avg("critic_issues"),
        "avg_numviol_before": _avg("numviol_before"),
        "avg_numviol_after": _avg("numviol_after"),
        "revise_rate": round(sum(1 for x in recent if x.get("revised")) / n, 2) if n else 0.0,
        "clean_after_rate": round(sum(1 for x in recent if (x.get("numviol_after") or 0) == 0) / n, 2) if n else 0.0,
        # 环比：近7天 vs 前7天。critic_issues/numviol_before 越低越好；clean_after 越高越好
        "trend": {
            "critic_issues": _trend("critic_issues"),
            "numviol_before": _trend("numviol_before"),
            "clean_after": {"cur": clean_cur, "prev": clean_prev, "dir": clean_dir, "good": clean_good,
                            "delta": (round(clean_cur - clean_prev, 2) if (clean_cur is not None and clean_prev is not None) else None)},
        },
        "cur7_count": len(cur_b), "prev7_count": len(prev_b),
        "recent": recent[:40],
    }


def is_complete(review: dict) -> bool:
    """行情数据是否取全（防止东财限流时落库半成品：指数走 push2his 仍在，但板块/个股的 push2 clist 可能空）。"""
    if not review:
        return False
    br = review.get("breadth") or {}
    sec = review.get("sectors") or {}
    # 大盘+板块版：有板块 或 有涨跌家数 或 有涨停数（涨停来自 movers/新浪兜底，东财板块限流时仍可成稿）
    return bool(br.get("total") or sec.get("top") or br.get("limit_up"))


def save_review(review: dict) -> bool:
    """只落库取全的复盘；半成品(限流)不存，返回是否已存。"""
    if not is_complete(review):
        return False
    data_store.record(_STORE_KIND, _STORE_SYM, review)
    return True


def latest_review() -> Optional[dict]:
    """最近一期【取全】的复盘（跳过限流时落下的半成品，若历史里混有）。
    含 session 字段：盘中返回午盘、收盘后返回收盘（按生成先后自然演进）。"""
    for r in data_store.history(_STORE_KIND, _STORE_SYM, limit=20):
        p = r.get("payload") or {}
        if is_complete(p):
            return p
    return None


def has_review_today(session: str, date_str: Optional[str] = None) -> bool:
    """今日该【场次】是否已生成取全的复盘（午盘/收盘独立去重，互不顶替）。"""
    date_str = date_str or cn_today_str()
    for r in data_store.history(_STORE_KIND, _STORE_SYM, limit=20):
        p = r.get("payload") or {}
        if p.get("date") == date_str and (p.get("session") or "close") == session and is_complete(p):
            return True
    return False


def list_reviews(limit: int = 60) -> list[dict]:
    """历史复盘（新→旧），按日期去重保留最新一版；返回轻量摘要列表。"""
    rows = data_store.history(_STORE_KIND, _STORE_SYM, limit=max(1, min(limit * 4, 400)))
    out: list[dict] = []
    seen: set[str] = set()  # 按 日期+场次 去重：同日午盘/收盘各保留最新一版
    for r in rows:
        p = r.get("payload") or {}
        d = p.get("date") or ""
        sess = p.get("session") or "close"
        key = f"{d}#{sess}"
        if not d or key in seen:
            continue
        seen.add(key)
        nar = p.get("narrative") or {}
        out.append({
            "date": d,
            "session": sess,
            "session_label": p.get("session_label") or session_label(sess),
            "one_liner": nar.get("one_liner", ""),
            "edge_count": len(p.get("our_edge") or []),
            "generated_at": p.get("generated_at", r.get("recorded_at", "")),
        })
        if len(out) >= limit:
            break
    return out


def review_for_date(date_str: str) -> Optional[dict]:
    rows = data_store.history(_STORE_KIND, _STORE_SYM, limit=300)
    for r in rows:
        p = r.get("payload") or {}
        if p.get("date") == date_str:
            return p
    return None


async def traded_today() -> bool:
    """今日是否 A股交易日：以上证指数最新日线日期 == 今日（北京时间）判定。免维护节假日表。

    数据源带兜底（东财 push2his 指数曾整段被封导致复盘停摆）：
    ①东财 push2his 日线 → ②新浪指数日期（节假日冻结在上一交易日，可靠）。
    ⚠️不用 iFinD 判交易日：其实时 time 字段是查询时刻、非最后成交时间，节假日会误判。"""
    today = cn_today_str()
    kl = await fetch_eastmoney_index("1.000001", points=2)
    if kl:
        return kl[-1][0] == today
    sina_date = await _sina_index_date("sh000001")  # 东财被封时兜底
    if sina_date:
        return sina_date == today
    return False  # 两源皆不可用 → 故障安全：宁可不发，不发错
