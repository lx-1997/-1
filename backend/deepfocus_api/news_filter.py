"""资讯内容过滤：在「入库之前」对竞品「斧头财信 / futoucaixin」内容做两档处理。

两档语义（与运营决策一致）：
1) **广告 → 丢弃**：明显的引流广告（如「微信群 + 客服微信/官网 futoucaixin/倒卖渠道」），
   `block_reason` 命中即不入库、不广播、不召回。
2) **正经研报夹带竞品域名 → 隐去后保留**：内容有价值、只是 PDF/图片链接挂在竞品域名
   `futoucaixin.cn` 上——`scrub` 把竞品域名/品牌字眼抹掉但保留文章本身。

所有快讯/文章（DAO 桥接、手动推送、数据源同步）都过这道闸。大小写不敏感 + 去空格匹配
（兼容 "fu tou" / "斧 头" 拆字绕过）。可用 env 追加屏蔽词/广告词，运营无需改代码：
    DEEPFOCUS_NEWS_BANNED_TERMS   追加「竞品品牌/域名」词（命中→参与判定与抹除）
    DEEPFOCUS_NEWS_AD_MARKERS     追加「广告特征」词（与竞品词同现→整条丢弃）
"""
from __future__ import annotations

import os
import re
from typing import Optional, Tuple

# 竞品品牌/域名词（命中即「需处理」：要么丢广告、要么抹域名保留）。用户点名：斧头 / futou。
_DEFAULT_BANNED: Tuple[str, ...] = (
    "futoucaixin", "斧头财信", "斧头财经", "斧头", "futou", "fǔtóu",
)
# 广告特征词：与竞品词「同现」即判定为引流广告 → 整条丢弃（不误伤只夹带域名的研报）。
_DEFAULT_AD_MARKERS: Tuple[str, ...] = (
    "微信群", "客服微信", "加微信", "扫码加", "扫码进", "二维码", "倒卖渠道",
    "认准我们", "认准官网", "唯一官网", "加群", "进群",
)
# 纯广告标题（本身就是引流标题，无需正文同现）。
_AD_TITLES: Tuple[str, ...] = ("微信群", "加群", "进群", "客服微信")

_WS_RE = re.compile(r"\s+")
# 竞品域名 URL（带/不带 scheme），整段抹除
_URL_RE = re.compile(r"(https?://)?[\w.-]*futoucaixin\.cn[^\s)）」』】\]]*", re.I)
# 上游内容里的第三方品牌字样（用户点名全局不得出现），抹词保留文章本身。
# TradeAlpha 兼容 Trade Alpha / trade-alpha / trade_alpha 变体；顺带吃掉紧随的一个空格，
# 避免 "TradeAlpha 财经新闻专题" 抹词后残留行首/串中双空格（不吃换行）。
_BRAND_SCRUB_RE = re.compile(r"trade[\s\-_]?alpha[ \t]?", re.I)


def _env_terms(var: str) -> list:
    raw = os.getenv(var, "") or ""
    return [w.strip().lower() for w in re.split(r"[,，;；\n]", raw) if w.strip()]


def banned_terms() -> list:
    """竞品词（默认 + env 追加），小写去重。"""
    out: list = []
    for w in [*(d.lower() for d in _DEFAULT_BANNED), *_env_terms("DEEPFOCUS_NEWS_BANNED_TERMS")]:
        if w and w not in out:
            out.append(w)
    return out


def ad_markers() -> list:
    out: list = []
    for w in [*(d.lower() for d in _DEFAULT_AD_MARKERS), *_env_terms("DEEPFOCUS_NEWS_AD_MARKERS")]:
        if w and w not in out:
            out.append(w)
    return out


def _contains_any(hay: str, hay_nospace: str, terms: list) -> Optional[str]:
    for w in terms:
        if not w:
            continue
        if w in hay or _WS_RE.sub("", w) in hay_nospace:
            return w
    return None


