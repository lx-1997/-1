import React from 'react';
import { render, screen } from '@testing-library/react';
import NewsSourcePreview from '../../components/NewsSourcePreview';
it('shows existing source takeaways immediately without calling them completed AI analysis', () => {
  render(<NewsSourcePreview title="订单观察" content={'Takeaways by Source AI\nThe company received new orders, pending acceptance.\n公司取得新增订单，仍待验收与确认收入。\n公司取得新增订单，仍待验收与确认收入；正文说明了交付安排。'} />);
  expect(screen.getByRole('heading', { name: '先看原文要点' })).toBeInTheDocument();
  expect(screen.getByText('以下是来源资料，AI 解读仍在生成。')).toBeInTheDocument();
  expect(screen.getByText('展开已收录正文')).toBeInTheDocument();
});
it('uses a labelled excerpt rather than inventing points for a source without a summary', () => {
  const { container } = render(<NewsSourcePreview title="测试新闻" content="这是一段来源正文，包含实际披露的事件及不确定性。" />);
  expect(screen.getByRole('heading', { name: '先看原文摘录' })).toBeInTheDocument();
  expect(container.querySelector('details')?.open).toBe(false);
});
