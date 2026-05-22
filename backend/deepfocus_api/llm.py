from __future__ import annotations

import asyncio
import json
import os
import re
from datetime import datetime, timezone
from typing import Any

from openai import AsyncOpenAI

from .model_config import load_model_config
from .schemas import (
    AgentBriefRequest,
    Capability,
    CapabilityListResponse,
    CorridorRiskRequest,
    FinGptTaskResponse,
    ForecastRequest,
    GeneralChatRequest,
    GeneralChatResponse,
    NewsSummaryRequest,
    OptionsAiAnalysisRequest,
    OptionsAiAnalysisResponse,
    OrchestratorChatRequest,
    OrchestratorChatResponse,
    RagQueryRequest,
    ReportAnalysisRequest,
    SentimentResponse,
    StockAnalysisRequest,
    StockAnalysisResponse,
)


class CloudResearchLLM:
    """Small cloud-model adapter for DeepFocus.

    The FinGPT project stays in backend/finogrid as the financial framework.
    This adapter avoids local GPU inference and calls an OpenAI-compatible cloud
    endpoint when configured.
    """

    def __init__(self) -> None:
        pass

    @property
    def config(self) -> dict[str, Any]:
        return load_model_config()

    @property
    def provider(self) -> str:
        return self.config["provider"]

    @property
    def model(self) -> str:
        return self.config["model"]

    @property
    def provider_name(self) -> str:
        if self.provider in {"openai-compatible", "cloud"}:
            return "openai-compatible"
        return self.provider

    def _client(self) -> AsyncOpenAI:
        config = self.config
        provider = config["provider"]
        if provider == "minimax":
            api_key = config.get("api_key")
            if not api_key:
                raise RuntimeError("当前 MiniMax 模型缺少 API Key，请在设置 → 模型配置中保存 API Key。")
            return AsyncOpenAI(
                api_key=api_key,
                base_url=config.get("base_url") or "https://api.minimax.io/v1",
            )

        if provider in {"openai", "openai-compatible", "cloud"}:
            api_key = config.get("api_key")
            if not api_key:
                raise RuntimeError(f"当前 {provider} 模型缺少 API Key，请在设置 → 模型配置中保存 API Key。")
            base_url = config.get("base_url") or None
            return AsyncOpenAI(api_key=api_key, base_url=base_url)

        raise RuntimeError(f"Unsupported DEEPFOCUS_LLM_PROVIDER={provider}")

    async def complete_json(
        self,
        prompt: str,
        max_tokens: int = 2200,
        timeout_seconds: float = 35,
        force_json_first: bool = True,
        retry_schema_hint: str | None = None,
    ) -> dict[str, Any]:
        if self.provider == "mock":
            raise RuntimeError("mock provider does not call cloud completion")

        text = await self._complete_text(
            prompt,
            max_tokens=max_tokens,
            force_json=force_json_first,
            timeout_seconds=timeout_seconds,
        )
        try:
            data = _extract_json(text)
            if _has_meaningful_json(data):
                return data
        except ValueError:
            pass

        retry_prompt = (
            f"{prompt}\n\n"
            "上一次输出不是有效 JSON 或内容为空。请重新生成更短的 JSON object："
            f"{retry_schema_hint or '必须填充 title, summary, key_points, signals, risks, actions, sources, confidence；'}"
            "不要 Markdown，不要解释文字；数组字段最多 3 项，每项不超过 18 个中文字符。"
        )
        retry_text = await self._complete_text(
            retry_prompt,
            max_tokens=max_tokens,
            force_json=False,
            timeout_seconds=timeout_seconds,
        )
        try:
            retry_data = _extract_json(retry_text)
            if _has_meaningful_json(retry_data):
                return retry_data
        except ValueError as exc:
            retry_prompt = (
                f"{prompt}\n\n"
                "上一次输出不是合法 JSON。请重新生成更短的严格 JSON："
                f"{retry_schema_hint or ''}"
                "不要 Markdown，不要解释文字；数组字段最多 5 项，每项不超过 24 个中文字符。"
            )
            retry_text = await self._complete_text(
                retry_prompt,
                max_tokens=max_tokens,
                force_json=True,
                timeout_seconds=timeout_seconds,
            )
            try:
                final_data = _extract_json(retry_text)
                if _has_meaningful_json(final_data):
                    return final_data
            except ValueError as exc:
                raise ValueError(
                    "模型返回格式不完整，已自动重试但仍无法解析。请重试，或在模型配置中选择支持 JSON 输出/更大输出长度的模型。"
                ) from exc
        raise ValueError("模型返回了空 JSON，已自动重试但仍没有有效解读内容。")

    async def _complete_text(
        self,
        prompt: str,
        max_tokens: int,
        force_json: bool,
        timeout_seconds: float,
    ) -> str:
        config = self.config
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是金融投研助手。输出必须是严格 JSON object，不要包含 Markdown。"
                        "结论要谨慎，避免确定性交易建议。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
        }
        if _is_kimi_switchable_thinking_model(self.model):
            # Kimi K2.6/K2.5 enable thinking by default; for structured JSON calls
            # that can spend the whole token budget on reasoning_content and return
            # an empty content field. Disable it here and show product-level
            # reasoning summaries from the Agent event stream instead.
            payload["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            payload["temperature"] = max(0.01, min(config["temperature"], 1.0))
        if force_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = await asyncio.wait_for(
                self._client().chat.completions.create(**payload),
                timeout=timeout_seconds,
            )
        except Exception as exc:
            if force_json and _looks_like_response_format_error(exc):
                payload.pop("response_format", None)
                try:
                    response = await asyncio.wait_for(
                        self._client().chat.completions.create(**payload),
                        timeout=timeout_seconds,
                    )
                except asyncio.TimeoutError as timeout_exc:
                    raise RuntimeError(f"云模型 {timeout_seconds:.0f} 秒内未返回，请稍后重试或换用更快的模型。") from timeout_exc
            elif isinstance(exc, asyncio.TimeoutError):
                raise RuntimeError(f"云模型 {timeout_seconds:.0f} 秒内未返回，请稍后重试或换用更快的模型。") from exc
            else:
                raise RuntimeError(f"云模型调用失败：{_clean_error(exc)}") from exc

        text = response.choices[0].message.content or "{}"
        return text

    def capabilities(self) -> CapabilityListResponse:
        mode = "mock" if self.provider == "mock" else "cloud"
        capabilities = [
            Capability(
                key="stock_analysis",
                name="个股投研",
                description="整合个股快照、社区内容和问题，生成投研摘要、催化因素和风险清单。",
                endpoint="/api/ai/stock-analysis",
                mode=mode,
            ),
            Capability(
                key="sentiment",
                name="金融情绪分析",
                description="对新闻、公告、社区文本做 positive/neutral/negative 情绪判断。",
                endpoint="/api/ai/sentiment",
                mode=mode,
            ),
            Capability(
                key="news_summary",
                name="新闻蒸馏",
                description="把多条新闻压缩成决策摘要、关键变化、风险和后续观察点。",
                endpoint="/api/fingpt/news-summary",
                mode=mode,
            ),
            Capability(
                key="report_analysis",
                name="财报/研报解读",
                description="从长文本报告中提炼核心结论、经营变化、风险和可验证问题。",
                endpoint="/api/fingpt/report-analysis",
                mode=mode,
            ),
            Capability(
                key="rag_query",
                name="RAG知识库问答",
                description="基于传入资料或 Finogrid 文档做检索式问答，返回引用来源。",
                endpoint="/api/fingpt/rag-query",
                mode=mode,
            ),
            Capability(
                key="forecast",
                name="预测与情景推演",
                description="参考 FinGPT-Forecaster 思路，输出短期方向情景和触发条件。",
                endpoint="/api/fingpt/forecast",
                mode=mode,
            ),
            Capability(
                key="corridor_risk",
                name="稳定币/通道风险",
                description="面向 Finogrid 支付通道，分析币种、地区和新闻风险信号。",
                endpoint="/api/fingpt/corridor-risk",
                mode=mode,
            ),
            Capability(
                key="agent_brief",
                name="Agent工作台",
                description="模拟 Finogrid 五类运营/审计/流程/支持/资金策略 Agent 的工作摘要。",
                endpoint="/api/fingpt/agent-brief",
                mode=mode,
            ),
        ]
        return CapabilityListResponse(
            provider=self.provider_name,
            model=self.model,
            capabilities=capabilities,
        )

    async def general_chat(self, request: GeneralChatRequest) -> GeneralChatResponse:
        if self.provider == "mock":
            return _mock_general_chat(request, self.provider_name, self.model)

        history = [
            {
                "role": str(item.get("role", "")).lower(),
                "content": str(item.get("content", "")).strip()[:1200],
            }
            for item in request.history[-8:]
            if str(item.get("role", "")).lower() in {"user", "assistant"} and str(item.get("content", "")).strip()
        ]
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    "你是 DeepFocus 的普通 AI 助手。默认像正常助手一样和用户自然对话，"
                    "可以解释产品、接上下文、澄清问题、给出简洁建议。"
                    "不要自称 OrchestratorAgent，不要展示 Agent 链路，不要说正在调用工具。"
                    "只有用户明确要求投研分析、研报解读、风险复核、行情判断、组合任务或上传文件时，"
                    "才简短提示可以启动投研分析工作流；不要在普通聊天里强行要求标的。"
                    "回答用中文，直接、友好、克制。"
                ),
            },
            *history,
            {"role": "user", "content": request.message},
        ]
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": 900,
        }
        if _is_kimi_switchable_thinking_model(self.model):
            payload["extra_body"] = {"thinking": {"type": "disabled"}}
        else:
            payload["temperature"] = max(0.01, min(self.config["temperature"], 0.9))

        try:
            response = await asyncio.wait_for(
                self._client().chat.completions.create(**payload),
                timeout=28,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError("普通聊天模型 28 秒内未返回，请稍后重试或换用更快的模型。") from exc
        except Exception as exc:
            raise RuntimeError(f"普通聊天模型调用失败：{_clean_error(exc)}") from exc

        content = (response.choices[0].message.content or "").strip()
        if not content:
            content = "我在。你继续说。"
        return GeneralChatResponse(
            provider=self.provider_name,
            model=self.model,
            generated_at=datetime.now(timezone.utc),
            title="DeepFocus",
            content=content[:1600],
        )

    async def analyze_stock(self, request: StockAnalysisRequest) -> StockAnalysisResponse:
        if self.provider == "mock":
            return _mock_stock_analysis(request, self.provider_name, self.model)

        payload = request.model_dump(by_alias=True)
        prompt = (
            "请基于下面的个股快照和社区/资讯内容，生成中文投研摘要。\n"
            "返回 JSON 字段必须为：executive_summary, sentiment_label, sentiment_score, "
            "risk_level, catalysts, risks, watch_items, suggested_questions。\n"
            "sentiment_label 只能是 positive/neutral/negative；sentiment_score 取 -1 到 1；"
            "risk_level 只能是 low/medium/high；数组字段每项不超过 28 个中文字符。\n"
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        data = await self.complete_json(prompt)
        return _normalize_stock_analysis(data, self.provider_name, self.model)

    async def analyze_options_trend(self, request: OptionsAiAnalysisRequest) -> OptionsAiAnalysisResponse:
        if self.provider == "mock":
            return _local_options_trend_analysis(request, self.provider_name, self.model)

        payload = request.model_dump(mode="json")
        prompt = (
            "你是期权链投研助手。只能基于输入的期权快照判断短期股价走势倾向，"
            "不要编造实时价格、新闻、财报或订单流；必须明确不确定性和反证条件。\n"
            "返回严格 JSON，字段必须为：trend_label, trend_score, confidence, time_horizon, "
            "thesis, key_drivers, upside_triggers, downside_triggers, watch_levels, risk_notes, suggested_action。\n"
            "trend_label 只能是 看涨/震荡偏强/震荡/震荡偏弱/看跌/不可判定；"
            "trend_score 取 0 到 100，confidence 取 0 到 1；"
            "数组字段最多 5 项，每项不超过 28 个中文字符；thesis 不超过 150 个中文字符；"
            "suggested_action 只能写观察、验证、风控动作，不能写确定买卖指令。\n"
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            data = await self.complete_json(
                prompt,
                max_tokens=1400,
                timeout_seconds=28,
                retry_schema_hint=(
                    "必须填充 trend_label, trend_score, confidence, time_horizon, thesis, "
                    "key_drivers, upside_triggers, downside_triggers, watch_levels, risk_notes, suggested_action。"
                ),
            )
            return _normalize_options_trend_analysis(data, request, self.provider_name, self.model)
        except Exception as exc:
            fallback = _local_options_trend_analysis(request, "local-rule", "options-trend-fallback-v1")
            warning = f"云模型暂不可用，已使用本地规则：{_clean_error(exc)}"
            fallback.risk_notes = [warning, *fallback.risk_notes][:6]
            return fallback

    async def score_sentiment(self, text: str) -> SentimentResponse:
        if self.provider == "mock":
            label, score = _quick_sentiment(text)
            return SentimentResponse(
                provider=self.provider_name,
                model=self.model,
                label=label,
                score=score,
                rationale="本地开发模式下基于关键词和涨跌语义粗略判断。",
            )

        prompt = (
            "判断下面金融文本的情绪。返回 JSON 字段：label, score, rationale。"
            "label 只能是 positive/neutral/negative，score 取 -1 到 1。\n"
            f"文本：{text[:2000]}"
        )
        data = await self.complete_json(prompt)
        label = _safe_label(data.get("label"))
        return SentimentResponse(
            provider=self.provider_name,
            model=self.model,
            label=label,
            score=_safe_score(data.get("score"), default=0),
            rationale=str(data.get("rationale") or "模型未给出解释。"),
        )

    async def summarize_news(self, request: NewsSummaryRequest) -> FinGptTaskResponse:
        payload = request.model_dump(by_alias=True)
        return await self._task(
            "news_summary",
            "新闻蒸馏",
            "请把这些金融新闻蒸馏成可操作的投研摘要，突出事实变化、影响路径、风险和待验证点。",
            payload,
            mock_payload=_mock_news_summary(request, self.provider_name, self.model),
        )

    async def analyze_report(self, request: ReportAnalysisRequest) -> FinGptTaskResponse:
        payload = request.model_dump(by_alias=True)
        payload["report_text"] = payload.get("report_text", "")[:12000]
        return await self._task(
            "report_analysis",
            "财报/研报解读",
            "请解读这份财报或研报，提炼核心结论、经营指标、风险、验证问题和下一步动作。",
            payload,
            mock_payload=_mock_report_analysis(request, self.provider_name, self.model),
        )

    async def analyze_wechat_article(self, article: dict[str, Any]) -> FinGptTaskResponse:
        if self.provider == "mock":
            return _mock_wechat_article(article, self.provider_name, self.model)

        payload = {
            "title": str(article.get("title") or "")[:180],
            "summary": str(article.get("summary") or "")[:420],
            "account": str(article.get("account") or "")[:80],
            "published_at": str(article.get("published_at") or article.get("published") or "")[:80],
            "symbol": str(article.get("symbol") or "")[:24],
            "keyword": str(article.get("keyword") or "")[:80],
            "tags": _safe_list(article.get("tags"))[:8],
            "url": str(article.get("url") or "")[:500],
        }
        prompt = (
            "这是微信公众号搜索结果快读，不是完整研报。只能基于标题、摘要、账号、时间、标的和链接做投研事件判断；"
            "不要编造正文没有出现的数字或结论。目标是给投资者 10 秒内判断是否值得打开原文。\n"
            "返回严格 JSON，字段必须为：title, summary, key_points, signals, risks, actions, sources, confidence。"
            "summary 不超过 90 个中文字符；key_points/signals/risks/actions 各 3 项以内，每项不超过 18 个中文字符；"
            "sources 填公众号名、发布时间或原文链接；证据不足必须在 risks/actions 里提示打开原文核验。\n"
            '格式示例：{"title":"...","summary":"...","key_points":["..."],'
            '"signals":["..."],"risks":["..."],"actions":["..."],"sources":["..."],"confidence":0.6}\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            data = await self.complete_json(prompt, max_tokens=900, timeout_seconds=3, force_json_first=False)
            result = _normalize_task_response(data, self.provider_name, self.model, "wechat_article", "公众号快读")
            if _is_low_value_task_response(result):
                raise ValueError("模型返回了空解读内容")
            return result
        except Exception as exc:
            return _fast_wechat_article(article, fallback_reason=_clean_error(exc))

    async def rag_query(self, request: RagQueryRequest) -> FinGptTaskResponse:
        payload = request.model_dump(by_alias=True)
        if not payload["documents"]:
            payload["documents"] = _load_default_docs()
        return await self._task(
            "rag_query",
            "RAG知识库问答",
            "请只基于给定 documents 回答问题；无法从资料推出时明确说明缺口，并列出引用来源。",
            payload,
            mock_payload=_mock_rag_query(request, self.provider_name, self.model),
        )

    async def forecast(self, request: ForecastRequest) -> FinGptTaskResponse:
        payload = request.model_dump(by_alias=True)
        return await self._task(
            "forecast",
            "预测与情景推演",
            "请参考 FinGPT-Forecaster 的风格，输出短期方向情景、正负催化、风险和验证条件。",
            payload,
            mock_payload=_mock_forecast(request, self.provider_name, self.model),
        )

    async def corridor_risk(self, request: CorridorRiskRequest) -> FinGptTaskResponse:
        payload = request.model_dump(by_alias=True)
        return await self._task(
            "corridor_risk",
            "稳定币/通道风险",
            "请分析稳定币资产、跨境通道和新闻事件对支付运营的风险影响。",
            payload,
            mock_payload=_mock_corridor_risk(request, self.provider_name, self.model),
        )

    async def agent_brief(self, request: AgentBriefRequest) -> FinGptTaskResponse:
        payload = request.model_dump(by_alias=True)
        return await self._task(
            "agent_brief",
            "Agent工作台",
            "请以指定 Finogrid Agent 角色输出运营摘要、发现、风险和下一步动作。",
            payload,
            mock_payload=_mock_agent_brief(request, self.provider_name, self.model),
        )

    async def analyze_customs_trade_agent(self, context: str) -> FinGptTaskResponse:
        if self.provider == "mock":
            return FinGptTaskResponse(
                provider=self.provider_name,
                model=self.model,
                generated_at=datetime.now(timezone.utc),
                capability="customs_trade_agent_analysis",
                title="中国海关进出口投研Agent",
                summary="已基于近12个月海关数据生成外贸与产业链投研框架。",
                key_points=["近12月趋势已读取", "结构分化需拆解", "关注量价背离"],
                signals=["关注AI算力链", "跟踪新能源出口链", "谨慎看待大宗进口"],
                risks=["单月波动较大", "价格影响金额", "转口扰动口径"],
                actions=[
                    "建议关注：专门电气设备出口链，代表股票：思源电气(002028)、特变电工(600089)、金盘科技(688676)",
                    "建议关注：AI服务器/PCB链，代表股票：工业富联(601138)、沪电股份(002463)、中际旭创(300308)",
                    "谨慎观察：电子零部件出口链，代表股票：立讯精密(002475)、歌尔股份(002241)",
                    "暂时回避：对美敞口高的传统出口链，代表股票：申洲国际(02313.HK)、华利集团(300979)、顾家家居(603816)",
                ],
                sources=["GACC Customs Statistics"],
                confidence=0.62,
            )

        prompt = (
            "你是 DeepFocus 的 CustomsTradeAgent，由 EvidenceAgent、ResearchAgent、RiskAgent、ReportAgent 协作。"
            "请基于中国海关官方数据做专业投资研究分析，并给出明确但非个性化的投资研究建议。\n"
            "必须体现近12个月趋势、环比/同比、结构分化、产业链传导、相关资产方向、反证指标。"
            "返回严格 JSON，字段必须为：title, summary, key_points, signals, risks, actions, sources, confidence。\n"
            "要求：summary 开头必须给出总体投资立场，例如“总体建议：偏积极/中性偏谨慎/防守观察”；"
            "key_points聚焦核心结论；signals写可投资产业链/主题/商品/ETF方向映射线索；"
            "risks写口径、周期和反证风险；actions必须写成明确投资建议，使用“建议关注：”“谨慎观察：”"
            "“暂时回避：”“加仓触发：”“减仓/止盈触发：”这类前缀。"
            "actions必须固定覆盖三类：至少2条“建议关注：”、至少2条“谨慎观察：”、至少1条“暂时回避：”，"
            "剩余1条可写触发条件。"
            "每条建议关注/谨慎观察/暂时回避都必须给出2-4个代表股票或ETF，格式为“代表股票：公司A(代码)、公司B(代码)”。"
            "不得用“相关标的”“中小标的”“代码待核实”替代代表股票；如果要写暂时回避，也必须从候选池中挑具体研究样本。"
            "如果建议涉及“电气设备/输变电/电力设备出海”，必须优先从专门电气设备候选池选择，"
            "例如思源电气、特变电工、金盘科技、中国西电、平高电气、许继电气；"
            "立讯精密、工业富联只能归入电子零部件/AI服务器代工链，不能作为专门电气设备代表。"
            "代表股票只作为研究样本池，不代表买入推荐；代码不确定时只写公司名，不要编造代码。"
            "不要给具体个股买卖指令、目标价、收益承诺或仓位比例；数组字段最多6项，每项不超过72个中文字符。"
            "不要 Markdown，不要输出 JSON 以外文字。\n"
            f"海关数据与分析材料：{context[:18000]}"
        )
        data = await self.complete_json(
            prompt,
            max_tokens=2800,
            timeout_seconds=45,
            retry_schema_hint="必须返回 customs trade investment analysis JSON。",
        )
        return _normalize_task_response(
            data,
            self.provider_name,
            self.model,
            "customs_trade_agent_analysis",
            "中国海关进出口投研Agent",
        )

    async def orchestrator_chat(self, request: OrchestratorChatRequest) -> OrchestratorChatResponse:
        literal_reply = _literal_inline_reply(request, self.provider_name, self.model)
        if literal_reply:
            return literal_reply

        if self.provider == "mock":
            return _mock_orchestrator_chat(request, self.provider_name, self.model)

        core_agents = ["OrchestratorAgent", "EvidenceAgent", "ResearchAgent", "RiskAgent", "ReportAgent"]
        engine_agents = {
            "deepfocus": core_agents,
            "tradingagents": core_agents,
            "financial_services": [
                "OrchestratorAgent",
                "EvidenceAgent",
                "FSIWorkflowAgent",
                "ControlAgent",
                "ReportAgent",
            ],
        }
        payload = request.model_dump(by_alias=True)
        history = [
            {
                "role": str(item.get("role", ""))[:16],
                "content": str(item.get("content", ""))[:1000],
            }
            for item in request.history[-8:]
            if str(item.get("role", "")).lower() in {"user", "assistant"} and str(item.get("content", "")).strip()
        ]
        prompt = (
            "你是 DeepFocus 多 Agent 工作台的 OrchestratorAgent，体验要像 Claude Code / Cursor / Codex："
            "用户发来任何消息，你都要正常用 AI 回复，而不是说未调用 Agent。"
            "你可以承认不知道，不能编造用户没有提供的私人事实；如果问题需要外部背景，直接说明需要用户补充。"
            "如果用户问题涉及投资、标的、研报、文件、风险、组合、行情或监控，要说明将如何调度 5 个核心 Agent。"
            "如果只是普通聊天、问候、联通测试，或用户要求精确简短回复，优先按字面直接回答；"
            "不要强行带入当前标的、投资上下文或 Agent 推荐，should_create_task 必须为 false。"
            "如果 reasoning_mode 是 fast，回答要短，reasoning_trace 返回空数组或最多 1 项；"
            "如果 reasoning_mode 是 thinking，要返回 3 到 5 项可展示推理摘要，体现目标识别、证据判断、风险约束和下一步。"
            "回复不要超过 220 个中文字符，像真实工作台助理，不要营销腔。\n"
            f"当前对用户可见的核心 Agent：{', '.join(engine_agents.get(request.engine, engine_agents['deepfocus']))}。"
            "TradingAgents 等底层引擎只作为执行映射，不要把内部角色当成同级 Agent 展示。"
            "如果 engine 是 financial_services，要强调会按金融服务 playbook 选择 market research、earnings review、model build、pitch、valuation review、KYC 或 reconciliation 路线。\n"
            f"最近对话上下文：{json.dumps(history, ensure_ascii=False)}\n"
            "返回严格 JSON，字段必须为：title, content, chips, suggested_actions, reasoning_trace, should_create_task, confidence。"
            "chips/suggested_actions 每项不超过 16 个中文字符；should_create_task 为布尔值。\n"
            "reasoning_trace 是给用户看的可审计思路摘要，不是隐藏推理原文；每项包含 phase, title, detail, status，"
            "status 只能是 done / working / wait / error，最多 5 项。\n"
            '格式示例：{"title":"OrchestratorAgent","content":"...","chips":["OrchestratorAgent"],'
            '"suggested_actions":["补充标的"],"reasoning_trace":[{"phase":"orchestrator","title":"OrchestratorAgent","detail":"判断是否需要长任务","status":"done"}],'
            '"should_create_task":false,"confidence":0.72}\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        try:
            data = await self.complete_json(prompt, max_tokens=1000, timeout_seconds=20, force_json_first=True)
            return _normalize_orchestrator_chat(data, request, self.provider_name, self.model)
        except Exception:
            try:
                text = await self._complete_orchestrator_text(request, engine_agents, timeout_seconds=18)
                return _orchestrator_text_response(text, request, self.provider_name, self.model)
            except Exception:
                return _mock_orchestrator_chat(request, self.provider_name, self.model)

    async def _complete_orchestrator_text(
        self,
        request: OrchestratorChatRequest,
        engine_agents: dict[str, list[str]],
        timeout_seconds: float = 18,
    ) -> str:
        config = self.config
        stock = request.stock
        stock_line = f"{stock.name}（{stock.symbol}）" if stock else "未选择标的"
        history = [
            f"{item.get('role')}: {str(item.get('content', '')).strip()[:700]}"
            for item in request.history[-8:]
            if str(item.get("role", "")).lower() in {"user", "assistant"} and str(item.get("content", "")).strip()
        ]
        prompt = (
            f"用户消息：{request.message}\n"
            f"最近对话：{chr(10).join(history) if history else '无'}\n"
            f"当前引擎：{request.engine}\n"
            f"当前标的：{stock_line}\n"
            f"模式：{request.mode}\n"
            f"思考模式：{request.reasoning_mode}\n"
            f"上传文件：{', '.join(request.attached_files) if request.attached_files else '无'}\n"
            f"资料库数量：{request.data_source_count}；工具连接数：{request.mcp_server_count}\n"
            f"对用户可见的核心 Agent：{', '.join(engine_agents.get(request.engine, engine_agents['deepfocus']))}\n"
            "请直接回复用户，不要 JSON，不要 Markdown 标题，不要编造私人信息。"
            "fast 模式要短，thinking 模式可以说明公开可展示的推理摘要。"
            "普通聊天和联通测试要按用户字面要求回复；只有投资研究问题才说明会调度哪些 Agent。"
            "回复不超过 220 个中文字符。"
        )
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是 DeepFocus 的 OrchestratorAgent。你是一个真实多 Agent 工作台入口，"
                        "负责理解用户消息、自然回复，并按需调度 Evidence/Research/Risk/Report 等核心 Agent。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": max(0.01, min(config["temperature"], 1.0)),
            "max_tokens": 650,
        }
        try:
            response = await asyncio.wait_for(
                self._client().chat.completions.create(**payload),
                timeout=timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"云模型 {timeout_seconds:.0f} 秒内未返回，请稍后重试或换用更快的模型。") from exc
        except Exception as exc:
            raise RuntimeError(f"云模型调用失败：{_clean_error(exc)}") from exc
        return response.choices[0].message.content or ""

    async def _task(
        self,
        capability: str,
        title: str,
        instruction: str,
        payload: dict[str, Any],
        mock_payload: FinGptTaskResponse,
    ) -> FinGptTaskResponse:
        if self.provider == "mock":
            return mock_payload

        prompt = (
            f"{instruction}\n"
            "返回严格 JSON，字段必须为：title, summary, key_points, signals, risks, "
            "actions, sources, confidence。confidence 取 0 到 1；数组字段最多 5 项，"
            "每项不超过 24 个中文字符；summary 不超过 120 个中文字符。\n"
            '格式示例：{"title":"...","summary":"...","key_points":["..."],'
            '"signals":["..."],"risks":["..."],"actions":["..."],"sources":["..."],"confidence":0.7}\n'
            f"输入：{json.dumps(payload, ensure_ascii=False)}"
        )
        data = await self.complete_json(prompt, max_tokens=2200)
        return _normalize_task_response(data, self.provider_name, self.model, capability, title)


def _extract_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            raise ValueError(f"Model did not return JSON: {text[:240]}")
        return json.loads(match.group(0))


def _has_meaningful_json(data: dict[str, Any]) -> bool:
    if not isinstance(data, dict) or not data:
        return False
    data = _unwrap_payload(data)
    for key in (
        "title",
        "标题",
        "summary",
        "摘要",
        "key_points",
        "要点",
        "signals",
        "信号",
        "risks",
        "风险",
        "actions",
        "动作",
    ):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, list) and any(str(item).strip() for item in value):
            return True
    return any(value not in (None, "", [], {}) for value in data.values())


def _looks_like_response_format_error(exc: Exception) -> bool:
    text = str(exc).lower()
    return "response_format" in text and any(
        marker in text
        for marker in (
            "unsupported",
            "not support",
            "invalid",
            "unknown",
            "unexpected",
            "不支持",
        )
    )


def _is_kimi_switchable_thinking_model(model: str) -> bool:
    model_name = str(model or "").lower()
    if "thinking" in model_name:
        return False
    return any(marker in model_name for marker in ("kimi-k2.6", "kimi-k2.5"))


def _clean_error(exc: Exception) -> str:
    text = str(exc).strip()
    lowered = text.lower()
    if "invalidsubscription" in lowered or "codingplan" in lowered:
        return (
            "火山 Ark 返回 InvalidSubscription：当前账号没有有效 CodingPlan 订阅或订阅已过期。"
            "模型配置已经生效，但该通道暂时不可调用；请在火山控制台续订/开通 CodingPlan，"
            "或在设置中切换到可用的 OpenAI-compatible Base URL / 模型。"
        )
    if "insufficient_quota" in lowered or "quota" in lowered or "billing" in lowered:
        return "云模型账号额度或计费状态不可用，请检查控制台额度、账单和订阅状态，或切换到可用模型。"
    if "invalid_api_key" in lowered or "unauthorized" in lowered or "authentication" in lowered:
        return "云模型鉴权失败，请在设置 → 模型配置中检查 API Key、Base URL 和模型名。"
    return re.sub(r"\s+", " ", text)[:400] or exc.__class__.__name__


def _normalize_stock_analysis(
    data: dict[str, Any],
    provider: str,
    model: str,
) -> StockAnalysisResponse:
    return StockAnalysisResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        executive_summary=str(data.get("executive_summary") or data.get("summary") or "暂无摘要。"),
        sentiment_label=_safe_label(data.get("sentiment_label")),
        sentiment_score=_safe_score(data.get("sentiment_score"), default=0),
        risk_level=_safe_risk(data.get("risk_level")),
        catalysts=_safe_list(data.get("catalysts")),
        risks=_safe_list(data.get("risks")),
        watch_items=_safe_list(data.get("watch_items")),
        suggested_questions=_safe_list(data.get("suggested_questions")),
    )


def _normalize_task_response(
    data: dict[str, Any],
    provider: str,
    model: str,
    capability: str,
    fallback_title: str,
) -> FinGptTaskResponse:
    data = _unwrap_payload(data)
    return FinGptTaskResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        capability=capability,
        title=str(_first_present(data, "title", "标题") or fallback_title),
        summary=str(_first_present(data, "summary", "摘要", "executive_summary", "核心摘要") or "暂无摘要。"),
        key_points=_safe_list(_first_present(data, "key_points", "keyPoints", "要点", "核心结论", "关键要点")),
        signals=_safe_list(_first_present(data, "signals", "信号", "投资信号", "催化因素", "catalysts")),
        risks=_safe_list(_first_present(data, "risks", "风险", "风险点", "主要风险")),
        actions=_safe_list(_first_present(data, "actions", "动作", "建议动作", "下一步动作", "watch_items")),
        sources=_safe_list(_first_present(data, "sources", "来源", "引用", "references")),
        confidence=_safe_confidence(_first_present(data, "confidence", "置信度")),
    )


def _normalize_options_trend_analysis(
    data: dict[str, Any],
    request: OptionsAiAnalysisRequest,
    provider: str,
    model: str,
) -> OptionsAiAnalysisResponse:
    data = _unwrap_payload(data)
    base = _local_options_trend_analysis(request, provider, model)
    return OptionsAiAnalysisResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        symbol=request.signal.symbol,
        trend_label=_safe_trend_label(_first_present(data, "trend_label", "走势", "趋势判断"), base.trend_label),
        trend_score=_safe_percent(_first_present(data, "trend_score", "score", "方向分"), base.trend_score),
        confidence=_safe_confidence(_first_present(data, "confidence", "置信度")),
        time_horizon=str(_first_present(data, "time_horizon", "horizon", "周期") or base.time_horizon)[:40],
        thesis=str(_first_present(data, "thesis", "summary", "摘要", "核心判断") or base.thesis)[:220],
        key_drivers=_safe_list(_first_present(data, "key_drivers", "drivers", "核心驱动", "关键依据"))[:5] or base.key_drivers,
        upside_triggers=_safe_list(_first_present(data, "upside_triggers", "bullish_triggers", "上行触发"))[:5] or base.upside_triggers,
        downside_triggers=_safe_list(_first_present(data, "downside_triggers", "bearish_triggers", "下行触发"))[:5] or base.downside_triggers,
        watch_levels=_safe_list(_first_present(data, "watch_levels", "levels", "观察价位"))[:5] or base.watch_levels,
        risk_notes=_safe_list(_first_present(data, "risk_notes", "risks", "风险提示"))[:5] or base.risk_notes,
        suggested_action=str(_first_present(data, "suggested_action", "action", "下一步动作") or base.suggested_action)[:160],
    )