def block_reason(title: str, content: str = "") -> Optional[str]:
    """是否「整条丢弃」——仅针对引流广告：竞品词 + 广告特征同现，或本身是纯广告标题。
    只夹带竞品域名的正经研报不在此丢弃（交给 scrub 抹域名保留）。返回原因或 None。"""
    t = (title or "").strip().lower()
    if _WS_RE.sub("", t) in [a.lower() for a in _AD_TITLES]:
        return "ad-title"
    hay = f"{title or ''}\n{content or ''}".lower()
    if not hay.strip():
        return None
    hay_ns = _WS_RE.sub("", hay)
    comp = _contains_any(hay, hay_ns, banned_terms())
    if not comp:
        return None
    mark = _contains_any(hay, hay_ns, ad_markers())
    if mark:
        return f"ad:{comp}+{mark}"
    return None  # 有竞品词但无广告特征 → 不丢，交给 scrub


def _tidy(s: str) -> str:
    """链接/品牌词删除后的收尾清理:去空括号对、串尾悬挂的分隔符/链接引导词、合并多余空白。
    不做激进的串中清理(避免误伤正文),只处理删除后最常见的"尾巴"残留。"""
    s = re.sub(r"[（(【\[「]\s*[）)】\]」]", "", s)                 # 删除后残留的空括号对
    s = re.sub(r"[ \t]{2,}", " ", s)                              # 多空格→单空格
    s = re.sub(r"[ \t]+([，。；、！？）】」])", r"\1", s)           # 标点前多余空格
    s = re.sub(r"[ \t]*[|｜·•\-—–]+[ \t]*$", "", s)               # 串尾悬挂分隔符(| · — 等)
    s = re.sub(r"[ \t]*(链接|详情|原文|全文|查看详情|点击查看|阅读原文)[:：]?[ \t]*$", "", s)  # 串尾悬挂引导词
    return s.rstrip()


def scrub(title: str, content: str = "", url: str = "") -> Tuple[str, str, str, bool]:
    """抹掉**可见正文/标题**里的竞品域名/品牌字眼，保留文章本身。

    ⚠️只动用户可见的 title/content（诉求是「内容出现 futou/斧头」）；结构化 `url` 原文链接**不在此处理**——
    聚合源(DAO财经/futoucaixin)几乎所有文章 url 都挂该域名，批量清空会让全部文章丢「原文」跳转，
    属另一个需单独决策的范围。返回 (title, content, url, changed)。"""
    def _clean(s: str) -> str:
        if not s:
            return s
        out = _URL_RE.sub("", s)  # 竞品链接直接删掉,不留「[链接已隐藏]」占位符(用户不想看到这种提示)
        for w in ("futoucaixin", "斧头财信", "斧头财经"):
            out = re.sub(re.escape(w), "", out, flags=re.I)
        out = _BRAND_SCRUB_RE.sub("", out)  # 第三方品牌字样(TradeAlpha 等)抹词保留文章
        return _tidy(out) if out != s else s
    nt, nc = _clean(title or ""), _clean(content or "")
    changed = (nt != (title or "")) or (nc != (content or ""))
    return nt, nc, (url or ""), changed


def should_block(title: str, content: str = "") -> bool:
    return block_reason(title, content) is not None


# ── 垃圾 / 非新闻内容（反爬提示、上游误抓进来的域名喊话页等，非真实文章）─────────────
# 均为特征短语，正规财经快讯/文章几乎不会命中；可用 env DEEPFOCUS_NEWS_JUNK_MARKERS 追加。
_DEFAULT_JUNK_MARKERS: Tuple[str, ...] = (
    # 爬虫/抓取类（"爬虫" 裸词覆盖 停止爬虫/禁止爬虫/反爬虫/爬虫协议… 一切含"爬虫"的）——用户点名：带"爬虫"字眼一律拦
    "爬虫", "禁止抓取", "停止抓取", "抓取频率", "robots.txt",
    # 反爬/限流/错误页类（上游误抓的非文章页本身；⚠️刻意不放"网络攻击/ddos/黑客"这类**正经新闻话题词**——
    # 那些是真实财经/时政新闻，误删=漏发新闻；针对"我方"的攻击由下方 self-attack(品牌+敌意词)兜住）
    "访问过于频繁", "请求过于频繁", "访问频率过高", "该网站已被",
    "stop crawling", "stop scraping", "access denied", "403 forbidden",
    # 直播导流类伪快讯（上游聚合源混入的第三方直播间/分析师推广钩子，标题像新闻实则无信息量，
    # 目的是导流去看直播/找分析师——用固定营销短语识别，不匹配人名（多变）；正规快讯不会用这类措辞。
    "正在直播中", "直播预告", "扫码观看直播", "识别二维码观看直播", "点击马上看", "点击立即观看",
    "戳我观看", "马上看直播", "正在多角度解析中", "正在为您解析",
)
_DOMAIN_RE = re.compile(r"[\w-]+\.(?:com|cn|net|org|io|co)(?![a-z0-9-])", re.I)  # 裸域名（TLD 后不接 ASCII 字母数字；兼容后接中文/标点）
_IMPERATIVE_RE = re.compile(r"(请|停止|禁止|勿|警告|谢绝)")               # 祈使/喊话
_SELF_BRAND_RE = re.compile(r"daocaijing|道财经", re.I)                   # 我方域名/品牌（攻击目标）
# 明确敌意词（**刻意不含"请"等中性祈使**，避免误伤我方"请关注 daocaijing.com"这类正常文案）
_HOSTILE_RE = re.compile(r"(停止|禁止|警告|攻击|滚蛋|骗子|骗人|盗版|侵权|举报|封禁|封号|黑客|恶意|爬虫|抓取|抄袭|山寨|假冒)")


