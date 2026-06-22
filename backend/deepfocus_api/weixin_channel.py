from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import weixin_bind as bind
from . import weixin_ilink as ilink
from .shared_utils import utc_now_iso

logger = logging.getLogger("deepfocus.weixin_channel")

"""
微信 iLink 渠道管理器（多租户问答 + 准推送）。

- 每个 active 绑定一条独立 getupdates 长轮询；入站用户文本 → agent_fn 取答 → send_text 回。
- 一个 bot = 一个 DeepFocus 用户（1:1），故同一 poller 内串行处理、无 context_token 抢占。
- quasi_push：对 active 绑定用各自缓存的 context_token best-effort 主动发（"准推送"，
  只够到 token 仍有效的近期活跃用户；失败即跳过——见 [[wechat-push-channel]] 的边界）。

agent_fn / context_hint_fn 注入，便于脱离真 LLM 单测；主程序用 make_agent_fn(llm) 接 run_tool_agent。
"""

# agent_fn(question, context_hint) -> answer_text or None
AgentFn = Callable[[str, str], Awaitable[Optional[str]]]
# context_hint_fn(binding) -> hint string
HintFn = Callable[[Dict[str, Any]], str]

_MIN_BACKOFF = 2.0
_MAX_BACKOFF = 30.0
_SESSION_EXPIRED = -14
_FALLBACK_REPLY = (
    "我是 DeepFocus 投研助手 🤖\n"
    "试试问我：\n"
    "· 某只股票怎么样（发名称或代码，如 贵州茅台 / 600519）\n"
    "· 今天大盘 / 收盘复盘\n"
    "· 某条快讯/事件怎么解读"
)
# 纯寒暄词：直接友好引导，不触发研究 agent（省 token、不答非所问）
_GREETINGS = {
    "你好", "您好", "你好啊", "您好啊", "哈喽", "哈啰", "嗨", "hi", "hello", "hey",
    "在吗", "在不在", "在", "在么", "在嘛", "有人吗", "早", "早上好", "中午好", "下午好", "晚上好", "你是谁",
}
# ⭐ 会员 AI 问答（已对所有在效会员开放，去掉了原 lx199710 白名单灰度）。
# 省 token 三板斧：①答案缓存跨用户复用（命中 0 token、不计次）②每日"现算"配额上限 ③推荐问题引导（把流量收敛到高命中问题）。
_QA_DAILY_LIMIT = int(os.getenv("DEEPFOCUS_WEIXIN_QA_DAILY", "10") or 10)            # 每会员每天"现算"问答上限（缓存命中不计入）
_QA_CACHE_TTL = float(os.getenv("DEEPFOCUS_WEIXIN_QA_CACHE_TTL", "1800") or 1800)    # 答案缓存有效期（秒），默认 30min 兼顾行情时效
_QA_AGENT_TIMEOUT = float(os.getenv("DEEPFOCUS_WEIXIN_QA_TIMEOUT", "60") or 60)      # run_tool_agent 每轮 LLM 超时(秒)；个股问答需多轮取数，默认30s 易超时返空→回兜底，放宽到 60s
_RECOMMEND_LIST = (
    "· 今天大盘怎么样 / 收盘复盘\n"
    "· 某只股票怎么样（发名称或代码，如 贵州茅台 / 600519）\n"
    "· 某条快讯 / 事件怎么解读\n"
    "· 某个行业 / 板块前景如何"
)
_HELP_TRIGGERS = {
    "帮助", "help", "功能", "怎么用", "使用说明", "菜单", "你能干嘛", "你能做什么", "?", "？", "？？",
}
_HELP_REPLY = (
    "我是 DeepFocus 投研助手 🤖 可以问我：\n"
    f"{_RECOMMEND_LIST}\n"
    f"（会员每天 {_QA_DAILY_LIMIT} 条 AI 问答；常见问题命中缓存时不计次 😉）"
)
_QUOTA_REPLY = (
    f"今天的 AI 问答额度（{_QA_DAILY_LIMIT} 条/天）已经用完啦，明天再来 🙏\n"
    "（重要快讯我仍会继续推送给你）\n"
    "明天可以试试这些：\n"
    f"{_RECOMMEND_LIST}"
)
_EXPIRED_REPLY = (
    "AI 问答是会员专享功能～你的会员可能已到期。\n"
    "续费后即可继续畅聊投研问题；快讯推送仍可正常使用。"
)
_GREETING_REPLY = (
    "你好 👋 我是 DeepFocus 投研助手。\n"
    "✅ 快讯推送已激活，有重要快讯我会主动推给你。\n"
    "想问点啥都行，比如：\n"
    f"{_RECOMMEND_LIST}"
)
_AUTO_PUSH_INTERVAL = 60.0  # 兜底轮询间隔（秒）；事件驱动是主路径，这只防漏/重启
_KEEPALIVE_INTERVAL = 150.0  # 保活探针间隔（秒，2.5min）：尽量保活——触摸更勤，远小于 token 过期窗，用 getconfig 静默维持 context_token
_KEEPALIVE_CONFIRM = 2       # getconfig 返回非0时，再复探几次确认才判死亡（防瞬时抖动误清活 token → 间歇断联）
_KEEPALIVE_CONFIRM_GAP = 2.0 # 复探间隔（秒）
_PUSH_DEBOUNCE = 1.5        # 被新快讯唤醒后稍等，聚合同一时刻涌入的多条，减少发送次数（≈推送延迟）
_PUSH_GAP_MAX = 0.5         # 错峰：相邻两条 send 的最大间隔（秒），摊平并发尖峰、降反垃圾风险
# ⭐推送游标持久化：重启/部署不再丢推（原 cursor=now 每次重启把停机窗口内的快讯全跳过）。
# 重启后从上次推到处续推；但回补不超过 _CURSOR_MAX_LOOKBACK 秒——长时间停机只补最近一段，防一次性刷屏（反垃圾红线）。
_CURSOR_FILE = Path(os.getenv("DEEPFOCUS_WX_PUSH_CURSOR_FILE", str(Path(__file__).resolve().parents[1] / ".wx_push_cursor")))
_CURSOR_MAX_LOOKBACK = float(os.getenv("DEEPFOCUS_WX_PUSH_MAX_LOOKBACK_SECONDS", "900"))  # 默认 15 分钟


