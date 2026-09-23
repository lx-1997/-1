"""输出泄密护栏：剥离回答 / 工具结果里的内部敏感标识（数据源服务商、内部工具名、密钥形态）。

与 compliance(荐股措辞中性化)正交：compliance 管「不构成建议」，本模块管「不泄露内部实现」。
设计原则——**保守**：只剥无歧义的内部标识(iFinD/finnhub/snake_case 工具名/密钥)，
绝不剥「东方财富/同花顺/雪球/新浪」这类与上市公司或常见词重名的 token(会误伤正常投研回答)。
行为侧由 run_tool_agent 系统提示的「保密红线」兜，本模块是输出层硬过滤的最后一道。
"""
from __future__ import annotations

import re
from typing import Any

# 工具结果回灌模型前要剥掉的「数据源/服务商标识」键（递归）。
# ⚠️ 保留 source_name —— 那是新闻出处(路透/彭博)，属合法引用、可对用户展示。
_INTERNAL_KEYS = {"provider", "provider_name", "source", "warnings"}

# 用户可见文本里的「无歧义内部标识」→ 中性替换（顺序：长串先，避免子串残留）。
_SOURCE_PHRASES: list[tuple[str, str]] = [
    # 对外只展示产品 AI 品牌，不让模型在免责声明里自曝底层供应商。
    ("MiniMax-M3", "AI"),
    ("MiniMax M3", "AI"),
    ("MiniMax", "AI"),
    ("同花顺 iFinD 实时", "公开行情数据"),
    ("同花顺 iFinD", "公开数据源"),
    ("同花顺iFinD", "公开数据源"),
    ("东方财富研报库", "公开研报库"),
    ("iFinD 优先、东财兜底", "多源回退"),
    ("iFinD优先、东财兜底", "多源回退"),
    ("iFinD优先东财兜底", "多源回退"),
    ("iFinD", "公开数据源"),
    ("Eastmoney", "公开行情源"),
    ("eastmoney", "公开行情源"),
    ("Google Finance", "公开行情"),
    ("Google 行情", "公开行情"),
    ("新浪/东财", "公开行情源"),
    ("东财/同花顺", "公开行情口径"),
    ("iFinD / 东财", "公开数据源"),
    ("万得/卓创资讯", "公开大宗商品报价"),
    ("同花顺口径", "公开行情口径"),
    ("东财的", "公开数据中的"),
    ("知识星球", "研报来源"),
    ("Finnhub", "公开数据源"),
    ("finnhub", "公开数据源"),
    ("stooq", "公开数据源"),
]
# 内部工具/接口名（snake_case 英文标识，正常中文回答里不会出现）。
_TOOL_NAME_RE = re.compile(r"\b(?:get|assess|search|fetch|build|run)_[a-z][a-z0-9_]{2,}\b")
_INTERNAL_SKILL_RE = re.compile(r"\b(?:skill：?|skill:\s*)[a-z][a-z0-9_.-]{2,}\b", re.I)
_PROVIDER_ERROR_RE = re.compile(
    r"(?:实时)?(?:行情)?接口[^\n。；]{0,100}(?:403|Forbidden)[^\n。；]{0,100}[。；]?",
    re.I,
)
_HTTP_STATUS_RE = re.compile(
    r"(?i)(?:\bHTTP\s*[45]\d\d\b|\b(?:400|401|402|403|404|408|409|429|500|502|503|504)\s+"
    r"(?:Forbidden|Unauthorized|Timeout|Error|Bad\s+Gateway|Service\s+Unavailable)\b)"
)
# 密钥/令牌形态。
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|bearer|sk-[a-z0-9]+)\b\s*[:=]?\s*[A-Za-z0-9_\-.]{6,}"
)


def scrub_internal_fields(obj: Any) -> Any:
    """递归剥掉工具结果里的数据源/服务商标识键(provider/provider_name/source)，
    防止「iFinD/东财」等经回灌进模型 → 被引用。保留 source_name(新闻出处)。"""
    if isinstance(obj, dict):
        clean: dict[str, Any] = {}
        for key, value in obj.items():
            if key in _INTERNAL_KEYS:
                continue
            # 错误文本只告诉模型「当前不可用」，不把 HTTP 状态、
            # 上游域名或 SDK 异常回灌进最终答案。
            clean[key] = "暂时无法获取" if key == "error" and value else scrub_internal_fields(value)
        return clean
    if isinstance(obj, list):
        return [scrub_internal_fields(v) for v in obj]
    if isinstance(obj, str):
        return scrub_internal_text(obj)
    return obj


def scrub_internal_text(s: str) -> str:
    """对用户可见文本做泄密清理：服务商品牌名→中性词、内部工具名→「内部数据工具」、密钥形态打码。幂等。"""
    if not s:
        return s
    out = _PROVIDER_ERROR_RE.sub("实时数据暂不可用。", s)
    out = _HTTP_STATUS_RE.sub("访问受限", out)
    out = _SECRET_RE.sub("[已隐去]", out)
    out = _TOOL_NAME_RE.sub("内部数据工具", out)
    out = _INTERNAL_SKILL_RE.sub("内部研究流程", out)
    for brand, repl in _SOURCE_PHRASES:
        if brand in out:
            out = out.replace(brand, repl)
    return out
