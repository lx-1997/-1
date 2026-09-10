from __future__ import annotations

import asyncio

from deepfocus_api.article_original_text import (
    ArticleOriginalText,
    _html_to_article_text,
    _stored_paragraphs,
    _source_urls,
    extract_article_original_text,
)
from deepfocus_api.schemas import RealtimeMessageRecord


def _article(content: str, url: str | None = None) -> RealtimeMessageRecord:
    return RealtimeMessageRecord(
        id="article-text-test",
        title="测试深度文章",
        content=content,
        topic="文章",
        severity="info",
        url=url,
        created_at="2026-08-27T00:00:00Z",
    )


def test_uses_stored_text_without_sending_any_file_to_reader():
    result = asyncio.run(extract_article_original_text(_article("第一段正文。\n\n第二段正文。\n来源：编辑整理")))

    assert result.parser == "stored-text"
    assert result.paragraphs == ["第一段正文。", "第二段正文。"]
    assert "来源" not in result.content


def test_stored_html_fragment_becomes_clean_paragraphs():
    paragraphs = _stored_paragraphs(
        '<p style="font-size:17px;line-height:2">第一段正文，保留内容。</p>'
        '<p>第二段正文，去掉网页标签。</p>'
    )

    assert paragraphs == ["第一段正文，保留内容。", "第二段正文，去掉网页标签。"]
    assert "<p" not in "\n".join(paragraphs)


def test_image_article_is_transcribed_server_side(monkeypatch):
    from deepfocus_api import article_original_text as module

    async def fake_download(url: str):
        return module._DownloadedSource(raw=b"fake-image", filename="article.png", content_type="image/png")

    async def fake_ocr(raw: bytes):
        assert raw == b"fake-image"
        return ArticleOriginalText(["截图第一段。", "截图第二段。"], "vision-ocr")

    monkeypatch.setattr(module, "_download_source", fake_download)
    monkeypatch.setattr(module, "_ocr_image", fake_ocr)
    monkeypatch.setattr(module, "_get_cached", lambda key: None)
    monkeypatch.setattr(module, "_put_cached", lambda key, value: None)

    result = asyncio.run(extract_article_original_text(_article("这只是导语。", "https://files.example.com/original.png")))

    assert result.parser == "vision-ocr"
    assert result.paragraphs == ["截图第一段。", "截图第二段。"]


def test_article_is_prewarmed_and_reused_from_cache(monkeypatch):
    from deepfocus_api import article_original_text as module

    message = _article("这只是导语。", "https://files.example.com/original.html")
    calls: list[str] = []

    async def fake_extract(url: str):
        calls.append(url)
        return ArticleOriginalText(["预热后的正文。"], "html")

    module._ARTICLE_TEXT_CACHE.clear()
    monkeypatch.setattr(module, "_extract_remote_source", fake_extract)

    asyncio.run(module.prewarm_article_original_text(message))
    result = asyncio.run(extract_article_original_text(message))

    assert result.paragraphs == ["预热后的正文。"]
    assert calls == ["https://files.example.com/original.html"]


def test_remote_source_failure_keeps_clean_stored_lead_available(monkeypatch):
    from deepfocus_api import article_original_text as module
    from fastapi import HTTPException

    message = _article(
        '<p style="font-size:17px">已收录的文章导语，来源暂时无法读取。</p>'
        '<a href="https://www.reuters.com/story">https://www.reuters.com/story</a>',
        "https://www.reuters.com/story",
    )

    async def failed_remote(url: str):
        raise HTTPException(status_code=502, detail="upstream unavailable")

    module._ARTICLE_TEXT_CACHE.clear()
    monkeypatch.setattr(module, "_extract_remote_source", failed_remote)
    result = asyncio.run(extract_article_original_text(message))

    assert result.parser == "stored-fallback"
    assert result.truncated is True
    assert result.paragraphs == ["已收录的文章导语，来源暂时无法读取。"]


def test_local_article_capture_is_preferred_to_blocked_source_page(monkeypatch):
    from deepfocus_api import article_original_text as module

    message = _article(
        '<p>已收录导语。</p><a href="https://www.bloomberg.com/story">原文</a>',
    ).model_copy(update={"source_id": "7830"})
    capture_url = "https://backend.futoucaixin.cn/uploads/article.png"
    monkeypatch.setattr(module, "_local_article_media_urls", lambda _message: [capture_url])
    calls: list[str] = []

    async def fake_extract(url: str):
        calls.append(url)
        return ArticleOriginalText(["截图中的完整正文。"], "vision-ocr")

    monkeypatch.setattr(module, "_extract_remote_source", fake_extract)
    module._ARTICLE_TEXT_CACHE.clear()
    result = asyncio.run(extract_article_original_text(message))

    assert _source_urls(message) == [capture_url, "https://www.bloomberg.com/story"]
    assert calls == [capture_url]
    assert result.parser == "vision-ocr"
    assert result.paragraphs == ["截图中的完整正文。"]


