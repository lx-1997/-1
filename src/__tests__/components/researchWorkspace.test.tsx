import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import ResearchWorkspace from '../../components/ResearchWorkspace';
import { apiGet, apiPost } from '../../services/apiClient';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn(), apiPost: jest.fn() }));
const get = apiGet as jest.Mock;
const post = apiPost as jest.Mock;
const account = (id: string) => ({ id, username: id, email: null, role: 'viewer' as const, is_active: true, created_at: '', membership: null });
const project = { id: 'private-one', title: '私人谷歌研究', symbol: 'GOOGL', market: 'US', objective: '研究盈利与估值', notes: '私人笔记', hypotheses: ['待验证假设'], interval_seconds: 0, version: 1, next_due: 0,
  runs: [], revisions: [{ id: 'rev-one', created: 1700000000, config_version: 1, payload: { report: { analysis_status: 'generated', summary: '测试有依据的研究解读' }, evidence: { get_valuation: { status: 'limited', currency: 'USD', source: 'https://example.test/source', fields: { market_cap: { status: 'limited', value: 123 }, pe_ratio: { status: 'missing', value: null } } } } } }] };
beforeEach(() => {
  jest.clearAllMocks();
  invalidateAuthSession();
  authenticateSession(account('alice'), 'alice-test-token', 'https://example.test');
  get.mockImplementation((path: string) => Promise.resolve(path.endsWith('/projects') ? { projects: [project] } : project));
  post.mockResolvedValue(project);
});
afterEach(() => { act(() => invalidateAuthSession()); });

it('shows the real field coverage, keeps missing PE empty and links evidence', async () => {
  render(<ResearchWorkspace />);
  expect(await screen.findByText('测试有依据的研究解读')).toBeVisible();
  expect(screen.getByText('123')).toBeVisible();
  expect(screen.getByText('—')).toBeVisible();
  expect(screen.getByRole('link', { name: '查看数据来源 ↗' })).toHaveAttribute('href', 'https://example.test/source');
  expect(screen.getByText(/结构检查通过不代表独立原文审计/)).toBeVisible();
});

it('creates a project with the explicit market and objective', async () => {
  render(<ResearchWorkspace />);
  await screen.findByText('测试有依据的研究解读');
  fireEvent.click(screen.getByRole('button', { name: '＋ 新建研究' }));
  fireEvent.change(screen.getByLabelText('研究名称'), { target: { value: '新的研究' } });
  fireEvent.change(screen.getByLabelText('股票代码'), { target: { value: 'aapl' } });
  fireEvent.change(screen.getByLabelText('市场'), { target: { value: 'US' } });
  fireEvent.change(screen.getByLabelText('研究问题'), { target: { value: '研究现金流变化' } });
  fireEvent.click(screen.getByRole('button', { name: '创建私有研究' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/research/projects', { title: '新的研究', symbol: 'AAPL', market: 'US', objective: '研究现金流变化' }));
});

it('saves notes as opinions and allows stopping automatic tracking', async () => {
  render(<ResearchWorkspace />);
  await screen.findByText('测试有依据的研究解读');
  fireEvent.change(screen.getByLabelText('个人笔记（观点，不作为已核实事实）'), { target: { value: '新的个人观点' } });
  fireEvent.change(screen.getByLabelText('自动跟踪频率'), { target: { value: '0' } });
  fireEvent.click(screen.getByRole('button', { name: '保存研究设置' }));
  await waitFor(() => expect(post).toHaveBeenCalledWith('/api/research/projects/private-one/settings', { expected_version: 1, notes: '新的个人观点', hypotheses: ['待验证假设'], interval_seconds: 0 }));
});

it('does not redisplay the old account project after a late response', async () => {
  let resolve: (value: any) => void = () => {};
  get.mockImplementation((path: string) => path.endsWith('/projects') ? Promise.resolve({ projects: [project] }) : new Promise(done => { resolve = done; }));
  render(<ResearchWorkspace />);
  await screen.findByText('私人谷歌研究');
  act(() => authenticateSession(account('bob'), 'bob-test-token', 'https://example.test'));
  get.mockResolvedValue({ projects: [] });
  await act(async () => { resolve(project); await Promise.resolve(); });
  expect(screen.queryByText('私人笔记')).not.toBeInTheDocument();
  expect(screen.queryByText('测试有依据的研究解读')).not.toBeInTheDocument();
});

it('shows an authenticated empty state after logout', async () => {
  render(<ResearchWorkspace />);
  await screen.findByText('测试有依据的研究解读');
  act(() => invalidateAuthSession());
  expect(screen.getByText('登录后保存私有研究、笔记和跟踪设置。')).toBeVisible();
  expect(screen.queryByText('私人谷歌研究')).not.toBeInTheDocument();
});
