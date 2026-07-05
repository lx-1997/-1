from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, List, Optional

from . import weixin_bind as bind
from . import weixin_ilink as ilink
from .shared_utils import utc_now_iso
from .stock_name_index import mentions_specific_stock

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
# multiview_fn(question, base_answer) -> 多空深度档文本 or None（争议个股第二条分阶段推送）
MultiviewFn = Callable[[str, str], Awaitable[Optional[str]]]

# 争议个股「多空深度档」：默认关，灰度时置 1 开启。基础答案先到，深度档作为第二条追加推送，
# 不顶撞微信单条响应超时；只对【锁定具体个股 + 决策/争议措辞】的独立提问触发(预计 5-8%)，控成本。
_MULTIVIEW_ENABLED = os.getenv("DEEPFOCUS_WEIXIN_MULTIVIEW", "0") not in ("0", "false", "False", "no")
_MULTIVIEW_DECISION_RE = re.compile(
    r"能不能买|能买|可以买|值不值|值得|要不要|该不该|怎么看|前景|后市|还能(拿|持|涨|追)|"
    r"加仓|减仓|清仓|抄底|上车|看多|看空|多还是空|分歧|争议|是涨是跌|会涨|会跌|风险大不大"
)

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
# 省 token 两板斧：①答案缓存跨用户复用（命中 0 token、不计次）②推荐问题引导（把流量收敛到高命中问题）。
# 每日"现算"配额已放开（_QA_DAILY_LIMIT<=0 = 不限次）：会员是付费用户、与终端 AI 问答口径一致；缓存仍兜大头成本，
# 真遇滥用可随时设 env DEEPFOCUS_WEIXIN_QA_DAILY=N 重新封顶（计数照常打点，看 wxqa:fresh）。
_QA_DAILY_LIMIT = int(os.getenv("DEEPFOCUS_WEIXIN_QA_DAILY", "0") or 0)              # 每会员每天"现算"问答上限；<=0 = 不限次（默认放开）。缓存命中本就不计入
_QA_UNLIMITED = _QA_DAILY_LIMIT <= 0
_QA_CACHE_TTL = float(os.getenv("DEEPFOCUS_WEIXIN_QA_CACHE_TTL", "1800") or 1800)    # 答案缓存有效期（秒），默认 30min 兼顾行情时效
_QA_AGENT_TIMEOUT = float(os.getenv("DEEPFOCUS_WEIXIN_QA_TIMEOUT", "60") or 60)      # run_tool_agent 每轮 LLM 超时(秒)；个股问答需多轮取数，默认30s 易超时返空→回兜底，放宽到 60s
# ⭐慢回复即时安抚（前沿对话体验：别让用户对着空白等十几秒）：现算可能要多轮取数，超过该阈值仍没结果，
# 就先秒回一条「正在查询，请稍候」让用户安心，答案算好再发正式回复。阈值内返回的快答不发安抚（不 double 消息），
# 只在真的慢时才安抚——既消解等待焦虑、又不刷屏。
_WX_THINKING_DELAY = float(os.getenv("DEEPFOCUS_WEIXIN_THINKING_DELAY", "4.5") or 4.5)
# 轻量会话上下文：每用户记最近几轮(短窗口)，让"反问→追答"能接上、像真助手。窗口内的追问绕开跨用户缓存(答案跟上文相关)。
_WX_CTX_TTL = float(os.getenv("DEEPFOCUS_WEIXIN_CTX_TTL", "600") or 600)             # 会话上下文窗口(秒)，默认 10min；超时即视作新对话
_WX_CTX_TURNS = int(os.getenv("DEEPFOCUS_WEIXIN_CTX_TURNS", "3") or 3)               # 记忆最近几轮(问+答)
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
    "💡 回复「同步自选」把终端自选股设为推送范围（只推你的股）\n"
    + ("（会员畅聊不限次，常见问题秒回 😉）" if _QA_UNLIMITED
       else f"（会员每天 {_QA_DAILY_LIMIT} 条 AI 问答；常见问题命中缓存时不计次 😉）")
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
_PUBLIC_SITE = (os.getenv("DEEPFOCUS_PUBLIC_BASE_URL", "https://daocaijing.com") or "https://daocaijing.com").rstrip("/")
# ⭐非会员试吃：微信是唯一能天天触达免费用户的自有渠道，一刀切拒绝把最好的转化面锁死在墙内。
# 每天 _WX_FREE_QA 次现算问答(缓存命中不计次、0成本放行)；超额回复带自助购买链接——
# "额度用完"是发生在聊天窗里的天然升级时机。env 设 0 回到会员专享。
_WX_FREE_QA = int(os.getenv("DEEPFOCUS_WEIXIN_FREE_QA", "2") or 2)
_NONMEMBER_QUOTA_REPLY = (
    f"今天的免费 AI 问答（{_WX_FREE_QA} 次/天）用完啦 🙏\n"
    f"开通会员：AI 问答不限次 + 全量快讯推送 + 每日复盘\n"
    f"👉 {_PUBLIC_SITE}（支持自助下单、秒级开通）"
)
_NONTEXT_REPLY = (
    "我暂时只能看懂文字消息～\n"
    "请打字告诉我你想问哪只股票或什么事 😊（如：贵州茅台怎么样）"
)
_EXPIRED_NOTICE = (
    "你的 DeepFocus 会员已到期，快讯推送已暂停 🙏\n"
    f"续费即恢复全量推送与 AI 问答不限次 👉 {_PUBLIC_SITE}（支持自助下单、秒级开通）\n"
    "（本条为服务状态通知，不构成投资建议）"
)
# 行情类提问识别：盘中命中 → 缓存 TTL 收紧到 120s + 答案带"截至 HH:MM"，
# 防止微信盘中把 29 分钟前别人触发的旧价格当现价发出去（渠道层自己造的"数据不准"）。
_MARKET_Q_RE = re.compile(r"现价|多少钱|报价|股价|价格|涨了|跌了|涨没|跌没|涨幅|跌幅|涨多少|跌多少|多少点")
_GREETING_REPLY = (
    "你好 👋 我是 DeepFocus 投研助手。\n"
    "✅ 快讯推送已激活，有重要快讯我会主动推给你。\n"
    "💡 回复「同步自选」把终端自选股设为推送范围（只推你的股）\n"
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


def _sync_watchlist_to_push(b: Dict[str, Any]) -> str:
    """「同步自选」指令：把终端自选股（服务端 user_prefs）设为该绑定的推送范围。
    push_symbols 同时写入 代码+名称（匹配逻辑是代码精确 OR 关键词命中标题/正文，快讯标题里是名称）。"""
    uid = (b.get("deepfocus_user_id") or "").strip()
    if not uid:
        return "未找到绑定的终端账号，请在网站重新绑定后再试"
    try:
        from . import user_prefs
        wl = user_prefs.get_watchlist(uid) or {}
    except Exception:  # noqa: BLE001
        wl = {}
    symbols = list(wl.get("symbols") or [])
    names = wl.get("names") or {}
    if not symbols:
        return f"你的终端自选股还是空的——先去 {_PUBLIC_SITE} 添加自选，再回复「同步自选」即可"
    keys: List[str] = []
    for s in symbols:
        keys.append(str(s))
        n = str(names.get(s) or "").strip()
        if n and n not in keys:
            keys.append(n)
    bind.set_push_config(uid, "watchlist", keys)
    show = "、".join(str(names.get(s) or s) for s in symbols[:8])
    more = f" 等 {len(symbols)} 只" if len(symbols) > 8 else ""
    return (f"✅ 已把你的自选股设为推送范围：{show}{more}\n"
            "之后只推与它们相关的快讯；回复「推送全部」改回全量，回复「关闭推送」暂停。")


# ⭐会员状态转移检测（到期断流通知）：推送停止的瞬间是用户对产品价值感知最清晰的时刻，
# 静默断流=静默流失。用 JSON 状态文件记录每个绑定上一轮的会员判定，true→false 即发一次性
# 服务状态通知（发送成功才落状态，token 冷则下轮重试）。
_MEMBER_STATE_FILE = Path(os.getenv("DEEPFOCUS_WX_MEMBER_STATE_FILE",
                                    str(Path(__file__).resolve().parents[1] / ".wx_member_state.json")))


def _load_member_state() -> Dict[str, bool]:
    try:
        if _MEMBER_STATE_FILE.exists():
            data = json.loads(_MEMBER_STATE_FILE.read_text(encoding="utf-8") or "{}")
            if isinstance(data, dict):
                return {str(k): bool(v) for k, v in data.items()}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _save_member_state(state: Dict[str, bool]) -> None:
    try:
        _MEMBER_STATE_FILE.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _in_cn_session_now() -> bool:
    """A股盘中(北京时间 工作日 09:15-15:05)。轻量判定，不判节假日——误判成盘中只是缓存短一点，无正确性代价。"""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    now = _dt.now(_tz(_td(hours=8)))
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    return 9 * 60 + 15 <= hm <= 15 * 60 + 5


def _qa_cache_ttl(question: str) -> float:
    """行情类提问盘中缓存收紧到 120s（防旧价格当现价）；其余/休市维持默认（收盘价不动，缓存收益最大）。"""
    if _MARKET_Q_RE.search(question or "") and _in_cn_session_now():
        return min(_QA_CACHE_TTL, 120.0)
    return _QA_CACHE_TTL


def _format_daily(m: Any) -> str:
    """晨报/复盘的微信推送文本：标题+正文+深链。内容生成侧已带 AI 标识与中性化。"""
    title = (getattr(m, "title", "") or "").strip()
    content = (getattr(m, "content", "") or "").strip()
    url = (getattr(m, "url", "") or "").strip()
    link = (_PUBLIC_SITE + url) if url.startswith("/") else (url or _PUBLIC_SITE)
    return f"{title}\n{content}\n👉 {link}\n（内容不构成投资建议）"


def _qa_fingerprint(question: str) -> str:
    """归一化问题 → MD5 指纹（答案缓存键）：去首尾/压缩内部空白、去尾部标点、小写。空问题→''（不缓存）。
    ⭐微信问答无个人上下文（hint 恒空，见 main.py 构造处不传 context_hint_fn），故同一问题跨用户可安全共享答案。"""
    q = re.sub(r"\s+", " ", (question or "").strip().lower()).rstrip("?？!！。.,，、 ")
    return hashlib.md5(q.encode("utf-8")).hexdigest() if q else ""


# ===== 轻量会话上下文记忆(每用户最近几轮,短窗口)=====
def _load_ctx(cid: str) -> list:
    """取该用户窗口内最近几轮对话；超时/无则空。失败安静返回 []。"""
    if not cid:
        return []
    try:
        from . import data_store
        rec = data_store.latest("wx_ctx", cid, max_age_seconds=_WX_CTX_TTL)
        turns = (rec.get("turns") if isinstance(rec, dict) else None) or []
        return turns if isinstance(turns, list) else []
    except Exception:  # noqa: BLE001
        return []


def _remember(cid: str, q: str, a: str) -> None:
    """把这一轮(问+答)追加进该用户的会话记忆,只留最近 _WX_CTX_TURNS 轮。失败不影响主流程。"""
    if not cid or not (q or "").strip() or not (a or "").strip():
        return
    try:
        from . import data_store
        turns = _load_ctx(cid)
        turns = (turns + [{"q": q[:200], "a": a[:400]}])[-_WX_CTX_TURNS:]
        data_store.record("wx_ctx", cid, {"turns": turns})
    except Exception:  # noqa: BLE001
        pass


def _ctx_hint(turns: list) -> str:
    """把最近几轮拼成喂给 agent 的上下文前缀(run_tool_agent 会拼在当前问题前)；助手长答截断省 token。"""
    lines: list = []
    for t in (turns or [])[-_WX_CTX_TURNS:]:
        if not isinstance(t, dict):
            continue
        if (t.get("q") or "").strip():
            lines.append(f"用户: {t['q']}")
        if (t.get("a") or "").strip():
            lines.append(f"助手: {str(t['a'])[:300]}")
    if not lines:
        return ""
    return ("【最近对话(仅用于理解用户当前这条追问的上下文;不要重复回答历史问题)】\n"
            + "\n".join(lines) + "\n\n用户当前消息：")


# 多数答案一条发完(门槛设高);只有超长才分段,且【只在小节(一、二、…)边界断,绝不拆散标题与正文】。
_WX_MSG_LIMIT = int(os.getenv("DEEPFOCUS_WEIXIN_MSG_LIMIT", "1500") or 1500)
_REPLY_CHUNK_GAP = 0.6  # 相邻分段消息间隔(秒),错峰、保顺序


def _fine_split(text: str, limit: int) -> list[str]:
    """超长单元兜底切分:按行→句末标点→无标点硬切,再贪心合并(仅在单节本身就超长时才会用到)。"""
    s = (text or "").strip()
    if len(s) <= limit:
        return [s] if s else []
    pieces: list[str] = []
    for para in s.split("\n"):
        if len(para) <= limit:
            pieces.append(para)
            continue
        for seg in re.split(r"(?<=[。；;!！?？])", para):
            if not seg:
                continue
            while len(seg) > limit:
                pieces.append(seg[:limit]); seg = seg[limit:]
            if seg:
                pieces.append(seg)
    chunks: list[str] = []
    cur = ""
    for part in pieces:
        add = ("\n" + part) if cur else part
        if cur and len(cur) + len(add) > limit:
            chunks.append(cur); cur = part
        else:
            cur += add
    if cur:
        chunks.append(cur)
    return chunks


def _split_for_wechat(text: str, limit: int = 0) -> list[str]:
    """短答案单条原样发;超长才分段,且**只在顶层小节标题(一、二、…)边界断**,每段含完整小节(标题+正文),
    绝不把标题与正文拆开。无小节结构的超长答案退回 _fine_split。"""
    limit = limit or _WX_MSG_LIMIT
    s = (text or "").strip()
    if len(s) <= limit:
        return [s] if s else []
    # 在"一、/二、/…"小节标题前断(容忍前导 ** 或空格);首段是引言
    sections = re.split(r"\n(?=\s*\*{0,2}[一二三四五六七八九十]+、)", s)
    units: list[str] = []
    for sec in sections:
        sec = sec.strip("\n")
        if not sec:
            continue
        units.extend([sec] if len(sec) <= limit else _fine_split(sec, limit))
    chunks: list[str] = []
    cur = ""
    for u in units:
        sep = "\n\n" if cur else ""
        if cur and len(cur) + len(sep) + len(u) > limit:
            chunks.append(cur); cur = u
        else:
            cur += sep + u
    if cur:
        chunks.append(cur)
    return chunks


# 大盘/复盘类高频问:命中且"今日复盘已生成"→直出我们已算好的复盘(0 LLM、秒回、用自有差异化内容);
# 否则落到实时 agent(防盘前/周末返回隔夜数据)。短问 + 不含代码,避免误伤夹个股的长问。
_MARKET_Q_KEYS = ("大盘", "复盘", "盘面", "收盘", "今日行情", "今天行情", "今天股市", "今日股市", "晨报", "今天行情如何")
# ⚠️只对"给我大盘概况"这种纯泛问直出;带【因果/分析/预测/事件/操作】意图的具体问题必须交给真 agent
# (否则"今天大盘为啥暴跌"会被关键词「大盘」截成甩一份复盘=伪 AI,既不答"为什么"也不纠正前提)。
_MARKET_Q_EXCLUDE = (
    "为什么", "为啥", "为何", "原因", "怎么回事", "啥情况", "咋", "怎么搞",
    "分析", "解读", "怎么看", "如何看", "怎样看", "点评", "看法",
    "会不会", "能不能", "要不要", "该不该", "可不可以", "是不是",
    "预测", "后市", "明天", "明日", "接下来", "未来", "下周", "后面", "之后", "走势",
    "暴跌", "暴涨", "大跌", "大涨", "跳水", "闪崩", "崩盘", "重挫", "跳空",
    "影响", "利好", "利空", "买", "卖", "加仓", "减仓", "操作", "建议", "机会",
)


def _is_market_overview_q(question: str) -> bool:
    s = (question or "").strip()
    if not s or len(s) > 14 or any(c.isdigit() for c in s):  # 太长或含代码→可能夹个股,不直出
        return False
    if any(k in s for k in _MARKET_Q_EXCLUDE):  # 有因果/分析/预测/事件/操作意图 → 不直出,交给真 agent
        return False
    return any(k in s for k in _MARKET_Q_KEYS)


def _market_overview_reply() -> Optional[str]:
    """今日复盘已生成 → 渲染成微信答案(0 LLM);未生成/非当日 → None(让实时 agent 接管,防隔夜数据)。"""
    try:
        from . import ashare_review
        rv = ashare_review.latest_review()
        if not rv or rv.get("date") != ashare_review.cn_today_str():
            return None
        nar = rv.get("narrative") or {}
        lines = [f"📊 {rv.get('session_label') or '大盘复盘'}（{rv.get('date')}）"]
        if (nar.get("one_liner") or "").strip():
            lines.append(nar["one_liner"].strip()[:160])
        for label, key in (("大盘", "market"), ("板块", "sectors"), ("资金", "funds")):
            v = (nar.get(key) or "").strip()
            if v:
                lines.append(f"· {label}：{v[:140]}")
        edges = [(e.get("title") or e.get("name") or "").strip()
                 for e in (rv.get("our_edge") or [])[:5]
                 if isinstance(e, dict) and (e.get("title") or e.get("name"))]
        if edges:
            lines.append("🔎 我们提前发现：" + "；".join(edges))
        return "\n".join(lines)
    except Exception:  # noqa: BLE001  复盘读取失败 → None 落到实时 agent
        return None


def _is_controversial_stock_q(question: str) -> bool:
    """是否「锁定具体个股 + 决策/争议措辞」——这类独立提问才值得多花一轮多空深度档。"""
    q = question or ""
    return bool(mentions_specific_stock(q)) and bool(_MULTIVIEW_DECISION_RE.search(q))


def _thinking_message(question: str) -> str:
    """回复较慢时先秒回的「正在处理」安抚语；按问题类型给更贴切的措辞，让用户知道我们正在为他真取数。"""
    q = question or ""
    if mentions_specific_stock(q):
        return "🤔 收到，正在为你调取这只票的实时行情和最新动态，稍等十几秒…"
    if any(k in q for k in _MARKET_Q_KEYS):
        return "🤔 收到，正在汇总最新盘面 / 快讯，请稍候…"
    return "🤔 收到，正在为你深度分析，请稍候片刻…"


# 微信问答多给几轮工具：个股研判常需 resolve_symbol(解析代码)→行情→估值→财报→收敛，4 轮易截断；
# 6 轮留足"先解析代码再逐项取数"的空间(慢回复有即时安抚兜着，不怕多花十几秒)。
_QA_MAX_ROUNDS = int(os.getenv("DEEPFOCUS_WEIXIN_QA_MAX_ROUNDS", "6") or 6)


def make_agent_fn(llm: Any) -> AgentFn:
    """把 CloudResearchLLM.run_tool_agent 适配成 AgentFn（返回 answer 文本或 None）。"""
    async def _agent(question: str, hint: str) -> Optional[str]:
        result = await llm.run_tool_agent(
            question=question, context_hint=hint,
            timeout_seconds=_QA_AGENT_TIMEOUT, max_rounds=_QA_MAX_ROUNDS,
        )
        if result and (result.get("answer") or "").strip():
            return result["answer"]
        return None
    return _agent


def make_multiview_fn(llm: Any) -> MultiviewFn:
    """把 CloudResearchLLM.synthesize_multiview 适配成 MultiviewFn（返回深度档文本或 None）。"""
    async def _mv(question: str, base_answer: str) -> Optional[str]:
        return await llm.synthesize_multiview(question, base_answer)
    return _mv


class WeixinChannelManager:
    def __init__(self, agent_fn: AgentFn, context_hint_fn: Optional[HintFn] = None,
                 multiview_fn: Optional[MultiviewFn] = None) -> None:
        self._agent_fn = agent_fn
        self._context_hint_fn = context_hint_fn
        self._multiview_fn = multiview_fn   # 争议个股多空深度档（None=不深化；灰度开启时注入）
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
        """扫一次新消息→按各绑定订阅匹配→错峰批量推。返回推进后的 cursor。

        覆盖三类 topic：快讯（会员、按订阅范围）、晨报（全体绑定含非会员——非会员试吃层的每日一条）、
        复盘（会员）。晨报/复盘是一日一次的策划摘要，"每天早 8:30 / 下午 3:35 准时见"的仪式感是
        微信通道从碎片快讯升级成每日回访钩子的关键，内容已生成、零边际成本。"""
        from .realtime_messages import list_realtime_messages
        opened = [b for b in bind.list_active() if (b.get("push_scope") or "off") != "off"]
        if not opened:
            return cursor
        # 到期断流通知：上一轮有推送资格、本轮失格 → 发一次性服务状态通知（续费意愿最高的时刻）
        try:
            await self._notify_lapsed(opened)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[weixin] lapsed notify err: %s", exc)
        actives = [b for b in opened if _member_can_push(b)]
        msgs = list_realtime_messages(topic="快讯", since=cursor, limit=50)
        new_msgs = [m for m in msgs if getattr(m, "created_at", "") > cursor]
        specials: List[Any] = []  # 晨报/复盘/异动（低频，每 topic 最多几条）
        for _topic in ("晨报", "复盘", "异动"):
            try:
                for m in list_realtime_messages(topic=_topic, since=cursor, limit=3):
                    if getattr(m, "created_at", "") > cursor:
                        specials.append(m)
            except Exception:  # noqa: BLE001
                pass
        if not new_msgs and not specials:
            return cursor
        cursor = max(getattr(m, "created_at", "") for m in (new_msgs + specials))
        new_msgs.sort(key=lambda m: getattr(m, "created_at", ""))  # 时间正序便于阅读
        specials.sort(key=lambda m: getattr(m, "created_at", ""))

        # —— 组装待发任务（复用）——
        # scope=all 的推送文本对所有人完全相同 → 只格式化一次、全员复用；
        # watchlist 各自命中不同 → 按命中集合分别格式化（同命中集合的文本天然相同）。
        all_text: Optional[str] = None
        jobs: List[tuple] = []  # (binding, to, ctx, text)
        for b in opened:
            is_member = b in actives
            scope = b.get("push_scope") or "off"
            ctx = b.get("context_token")
            to = b.get("wechat_user_id")
            if not ctx or not to:
                if new_msgs and is_member:
                    # 冷用户：有新快讯却推不出去 → 记一条"漏推"，让"断联"有据可查（cursor 仍前进，这批对冷用户即丢失）。
                    bind.log_push_event("push", "skipped", b.get("ilink_bot_id") or "", b.get("username") or "",
                                        f"token冷/无ctx，漏推 {len(new_msgs)} 条新快讯（待用户发消息重新激活）")
                continue
            # ① 快讯：会员按订阅范围
            if new_msgs and is_member:
                if scope == "all":
                    if all_text is None:
                        all_text = _format_push(new_msgs)  # 全量推送：一次格式化，所有人共用
                    jobs.append((b, to, ctx, all_text))
                else:
                    matched = [m for m in new_msgs if _matches_binding(m, scope, b.get("push_symbols") or [])]
                    if matched:
                        jobs.append((b, to, ctx, _format_push(matched)))
            # ② 晨报（全体绑定，非会员也推——试吃层的每日价值锚）/ 复盘·异动（会员）
            for m in specials:
                topic = getattr(m, "topic", "") or ""
                if topic in ("复盘", "异动") and not is_member:
                    continue
                jobs.append((b, to, ctx, _format_daily(m)))

        if jobs:
            await self._dispatch_push(jobs)
        return cursor

    async def _notify_lapsed(self, opened: List[Dict[str, Any]]) -> None:
        """会员 true→false 状态转移 → 发一次性到期断流通知。发送成功才落状态（token 冷下轮重试）。"""
        state = _load_member_state()
        changed = False
        for b in opened:
            uname = (b.get("username") or "").strip().lower()
            if not uname:
                continue
            cur = _member_can_push(b)
            prev = state.get(uname)
            if prev is None or cur:  # 首见 / 仍是会员：只记录
                if state.get(uname) != cur:
                    state[uname] = cur
                    changed = True
                continue
            if prev and not cur:  # 有资格→失格：通知一次
                ctx = b.get("context_token")
                to = b.get("wechat_user_id")
                if not ctx or not to:
                    continue  # token 冷：状态不动，下轮再试
                ok, _ret = await _send_retry(b, to, ctx, _EXPIRED_NOTICE,
                                             lock=self._lock_for(b.get("ilink_bot_id") or ""))
                if ok:
                    state[uname] = False
                    changed = True
                    bind.log_push_event("push", "delivered", b.get("ilink_bot_id") or "", uname, "会员到期断流通知")
        if changed:
            _save_member_state(state)

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
        saw_nontext = False             # 用户发了语音/图片等非文本(提取不出文字)
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
            else:
                saw_nontext = True
        # 刷新缓存 token：优先用户消息的，其次任意事件里捡到的（白捡的保活机会）
        if ctx or any_ctx:
            bind.update_context_token(bot_id, ctx or any_ctx)
        if not texts or not to_user or not ctx:
            # 语音/图片被静默吞掉是聊天机器人最差的失败模式(用户会判定"这个号是死的")→给一句兜底(不计次、不进缓存)
            if saw_nontext and to_user and ctx:
                try:
                    async with self._lock_for(bot_id):
                        await ilink.send_text(b["token"], b["base_url"], to_user, ctx, _NONTEXT_REPLY)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("[weixin] nontext reply err %s: %s", bot_id[:8], exc)
            return
        question = "\n".join(texts)
        # —— 会员 AI 问答（已去 lx199710 白名单，所有在效会员可用）——
        # 推送 token 已在上面刷新，故"发消息激活推送"对所有人始终生效；问答另走下面的省 token 闸。
        from . import compliance, data_store, metrics_store, privacy_guard

        def _clean(text: str) -> str:
            # 出口三护栏:合规中性化(荐股措辞)→泄密扫描(剥数据源/工具名/密钥)→AI 生成显式标识(《标识办法》硬要求,幂等)
            return compliance.ai_label(privacy_guard.scrub_internal_text(compliance.neutralize_text(text)), brief=True)

        async def _reply(text: str) -> None:
            chunks = _split_for_wechat(text) or [text]
            try:
                async with self._lock_for(bot_id):  # 与推送/保活串行，不抢同一 token
                    for i, ck in enumerate(chunks):
                        await ilink.send_text(b["token"], b["base_url"], to_user, ctx, ck)
                        if i != len(chunks) - 1:
                            await asyncio.sleep(_REPLY_CHUNK_GAP)  # 分段连发,保顺序+错峰
            except Exception as exc:  # noqa: BLE001
                logger.warning("[weixin] reply err %s: %s", bot_id[:8], exc)

        q_norm = question.strip().lower()
        # ⓪ 确定性指令（不耗 token、对全体绑定用户开放）：推送范围管理
        if q_norm in ("同步自选", "同步自选股"):
            await _reply(_sync_watchlist_to_push(b))
            return
        if q_norm in ("推送全部", "全部推送"):
            uid0 = (b.get("deepfocus_user_id") or "").strip()
            if uid0:
                bind.set_push_config(uid0, "all", [])
                await _reply("✅ 已切换为全量快讯推送。回复「同步自选」可只推自选相关。")
            else:
                await _reply("未找到绑定的终端账号，请在网站重新绑定后再试")
            return
        if q_norm in ("关闭推送", "停止推送", "取消推送"):
            uid0 = (b.get("deepfocus_user_id") or "").strip()
            if uid0:
                bind.set_push_config(uid0, "off", list(b.get("push_symbols") or []))
                await _reply("✅ 已暂停推送。回复「推送全部」或「同步自选」随时恢复。")
            else:
                await _reply("未找到绑定的终端账号，请在网站重新绑定后再试")
            return
        # ② 纯寒暄（兼推送激活确认）/ ③ 帮助菜单：友好引导，不触发 agent、不计次（对非会员也开放）
        if q_norm in _GREETINGS:
            await _reply(_GREETING_REPLY)
            return
        if q_norm in _HELP_TRIGGERS:
            await _reply(_HELP_REPLY)
            return
        # ① 会员/试吃闸：非会员每天 _WX_FREE_QA 次现算试吃（缓存命中不计次），超额引导自助开通；
        #    env DEEPFOCUS_WEIXIN_FREE_QA=0 回到会员专享一刀切。
        is_member = _member_can_push(b)
        if not is_member and _WX_FREE_QA <= 0:
            await _reply(_EXPIRED_REPLY)
            return

        # 收集真实提问到运营看板(/api/metrics/dashboard「操作流水」:谁/何时/问了什么),失败不影响主流程
        _uname = (b.get("username") or "").strip()
        try:
            metrics_store.log_activity(actor_kind="weixin", actor_id=(_uname or bot_id),
                                       actor_name=(_uname or "微信用户"), action="weixin_qa",
                                       target=question, device="mobile")
        except Exception:  # noqa: BLE001
            pass

        # 会话记忆:取该用户窗口内最近几轮。有上下文(正在多轮对话中)→ 追问绕开跨用户缓存(答案跟上文相关),交给 agent 带上下文作答。
        cid = (b.get("username") or "").strip().lower() or bot_id
        recent_turns = _load_ctx(cid)
        has_ctx = bool(recent_turns)

        # ③.5 大盘/复盘高频问:今日复盘已生成→直出已算结论(0 token、秒回、用自有复盘);未生成→落到实时 agent
        if _is_market_overview_q(question):
            ov = _market_overview_reply()
            if ov:
                metrics_store.incr("wxqa:overview_hit")  # 观测:大盘直出命中(0 token)
                await _reply(_clean(ov))
                _remember(cid, question, ov)  # 记进会话,后续"为啥跌"等追问能接上
                return

        # ④ 答案缓存跨用户复用：命中即秒回，0 token、不计次（同一问题跨用户安全共享，见 _qa_fingerprint）
        # ⚠️仅【无会话上下文】才用共享缓存：多轮追问的答案跟个人上文相关,不能给别人(也不该被别人的缓存覆盖)。
        fp = _qa_fingerprint(question)
        if fp and not has_ctx:
            # 行情类提问盘中只认 120s 内的缓存（防把 29 分钟前别人触发的旧价格当现价发出去）
            cached = data_store.latest("wx_qa", fp, max_age_seconds=_qa_cache_ttl(question))
            if isinstance(cached, dict) and (cached.get("answer") or "").strip():
                metrics_store.incr("wxqa:cache_hit")  # 观测:缓存命中(0 token)
                await _reply(_clean(cached["answer"].strip()))  # 缓存答案也过出口护栏(防旧缓存泄密)
                _remember(cid, question, cached["answer"].strip())
                return

        # ⑤ 每日"现算"配额：仅缓存未命中才计次。会员已放开(_QA_UNLIMITED)→不拦截仅观测；
        #    非会员走试吃配额（_WX_FREE_QA 次/天），超额回复带自助购买链接（聊天窗里的天然升级时机）。
        uname = (b.get("username") or "").strip().lower()
        if is_member:
            qkey = f"q:wxqa:{uname}" if uname else f"q:wxqa:bot:{bot_id}"
            if _QA_DAILY_LIMIT > 0 and metrics_store.get_daily(qkey) >= _QA_DAILY_LIMIT:
                await _reply(_QUOTA_REPLY)
                return
        else:
            qkey = f"q:wxfree:{uname}" if uname else f"q:wxfree:bot:{bot_id}"
            if metrics_store.get_daily(qkey) >= _WX_FREE_QA:
                await _reply(_NONMEMBER_QUOTA_REPLY)
                return

        # ⑥ 现算：计次 → 调 agent（耗 token）→ 合规中性化 → 回 → 写缓存供后续复用
        metrics_store.incr(qkey)
        metrics_store.incr("wxqa:fresh")  # 观测:现算次数(配 wxqa:cache_hit 看命中率)
        # 上下文优先:有最近对话→把历史拼进 hint(run_tool_agent 会拼在当前问题前),让"反问→追答"能接上;否则用既有 hint 注入(prod 恒空)
        hint = _ctx_hint(recent_turns)
        if not hint and self._context_hint_fn:
            try:
                hint = self._context_hint_fn(b) or ""
            except Exception:  # noqa: BLE001
                hint = ""
        # 慢回复即时安抚：起 agent 任务，若 _WX_THINKING_DELAY 秒内还没出结果，先秒回一条「正在查询」让用户安心，
        # 再继续等真正的答案（asyncio.wait 超时不取消任务，agent 继续在后台跑）。安抚发送失败不影响正式回复。
        answer = None
        # 把当前用户名注入 ContextVar，让 get_my_watchlist 能读"本人自选股"(任务在此上下文创建即捕获)。
        from . import agent_tools as _agent_tools
        _bind_tok = _agent_tools._BINDING_USER.set((b.get("username") or "").strip())
        try:
            agent_task = asyncio.ensure_future(self._agent_fn(question, hint))
            done, _pending = await asyncio.wait({agent_task}, timeout=_WX_THINKING_DELAY)
            if not done:
                try:
                    await _reply(_thinking_message(question))
                    metrics_store.incr("wxqa:thinking_sent")  # 观测：安抚发送次数（配 wxqa:fresh 看慢回复占比）
                except Exception:  # noqa: BLE001  安抚失败不影响主流程
                    pass
            answer = await agent_task
        except Exception as exc:  # noqa: BLE001
            logger.warning("[weixin] agent err for %s: %s", bot_id[:8], exc)
            answer = None
        finally:
            _agent_tools._BINDING_USER.reset(_bind_tok)
        ans = (answer or "").strip()
        if ans and _MARKET_Q_RE.search(question):
            # 行情类答案打时间戳：现算时刻即数据时刻，缓存回放也带着原始时间——诚实标注新鲜度
            from datetime import datetime as _dt, timedelta as _td, timezone as _tz
            ans += f"\n（行情数据截至 {_dt.now(_tz(_td(hours=8))).strftime('%H:%M')} 北京时间）"
        if ans and not has_ctx:
            # 确定性追问引导（零 token）：把单发问答变成多轮研究会话，顺带展示更深的工具面
            try:
                from .stock_name_index import resolve_to_code as _rtc
                if _rtc(question):
                    ans += "\n\n还可以问我：它现在估值贵不贵？· 最近有没有上龙虎榜？· 历年分红怎么样？"
            except Exception:  # noqa: BLE001
                pass
        if ans and fp and not has_ctx:  # 只把【独立问题】的答案写进跨用户共享缓存;带上下文的答案是个人化的,不共享
            data_store.record("wx_qa", fp, {"answer": ans, "q": question[:200]})
        if ans:
            _remember(cid, question, ans)  # 记进会话(含 agent 的反问),下一条追问才接得上
        await _reply(_clean(ans) if ans else _FALLBACK_REPLY)
        # 非会员试吃的最后一次：答完顺带预告（成功时刻转化位——刚被答爽时付费意愿最高）
        if not is_member and ans and metrics_store.get_daily(qkey) >= _WX_FREE_QA:
            try:
                await _reply(f"（今天 {_WX_FREE_QA} 次免费问答已用完；开通会员不限次 👉 {_PUBLIC_SITE}）")
            except Exception:  # noqa: BLE001
                pass

        # ⑦ 争议个股「多空深度档」(灰度·可选·会员专属——多一轮 LLM 成本)：基础答案已先到，对【锁定个股+决策措辞】的独立提问
        #    再多花一轮生成多头/空头/投委裁决，作为第二条分阶段推送——不顶撞单条响应超时。
        #    失败/不合格/超时一律静默(只发了基础答案)，绝不影响主流程。
        if (_MULTIVIEW_ENABLED and self._multiview_fn and ans and not has_ctx and is_member
                and _is_controversial_stock_q(question)):
            try:
                deep = await self._multiview_fn(question, ans)
                if deep and deep.strip():
                    metrics_store.incr("wxqa:multiview")  # 观测:深度档触发次数
                    await _reply(_clean(deep.strip()))
            except Exception as exc:  # noqa: BLE001
                logger.warning("[weixin] multiview err %s: %s", bot_id[:8], exc)

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