def junk_markers() -> list:
    out: list = []
    for w in [*(d.lower() for d in _DEFAULT_JUNK_MARKERS), *_env_terms("DEEPFOCUS_NEWS_JUNK_MARKERS")]:
        if w and w not in out:
            out.append(w)
    return out


def junk_reason(title: str, content: str = "") -> Optional[str]:
    """非新闻 / 垃圾内容（反爬提示、上游误抓的域名喊话页）→ 整条丢弃。返回原因或 None。
    仅用「特征短语」+「短标题=裸域名+祈使」两条稳妥规则，避免误伤正常财经内容。"""
    hay = f"{title or ''}\n{content or ''}".lower()
    if not hay.strip():
        return "empty"
    hay_ns = _WS_RE.sub("", hay)
    for w in junk_markers():
        if w in hay or _WS_RE.sub("", w) in hay_ns:
            return f"junk:{w}"
    # 针对我方的恶意 / 骚扰内容（反爬页、攻击喊话、辱骂等）：可见文字里同时出现「我方品牌/域名」+「敌意/祈使词」。
    # 第三方财经新闻的可见正文几乎不会出现我方域名，出现且带敌意 → 判为攻击/自指垃圾。
    if _SELF_BRAND_RE.search(hay) and _HOSTILE_RE.search(hay):
        return "junk:self-attack"
    # 标题本身就是「裸域名 + 祈使喊话」（如 example.com请停止…）：短标题、含域名、含祈使动词，
    # 且**去掉域名+祈使词后所剩无几**——即标题本体就是"域名+喊话"（攻击），而非"正常句子恰好带个域名+请"。
    t_ns = _WS_RE.sub("", (title or "").strip())
    if 0 < len(t_ns) <= 40 and _DOMAIN_RE.search(t_ns) and _IMPERATIVE_RE.search(t_ns):
        rest = _IMPERATIVE_RE.sub("", _DOMAIN_RE.sub("", t_ns))
        rest = re.sub(r"[^\w一-鿿]", "", rest)      # 去标点/符号
        if len(rest) <= 5:
            return "junk:domain-plea"
    return None


def discard_reason(title: str, content: str = "") -> Optional[str]:
    """统一「是否整条丢弃」口径，create_realtime_message 入库前 + retract_news 撤回存量共用（避免两套标准）：
      ① 引流广告（竞品词+广告特征同现）
      ② 垃圾/非新闻（反爬提示、域名喊话页）
      ③ 竞品词（futou/斧头）出现在**用户可见**的标题/正文里
    注意：只夹带竞品域名在结构化 `url` 里的正经研报不在此列（由 scrub 抹域名保留，不误删存量）。"""
    r = block_reason(title, content)
    if r:
        return r
    r = junk_reason(title, content)
    if r:
        return r
    hay = f"{title or ''}\n{content or ''}".lower()
    hay_ns = _WS_RE.sub("", hay)
    comp = _contains_any(hay, hay_ns, banned_terms())
    if comp:
        return f"competitor-visible:{comp}"
    return None
