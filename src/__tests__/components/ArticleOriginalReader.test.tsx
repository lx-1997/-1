import React from 'react';
import { render } from '@testing-library/react';
import ArticleOriginalReader from '../../components/ArticleOriginalReader';

describe('ArticleOriginalReader', () => {
  it('renders the article as a readable Chinese-first layout', () => {
    const { getByText, queryByText } = render(
      <ArticleOriginalReader
        title="彭博社：内存短缺冲击持续 华为利润降幅扩大"
        sourceName="Bloomberg"
        paragraphs={[
          '彭博社：内存短缺冲击持续 华为利润降幅扩大',
          'Technology | Asia 技术亚洲',
          '# Huawei Profit Decline Widens After Memory Crunch Takes Toll',
          'By Bloomberg News 根据彭博新闻社的报道',
          '💬 Takeaways by Bloomberg AI',
          '华为技术有限公司由于内存成本上升，导致利润大幅下滑。',
          'Huawei Technologies Co. posted a deep profit decline due to rising memory costs.',
          '华为技术有限公司在应对内存成本上涨时，利润出现明显下降。',
          'Huawei Technologies Co. posted a deep profit decline after the Chinese tech champion grappled with rising memory costs.',
          'More From Bloomberg 更多来自彭博社的信息',
          'Home 首页 News 新闻 Market Data 市场数据',
        ]}
      />
    );

    // 要点块与正文首段是同主语改写复述：要点被吸收（不渲染），正文首段必须保留在正文里。
    expect(getByText('华为技术有限公司在应对内存成本上涨时，利润出现明显下降。')).toBeInTheDocument();
    // AI TAKEAWAYS 摘要块已移除：takeaways 仅用于判断有无正文，不再渲染。
    expect(queryByText(/takeaways/i)).not.toBeInTheDocument();
    expect(queryByText('More From Bloomberg 更多来自彭博社的信息')).not.toBeInTheDocument();
    expect(queryByText('Home 首页 News 新闻 Market Data 市场数据')).not.toBeInTheDocument();
    // 段落下方不再挂「查看英文原文」折叠条：英文原文不渲染，由中英配对逻辑静默吸收。
    expect(queryByText(/查看英文原文/i)).not.toBeInTheDocument();
    expect(queryByText('Huawei Technologies Co. posted a deep profit decline after the Chinese tech champion grappled with rising memory costs.')).not.toBeInTheDocument();
  });
});
