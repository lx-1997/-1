import React from 'react';
import { fireEvent, render } from '@testing-library/react';
import ArticleOriginalReader from '../../components/ArticleOriginalReader';

describe('ArticleOriginalReader', () => {
  it('renders the article as a readable Chinese-first layout', () => {
    const { getByText, queryByText, getAllByText } = render(
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

    expect(getByText('华为技术有限公司在应对内存成本上涨时，利润出现明显下降。')).toBeInTheDocument();
    expect(getByText('AI TAKEAWAYS')).toBeInTheDocument();
    expect(queryByText('More From Bloomberg 更多来自彭博社的信息')).not.toBeInTheDocument();
    expect(queryByText('Home 首页 News 新闻 Market Data 市场数据')).not.toBeInTheDocument();
    expect(getAllByText(/查看英文原文/)).toHaveLength(2);

    fireEvent.click(getAllByText(/查看英文原文/)[0]);
    expect(getByText('Huawei Technologies Co. posted a deep profit decline due to rising memory costs.')).toBeVisible();
  });
});
