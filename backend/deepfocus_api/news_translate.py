"""英文快讯 → 中文直译（入库前）。

终端是中文产品，但上游 DAO/futou 源会直出英文快讯（路透式地缘/能源标题）。
本模块在 dao_bridge 灌入 create_realtime_message 之前，把英文快讯【直译】成简体中文，
让译文直接落进 title/content —— 全链路（SSE/SEO 页/召回邮件/AI agent/对外 API）都拿到中文，
而不只是终端前端。英文原文由调用方存进 metadata 备查。

设计红线（沿用「快讯逐字忠实」原则，见 memory:news-share-jin10-style）：
- 只直译、不改写、不加观点；数字/代码/机构名照搬，绝不篡改数值。
- 失败 / 超时 / 不是英文 → 返回 None，调用方回退英文原文，绝不丢消息。
- 按内容 md5 指纹缓存到 data_store（复用 memory:llm-token-cost-optimization 那套），
  同一条英文重复进来零成本复用，省 MiniMax token。
"""
from __future__ import annotations

import hashlib
import os
import re
from typing import Optional

from . import data_store
from .llm import CloudResearchLLM

# 字母占「字母+汉字」比例超过该值才判定为英文快讯（默认 0.55：中文为主的混排快讯不翻）。
_EN_RATIO_THRESHOLD = float(os.getenv("DEEPFOCUS_NEWS_TRANSLATE_EN_RATIO", "0.55"))
# 至少这么多个英文字母才考虑翻译（挡掉「茅台 EPS」这类零星缩写，避免无谓调模型）。
_MIN_LETTERS = int(os.getenv("DEEPFOCUS_NEWS_TRANSLATE_MIN_LETTERS", "12"))
_CACHE_KIND = "news_trans"
_CACHE_MAX_AGE = float(os.getenv("DEEPFOCUS_NEWS_TRANSLATE_CACHE_DAYS", "90")) * 86400

# ── 数字忠实核验（多花一轮 token 换数字红线）──────────────────────────────
# 财报/数据型英文快讯里「0.5→5%」「净利→总利」「billion 漏成 million」是最伤的错译。
# 仅当原文数字够密(达 _VERIFY_MIN_NUMBERS)才多跑一轮专盯数字/单位/方向的质检——
# 叙述型快讯(地缘/人事)不触发,绝不无谓烧 token；质检失败安全回退基础译文，绝不丢消息。
_VERIFY_ENABLED = os.getenv("DEEPFOCUS_NEWS_TRANSLATE_VERIFY", "1") not in ("0", "false", "False", "no")
_VERIFY_MIN_NUMBERS = int(os.getenv("DEEPFOCUS_NEWS_TRANSLATE_VERIFY_MIN_NUMBERS", "3"))
_NUM_TOKEN_RE = re.compile(r"\d+(?:[.,]\d+)?")
# 「锚点数字」=百分比与四位年份：这类在中英之间不做量级换算(5%→5%、2028→2028)，
# 适合做确定性套利仲裁——译文若把原文的某个百分比/年份弄丢了，就是实锤退化。
_PCT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b|pct\b)", re.I)
_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")


def looks_english(text: str) -> bool:
    """文本是否「以英文为主」——据此决定要不要翻译。纯中文/中文为主/太短 → False。"""
    if not text:
        return False
    letters = sum(1 for c in text if c.isascii() and ("a" <= c.lower() <= "z"))
    if letters < _MIN_LETTERS:
        return False
    han = sum(1 for c in text if "一" <= c <= "鿿")
    total = letters + han
    if total == 0:
        return False
    return letters / total >= _EN_RATIO_THRESHOLD


def _build_prompt(title: str, content: str) -> str:
    return (
        "你是金融快讯翻译。把下面的英文财经快讯【直译】成简体中文，严格遵守：\n"
        "①只翻译、不改写、不增删、不加任何观点/解读/背景；\n"
        "②数字、百分比、日期、货币金额、股票代码、机构/公司/人名、缩写(如 EU/OPEC/CPI/Fed)"
        "一律照搬或用通行中文译名，绝不篡改任何数值；\n"
        "③保持原有语气与信息量，不夸张不弱化；\n"
        "④若没有正文，content 返回空字符串。\n"
        '只返回 JSON object：{"title": "中文标题", "content": "中文正文"}。\n\n'
        f"【英文标题】{title}\n【英文正文】{content or '(无)'}"
    )


def _anchor_numbers(text: str) -> list[str]:
    """从文本里抽「锚点数字」(百分比 + 四位年份)——中英之间不做量级换算，可做确定性比对。"""
    out = [f"{m}%" for m in _PCT_RE.findall(text or "")]
    out += list(_YEAR_RE.findall(text or ""))
    return out


