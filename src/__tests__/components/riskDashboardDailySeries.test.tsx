import React from 'react';
import { App } from 'antd';
import { fireEvent, render, screen, within } from '@testing-library/react';
import RiskDashboard from '../../components/RiskDashboard';
import { apiGet } from '../../services/apiClient';
import type { PositionRecord, RiskSummaryResponse, VaRResult } from '../../services/riskService';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn(), apiPost: jest.fn(), apiPut: jest.fn(), apiDelete: jest.fn() }));
jest.setTimeout(20000);
const get = apiGet as jest.Mock;
const unavailable: VaRResult = { historical_var: null, parametric_var: null, var_amount: null, var_pct: null, confidence: .95, observations: 0, mean_return: null, std_dev: null, sharpe_ratio: null, method: 'daily_series_unavailable', data_available: false, reason: '平仓交易收益不能当日收益' };
const position: PositionRecord = { id: 'position-test', symbol: 'TEST', name: '虚构持仓', market: 'US', asset_class: 'stock', direction: 'long', entry_price: 100, entry_date: '2026-01-01', quantity: 2, current_price: 110, stop_loss: null, take_profit: null, position_size_pct: 100, sector: '测试行业', strategy: '', notes: '', tags: [], greeks: {}, created_at: '', updated_at: '', closed_at: null, status: 'open', unrealized_pnl: 20, unrealized_pnl_pct: 10, notional_value: 220, cost_basis: 200, risk_reward_ratio: 0, distance_to_stop_pct: 0, distance_to_target_pct: 0 };
function summary(varResult = unavailable): RiskSummaryResponse {
  return { portfolio: { total_value: 220, total_cost: 200, total_pnl: 20, total_pnl_pct: 10, position_count: 1, open_count: 1, win_rate: 100, sector_exposure: {}, asset_class_exposure: {}, direction_exposure: { long: 220, short: 0, net: 220, gross: 220 }, concentration_risk: 'high', risk_flags: [] }, var: varResult, open_positions: [position], closed_positions_count: 0, alerts: [], risk_limits: {}, generated_at: '2026-10-12' };
}
beforeEach(() => {
  invalidateAuthSession(); jest.clearAllMocks();
  authenticateSession({ id: 'risk-test-user', username: 'risk-test-user', email: null, role: 'viewer', is_active: true, created_at: '', membership: null } as AuthUser, 'risk-test-token', 'https://example.test');
  get.mockImplementation((url: string) => Promise.resolve(url.includes('/research-tools/catalog') ? { tools: [{ tool: 'portfolio', title: '组合风险', available: true }], expires_in_minutes: 30 } : summary()));
});

it('renders the real dashboard with null daily-risk data without zero or NaN estimates, and opens the shared portfolio tool', async () => {
  render(<App><RiskDashboard /></App>);
  expect(await screen.findByText('待补组合日收益')).toBeInTheDocument();
  expect(screen.getByText(/平仓交易收益不能当日收益/)).toBeInTheDocument();
  const varCard = screen.getByLabelText('VaR风险估计');
  expect(within(varCard).getByText('待补日收益')).toBeInTheDocument();
  expect(within(varCard).getByText('金额: 待补日收益')).toBeInTheDocument();
  expect(varCard).not.toHaveTextContent('0.00%'); expect(varCard).not.toHaveTextContent('$0'); expect(varCard).not.toHaveTextContent('NaN');
  const metrics = screen.getByLabelText('日收益风险指标');
  expect(within(metrics).getAllByText('待补日收益')).toHaveLength(3);
  expect(metrics).not.toHaveTextContent('0.00'); expect(metrics).not.toHaveTextContent('NaN');
  expect(screen.getByText('虚构持仓')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '组合日价格分析' }));
  expect(await screen.findByRole('button', { name: /^组合风险/ })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.getByRole('radio', { name: '按资产代码查行情' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
});

it('preserves available numeric VaR while labeling the unannualized standard deviation as daily volatility', async () => {
  get.mockResolvedValue(summary({ historical_var: 1.4, parametric_var: 1.5, var_amount: 150, var_pct: 1.5, confidence: .95, observations: 60, mean_return: .05, std_dev: .8, sharpe_ratio: .99, method: 'parametric', data_available: true }));
  render(<App><RiskDashboard /></App>);
  const card = await screen.findByLabelText('VaR风险估计');
  expect(card).toHaveTextContent('1.50'); expect(card).toHaveTextContent('金额: $150');
  const metrics = screen.getByLabelText('日收益风险指标');
  expect(metrics).toHaveTextContent('0.99'); expect(metrics).toHaveTextContent('日收益波动'); expect(metrics).toHaveTextContent('0.80%');
  expect(screen.queryByText('待补组合日收益')).not.toBeInTheDocument(); expect(screen.queryByText('年化波动')).not.toBeInTheDocument();
});

it('treats explicit unavailable metadata as unavailable even if an old response contains zero placeholders', async () => {
  get.mockResolvedValue(summary({ ...unavailable, var_pct: 0, var_amount: 0, std_dev: 0, sharpe_ratio: 0 }));
  render(<App><RiskDashboard /></App>);
  const card = await screen.findByLabelText('VaR风险估计');
  expect(card).toHaveTextContent('待补日收益'); expect(card).not.toHaveTextContent('0.00'); expect(card).not.toHaveTextContent('$0');
  expect(screen.getByLabelText('日收益风险指标')).not.toHaveTextContent('0.00');
});