def _local_options_trend_analysis(
    request: OptionsAiAnalysisRequest,
    provider: str,
    model: str,
) -> OptionsAiAnalysisResponse:
    signal = request.signal
    score = int(max(0, min(100, signal.score)))
    data_quality = int(max(0, min(100, signal.data_quality)))
    trend_label = _trend_label_from_score(score, data_quality, signal.direction)
    conviction_weight = {"高": 0.16, "中": 0.1, "低": 0.04}.get(signal.conviction, 0.04)
    confidence = (abs(score - 50) / 50) * 0.46 + (data_quality / 100) * 0.38 + conviction_weight
    if signal.source_status == "unavailable" or trend_label == "不可判定":
        confidence *= 0.45
    elif data_quality < 45:
        confidence *= 0.72
    confidence = max(0.12, min(0.88, confidence))

    thesis = (
        f"{signal.symbol} 在 {request.horizon_days} 日期权窗口内呈现{trend_label}倾向："
        f"方向分 {score}/100，数据质量 {data_quality}/100。{signal.summary}"
    )
    if trend_label == "不可判定":
        thesis = (
            f"{signal.symbol} 当前期权链不足以形成可靠走势判断；"
            f"方向分 {score}/100，数据质量 {data_quality}/100，优先补充实时链和价格确认。"
        )

    key_drivers = list(signal.signals[:4])
    if signal.term_structure and signal.term_structure not in key_drivers:
        key_drivers.append(signal.term_structure)
    if signal.unusual_flow_count:
        key_drivers.append(f"异常大单 {signal.unusual_flow_count} 条，权利金约 {_format_money(signal.unusual_premium_notional)}")
    key_drivers = [item for item in key_drivers if item][:5]
    if not key_drivers:
        key_drivers = ["期权链字段稀疏，暂以方向分和风险项为主"]

    upside_triggers = _options_upside_triggers(signal)
    downside_triggers = _options_downside_triggers(signal)
    watch_levels = _options_watch_levels(signal)
    risk_notes = [
        *signal.risk_flags[:4],
        "期权链无法单独确认主动买卖方向，需结合价格和成交量复核。",
    ][:5]

    suggested_action = _options_suggested_action(trend_label, signal)

    return OptionsAiAnalysisResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        symbol=signal.symbol,
        trend_label=trend_label,
        trend_score=score,
        confidence=round(confidence, 2),
        time_horizon=f"{request.horizon_days} 日内",
        thesis=thesis[:220],
        key_drivers=key_drivers,
        upside_triggers=upside_triggers,
        downside_triggers=downside_triggers,
        watch_levels=watch_levels,
        risk_notes=risk_notes,
        suggested_action=suggested_action,
    )