def _anchor_coverage(src_en: str, zh: str) -> int:
    """原文每个锚点数字在译文里出现几个——数字保得越全分越高（多重集合计数）。"""
    pool = list(zh or "")
    kept = 0
    # 年份直接找数字串；百分比找「数字%」或「数字 百分」(译文常写「上涨 5%」「下滑 5 个百分点」)。
    for m in _YEAR_RE.findall(src_en or ""):
        if m in (zh or ""):
            kept += 1
    for m in _PCT_RE.findall(src_en or ""):
        if (m + "%") in (zh or "") or re.search(rf"{re.escape(m)}\s*(?:%|个百分点|百分点|个点)", zh or ""):
            kept += 1
    return kept


def _build_verify_prompt(title: str, content: str, zh_title: str, zh_content: str) -> str:
    return (
        "你是金融快讯翻译质检员。下面是英文原文与它的中文译文。只核验【数字/百分比/金额/单位/日期/涨跌方向】"
        "是否被准确保留，不要润色文风、不要改写表达。逐一比对原文里每个数值与单位在译文里是否存在且正确，"
        "重点盯：billion=十亿(约10亿)/亿、million=百万、bps=基点、同比/环比别混、涨/升 与 跌/降 别反、货币单位别错。\n"
        '全部正确 → 只返回 {"ok": true}。\n'
        '有任何数字/单位/方向错误或遗漏 → 返回 {"ok": false, "issues":["问题点"], "title":"修正后中文标题", '
        '"content":"修正后中文正文"}：修正版只改错处、其余照旧，绝不新增原文里没有的数字。\n\n'
        f"【英文原文】标题：{title}\n正文：{content or '(无)'}\n\n"
        f"【中文译文】标题：{zh_title}\n正文：{zh_content or '(无)'}"
    )


async def _verify_numbers(llm, title: str, content: str, base: dict) -> dict:
    """对数字密集的财报型快讯多跑一轮专盯数字的质检；返回最终采用的译文。

    仲裁红线：用确定性「锚点数字覆盖度」当裁判——只有模型给的修正版【没弄丢】原文任何
    百分比/年份(覆盖度 ≥ 基础版)才采纳，否则保留基础版。这样第二轮只能纠错、绝不会
    因为模型『手滑』把对的数字改没了而让译文退化。失败/异常一律回退基础版。
    """
    en_blob = f"{title}\n{content}"
    if len(_NUM_TOKEN_RE.findall(en_blob)) < _VERIFY_MIN_NUMBERS:
        return base  # 数字不够密，叙述型快讯——不值得多花一轮
    try:
        chk = await llm.complete_json(
            _build_verify_prompt(title, content, base["title"], base["content"]),
            max_tokens=900, timeout_seconds=18, force_json_first=True,
            retry_schema_hint='只需 ok(bool)；不正确时再给 issues/title/content。',
        )
    except Exception:
        return base
    if not isinstance(chk, dict) or chk.get("ok") is True:
        return base
    fixed_title = str(chk.get("title") or "").strip()
    fixed_content = str(chk.get("content") or "").strip()
    if not fixed_title:
        return base
    fixed = {"title": fixed_title[:240], "content": fixed_content[:8000]}
    # 确定性仲裁：修正版必须保住原文全部锚点数字(不少于基础版)才采纳
    if _anchor_coverage(en_blob, fixed["title"] + fixed["content"]) >= \
            _anchor_coverage(en_blob, base["title"] + base["content"]):
        return fixed
    return base


async def translate_news(title: str, content: str) -> Optional[dict]:
    """英文快讯 → 中文。返回 {'title': zh, 'content': zh}；不需译/失败/超时 → None。"""
    title = (title or "").strip()
    content = (content or "").strip()
    blob = f"{title}\n{content}".strip()
    # 标题或整体任一以英文为主即触发（正文长、标题短的情况也能覆盖）。
    if not (looks_english(title) or looks_english(blob)):
        return None

    fp = hashlib.md5(blob.encode("utf-8")).hexdigest()
    cached = data_store.latest(_CACHE_KIND, fp, max_age_seconds=_CACHE_MAX_AGE)
    if isinstance(cached, dict) and str(cached.get("title") or "").strip():
        return {"title": cached["title"], "content": cached.get("content") or ""}

    llm = CloudResearchLLM()
    try:
        data = await llm.complete_json(
            _build_prompt(title, content), max_tokens=1200, timeout_seconds=20
        )
    except Exception:
        return None

    zh_title = str((data or {}).get("title") or "").strip()
    zh_content = str((data or {}).get("content") or "").strip()
    if not zh_title:
        return None
    out = {"title": zh_title[:240], "content": zh_content[:8000]}
    # 数字红线：财报/数据型快讯多花一轮核验数字/单位/方向（叙述型不触发、失败安全回退）
    if _VERIFY_ENABLED:
        out = await _verify_numbers(llm, title, content, out)
    try:
        data_store.record(_CACHE_KIND, fp, out)
    except Exception:
        pass
    return out