def test_html_reader_selects_article_body_and_drops_reuters_page_shell():
    html = """
    <html>
      <body>
        <header>
          <div>Exclusive news, data and analytics for financial market professionals</div>
          <div>LSEG</div><div>Reuters</div><div>My News</div>
          <button>Sign In</button><button>Subscribe</button>
        </header>
        <main>
          <div class="article-header"><h1>Anthropic signs a cloud deal</h1><div>By Reuters</div></div>
          <div class="article-body__content__17Yit">
            <p>Anthropic has signed a cloud-computing deal worth $35 billion with Lambda.</p>
            <p>The deal will bring online Nvidia capacity to meet growing demand for Claude AI.</p>
          </div>
          <section class="read-next"><h2>Read Next</h2><p>Unrelated recommendation.</p></section>
        </main>
        <footer><div>Latest</div><div>Home</div><div>© 2026 Reuters. All rights reserved</div></footer>
      </body>
    </html>
    """.encode("utf-8")

    text, truncated = _html_to_article_text(html)

    assert not truncated
    assert "Anthropic has signed" in text
    assert "Nvidia capacity" in text
    assert "Exclusive news" not in text
    assert "My News" not in text
    assert "Unrelated recommendation" not in text
    assert "© 2026" not in text


def test_html_reader_uses_jsonld_when_visible_article_is_client_rendered():
    html = """
    <html><head>
      <script type="application/ld+json">
        {"@type":"NewsArticle","articleBody":"第一段正文，介绍需求变化和行业背景。\\n\\n第二段正文，介绍公司后续计划。"}
      </script>
    </head><body><div id="app"></div></body></html>
    """.encode("utf-8")

    text, truncated = _html_to_article_text(html)

    assert not truncated
    assert text == "第一段正文，介绍需求变化和行业背景。\n第二段正文，介绍公司后续计划。"


def test_stored_reuters_page_keeps_shein_article_and_drops_terminal_shell():
    content = "\n".join([
        "Daocaijing 金融终端 A股每日复盘 热门个股多维证据速判 财经资讯",
        "稻财经",
        "DEEPFOCUS AI",
        "主菜单",
        "深度文章",
        "★ 头条",
        "路透社：快时尚巨头希音计划在香港上市。",
        "Fast-fashion giant Shein set to open flat in Hong Kong market debut",
        "快时尚巨头施恩计划在香港市场首次开设实体店",
        "By Reuters 由路透社报道",
        "September 1, 2026 9:26 AM GMT+8 • Updated 9 mins ago",
        "HONG KONG, Sept 1 (Reuters) - Shares in fast fashion giant Shein were set open flat in their Hong Kong market debut on Tuesday.",
        "香港，9 月 1 日（路透社）——快时尚巨头 Shein 集团在周二于香港市场的首次公开募股中表现平平。",
        "Known globally for selling $5 tops and $10 dresses, Shein has been humbled by tariff and duty changes in the U.S. and Europe.",
        "Shein 以售价仅 5 美元或 10 美元的商品而闻名全球。然而，美国和欧洲关税及税收政策的变化影响了其业务。",
        "Learn about latest legal news delivered straight to your inbox from The Daily Docket newsletter. Sign up here.",
        "Our Standards: The Thomson Reuters Trust Principles.",
        "Suggested Topics: Deals Capital Markets",
        "Read Next / Editor's Picks",
        "Unrelated recommendation.",
        "About Reuters",
        "© 2026 Reuters. All rights reserved",
    ])

    paragraphs = _stored_paragraphs(content, "路透社：快时尚巨头希音计划在香港上市。")
    body = "\n".join(paragraphs)

    assert "Shares in fast fashion giant Shein" in body
    assert "关税及税收政策" in body
    assert "Daocaijing" not in body
    assert "DEEPFOCUS AI" not in body
    assert "The Daily Docket" not in body
    assert "Suggested Topics" not in body
    assert "Unrelated recommendation" not in body
    assert "© 2026" not in body


