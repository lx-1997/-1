"""Dynamic buy-side research protocol for daocaijing AI Q&A.

The protocol deliberately separates *how to research* from tool routing.  Tools
still own facts; this module turns the user's question into a compact research
mandate so the model does not force every question through one generic investing
style or mistake a polished summary for an investable view.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any


PROTOCOL_VERSION = "buy-side-v1"


_SMALLTALK_RE = re.compile(
    r"^(?:你好|您好|hi|hello|谢谢|多谢|再见|你是谁|你能做什么|怎么收费|会员多少钱)[！!。.?？\s]*$",
    re.I,
)
_RESEARCH_RE = re.compile(
    r"股票|个股|标的|A股|港股|美股|指数|大盘|板块|题材|行情|估值|市盈率|市净率|"
    r"PE|PB|PEG|ROE|EPS|财报|业绩|营收|利润|现金流|分红|公告|研报|投研|投资|"
    r"买入|卖出|持有|仓位|组合|风险|催化|预期|共识|护城河|管理层|指引|复盘|"
    r"投委会|尽调|研究报告|目标价|目标位|上涨空间|上行空间|半年内|六个月|"
    r"[0368]\d{5}",
    re.I,
)
_TICKER_RE = re.compile(r"\b[A-Z]{1,5}\b")
_DECISION_RE = re.compile(r"怎么看|怎么样|值得|能不能|该不该|更偏向|机会|风险|贵不贵|买什么|买哪|选股|筛选|比较", re.I)
_METHOD_RE = re.compile(r"怎么算|如何计算|什么是|解释.{0,8}(?:PE|PB|PEG|ROE|DCF)|只讲方法|教学", re.I)
_DOCUMENT_RE = re.compile(r"附件|文档|文件|原文|这份|这篇", re.I)
_SCREEN_RE = re.compile(
    r"选股|筛.{0,8}(?:股票|个股|标的)|推荐.{0,8}(?:股票|个股|标的)|"
    r"买什么(?:股|票)|买哪(?:只|些|几只)|给我.{0,5}(?:只|个).{0,5}(?:股票|标的)",
    re.I,
)
_COMPARISON_RE = re.compile(r"对比|比较|更偏向谁|二选一|三选一|谁更|哪(?:只|个).{0,8}更", re.I)
_MANAGEMENT_RE = re.compile(r"管理层|CEO|CFO|董事长|高管|承诺兑现|指引兑现|回购效果|内部人|增持|减持", re.I)
_EARNINGS_RE = re.compile(r"财报前|财报后|业绩预告|业绩快报|超预期|低于预期|盈利预测|一致预期|业绩指引|指引", re.I)
_PORTFOLIO_RE = re.compile(r"我的持仓|组合|仓位|回撤|风险敞口|配置|调仓", re.I)
_MARKET_RE = re.compile(r"大盘|盘面|指数|市场复盘|今日市场|A股今天|港股今天|美股今天", re.I)
_NEWS_RE = re.compile(r"新闻|快讯|公告|研报|近期动态|最近发生|消息|资讯|舆情", re.I)
_FACT_RE = re.compile(
    r"现价|价格多少|涨跌多少|市值多少|PE多少|PB多少|市盈率多少|市净率多少|"
    r"什么时候(?:披露|分红|除权)|发布日期|公告日期|代码是什么",
    re.I,
)
_DEEP_RESEARCH_RE = re.compile(r"深度|完整|全面|尽调|投委会|研究报告|十五|15|二十|20", re.I)


@dataclass(frozen=True)
class BuySideResearchMandate:
    """Question-specific research context shared by quick Q&A and roundtables."""

    is_research: bool
    task_type: str
    task_label: str
    horizon: str
    horizon_label: str
    horizon_explicit: bool
    decision_context_note: str
    research_lens: str
    required_sections: tuple[str, ...]
    key_variable_target: int

    @property
    def needs_variant_view(self) -> bool:
        return self.task_type in {
            "single_stock",
            "stock_comparison",
            "stock_screen",
            "earnings_event",
            "management_quality",
            "portfolio_review",
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "protocol_version": PROTOCOL_VERSION,
            "is_research": self.is_research,
            "task_type": self.task_type,
            "task_label": self.task_label,
            "horizon": self.horizon,
            "horizon_label": self.horizon_label,
            "horizon_explicit": self.horizon_explicit,
            "decision_context_note": self.decision_context_note,
            "research_lens": self.research_lens,
            "required_sections": list(self.required_sections),
            "key_variable_target": self.key_variable_target,
        }

    def system_prompt(self) -> str:
        """Compact, question-specific contract appended to the tool-agent prompt."""
        if not self.is_research:
            return ""
        sections = "、".join(self.required_sections)
        variant_rules = ""
        if self.needs_variant_view:
            variant_rules = (
                "先回答『市场当前在定价什么』，再写『可能的预期差/非共识假设』；"
                "只有工具证据含一致预期、研报或可比基准时才能描述市场共识，"
                "否则明确写『缺少可核验的一致预期，暂不能判断预期差』。"
            )
        key_variable_rule = (
            f"按结论影响从高到低列 {self.key_variable_target} 个关键变量供人工回到原始资料复核；"
            if self.key_variable_target
            else "事实型问题不强行扩写投资逻辑；"
        )
        depth_rule = (
            "本题明确要求深度尽调/投委会材料，覆盖优先于短答，允许 1200—1800 字并保留 15 项关键复核变量。"
            if self.key_variable_target >= 15
            else ""
        )
        return (
            f"【买方研究协议 {PROTOCOL_VERSION}｜本题动态上下文】\n"
            "定位：你是投资者的 AI 外骨骼，负责取数、整理、提出假设和证伪路径；最终判断仍由用户完成。\n"
            f"任务：{self.task_label}。期限：{self.horizon_label}。{self.decision_context_note}\n"
            f"研究镜头：{self.research_lens}\n"
            "执行纪律：\n"
            "- 把核心表述分成四类：可追溯的【事实】、有明确载体的【市场共识】、由证据推导的【研究判断】、尚待数据验证的【假设】；不要把后两类写成事实。\n"
            "- 来源优先级为公司公告/定期报告/电话会等一手资料 > 标准化行情财务数据 > 可信研报与新闻 > 论坛自媒体线索。首选来源失败时披露缺口并降置信度，禁止静默换成低质量来源。\n"
            f"- {variant_rules or '本题以准确、直接回答为主，不为显得深刻而硬造非共识观点。'}\n"
            "- 每个关键判断都给反证或失效条件；没有证据时少下结论，不用常识补业务故事。复杂计算只使用工具返回值或确定性计算结果。\n"
            f"- {key_variable_rule}高置信度只用于关键事实被多项直接证据覆盖且口径一致的情况。\n"
            f"输出：优先使用「{sections}」；用户要求简短或本题只是查数时服从短答，不机械堆满章节。{depth_rule}"
        )

    def audit_prompt(self) -> str:
        """Rules for the existing evidence-only final-answer audit pass."""
        if not self.is_research:
            return ""
        if self.task_type == "fact_lookup":
            return (
                "\n11. 本题是事实查询：保留直接答案、时点和口径即可；删除无关投资逻辑，"
                "不要为了套模板扩写市场共识或买卖判断。"
            )
        section_rule = "、".join(self.required_sections)
        key_rule = (
            f"在现有证据和缺口范围内保留或整理最多 {self.key_variable_target} 个关键复核变量；"
            if self.key_variable_target
            else ""
        )
        return (
            f"\n11. 按本题研究口径审校：期限为『{self.horizon_label}』，不得把短期事件结论冒充长期价值判断，反之亦然。"
            f"用户未明确期限时可以写明本次默认口径，但不能假装这是用户偏好。\n"
            "12. 只有证据 JSON 明确含一致预期、研报观点或可比基准，才可写『市场共识/已定价/预期差』；"
            "否则改成『缺少可核验的一致预期，预期差待确认』。因果解释若无直接证据，标成待验证假设或删除。\n"
            f"13. 决策型答案应覆盖「{section_rule}」的含义；允许重组已有草稿和证据，但不得新增事实。"
            f"{key_rule}反证必须具体到什么数据变化会推翻当前判断。"
        )

    def roundtable_prompt(self) -> str:
        """Role-neutral guidance that fits the roundtable's fixed four headings."""
        if not self.is_research:
            return ""
        variable_rule = (
            f"在『下一步核验』按重要性列出 {self.key_variable_target} 个关键变量；"
            if self.key_variable_target
            else ""
        )
        return (
            f"\n【买方研究口径 {PROTOCOL_VERSION}】本题是{self.task_label}，期限按『{self.horizon_label}』处理。"
            f"{self.decision_context_note} {self.research_lens}"
            "证据角色区分事实与来源质量；研究角色区分市场共识、研究判断和待验证假设；"
            "风险角色必须提出能推翻主结论的具体反证。"
            "主席在『核心依据』中写清市场在定价什么及预期差；无共识证据就明确缺口，不得臆造。"
            f"{variable_rule}不要把 AI 结论写成自动交易指令。\n"
        )