def _load_cursor() -> Optional[str]:
    try:
        v = _CURSOR_FILE.read_text(encoding="utf-8").strip()
        return v or None
    except Exception:
        return None


def _save_cursor(cursor: str) -> None:
    try:
        tmp = _CURSOR_FILE.with_suffix(".tmp")
        tmp.write_text(cursor or "", encoding="utf-8")
        tmp.replace(_CURSOR_FILE)
    except Exception:
        pass


def _iso_shift(iso: str, delta_seconds: float) -> str:
    try:
        from datetime import datetime, timedelta
        t = datetime.fromisoformat(iso.replace("Z", "+00:00")) + timedelta(seconds=delta_seconds)
        return t.isoformat()
    except Exception:
        return iso
_PUSH_GAP_MIN = 0.05        # 量大时自适应压缩间隔的下限
_SEND_TRIES = 3            # 单条推送失败重试次数（覆盖网络抖动/临时 ret!=0/短暂冷却）
_SEND_RETRY_BACKOFF = 1.2  # 重试退避基数（秒）：第 k 次失败后等 (k+1)*base

# 当前活跃的渠道管理器（单例语义）；realtime_messages 落库新快讯时经 notify_new_flash() 唤醒它，实现近实时推送
_active_mgr: Optional["WeixinChannelManager"] = None


def notify_new_flash() -> None:
    """新快讯落库时调用：唤醒自动推送循环立即跑一遍（事件驱动）。线程安全、无渠道时静默。"""
    m = _active_mgr
    if m is None or m._wake is None:
        return
    try:
        loop = m._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(m._wake.set)  # 可能从采集线程调用 → 投递到事件循环线程
        else:
            m._wake.set()
    except Exception:  # noqa: BLE001  唤醒失败不影响快讯本身入库/广播
        pass