def test_stored_bloomberg_page_drops_translation_controls_and_audio_line():
    content = "\n".join([
        "彭博社：加拿大央行料按兵不动 贸易战令麦克勒姆面临新难题The Company & its Products ▼ | Bloomberg Terminal Demo Request | Bloomberg Anywhere Login | Customer Support",
        "Bloomberg",
        "Subscribe 订阅",
        "Economics | Central Banks 经济学 中央银行",
        "# Bank of Canada Set to Hold as Trade War Creates New Dilemma for Macklem",
        "## 由于贸易战给麦卡伦带来了新的困境，加拿大银行决定继续持有其股份。",
        "By Erik Hertzberg 作者：埃里克·赫兹特伯格",
        "September 1, 2026 at 6:30 PM GMT+8",
        "Save 保存 Translate 翻译结果",
        "Takeaways by Bloomberg AI",
        "由 Bloomberg AI 提供的要点总结",
        "0:00 / 4:12",
        "**Takeaways 外卖食品**",
        "The Bank of Canada is likely to hold borrowing costs steady as an escalation in the trade war with the US threatens the economic recovery while adding to inflation risks.",
        "加拿大银行可能会保持借贷成本不变，因为与美国的贸易战升级威胁到了经济的复苏，同时也会增加通胀风险。",
        "More From Bloomberg 更多来自彭博社的信息",
        "Home 首页 News 新闻 Market Data 市场数据",
    ])

    paragraphs = _stored_paragraphs(content, "彭博社：加拿大央行料按兵不动 贸易战令麦克勒姆面临新难题")
    body = "\n".join(paragraphs)

    assert "加拿大银行可能会保持借贷成本不变" in body
    assert "彭博社：加拿大央行料按兵不动" not in body
    assert "The Company & its Products" not in body
    assert "Save 保存" not in body
    assert "0:00 / 4:12" not in body
    assert "外卖食品" not in body
    assert "More From Bloomberg" not in body


def test_stored_bloomberg_page_does_not_start_at_last_top_read_byline():
    content = "\n".join([
        "宇树科技股价较峰值回落50%",
        "The Company & Its Products ▼ | Bloomberg Terminal Demo Request | Customer Support",
        "Bloomberg",
        "Markets 市场",
        "# Unitree Plunges 50% From Peak in Fast Reversal After Huge Debut Pop",
        "# Unitree 的股价从峰值下跌了 50%，经历了快速逆转，此前该公司取得了巨大的成功。",
        "By Bloomberg News 根据彭博新闻社的报道",
        "September 2, 2026 at 10:14 AM GMT+8",
        "Unitree Robotics shares have tumbled 50% from their intraday peak, marking one of the steepest declines for a newcomer on Shanghai's Star Board.",
        "Unitree Robotics 的股票价格从当天的峰值下跌了 50%。这是上海科创板上市的新创企业中最剧烈的跌幅之一。",
        "The stock fell as much as 4.3% to 546.51 yuan Wednesday.",
        "周三，该股票的跌幅达到了 4.3%，价格降至 546.51 元。",
        "More From Bloomberg",
        "A Top Read",
        "by Jason Gale and Max Chafkin",
        "Another Top Read",
        "by Alice French and Mari Kiyohara",
        "Home 首页 News 新闻 Market Data 市场数据",
    ])

    paragraphs = _stored_paragraphs(content, "宇树科技股价较峰值回落50%")
    body = "\n".join(paragraphs)

    assert paragraphs[0] == "# Unitree Plunges 50% From Peak in Fast Reversal After Huge Debut Pop"
    assert "Unitree Robotics shares have tumbled" in body
    assert "Jason Gale" not in body
    assert "Alice French" not in body


