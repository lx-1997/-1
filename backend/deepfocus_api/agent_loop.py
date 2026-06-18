from __future__ import annotations

import asyncio
import json
import time
import uuid
from typing import Any, AsyncIterator, Optional

from fastapi import Request

from .cross_module_aggregator import (
    build_injection_block,
    gather_all_for_stock,
    gather_data_source_evidence,
    gather_macro_brief,
    gather_market_quotes,
    gather_professional_metrics,
    gather_risk_context,
    gather_backtest_context,
)
from .llm import CloudResearchLLM, _cred_tier_label
from .shared_utils import safe_float, utc_now_iso

MAX_LOOP_ROUNDS = 2
PLAN_TIMEOUT = 22
ANALYZE_TIMEOUT = 55
SYNTHESIZE_TIMEOUT = 65

PHASES = ["scope", "acquire", "analyze", "synthesize"]

PHASE_DISPLAY: dict[str, str] = {
    "scope": "研究范围界定",
    "acquire": "多维数据采集",
    "analyze": "深度交叉分析",
    "synthesize": "综合研判与建议",
}

MODULE_NAMES: dict[str, str] = {
    "quotes": "实时行情",
    "macro": "宏观环境",
    "risk": "持仓风险",
    "evidence": "数据源证据",
    "metrics": "财报指标",
    "backtest": "策略回测",
}

AVAILABLE_MODULES = list(MODULE_NAMES)


def _sse(event_type: str, payload: dict[str, Any], event_id: str | None = None) -> str:
    lines = []
    if event_id:
        lines.append(f"id: {event_id}")
    lines.append(f"event: {event_type}")
    lines.append(f"data: {json.dumps(payload, ensure_ascii=False)}")
    return "\n".join(lines) + "\n\n"


def _safe_list(val: Any, max_items: int = 8) -> list[str]:
    if isinstance(val, list):
        return [str(v)[:200] for v in val[:max_items]]
    return []


async def _call_json(llm: CloudResearchLLM, prompt: str, max_tokens: int = 1400, timeout: int = 40) -> dict[str, Any]:
    try:
        return await llm.complete_json(prompt, max_tokens=max_tokens, timeout_seconds=timeout)
    except Exception:
        return {}


def _scoping_prompt(symbol: str, question: str) -> str:
    return (
        "你是一家顶级对冲基金的首席研究员。现在要对一个标的启动系统性投研。\n\n"
        f"标的代码：{symbol}\n"
        f"研究问题：{question}\n\n"
        "请界定研究范围与可用数据模块的优先级，返回严格 JSON：\n"
        '{"research_framework": "一份不超过40字的中文研究框架说明", '
        '"modules_order": ["quotes","macro","metrics","risk","evidence"], '
        '"scoping_rationale": "为什么要这样排序，不超过50字", '
        '"key_questions": ["需要回答的关键问题不超过30字"], '
        '"data_budget": 5}\n'
        "modules_order 从以下中选择并按优先级排列：quotes, macro, risk, evidence, metrics。\n"
        "只返回 JSON，不要任何其他内容。"
    )


def _analyze_prompt(symbol: str, question: str, data_block: str, findings: str = "") -> str:
    prev = f"\n前序发现：{findings}\n" if findings else ""
    return (
        "你是顶级买方研究分析师（Buy-Side Research Analyst）。基于以下数据进行分析，必须返回严格 JSON。\n\n"
        f"标的：{symbol}\n研究问题：{question}\n{prev}\n"
        f"【数据】\n{data_block}\n\n"
        "请从以下维度分析并返回 JSON：\n"
        '{"momentum_signal": "短期动量判断（bullish/neutral/bearish）", '
        '"momentum_detail": "具体论据（不超过 50 字）", '
        '"fundamental_quality": "基本面质量评分 0~100", '
        '"fundamental_detail": "基本面论据（不超过 50 字）", '
        '"valuation_view": "估值判断（undervalued/fair/overvalued/unknown）", '
        '"valuation_detail": "估值依据（不超过 50 字）", '
        '"macro_tailwind": "宏观顺风还是逆风（tailwind/neutral/headwind）", '
        '"macro_detail": "宏观判断依据（不超过 50 字）", '
        '"key_insight": "本轮最关键的 1 个洞察（不超过 60 字）", '
        '"confidence_contribution": 0.25, '
        '"need_another_round": false, '
        '"next_modules": ["metrics"]}\n'
        "confidence_contribution 范围 0.1~0.4。need_another_round 表示是否需要再采集数据。\n"
        "next_modules 如果 need_another_round=true 则填写推荐的 1~2 个模块。\n"
        "源纪律：先判断每条数据/证据与问题的相关性和可信度——优先用高可信、直接相关的证据；"
        "论坛传闻/营销内容/单一来源需谨慎，不能当确定结论；某维度数据不足就填 unknown 并在 detail 里点明缺口，绝不编造价格/财报/事件。\n"
        "只返回 JSON。"
    )