def _trend_label_from_score(score: int, data_quality: int, direction: str) -> str:
    if direction == "不可判定" or data_quality < 18:
        return "不可判定"
    if score >= 72:
        return "看涨"
    if score >= 58:
        return "震荡偏强"
    if score > 42:
        return "震荡"
    if score > 28:
        return "震荡偏弱"
    return "看跌"


def _safe_trend_label(value: Any, default: str) -> str:
    label = str(value or default).strip()
    return label if label in {"看涨", "震荡偏强", "震荡", "震荡偏弱", "看跌", "不可判定"} else default


def _safe_percent(value: Any, default: int) -> int:
    try:
        return int(round(max(0, min(100, float(value)))))
    except (TypeError, ValueError):
        return default


def _options_upside_triggers(signal: Any) -> list[str]:
    triggers: list[str] = []
    if signal.call_wall:
        triggers.append(f"放量站上 Call Wall {_format_price(signal.call_wall)}")
    if signal.max_pain and signal.underlying_price and signal.max_pain > signal.underlying_price:
        triggers.append(f"重心靠近 Max Pain {_format_price(signal.max_pain)}")
    if (signal.put_call_volume_ratio or 0) < 0.9:
        triggers.append("PCR 维持低位或继续下行")
    if any(flow.side == "call" for flow in signal.unusual_flows[:3]):
        triggers.append("Call 异常单继续放大")
    return triggers[:4] or ["等待价格突破关键阻力并放量确认"]


