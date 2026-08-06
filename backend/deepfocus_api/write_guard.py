"""写侧风控（防主动灌输垃圾信息）：全局写限流 + 复用快讯 junk 内容识别。

背景：内容过滤（news_filter）只罩快讯入库链路；用户可写接口（AI 反馈 / 私信 /
表态）此前无频率限制，恶意者批量注册后可无限灌垃圾文本、刷踩作废共享答案缓存。
本模块提供两个守卫，供写接口在落库前调用：

- check_rate(kind, actor)：内存滑窗限流，登录用户按用户名、匿名按客户端 IP 计数。
  进程重启即清零——可接受，目的是挡脚本化灌入而非精确计费。
- junk_hit(text)：复用 news_filter.junk_reason（反爬提示页 / 域名喊话 / 会议分享卡
  等垃圾模式），让反馈 / 私信与快讯共用同一套内容识别标准。
"""
from __future__ import annotations

import time
from collections import defaultdict
from typing import Optional

from . import news_filter

# 每类写操作的限流窗口：kind -> (窗口内最大次数, 窗口秒数)
_LIMITS: dict[str, tuple[int, float]] = {
    "feedback": (10, 60),   # AI 答案 👍👎 反馈
    "support": (5, 60),     # 管理员私信
    "react": (30, 60),      # 资讯看多/看空表态
}
_HITS: dict[str, list[float]] = defaultdict(list)
_MAX_KEYS = 4096  # 匿名 key=IP，防 key 无限增长；超出时删最早插入的


def check_rate(kind: str, actor: str) -> bool:
    """返回 True=放行；False=已超限（调用方应回 429）。"""
    cap, win = _LIMITS.get(kind, (20, 60))
    key = f"{kind}:{(actor or 'anon').strip() or 'anon'}"
    now = time.monotonic()
    dq = _HITS[key]
    dq[:] = [t for t in dq if now - t < win]
    if len(dq) >= cap:
        return False
    dq.append(now)
    if len(_HITS) > _MAX_KEYS:
        for k in list(_HITS)[: len(_HITS) - _MAX_KEYS]:
            _HITS.pop(k, None)
    return True


def junk_hit(text: str) -> Optional[str]:
    """文本命中快讯垃圾识别规则 → 返回 reason；干净/判不了 → None。"""
    t = (text or "").strip()
    if not t:
        return None
    try:
        return news_filter.junk_reason(t, "")
    except Exception:  # noqa: BLE001 - 过滤模块异常不阻断用户写入
        return None
