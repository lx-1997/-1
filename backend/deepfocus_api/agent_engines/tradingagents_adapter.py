from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .base import AgentEngineRunContext, AgentEngineRunResult
from ..model_config import load_model_config


TRADINGAGENTS_TIMEOUT_SECONDS = int(os.getenv("DEEPFOCUS_TRADINGAGENTS_TIMEOUT_SECONDS", "600"))
TRADINGAGENTS_HEARTBEAT_SECONDS = float(os.getenv("DEEPFOCUS_TRADINGAGENTS_HEARTBEAT_SECONDS", "20"))
TRADINGAGENTS_TERMINATE_GRACE_SECONDS = float(
    os.getenv("DEEPFOCUS_TRADINGAGENTS_TERMINATE_GRACE_SECONDS", "8")
)
PROJECT_ROOT = Path(__file__).resolve().parents[3]
BUNDLED_RUNTIME_PYTHON = PROJECT_ROOT / "modules" / "tradingagents-runtime" / ".venv" / "bin" / "python"
BUNDLED_RESULTS_DIR = PROJECT_ROOT / "modules" / "tradingagents-runtime" / "results"


def _safe_text(value: Any, limit: int = 900) -> str:
    text = str(value or "").strip()
    return text[:limit] + ("..." if len(text) > limit else "")


def _evidence_cards(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "title": str(item.get("title") or "未命名资料"),
            "source": str(item.get("source") or "DeepFocus Evidence"),
            "source_type": str(item.get("source_type") or "evidence"),
            "tags": item.get("tags") or [],
            "credibility_score": item.get("credibility_score", 0.5),
            "url": item.get("url"),
            "takeaway": str(item.get("text") or item.get("summary") or "")[:180],
        }
        for item in evidence[:8]
    ]


