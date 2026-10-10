"""研报速览解读（快轨）：思考禁用档 + 受限输入，十秒级产出紧凑卡片。

深度稿（research_digest.generate_deep_draft）追求完整文章结构，长文推理
1-4 分钟；速览只求「先有方向感」——one_liner / 利好 / 风险，AiAnalysis 形状，
由 SSE 端点在深度生成前作为 ``quick`` 事件推给前端先行渲染。

独立成模块（不并入 research_digest）：服务器侧该文件与 Mac 有历史分叉，
新文件零锚点冲突。失败一律返回 None：快轨是增强层，绝不阻塞或污染深度稿
主链路。模型仍走 CloudResearchLLM 池——漏斗层已对 qwen3/glm 统一禁思考，
速度来自小 max_tokens（1.2k）+ 文本截断（2 万字符/篇）。
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from .llm import CloudResearchLLM
from .research_digest import ResearchDeepDraftRequest, SourceDocument
from .research_multi_agent import analyze_pdf_adaptive

_QUICK_TEXT_CHARS = 20_000      # 每篇文档送入模型的最大字符数（快轨只求方向感）
_QUICK_MAX_TOKENS = 1_200
_QUICK_CALL_TIMEOUT = 40        # 单次调用秒数
_QUICK_TOTAL_TIMEOUT = 55       # 含一次重试的总预算
_QUICK_VISION_MAX_PAGES = 1     # 扫描版快轨只读封面页（页视觉 ~10s + 综合；2 页实测仍超 30s 预算）
_QUICK_VISION_TIMEOUT = 30

_QUICK_SCHEMA_HINT = (
    "必须包含 one_liner, summary, bullish, bearish, key_points, instruments, confidence"
)

_QUICK_PROMPT_TMPL = """你是研报速览助手。基于下方研报文字快速通读，输出严格 JSON（不要 Markdown、不要解释）：
{{"one_liner": "一句话核心判断，不超过60字",
 "summary": "2-3句事件/观点摘要，不超过160字",
 "bullish": ["关键利好或看点，最多4条，每条不超过40字"],
 "bearish": ["主要风险或反方观点，最多4条，每条不超过40字"],
 "key_points": ["关键事实与数字，最多4条，每条不超过40字"],
 "instruments": ["文中明确提及的可交易标的名称，最多4个，没有则空数组"],
 "confidence": 0.05到0.85之间的数字}}
只使用原文明确内容，读不到的写「原文未提供」，不要编造、不要引入外部信息。

《{title}》{symbol_line}
{body}"""


def _symbol_line(symbol: Optional[str]) -> str:
    symbol = str(symbol or "").strip()
    return f"（标的：{symbol}）" if symbol else ""


def _quick_prompt(request: ResearchDeepDraftRequest, docs: list[SourceDocument]) -> str:
    body_parts: list[str] = []
    for doc in docs:
        text = str(doc.text or "").strip()
        if not text:
            continue
        body_parts.append(f"--- {doc.title or request.title} ---\n{text[:_QUICK_TEXT_CHARS]}")
    if not body_parts:
        return ""
    return _QUICK_PROMPT_TMPL.format(
        title=str(request.title or "研报")[:160],
        symbol_line=_symbol_line(request.symbol),
        body="\n\n".join(body_parts),
    )


def _string_list(value: Any, limit: int = 4) -> list[str]:
    if isinstance(value, str):
        value = [value]
    items: list[str] = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip()
        if text:
            items.append(text[:80])
        if len(items) >= limit:
            break
    return items


def _quick_confidence(value: Any) -> float:
    try:
        return max(0.05, min(0.85, float(value)))
    except (TypeError, ValueError):
        return 0.4


def _quick_normalise(
    data: dict[str, Any],
    request: ResearchDeepDraftRequest,
    provider: str,
    *,
    vision: bool = False,
) -> dict[str, Any]:
    one_liner = str(data.get("one_liner") or data.get("summary") or "").strip()
    if not one_liner:
        return {}
    return {
        "title": str(request.title or "研报速览"),
        "subject": str(data.get("subject") or request.symbol or "").strip(),
        "one_liner": one_liner[:400],
        "summary": str(data.get("summary") or "").strip()[:1_200],
        "bullish": _string_list(data.get("bullish") or data.get("key_points")),
        "bearish": _string_list(data.get("bearish") or data.get("risks")),
        "key_points": _string_list(data.get("key_points")),
        "instruments": _string_list(data.get("instruments"), 4),
        "confidence": _quick_confidence(data.get("confidence")),
        "provider": f"{provider} 速览",
        "source_note": "速览版：快速通读；完整深度解读生成后自动替换",
        "quick": True,
        "quick_vision": vision,
    }


async def generate_deep_quick(
    request: ResearchDeepDraftRequest,
    *,
    documents: Optional[list[SourceDocument]] = None,
) -> Optional[dict[str, Any]]:
    """速览版紧凑解读；任何失败返回 None，调用方跳过 quick 事件即可。"""

    docs = list(documents or [])
    if not docs:
        return None
    try:
        llm = CloudResearchLLM()
        if llm.provider == "mock":
            return None
    except Exception:
        return None

    # 文本型：一次禁思考的小调用
    prompt = _quick_prompt(request, docs)
    if prompt:
        try:
            data = await asyncio.wait_for(
                llm.complete_json(
                    prompt,
                    max_tokens=_QUICK_MAX_TOKENS,
                    timeout_seconds=_QUICK_CALL_TIMEOUT,
                    retry_schema_hint=_QUICK_SCHEMA_HINT,
                ),
                timeout=_QUICK_TOTAL_TIMEOUT,
            )
            if isinstance(data, dict) and data:
                normalised = _quick_normalise(data, request, str(getattr(llm, "model", "cloud")))
                if normalised:
                    return normalised
        except Exception as exc:
            print(f"[deep-quick] 文本速览失败：{type(exc).__name__}: {str(exc)[:120]}")

    # 扫描版：小页数视觉快读（analyze_pdf_adaptive 产出的即紧凑卡形状）
    vision_bytes = next((doc.pdf_bytes for doc in docs if doc.pdf_bytes), b"")
    if vision_bytes:
        try:
            compact = await asyncio.wait_for(
                analyze_pdf_adaptive(
                    vision_bytes,
                    title=request.title,
                    symbol=request.symbol,
                    max_pages=_QUICK_VISION_MAX_PAGES,
                ),
                timeout=_QUICK_VISION_TIMEOUT,
            )
            if isinstance(compact, dict) and compact:
                return _quick_normalise(
                    compact, request, str(getattr(llm, "model", "vision")), vision=True
                )
        except Exception as exc:
            print(f"[deep-quick] 视觉速览失败：{type(exc).__name__}: {str(exc)[:120]}")

    return None
