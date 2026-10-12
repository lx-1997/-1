"""Conservative routing for questions that require no fresh financial evidence.

False is the safe default: the live/evidence route decides every request outside
an explicit glossary question or a standalone greeting. No company-name model,
LLM classifier, network request, or mutable market dataset is needed here.
"""
from __future__ import annotations

import re
import unicodedata


_FINANCIAL_TERMS = (
    "市盈率", "市净率", "股息率", "分红率", "派息率", "每股收益", "每股净资产",
    "净资产收益率", "总资产收益率", "营业收入", "营业利润", "净利润", "毛利率", "净利率",
    "资产负债率", "现金流量表", "资产负债表", "利润表", "财务报表", "经营现金流", "自由现金流", "现金流",
    "复合年增长率", "年化收益率", "波动率", "标准差", "夏普比率", "最大回撤", "贝塔", "阿尔法",
    "贴现率", "折现率", "净现值", "现值", "内部收益率", "账面价值", "市场价值", "估值",
    "证券", "股票", "普通股", "优先股", "债券", "国债", "企业债", "信用债", "可转债",
    "指数基金", "货币基金", "开放式基金", "封闭式基金", "基金", "ETF联接基金",
    "看涨期权", "看跌期权", "认购期权", "认沽期权", "期权", "期货", "行权价", "权利金",
    "保证金", "杠杆", "做多", "做空", "价值投资", "成长投资", "指数投资", "基金定投",
    "通货膨胀", "通货紧缩", "利率", "汇率", "基点", "同比", "环比", "流动性", "风险溢价",
    "信用利差", "复利", "单利", "成交量", "换手率", "市值", "K线", "均线", "布林带", "除权", "除息",
    "PE", "P/E", "PB", "P/B", "PS", "P/S", "PEG", "ROE", "ROA", "EPS", "NAV", "ETF", "REIT", "REITs",
    "EBIT", "EBITDA", "IRR", "NPV", "DCF", "CAGR", "TTM", "MACD", "RSI", "GDP", "CPI", "PPI", "IPO",
    "price-to-earnings ratio", "price to earnings ratio", "price-to-book ratio", "price to book ratio",
    "earnings per share", "return on equity", "return on assets", "free cash flow", "cash flow",
    "dividend yield", "market cap", "index fund", "stock", "stocks", "bond", "bonds", "option", "options",
    "futures", "volatility", "compound interest", "simple interest",
)


def _term_pattern(term: str) -> str:
    escaped = re.escape(term)
    if re.search(r"[A-Za-z]", term):
        # PE is a term; AAPLPE and PE.HK must not become a glossary question.
        return rf"(?<![A-Za-z0-9]){escaped}(?![A-Za-z0-9])"
    return escaped


