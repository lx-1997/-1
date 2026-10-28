import React from 'react';
import { render, fireEvent } from '@testing-library/react';
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

    // 来源要点放在正文之前，正文的独有信息仍完整保留。
    expect(getByText('华为技术有限公司在应对内存成本上涨时，利润出现明显下降。')).toBeInTheDocument();
    expect(getByText('先看要点')).toBeInTheDocument();
    expect(getByText('上游 AI 摘要')).toBeInTheDocument();
    expect(getByText('华为技术有限公司由于内存成本上升，导致利润大幅下滑。')).toBeInTheDocument();
    // 不把上游标题和网站导航当成正文。
    expect(queryByText(/takeaways/i)).not.toBeInTheDocument();
    expect(queryByText('More From Bloomberg 更多来自彭博社的信息')).not.toBeInTheDocument();
    expect(queryByText('Home 首页 News 新闻 Market Data 市场数据')).not.toBeInTheDocument();
    // 段落下方不再挂「查看英文原文」折叠条：英文原文不渲染，由中英配对逻辑静默吸收。
    expect(queryByText(/查看英文原文/i)).not.toBeInTheDocument();
    expect(queryByText('Huawei Technologies Co. posted a deep profit decline after the Chinese tech champion grappled with rising memory costs.')).not.toBeInTheDocument();
  });
});

it('does not invent a summary and keeps original files accessible below extracted text', () => {
  const ask = jest.fn();
  const { container, getByText, queryByText } = render(<ArticleOriginalReader title="普通文章" content="这是已经提取的正文，保留原文信息供读者核对。" sourceImageUrl="https://example.test/original.png" onAsk={ask} />);
  expect(queryByText('先看要点')).not.toBeInTheDocument();
  const original = container.querySelector('details')!;
  expect(original.open).toBe(false);
  fireEvent.click(getByText('围绕这篇问 AI'));
  expect(ask).toHaveBeenCalledTimes(1);
  expect(container.querySelector('img')).toHaveAttribute('src', 'https://example.test/original.png');
});
it('opens the original immediately when readable extracted text is not available', () => {
  const { container } = render(<ArticleOriginalReader title="原件" sourceImageUrl="https://example.test/original.pdf" extracting />);
  expect(container.querySelector('details')!.open).toBe(true);
  expect(container.querySelector('object')).toHaveAttribute('data', 'https://example.test/original.pdf');
});

it('keeps extraction coverage and ingestion time explicit', () => {
  const { getByText } = render(<ArticleOriginalReader title="部分正文" content="这是已提取的前段正文，后续段落仍以上游原件为准。" createdAt="2026-10-28T01:00:00Z" truncated sourceImageUrl="https://example.test/original.png" />);
  expect(getByText(/可展开原始文件核对完整内容/)).toBeInTheDocument();
  expect(getByText(/收录于 2026-10-28/)).toBeInTheDocument();
});