def _synthesize_prompt(
    symbol: str,
    question: str,
    all_findings: list[dict[str, Any]],
    full_data: str,
    citable_sources: list[dict[str, Any]] | None = None,
) -> str:
    findings_text = json.dumps(all_findings, ensure_ascii=False, default=str)
    # 编号可引用来源 + [n] 内联标注指令（与分析师快答一致，让深研结论可溯源）。
    sources_block = ""
    cite_rule = ""
    if citable_sources:
        lines = [
            f"[{s.get('n')}] 《{str(s.get('title', ''))[:60]}》— {s.get('source', '')}〔可信度：{_cred_tier_label(s.get('credibility'))}〕"
            for s in citable_sources
        ]
        sources_block = (
            "\n【可引用来源（按编号 + 可信度档）】\n" + "\n".join(lines) + "\n"
        )
        cite_rule = (
            "引用纪律：先判断每条来源与本问题的相关性和可信度——优先用高可信、直接相关的来源；"
            "**存疑**来源（论坛传闻/营销号/单一转载）仅在能与其它来源交叉印证时使用并注明「单一来源待核验」；与问题无关的来源不要引。"
            "在 executive_summary、target_rationale、thesis 各情景里，凡引用证据支撑的结论用 [n] 内联标注（可多个如 [1][3]），"
            "只引用已列出的编号、不要编造；来源之间结论冲突时点明分歧、不要简单取一。\n"
        )
    return (
        "你是顶级对冲基金 PM（投资组合经理）。所有研究工作已完成，现在出具最终研究结论。\n\n"
        f"标的：{symbol}\n研究问题：{question}\n"
        f"【所有研究发现】\n{findings_text}\n"
        f"【原始数据摘要】\n{full_data[:3000]}\n"
        f"{sources_block}\n"
        f"{cite_rule}"
        "请以机构研究报告标准输出严格 JSON：\n"
        '{\n'
        '  "executive_summary": "执行摘要 — 用 3~5 句话概括核心结论（不超过 200 字）",\n'
        '  "recommendation": "strong_buy / buy / hold / reduce / sell / strong_sell",\n'
        '  "conviction": "high / medium / low",\n'
        '  "target_rationale": "建议依据（不超过 80 字）",\n'
        '  "thesis": {\n'
        '    "bull_case": "乐观情景描述与目标（不超过 80 字）",\n'
        '    "bull_probability": 0.30,\n'
        '    "base_case": "基准情景描述与目标（不超过 80 字）",\n'
        '    "base_probability": 0.50,\n'
        '    "bear_case": "悲观情景描述与目标（不超过 80 字）",\n'
        '    "bear_probability": 0.20\n'
        '  },\n'
        '  "risk_matrix": {\n'
        '    "market_risk": {"score": 0~10, "label": "市场风险", "detail": "说明（不超过 25 字）"},\n'
        '    "valuation_risk": {"score": 0~10, "label": "估值风险", "detail": "说明（不超过 25 字）"},\n'
        '    "liquidity_risk": {"score": 0~10, "label": "流动性风险", "detail": "说明（不超过 25 字）"},\n'
        '    "macro_risk": {"score": 0~10, "label": "宏观风险", "detail": "说明（不超过 25 字）"},\n'
        '    "event_risk": {"score": 0~10, "label": "事件风险", "detail": "说明（不超过 25 字）"}\n'
        '  },\n'
        '  "key_catalysts": ["近期关键催化剂 1（不超过 35 字）", "催化剂 2"],\n'
        '  "data_issues": ["数据不足或质量问题的说明，如无问题则为空数组"],\n'
        '  "next_steps": ["建议的后续研究步骤 1", "步骤 2"],\n'
        '  "confidence": 0.82,\n'
        '  "sources": ["数据来源模块名称"]\n'
        '}\n'
        "recommendation 含义：strong_buy（强烈建议买入，高确信），buy（建议买入），hold（持有/观望），"
        "reduce（建议减仓），sell（建议卖出），strong_sell（强烈建议卖出）。\n"
        "只返回 JSON。"
    )


