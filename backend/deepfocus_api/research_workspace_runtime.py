"""Research-project refresh adapter hosted by the existing leased Agent worker."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re

from . import research_workspace as store
from .research_harness import build_research_packet
from .schemas import StockSnapshot


async def process_project_task(task, lease: str, progress, llm_factory):
    project_id = task.input["engine_config"]["research_project_id"]
    project = store.get_project(task.owner_user_id, project_id)
    if project["version"] != task.input["engine_config"]["config_version"]:
        raise ValueError("项目配置已变更，请重新刷新")
    # Check the immutable queue mapping before any data/model calls. Supplying
    # an engine_config in the generic tasks API cannot impersonate a project job.
    if not any(run["id"] == task.id for run in project["runs"]):
        raise ValueError("研究任务与项目不匹配")
    progress("ResearchWorkspace", "正在取数并核验标的、期间、来源和字段覆盖。", 12)
    packet = await asyncio.wait_for(build_research_packet(
        f"研究 {project['symbol']}：{project['objective']}", stock=StockSnapshot(symbol=project["symbol"], name=project["symbol"], market=project["market"]), include_industry=True), timeout=45)
    state = store.evidence_state(packet)
    if not state:
        raise ValueError("未返回字段核验结果，不能生成研究版本")
    previous = project["revisions"][0] if project["revisions"] else None
    followup_question = str(task.input["engine_config"].get("followup_question") or "")
    digest = hashlib.sha256(store._json(state).encode()).hexdigest()
    changed = previous is None or previous["evidence_hash"] != digest or previous["config_version"] != project["version"]
    changed = changed or bool(followup_question)
    if previous and not task.input["engine_config"].get("scheduled") and previous["payload"].get("report", {}).get("analysis_status") == "unavailable":
        changed = True  # Explicit manual refresh may retry an unavailable analysis.
    report = {"analysis_status": "unchanged", "summary": "本次已检查，所跟踪的结构化字段没有变化；沿用上一研究版本。"}
    if changed:
        progress("ResearchWorkspace", "已识别事实或覆盖变化，正在更新受影响的研究判断。", 65)
        has_facts = any(field.get("status") in {"usable", "limited"} for module in state.values() for field in (module.get("fields") or {}).values())
        report = {"analysis_status": "insufficient_data", "summary": "本次没有可用的结构化事实，已记录数据缺口；不能生成实质判断。"}
        if has_facts:
            model = llm_factory()
            if model.provider == "mock":
                report = {"analysis_status": "unavailable", "summary": "已核验的事实可查看；真实解读模型当前不可用。"}
            else:
                prompt = (
                    "你是内部研究员，仅根据输入事实更新研究判断。只输出JSON：summary(最多500字)、"
                    "observations(最多6项，每项含tool、field、interpretation)、hypothesis_updates(最多6条)。"
                    "observations的tool和field必须引用evidence中状态usable/limited的字段。"
                    "只解释该事实支持或不支持什么；limited的限制必须说明。评级不等于盈利预测，市值不等于PE。"
                    "缺一类数据只限制相应判断，不能抹掉已有财报分析。禁止编造估值、目标价、收益、因果贡献比例和实时性；"
                    "禁止根据用户笔记认定事实。hypothesis_updates全部是待验证判断，不能宣称已验证假设。"
                    "不要给下单指令。历史版本仅供对比，不能当本轮新证据。\n"
                    "summary、interpretation和hypothesis_updates只写不含数字的定性解释；所有数值由系统用关联字段单独展示，不在解释中重写数字或百分比。\n"
                    + json.dumps({"objective": project["objective"], "current_followup_question": followup_question, "evidence": state,
                                  "user_opinions_not_evidence": {"notes": project["notes"], "hypotheses": project["hypotheses"]},
                                  "previous": previous["payload"] if previous else None}, ensure_ascii=False)
                )
                try:
                    generated = await asyncio.wait_for(model.complete_json(prompt, max_tokens=1800, timeout_seconds=35), timeout=40)
                    report = validate_report(generated, state)
                except Exception:
                    report = {"analysis_status": "unavailable", "summary": "事实已核验；模型解读未通过本轮检查或超时，可稍后重试。"}
    report["evidence_audit"] = packet.get("evidence_audit") or {}
    if followup_question:
        report["followup_question"] = followup_question
    progress("ResearchWorkspace", "正在原子保存研究版本与本次检查记录。", 95)
    if not store.finalize_refresh(task.owner_user_id, project_id, task.id, lease, project["version"], state, report):
        raise ValueError("任务已取消、租约失效或项目已更新，本轮结果未发布")


def validate_report(value: dict, state: dict) -> dict:
    if not isinstance(value, dict) or not isinstance(value.get("summary"), str) or not value["summary"].strip() or len(value["summary"]) > 2000:
        raise ValueError("invalid summary")
    rows = value.get("observations")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 6:
        raise ValueError("missing grounded observations")
    observations = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("tool"), str) or not isinstance(row.get("field"), str):
            raise ValueError("invalid observation")
        tool, field = row["tool"], row["field"]
        fact = (state.get(tool, {}).get("fields") or {}).get(field)
        text = row.get("interpretation")
        if not fact or fact.get("status") not in {"usable", "limited"} or not isinstance(text, str) or not text.strip() or len(text) > 1000:
            raise ValueError("unbacked observation")
        observations.append({"tool": tool, "field": field, "interpretation": text,
                             "value": fact["value"], "limitations": state[tool].get("issues", [])})
    hypotheses = value.get("hypothesis_updates", [])
    if not isinstance(hypotheses, list) or len(hypotheses) > 6 or any(not isinstance(item, str) or len(item) > 1000 for item in hypotheses):
        raise ValueError("invalid hypotheses")
    if any(re.search(r"[0-9０-９%％]", text) for text in [value["summary"], *(row["interpretation"] for row in observations), *hypotheses]):
        raise ValueError("quantitative narrative must use the deterministic fact cards")
    return {"analysis_status": "generated", "summary": value["summary"], "observations": observations,
            "hypothesis_updates": hypotheses, "review_note": "模型解释与待验证假设；数值以关联字段为准，未完成独立原文审计。"}
