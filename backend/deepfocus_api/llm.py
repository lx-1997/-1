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
    NewsSummaryRequest,
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
                raise RuntimeError("MINIMAX_API_KEY is required when DEEPFOCUS_LLM_PROVIDER=minimax")
            return AsyncOpenAI(
                api_key=api_key,
                base_url=config.get("base_url") or "https://api.minimax.io/v1",
            )

        if provider in {"openai", "openai-compatible", "cloud"}:
            api_key = config.get("api_key")
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is required when DEEPFOCUS_LLM_PROVIDER=openai")
            base_url = config.get("base_url") or None
            return AsyncOpenAI(api_key=api_key, base_url=base_url)

        raise RuntimeError(f"Unsupported DEEPFOCUS_LLM_PROVIDER={provider}")

    async def complete_json(
        self,
        prompt: str,
        max_tokens: int = 2200,
        timeout_seconds: float = 35,
        force_json_first: bool = True,
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
            "必须填充 title, summary, key_points, signals, risks, actions, sources, confidence；"
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
            "temperature": max(0.01, min(config["temperature"], 1.0)),
            "max_tokens": max_tokens,
        }
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


def _clean_error(exc: Exception) -> str:
    text = str(exc).strip()
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


def _safe_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()][:6]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []
