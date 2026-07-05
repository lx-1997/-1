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
    # ⚠️ 已移除「翻倍/暴涨/暴跌/目标价」——它们是客观财经描述/研报措辞(如"营收同比翻倍""中金目标价200元")，
    # 无条件子串替换会篡改事实+造出残句；这里只保留真正的承诺/促销/荐股词。若要约束这些词，应基于语境(与"必/稳赚/建议买入"同句)而非裸替换。
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


# ---------------------------------------------------------------------------
# AI 生成内容显式标识（《人工智能生成合成内容标识办法》2025-09-01 施行）
# 要求：AI 生成文本须在起始/末尾/界面附加可感知的显式标识；对外分发页面另需隐式元数据标识。
# 用法：所有面向用户的 AI 叙述出口（df_take/晨报/复盘/深研/微信 AI 回复/公开落地页）在
# 落地前过一遍 ai_label()；HTML 页面 head 里加 AI_META_TAG、正文加 AI_BADGE_HTML。
# ---------------------------------------------------------------------------

# 判定「已带标识」的关键短语（幂等去重用，兼容历史上手写过的变体）
_AI_LABEL_MARKERS = ("AI 生成", "AI生成", "AI 辅助生成")

AI_CONTENT_NOTICE = "本内容由 AI 生成，仅供参考，不构成投资建议"

# 公开 HTML 落地页用：可见徽标 + 隐式元数据标识
AI_BADGE_HTML = (
    '<p class="ai-label" style="color:#8a8f98;font-size:12px;margin:8px 0 0">'
    "🤖 本内容由 AI 生成，仅供参考，不构成投资建议</p>"
)
AI_META_TAG = '<meta name="ai-generated" content="true">'


def has_ai_label(s: str) -> bool:
    """文本是否已带 AI 生成标识（幂等判定）。"""
    if not s:
        return False
    return any(m in s for m in _AI_LABEL_MARKERS)


def ai_label(s: str, brief: bool = False) -> str:
    """给 AI 生成的用户可见文本追加显式标识（幂等：已带标识不重复追加）。

    brief=True 用于微信/推送等寸土寸金的短文本出口，追加单行短标识；
    默认追加完整声明。只对自由文本叙述使用，不要对结构化字段套用。
    """
    if not s or has_ai_label(s):
        return s
    tail = "（AI 生成，仅供参考）" if brief else f"\n\n（{AI_CONTENT_NOTICE}）"
    return s.rstrip() + tail