def _options_downside_triggers(signal: Any) -> list[str]:
    triggers: list[str] = []
    if signal.put_wall:
        triggers.append(f"跌破 Put Wall {_format_price(signal.put_wall)}")
    if signal.max_pain and signal.underlying_price and signal.max_pain < signal.underlying_price:
        triggers.append(f"回落靠近 Max Pain {_format_price(signal.max_pain)}")
    if (signal.put_call_volume_ratio or 0) > 1.05:
        triggers.append("PCR 升高显示防守需求")
    if signal.iv_skew and signal.iv_skew > 0.04:
        triggers.append("Put IV 偏斜继续扩大")
    if any(flow.side == "put" for flow in signal.unusual_flows[:3]):
        triggers.append("Put 异常单继续放大")
    return triggers[:4] or ["若跌破近价支撑，降低方向判断权重"]


def _options_watch_levels(signal: Any) -> list[str]:
    levels: list[str] = []
    if signal.underlying_price:
        levels.append(f"现价 {_format_price(signal.underlying_price)}")
    if signal.call_wall:
        levels.append(f"Call Wall {_format_price(signal.call_wall)}")
    if signal.put_wall:
        levels.append(f"Put Wall {_format_price(signal.put_wall)}")
    if signal.max_pain:
        levels.append(f"Max Pain {_format_price(signal.max_pain)}")
    if signal.expected_move_pct and signal.underlying_price:
        move = signal.underlying_price * signal.expected_move_pct
        levels.append(f"隐含区间 {_format_price(signal.underlying_price - move)}-{_format_price(signal.underlying_price + move)}")
    return levels[:5] or ["关键价位待补充"]


