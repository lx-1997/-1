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
_INTERNAL_KEYS = {"provider", "provider_name", "source"}

# 用户可见文本里的「无歧义内部标识」→ 中性替换（顺序：长串先，避免子串残留）。
_SOURCE_PHRASES: list[tuple[str, str]] = [
    ("同花顺 iFinD 实时", "公开行情数据"),
    ("同花顺 iFinD", "公开数据源"),
    ("同花顺iFinD", "公开数据源"),
    ("东方财富研报库", "公开研报库"),
    ("iFinD 优先、东财兜底", "多源回退"),
    ("iFinD优先、东财兜底", "多源回退"),
    ("iFinD优先东财兜底", "多源回退"),
    ("iFinD", "公开数据源"),
    ("知识星球", "研报来源"),
    ("Finnhub", "公开数据源"),
    ("finnhub", "公开数据源"),
    ("stooq", "公开数据源"),
]
# 内部工具/接口名（snake_case 英文标识，正常中文回答里不会出现）。
_TOOL_NAME_RE = re.compile(r"\b(?:get|assess|search|fetch|build|run)_[a-z][a-z0-9_]{2,}\b")
# 密钥/令牌形态。
_SECRET_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|secret[_-]?key|bearer|sk-[a-z0-9]+)\b\s*[:=]?\s*[A-Za-z0-9_\-.]{6,}"
)


def scrub_internal_fields(obj: Any) -> Any:
    """递归剥掉工具结果里的数据源/服务商标识键(provider/provider_name/source)，
    防止「iFinD/东财」等经回灌进模型 → 被引用。保留 source_name(新闻出处)。"""
    if isinstance(obj, dict):
        return {k: scrub_internal_fields(v) for k, v in obj.items() if k not in _INTERNAL_KEYS}
    if isinstance(obj, list):
        return [scrub_internal_fields(v) for v in obj]
    return obj


def scrub_internal_text(s: str) -> str:
    """对用户可见文本做泄密清理：服务商品牌名→中性词、内部工具名→「内部数据工具」、密钥形态打码。幂等。"""
    if not s:
        return s
    out = _SECRET_RE.sub("[已隐去]", s)
    out = _TOOL_NAME_RE.sub("内部数据工具", out)
    for brand, repl in _SOURCE_PHRASES:
        if brand in out:
            out = out.replace(brand, repl)
    return out
