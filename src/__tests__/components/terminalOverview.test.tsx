import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import TerminalTodayStories from '../../components/TerminalTodayStories';
import TerminalAiFundCard from '../../components/TerminalAiFundCard';
import { getMyStories, getTodayStories } from '../../services/storyService';
import { apiGet } from '../../services/apiClient';

jest.mock('../../services/storyService', () => ({ getTodayStories: jest.fn(), getMyStories: jest.fn(), logStoryEvent: jest.fn(() => Promise.resolve()) }));
jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn() }));
const today = getTodayStories as jest.Mock;
const mine = getMyStories as jest.Mock;
const props = { loggedIn: false, onSelectSymbol: jest.fn(), onOpenMarket: jest.fn(), onRequireLogin: jest.fn() };
const brief = (headline: string) => ({ brief: { headline, sections: [], stats: { stories: 2 } }, health: null });
beforeEach(() => {
  jest.clearAllMocks(); localStorage.clear();
  window.matchMedia = jest.fn().mockReturnValue({ matches: false, addEventListener: jest.fn(), removeEventListener: jest.fn() });
  today.mockResolvedValue(brief('科技事件更新'));
  mine.mockResolvedValue({ stories: [], personalized: false });
});
test('event details are collapsed and can be reached with the disclosure button', async () => {
  render(<TerminalTodayStories {...props} />);
  await screen.findByText('科技事件更新');
  expect(screen.getByRole('button', { name: '查看事件' })).toHaveAttribute('aria-expanded', 'false');
  expect(screen.queryByRole('button', { name: '刷新事件' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看事件' }));
  expect(screen.getByRole('button', { name: '收起' })).toHaveAttribute('aria-expanded', 'true');
  expect(screen.getByRole('button', { name: '刷新事件' })).toBeVisible();
});
test('a failed request reports failure and supports retry', async () => {
  today.mockRejectedValueOnce(new Error('offline'));
  render(<TerminalTodayStories {...props} />);
  await screen.findByText('事件摘要暂未更新，可重试');
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  await screen.findByText('科技事件更新');
  expect(screen.queryByRole('button', { name: '重试' })).not.toBeInTheDocument();
});
test('an older account request cannot overwrite the new account view', async () => {
  let resolveOld!: (value: unknown) => void;
  today.mockImplementationOnce(() => new Promise(resolve => { resolveOld = resolve; }));
  const { rerender } = render(<TerminalTodayStories {...props} />);
  rerender(<TerminalTodayStories {...props} loggedIn />);
  await screen.findByText('科技事件更新');
  await act(async () => { resolveOld(brief('过期账号的结果')); });
  expect(screen.queryByText('过期账号的结果')).not.toBeInTheDocument();
});
test('simulation stays usable when storage is unavailable and labels virtual performance', async () => {
  (apiGet as jest.Mock).mockResolvedValue({ strategies: [{ fund_id: 'main', name: '样本策略', rank: 1, nav_pct: 1, position_count: 0 }], benchmark: { nav_pct: 0 }, in_session: true });
  const read = jest.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('disabled'); });
  const write = jest.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('disabled'); });
  render(<TerminalAiFundCard />);
  const button = await screen.findByRole('button', { expanded: false });
  fireEvent.click(button);
  await waitFor(() => expect(screen.getByText('AI 策略模拟盘')).toBeVisible());
  expect(screen.getByText('虚拟资金模拟交易，策略表现仅供研究。')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '收起' }));
  expect(screen.queryByText('AI 策略模拟盘')).not.toBeInTheDocument();
  read.mockRestore(); write.mockRestore();
});