class TradingAgentsAdapter:
    key = "tradingagents"
    label = "TradingAgents"

    async def run(self, context: AgentEngineRunContext) -> AgentEngineRunResult:
        symbol = (context.symbol or "").strip().upper()
        if not symbol:
            return AgentEngineRunResult(
                result=self._setup_result(context, "缺少 ticker，TradingAgents 需要明确的股票代码。", installed=False),
                logs=[("TradingAgents", "缺少 ticker，已返回配置诊断。", 86)],
            )

        config_issue = self._model_config_issue(context.payload)
        if config_issue:
            return AgentEngineRunResult(
                result=self._setup_result(context, config_issue, installed=True),
                logs=[("TradingAgents", "全局模型配置未就绪，已返回可复核诊断。", 86)],
            )

        try:
            raw_decision = await asyncio.to_thread(self._run_sync, context)
        except ModuleNotFoundError as exc:
            missing = exc.name or "tradingagents"
            return AgentEngineRunResult(
                result=self._setup_result(
                    context,
                    f"当前运行环境缺少 {missing}；已生成 TradingAgents 融合诊断报告。",
                    installed=False,
                ),
                logs=[("TradingAgents", f"未检测到 {missing}，任务以融合诊断完成。", 86)],
            )
        except Exception as exc:
            return AgentEngineRunResult(
                result=self._setup_result(context, f"TradingAgents 运行失败：{exc}", installed=True),
                logs=[("TradingAgents", f"运行失败，已返回可复核诊断：{exc}", 86)],
            )

        return AgentEngineRunResult(
            result=self._map_decision(context, raw_decision),
            logs=[
                ("ResearchAgent", "TradingAgents 分析、辩论和交易研究链路已完成。", 86),
                ("ReportAgent", "TradingAgents decision 已映射为 DeepFocus 投资任务结果。", 94),
            ],
        )

    def _run_sync(self, context: AgentEngineRunContext) -> Any:
        external_python = self._runtime_python()
        if external_python:
            return self._run_external(context, external_python)
        return self._run_in_process(context)

    def _runtime_python(self) -> str:
        configured = str(os.getenv("DEEPFOCUS_TRADINGAGENTS_PYTHON") or "").strip()
        if configured:
            return configured
        if BUNDLED_RUNTIME_PYTHON.exists():
            return str(BUNDLED_RUNTIME_PYTHON)
        return ""

    def _run_external(self, context: AgentEngineRunContext, python_bin: str) -> Any:
        runner_path = Path(__file__).with_name("tradingagents_runner.py")
        engine_config = context.payload.get("engine_config") or {}
        request = {
            "symbol": context.symbol.upper(),
            "analysis_date": self._analysis_date(context.payload),
            "config_overrides": self._config_overrides(context.payload),
            "dry_run": bool(engine_config.get("dry_run")),
        }
        timeout_seconds = int(engine_config.get("timeout_seconds") or TRADINGAGENTS_TIMEOUT_SECONDS)
        payload = json.dumps(request, ensure_ascii=False).encode("utf-8")
        stdout_text = ""
        stderr_text = ""
        with tempfile.TemporaryFile(mode="w+b") as stdout_file, tempfile.TemporaryFile(mode="w+b") as stderr_file:
            process = subprocess.Popen(
                [python_bin, str(runner_path)],
                stdin=subprocess.PIPE,
                stdout=stdout_file,
                stderr=stderr_file,
                env=self._runtime_env(context.payload),
                start_new_session=True,
            )
            if context.register_runtime_process:
                context.register_runtime_process(process.pid, "tradingagents_external")
            try:
                assert process.stdin is not None
                process.stdin.write(payload)
                process.stdin.close()
                self._wait_for_external_runner(context, process, timeout_seconds)
            finally:
                stdout_file.seek(0)
                stderr_file.seek(0)
                stdout_text = stdout_file.read().decode("utf-8", errors="replace")
                stderr_text = stderr_file.read().decode("utf-8", errors="replace")

        data = self._parse_runner_output(stdout_text)
        if process.returncode != 0 and not data:
            details = (stderr_text or stdout_text or "").strip()
            raise RuntimeError(details or f"external runner exited with code {process.returncode}")
        if not data.get("ok"):
            details = str(data.get("error") or "external runner returned empty error")
            if data.get("traceback"):
                details = f"{details}\n{data['traceback']}"
            raise RuntimeError(details)
        return data.get("decision")

    def _wait_for_external_runner(
        self,
        context: AgentEngineRunContext,
        process: subprocess.Popen[bytes],
        timeout_seconds: int,
    ) -> None:
        start = time.monotonic()
        next_heartbeat = start
        last_progress = 85
        while True:
            returncode = process.poll()
            if returncode is not None:
                return

            elapsed = time.monotonic() - start
            if elapsed > timeout_seconds:
                self._terminate_external_runner(process)
                raise TimeoutError(f"TradingAgents 外部运行超过 {timeout_seconds} 秒，已终止。")

            if time.monotonic() >= next_heartbeat:
                progress = min(94, max(last_progress, 85 + int((elapsed / max(timeout_seconds, 1)) * 9)))
                last_progress = progress
                if context.heartbeat:
                    keep_running = context.heartbeat(
                        f"TradingAgents 外部长任务运行中，已运行约 {int(elapsed // 60)} 分钟。",
                        progress,
                    )
                    if keep_running is False:
                        self._terminate_external_runner(process)
                        raise RuntimeError("任务已取消或不再处于 running 状态，TradingAgents 子进程已终止。")
                next_heartbeat = time.monotonic() + max(5.0, TRADINGAGENTS_HEARTBEAT_SECONDS)

            time.sleep(1.0)

    def _terminate_external_runner(self, process: subprocess.Popen[bytes]) -> None:
        if process.poll() is not None:
            return
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except Exception:
            process.terminate()
        try:
            process.wait(timeout=TRADINGAGENTS_TERMINATE_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except Exception:
                process.kill()
            process.wait(timeout=TRADINGAGENTS_TERMINATE_GRACE_SECONDS)

    def _parse_runner_output(self, stdout: str) -> dict[str, Any]:
        text = (stdout or "").strip()
        if not text:
            return {}
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            lines = [line for line in text.splitlines() if line.strip()]
            for line in reversed(lines):
                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(data, dict):
                    return data
            return {}

    def _run_in_process(self, context: AgentEngineRunContext) -> Any:
        from tradingagents.graph.trading_graph import TradingAgentsGraph

        try:
            from tradingagents.default_config import DEFAULT_CONFIG

            config = DEFAULT_CONFIG.copy()
            config.update(
                self._config_overrides(
                    context.payload,
                    fallback_provider=config.get("llm_provider"),
                    legacy=True,
                )
            )
        except ModuleNotFoundError:
            from tradingagents.config import TradingAgentsConfig

            config = TradingAgentsConfig(
                **self._config_overrides(context.payload, legacy=False)
            )
        graph = TradingAgentsGraph(debug=False, config=config)
        _, decision = graph.propagate(context.symbol.upper(), self._analysis_date(context.payload))
        return decision

    def _config_overrides(
        self,
        payload: dict[str, Any],
        fallback_provider: Any = None,
        legacy: bool = False,
    ) -> dict[str, Any]:
        overrides: dict[str, Any] = {}
        engine_config = payload.get("engine_config") or {}
        model_config = self._model_config()
        provider = self._normalize_provider(
            engine_config.get("tradingagents_provider")
            or payload.get("tradingagents_provider")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_PROVIDER")
            or model_config.get("provider")
            or os.getenv("DEEPFOCUS_LLM_PROVIDER")
            or fallback_provider
            or "openai"
        )
        overrides["llm_provider"] = provider
        rounds = (
            engine_config.get("max_debate_rounds")
            or payload.get("max_debate_rounds")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_DEBATE_ROUNDS")
        )
        if rounds:
            overrides["max_debate_rounds"] = max(1, min(5, int(rounds)))
        elif not legacy:
            overrides["max_debate_rounds"] = 1
        risk_rounds = (
            engine_config.get("max_risk_discuss_rounds")
            or payload.get("max_risk_discuss_rounds")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_RISK_ROUNDS")
        )
        if risk_rounds:
            overrides["max_risk_discuss_rounds"] = max(1, min(5, int(risk_rounds)))
        elif not legacy:
            overrides["max_risk_discuss_rounds"] = 1
        recur_limit = (
            engine_config.get("max_recur_limit")
            or payload.get("max_recur_limit")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_RECUR_LIMIT")
        )
        if recur_limit:
            overrides["max_recur_limit"] = max(30, min(200, int(recur_limit)))
        elif not legacy:
            overrides["max_recur_limit"] = 60
        quick_model = self._usable_model(
            engine_config.get("quick_think_llm")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_QUICK_MODEL")
            or model_config.get("model")
            or os.getenv("OPENAI_MODEL")
        )
        deep_model = self._usable_model(
            engine_config.get("deep_think_llm")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_DEEP_MODEL")
            or model_config.get("model")
            or os.getenv("OPENAI_MODEL")
        )
        if quick_model:
            overrides["quick_think_llm"] = quick_model
        elif not legacy:
            overrides["quick_think_llm"] = "gpt-4o-mini"
        if deep_model:
            overrides["deep_think_llm"] = deep_model
        elif not legacy:
            overrides["deep_think_llm"] = "gpt-4o"
        if not legacy:
            overrides["reasoning_effort"] = str(
                engine_config.get("reasoning_effort")
                or os.getenv("DEEPFOCUS_TRADINGAGENTS_REASONING")
                or "medium"
            )
            overrides["response_language"] = str(
                engine_config.get("response_language")
                or os.getenv("DEEPFOCUS_TRADINGAGENTS_LANGUAGE")
                or "zh-CN"
            )
            overrides["results_dir"] = str(engine_config.get("results_dir") or BUNDLED_RESULTS_DIR)
            tool_timeout = (
                engine_config.get("tool_timeout_seconds")
                or os.getenv("DEEPFOCUS_TRADINGAGENTS_TOOL_TIMEOUT")
                or 20
            )
            overrides["_tool_timeout_seconds"] = max(5, min(120, int(tool_timeout)))
            web_search_enabled = (
                engine_config.get("web_search_enabled")
                if "web_search_enabled" in engine_config
                else os.getenv("DEEPFOCUS_TRADINGAGENTS_WEB_SEARCH", "1")
            )
            overrides["_web_search_enabled"] = str(web_search_enabled).strip().lower() not in {
                "0",
                "false",
                "no",
                "off",
            }
            web_search_limit = (
                engine_config.get("web_search_limit")
                or os.getenv("DEEPFOCUS_TRADINGAGENTS_WEB_SEARCH_LIMIT")
                or 6
            )
            overrides["_web_search_limit"] = max(1, min(10, int(web_search_limit)))
            web_search_timeout = (
                engine_config.get("web_search_timeout_seconds")
                or os.getenv("DEEPFOCUS_TRADINGAGENTS_WEB_SEARCH_TIMEOUT")
                or 8
            )
            overrides["_web_search_timeout_seconds"] = max(3, min(30, int(web_search_timeout)))
            selected_analysts = (
                engine_config.get("selected_analysts")
                or payload.get("selected_analysts")
                or os.getenv("DEEPFOCUS_TRADINGAGENTS_ANALYSTS")
                or "market,news,fundamentals"
            )
            if isinstance(selected_analysts, str):
                overrides["selected_analysts"] = [
                    item.strip() for item in selected_analysts.split(",") if item.strip()
                ]
            elif isinstance(selected_analysts, list):
                overrides["selected_analysts"] = selected_analysts
        return overrides

    def _normalize_provider(self, provider: Any) -> str:
        value = str(provider or "openai").strip()
        aliases = {
            "mock": "openai",
            "cloud": "openai",
            "openai-compatible": "openai",
            "minimax": "openai",
            "google": "google_genai",
            "gemini": "google_genai",
        }
        return aliases.get(value, value)

    def _usable_model(self, model: Any) -> str:
        value = str(model or "").strip()
        lowered = value.lower()
        if (
            not value
            or "你的" in value
            or lowered in {"your-model", "model"}
            or lowered.startswith("mock")
        ):
            return ""
        return value

    def _model_config(self) -> dict[str, Any]:
        try:
            return load_model_config()
        except Exception:
            return {}

    def _runtime_env(self, payload: dict[str, Any]) -> dict[str, str]:
        env = os.environ.copy()
        model_config = self._model_config()
        engine_config = payload.get("engine_config") or {}
        provider = str(model_config.get("provider") or "").strip().lower()
        api_key = str(model_config.get("api_key") or "").strip()
        base_url = str(model_config.get("base_url") or "").strip()
        model = self._usable_model(model_config.get("model"))

        if api_key:
            if provider == "minimax":
                env.setdefault("MINIMAX_API_KEY", api_key)
                env.setdefault("MINIMAX_MODEL", model or "MiniMax-M3")
                if base_url:
                    env.setdefault("MINIMAX_BASE_URL", base_url)
            env["OPENAI_API_KEY"] = api_key
            env.setdefault("LITELLM_API_KEY", api_key)

        if base_url:
            env["OPENAI_BASE_URL"] = base_url
            env["OPENAI_API_BASE"] = base_url
            env.setdefault("LITELLM_API_BASE", base_url)

        if model:
            env["OPENAI_MODEL"] = model
        elif not self._usable_model(env.get("OPENAI_MODEL")):
            env.pop("OPENAI_MODEL", None)

        for key in [
            "OPENAI_API_KEY",
            "OPENAI_BASE_URL",
            "OPENAI_API_BASE",
            "ANTHROPIC_API_KEY",
            "GOOGLE_API_KEY",
            "GEMINI_API_KEY",
            "OPENROUTER_API_KEY",
            "XAI_API_KEY",
        ]:
            if engine_config.get(key):
                env[key] = str(engine_config[key])

        return env

    def _model_config_issue(self, payload: dict[str, Any]) -> str:
        engine_config = payload.get("engine_config") or {}
        if engine_config.get("dry_run"):
            return ""

        model_config = self._model_config()
        provider = str(
            engine_config.get("tradingagents_provider")
            or payload.get("tradingagents_provider")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_PROVIDER")
            or model_config.get("provider")
            or os.getenv("DEEPFOCUS_LLM_PROVIDER")
            or ""
        ).strip().lower()
        model = self._usable_model(
            engine_config.get("quick_think_llm")
            or engine_config.get("deep_think_llm")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_QUICK_MODEL")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_DEEP_MODEL")
            or model_config.get("model")
            or os.getenv("OPENAI_MODEL")
        )
        has_key = any(
            str(value or "").strip()
            for value in [
                engine_config.get("OPENAI_API_KEY"),
                engine_config.get("MINIMAX_API_KEY"),
                engine_config.get("ANTHROPIC_API_KEY"),
                engine_config.get("GOOGLE_API_KEY"),
                engine_config.get("GEMINI_API_KEY"),
                engine_config.get("OPENROUTER_API_KEY"),
                engine_config.get("XAI_API_KEY"),
                model_config.get("api_key"),
                os.getenv("OPENAI_API_KEY"),
                os.getenv("MINIMAX_API_KEY"),
                os.getenv("ANTHROPIC_API_KEY"),
                os.getenv("GOOGLE_API_KEY"),
                os.getenv("GEMINI_API_KEY"),
                os.getenv("OPENROUTER_API_KEY"),
                os.getenv("XAI_API_KEY"),
            ]
        )

        if provider in {"", "mock"} and not has_key:
            return "TradingAgents 已关联全局模型配置，但当前仍是 mock 模式。请先在 设置 → 模型配置 中保存真实模型、API Key 和 Base URL。"
        if not model:
            return "TradingAgents 已读取全局模型配置，但模型名不可用。请在 设置 → 模型配置 中填写真实模型名。"
        if not has_key and provider not in {"ollama"}:
            return "TradingAgents 已读取全局模型配置，但没有检测到可用 API Key。请在 设置 → 模型配置 中保存 API Key。"
        return ""

    def _analysis_date(self, payload: dict[str, Any]) -> str:
        return str(
            payload.get("analysis_date")
            or os.getenv("DEEPFOCUS_TRADINGAGENTS_DATE")
            or datetime.now(timezone.utc).date().isoformat()
        )

    def _map_decision(self, context: AgentEngineRunContext, raw_decision: Any) -> dict[str, Any]:
        raw_text = (
            json.dumps(raw_decision, ensure_ascii=False, indent=2)
            if isinstance(raw_decision, dict)
            else _safe_text(raw_decision, 6000)
        )
        lowered = raw_text.lower()
        decision = "research_more"
        signal = str(raw_decision.get("signal", "")).upper() if isinstance(raw_decision, dict) else ""
        if signal == "BUY" or any(token in lowered for token in ["strong buy", "buy", "candidate", "long"]):
            decision = "candidate"
        elif signal == "SELL" or any(token in lowered for token in ["sell", "short", "avoid", "reduce"]):
            decision = "avoid"
        elif signal == "HOLD" or any(token in lowered for token in ["hold", "neutral", "watch"]):
            decision = "watch"

        symbol = context.symbol or "目标资产"
        name = context.asset_name or symbol
        confidence = raw_decision.get("confidence") if isinstance(raw_decision, dict) else None
        try:
            confidence_value = max(0.0, min(1.0, float(confidence)))
        except (TypeError, ValueError):
            confidence_value = 0.72 if decision == "candidate" else 0.62
        rationale = str(raw_decision.get("rationale") or "") if isinstance(raw_decision, dict) else ""
        return {
            "engine": self.key,
            "engine_label": self.label,
            "engine_status": "completed",
            "investor_summary": (
                f"{name}（{symbol}）已由 TradingAgents 多角色链路完成推演。"
                "结果已映射回 DeepFocus 的投资者报告结构，建议结合右侧证据继续人工复核。"
            ),
            "decision": decision,
            "confidence": confidence_value,
            "agent_findings": {
                "orchestrator": ["任务已路由到 TradingAgents 分析引擎", "对用户展示为 DeepFocus 四个核心角色 + 输出层"],
                "evidence": ["需与 DeepFocus 本地研报、财报和新闻证据交叉验证", "模型输出和证据冲突时，以可追溯证据优先"],
                "research": [
                    "TradingAgents fundamentals / sentiment / news / technical 分析已参与评估",
                    "Bull / Bear debate 与 trader proposal 已纳入研究判断",
                    "情绪和技术信号不能单独触发交易动作",
                ],
                "risk": [
                    "Risk Manager / Portfolio Manager 决策已作为参考",
                    *([rationale] if rationale else []),
                    "DeepFocus 不自动下单，所有动作需要人工确认",
                ],
                "report": ["TradingAgents 输出已映射为 DeepFocus 投资报告", "原始输出保留在 artifacts 里供人工复核"],
            },
            "scenarios": [
                {
                    "case": "bull",
                    "probability": 30,
                    "thesis": "TradingAgents 综合信号偏正面，若基本面和价格继续确认，可进入候选机会。",
                    "triggers": ["业绩确认", "资金流入", "技术结构改善"],
                },
                {
                    "case": "base",
                    "probability": 45,
                    "thesis": "分析引擎结论仍需 DeepFocus 证据层补强，当前以继续研究和观察为主。",
                    "triggers": ["补齐研报证据", "复核财报", "确认市场情绪"],
                },
                {
                    "case": "bear",
                    "probability": 25,
                    "thesis": "若核心假设被反证或风险管理拒绝，应降低置信度并暂避。",
                    "triggers": ["指引下修", "价格跌破纪律线", "风险事件放大"],
                },
            ],
            "risk_controls": [
                "TradingAgents 输出只作为研究意见，不直接转化为交易指令",
                "执行前必须确认仓位上限、止损条件和事件风险",
                "模型输出和 DeepFocus 证据冲突时，以可追溯证据优先",
            ],
            "action_plan": [
                "把 TradingAgents 原始决策与本地研报证据逐条对齐",
                "补齐最近财报、电话会和关键新闻",
                "把 bull/bear 分歧转化为观察触发器",
                *self._structured_trade_actions(raw_decision),
            ],
            "watchlist": ["模型置信度变化", "关键催化兑现", "价格与成交量确认", "反证事件"],
            "disconfirming_evidence": [
                "核心假设没有证据支持",
                "风险经理结论与交易计划冲突",
                "价格行为和基本面叙事背离",
            ],
            "evidence": _evidence_cards(context.evidence),
            "plain_language_takeaway": "TradingAgents 适合增强投研辩论，但最终仍要回到 DeepFocus 的证据链和人工复核。",
            "disclaimer": "仅供投研参考，不构成投资建议、收益承诺或自动交易指令。",
            "artifacts": [
                {
                    "type": "raw_decision",
                    "title": "TradingAgents 原始输出",
                    "content": raw_text,
                }
            ],
        }

    def _setup_result(self, context: AgentEngineRunContext, message: str, installed: bool) -> dict[str, Any]:
        symbol = context.symbol or "目标资产"
        python_hint = (
            f"当前后端 Python：{sys.version_info.major}.{sys.version_info.minor}；"
            "TradingAgents 官方包要求 Python >=3.10。"
        )
        return {
            "engine": self.key,
            "engine_label": self.label,
            "engine_status": "runtime_error" if installed else "setup_required",
            "investor_summary": message,
            "decision": "research_more",
            "confidence": 0.4,
            "agent_findings": {
                "orchestrator": [
                    "DeepFocus 已预留 TradingAgents 引擎接口",
                    f"项目内置运行时：{BUNDLED_RUNTIME_PYTHON}",
                    "任务队列、证据层、结果映射和 UI 可继续使用",
                    "配置运行时后将调用 TradingAgentsGraph.propagate(ticker, date)",
                ],
                "evidence": [
                    python_hint,
                    "项目内置运行时路径为 modules/tradingagents-runtime/.venv",
                    "可以执行 npm run tradingagents:install 重新安装该运行时",
                    "如需使用其他运行时，设置 DEEPFOCUS_TRADINGAGENTS_PYTHON 指向 python 可执行文件",
                    "模型 Provider、模型名、API Key 和 Base URL 会优先读取 设置 → 模型配置",
                    "行情数据 key 仍按 TradingAgents 数据源要求配置，例如 ALPHA_VANTAGE_API_KEY",
                ],
                "risk": [
                    "TradingAgents 是 research framework，不应直接下单",
                    "所有输出必须绑定 DeepFocus 证据并人工复核",
                ],
                "report": ["运行环境待补齐时，只输出配置诊断和下一步动作"],
            },
            "scenarios": [
                {
                    "case": "integration",
                    "probability": 100,
                    "thesis": f"{symbol} 任务已完成融合诊断，下一步是安装并配置 TradingAgents 运行环境。",
                    "triggers": ["安装 tradingagents", "配置模型 key", "跑通单 ticker POC"],
                }
            ],
            "risk_controls": ["未跑通前不启用自动交易", "外部引擎输出必须保留原文和映射记录"],
            "action_plan": [
                "确认 modules/tradingagents-runtime/.venv 已安装 tradingagents",
                "在 设置 → 模型配置 保存真实模型、API Key 和 Base URL",
                "只有需要覆盖默认行为时，再配置 DEEPFOCUS_TRADINGAGENTS_* 环境变量",
                "先用单 ticker 小轮数运行，再逐步提高 debate rounds",
            ],
            "watchlist": ["依赖安装状态", "模型 key", "行情数据 key", "单 ticker POC"],
            "disconfirming_evidence": ["无法稳定获取行情", "模型返回空结果", "输出无法映射到证据链"],
            "evidence": _evidence_cards(context.evidence),
            "plain_language_takeaway": "接口已经接好；TradingAgents 会读取全局模型配置，缺什么会在这里给出诊断。",
            "disclaimer": "仅供投研参考，不构成投资建议、收益承诺或自动交易指令。",
        }

    def _structured_trade_actions(self, raw_decision: Any) -> list[str]:
        if not isinstance(raw_decision, dict):
            return []
        actions = []
        size = raw_decision.get("size_fraction")
        target = raw_decision.get("target_price")
        stop = raw_decision.get("stop_loss")
        horizon = raw_decision.get("time_horizon_days")
        if size is not None:
            actions.append(f"TradingAgents 建议仓位比例参考：{size}")
        if target is not None:
            actions.append(f"目标价参考：{target}")
        if stop is not None:
            actions.append(f"止损价参考：{stop}")
        if horizon is not None:
            actions.append(f"建议验证周期：{horizon} 天")
        return actions
