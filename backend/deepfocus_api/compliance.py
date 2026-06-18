"""合规中性化护栏（全链路共享）。

背景：原先只有「深度研判」一条链路在 LLM 输出后做禁词中性化；速判卡叙述 / 投研晨报 /
A股复盘 / AI 模拟盘解说等其它 LLM 叙述只靠 prompt 软约束，一旦模型漂移或缓存命中旧文案，
可能把『建议买入 / 目标价 / 稳赚』直送用户。本模块把那套词表提成单一可信源，供各链路在
落地前统一过一遍——prompt 是软约束，这是 prompt 之外的第二道硬护栏。

⚠️只对【面向用户的自由文本叙述】使用，不要对结构化数据字段（如方向枚举、原始数值）套用。
"""
from __future__ import annotations

from typing import Any

# 子串替换，按「先长后短」排序：避免「建议买入」被「买入」抢先替坏。
# 顺序即优先级；新增项放到对应长度位置或末尾的明确促销词区。
NEUTRALIZE_MAP: list[tuple[str, str]] = [
    ("强烈建议买入", "偏多关注"),
    ("强烈推荐买入", "偏多关注"),
    ("建议买入", "偏多关注"),
    ("建议卖出", "偏空规避"),
    ("建议清仓", "偏空规避"),
    ("立即买入", "偏多关注"),
    ("满仓", "偏多"),
    ("加仓", "偏多"),
    ("减仓", "偏空"),
    ("清仓", "偏空规避"),
    ("买入", "偏多"),
    ("卖出", "偏空"),
    ("必涨", "或有上行"),
    ("必跌", "或有下行"),
    ("稳赚不赔", "存在不确定性"),
    ("稳赚", "存在机会"),
    ("包赚", "存在机会"),
    ("躺赚", "存在机会"),
    ("一夜暴富", "市场有风险"),
    ("保本保息", "不保证本息"),
    ("翻倍", "弹性较大"),
    ("暴涨", "明显上行"),
    ("暴跌", "明显下行"),
    ("目标价", "观察价位"),
]


def neutralize_text(s: str) -> str:
    """对单条用户可见文本做禁词中性化（幂等：已中性化的文本再过一遍不变）。"""
    if not s:
        return s
    out = s
    for bad, good in NEUTRALIZE_MAP:
        if bad in out:
            out = out.replace(bad, good)
    return out


def neutralize_deep(value: Any) -> Any:
    """递归对所有对用户可见的字符串做禁词中性化（dict/list/str）。"""
    if isinstance(value, str):
        return neutralize_text(value)
    if isinstance(value, list):
        return [neutralize_deep(v) for v in value]
    if isinstance(value, dict):
        return {k: neutralize_deep(v) for k, v in value.items()}
    return value
