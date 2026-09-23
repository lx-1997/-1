"""机构纪要当日多空统计：LLM 逐条判定看多/看空/中性 + 板块，聚合成比例与板块分布。

- 数据：zsxq_stream.fetch_stream 最新首页（+必要时翻一页），按北京时间过滤今天的帖子；
  上限 60 条，样本量如实披露，不做全市场推断。
- 判定：单次批量 complete_json（严格 JSON）；无明确方向一律 neutral，绝不臆测。
- 缓存：进程内 TTL 20min + AsyncSingleFlight 防击穿；LLM 失败回退当日旧缓存，否则 ok=False（前端隐藏统计条）。
- 输出仅方向枚举与板块名词，已属中性化表述；展示层须带 AI 生成标识（前端负责）。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .async_singleflight import AsyncSingleFlight

logger = logging.getLogger(__name__)

_TTL_SECONDS = 20 * 60.0
_MAX_NOTES = 60
_MAX_SECTORS = 8
_STANCES = ("bull", "bear", "neutral")

_flight: AsyncSingleFlight[dict[str, Any]] = AsyncSingleFlight()
_cache: dict[str, Any] = {"day": "", "expires": 0.0, "payload": None}


def _bj_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _norm_sector(value: Any) -> str:
    s = str(value or "").strip().strip("#，。；、,.; ")
    return s[:12]


async def _collect_today(day: str) -> list[dict[str, Any]]:
    """最新首页 + 必要时翻一页，取今天（北京时间）的帖子，上限 _MAX_NOTES。"""
    from .zsxq_stream import fetch_stream  # noqa: PLC0415

    out: list[dict[str, Any]] = []
    before = ""
    for _ in range(2):  # 首页 + 至多一页更早，足够覆盖一天
        page = await fetch_stream(limit=40, end_time=before, use_cache=True)
        items = page.get("items") or []
        out.extend(items)
        all_today = all(str(it.get("create_time") or "")[:10] == day for it in items)
        if not all_today or not page.get("has_more"):
            break
        before = str(page.get("next_before") or "")
        if not before:
            break
    todays = [it for it in out if str(it.get("create_time") or "")[:10] == day]
    return todays[-_MAX_NOTES:]


def _build_prompt(notes: list[dict[str, Any]]) -> str:
    lines = []
    for i, it in enumerate(notes):
        title = str(it.get("title") or "").strip()[:60]
        text = str(it.get("text") or "").strip()
        text = " ".join(text.split())[:140]
        body = f"{title}：{text}" if title else text
        lines.append(f"{i}: {body}")
    return (
        "你是买方研究助理。下面是当日机构纪要/交易台观点帖子（编号: 内容）。"
        "逐条判定整体方向：bull=明确看多/偏多，bear=明确看空/偏空，neutral=中性/中性偏观望/仅事实陈述。"
        "并提取每条涉及的板块/资产/主题（如 半导体、地产、黄金、人民币、AI算力；无则空数组；每条最多2个，用不超过6个字的名词）。\n"
        "只输出严格 JSON：{\"items\": [{\"i\": 0, \"s\": \"bull|bear|neutral\", \"sec\": [\"..\"]}, ...]}，"
        f"共 {len(notes)} 条，i 必须与输入编号一致，不得遗漏或新增。\n\n"
        + "\n".join(lines)
    )


def _aggregate(notes: list[dict[str, Any]], data: dict[str, Any], day: str) -> dict[str, Any]:
    counts = {"bull": 0, "bear": 0, "neutral": 0}
    sector_stance: dict[str, dict[str, int]] = {}
    judged = 0
    for row in (data.get("items") or []) if isinstance(data, dict) else []:
        if not isinstance(row, dict):
            continue
        try:
            i = int(row.get("i"))
        except (TypeError, ValueError):
            continue
        if not (0 <= i < len(notes)):
            continue
        s = str(row.get("s") or "").strip().lower()
        if s not in _STANCES:
            s = "neutral"
        counts[s] += 1
        judged += 1
        sectors = row.get("sec") or []
        if isinstance(sectors, list):
            for sec in sectors[:2]:
                name = _norm_sector(sec)
                if not name:
                    continue
                bucket = sector_stance.setdefault(name, {"bull": 0, "bear": 0, "neutral": 0})
                bucket[s] = bucket.get(s, 0) + 1
    total = len(notes)
    sample = max(judged, counts["bull"] + counts["bear"] + counts["neutral"])

    def top_sectors(stance: str) -> list[str]:
        ranked = sorted(
            ((name, b[stance]) for name, b in sector_stance.items() if b[stance] > 0),
            key=lambda kv: kv[1], reverse=True,
        )
        return [name for name, _ in ranked[:_MAX_SECTORS]]

    def ratio(n: int) -> Optional[float]:
        return round(n / sample, 3) if sample else None

    return {
        "ok": True,
        "day": day,
        "sample": sample,
        "total_today": total,
        "bull": counts["bull"],
        "bear": counts["bear"],
        "neutral": counts["neutral"],
        "bull_ratio": ratio(counts["bull"]),
        "bear_ratio": ratio(counts["bear"]),
        "neutral_ratio": ratio(counts["neutral"]),
        "bull_sectors": top_sectors("bull"),
        "bear_sectors": top_sectors("bear"),
        "generated_at": _bj_now().isoformat(),
        "ai_generated": True,
    }


async def _compute(day: str, llm: Any) -> dict[str, Any]:
    from .llm import CloudResearchLLM  # noqa: PLC0415

    notes = await _collect_today(day)
    now_iso = _bj_now().isoformat()
    if not notes:
        payload = {
            "ok": True, "day": day, "sample": 0, "total_today": 0,
            "bull": 0, "bear": 0, "neutral": 0,
            "bull_ratio": None, "bear_ratio": None, "neutral_ratio": None,
            "bull_sectors": [], "bear_sectors": [], "generated_at": now_iso, "ai_generated": True,
        }
        _cache.update(day=day, expires=time.monotonic() + _TTL_SECONDS, payload=payload)
        return payload
    llm = llm or CloudResearchLLM()
    data = await llm.complete_json(_build_prompt(notes), max_tokens=2400, timeout_seconds=50)
    payload = _aggregate(notes, data, day)
    _cache.update(day=day, expires=time.monotonic() + _TTL_SECONDS, payload=payload)
    return payload


async def get_daily_sentiment(llm: Any = None, force: bool = False) -> dict[str, Any]:
    now = _bj_now()
    day = now.strftime("%Y-%m-%d")
    if (not force and _cache["day"] == day and time.monotonic() < _cache["expires"]
            and isinstance(_cache["payload"], dict) and _cache["payload"].get("ok")):
        return _cache["payload"]
    try:
        payload = await _flight.run(f"note-sentiment:{day}:{int(force)}", lambda: _compute(day, llm))
        return payload
    except Exception as exc:  # noqa: BLE001
        logger.warning("note_sentiment compute failed: %s: %s", type(exc).__name__, str(exc)[:120])
        stale = _cache["payload"]
        if _cache["day"] == day and isinstance(stale, dict) and stale.get("ok"):
            return stale
        return {"ok": False, "reason": f"{type(exc).__name__}", "generated_at": now.isoformat()}