def _options_suggested_action(trend_label: str, signal: Any) -> str:
    if trend_label in {"看涨", "震荡偏强"}:
        return "偏多观察，但需等待价格站上关键阻力或 Call 异常流延续；若跌破 Put Wall，应降低判断权重。"
    if trend_label in {"看跌", "震荡偏弱"}:
        return "偏防守观察，重点看 Put Wall、PCR 和 IV 偏斜是否继续恶化；若重新站回 Call Wall，需复核空头判断。"
    if trend_label == "震荡":
        return "以区间和事件风险处理，重点跟踪 Max Pain、跨式隐含区间和突破方向。"
    return "先补充实时期权链、价格趋势和成交量，再做方向判断。"


def _format_price(value: Any) -> str:
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "--"


def _format_money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "--"
    if abs(number) >= 1_000_000:
        return f"${number / 1_000_000:.1f}M"
    if abs(number) >= 1_000:
        return f"${number / 1_000:.1f}K"
    return f"${number:.0f}"


def _normalize_orchestrator_chat(
    data: dict[str, Any],
    request: OrchestratorChatRequest,
    provider: str,
    model: str,
) -> OrchestratorChatResponse:
    data = _unwrap_payload(data)
    content = str(_first_present(data, "content", "message", "reply", "summary", "回复") or "").strip()
    if not content:
        content = "我在，你继续说。"
    chips = _safe_list(_first_present(data, "chips", "agents", "标签"))
    should_create_task = _safe_bool(_first_present(data, "should_create_task", "create_task", "需要任务"))
    handled_inline = not should_create_task and not request.attached_files
    if handled_inline:
        chips = []
    elif not chips:
        chips = ["OrchestratorAgent", _engine_chip(request.engine)]
    suggested_actions = _safe_list(_first_present(data, "suggested_actions", "actions", "下一步"))
    if handled_inline:
        suggested_actions = []
    reasoning_trace = [] if request.reasoning_mode == "fast" or handled_inline else _safe_reasoning_trace(
        _first_present(data, "reasoning_trace", "trace", "steps", "思路", "推理摘要"),
        request,
        should_create_task,
    )
    return OrchestratorChatResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        agent="OrchestratorAgent",
        engine=request.engine,
        title="DeepFocus" if handled_inline else str(_first_present(data, "title", "标题") or "OrchestratorAgent"),
        content=content,
        chips=chips[:6],
        suggested_actions=suggested_actions[:4],
        reasoning_trace=reasoning_trace,
        should_create_task=should_create_task,
        handled_inline=handled_inline,
        confidence=_safe_confidence(_first_present(data, "confidence", "置信度")),
    )


