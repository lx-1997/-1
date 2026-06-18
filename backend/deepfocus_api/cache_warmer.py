"""后台缓存预热：定时刷新重外部数据源，请求只读暖缓存——不再让某个倒霉请求承担冷取延迟。

为什么需要：宏观看板等源是「懒加载 + TTL」缓存，TTL 一过，下一个请求就得现场冷取
（Sina/财政部/FRED/东财一大串并发，数秒级）。本预热器在 TTL 到期前主动 force 刷新，
使用户请求几乎永远命中暖缓存。

设计：
- 单循环 + 每源独立周期，到期才刷；**串行**执行——美/中两看板共享 Sina/东财上游，
  并发冷取会互相饿死（见 main._gather_macro_inputs 注释），故预热也必须串行。
- 刷新周期略短于各自缓存 TTL，留足前台预热余量。
- 全程容错：单源失败只记日志、不中断循环，并记为已尝试以免每个 tick 反复打失败源。
- 受 DEEPFOCUS_ENABLE_CACHE_REFRESH 开关控制（默认开；单测/特定部署可关）。
- 单 worker uvicorn → 全进程一份缓存、一个预热任务，无竞态。
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

# (名称, 协程工厂, 周期秒)。周期 < 各自缓存 TTL，保证暖缓存不断档。
Job = tuple[str, Callable[[], Awaitable], float]

_DEFAULT_TICK_SECONDS = 20.0


def is_enabled() -> bool:
    """默认开启；显式设 DEEPFOCUS_ENABLE_CACHE_REFRESH=false 才关闭。"""
    return os.getenv("DEEPFOCUS_ENABLE_CACHE_REFRESH", "true").strip().lower() not in ("false", "0", "no")


def _build_jobs() -> list[Job]:
    """声明式预热表。延迟 import，避免模块级循环依赖；缺源时该 job 自然容错跳过。"""
    from .github_data import (
        fetch_gold_history,
        fetch_oil_history,
        fetch_sp500_index_history,
        fetch_us10y_history,
    )
    from .market_dashboard import fetch_ashare_dashboard, fetch_market_dashboard

    async def _warm_github() -> None:
        # github 月度系列：6h TTL，串行 force 刷新（数据更新罕见，主要为消除偶发冷取 25s 卡顿）。
        await fetch_sp500_index_history(force=True)
        await fetch_us10y_history(force=True)
        await fetch_oil_history(force=True)
        await fetch_gold_history(force=True)

    return [
        ("market_dashboard", lambda: fetch_market_dashboard(force=True), 240.0),   # TTL 300s
        ("ashare_dashboard", lambda: fetch_ashare_dashboard(force=True), 100.0),   # TTL 120s
        ("github_macro", _warm_github, 5 * 3600.0),                               # TTL 6h
    ]


async def _run_due(jobs: list[Job], last_run: dict[str, float], now: float) -> dict[str, float]:
    """跑一轮：到期的 job **串行**执行。容错：单 job 异常不影响其他，且仍记为已尝试
    （下一周期才重试，避免每 tick 反复打失败源）。返回更新后的 last_run（原地修改）。"""
    for name, fn, period in jobs:
        if now - last_run.get(name, 0.0) >= period:
            started = time.monotonic()
            try:
                await fn()
                logger.debug("cache warm ok: %s (%.1fs)", name, time.monotonic() - started)
            except Exception as exc:  # noqa: BLE001 — 预热永不致命
                logger.warning("cache warm failed: %s (%s)", name, exc)
            last_run[name] = now
    return last_run


async def run_cache_warmer(tick: float = _DEFAULT_TICK_SECONDS) -> None:
    """后台预热主循环。首 tick 所有 job 都到期 → 启动即把全部缓存热起来，之后各按周期刷。"""
    if not is_enabled():
        logger.info("cache warmer disabled via DEEPFOCUS_ENABLE_CACHE_REFRESH")
        return
    jobs = _build_jobs()
    last_run: dict[str, float] = {}
    logger.info("cache warmer started: %d jobs, tick=%.0fs", len(jobs), tick)
    while True:
        try:
            await _run_due(jobs, last_run, time.monotonic())
        except asyncio.CancelledError:
            logger.info("cache warmer stopped")
            raise
        except Exception as exc:  # noqa: BLE001 — 循环本身永不退出
            logger.warning("cache warmer tick error: %s", exc)
        await asyncio.sleep(tick)