async def _gather_module(module_key: str, symbol: str, question: str = "") -> dict[str, Any]:
    if module_key == "quotes":
        return await gather_market_quotes([symbol])
    if module_key == "macro":
        return await gather_macro_brief()
    if module_key == "risk":
        return await gather_risk_context([symbol])
    if module_key == "evidence":
        # 主动取数：深研的证据模块在本地稀薄时，自动按问题爬一轮最新资料补进证据库。
        return await gather_data_source_evidence(symbol=symbol, acquire_keyword=(question or symbol))
    if module_key == "metrics":
        return await gather_professional_metrics(symbol=symbol)
    if module_key == "backtest":
        return await gather_backtest_context(symbol)
    return {"error": f"unknown module: {module_key}"}


def _gather_summary(key: str, data: dict[str, Any]) -> str:
    maps: dict[str, Any] = {
        "quotes": lambda: f"{len(data.get('quotes',[]))}条行情 | 来源:{data.get('source','')}",
        "macro": lambda: f"{len(data.get('indicators',[]))}项指标 | 信号:{data.get('overall_signal','')}",
        "risk": lambda: f"{len(data.get('positions',[]))}笔持仓 | 市值:{data.get('portfolio_market_value','')}",
        "evidence": lambda: f"{len(data.get('evidence_items',[]))}条证据",
        "metrics": lambda: f"{len(data.get('metrics',[]))}项财报指标",
        "backtest": lambda: f"{data.get('total',0)}条历史回测记录",
    }
    return maps.get(key, lambda: "数据采集完成")()