def _orchestrator_text_response(
    text: str,
    request: OrchestratorChatRequest,
    provider: str,
    model: str,
) -> OrchestratorChatResponse:
    content = re.sub(r"^```(?:json|markdown)?|```$", "", text.strip(), flags=re.I | re.M).strip()
    if not content:
        return _mock_orchestrator_chat(request, provider, model)
    should_create_task = _orchestrator_should_create_task(request)
    handled_inline = not should_create_task and not request.attached_files
    chips = [] if handled_inline else ["OrchestratorAgent", _engine_chip(request.engine)]
    actions = [] if handled_inline else _orchestrator_suggested_actions(request)
    return OrchestratorChatResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        agent="OrchestratorAgent",
        engine=request.engine,
        title="DeepFocus" if handled_inline else "OrchestratorAgent",
        content=content[:700],
        chips=chips,
        suggested_actions=actions,
        reasoning_trace=[] if handled_inline else _fallback_reasoning_trace(request, should_create_task),
        should_create_task=should_create_task,
        handled_inline=handled_inline,
        confidence=0.66,
    )


def _literal_inline_reply(
    request: OrchestratorChatRequest,
    provider: str,
    model: str,
) -> OrchestratorChatResponse | None:
    if request.attached_files or _orchestrator_should_create_task(request):
        return None

    match = re.search(r"只回复[：:]?[“\"]([^”\"]{1,80})[”\"]", request.message.strip())
    if not match:
        return None

    return OrchestratorChatResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        agent="OrchestratorAgent",
        engine=request.engine,
        title="DeepFocus",
        content=match.group(1),
        chips=[],
        suggested_actions=[],
        reasoning_trace=[],
        should_create_task=False,
        handled_inline=True,
        confidence=0.9,
    )


def _is_general_chat_message(request: OrchestratorChatRequest) -> bool:
    text = request.message.strip()
    if not text or request.attached_files or _orchestrator_should_create_task(request):
        return False
    return bool(re.search(r"你好|您好|hello|hi|在吗|能做什么|怎么用|联通测试|测试一下|ping", text, re.I))


def _orchestrator_should_create_task(request: OrchestratorChatRequest) -> bool:
    if request.attached_files:
        return True
    return bool(
        re.search(
            r"投研|投资|股票|个股|标的|财报|研报|行情|风险|仓位|组合|复盘|监控|买入|卖出|分析|研究|预测|机会|证据|估值|DCF|LBO|三表|comps|pitch|尽调|KYC|对账|reconcile|valuation|earnings|TSLA|NVDA|AAPL|MSFT",
            request.message,
            re.I,
        )
    )


def _orchestrator_suggested_actions(request: OrchestratorChatRequest) -> list[str]:
    if request.engine == "financial_services" and _orchestrator_should_create_task(request):
        return ["选择工作流", "补齐输入包", "生成交付件"]
    if _orchestrator_should_create_task(request):
        return ["启动研究任务", "补充时间范围", "查看证据库"]
    return []


def _engine_chip(engine: str) -> str:
    if engine == "tradingagents":
        return "TradingAgents"
    if engine == "financial_services":
        return "FSI Playbook"
    return "DeepFocus"


def _fallback_reasoning_trace(request: OrchestratorChatRequest, should_create_task: bool) -> list[dict[str, str]]:
    if request.reasoning_mode == "fast":
        return []

    if not should_create_task and not request.stock and not request.attached_files:
        return [
            {
                "phase": "orchestrator",
                "title": "OrchestratorAgent",
                "detail": "识别为普通问答，先按用户问题直接回复。",
                "status": "done",
            },
            {
                "phase": "evidence",
                "title": "EvidenceAgent",
                "detail": "当前无需调用行情、资料库或上传文件。",
                "status": "done",
            },
            {
                "phase": "report",
                "title": "ReportAgent",
                "detail": "整理为简短回答；如继续给出投资目标，再升级为多 Agent Run。",
                "status": "done",
            },
        ]

    stock_label = f"{request.stock.name}（{request.stock.symbol}）" if request.stock else "未选择标的"
    research_detail = "进入后台研究任务" if should_create_task else "即时回复，保留上下文"
    return [
        {
            "phase": "orchestrator",
            "title": "OrchestratorAgent",
            "detail": f"模式 {request.mode}，标的 {stock_label}。",
            "status": "done",
        },
        {
            "phase": "evidence",
            "title": "EvidenceAgent",
            "detail": f"{request.data_source_count} 个数据源，{len(request.attached_files)} 个附件，{request.mcp_server_count} 个工具连接。",
            "status": "done",
        },
        {
            "phase": "research",
            "title": "ResearchAgent",
            "detail": research_detail,
            "status": "working" if should_create_task else "done",
        },
        {
            "phase": "risk",
            "title": "RiskAgent",
            "detail": "后续输出会把事实、推断、反证和动作分开。",
            "status": "wait" if should_create_task else "done",
        },
        {
            "phase": "report",
            "title": "ReportAgent",
            "detail": "最终只输出可复核结论和下一步动作。",
            "status": "wait" if should_create_task else "done",
        },
    ]


def _unwrap_payload(data: dict[str, Any]) -> dict[str, Any]:
    for key in ("result", "data", "analysis", "解读", "投研解读"):
        nested = data.get(key)
        if isinstance(nested, dict):
            return nested
    return data


def _first_present(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def _is_low_value_task_response(result: FinGptTaskResponse) -> bool:
    return (
        result.summary.strip() in {"", "暂无摘要。", "暂无摘要"}
        and not result.key_points
        and not result.signals
        and not result.risks
        and not result.actions
    )


def _mock_stock_analysis(
    request: StockAnalysisRequest,
    provider: str,
    model: str,
) -> StockAnalysisResponse:
    stock = request.stock
    change = stock.change_percent or 0
    label, score = _quick_sentiment(
        " ".join([stock.name, stock.description or "", *[p.summary or p.title for p in request.posts]])
    )


def _mock_task(
    provider: str,
    model: str,
    capability: str,
    title: str,
    summary: str,
    key_points: list[str],
    signals: list[str],
    risks: list[str],
    actions: list[str],
    sources: list[str] | None = None,
    confidence: float = 0.62,
) -> FinGptTaskResponse:
    return FinGptTaskResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        capability=capability,
        title=title,
        summary=summary,
        key_points=key_points,
        signals=signals,
        risks=risks,
        actions=actions,
        sources=sources or ["mock"],
        confidence=confidence,
    )


