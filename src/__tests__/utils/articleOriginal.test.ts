import { parseArticleOriginal } from '../../utils/articleOriginal';

describe('article original reader parser', () => {
  it('turns a bilingual Bloomberg page into Chinese-first paragraphs and collapsed originals', () => {
    const doc = parseArticleOriginal({
      title: '彭博社：内存短缺冲击持续 华为利润降幅扩大',
      paragraphs: [
        '彭博社：内存短缺冲击持续 华为利润降幅扩大',
        'Technology | Asia 技术亚洲',
        '# Huawei Profit Decline Widens After Memory Crunch Takes Toll',
        '# 在内存短缺问题的影响下，华为的利润下滑幅度进一步扩大。',
        'By Bloomberg News 根据彭博新闻社的报道',
        'August 31, 2026 at 5:38 PM GMT+8',
        '💬 Takeaways by Bloomberg AI',
        '由 Bloomberg AI 提供的要点总结',
        'Huawei Technologies Co. posted a deep profit decline due to rising memory costs.',
        '华为技术有限公司由于内存成本上升，导致利润大幅下滑。',
        'Revenue rose while research spending increased.',
        '收入上升的同时，研发支出也增加了。',
        'The company unveiled a pathway to overcome sanctions.',
        '该公司公布了克服制裁的技术方案。',
        'Huawei Technologies Co. posted a deep profit decline after the Chinese tech champion grappled with rising memory costs.',
        '华为技术有限公司在应对内存成本上涨时，利润出现明显下降。',
        'More From Bloomberg 更多来自彭博社的信息',
        'Home 首页 News 新闻 Market Data 市场数据',
      ],
    });

    expect(doc.kicker).toBe('Technology | Asia 技术亚洲');
    expect(doc.englishTitle).toContain('Huawei Profit Decline');
    expect(doc.takeaways).toHaveLength(3);
    expect(doc.takeaways[0].text).toContain('内存成本');
    expect(doc.takeaways[0].original).toContain('memory costs');
    expect(doc.sections).toHaveLength(1);
    expect(doc.sections[0].paragraphs).toHaveLength(1);
    expect(doc.sections[0].paragraphs[0].text).toContain('利润出现明显下降');
    expect(doc.sections[0].paragraphs[0].original).toContain('grappled');
    expect(doc.filteredLines).toBeGreaterThan(0);
  });

  it('keeps a normal monolingual article readable without inventing sections', () => {
    const doc = parseArticleOriginal({
      title: '新能源行业观察',
      paragraphs: ['新能源行业观察', '作者：研究组', '第一段正文，介绍需求变化。', '第二段正文，介绍供给变化。'],
    });

    expect(doc.sections).toHaveLength(1);
    expect(doc.sections[0].paragraphs.map(item => item.text)).toEqual(['第一段正文，介绍需求变化。', '第二段正文，介绍供给变化。']);
    expect(doc.byline).toBe('作者：研究组');
  });

  it('turns stored HTML fragments into clean readable paragraphs', () => {
    const doc = parseArticleOriginal({
      title: 'HTML 文章',
      content: '<p style="font-size:17px">第一段正文。</p><p>第二段正文。</p>',
    });

    const body = doc.sections.flatMap(section => section.paragraphs).map(paragraph => paragraph.text);
    expect(body).toEqual(['第一段正文。', '第二段正文。']);
    expect(body.join(' ')).not.toContain('<p');
  });

  it('removes single-line Reuters chrome and stops at related content', () => {
    const doc = parseArticleOriginal({
      title: 'Anthropic 云服务协议',
      paragraphs: [
        'Exclusive news, data and analytics for financial market professionals',
        'LSEG', 'Reuters', 'My News', 'Sign In', 'Subscribe',
        'Anthropic has signed a cloud-computing deal worth $35 billion with Lambda.',
        'The deal will bring online Nvidia capacity to meet growing demand for Claude AI.',
        'Suggested Topics: Technology',
        'Read Next', 'Unrelated recommendation.', 'Latest', 'Home',
        '© 2026 Reuters. All rights reserved',
      ],
    });

    const body = doc.sections.flatMap(section => section.paragraphs).map(paragraph => paragraph.text).join('\n');
    expect(body).toContain('Anthropic has signed');
    expect(body).toContain('Nvidia capacity');
    expect(body).not.toContain('My News');
    expect(body).not.toContain('Unrelated recommendation');
    expect(body).not.toContain('© 2026');
  });

  it('extracts the opened Shein article from a DAO财经 full-page capture', () => {
    const doc = parseArticleOriginal({
      title: '路透社：快时尚巨头希音计划在香港上市。',
      paragraphs: [
        'Daocaijing 金融终端 A股每日复盘 热门个股多维证据速判 财经资讯',
        '稻财经', 'DEEPFOCUS AI', '主菜单', '深度文章', '★ 头条',
        '路透社：快时尚巨头希音计划在香港上市。',
        'Fast-fashion giant Shein set to open flat in Hong Kong market debut',
        '快时尚巨头施恩计划在香港市场首次开设实体店',
        'By Reuters 由路透社报道',
        'September 1, 2026 9:26 AM GMT+8 • Updated 9 mins ago',
        'HONG KONG, Sept 1 (Reuters) - Shares in fast fashion giant Shein were set open flat in their Hong Kong market debut on Tuesday.',
        '香港，9 月 1 日（路透社）——快时尚巨头 Shein 集团在周二于香港市场的首次公开募股中表现平平。',
        'Known globally for selling $5 tops and $10 dresses, Shein has been humbled by tariff and duty changes in the U.S. and Europe.',
        'Shein 以售价仅 5 美元或 10 美元的商品而闻名全球。然而，美国和欧洲关税及税收政策的变化影响了其业务。',
        'Learn about latest legal news delivered straight to your inbox from The Daily Docket newsletter. Sign up here.',
        'Our Standards: The Thomson Reuters Trust Principles.',
        'Suggested Topics: Deals Capital Markets',
        "Read Next / Editor's Picks", 'Unrelated recommendation.',
        'About Reuters', '© 2026 Reuters. All rights reserved',
      ],
    });

    const body = doc.sections.flatMap(section => section.paragraphs).map(paragraph => paragraph.text).join('\n');
    expect(body).toContain('香港市场的首次公开募股');
    expect(body).toContain('关税及税收政策');
    expect(body).not.toContain('Daocaijing');
    expect(body).not.toContain('DEEPFOCUS AI');
    expect(body).not.toContain('The Daily Docket');
    expect(body).not.toContain('Suggested Topics');
    expect(body).not.toContain('Unrelated recommendation');
    expect(body).not.toContain('© 2026');
  });

  it('formats a Bloomberg full-page capture without audio and publisher chrome', () => {
    const doc = parseArticleOriginal({
      title: '彭博社：加拿大央行料按兵不动 贸易战令麦克勒姆面临新难题',
      paragraphs: [
        '彭博社：加拿大央行料按兵不动 贸易战令麦克勒姆面临新难题The Company & its Products ▼ | Bloomberg Terminal Demo Request | Bloomberg Anywhere Login | Customer Support',
        'Bloomberg',
        'Subscribe 订阅',
        'Economics | Central Banks 经济学 中央银行',
        '# Bank of Canada Set to Hold as Trade War Creates New Dilemma for Macklem',
        '## 由于贸易战给麦卡伦带来了新的困境，加拿大银行决定继续持有其股份。',
        'By Erik Hertzberg 作者：埃里克·赫兹特伯格',
        'September 1, 2026 at 6:30 PM GMT+8',
        'Save 保存 Translate 翻译结果',
        'Takeaways by Bloomberg AI',
        '由 Bloomberg AI 提供的要点总结',
        '0:00 / 4:12',
        '**Takeaways 外卖食品**',
        'The Bank of Canada is likely to hold borrowing costs steady as an escalation in the trade war with the US threatens the economic recovery while adding to inflation risks.',
        '加拿大银行可能会保持借贷成本不变，因为与美国的贸易战升级威胁到了经济的复苏，同时也会增加通胀风险。',
        'Economists expect policymakers to keep the policy rate at 2.25% on Wednesday.',
        '经济学家预计，政策制定者在周三会将政策利率维持在 2.25%。',
        'The trade war and retaliatory tariffs are expected to add to inflation while weighing on growth.',
        '贸易战和报复性关税预计会加剧通货膨胀，并对经济增长产生负面影响。',
        'The Bank of Canada is likely to hold borrowing costs steady after the Chinese tech champion grappled with rising costs.',
        '加拿大银行可能会保持借贷成本不变，在应对成本上涨时，利润出现明显下降。',
        '**Retaliation Is Inflationary**',
        '**报复行为会引发通货膨胀**',
        'Previous research from the central bank suggests retaliatory tariffs will hit Canadian consumers with higher prices.',
        '中央银行之前的研究表明，报复性关税将导致加拿大消费者的物价上涨。',
        'More From Bloomberg 更多来自彭博社的信息',
        'Home 首页 News 新闻 Market Data 市场数据',
      ],
    });

    const body = doc.sections.flatMap(section => section.paragraphs).map(paragraph => paragraph.text).join('\n');
    expect(doc.englishTitle).toContain('Bank of Canada Set to Hold');
    expect(doc.takeaways).toHaveLength(3);
    expect(body).toContain('加拿大银行可能会保持借贷成本不变');
    expect(doc.sections.some(section => section.heading === 'Retaliation Is Inflationary')).toBe(true);
    expect(body).not.toContain('0:00 / 4:12');
    expect(body).not.toContain('外卖食品');
    expect(body).not.toContain('Save 保存');
    expect(body).not.toContain('Bloomberg');
  });

  it('anchors a full-page article to its own byline, not a Top Reads byline', () => {
    const doc = parseArticleOriginal({
      title: '宇树科技股价较峰值回落50%',
      paragraphs: [
        '宇树科技股价较峰值回落50%',
        'The Company & Its Products ▼ | Bloomberg Terminal Demo Request | Customer Support',
        'Bloomberg', 'Markets 市场',
        '# Unitree Plunges 50% From Peak in Fast Reversal After Huge Debut Pop',
        '# Unitree 的股价从峰值下跌了 50%，经历了快速逆转，此前该公司取得了巨大的成功。',
        'By Bloomberg News 根据彭博新闻社的报道',
        'September 2, 2026 at 10:14 AM GMT+8',
        'Unitree Robotics shares have tumbled 50% from their intraday peak, marking one of the steepest declines for a newcomer on Shanghai\'s Star Board.',
        'Unitree Robotics 的股票价格从当天的峰值下跌了 50%。这是上海科创板上市的新创企业中最剧烈的跌幅之一。',
        'More From Bloomberg', 'A Top Read', 'by Jason Gale and Max Chafkin',
      ],
    });
    const body = doc.sections.flatMap(section => section.paragraphs).map(paragraph => paragraph.text).join('\n');
    expect(body).toContain('上海科创板上市');
    expect(body).not.toContain('Jason Gale');
  });

  it('drops reader toggle labels, related cards and the Bloomberg footer menu (Amman roundtrip)', () => {
    const doc = parseArticleOriginal({
      title: '彭博社：印尼Amman Mineral据称已选定银行推进10亿美元上市',
      paragraphs: [
        '彭博社：印尼Amman Mineral据称已选定银行推进10亿美元上市',
        '稻草财经',
        'By Julia Fioretti, Manuel Baigorri, and Elffie Chew',
        'September 10, 2026 at 5:54 PM GMT+8',
        'Markets 市场/行情',
        '据称，印度尼西亚的 Amman Mineral 公司正在与多家银行接洽，准备进行价值 10 亿美元的股票上市交易。',
        '查看英文原文 · English original',
        '作者：朱莉娅·菲奥雷蒂、曼努埃尔·巴伊戈里、艾尔菲·周',
        '据知情人士透露，PT Amman Mineral Internasional 是印度尼西亚最大的铜和黄金生产商之一。该公司已选定几家银行来协助其在香港上市，预计此次融资规模至少可达 10 亿美元。',
        '查看英文原文 · English original',
        '据这些不愿公开身份的人士透露，这家已经在雅加达开展业务的公司在与中信证券和摩根士丹利合作，计划明年推出相关金融产品。',
        '查看英文原文 · English original',
        '据相关人员称，相关讨论仍在进行中，规模和时机等细节可能会有所变动。安曼矿业、CLSA 和摩根士丹利的代表均拒绝置评。',
        '查看英文原文 · English original',
        '香港一直在努力吸引中国境外的企业来上市，从而实现上市企业来源的多元化。目前，香港的上市企业大多来自中国大陆。',
        '查看英文原文 · English original',
        'Amman Mineral would follow in the footsteps of fellow miner PT Merdeka Gold Resources, which raised $304 million in Hong Kong in June.',
        '阿曼矿业公司打算步同为矿业公司的 PT Merdeka Gold Resources 的后尘。PT Merdeka Gold Resources 曾在 6 月于香港进行 IPO，成功募集了 3.04 亿美元。',
        '其他希望上市的东南亚企业还包括知识产权数据提供商 Patsnap，以及印度尼西亚 MNC 集团的某个子公司。',
        '查看英文原文 · English original',
        '阿曼矿业公司于 2023 年 7 月在雅加达证券交易所上市。在 5 月份创下近三年来的最低点后，由于铜价飙升至历史高位，该公司的股价反弹了 66%。不过，全年来看，其股价仍下跌了 25%以上。',
        '查看英文原文 · English original',
        '特朗普驱逐行动的下一目标：17 万名萨尔瓦多移民',
        '查看英文原文 · English original',
        '作者：纳查·卡坦和艾丽西亚·A·考德威尔',
        '迎来新纪元，管理资产规模逼近 1000 亿美元',
        '查看英文原文 · English original',
        '作者：赫玛·帕尔马尔和凯瑟琳·伯顿',
        '欧洲软件巨头竭力在人工智能时代保持竞争力',
        '查看英文原文 · English original',
        '"Power Trader Pay"计划的薪酬高达 120 万美元，高于巴西石油和咖啡行业的薪资水平。',
        '查看英文原文 · English original',
        '作者：露西亚·卡萨伊、加布里埃尔·莱文和德维卡·克里希纳·库马尔',
        '查看英文原文 · English original',
        'Home 主页', 'BTV+', 'Market Data 市场数据', 'Opinion 意见/看法', 'Audio 音频',
        'News 新闻', 'Markets 市场/行情', 'Economics 经济学', 'Technology 技术',
        'CityLab', 'Sports 体育运动', 'Economic Calendar 经济日历',
      ],
    });

    const body = doc.sections.flatMap(section => section.paragraphs);
    const text = body.map(paragraph => paragraph.text).join('\n');
    expect(body.length).toBeGreaterThanOrEqual(8);
    expect(text).toContain('准备进行价值 10 亿美元的股票上市交易');
    expect(text).toContain('相关讨论仍在进行中');
    expect(text).toContain('成功募集了 3.04 亿美元');
    expect(text).toContain('该公司的股价反弹了 66%');
    expect(text).not.toContain('查看英文原文');
    expect(text).not.toContain('English original');
    expect(body.map(p => p.original).filter(Boolean)).not.toContain('查看英文原文 · English original');
    expect(text).not.toContain('特朗普');
    expect(text).not.toContain('迎来新纪元');
    expect(text).not.toContain('欧洲软件巨头');
    expect(text).not.toContain('Power Trader Pay');
    expect(text).not.toContain('纳查·卡坦');
    expect(text).not.toContain('露西亚·卡萨伊');
    expect(text).not.toContain('Home 主页');
    expect(text).not.toContain('BTV+');
    expect(text).not.toContain('Economic Calendar');
    expect(doc.byline).toContain('Julia Fioretti');
    expect(doc.publishedAt).toContain('September 10, 2026');
  });
});
