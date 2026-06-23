"""资讯内容过滤（两档）：引流广告→丢弃；研报夹带竞品域名→抹域名保留；正常资讯放行。

注：策略已由「含 斧头/futou 即一律丢弃」精炼为「广告才丢、研报抹域名保留」（运营决策：
保留正经投行研报内容，只隐去竞品 futoucaixin 域名/品牌；纯引流广告才整条拦下）。
"""
from __future__ import annotations

from deepfocus_api import news_filter as nf


def test_drops_ads():
    # 纯广告标题
    assert nf.should_block("微信群", "")
    assert nf.should_block("加群", "")
    # 竞品词 + 广告特征同现 → 整条丢弃
    assert nf.block_reason("微信群", "认准我们的官网 www.futoucaixin.cn 客服微信 futoucj") is not None
    assert nf.should_block("福利", "斧头财信 扫码加微信进群")


def test_research_with_competitor_domain_kept_and_scrubbed():
    title = "【投行研报】大摩：关于CPO担忧的看法"
    content = "正文要点…… || https://backend.futoucaixin.cn/uploads/20260611/921.pdf"
    # 只夹带竞品域名、无广告特征 → 不丢弃
    assert nf.block_reason(title, content) is None
    nt, nc, nu, changed = nf.scrub(title, content, "https://backend.futoucaixin.cn/x.pdf")
    assert changed
    assert "futoucaixin" not in (nt + nc).lower()         # 可见正文/标题里的域名已抹
    assert "大摩" in nt and "CPO" in nt and "正文要点" in nc  # 研报内容保留
    # 竞品链接直接删除,不留「链接已隐藏」提示,且收尾不留悬挂分隔符
    assert "链接已隐藏" not in (nt + nc)
    assert nc == "正文要点……"                              # 「 || URL」整段被清掉
    # 结构化 url 原文链接字段刻意不动（聚合源整体挂该域名，属单独决策）
    assert nu == "https://backend.futoucaixin.cn/x.pdf"


def test_scrub_removes_link_without_placeholder():
    # URL 在句尾、句中、被括号包裹三种位置都干净删除,不留占位符/空括号/悬挂分隔符
    nt, nc, _u, ch = nf.scrub("高盛：微信AI智能体公测", "详情见 https://backend.futoucaixin.cn/a 了解", "")
    assert ch and "链接已隐藏" not in nc and "futoucaixin" not in nc.lower()
    assert nc == "详情见 了解"
    _t2, nc2, _u2, _c2 = nf.scrub("x", "公测（https://backend.futoucaixin.cn/b）", "")
    assert nc2 == "公测" and "（）" not in nc2               # 空括号也清掉
    _t3, nc3, _u3, _c3 = nf.scrub("x", "公测 https://backend.futoucaixin.cn/c", "")
    assert nc3 == "公测"                                     # 句尾链接+空格清掉


def test_passes_normal_financial_news():
    assert not nf.should_block("贵州茅台发布一季报，净利同比增长20%", "营收创新高")
    assert not nf.should_block("美联储维持利率不变", "鲍威尔讲话偏鸽")
    nt, nc, nu, changed = nf.scrub("沪指放量收涨", "资源股集体爆发", "https://daocaijing.com/x")
    assert not changed and nt == "沪指放量收涨" and nu == "https://daocaijing.com/x"


def test_empty_is_not_blocked():
    assert not nf.should_block("", "")
    assert nf.block_reason("   ", "  ") is None


def test_despace_evasion_in_ads():
    # 拆字绕过：与广告特征同现仍判广告
    assert nf.should_block("微信群", "fu tou cai xin 客服微信")


def test_env_extends_terms(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_NEWS_BANNED_TERMS", "newrival")
    monkeypatch.setenv("DEEPFOCUS_NEWS_AD_MARKERS", "私聊")
    assert nf.block_reason("标题", "来 newrival 私聊咨询") is not None
    assert not nf.should_block("贵州茅台一季报", "营收创新高")  # 不误伤