def _task_type(text: str) -> tuple[str, str]:
    if _METHOD_RE.search(text):
        return "method_explanation", "投资方法解释"
    if _DOCUMENT_RE.search(text) and not _DECISION_RE.search(text):
        return "document_summary", "材料解读"
    if _SCREEN_RE.search(text):
        return "stock_screen", "候选股筛选"
    if _COMPARISON_RE.search(text):
        return "stock_comparison", "多标的比较"
    if _MANAGEMENT_RE.search(text):
        return "management_quality", "管理层可信度研究"
    if _EARNINGS_RE.search(text):
        return "earnings_event", "财报与预期研究"
    if _PORTFOLIO_RE.search(text):
        return "portfolio_review", "组合风险复核"
    if _MARKET_RE.search(text):
        return "market_review", "市场复盘"
    if _NEWS_RE.search(text):
        return "news_digest", "资讯影响分析"
    if _FACT_RE.search(text) and not _DECISION_RE.search(text):
        return "fact_lookup", "事实查询"
    return "single_stock", "个股投资研究"


def _horizon(text: str, task_type: str) -> tuple[str, str, bool]:
    if task_type in {"fact_lookup", "method_explanation", "document_summary", "news_digest", "market_review"}:
        return "not_applicable", "当前事实/材料口径（不外推持有期）", False
    if re.search(r"盘中|今天|今日|明天|本周|超短|日内|短线|[1-5]\s*天", text, re.I):
        return "intraday", "日内至 1 周", True
    if re.search(r"长线|长期|长期持有|护城河|三年|五年|十年|[2-9]\s*[—\-~到至]\s*[3-9]\s*年|[2-9]\s*年", text, re.I):
        return "long", "3—5 年及以上", True
    if re.search(r"半年|6\s*[—\-~到至]\s*12\s*个?月|未来.{0,3}(?:一年|1\s*年)|中期|12\s*个?月", text, re.I):
        return "medium", "6—12 个月", True
    if re.search(r"财报前|财报后|事件驱动|未来.{0,3}(?:一个|1)季度|[1-4]\s*(?:周|个?季度)|短期", text, re.I):
        return "event", "1—4 周/未来 1—2 个季度", True
    if task_type == "earnings_event":
        return "event", "默认 1—4 周/未来 1—2 个季度", False
    if task_type == "management_quality":
        return "cross_cycle", "默认跨周期（至少覆盖多个报告期）", False
    if task_type == "portfolio_review":
        return "medium", "默认 6—12 个月，并单列短期风险", False
    return "dual", "默认同时看未来 1—4 个季度与 3—5 年", False