def _matches_binding(msg: Any, scope: str, symbols: List[str]) -> bool:
    """快讯是否命中该绑定的订阅。scope=all 全推；watchlist 按关键词(代码精确相等 或 任一关键词模糊命中标题/正文，命中其一即推)。"""
    if scope == "all":
        return True
    if scope != "watchlist" or not symbols:
        return False
    sym = (getattr(msg, "symbol", "") or "").upper()
    text_l = ((getattr(msg, "title", "") or "") + " " + (getattr(msg, "content", "") or "")).lower()
    for s in symbols:
        s2 = str(s).strip()
        if s2 and (s2.upper() == sym or s2.lower() in text_l):  # 代码精确 或 关键词模糊(不区分大小写)
            return True
    return False


def _format_push(msgs: List[Any]) -> str:
    """匹配到的快讯渲染成推送文本（共享格式：每条 `MM-DD HH:MM  内容`）。"""
    return ilink.format_news_push(msgs)


async def _send_retry(b: Dict[str, Any], to: str, ctx: str, text: str, client: Any = None,
                      tries: int = _SEND_TRIES, lock: Any = None) -> tuple[bool, Any]:
    """发一条文本，失败自动重试 + 退避。返回 (是否成功, ret标记)。
    ret标记：成功→None；被确定性拒绝→该 ret 值(int，如 -2 token失效)；多次无响应→字符串 "no-response"。
    重试覆盖：网络抖动/超时（仅"没拿到响应"才重试）。lock：per-bot 串行锁，根除并发抢 token 的竞态。"""
    async def _attempt() -> tuple[bool, Any]:
        last = ""
        for k in range(tries):
            try:
                resp = await ilink.send_text(b["token"], b["base_url"], to, ctx, text, client=client)
                ret = (resp or {}).get("ret")
                # ⭐成功判定对齐 iLink/RDK：ret 缺省(None)或 0 都算成功——成功时常不带 ret，
                # 之前把 None 当失败→重试→把同一条发了多次(重复发送 bug)。
                if ret in (0, None):
                    return True, None
                # 拿到明确非0响应 = 服务端已确定性拒绝(如 -2 token失效)：不重试(重试也发不出、且避免"已送达又重发")。
                logger.warning("[weixin] 推送被拒(ret=%s) %s，不重试", ret, (b.get("ilink_bot_id") or "")[:8])
                return False, ret
            except Exception as exc:  # noqa: BLE001  仅"没拿到响应"(网络/超时)才重试
                last = (type(exc).__name__ + ":" + str(exc))[:80]
            if k < tries - 1:
                await asyncio.sleep(_SEND_RETRY_BACKOFF * (k + 1))  # 退避 1.2s, 2.4s…
        logger.warning("[weixin] 推送重试 %d 次仍失败(无响应) %s: %s", tries, (b.get("ilink_bot_id") or "")[:8], last)
        return False, "no-response"
    if lock is not None:
        async with lock:
            return await _attempt()
    return await _attempt()


# 发送被这些 ret 拒绝 = context_token 对发送已失效（getconfig 仍可能说"活"，但 sendmessage 权威）：清冷转 COLD，
# 停止对死 token 每条快讯反复硬发（反垃圾风险），并诚实提示用户重发激活。
_SEND_DEAD_RETS = {-2, -14}


def _member_can_push(b: Dict[str, Any]) -> bool:
    """该绑定当前是否仍有推送资格：会员 tier ∈ premium/lifetime。
    会员过期/降级（trial/None）即停推——尊享专享，过期自动断流。"""
    uname = (b.get("username") or "").strip()
    if not uname:
        return False
    try:
        from . import auth
        m = auth.membership_of_username(uname)
    except Exception:  # noqa: BLE001  会员服务异常时保守不推
        return False
    return bool(m and m.get("tier") in ("premium", "lifetime"))


