import React from 'react';
import { act, fireEvent, render, screen } from '@testing-library/react';
import PlatformAssistantActions from '../../components/PlatformAssistantActions';
import { apiPost } from '../../services/apiClient';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import { PLATFORM_RESULT_EVENT, publishPlatformResult, registerPlatformNavigator } from '../../utils/platformAssistant';
import type { PlatformAction } from '../../utils/platformAssistant';

jest.mock('../../services/apiClient', () => ({ apiPost: jest.fn() }));
const post = apiPost as jest.Mock;
const user = (id: string) => ({ id, username: id, email: null, role: 'viewer' as const, is_active: true, created_at: '' });
const proposed: PlatformAction = { id: 'reviewed-action', title: '加入自选股', capability_id: 'watchlist.update', method: 'POST', path: '/api/me/watchlist', arguments: { symbols: ['NVDA'] }, status: 'needs_confirmation', confirmation_token: 'opaque-proposal' };
beforeEach(() => { invalidateAuthSession(); authenticateSession(user('A'), 'token-a', 'https://example.test'); post.mockReset(); });
afterEach(() => { act(() => invalidateAuthSession()); });

it('reviews a proposed operation and submits only after an explicit click, once', async () => {
  render(<PlatformAssistantActions />);
  act(() => publishPlatformResult({ platform_actions: [proposed] }, getAuthSnapshot().revision, 'preview'));
  expect(screen.getByText('待确认')).toBeInTheDocument();
  expect(screen.getByText(/NVDA/)).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
  post.mockResolvedValue({ platform_actions: [{ ...proposed, status: 'completed', result: { symbols: ['NVDA'] } }] });
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认执行' })); });
  expect(post).toHaveBeenCalledTimes(1);
  expect(post).toHaveBeenCalledWith('/api/agents/platform-actions/confirm', { confirmation_token: 'opaque-proposal' });
  expect(screen.getByText('已完成')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '确认执行' })).not.toBeInTheDocument();
});

it('clears receipts on account change and ignores the previous account late result', () => {
  render(<PlatformAssistantActions />);
  const revision = getAuthSnapshot().revision;
  act(() => publishPlatformResult({ platform_actions: [proposed] }, revision, 'account-a'));
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  act(() => publishPlatformResult({ platform_actions: [proposed] }, revision, 'late-a'));
  expect(screen.queryByText('加入自选股')).not.toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

it('does not replay a confirmation after an uncertain network failure', async () => {
  render(<PlatformAssistantActions />);
  act(() => publishPlatformResult({ platform_actions: [proposed] }, getAuthSnapshot().revision, 'network'));
  post.mockRejectedValue(new Error('网络中断，请查询操作状态'));
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '确认执行' })); });
  expect(screen.getByRole('button', { name: '已提交，请查询结果' })).toBeDisabled();
  expect(post).toHaveBeenCalledTimes(1);
});

it('presents labeled operation fields without HTTP wrappers or route details', () => {
  render(<PlatformAssistantActions />);
  const action: PlatformAction = { ...proposed, arguments: {
    body: { symbol: 'NVDA', quantity: 10, price: 150, objective: '检查估值', names: { NVDA: '英伟达' } },
    path_params: { position_id: 'position-42' }, query: { enabled: true },
  } };
  act(() => publishPlatformResult({ platform_actions: [action] }, getAuthSnapshot().revision, 'readable-fields'));
  expect(screen.getByText('股票代码').tagName).toBe('DT');
  expect(screen.getByText('NVDA').tagName).toBe('DD');
  expect(screen.getByText('数量')).toBeInTheDocument();
  expect(screen.getByText('价格')).toBeInTheDocument();
  expect(screen.getByText('研究目标')).toBeInTheDocument();
  expect(screen.getByText('NVDA：英伟达')).toBeInTheDocument();
  expect(screen.getByText('持仓编号')).toBeInTheDocument();
  expect(screen.getByText('是')).toBeInTheDocument();
  expect(screen.queryByText(/path_params|body|\/api\/me\/watchlist|watchlist\.update/)).not.toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});

it('deduplicates receipts and rejects unknown navigation destinations', () => {
  const listener = jest.fn();
  const navigate = jest.fn();
  const unregister = registerPlatformNavigator(navigate);
  window.addEventListener(PLATFORM_RESULT_EVENT, listener);
  const revision = getAuthSnapshot().revision;
  publishPlatformResult({ platform_actions: [proposed] }, revision, 'same-turn');
  publishPlatformResult({ platform_actions: [proposed] }, revision, 'same-turn');
  publishPlatformResult({ navigation: { view: 'https://evil.example' } }, revision, 'unknown-view');
  expect(listener).toHaveBeenCalledTimes(1);
  expect(navigate).not.toHaveBeenCalled();
  publishPlatformResult({ navigation: { view: 'position-monitor' } }, revision, 'valid-view');
  expect(navigate).toHaveBeenCalledTimes(1);
  unregister(); window.removeEventListener(PLATFORM_RESULT_EVENT, listener);
});
