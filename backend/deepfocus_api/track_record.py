"""「我们提前发现的」量化战绩。

可靠性铁律：**只聚合已经过 AI 判定层验证的命中**——即历史复盘里 our_edge 中
带 reason（AI 判定了"为何算提前覆盖"）或 evidence>=2（多条佐证）的条目。
绝不重新归因、绝不把关键词撞库的未验证项算进战绩。每条命中都带时间戳证据，可溯源。

数据源：data_store 历史复盘（kind=ashare_review）。同一天同一标的去重；
平台战绩对所有人公开（信任引流），个人战绩按自选股保守匹配（宁可少算不强行算）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from . import data_store

_STORE_KIND = "ashare_review"
_STORE_SYM = "DAILY"
CN_TZ = timezone(timedelta(hours=8))


def _credible(edge: dict) -> bool:
    """这条命中是否够格计入战绩：经 AI 判定(有 reason) 或 多条佐证(evidence>=2)。"""
    if not isinstance(edge, dict):
        return False
    if (edge.get("reason") or "").strip():
        return True
    return (edge.get("evidence") or 0) >= 2


def _collect_hits(days: int) -> list[dict]:
    """从历史复盘里取出所有【可信】命中，按 日期+标的 去重(同标的多场次/多日只留领先最久的一条)。"""
    cutoff = (datetime.now(CN_TZ) - timedelta(days=max(1, days))).strftime("%Y-%m-%d")
    rows = data_store.history(_STORE_KIND, _STORE_SYM, limit=400)
    best: dict[str, dict] = {}
    for r in rows:
        p = r.get("payload") or {}
        date = p.get("date") or ""
        if not date or date < cutoff:
            continue
        for e in (p.get("our_edge") or []):
            if not _credible(e):
                continue
            name = (e.get("name") or "").strip()
            if not name:
                continue
            key = f"{date}#{e.get('kind')}#{name}"
            lead = e.get("lead_hours") or 0
            cur = best.get(key)
            if cur is None or lead > (cur.get("lead_hours") or 0):
                best[key] = {
                    "date": date,
                    "name": name,
                    "kind": e.get("kind") or "sector",
                    "pct": e.get("pct"),
                    "lead_hours": lead,
                    "evidence": e.get("evidence") or len(e.get("signals") or []),
                    "reason": (e.get("reason") or "").strip(),
                    "signals": [{k: s.get(k) for k in ("id", "url", "title", "topic", "lead")}
                                for s in (e.get("signals") or [])[:3]],
                }
    hits = list(best.values())
    hits.sort(key=lambda h: (h["date"], h.get("lead_hours") or 0), reverse=True)
    return hits


def _summarize(hits: list[dict]) -> dict:
    n = len(hits)
    leads = [h["lead_hours"] for h in hits if isinstance(h.get("lead_hours"), (int, float)) and h["lead_hours"] > 0]
    avg_lead = round(sum(leads) / len(leads), 1) if leads else 0
    max_lead = round(max(leads), 1) if leads else 0
    days_covered = len({h["date"] for h in hits})
    return {
        "hit_count": n,
        "avg_lead_hours": avg_lead,
        "max_lead_hours": max_lead,
        "sector_hits": sum(1 for h in hits if h.get("kind") == "sector"),
        "stock_hits": sum(1 for h in hits if h.get("kind") == "stock"),
        "days_covered": days_covered,
    }


def platform_track_record(days: int = 30, recent: int = 12) -> dict:
    """平台战绩：近 N 天经验证的命中聚合 + 近期命中明细(可溯源)。公开。"""
    hits = _collect_hits(days)
    out = _summarize(hits)
    out["days"] = days
    out["recent"] = hits[:recent]
    return out


def _norm(s: str) -> str:
    return re.sub(r"[\s·\-—()（）【】\[\]\"'《》]", "", s or "")


def personal_track_record(hits: list[dict], watchlist_names: list[str], *, recent: int = 8) -> dict:
    """个人战绩：保守匹配——命中标的名与自选股名互为子串，或命中的某条佐证标题含自选股名。
    宁可少算也不强行归因：板块级命中除非自选名直接出现在佐证里，否则不计入个人。"""
    names = [_norm(n) for n in (watchlist_names or []) if n and len(str(n)) >= 2]
    if not names:
        return {"hit_count": 0, "matched": []}
    matched: list[dict] = []
    for h in hits:
        hn = _norm(h.get("name", ""))
        sig_text = _norm(" ".join(s.get("title", "") or "" for s in (h.get("signals") or [])))
        hit_match = False
        for wn in names:
            if len(wn) < 2:
                continue
            if (hn and (wn in hn or hn in wn)) or (wn in sig_text):
                hit_match = True
                break
        if hit_match:
            matched.append(h)
    return {
        "hit_count": len(matched),
        "avg_lead_hours": _summarize(matched)["avg_lead_hours"],
        "matched": matched[:recent],
    }