def _decision_context_note(horizon: str, explicit: bool) -> str:
    if explicit:
        return "用户已给出期限，所有催化、估值和风险必须与该期限对齐。"
    if horizon == "dual":
        return "用户未说明持有期；本轮采用短中期+长期双视角，若两种结论不同必须分别写，不能擅自假定用户是长线投资者。"
    if horizon == "not_applicable":
        return "本题不需要推断用户投资风格，也不把材料摘要自动升级为交易结论。"
    return "用户未明确完整投资背景；以上期限是本轮透明默认值，答案需允许用户随后修正。"


def _research_lens(task_type: str, horizon: str) -> str:
    task_lenses = {
        "fact_lookup": "只回答可核验事实、数据时点和统计口径。",
        "method_explanation": "解释公式、适用条件和常见误区，不调用现实公司记忆补例子。",
        "document_summary": "区分材料原文事实、作者观点和可进一步验证的假设。",
        "news_digest": "先核对发生了什么，再写影响路径、受影响变量与尚不能确定的部分。",
        "market_review": "区分市场表现与因果解释；没有催化证据时不强行归因。",
        "stock_screen": "先限定市场、期限、风险偏好和真实样本池，再按同口径筛选并披露未覆盖范围。",
        "stock_comparison": "先对齐用户目标、数据时点和报告期，再按相同维度比较；数据覆盖差异不等于投资优劣。",
        "management_quality": "回看历史承诺—实际结果、指引偏差、资本配置、内部人行为及 CEO/CFO 表述一致性。",
        "earnings_event": "围绕一致预期、公司指引、细分经营指标、盈利预测修正和事件催化寻找拐点。",
        "portfolio_review": "把收益来源、相关性、集中度、流动性和失效情景放在同一风险预算下。",
    }
    if task_type in task_lenses:
        return task_lenses[task_type]
    horizon_lenses = {
        "intraday": "关注当日事实、价格/成交结构与明确催化，不用单日波动证明长期逻辑。",
        "event": "关注预期差、指引变化、经营拐点、催化兑现与未来几个季度盈利修正。",
        "medium": "关注未来 6—12 个月盈利兑现、估值消化、催化时点和市场叙事变化。",
        "long": "关注行业结构、护城河、单位经济、资本配置、管理层可信度和长期价值驱动。",
        "dual": "短中期看预期修正与催化，长期看竞争优势与价值驱动，两套证据和结论分开。",
    }
    return horizon_lenses.get(horizon, horizon_lenses["dual"])