def test_reader_copy_roundtrip_drops_toggle_labels_related_cards_and_nav_menu():
    """Regression: text copied from the rendered reader (Amman Mineral story).

    The copied stream interleaves the reader's collapsed-original toggle label
    (「查看英文原文 · English original」) between every paragraph, then continues
    into Bloomberg's related-story cards and the full footer menu. All of that
    chrome must be dropped while the eight real story paragraphs survive.
    """
    content = "\n".join([
        "彭博社：印尼Amman Mineral据称已选定银行推进10亿美元上市",
        "稻草财经",
        "By Julia Fioretti, Manuel Baigorri, and Elffie Chew",
        "September 10, 2026 at 5:54 PM GMT+8",
        "Markets 市场/行情",
        "据称，印度尼西亚的 Amman Mineral 公司正在与多家银行接洽，准备进行价值 10 亿美元的股票上市交易。",
        "查看英文原文 · English original",
        "作者：朱莉娅·菲奥雷蒂、曼努埃尔·巴伊戈里、艾尔菲·周",
        "据知情人士透露，PT Amman Mineral Internasional 是印度尼西亚最大的铜和黄金生产商之一。该公司已选定几家银行来协助其在香港上市，预计此次融资规模至少可达 10 亿美元。",
        "查看英文原文 · English original",
        "据这些不愿公开身份的人士透露，这家已经在雅加达开展业务的公司在与中信证券和摩根士丹利合作，计划明年推出相关金融产品。",
        "查看英文原文 · English original",
        "据相关人员称，相关讨论仍在进行中，规模和时机等细节可能会有所变动。安曼矿业、CLSA 和摩根士丹利的代表均拒绝置评。",
        "查看英文原文 · English original",
        "香港一直在努力吸引中国境外的企业来上市，从而实现上市企业来源的多元化。目前，香港的上市企业大多来自中国大陆。",
        "查看英文原文 · English original",
        "Amman Mineral would follow in the footsteps of fellow miner PT Merdeka Gold Resources.",
        "阿曼矿业公司打算步同为矿业公司的 PT Merdeka Gold Resources 的后尘。PT Merdeka Gold Resources 曾在 6 月于香港进行 IPO，成功募集了 3.04 亿美元。",
        "其他希望上市的东南亚企业还包括知识产权数据提供商 Patsnap，以及印度尼西亚 MNC 集团的某个子公司。",
        "查看英文原文 · English original",
        "阿曼矿业公司于 2023 年 7 月在雅加达证券交易所上市。在 5 月份创下近三年来的最低点后，由于铜价飙升至历史高位，该公司的股价反弹了 66%。不过，全年来看，其股价仍下跌了 25%以上。",
        "查看英文原文 · English original",
        "特朗普驱逐行动的下一目标：17 万名萨尔瓦多移民",
        "查看英文原文 · English original",
        "作者：纳查·卡坦和艾丽西亚·A·考德威尔",
        "迎来新纪元，管理资产规模逼近 1000 亿美元",
        "查看英文原文 · English original",
        "作者：赫玛·帕尔马尔和凯瑟琳·伯顿",
        "欧洲软件巨头竭力在人工智能时代保持竞争力",
        "查看英文原文 · English original",
        "\"Power Trader Pay\"计划的薪酬高达 120 万美元，高于巴西石油和咖啡行业的薪资水平。",
        "查看英文原文 · English original",
        "作者：露西亚·卡萨伊、加布里埃尔·莱文和德维卡·克里希纳·库马尔",
        "查看英文原文 · English original",
        "Home 主页",
        "BTV+",
        "Market Data 市场数据",
        "Opinion 意见/看法",
        "Audio 音频",
        "News 新闻",
        "Markets 市场/行情",
        "Economics 经济学",
        "Technology 技术",
        "CityLab",
        "Sports 体育运动",
        "Economic Calendar 经济日历",
    ])

    paragraphs = _stored_paragraphs(content, "彭博社：印尼Amman Mineral据称已选定银行推进10亿美元上市")
    body = "\n".join(paragraphs)

    # The eight real story paragraphs survive.
    assert "准备进行价值 10 亿美元的股票上市交易" in body
    assert "印度尼西亚最大的铜和黄金生产商" in body
    assert "中信证券和摩根士丹利" in body
    assert "相关讨论仍在进行中" in body
    assert "实现上市企业来源的多元化" in body
    assert "成功募集了 3.04 亿美元" in body
    assert "知识产权数据提供商 Patsnap" in body
    assert "该公司的股价反弹了 66%" in body
    # Toggle labels are reader UI copy, never article text.
    assert "查看英文原文" not in body
    assert "English original" not in body
    # Related-story cards and the author credits inside them are not the story.
    assert "特朗普" not in body
    assert "迎来新纪元" not in body
    assert "欧洲软件巨头" not in body
    assert "Power Trader Pay" not in body
    assert "纳查·卡坦" not in body
    assert "赫玛·帕尔马尔" not in body
    assert "露西亚·卡萨伊" not in body
    # Bloomberg's footer menu is not the story either.
    assert "Home 主页" not in body
    assert "BTV+" not in body
    assert "Economic Calendar" not in body