def _qa_fingerprint(question: str) -> str:
    """归一化问题 → MD5 指纹（答案缓存键）：去首尾/压缩内部空白、去尾部标点、小写。空问题→''（不缓存）。
    ⭐微信问答无个人上下文（hint 恒空，见 main.py 构造处不传 context_hint_fn），故同一问题跨用户可安全共享答案。"""
    q = re.sub(r"\s+", " ", (question or "").strip().lower()).rstrip("?？!！。.,，、 ")
    return hashlib.md5(q.encode("utf-8")).hexdigest() if q else ""


def make_agent_fn(llm: Any) -> AgentFn:
    """把 CloudResearchLLM.run_tool_agent 适配成 AgentFn（返回 answer 文本或 None）。"""
    async def _agent(question: str, hint: str) -> Optional[str]:
        result = await llm.run_tool_agent(question=question, context_hint=hint, timeout_seconds=_QA_AGENT_TIMEOUT)
        if result and (result.get("answer") or "").strip():
            return result["answer"]
        return None
    return _agent


class WeixinChannelManager:
    def __init__(self, agent_fn: AgentFn, context_hint_fn: Optional[HintFn] = None) -> None:
        self._agent_fn = agent_fn
        self._context_hint_fn = context_hint_fn
        self._tasks: Dict[str, asyncio.Task] = {}
        self._running = False
        self._wake: Optional[asyncio.Event] = None      # 新快讯事件触发（事件驱动，近实时）
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._locks: Dict[str, asyncio.Lock] = {}       # per-bot 串行锁：同一 bot 的 iLink 调用排队，根除并发抢 token

    def _lock_for(self, bot_id: str) -> asyncio.Lock:
        lk = self._locks.get(bot_id)
        if lk is None:
            lk = asyncio.Lock()
            self._locks[bot_id] = lk
        return lk

    # ---- 生命周期 ----

    def start(self) -> None:
        """加载所有 active 绑定，各起一条 poller + 起自动推送循环。幂等。"""
        global _active_mgr
        self._running = True
        self._wake = asyncio.Event()
        try:
            self._loop = asyncio.get_event_loop()
        except RuntimeError:
            self._loop = None
        _active_mgr = self  # 供 notify_new_flash() 跨模块唤醒
        for b in bind.list_active():
            self.add_account(b["ilink_bot_id"])
        ap = self._tasks.get("__autopush__")
        if not (ap and not ap.done()):
            self._tasks["__autopush__"] = asyncio.create_task(self._auto_push_loop())
        ka = self._tasks.get("__keepalive__")
        if not (ka and not ka.done()):
            self._tasks["__keepalive__"] = asyncio.create_task(self._keepalive_loop())

    async def _keepalive_loop(self) -> None:
        """保活探针：每 _KEEPALIVE_INTERVAL 秒对每个「已激活(有 context_token)」绑定静默调 getconfig，
        试图维持 token 活性，使用户【无需主动发消息】也能持续收推。
        - ret==0：token 仍有效 → 刷新本地时间戳；
        - ret!=0 或异常：token 已失效 → 清掉(转冷)，待用户下次发消息重建。
        ⚠️ getconfig 对用户完全不可见(不发消息/不显示"正在输入")，低频(4min)控风控。"""
        while self._running:
            await asyncio.sleep(_KEEPALIVE_INTERVAL)
            if not self._running:
                break
            try:
                targets = [b for b in bind.list_active() if b.get("context_token") and b.get("wechat_user_id")]
                if not targets:
                    continue
                alive = dead = 0
                async with ilink.push_client() as client:
                    for b in targets:
                        if not self._running:
                            break
                        bot_id = b.get("ilink_bot_id") or ""
                        try:
                            async with self._lock_for(bot_id):  # 与发送/回复串行，不并发抢同一 token
                                resp = await ilink.get_config(
                                    b["token"], b["base_url"], b["wechat_user_id"], b["context_token"], client=client)
                            if (resp or {}).get("ret") in (0, None):
                                alive += 1
                                bind.update_context_token(bot_id, b["context_token"])  # 确认存活 → 刷新时间戳(维持活性)
                            else:
                                # ⭐加固：getconfig 单次非0 不立即清——可能是限流/服务端瞬时抖动，误清会让活 token 转冷→间歇断联。
                                # 复探 _KEEPALIVE_CONFIRM 次，全部非0 才判真死。只要有一次活，就保留。
                                confirmed_dead = True
                                ret_seen = (resp or {}).get("ret")
                                for _ in range(_KEEPALIVE_CONFIRM):
                                    await asyncio.sleep(_KEEPALIVE_CONFIRM_GAP)
                                    try:
                                        async with self._lock_for(bot_id):
                                            r2 = await ilink.get_config(
                                                b["token"], b["base_url"], b["wechat_user_id"], b["context_token"], client=client)
                                        if (r2 or {}).get("ret") in (0, None):
                                            confirmed_dead = False
                                            break
                                        ret_seen = (r2 or {}).get("ret")
                                    except Exception:  # noqa: BLE001  复探异常不算确认死亡（网络抖）
                                        confirmed_dead = False
                                        break
                                if confirmed_dead:
                                    dead += 1
                                    bind.clear_context_token(bot_id)
                                    bind.log_push_event("keepalive", "dead", bot_id, b.get("username") or "",
                                                        f"getconfig 连续 {1+_KEEPALIVE_CONFIRM} 次非0(ret={ret_seen})→判失效转冷，待用户重发激活")
                                else:
                                    alive += 1  # 复探救回，仍活
                        except Exception:  # noqa: BLE001  探针网络异常不清(可能只是抖)，下轮再探
                            pass
                        await asyncio.sleep(0.2)  # 轻错峰
                if alive or dead:
                    logger.info("[weixin] keepalive：存活 %d，失效清理 %d", alive, dead)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                logger.warning("[weixin] keepalive loop err: %s", exc)

    async def _auto_push_loop(self) -> None:
        """事件驱动 + 兜底轮询：新快讯落库即被唤醒(近实时)，否则每 _AUTO_PUSH_INTERVAL 秒兜底扫一次。
        唤醒后等 _PUSH_DEBOUNCE 秒聚合同一时刻涌入的多条，减少发送次数；
        只推给 context_token 仍有效(近期活跃)的绑定；冷用户跳过(iLink 准推送固有上限)。"""
        # 持久化游标：重启从上次推到处续推（不丢推）；长停机用 floor 兜底只补最近一段，防刷屏。
        now0 = utc_now_iso()
        floor = _iso_shift(now0, -_CURSOR_MAX_LOOKBACK)
        saved = _load_cursor()
        cursor = saved if saved else now0
        if cursor < floor:
            cursor = floor
        _save_cursor(cursor)
        wake = self._wake or asyncio.Event()
        while self._running:
            woke = True
            try:
                await asyncio.wait_for(wake.wait(), timeout=_AUTO_PUSH_INTERVAL)
            except asyncio.TimeoutError:
                woke = False  # 到点兜底（防漏/重启）
            wake.clear()
            if not self._running:
                break
            if woke:
                await asyncio.sleep(_PUSH_DEBOUNCE)  # 聚合突发的多条快讯
            try:
                cursor = await self._run_push_pass(cursor)
                _save_cursor(cursor)  # 推进后落盘：重启可续推，不丢快讯
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 单轮失败不拖垮循环
                logger.warning("[weixin] auto-push loop err: %s", exc)

    async def _run_push_pass(self, cursor: str) -> str:
        """扫一次新快讯→按各绑定订阅匹配→错峰批量推。返回推进后的 cursor。"""
        from .realtime_messages import list_realtime_messages
        # 仅推：开了推送范围(≠off) 且 会员仍有效(premium/lifetime)。过期会员自动停推。
        actives = [b for b in bind.list_active()
                   if (b.get("push_scope") or "off") != "off" and _member_can_push(b)]
        if not actives:
            return cursor
        msgs = list_realtime_messages(topic="快讯", since=cursor, limit=50)
        new_msgs = [m for m in msgs if getattr(m, "created_at", "") > cursor]
        if not new_msgs:
            return cursor
        cursor = max(getattr(m, "created_at", "") for m in new_msgs)
        new_msgs.sort(key=lambda m: getattr(m, "created_at", ""))  # 时间正序便于阅读

        # —— 组装待发任务（复用）——
        # scope=all 的推送文本对所有人完全相同 → 只格式化一次、全员复用；
        # watchlist 各自命中不同 → 按命中集合分别格式化（同命中集合的文本天然相同）。
        all_text: Optional[str] = None
        jobs: List[tuple] = []  # (binding, to, ctx, text)
        for b in actives:
            scope = b.get("push_scope") or "off"
            ctx = b.get("context_token")
            to = b.get("wechat_user_id")
            if not ctx or not to:
                # 冷用户：有新快讯却推不出去 → 记一条"漏推"，让"断联"有据可查（cursor 仍前进，这批对冷用户即丢失）。
                bind.log_push_event("push", "skipped", b.get("ilink_bot_id") or "", b.get("username") or "",
                                    f"token冷/无ctx，漏推 {len(new_msgs)} 条新快讯（待用户发消息重新激活）")
                continue
            if scope == "all":
                if all_text is None:
                    all_text = _format_push(new_msgs)  # 全量推送：一次格式化，所有人共用
                text = all_text
            else:
                matched = [m for m in new_msgs if _matches_binding(m, scope, b.get("push_symbols") or [])]
                if not matched:
                    continue
                text = _format_push(matched)
            jobs.append((b, to, ctx, text))

        if jobs:
            await self._dispatch_push(jobs)
        return cursor

    async def _dispatch_push(self, jobs: List[tuple]) -> None:
        """批量错峰发送：整批共用一条 keep-alive 连接（复用）；相邻 send 之间留间隔（错峰），
        自适应把发送摊到推送周期内，避免一次性并发尖峰触发反垃圾。"""
        # 错峰间隔：把这批发送摊到约 80% 周期内，但夹在 [MIN, MAX] 之间
        gap = min(_PUSH_GAP_MAX, max(_PUSH_GAP_MIN, (_AUTO_PUSH_INTERVAL * 0.8) / max(1, len(jobs))))
        sent = failed = 0
        async with ilink.push_client() as client:
            for i, (b, to, ctx, text) in enumerate(jobs):
                if not self._running:
                    break
                ok, ret = await _send_retry(b, to, ctx, text, client, lock=self._lock_for(b.get("ilink_bot_id") or ""))  # 重试+退避+串行锁
                bid = b.get("ilink_bot_id") or ""
                uname = b.get("username") or ""
                if ok:
                    sent += 1
                    bind.log_push_event("push", "delivered", bid, uname, f"{len(text)}字")
                elif ret in _SEND_DEAD_RETS:
                    # sendmessage 确定性拒绝(token 对发送已失效)——getconfig 说"活"是假象，以发送为准：清冷、止损、提示重发。
                    failed += 1
                    bind.clear_context_token(bid)
                    bind.log_push_event("push", "dead", bid, uname,
                                        f"sendmessage ret={ret}=context_token 失效→清冷，待用户发消息重新激活")
                else:
                    # 无响应/未知 ret：不清 token（可能只是网络抖/临时冷却），记录待观察。
                    failed += 1
                    bind.log_push_event("push", "failed", bid, uname, f"发送失败(ret={ret})，暂不清token待观察")
                if i != len(jobs) - 1:
                    await asyncio.sleep(gap)  # 错峰
        logger.info("[weixin] auto-push 批量完成：成功 %d 失败 %d（错峰间隔 %.2fs）", sent, failed, gap)

    def add_account(self, bot_id: str) -> None:
        """为一个绑定起 poller（绑定成功后即时调用，用户无感进入对话态）。"""
        if not self._running:
            self._running = True
        existing = self._tasks.get(bot_id)
        if existing and not existing.done():
            return
        self._tasks[bot_id] = asyncio.create_task(self._poll_loop(bot_id))

    async def stop(self) -> None:
        self._running = False
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        for t in tasks:
            try:
                await t
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        self._tasks.clear()

    # ---- 收：长轮询 ----

    async def _poll_loop(self, bot_id: str) -> None:
        sync_buf = ""
        backoff = _MIN_BACKOFF
        while self._running:
            b = bind.get_by_bot(bot_id)
            if not b or not b.get("active"):
                break
            try:
                resp = await ilink.get_updates(b["token"], b["base_url"], sync_buf)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # 超时（长轮询空转）/网络抖动：退避重试，不崩
                logger.debug("[weixin] getupdates %s err: %s", bot_id[:8], exc)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            errcode = resp.get("errcode")
            if errcode == _SESSION_EXPIRED:
                logger.warning("[weixin] bot %s 会话过期(errcode -14)，标记失效待重扫", bot_id[:8])
                bind.deactivate(bot_id)
                break
            if resp.get("ret") not in (0, None):
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, _MAX_BACKOFF)
                continue
            backoff = _MIN_BACKOFF
            sync_buf = resp.get("get_updates_buf") or resp.get("sync_buf") or sync_buf
            try:
                await self._handle_batch(bot_id, resp.get("msgs") or [])
            except Exception as exc:  # 单批处理失败不拖垮 poller
                logger.warning("[weixin] handle_batch %s err: %s", bot_id[:8], exc)

    async def _handle_batch(self, bot_id: str, msgs: List[Dict[str, Any]]) -> None:
        """同一轮里同一用户的连续文本合并为一次问答（对齐 RDK 分组，避免抢 context_token）。"""
        b = bind.get_by_bot(bot_id)
        if not b or not b.get("active"):
            return
        texts: List[str] = []
        to_user: Optional[str] = None
        ctx: Optional[str] = None
        any_ctx: Optional[str] = None   # 尽量保活：从【任意】事件(含 type!=1 的 bot 回显/系统事件)捡 token 刷新
        for m in msgs:
            if m.get("context_token"):
                any_ctx = m["context_token"]
            if m.get("message_type") != 1:  # 问答只处理 USER（跳过 BOT 自己发的）；但上面已捡其 token
                continue
            if not m.get("from_user_id"):
                continue
            if m.get("context_token"):
                ctx = m["context_token"]
                to_user = m["from_user_id"]
            t = ilink.extract_inbound_text(m)
            if t:
                texts.append(t)
        # 刷新缓存 token：优先用户消息的，其次任意事件里捡到的（白捡的保活机会）
        if ctx or any_ctx:
            bind.update_context_token(bot_id, ctx or any_ctx)
        if not texts or not to_user or not ctx:
            return
        question = "\n".join(texts)
        # —— 会员 AI 问答（已去 lx199710 白名单，所有在效会员可用）——
        # 推送 token 已在上面刷新，故"发消息激活推送"对所有人始终生效；问答另走下面的省 token 闸。
        from . import compliance, data_store, metrics_store

        async def _reply(text: str) -> None:
            try:
                async with self._lock_for(bot_id):  # 与推送/保活串行，不抢同一 token
                    await ilink.send_text(b["token"], b["base_url"], to_user, ctx, text)
            except Exception as exc:  # noqa: BLE001
                logger.warning("[weixin] reply err %s: %s", bot_id[:8], exc)

        q_norm = question.strip().lower()
        # ① 会员校验：AI 问答会员专享；会员过期只保留推送、问答引导续费（不耗 token）
        if not _member_can_push(b):
            await _reply(_EXPIRED_REPLY)
            return
        # ② 纯寒暄（兼推送激活确认）/ ③ 帮助菜单：友好引导，不触发 agent、不计次
        if q_norm in _GREETINGS:
            await _reply(_GREETING_REPLY)
            return
        if q_norm in _HELP_TRIGGERS:
            await _reply(_HELP_REPLY)
            return

        # ④ 答案缓存跨用户复用：命中即秒回，0 token、不计次（同一问题跨用户安全共享，见 _qa_fingerprint）
        fp = _qa_fingerprint(question)
        if fp:
            cached = data_store.latest("wx_qa", fp, max_age_seconds=_QA_CACHE_TTL)
            if isinstance(cached, dict) and (cached.get("answer") or "").strip():
                metrics_store.incr("wxqa:cache_hit")  # 观测:缓存命中(0 token)
                await _reply(compliance.neutralize_text(cached["answer"].strip()))
                return

        # ⑤ 每日"现算"配额：仅缓存未命中才计次；超额 → 引导明天 / 问推荐问题，不耗 token
        uname = (b.get("username") or "").strip().lower()
        qkey = f"q:wxqa:{uname}" if uname else f"q:wxqa:bot:{bot_id}"
        if metrics_store.get_daily(qkey) >= _QA_DAILY_LIMIT:
            await _reply(_QUOTA_REPLY)
            return

        # ⑥ 现算：计次 → 调 agent（耗 token）→ 合规中性化 → 回 → 写缓存供后续复用
        metrics_store.incr(qkey)
        metrics_store.incr("wxqa:fresh")  # 观测:现算次数(配 wxqa:cache_hit 看命中率)
        hint = ""
        if self._context_hint_fn:
            try:
                hint = self._context_hint_fn(b) or ""
            except Exception:  # noqa: BLE001
                hint = ""
        try:
            answer = await self._agent_fn(question, hint)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[weixin] agent err for %s: %s", bot_id[:8], exc)
            answer = None
        ans = (answer or "").strip()
        if ans and fp:
            data_store.record("wx_qa", fp, {"answer": ans, "q": question[:200]})
        await _reply(compliance.neutralize_text(ans) if ans else _FALLBACK_REPLY)

    # ---- 准推送：best-effort flush ----

    async def quasi_push(self, text: str, fresh_within_seconds: Optional[int] = None) -> Dict[str, int]:
        """对 active 绑定用各自缓存的 context_token 主动发一条。
        token 失效/无 ctx → 跳过（不报错）；fresh_within_seconds 可只推近期活跃者。
        返回 {delivered, skipped, failed}。⚠️ 低频用，高频群发触反垃圾红线。"""
        delivered = skipped = failed = 0
        now = utc_now_iso()
        for b in bind.list_active():
            ctx = b.get("context_token")
            to = b.get("wechat_user_id")
            if not ctx or not to:
                skipped += 1
                continue
            if fresh_within_seconds is not None and not _within(b.get("context_token_at"), now, fresh_within_seconds):
                skipped += 1
                continue
            ok, ret = await _send_retry(b, to, ctx, text, lock=self._lock_for(b.get("ilink_bot_id") or ""))  # 重试+退避+串行锁
            if ok:
                delivered += 1
            else:
                failed += 1
                if ret in _SEND_DEAD_RETS:  # 手动推也遇 token 失效 → 清冷，与自动推一致
                    bind.clear_context_token(b.get("ilink_bot_id") or "")
        return {"delivered": delivered, "skipped": skipped, "failed": failed}


def _within(then_iso: Optional[str], now_iso: str, seconds: int) -> bool:
    """then 距 now 是否在 seconds 内（ISO 字符串比较，容错失败即视为过期）。"""
    if not then_iso:
        return False
    try:
        from datetime import datetime
        t0 = datetime.fromisoformat(then_iso.replace("Z", "+00:00"))
        t1 = datetime.fromisoformat(now_iso.replace("Z", "+00:00"))
        return (t1 - t0).total_seconds() <= seconds
    except Exception:
        return False