_TERM_RE = re.compile("|".join(_term_pattern(term) for term in sorted(_FINANCIAL_TERMS, key=len, reverse=True)), re.I)
_GREETING_RE = re.compile(
    r"(?:你好[呀啊]?|您好|嗨|哈喽|早上好|上午好|中午好|下午好|晚上好|早安|晚安|在吗|你在吗|谢谢(?:你)?|"
    r"hello(?:\s+there)?|hi(?:\s+there)?|hey|good\s+(?:morning|afternoon|evening)|thanks|thank\s+you)"
    r"[\s!！?？。.,，~～]*\Z", re.I,
)
# These triggers override glossary words. "风险溢价" and "价值投资" remain
# valid terms; a decision about taking risk or investing is a different request.
_EVIDENCE_OR_ACTION_RE = re.compile(
    r"当前|现在|今天|今日|昨天|昨日|明天|本周|本月|今年|去年|最近|近期|最新|实时|目前|现价|行情|"
    r"历史|过去|走势|涨跌|上涨|下跌|预测|趋势|新闻|公告|财报|研报|消息|政策|资料|数据|"
    r"我的|我持|我买|我卖|我想|我该|我应|你认为|你觉得|我们|咱们|账户|持仓|自选|交割|交易记录|记录|"
    r"查(?:下|一下|询)?|核实|核对|验证|校验|分析|评估|评价|怎么看|怎么样|好不好|多少|高不高|低不低|"
    r"合理|便宜|贵不贵|更好|更优|适合|推荐|建议|选择|选股|应该|能否|能不能|该不该|值得|是否|"
    r"买入|卖出|买不买|卖不卖|买卖|购买|卖掉|抄底|加仓|减仓|仓位|下单|操作|开户|转账|提交|修改|删除|保存|"
    r"[¥￥$€£%％=<>]|https?://|www\.|@|"
    r"\b(?:today|yesterday|tomorrow|latest|current|currently|now|recent|live|price|prices|quote|quotes|"
    r"news|forecast|predict|recommend|should|buy|sell|portfolio|watchlist|account|my|our|verify|check|lookup|fetch|update|delete)\b",
    re.I,
)
_INTENT_RE = re.compile(
    r"解释|说明|介绍|定义|含义|概念|区别|差别|不同|差异|什么意思|什么是|是什么|"
    r"\b(?:explain|define|definition|meaning|difference|differences|distinguish|compare|vs)\b|"
    r"\bwhat\s+(?:is|are)\b|\bwhat['’]s\b|(?<![A-Za-z])vs(?![A-Za-z])", re.I,
)
_CN_OUTPUT_FORMAT_RE = re.compile(r"(?:请)?(?:用|以|使用|控制在|不超过|最多)?[一二三四五六七八九十百两\d]{1,4}(?:句话|个要点|条要点|个例子|句|字)")
_EN_OUTPUT_FORMAT_RE = re.compile(r"\b(?:in|using|within|no more than|at most)\s+(?:one|two|three|four|five|six|seven|eight|nine|ten|\d{1,4})\s+(?:sentences?|words?|bullet points?)\b", re.I)
# After recognized terms and output constraints are removed, every character
# must still belong to explanatory grammar. An unrecognized ticker/company,
# document title, or personal detail cannot silently pass through this check.
_GRAMMAR_TOKEN_RE = re.compile(
    r"(?:__TERM__|[\s,，。.!！?？:：;；、()（）\[\]【】“”\"'‘’/\-—]|"
    r"请问|请|麻烦|帮忙|帮我|简单|简短|简要|通俗|易懂|清楚|准确|分别|各自|一般|通常|金融|术语|"
    r"解释|说明|介绍|定义|含义|概念|区别|差别|不同|差异|什么|是|意思|之间|关系|对比|比较|"
    r"计算公式|公式|和|与|及|以及|或者|或|的|有|有什么|怎么|如何|用|一下|一下子|呢|吗|啊|"
    r"\b(?:what|is|are|the|a|an|of|and|or|between|to|please|briefly|plain|simple|language|terms|financial|finance|"
    r"explain|define|definition|definitions|meaning|difference|differences|distinguish|compare|versus|vs|in|how|do|does|differ|from|its|their|formula)\b)",
    re.I,
)


def is_static_explanation(message: str, has_attachment: bool = False) -> bool:
    """Allow only a standalone greeting or an explicit finance glossary request."""
    if has_attachment or not isinstance(message, str):
        return False
    text = unicodedata.normalize("NFKC", message).strip()
    if not text or len(text) > 400 or any(unicodedata.category(char) == "Cf" for char in text):
        return False
    if _GREETING_RE.fullmatch(text):
        return True
    if not _INTENT_RE.search(text):
        return False
    text = _CN_OUTPUT_FORMAT_RE.sub(" ", text)
    text = _EN_OUTPUT_FORMAT_RE.sub(" ", text)
    residual, term_count = _TERM_RE.subn(" __TERM__ ", text)
    if not 1 <= term_count <= 6:
        return False
    # A glossary term such as "price-to-earnings ratio" contains "price";
    # it does not ask for a current price. Check triggers outside known terms.
    if _EVIDENCE_OR_ACTION_RE.search(residual):
        return False
    # Output-length numbers were already removed. Any remaining number is a
    # concrete observation/identifier, even if it appears next to a known term.
    if re.search(r"\d", residual):
        return False
    # Unknown all-caps identifiers are particularly likely to be ticker codes.
    uppercase_words = re.findall(r"\b[A-Z]{1,6}\b", residual.replace("__TERM__", ""))
    if any(word not in {"WHAT", "IS", "ARE", "THE", "OF", "AND", "OR", "TO", "DEFINE", "VS"} for word in uppercase_words):
        return False
    # Match one token at a time. A repeated full-sentence alternation can
    # backtrack exponentially on ambiguous grammar followed by a company name.
    position = 0
    while position < len(residual):
        token = _GRAMMAR_TOKEN_RE.match(residual, position)
        if token is None:
            return False
        position = token.end()
    return True
