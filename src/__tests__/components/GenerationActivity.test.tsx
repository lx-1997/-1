import React from 'react';
import { act, render, screen } from '@testing-library/react';
import GenerationActivity, { appendGenerationActivity } from '../../components/GenerationActivity';

afterEach(() => { jest.useRealTimers(); });

it('advances the clock without inventing new stages or completion percentages', () => {
  jest.useFakeTimers();
  const start = Date.now();
  render(<GenerationActivity startedAt={start} events={[{ detail: '正在获取研报原文', at: start }]} />);
  act(() => { jest.advanceTimersByTime(45000); });
  expect(screen.getByText('已用 45 秒')).toBeInTheDocument();
  expect(screen.getByText(/距上次进度更新 45 秒/)).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(1);
  expect(screen.queryByText(/核对风险|生成结论|94%/)).not.toBeInTheDocument();
});

it('keeps the real source receipt and progress visible while the preview is readable', () => {
  const start = Date.now();
  const events = appendGenerationActivity([{ detail: '正在获取原文', at: start }], '原文已获取 · 24 页 · 文本层 83000 字符', start + 1000);
  const view = render(<GenerationActivity startedAt={start} events={events} />);
  view.rerender(<GenerationActivity startedAt={start} events={appendGenerationActivity(events, '速览完成 · 深度解读继续生成中', start + 2000)} previewReady />);
  expect(screen.getByText('原文已获取 · 24 页 · 文本层 83000 字符')).toBeInTheDocument();
  expect(screen.getByText('速览已就绪 · 全文继续生成')).toBeInTheDocument();
  expect(screen.getAllByRole('listitem')).toHaveLength(3);
});

it('deduplicates heartbeats and bounds the retained event list', () => {
  const events = [{ detail: '等待模型返回', at: 1000 }];
  expect(appendGenerationActivity(events, '等待模型返回', 2000)).toBe(events);
  let many = events;
  for (let i = 0; i < 20; i++) many = appendGenerationActivity(many, `真实阶段 ${i}`, 3000 + i);
  expect(many).toHaveLength(12);
});