async def run_agent_research_loop(
    llm: CloudResearchLLM,
    symbol: str,
    question: str,
    request: Optional[Request] = None,
) -> AsyncIterator[str]:
    loop_id = uuid.uuid4().hex[:8]
    sym = symbol.upper().strip()
    t0 = time.time()

    yield _sse("loop_start", {
        "loop_id": loop_id, "symbol": sym, "question": question,
        "generated_at": utc_now_iso(),
    })

    gathered: set[str] = set()
    all_findings: list[dict[str, Any]] = []
    all_data_blocks: list[str] = []
    evidence_items_collected: list[dict[str, Any]] = []  # 证据模块拉到的真实条目→编号可引用来源
    cumulative_confidence = 0.0

    for round_num in range(1, MAX_LOOP_ROUNDS + 1):
        if request and await request.is_disconnected():
            break

        remaining = [m for m in AVAILABLE_MODULES if m not in gathered]
        if not remaining:
            break

        if round_num == 1:
            scope = await _call_json(llm, _scoping_prompt(sym, question), max_tokens=800, timeout=PLAN_TIMEOUT)
            plan_order = _safe_list(scope.get("modules_order"), max_items=5) or remaining
            framework = str(scope.get("research_framework", "") or f"{sym} 系统性投研")
            scoping = str(scope.get("scoping_rationale", "") or "按行情→基本面→风险优先级")
            key_qs = _safe_list(scope.get("key_questions"), max_items=4)

            yield _sse("research_scope", {
                "loop_id": loop_id, "round": round_num, "phase": "scope",
                "phase_label": PHASE_DISPLAY["scope"],
                "research_framework": framework,
                "scoping_rationale": scoping,
                "key_questions": key_qs,
                "data_budget": scope.get("data_budget", len(remaining)),
                "status": "done",
            })
        else:
            plan_order = remaining[:2]

        round_data: list[str] = []
        for mod in plan_order:
            if mod in gathered or len(round_data) >= 3:
                continue

            yield _sse("research_acquire", {
                "loop_id": loop_id, "round": round_num, "phase": "acquire",
                "phase_label": PHASE_DISPLAY["acquire"],
                "module": mod, "module_name": MODULE_NAMES.get(mod, mod),
                "detail": f"采集 {MODULE_NAMES.get(mod, mod)} 数据...",
                "status": "working",
            })

            try:
                data = await _gather_module(mod, sym, question)
                gathered.add(mod)
                # 证据模块：留存真实条目，synthesize 时转成编号可引用来源（可溯源）。
                if mod == "evidence" and isinstance(data, dict):
                    for ev in (data.get("evidence_items") or []):
                        if isinstance(ev, dict) and ev.get("title"):
                            evidence_items_collected.append(ev)
                block = build_injection_block({"symbol": sym, mod: data})
                round_data.append(block)
                yield _sse("research_acquire", {
                    "loop_id": loop_id, "round": round_num, "phase": "acquire",
                    "phase_label": PHASE_DISPLAY["acquire"],
                    "module": mod, "module_name": MODULE_NAMES.get(mod, mod),
                    "detail": _gather_summary(mod, data),
                    "status": "done",
                })
            except Exception as e:
                yield _sse("research_acquire", {
                    "loop_id": loop_id, "round": round_num, "phase": "acquire",
                    "phase_label": PHASE_DISPLAY["acquire"],
                    "module": mod, "module_name": MODULE_NAMES.get(mod, mod),
                    "detail": f"失败: {str(e)[:50]}",
                    "status": "error",
                })

        if not round_data:
            break

        block_text = "\n".join(round_data)
        all_data_blocks.append(block_text)

        yield _sse("research_analyze", {
            "loop_id": loop_id, "round": round_num, "phase": "analyze",
            "phase_label": PHASE_DISPLAY["analyze"],
            "title": f"第{round_num}轮交叉分析",
            "detail": "正在执行多维度信号交叉验证...",
            "status": "working",
        })

        prev_findings = "; ".join(f.get("key_insight", "") for f in all_findings[-2:])
        analysis = await _call_json(llm, _analyze_prompt(sym, question, block_text, prev_findings),
                                    max_tokens=1400, timeout=ANALYZE_TIMEOUT)

        key_insight = str(analysis.get("key_insight", "") or "分析完成")
        conf_gain = min(0.4, max(0.05, safe_float(analysis.get("confidence_contribution"), default=0.18) or 0.18))
        cumulative_confidence = min(1.0, cumulative_confidence + conf_gain)
        need_another = bool(analysis.get("need_another_round", False))
        next_mods = _safe_list(analysis.get("next_modules"), max_items=2)

        all_findings.append({
            "round": round_num,
            "insight": key_insight,
            "momentum": analysis.get("momentum_signal", "neutral"),
            "momentum_detail": analysis.get("momentum_detail", ""),
            "fundamental": safe_float(analysis.get("fundamental_quality"), default=50) or 50,
            "fundamental_detail": analysis.get("fundamental_detail", ""),
            "valuation": analysis.get("valuation_view", "fair"),
            "valuation_detail": analysis.get("valuation_detail", ""),
            "macro_posture": analysis.get("macro_tailwind", "neutral"),
            "macro_detail": analysis.get("macro_detail", ""),
            "confidence": cumulative_confidence,
        })

        yield _sse("research_analyze", {
            "loop_id": loop_id, "round": round_num, "phase": "analyze",
            "phase_label": PHASE_DISPLAY["analyze"],
            "title": f"第{round_num}轮分析结论",
            "detail": key_insight,
            "momentum_signal": analysis.get("momentum_signal"),
            "momentum_detail": analysis.get("momentum_detail", ""),
            "fundamental_quality": safe_float(analysis.get("fundamental_quality"), default=50) or 50,
            "fundamental_detail": analysis.get("fundamental_detail", ""),
            "valuation_view": analysis.get("valuation_view"),
            "valuation_detail": analysis.get("valuation_detail", ""),
            "macro_tailwind": analysis.get("macro_tailwind"),
            "macro_detail": analysis.get("macro_detail", ""),
            "confidence_contribution": round(conf_gain, 2),
            "cumulative_confidence": round(cumulative_confidence, 2),
            "need_another_round": need_another,
            "next_modules": next_mods if need_another else [],
            "status": "done",
        })

        if not need_another or len(gathered) >= len(AVAILABLE_MODULES):
            break

    yield _sse("research_synthesize", {
        "loop_id": loop_id, "phase": "synthesize",
        "phase_label": PHASE_DISPLAY["synthesize"],
        "title": "机构研究结论合成中",
        "detail": "综合全部研究发现，撰写机构级研究结论...",
        "status": "working",
    })

    # 编号可引用来源：把证据模块拉到的真实条目去重→ {n,title,source,url,credibility}，
    # 让 flagship 深研结论像分析师快答一样可溯源（带链接+可信度）。先构建，再喂给 synthesize
    # prompt 让模型在结论正文里用 [n] 内联引用（编号与前端渲染一致）。
    citable_seen: set[str] = set()
    citable_sources: list[dict[str, Any]] = []
    for ev in evidence_items_collected:
        ev_title = str(ev.get("title", "") or "").strip()
        if not ev_title or ev_title in citable_seen:
            continue
        citable_seen.add(ev_title)
        ev_cred = ev.get("credibility")
        citable_sources.append({
            "n": len(citable_sources) + 1,
            "title": ev_title,
            "source": str(ev.get("source", "") or "证据库"),
            "url": str(ev.get("url", "") or ""),
            "credibility": float(ev_cred) if isinstance(ev_cred, (int, float)) else None,
        })
        if len(citable_sources) >= 6:
            break

    full_text = "\n---\n".join(all_data_blocks)
    synth = await _call_json(llm, _synthesize_prompt(sym, question, all_findings, full_text, citable_sources),
                             max_tokens=2200, timeout=SYNTHESIZE_TIMEOUT)

    thesis = synth.get("thesis", {}) or {}
    risk_matrix = synth.get("risk_matrix", {}) or {}
    elapsed = round(time.time() - t0, 1)
    final_confidence = safe_float(synth.get("confidence"), default=cumulative_confidence) or 0

    yield _sse("research_synthesize", {
        "loop_id": loop_id, "phase": "synthesize",
        "phase_label": PHASE_DISPLAY["synthesize"],
        "title": "研究完成",
        "executive_summary": str(synth.get("executive_summary", "") or "分析完成"),
        "recommendation": str(synth.get("recommendation", "") or "hold"),
        "conviction": str(synth.get("conviction", "") or "medium"),
        "target_rationale": str(synth.get("target_rationale", "") or ""),
        "thesis": {
            "bull_case": str(thesis.get("bull_case", "")),
            "bull_probability": safe_float(thesis.get("bull_probability"), default=0.30) or 0.30,
            "base_case": str(thesis.get("base_case", "")),
            "base_probability": safe_float(thesis.get("base_probability"), default=0.50) or 0.50,
            "bear_case": str(thesis.get("bear_case", "")),
            "bear_probability": safe_float(thesis.get("bear_probability"), default=0.20) or 0.20,
        },
        "risk_matrix": {
            "market_risk": {"score": safe_float(risk_matrix.get("market_risk", {}).get("score"), default=5) or 5,
                            "label": "市场风险", "detail": str(risk_matrix.get("market_risk", {}).get("detail", ""))},
            "valuation_risk": {"score": safe_float(risk_matrix.get("valuation_risk", {}).get("score"), default=5) or 5,
                               "label": "估值风险", "detail": str(risk_matrix.get("valuation_risk", {}).get("detail", ""))},
            "liquidity_risk": {"score": safe_float(risk_matrix.get("liquidity_risk", {}).get("score"), default=5) or 5,
                               "label": "流动性风险", "detail": str(risk_matrix.get("liquidity_risk", {}).get("detail", ""))},
            "macro_risk": {"score": safe_float(risk_matrix.get("macro_risk", {}).get("score"), default=5) or 5,
                           "label": "宏观风险", "detail": str(risk_matrix.get("macro_risk", {}).get("detail", ""))},
            "event_risk": {"score": safe_float(risk_matrix.get("event_risk", {}).get("score"), default=5) or 5,
                           "label": "事件风险", "detail": str(risk_matrix.get("event_risk", {}).get("detail", ""))},
        },
        "key_catalysts": _safe_list(synth.get("key_catalysts"), max_items=4),
        "data_issues": _safe_list(synth.get("data_issues"), max_items=3),
        "next_steps": _safe_list(synth.get("next_steps"), max_items=3),
        "confidence": round(final_confidence, 2),
        "sources": _safe_list(synth.get("sources"), max_items=6) or sorted(gathered),
        "citable_sources": citable_sources,  # 编号可引用证据（带 url+可信度），前端渲染可溯源来源列表
        "total_rounds": len(all_findings),
        "modules_gathered": sorted(gathered),
        "elapsed_seconds": elapsed,
        "status": "done",
    })

    yield _sse("research_done", {
        "loop_id": loop_id,
        "total_rounds": len(all_findings),
        "modules_gathered": sorted(gathered),
        "elapsed_seconds": elapsed,
        "recommendation": str(synth.get("recommendation", "") or "hold"),
        "conviction": str(synth.get("conviction", "") or "medium"),
        "confidence": round(final_confidence, 2),
    })