def _mock_news_summary(
    request: NewsSummaryRequest,
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    stock_name = request.stock.name if request.stock else "目标资产"
    count = len(request.items)
    return _mock_task(
        provider,
        model,
        "news_summary",
        "新闻蒸馏",
        f"已聚合 {count} 条关于 {stock_name} 的新闻/社区内容，优先关注事实变化、资金面反应和事件持续性。",
        ["去重后保留高相关事件", "提炼影响路径", "区分事实与观点"],
        ["市场关注度升温", "短线情绪影响明显", "需要跟踪公告验证"],
        ["新闻源仍需交叉验证", "短期价格可能先于基本面", "标题党会放大噪音"],
        ["补充官方公告", "核对财报和电话会", "监控成交量变化"],
        [item.source or item.title for item in request.items[:4]] or ["mock-news"],
    )


def _mock_report_analysis(
    request: ReportAnalysisRequest,
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    text = request.report_text or ""
    title = request.title or _extract_labeled_field(text, "标题") or "文章解读"
    summary_text = _extract_labeled_field(text, "摘要") or _first_meaningful_sentence(text) or title
    account = _extract_labeled_field(text, "公众号") or _extract_labeled_field(text, "来源")
    published = _extract_labeled_field(text, "时间")
    keyword = _extract_labeled_field(text, "搜索关键词")
    asset = request.stock.name if request.stock else keyword or "相关标的"

    clean_title = _compact_text(title, 80)
    clean_summary = _compact_text(summary_text, 180)
    summary = (
        f"这篇资料围绕{asset}展开，核心信息是：{clean_summary}"
        f"{f' 来源为{account}' if account else ''}"
        f"{f'，发布时间 {published}' if published else ''}。"
    )
    key_points = _dedupe_short(
        [
            f"主题：{clean_title}",
            clean_summary,
            f"来源：{account}" if account else "",
            f"关键词：{keyword}" if keyword else "",
        ]
        + _extract_content_points(text)
    )
    signals = _infer_article_signals(f"{title}\n{summary_text}", asset)
    risks = _infer_article_risks(f"{title}\n{summary_text}", asset)
    actions = _infer_article_actions(f"{title}\n{summary_text}", asset)
    confidence = 0.72 if summary_text and account else 0.62
    return _mock_task(
        provider,
        model,
        "report_analysis",
        title,
        summary,
        key_points,
        signals,
        risks,
        actions,
        [source for source in [account, title] if source],
        confidence=confidence,
    )


def _mock_wechat_article(
    article: dict[str, Any],
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    return _fast_wechat_article(article, provider=provider, model=model)


def _fast_wechat_article(
    article: dict[str, Any],
    provider: str = "local-rule",
    model: str = "wechat-fast-v1",
    fallback_reason: str = "",
) -> FinGptTaskResponse:
    title = _compact_text(article.get("title") or "公众号快读", 80)
    summary_text = _compact_text(article.get("summary") or title, 140)
    account = _compact_text(article.get("account") or "公众号", 40)
    symbol = _compact_text(article.get("symbol") or article.get("keyword") or "相关标的", 24)
    published = _compact_text(article.get("published") or article.get("published_at") or "", 40)
    evidence = [source for source in [account, published, article.get("url")] if source]
    risks = ["仅有搜索摘要，需打开原文核验", "来源观点可能有偏差", "短线情绪不可替代基本面"]
    if fallback_reason:
        risks.append(f"云模型未完成：{fallback_reason[:48]}")
    return FinGptTaskResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        capability="wechat_article",
        title=title,
        summary=f"{account} 发布的公众号搜索结果显示：{summary_text}",
        key_points=_dedupe_short([title, summary_text, f"关联标的：{symbol}"])[:3],
        signals=_infer_article_signals(f"{title}\n{summary_text}", symbol)[:3],
        risks=risks[:4],
        actions=["打开原文确认事实", "核对官方公告", "跟踪股价和成交量"],
        sources=evidence,
        confidence=0.52,
    )


def _extract_labeled_field(text: str, label: str) -> str:
    pattern = rf"{re.escape(label)}[：:]\s*(.*?)(?=\n\S{{1,12}}[：:]|\Z)"
    match = re.search(pattern, text or "", flags=re.S)
    return _compact_text(match.group(1), 260) if match else ""


def _first_meaningful_sentence(text: str) -> str:
    clean = _compact_text(re.sub(r"^\S{1,12}[：:].*$", "", text or "", flags=re.M), 800)
    for sentence in re.split(r"[。！？!?；;]\s*", clean):
        sentence = sentence.strip(" ，,")
        if len(sentence) >= 18:
            return _compact_text(sentence, 180)
    return _compact_text(clean, 180)


def _compact_text(text: str, limit: int) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    clean = clean.replace("<!--red_beg-->", "").replace("<!--red_end-->", "")
    return clean[:limit].rstrip()


def _extract_content_points(text: str) -> list[str]:
    summary = _extract_labeled_field(text, "摘要") or text
    pieces = [
        _compact_text(part, 72)
        for part in re.split(r"[。！？!?；;]\s*", summary)
        if len(_compact_text(part, 120)) >= 18
    ]
    return pieces[:3]


def _infer_article_signals(text: str, asset: str) -> list[str]:
    lowered = text.lower()
    rules = [
        (("超充", "充电", "目的地充电", "补能"), "补能网络开放或扩容，可能增强车主生态和品牌触达"),
        (("fsd", "自动驾驶", "算力", "训练"), "智能驾驶本地化与算力投入是后续验证重点"),
        (("机器人", "optimus", "人形"), "机器人叙事升温，可能带动供应链关注度"),
        (("降价", "价格", "售价"), "价格调整会影响需求弹性和毛利预期"),
        (("合作", "订单", "供应链", "专利"), "产业链合作或订单信号需要交叉验证"),
        (("英伟达", "gpu", "芯片", "ai"), "AI 算力链条仍是市场关注主线"),
    ]
    signals = [message for keywords, message in rules if any(keyword in lowered for keyword in keywords)]
    if not signals:
        signals = [f"{asset}相关事件带来短期关注度变化", "需要观察市场是否把事件转化为订单或业绩预期"]
    return _dedupe_short(signals)


def _infer_article_risks(text: str, asset: str) -> list[str]:
    lowered = text.lower()
    risks = ["当前资料主要来自媒体/公众号摘要，事实需要官方公告或多源交叉验证"]
    if any(word in lowered for word in ("降价", "售价", "价格")):
        risks.append("降价可能刺激需求，但也可能压缩毛利和品牌溢价")
    if any(word in lowered for word in ("机器人", "量产", "optimus", "自动驾驶", "fsd")):
        risks.append("技术量产和商业化节奏存在不确定性，不能只看叙事热度")
    if any(word in lowered for word in ("加拿大", "贸易", "政策", "监管")):
        risks.append("海外政策和贸易环境可能改变事件影响路径")
    risks.append(f"{asset}股价可能先反映情绪，后续需用数据验证")
    return _dedupe_short(risks)


def _infer_article_actions(text: str, asset: str) -> list[str]:
    lowered = text.lower()
    actions = ["核对原文和官方公告，区分事实、转载和作者观点"]
    if any(word in lowered for word in ("超充", "充电", "目的地充电")):
        actions.append("跟踪开放站点数量、覆盖区域和利用率变化")
    if any(word in lowered for word in ("机器人", "optimus", "量产")):
        actions.append("整理相关供应链名单，核验订单、产能和收入占比")
    if any(word in lowered for word in ("fsd", "自动驾驶")):
        actions.append("关注监管审批、路测数据和本地训练数据进展")
    if any(word in lowered for word in ("降价", "售价")):
        actions.append("观察订单、交付、库存和毛利率是否同步变化")
    actions.append("把事件放入多 Agent 投研队列做证据复核")
    return _dedupe_short(actions)


def _dedupe_short(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = _compact_text(item, 96)
        if not clean or clean in seen:
            continue
        seen.add(clean)
        result.append(clean)
        if len(result) >= 6:
            break
    return result


def _mock_rag_query(
    request: RagQueryRequest,
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    docs = request.documents or [doc for doc in _load_default_docs()]
    sources = [doc["source"] if isinstance(doc, dict) else doc.source for doc in docs[:4]]
    return _mock_task(
        provider,
        model,
        "rag_query",
        "RAG知识库问答",
        f"问题“{request.question}”已基于 {len(docs)} 份资料生成回答；当前 mock 模式不会做向量检索，只做结构化演示。",
        ["优先引用内部资料", "回答标注资料来源", "缺口会显式列出"],
        ["适合接入 Finogrid 文档", "可扩展为 Chroma/向量库", "适合作为客服和审计助手"],
        ["mock 模式不做真实召回", "资料缺失会影响答案", "生产需加权限控制"],
        ["接入文档上传", "启用向量索引", "记录问答审计日志"],
        sources or ["finogrid-docs"],
    )


def _mock_forecast(
    request: ForecastRequest,
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    change = request.stock.change_percent or 0
    direction = "偏强" if change > 1 else "偏弱" if change < -1 else "震荡"
    return _mock_task(
        provider,
        model,
        "forecast",
        f"{request.stock.name} {request.horizon} 情景推演",
        f"基于当前涨跌幅 {change:.2f}% 和内容热度，{request.horizon} 的基准情景为{direction}，需等待真实行情和新闻补全。",
        ["基准情景不等于交易建议", "需要跟踪催化兑现", "回撤条件要提前定义"],
        [f"短期方向：{direction}", "关注成交量确认", "关注同业联动"],
        ["模型输入仍是模拟数据", "突发宏观事件会改写情景", "高波动会降低置信度"],
        ["接入实时行情", "补充新闻源", "设定多空触发条件"],
        [request.stock.symbol],
        confidence=0.58,
    )


def _mock_corridor_risk(
    request: CorridorRiskRequest,
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    return _mock_task(
        provider,
        model,
        "corridor_risk",
        f"{request.corridor_code} 通道风险",
        f"{request.asset} 与 {request.corridor_code} 通道当前处于演示评估状态；生产环境应接入 FX、KYT、支付失败率和链上数据。",
        ["稳定币脱锚需单独监控", "通道失败率影响交付", "合规事件需要人工复核"],
        ["运营风险中性", "链上结算需观察", "新闻事件需复核"],
        ["缺少真实 FX 和链上数据", "合规状态不可由模型单独判断", "支付伙伴 SLA 未接入"],
        ["接入 Bridge 状态", "接入 KYT/AML 结果", "设置异常阈值告警"],
        [request.corridor_code, request.asset],
        confidence=0.55,
    )


def _mock_agent_brief(
    request: AgentBriefRequest,
    provider: str,
    model: str,
) -> FinGptTaskResponse:
    role_names = {
        "ops_oversight": "运营监督 Agent",
        "audit_governance": "审计治理 Agent",
        "process_improvement": "流程改进 Agent",
        "internal_support": "内部支持 Agent",
        "treasury_strategy": "资金策略 Agent",
    }
    role_name = role_names[request.role]
    return _mock_task(
        provider,
        model,
        "agent_brief",
        role_name,
        f"{role_name} 已根据输入上下文生成工作摘要，适合放入运维台作为人工复核前的第一版分析。",
        ["识别关键异常", "拆出可执行动作", "保留人工复核入口"],
        ["需要连接真实后台事件", "适合与审批流结合", "可写入审计日志"],
        ["不能替代合规判断", "上下文不完整会误判", "需控制可执行权限"],
        ["接入 ops_console API", "增加任务状态流转", "保留操作留痕"],
        [request.role],
    )


def _mock_general_chat(
    request: GeneralChatRequest,
    provider: str,
    model: str,
) -> GeneralChatResponse:
    text = request.message.strip()
    compact = re.sub(r"[\s，。！？!?,.]+", "", text).lower()
    if re.fullmatch(r"你好|您好|嗨|哈喽|hello|hi|hey|在吗|在不在|早上好|上午好|中午好|下午好|晚上好", compact):
        content = "你好，我在。今天想聊点什么？"
    elif re.fullmatch(r"谢谢|谢了|感谢|多谢|thanks|thankyou|thx", compact):
        content = "不客气。"
    elif re.fullmatch(r"好的|好|ok|收到|明白|了解|嗯|嗯嗯", compact):
        content = "好，我跟着。你继续说。"
    elif re.fullmatch(r"ping|测试|测试一下|联通测试", compact):
        content = "在，连接正常。"
    elif re.search(r"你是谁|你能做什么|你会做什么|能干嘛|帮助|help", text, re.I):
        content = "我是 DeepFocus。你可以正常和我聊天；需要时，我也能帮你读研报、找证据、做风险复核和生成投研任务。"
    else:
        content = "我在。你可以直接像正常聊天一样问我；如果需要投研分析，再说“分析这个标的”或“解读这份研报”。"
    return GeneralChatResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        title="DeepFocus",
        content=content,
    )


def _mock_orchestrator_chat(
    request: OrchestratorChatRequest,
    provider: str,
    model: str,
) -> OrchestratorChatResponse:
    text = request.message.strip()
    stock = request.stock
    stock_label = f"{stock.name}（{stock.symbol}）" if stock else "当前工作区"
    private_pattern = re.compile(r"我爸是谁|我妈是谁|我是谁|你知道我.*吗|我的.*是谁")
    greeting_pattern = re.compile(r"^(你好|您好|嗨|哈喽|hello|hi|hey|在吗|在不在|早上好|上午好|中午好|下午好|晚上好)[！!。,.，\s]*$", re.I)
    thanks_pattern = re.compile(r"^(谢谢|谢了|感谢|多谢|thanks|thankyou|thx|好的|好|ok|收到|明白|了解|嗯|嗯嗯)[！!。,.，\s]*$", re.I)
    investment_pattern = re.compile(
        r"投研|投资|股票|个股|标的|财报|研报|行情|风险|仓位|组合|复盘|监控|买入|卖出|分析|研究|预测|机会|证据|估值|DCF|LBO|三表|comps|pitch|尽调|KYC|对账|reconcile|valuation|earnings|TSLA|NVDA|AAPL|MSFT",
        re.I,
    )
    if private_pattern.search(text):
        content = "我不知道你爸爸是谁，因为当前对话没有这类个人背景信息。你可以直接告诉我背景；如果这是投资相关身份、账户或资料归属问题，我会先做证据核对再回答。"
        actions = ["补充背景", "上传资料", "说明目标"]
        should_create_task = False
    elif investment_pattern.search(text) or request.attached_files:
        content = (
            f"收到。我会以 {stock_label} 为上下文，先由 Orchestrator 拆解目标，再调度 Evidence、Research、Risk 和 Report Agent "
            "做证据核对、研究判断、反证约束和结论汇总。"
        )
        actions = ["启动研究任务", "补充时间范围", "查看证据库"]
        should_create_task = True
    else:
        literal_match = re.search(r"只回复[：:]?[“\"]([^”\"]{1,80})[”\"]", text)
        if literal_match:
            content = literal_match.group(1)
        elif greeting_pattern.search(text):
            content = "你好，我在。你可以直接和我聊天；需要投研、研报或风控时，我再切到工作流。"
        elif thanks_pattern.search(text):
            content = "不客气。你继续说，我会跟着当前对话走。"
        else:
            content = "我在。你可以像正常聊天一样直接问；如果聊到标的、研报、文件或风险，我会再调用对应工作流。"
        actions = []
        should_create_task = False
    handled_inline = not should_create_task and not request.attached_files
    return OrchestratorChatResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        agent="OrchestratorAgent",
        engine=request.engine,
        title="DeepFocus" if handled_inline else "OrchestratorAgent",
        content=content,
        chips=[] if handled_inline else ["OrchestratorAgent", _engine_chip(request.engine)],
        suggested_actions=actions,
        reasoning_trace=[] if handled_inline else _fallback_reasoning_trace(request, should_create_task),
        should_create_task=should_create_task,
        handled_inline=handled_inline,
        confidence=0.62,
    )


def _load_default_docs() -> list[dict[str, str]]:
    docs_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "finogrid", "docs"))
    defaults = []
    for relative in ["architecture.md", "dr-runbook.md", "fingpt_usage_policy.md"]:
        path = os.path.join(docs_root, relative)
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as handle:
                defaults.append({"source": relative, "text": handle.read()[:4000]})
    return defaults
    if change > 2:
        label, score = "positive", max(score, 0.35)
    elif change < -2:
        label, score = "negative", min(score, -0.35)

    risk = "high" if abs(change) >= 5 else "medium" if abs(change) >= 2 else "low"
    post_count = len(request.posts)
    summary = (
        f"{stock.name}（{stock.symbol}）当前涨跌幅约 {change:.2f}%。"
        f"系统已汇总 {post_count} 条社区/资讯内容，建议重点核验基本面变化、资金面和事件催化。"
    )
    return StockAnalysisResponse(
        provider=provider,
        model=model,
        generated_at=datetime.now(timezone.utc),
        executive_summary=summary,
        sentiment_label=label,
        sentiment_score=score,
        risk_level=risk,
        catalysts=[
            "社区关注度变化",
            "近期价格动量",
            "基本面事件更新",
        ],
        risks=[
            "信息源仍以模拟数据为主",
            "短期波动可能放大",
            "需补充真实公告和财报",
        ],
        watch_items=[
            "成交量是否同步放大",
            "财报或指引变化",
            "核心业务新闻验证",
        ],
        suggested_questions=[
            "上涨由业绩还是情绪驱动？",
            "风险事件是否已被定价？",
            "同业估值是否更有吸引力？",
        ],
    )


def _quick_sentiment(text: str) -> tuple[str, float]:
    lowered = text.lower()
    positive_words = ["增长", "利好", "超预期", "positive", "beat", "upgrade", "bull"]
    negative_words = ["下滑", "风险", "利空", "negative", "miss", "downgrade", "bear"]
    pos = sum(1 for word in positive_words if word in lowered)
    neg = sum(1 for word in negative_words if word in lowered)
    if pos > neg:
        return "positive", min(1.0, 0.25 + pos * 0.15)
    if neg > pos:
        return "negative", max(-1.0, -0.25 - neg * 0.15)
    return "neutral", 0.0


def _safe_label(value: Any) -> str:
    label = str(value or "neutral").lower()
    return label if label in {"positive", "neutral", "negative"} else "neutral"


def _safe_risk(value: Any) -> str:
    risk = str(value or "medium").lower()
    return risk if risk in {"low", "medium", "high"} else "medium"


def _safe_score(value: Any, default: float) -> float:
    try:
        return max(-1.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return default


def _safe_confidence(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.5


def _safe_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1", "需要", "是", "创建"}
    return False


def _safe_reasoning_trace(
    value: Any,
    request: OrchestratorChatRequest,
    should_create_task: bool,
) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return _fallback_reasoning_trace(request, should_create_task)

    steps: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, dict):
            title = str(_first_present(item, "title", "标题") or "").strip()
            detail = str(_first_present(item, "detail", "description", "内容", "说明") or "").strip()
            phase = str(_first_present(item, "phase", "key", "阶段") or "step").strip()
            status = str(_first_present(item, "status", "状态") or "done").strip().lower()
        else:
            title = str(item).strip()
            detail = ""
            phase = "step"
            status = "done"
        if not title and not detail:
            continue
        if status not in {"done", "working", "wait", "error"}:
            status = "done"
        steps.append({
            "phase": phase[:24] or "step",
            "title": title[:48] or "思路摘要",
            "detail": detail[:180],
            "status": status,
        })
        if len(steps) >= 5:
            break
    return steps or _fallback_reasoning_trace(request, should_create_task)


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()][:6]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
