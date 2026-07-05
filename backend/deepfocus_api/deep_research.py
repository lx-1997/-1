"""深度研判（多智能体辩论）——TradingAgents 式范式，跑在现有栈上。

设计背景：生产机仅 1.8GB 内存，跑不了重型 TradingAgents 框架（会 OOM 搞挂站点），
故不装那个框架，而是用现有的 CloudResearchLLM（run_tool_agent 取数 + complete_json 推理）
编排出同样的多智能体辩论深度：
    取证(双路并行) → 多空并行立论 → 空头交叉反驳 + 风控并行 → 投委会裁判
固定 7 次 LLM 调用，三段 asyncio.gather 并行，总预算 150s 硬切。

⚠️ 部署前提：本模块用**进程内 dict** 存任务态，依赖**单 uvicorn worker**。
多 worker / gunicorn 下轮询会打到没有该任务的 worker → 永远 pending，届时此设计全废。
（当前 systemd 是 `uvicorn ... --port 8300` 无 --workers，即单 worker，成立。）

⭐ 安全红线（iFinD 灰度 + 合规）：
  1. iFinD 衍生结论绝不落任何共享持久化面：run_deep_research 全程 set
     data_store.DEEP_NO_PERSIST=True，期间一切 record() 静默跳过；iFinD 衍生结论只活在
     本内存任务表，30min 蒸发。
  2. ifind_user=True 只透传给取证路 A 的 run_tool_agent；后续所有 complete_json 角色只
     推理不取数，杜绝 iFinD 多点扩散。
  3. 取证 question 显式禁止调 get_stock_verdict（双保险，避免触达公开 verdict_tool 缓存）。
  4. 合规三层兜底：角色 prompt 禁词约束 + 后端 _neutralize 中性化（覆盖所有对用户可见
     文本）+ 强制 disclaimer 注入；方向只取 5 枚举，无买卖/目标价/收益承诺。

Python 3.9 兼容：全部用 Optional[X]/List[X]/Dict[X]，禁 PEP604 `X | None`。
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import data_store
from .llm import CloudResearchLLM

# ---- 资源/时延护栏（1.8G 机器，防 OOM / 失控）----
TOTAL_BUDGET_SECONDS = 150.0          # 单次研判总预算，硬切
MAX_LLM_CALLS = 8                     # 熔断：超过即放弃后续阶段
GLOBAL_INFLIGHT_CAP = 2               # 全局同时在跑的研判数上限
TASK_TTL_SECONDS = 1800.0             # 终态结果保留 30min（用户离开再回看）
TASK_HARD_CAP = 200                   # 内存表硬上限，超出按 created_ts 淘最旧
REUSE_TTL_SECONDS = 600.0             # 省 token：同用户+同股 10min 内复用已完成研判（0 次 LLM）

# 取证两路
_EVIDENCE_ROUNDS = 3
_EVIDENCE_TIMEOUT = 28.0
# 角色推理
_ROLE_TIMEOUT = 30.0
_JUDGE_TIMEOUT = 35.0

_DIRECTIONS = ("看多", "中性偏多", "中性", "中性偏空", "看空")
_DISCLAIMER = "本研判由 AI 多角色综合生成，仅供研究参考，不构成投资建议、收益承诺或自动交易指令。市场有风险。"

# 合规中性化护栏现统一收口到 compliance 模块（全链路共享单一可信源，速判卡/晨报/复盘/模拟盘同用）。
from .compliance import (  # noqa: E402
    NEUTRALIZE_MAP as _NEUTRALIZE_MAP,  # noqa: F401  保留旧名供本模块引用
    neutralize_deep as _neutralize_deep,
    neutralize_text as _neutralize_text,  # noqa: F401
)


# =========================================================================
# 数据结构
# =========================================================================
@dataclass
class DeepStage:
    key: str
    label: str
    status: str = "wait"            # wait | working | done | error
    detail: str = ""
    output: Optional[Dict[str, Any]] = None


@dataclass
class DeepTask:
    task_id: str
    owner: str                      # claims username，小写；仅服务端用，to_public 剔除
    ifind_used: bool
    symbol: str
    name: str
    market: str = "CN"
    status: str = "pending"         # pending | running | done | error
    current_stage: str = "evidence"
    progress: int = 0               # 0-100
    stages: List[DeepStage] = field(default_factory=list)
    result: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    created_ts: float = 0.0
    updated_ts: float = 0.0
    expires_ts: float = 0.0
    llm_calls: int = 0


_STAGE_SKELETON = [
    ("evidence", "取证"),
    ("bull", "多头"),
    ("bear", "空头"),
    ("risk", "风控"),
    ("judge", "裁决"),
]
# 各 stage 完成时的进度推进
_STAGE_PROGRESS = {"evidence": 25, "bull": 45, "bear": 60, "risk": 78, "judge": 100}


# =========================================================================
# 内存任务表（进程内，单 worker）
# =========================================================================
# 无锁设计：所有临界区都是纯 dict 操作、中间无 await，在单线程 asyncio 事件循环下天然原子
# （协程只在 await 处让出，本模块的读改写之间没有 await）。故不需要 asyncio.Lock——
# 且模块级 Lock 在测试多事件循环下会「attached to a different loop」报错。函数保持 async
# 仅为调用风格统一与未来可扩展。
_DEEP_TASKS: Dict[str, DeepTask] = {}


def _now() -> float:
    return time.time()


def _sweep() -> None:
    """惰性 GC：删过期任务；超硬上限按 created_ts 淘最旧。"""
    now = _now()
    expired = [tid for tid, t in _DEEP_TASKS.items() if t.expires_ts and t.expires_ts < now]
    for tid in expired:
        _DEEP_TASKS.pop(tid, None)
    if len(_DEEP_TASKS) > TASK_HARD_CAP:
        ordered = sorted(_DEEP_TASKS.values(), key=lambda t: t.created_ts)
        for t in ordered[: len(_DEEP_TASKS) - TASK_HARD_CAP]:
            _DEEP_TASKS.pop(t.task_id, None)


async def create_task(owner: str, ifind_used: bool, symbol: str, name: str, market: str = "CN") -> DeepTask:
    _sweep()
    now = _now()
    task = DeepTask(
        task_id=uuid.uuid4().hex,
        owner=(owner or "").strip().lower(),
        ifind_used=bool(ifind_used),
        symbol=symbol.strip(),
        name=(name or "").strip(),
        market=(market or "CN").strip().upper() or "CN",
        status="pending",
        current_stage="evidence",
        progress=0,
        stages=[DeepStage(key=k, label=lbl) for k, lbl in _STAGE_SKELETON],
        created_ts=now,
        updated_ts=now,
        expires_ts=now + TASK_TTL_SECONDS,
    )
    _DEEP_TASKS[task.task_id] = task
    return task


async def get_task(task_id: str) -> Optional[DeepTask]:
    return _DEEP_TASKS.get(task_id)


async def _patch(task_id: str, **patch: Any) -> None:
    task = _DEEP_TASKS.get(task_id)
    if not task:
        return
    for k, v in patch.items():
        setattr(task, k, v)
    task.updated_ts = _now()


async def _set_stage(
    task_id: str,
    key: str,
    status: str,
    detail: str = "",
    output: Optional[Dict[str, Any]] = None,
    bump_progress: bool = True,
) -> None:
    task = _DEEP_TASKS.get(task_id)
    if not task:
        return
    for st in task.stages:
        if st.key == key:
            st.status = status
            if detail:
                st.detail = detail
            if output is not None:
                st.output = output
            break
    if status == "working":
        task.current_stage = key
    if status == "done" and bump_progress:
        task.progress = max(task.progress, _STAGE_PROGRESS.get(key, task.progress))
    task.updated_ts = _now()


async def _bump_llm(task_id: str) -> int:
    task = _DEEP_TASKS.get(task_id)
    if not task:
        return 0
    task.llm_calls += 1
    return task.llm_calls


async def owner_running(owner: str) -> Optional[str]:
    owner = (owner or "").strip().lower()
    for t in _DEEP_TASKS.values():
        if t.owner == owner and t.status in ("pending", "running"):
            return t.task_id
    return None


async def in_flight_count() -> int:
    return sum(1 for t in _DEEP_TASKS.values() if t.status in ("pending", "running"))


async def recent_done(owner: str, symbol: str, market: str, max_age: float = REUSE_TTL_SECONDS) -> Optional[str]:
    """省 token：同一用户对同股在 max_age 内已有完成研判 → 复用其 task_id（0 次 LLM 调用）。
    只复用本人任务（轮询端点按 owner 校验，跨人复用会 404）；研判是研究结论、10min 内数据未实质移动，可复用。"""
    own = (owner or "").strip().lower()
    sym = (symbol or "").strip()
    mkt = (market or "CN").strip().upper() or "CN"
    now = _now()
    best: Optional[DeepTask] = None
    for t in _DEEP_TASKS.values():
        if t.owner == own and t.status == "done" and t.result and t.symbol == sym and t.market == mkt and (now - t.updated_ts) <= max_age:
            if best is None or t.updated_ts > best.updated_ts:
                best = t
    return best.task_id if best else None


def to_public(task: DeepTask) -> Dict[str, Any]:
    """序列化给前端——**剔除 owner**，保留 ifind_used（前端打「同花顺 iFinD 实时」徽标用）。"""
    return {
        "task_id": task.task_id,
        "status": task.status,
        "symbol": task.symbol,
        "name": task.name,
        "market": task.market,
        "progress": task.progress,
        "current_stage": task.current_stage,
        "ifind_used": task.ifind_used,
        "stages": [
            {"key": s.key, "label": s.label, "status": s.status, "detail": s.detail, "output": s.output}
            for s in task.stages
        ],
        "result": task.result,
        "error": task.error,
    }


# =========================================================================
# 合规后处理
# =========================================================================
def _finalize_verdict(raw: Dict[str, Any], *, ifind_used: bool, gaps: List[str], degraded: List[str]) -> Dict[str, Any]:
    """裁判原始输出 → 合规化最终研判：方向枚举校验 + 全文中性化 + 强制免责 + 数据质量标注。"""
    verdict: Dict[str, Any] = dict(raw or {})
    # 方向枚举：非法 → 中性
    direction = str(verdict.get("direction") or "").strip()
    if direction not in _DIRECTIONS:
        direction = "中性"
    # 全文中性化（含 thesis / core_evidence / debate_synthesis / watch_levels.note 等）
    verdict = _neutralize_deep(verdict)
    verdict["direction"] = direction
    # 置信度收口到 [0,1]
    try:
        conf = float(verdict.get("confidence"))
        verdict["confidence"] = max(0.0, min(1.0, conf))
    except (TypeError, ValueError):
        verdict["confidence"] = None
    # 强制免责 + AI 生成显式标识（《人工智能生成合成内容标识办法》2025-09-01 施行的硬要求）
    from .compliance import AI_CONTENT_NOTICE as _AI_NOTICE, has_ai_label as _has_ai
    disc = str(verdict.get("disclaimer") or "").strip()
    if not disc:
        disc = _DISCLAIMER
    if not _has_ai(disc):
        disc = disc + "；" + _AI_NOTICE
    verdict["disclaimer"] = disc
    # 数据质量标注（ifind_used 只标"用了"这个事实，不放裸数值）
    dq = verdict.get("data_quality")
    if not isinstance(dq, dict):
        dq = {}
    dq["ifind_used"] = bool(ifind_used)
    if gaps:
        dq["gaps"] = gaps[:6]
    if degraded:
        dq["degraded_sources"] = degraded[:6]
    verdict["data_quality"] = dq
    return verdict


# =========================================================================
# LLM 调用（可被测试 monkeypatch）
# =========================================================================
def _make_llm() -> CloudResearchLLM:
    return CloudResearchLLM()


def _trim(s: Any, limit: int) -> str:
    text = str(s or "")
    return text if len(text) <= limit else text[:limit] + "…"


# ---- 角色提示词公共约束 ----
_COMMON = (
    "你是买方研究员，客观谨慎。严禁收益承诺、'必涨/必跌/稳赚/翻倍/建议买入卖出清仓'等确定性指令；"
    "方向用中性词。只基于提供的证据推理，证据不足就说不足，不得编造数据或目标价。"
    "仅输出严格 JSON object，无 Markdown，无解释文字。"
)


def _evidence_q_fundamental(name: str, symbol: str) -> str:
    return (
        f"你是 A股基本面取证分析师。对 {name}（{symbol}）依次调用 get_market_quote、get_valuation、"
        "get_financials、get_fund_flow 取真实数据；**不要调用 get_stock_verdict**（下结论由后续裁判负责）。"
        "工具 ok=false/data=null 如实记『暂无』不得杜撰。仅汇总数字事实（现价/涨跌/PE/PB/市值/净利营收同比/"
        "主力净流入），不做方向判断、不写结论，≤180字。"
    )


def _evidence_q_context(name: str, symbol: str) -> str:
    return (
        f"你是观点与情报取证分析师。对 {name}（{symbol}）依次：①调用 get_analyst_consensus 取卖方一致预期；"
        f"②**必须调用 get_stock_research（symbol={symbol}）取本平台研报面板的券商研报**（东财近 2 年，标题/机构/评级/"
        "日期）——这是核实『我们网站能查到的研报』的主力，务必调用；③用 search_our_content 换 2-3 个关键词（公司名/"
        "所属板块/近期催化）补检索快讯·文章；④调用 get_daily_review 取最新 A股复盘看板块资金主线。"
        "**不要调用 get_stock_verdict**。汇总『券商研报怎么看（列机构+评级+标题）+ 外部一致预期 + 我们提前覆盖了什么』，"
        "不做方向判断，≤220字。"
    )


def _case_prompt(stance: str, evidence_pack: str) -> str:
    pick = "支撑上涨" if stance == "多头" else "支撑下跌/估值过热/资金流出/增长证伪"
    return (
        f"{_COMMON}\n下面是确定性取证包(JSON)。扮演看{stance[0]}分析师，从中挑{pick}的证据构建{stance}论点。"
        "每条 key_arg 必须在 evidence_ref 指回包内具体字段或来源标题；包里没有的数字一律不得编造，"
        "无支撑标『未经证据支持』。仅返回 JSON："
        f'{{"stance":"{stance}","thesis":"≤80字","key_args":[{{"point":"","evidence_ref":""}}],'
        '"catalysts":[],"confidence":0.0}\n'
        f"证据包：{evidence_pack}"
    )


def _debate_prompt(evidence_pack: str, bull_case: str) -> str:
    return (
        f"{_COMMON}\n进入辩论环节。给你①取证包②多头完整论点。逐条审视多头每个 key_arg：用取证包硬数据判断它被"
        "证伪(驳倒)/夸大(削弱)/成立(认同)，说明理由并引用具体数值。同时诚实承认多头哪条论据动摇了你的空头立场"
        "(bear_concessions)。不得引入取证包外新数字。仅返回 JSON："
        '{"rebuttals":[{"targets_bull_point":"","verdict":"驳倒|削弱|认同","reasoning":""}],'
        '"bear_concessions":[],"strongest_bull_point":"","strongest_bear_point":"","net_lean":"偏多|偏空|胶着"}\n'
        f"取证包：{evidence_pack}\n多头论点：{bull_case}"
    )


def _risk_prompt(evidence_pack: str, bull_case: str, bear_case: str) -> str:
    return (
        f"{_COMMON}\n你是独立风控官，不站队只盯下行与不确定性。基于取证包（含 gaps 数据缺口）与多空双方初论，"
        "列最关键风险（含严重度与证据指向）、数据可信度软肋（单一来源/降级数据/iFinD 不可外泄须谨慎表述）、"
        "让本结论作废的否决条件。仅返回 JSON："
        '{"key_risks":[{"risk":"","severity":"高|中|低","evidence_ref":""}],"data_caveats":[],'
        '"invalidation_conditions":[],"overall_risk_level":"低|中|高"}\n'
        f"取证包：{evidence_pack}\n多头：{bull_case}\n空头：{bear_case}"
    )


def _judge_prompt(evidence_pack: str, bull: str, bear: str, debate: str, risk: str) -> str:
    return (
        f"{_COMMON}\n你是 DeepFocus 投委会主席，做最终裁决——不是简单投票，而是权衡：哪一方论据更被取证包硬数据"
        "支撑、辩论中谁的论点被驳倒、风控否决条件是否触发。据此给唯一方向（只能取 看多/中性偏多/中性/中性偏空/"
        "看空）与置信度（随数据缺口/低可信来源/胶着辩论而下调）。core_evidence 必须引用取证包出现过的具体数值。"
        "watch_levels 给观察性支撑/压力位，措辞中性化（『若跌破X需警惕、站上Y可关注』），严禁买入/卖出/加仓/"
        "目标价/收益承诺。仅返回 JSON："
        '{"direction":"看多|中性偏多|中性|中性偏空|看空","confidence":0.0,"thesis":"≤120字",'
        '"core_evidence":[{"point":"≤30字","evidence_ref":""}],"key_risks":[{"risk":"≤24字","severity":"高|中|低"}],'
        '"watch_levels":{"support":"","resistance":"","note":"中性化观察措辞"},"debate_synthesis":"≤100字"}\n'
        f"取证包：{evidence_pack}\n多头：{bull}\n空头：{bear}\n辩论：{debate}\n风控：{risk}"
    )


# =========================================================================
# 编排
# =========================================================================
async def _gather_evidence(
    llm: CloudResearchLLM, task_id: str, name: str, symbol: str, ifind_user: bool
) -> Dict[str, Any]:
    """S0 取证：双路并行。路 A 透传 ifind_user，路 B 不需 iFinD（减少触面）。"""
    await _set_stage(task_id, "evidence", "working", detail="取证中…")

    async def route_a() -> Optional[Dict[str, Any]]:
        await _bump_llm(task_id)
        return await llm.run_tool_agent(
            question=_evidence_q_fundamental(name, symbol),
            context_hint=f"当前标的：{name}（{symbol}）",
            max_rounds=_EVIDENCE_ROUNDS,
            timeout_seconds=_EVIDENCE_TIMEOUT,
            ifind_user=ifind_user,
        )

    async def route_b() -> Optional[Dict[str, Any]]:
        await _bump_llm(task_id)
        return await llm.run_tool_agent(
            question=_evidence_q_context(name, symbol),
            context_hint=f"当前标的：{name}（{symbol}）",
            max_rounds=_EVIDENCE_ROUNDS,
            timeout_seconds=_EVIDENCE_TIMEOUT,
            ifind_user=False,
        )

    a, b = await asyncio.gather(route_a(), route_b(), return_exceptions=True)
    a = None if isinstance(a, BaseException) else a
    b = None if isinstance(b, BaseException) else b

    gaps: List[str] = []
    traces: List[Dict[str, Any]] = []
    fundamental = ""
    context = ""
    if a and (a.get("answer") or "").strip():
        fundamental = _trim(a.get("answer"), 1500)
        traces.extend((a.get("tool_trace") or [])[:12])
    else:
        gaps.append("基本面取证不可用")
    if b and (b.get("answer") or "").strip():
        context = _trim(b.get("answer"), 1500)
        traces.extend((b.get("tool_trace") or [])[:12])
    else:
        gaps.append("观点/自有内容取证不可用")

    pack = {
        "fundamental": fundamental,
        "context": context,
        "tool_trace": traces[:20],
        "ifind_used": bool(ifind_user),
        "gaps": gaps,
        "both_failed": not fundamental and not context,
    }
    detail = "取证完成" if not pack["both_failed"] else "取证未取到数据"
    await _set_stage(task_id, "evidence", "done" if not pack["both_failed"] else "error", detail=detail, output=pack)
    return pack


async def _role_json(llm: CloudResearchLLM, task_id: str, prompt: str, max_tokens: int, timeout: float) -> Optional[Dict[str, Any]]:
    await _bump_llm(task_id)
    return await llm.complete_json(prompt, max_tokens=max_tokens, timeout_seconds=timeout)


async def _pipeline(task_id: str, symbol: str, name: str, market: str, ifind_user: bool) -> None:
    llm = _make_llm()
    await _patch(task_id, status="running")

    # ---- S0 取证 ----
    pack = await _gather_evidence(llm, task_id, name, symbol, ifind_user)
    if pack["both_failed"]:
        await _patch(
            task_id,
            status="error",
            error="深度研判需云模型/实时数据源，当前两路取证均未取到数据，请稍后重试。",
        )
        return
    evidence_str = _trim(json.dumps(pack, ensure_ascii=False), 4000)
    gaps = list(pack.get("gaps") or [])
    degraded: List[str] = []
    if pack.get("ifind_used"):
        pass  # iFinD 是增强而非降级

    # ---- S1 多空并行立论 ----
    await _set_stage(task_id, "bull", "working", detail="多头立论中…")
    await _set_stage(task_id, "bear", "working", detail="空头立论中…")
    bull_r, bear_r = await asyncio.gather(
        _role_json(llm, task_id, _case_prompt("多头", evidence_str), 1100, _ROLE_TIMEOUT),
        _role_json(llm, task_id, _case_prompt("空头", evidence_str), 1100, _ROLE_TIMEOUT),
        return_exceptions=True,
    )
    bull = None if isinstance(bull_r, BaseException) else bull_r
    bear = None if isinstance(bear_r, BaseException) else bear_r
    if bull:
        await _set_stage(task_id, "bull", "done", detail="多头观点已成形", output=bull)
    else:
        gaps.append("多头立论失败")
        await _set_stage(task_id, "bull", "error", detail="多头立论失败")
    if bear:
        await _set_stage(task_id, "bear", "done", detail="空头观点已成形", output=bear)
    else:
        gaps.append("空头立论失败")
        await _set_stage(task_id, "bear", "error", detail="空头立论失败")

    bull_str = _trim(json.dumps(bull or {}, ensure_ascii=False), 1800)
    bear_str = _trim(json.dumps(bear or {}, ensure_ascii=False), 1800)

    # ---- S2 空头反驳 + S3 风控（并行，仅依赖 S0+S1）----
    await _set_stage(task_id, "risk", "working", detail="风控评估中…")
    debate_r, risk_r = await asyncio.gather(
        _role_json(llm, task_id, _debate_prompt(evidence_str, bull_str), 1200, _ROLE_TIMEOUT),
        _role_json(llm, task_id, _risk_prompt(evidence_str, bull_str, bear_str), 900, _EVIDENCE_TIMEOUT),
        return_exceptions=True,
    )
    debate = None if isinstance(debate_r, BaseException) else debate_r
    risk = None if isinstance(risk_r, BaseException) else risk_r
    if risk:
        await _set_stage(task_id, "risk", "done", detail="风控评估完成", output=risk)
    else:
        gaps.append("风控评估失败")
        await _set_stage(task_id, "risk", "error", detail="风控评估失败")
    # 把空头反驳挂到 bear stage 的 output 里（真辩论留痕，不单列时间轴节点）
    if debate and bear:
        t = _DEEP_TASKS.get(task_id)
        if t:
            for st in t.stages:
                if st.key == "bear" and isinstance(st.output, dict):
                    st.output = dict(st.output, rebuttal=debate)
                    break

    debate_str = _trim(json.dumps(debate or {}, ensure_ascii=False), 1500)
    risk_str = _trim(json.dumps(risk or {}, ensure_ascii=False), 1200)

    # ---- S4 裁判 ----
    await _set_stage(task_id, "judge", "working", detail="投委会裁决中…")
    raw = None
    try:
        raw = await _role_json(
            llm, task_id, _judge_prompt(evidence_str, bull_str, bear_str, debate_str, risk_str), 1500, _JUDGE_TIMEOUT
        )
    except Exception as exc:  # noqa: BLE001
        await _patch(task_id, status="error", error=f"裁判阶段失败：{_trim(str(exc), 120)}")
        await _set_stage(task_id, "judge", "error", detail="裁决失败")
        return

    if not raw or not isinstance(raw, dict):
        await _patch(task_id, status="error", error="裁判未返回有效研判，请重试。")
        await _set_stage(task_id, "judge", "error", detail="裁决失败")
        return

    verdict = _finalize_verdict(raw, ifind_used=ifind_user, gaps=gaps, degraded=degraded)
    await _set_stage(task_id, "judge", "done", detail="研判完成", output={"direction": verdict.get("direction")})
    await _patch(task_id, status="done", current_stage="finalized", progress=100, result=verdict)


async def run_deep_research(task_id: str, symbol: str, name: str, market: str, ifind_user: bool = False) -> None:
    """对外入口：跑完整 5 阶段管线。150s 硬超时 + 熔断。

    持久化闸门收窄：DEEP_NO_PERSIST 本意只防 iFinD 衍生结论泄漏进共享库，
    原实现对所有任务无差别抑制 → 非 iFinD 任务的结果无法跨用户复用，同一只热门股
    每个会员都要重烧 7 次 LLM。现在仅 ifind_user=True 时抑制；非 iFinD 任务成功后
    按 symbol+交易日落库，第二人起 0 token 直读（见 seed_cached_task）。"""
    token = data_store.DEEP_NO_PERSIST.set(True) if ifind_user else None
    try:
        try:
            await asyncio.wait_for(
                _pipeline(task_id, symbol, name, market, ifind_user),
                timeout=TOTAL_BUDGET_SECONDS,
            )
        except asyncio.TimeoutError:
            await _patch(task_id, status="error", error="研判超时，请稍后重试。")
            await _set_stage(task_id, "judge", "error", detail="超时", bump_progress=False)
        except Exception as exc:  # noqa: BLE001
            await _patch(task_id, status="error", error=f"研判异常：{_trim(str(exc), 120)}")
    finally:
        if token is not None:
            data_store.DEEP_NO_PERSIST.reset(token)
    # 跨用户当日缓存（非 iFinD 任务专用）：成功结果按 symbol+市场落库，同股当天全站最多烧一次
    if not ifind_user:
        t = _DEEP_TASKS.get(task_id)
        if t and t.status == "done" and isinstance(t.result, dict):
            try:
                from datetime import datetime as _dt, timedelta as _td, timezone as _tz
                data_store.record("deep_research", f"{t.symbol}:{t.market}", {
                    "verdict": t.result,
                    "name": t.name,
                    "symbol": t.symbol,
                    "market": t.market,
                    "date": _dt.now(_tz(_td(hours=8))).strftime("%Y-%m-%d"),
                })
            except Exception:  # noqa: BLE001 - 缓存落库失败不影响本次结果
                pass


async def seed_cached_task(owner: str, symbol: str, name: str, market: str) -> Optional[str]:
    """跨用户当日缓存命中 → 给该 owner 铸一个已完成任务（0 次 LLM），返回 task_id；未命中 None。

    只缓存非 iFinD 结果（落库侧已保证），结果本身已过 _finalize_verdict 合规化；
    轮询端点按 owner 校验，铸出的任务归属提问者本人。"""
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    sym = (symbol or "").strip()
    mkt = (market or "CN").strip().upper() or "CN"
    try:
        cached = data_store.latest("deep_research", f"{sym}:{mkt}", max_age_seconds=86400.0)
    except Exception:  # noqa: BLE001
        cached = None
    today = _dt.now(_tz(_td(hours=8))).strftime("%Y-%m-%d")
    if not (isinstance(cached, dict) and cached.get("date") == today and isinstance(cached.get("verdict"), dict)):
        return None
    task = await create_task(owner, False, sym, (name or str(cached.get("name") or "")), mkt)
    task.status = "done"
    task.progress = 100
    task.current_stage = "finalized"
    task.result = dict(cached["verdict"])
    for st in task.stages:
        st.status = "done"
    task.updated_ts = _now()
    return task.task_id
