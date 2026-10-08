"""新研报入库 → 预热循环的进程内唤醒信号。

recall_ingest 检测到新增研报时置位脉冲；run_research_prewarm 把固定轮询
sleep 换成可被脉冲提前打断的等待，新报告从最长一个预热周期（服务器配置
5 分钟）缩短到下一个召回周期（默认 75 秒）内进入预热。
脉冲语义：唤醒即重新扫描全库分类，不携带条目数据，多次新增合并为一次唤醒。
"""

from __future__ import annotations

import asyncio

_event: asyncio.Event | None = None


def notify_research_arrived() -> None:
    """置位一次唤醒脉冲。尽力而为，失败静默（预热循环仍按周期兜底）。"""
    global _event
    try:
        if _event is None:
            _event = asyncio.Event()
        _event.set()
    except Exception:
        pass


async def wait_research_arrival(timeout: float) -> bool:
    """等待唤醒脉冲或 timeout 到期；返回是否因脉冲提前唤醒。"""
    global _event
    if _event is None:
        _event = asyncio.Event()
    try:
        await asyncio.wait_for(_event.wait(), timeout)
    except asyncio.TimeoutError:
        return False
    _event.clear()
    return True
