"""Persisted incremental deep prewarming with foreground priority."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
import os
import random
import time
from typing import Any, Callable
from types import SimpleNamespace

from .research_cache import deep_draft_cache_key, is_valid_deep_draft_cache
from .research_prewarm_title_policy import is_bare_company_date_title, normalize_company_names


@dataclass
class RetryState:
    failures: int = 0
    after: float = 0.0

    def defer(self, now: float, *, busy: bool = False) -> None:
        if not busy:
            self.failures = min(7, self.failures + 1)
        self.after = now + (30.0 if busy else min(3600.0, 60.0 * 2 ** self.failures))


def choose_jobs(items: list[dict], *, is_local: Callable[[str], bool], retries: dict[str, RetryState],
                now: float, limit: int, download_room: int) -> list[dict]:
    """Only queued arrivals participate; cached PDFs can run without downloads."""
    local, remote = [], []
    for item in items:
        fid = str(item["file_id"])
        if retries.get(fid, RetryState()).after > now:
            continue
        (local if is_local(fid) else remote).append(item)
    reserved = int(bool(remote) and download_room > 0)
    jobs = local[:max(0, limit - reserved)]
    jobs.extend(remote[:max(0, min(limit - len(jobs), download_room))])
    selected_ids = {str(item["file_id"]) for item in jobs}
    for item in local:
        if len(jobs) >= limit:
            break
        if str(item["file_id"]) not in selected_ids:
            jobs.append(item)
    return jobs


async def pending_items(items: list[dict], get_many: Callable, retries: dict[str, RetryState],
                        *, batch_size: int = 100) -> tuple[list[dict], int]:
    """Discard full payloads every batch instead of retaining thousands in RAM."""
    pending, ready = [], 0
    for start in range(0, len(items), batch_size):
        batch = items[start:start + batch_size]
        keys = [deep_draft_cache_key(SimpleNamespace(file_id=item["file_id"])) for item in batch]
        cached = await asyncio.to_thread(get_many, keys)
        for item, key in zip(batch, keys):
            if is_valid_deep_draft_cache(cached.get(key)):
                ready += 1
                retries.pop(str(item["file_id"]), None)
            else:
                pending.append(item)
    return pending, ready


async def _hot_keys() -> tuple[frozenset[str], frozenset[str]]:
    """热门匹配键：(个股名称集, 题材词集)。任何失败退化为空集，预热保持原到达顺序。

    个股：站内关注热度榜（data_points 记录数前 40）→ 经「名称→代码」映射转成名称；
    题材：当日概念板块涨幅榜前 15 个板块名（theme_navigation，内部自带 3 分钟缓存）。"""
    names: set[str] = set()
    themes: set[str] = set()
    try:
        from . import data_store
        rows = await asyncio.to_thread(data_store.hot_symbols, "verdict", days=7.0, limit=40)
        if rows:
            from .stock_name_index import all_name_code
            name_code = await asyncio.to_thread(all_name_code)
            wanted = {str(r.get("symbol") or "").upper() for r in rows if r.get("symbol")}
            for name, code in name_code.items():
                if str(code).upper() in wanted and name:
                    names.add(str(name))
    except Exception:
        names = set()
    try:
        from .theme_navigation import fetch_concept_boards
        boards = await asyncio.wait_for(fetch_concept_boards(limit=15), timeout=20)
        for board in boards or []:
            name = str((board or {}).get("name") or "").strip()
            if len(name) >= 2:
                themes.add(name)
    except Exception:
        themes = set()
    return frozenset(names), frozenset(themes)


def _item_priority(item: dict, hot_names: frozenset[str], hot_topics: frozenset[str]) -> int:
    """0 = 命中热门股名或题材词，1 = 其余。标题/文件名包含即命中。"""
    text = f"{item.get('title') or ''}{item.get('filename') or ''}"
    for name in hot_names:
        if len(name) >= 2 and name in text:
            return 0
    for topic in hot_topics:
        if topic in text:
            return 0
    return 1


async def run_research_prewarm_loop(*, fetch_wire: Callable, fetch_pdf: Callable, limiter: Any) -> None:
    if os.getenv("DEEPFOCUS_RESEARCH_PREWARM", "0").strip().lower() in {"0", "false", "no"}:
        print("[prewarm] 研报预解读未启用")
        return
    from . import research_archive
    from .analysis_limiter import AnalysisBusy
    from .metrics_store import get_ai_cache, get_ai_cache_many, get_daily, incr, set_ai_cache
    from .pdf_brand import has_cached_file_id
    from .research_digest import ResearchDeepDraftRequest
    from .research_multi_agent import ResearchMultiAgentBusy, analysis_cache_key
    from .research_prewarm_policy import research_prewarm_download_cap
    from .research_prewarm_runtime import background_deep_prewarm, deep_to_compact
    from .research_prewarm_signal import wait_research_arrival
    from .stock_name_index import exact_a_share_names

    per_cycle = max(1, min(6, int(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_PER_CYCLE", "6"))))
    gap = max(0.0, float(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_GAP_SECONDS", "3")))
    cycle = max(60.0, float(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_CYCLE_SECONDS", "1800")))
    jitter = min(.45, max(0.0, float(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_JITTER_RATIO", "0"))))
    daily_max = max(0, int(os.getenv("DEEPFOCUS_RESEARCH_PREWARM_DAILY_MAX", "40")))
    dl_key = "research_pdf_dl"
    retries: dict[str, RetryState] = {}
    none_rejects: dict[str, int] = {}
    banned_day = ""
    await asyncio.sleep(25)
    print(f"[prewarm] 增量深度预解读启动：存量不回填，仅跳过A股公司名+日期，每日下载上限{daily_max}")
    while True:
        completed = 0
        try:
            # First successful feed is the baseline; later unseen arrivals queue durably.
            try:
                data = await fetch_wire(limit=200, use_cache=False)
                observed = await asyncio.to_thread(research_archive.observe_prewarm_feed, data.get("items") or [])
                print(f"[prewarm] 增量观察 {observed}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                print(f"[prewarm] 最新列表暂不可用，继续待办增量：{type(exc).__name__}")
            queued, raw_names = await asyncio.gather(
                asyncio.to_thread(research_archive.list_prewarm_pending), asyncio.to_thread(exact_a_share_names),
            )
            names = normalize_company_names(raw_names)
            items, excluded = [], 0
            for item in queued:
                if is_bare_company_date_title(str(item.get("title") or item.get("filename") or ""), names):
                    await asyncio.to_thread(research_archive.mark_prewarm_done, str(item["file_id"]), "excluded")
                    excluded += 1
                else:
                    items.append(item)
            known_ids = {str(item["file_id"]) for item in items}
            retries = {fid: state for fid, state in retries.items() if fid in known_ids}
            pending, ready = await pending_items(items, get_ai_cache_many, retries)
            # 热门股/热门话题优先：稳定排序，组内保持到达顺序；取键失败则原顺序。
            hot_names, hot_topics = await _hot_keys()
            pending.sort(key=lambda it: _item_priority(it, hot_names, hot_topics))
            hot_n = sum(1 for it in pending if _item_priority(it, hot_names, hot_topics) == 0)
            pending_ids = {str(item["file_id"]) for item in pending}
            for item in items:
                if str(item["file_id"]) not in pending_ids:
                    await asyncio.to_thread(research_archive.mark_prewarm_done, str(item["file_id"]))
            now = datetime.now(timezone(timedelta(hours=8)))
            day = now.strftime("%Y-%m-%d")
            used = await asyncio.to_thread(get_daily, dl_key)
            cap = research_prewarm_download_cap(daily_max, now.hour)
            jobs = await asyncio.to_thread(
                choose_jobs, pending, is_local=has_cached_file_id, retries=retries, now=time.monotonic(),
                limit=per_cycle, download_room=0 if banned_day == day else max(0, cap - used),
            )
            print(f"[prewarm] 增量队列 eligible={len(items)} excluded={excluded} ready={ready} pending={len(pending)} hot={hot_n} batch={len(jobs)} downloads={used}/{cap}")
            # One background task: no detached workers and no competing model queue.
            for item in jobs:
                fid = str(item["file_id"])
                state = retries.setdefault(fid, RetryState())
                local = await asyncio.to_thread(has_cached_file_id, fid)
                used = await asyncio.to_thread(get_daily, dl_key)
                if not local and (banned_day == day or used >= cap):
                    continue
                try:
                    # Recheck immediately before any download, including foreground completions.
                    key = deep_draft_cache_key(SimpleNamespace(file_id=fid))
                    existing = await asyncio.to_thread(get_ai_cache, key)
                    if not is_valid_deep_draft_cache(existing):
                        await fetch_pdf(fid, item.get("filename", ""))
                        if not local:
                            await asyncio.to_thread(incr, dl_key)
                    request = ResearchDeepDraftRequest(file_id=fid, title=item.get("title") or "研报",
                                                       filename=item.get("filename") or "", max_pages=18)
                    payload = await background_deep_prewarm(request, get_cache=get_ai_cache,
                                                            set_cache=set_ai_cache, limiter=limiter)
                    if payload is None:
                        rejected = none_rejects.get(fid, 0) + 1
                        none_rejects[fid] = rejected
                        state.defer(time.monotonic())
                        if rejected >= 3:
                            await asyncio.to_thread(
                                research_archive.mark_prewarm_done, fid, "excluded")
                            none_rejects.pop(fid, None)
                            retries.pop(fid, None)
                            print(f"[prewarm] 永久失败(连续{rejected}次未成稿，多为内容风控) fid={fid}")
                        else:
                            print(f"[prewarm] 解读未成稿，退避后重试 fid={fid} {rejected}/3")
                        continue
                    compact = deep_to_compact(payload)
                    await asyncio.to_thread(set_ai_cache, analysis_cache_key(fid), compact)
                    await asyncio.to_thread(set_ai_cache, fid, compact)
                    await asyncio.to_thread(research_archive.mark_prewarm_done, fid)
                    retries.pop(fid, None)
                    none_rejects.pop(fid, None)
                    completed += 1
                    print(f"[prewarm] 深度缓存完成 fid={fid}")
                except asyncio.CancelledError:
                    raise
                except (AnalysisBusy, ResearchMultiAgentBusy):
                    state.defer(time.monotonic(), busy=True)
                    print(f"[prewarm] 解读通道繁忙，稍后重试 fid={fid}")
                except Exception as exc:
                    state.defer(time.monotonic())
                    message = str(exc)
                    if any(phrase in message for phrase in ("下载量异常", "拒绝下载", "下个自然日")):
                        banned_day = day
                        print("[prewarm] 源站今日下载限额，继续处理本地PDF")
                    status_code = getattr(getattr(exc, "response", None), "status_code", None)
                    if (isinstance(status_code, int) and 400 <= status_code < 500
                            and status_code != 429 and state.failures >= 2):
                        await asyncio.to_thread(
                            research_archive.mark_prewarm_done, fid, "excluded")
                        retries.pop(fid, None)
                        none_rejects.pop(fid, None)
                        print(f"[prewarm] 永久失败(下载HTTP {status_code}) fid={fid}")
                    else:
                        print(f"[prewarm] 稍后重试 fid={fid} error={type(exc).__name__}: {str(exc)[:240]}")
                await asyncio.sleep(gap)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            print(f"[prewarm] 本轮异常：{type(exc).__name__}: {str(exc)[:80]}")
        # Drain useful work without a ten-minute pause; idle/failing queues back off.
        delay = 30.0 if completed else cycle
        if jitter:
            delay *= random.uniform(1.0 - jitter, 1.0 + jitter)
        await wait_research_arrival(max(10.0, delay))