def _required_sections(task_type: str) -> tuple[str, ...]:
    if task_type == "fact_lookup":
        return ("直接答案", "时点与口径")
    if task_type == "method_explanation":
        return ("定义", "方法", "适用边界")
    if task_type == "document_summary":
        return ("原文事实", "原文观点", "待验证事项")
    if task_type in {"news_digest", "market_review"}:
        return ("结论", "已核验事实", "影响路径", "待验证事项")
    return ("结论与期限", "市场预期与核心矛盾", "证据与预期差", "风险、反证与失效条件", "关键变量与人工复核")


def build_buy_side_mandate(question: str, context_hint: str = "") -> BuySideResearchMandate:
    """Classify a user question without using an LLM or external state."""
    question_text = str(question or "").strip()
    context_text = str(context_hint or "")
    combined = f"{context_text[-1800:]}\n{question_text}".strip()
    is_research = bool(
        question_text
        and not _SMALLTALK_RE.fullmatch(question_text)
        and (
            _RESEARCH_RE.search(combined)
            or _DECISION_RE.search(question_text)
            or _FACT_RE.search(question_text)
            or _METHOD_RE.search(question_text)
            or _TICKER_RE.search(question_text)
        )
    )
    if not is_research:
        return BuySideResearchMandate(
            is_research=False,
            task_type="non_research",
            task_label="非投研问答",
            horizon="not_applicable",
            horizon_label="不适用",
            horizon_explicit=False,
            decision_context_note="",
            research_lens="自然、简短回答即可。",
            required_sections=(),
            key_variable_target=0,
        )

    task_type, task_label = _task_type(question_text)
    horizon, horizon_label, explicit = _horizon(question_text, task_type)
    if task_type in {"fact_lookup", "method_explanation"}:
        key_variable_target = 0
    elif _DEEP_RESEARCH_RE.search(question_text):
        key_variable_target = 15
    elif task_type in {"news_digest", "market_review", "document_summary"}:
        key_variable_target = 3
    else:
        key_variable_target = 5
    return BuySideResearchMandate(
        is_research=True,
        task_type=task_type,
        task_label=task_label,
        horizon=horizon,
        horizon_label=horizon_label,
        horizon_explicit=explicit,
        decision_context_note=_decision_context_note(horizon, explicit),
        research_lens=_research_lens(task_type, horizon),
        required_sections=_required_sections(task_type),
        key_variable_target=key_variable_target,
    )


def answer_protocol_issues(mandate: BuySideResearchMandate, answer: str) -> list[str]:
    """Cheap deterministic eval signals; never rewrites or invents answer content."""
    text = str(answer or "")
    if not mandate.is_research or mandate.task_type in {"fact_lookup", "method_explanation"}:
        return []
    issues: list[str] = []
    if mandate.needs_variant_view and not re.search(r"市场预期|市场定价|一致预期|共识|预期差", text):
        issues.append("missing_market_expectation_or_gap")
    if not re.search(r"反证|证伪|失效|推翻", text):
        issues.append("missing_falsifier")
    if mandate.key_variable_target and not re.search(r"关键变量|下一步核验|人工复核|待核验", text):
        issues.append("missing_human_verification")
    if not mandate.horizon_explicit and mandate.horizon == "dual" and not (
        re.search(r"季度|短中期|短期", text) and re.search(r"长期|3.?5年|三到五年", text)
    ):
        issues.append("missing_transparent_horizon_assumption")
    return issues


__all__ = [
    "PROTOCOL_VERSION",
    "BuySideResearchMandate",
    "answer_protocol_issues",
    "build_buy_side_mandate",
]
