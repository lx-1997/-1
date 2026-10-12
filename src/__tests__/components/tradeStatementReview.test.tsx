import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import TradeStatementReview, { TradeStatementReviewButton } from '../../components/TradeStatementReview';
import { apiGet, apiPost } from '../../services/apiClient';
import type { StatementCurrencyReview, TradeStatementAnalysis } from '../../services/riskService';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';
import type { AuthUser } from '../../services/authService';

jest.mock('../../services/apiClient', () => ({
  apiGet: jest.fn(), apiPost: jest.fn(), apiPut: jest.fn(), apiDelete: jest.fn(),
}));
const post = apiPost as jest.Mock;
const get = apiGet as jest.Mock;
const csv = 'date,symbol,side,quantity,price,currency\n2026-10-01,AAPL,buy,1,10,USD';
const account = (id: string): AuthUser => ({ id, username: id, email: null, role: 'viewer', is_active: true, created_at: '', membership: null });

function statementFile(name = 'trades.csv') {
  const file = new File([csv], name, { type: 'text/csv' });
  const bytes = new Uint8Array(Array.from(csv).map(character => character.charCodeAt(0)));
  Object.defineProperty(file, 'arrayBuffer', { value: jest.fn().mockResolvedValue(bytes.buffer) });
  return file;
}

function choose(file = statementFile()) {
  fireEvent.change(screen.getByLabelText('交割单文件'), { target: { files: [file] } });
  return file;
}

function currencyReview(currency: string, pnl: number): StatementCurrencyReview {
  return {
    currency, buy_amount: 1000, sell_amount: 1600, fees: 100,
    realized_pnl: pnl, closed_trades: 2, winning_trades: 1, losing_trades: 1,
    win_rate: 50, unknown_cost_quantity: 0, open_quantity: 10,
    monthly: [{ month: '2026-10', realized_pnl: pnl, fees: 100 }],
    symbols: [{ symbol: '000001', name: '演示股票', realized_pnl: pnl, fees: 100, open_quantity: 10 }],
  };
}

function analysis(overrides: Partial<TradeStatementAnalysis> = {}): TradeStatementAnalysis {
  return {
    file_name: 'trades.csv', total_rows: 3, trade_count: 3, skipped_rows: 0,
    currencies: [currencyReview('CNY', 500), currencyReview('USD', 20)],
    trades: [{ row: 2, date: '2026-10-01', time: '', symbol: '000001', name: '演示股票', side: 'buy', quantity: 10, price: 100, gross_amount: 1000, fees: 100, net_amount: 1100, currency: 'CNY' }],
    issues: [], warnings: [], ...overrides,
  };
}

beforeEach(() => {
  invalidateAuthSession();
  jest.clearAllMocks();
  post.mockResolvedValue(analysis());
  authenticateSession(account('statement-a'), 'test-statement-a', 'https://example.test');
});

it('submits only after the explicit review action and uses the authenticated API client', async () => {
  render(<TradeStatementReview />);
  expect(screen.getByRole('button', { name: '开始复盘' })).toBeDisabled();
  const file = choose();
  expect(screen.getByText(/trades.csv ·/)).toBeInTheDocument();
  expect(file.arrayBuffer).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post).toHaveBeenCalledWith('/api/portfolio/statement/analyze', {
    file_name: 'trades.csv', content_base64: btoa(csv),
  }, expect.objectContaining({ signal: expect.any(AbortSignal), timeout: 120000 }));
  expect(await screen.findByLabelText('交割单分析结果')).toBeInTheDocument();
});

it('keeps each currency separate and preserves percentage units', async () => {
  render(<TradeStatementReview />);
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  const cny = await screen.findByRole('region', { name: 'CNY 交易复盘' });
  expect(within(cny).getAllByText('CNY 500.00')).not.toHaveLength(0);
  expect(within(cny).getByText('50.0%')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '美元 · USD' }));
  const usd = screen.getByRole('region', { name: 'USD 交易复盘' });
  expect(within(usd).getAllByText('USD 20.00')).not.toHaveLength(0);
  expect(screen.queryByText(/520\.00/)).not.toBeInTheDocument();
  expect(screen.getByText(/金额不跨币种合计/)).toBeInTheDocument();
});

it('shows server warnings, unknown-cost sells and row issues', async () => {
  post.mockResolvedValue(analysis({
    currencies: [{ ...currencyReview('HKD', 15), unknown_cost_quantity: 25 }],
    warnings: ['请确认港股通使用人民币结算金额。'],
    issues: [{ row: 4, code: 'missing_cost', message: '此卖出记录没有可配对买入。' }],
  }));
  render(<TradeStatementReview />);
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  expect(await screen.findByText('请确认港股通使用人民币结算金额。')).toBeInTheDocument();
  expect(screen.getByText('有卖出记录缺少买入成本')).toBeInTheDocument();
  expect(screen.getByText(/HKD 有 25 单位卖出无法确定成本/)).toBeInTheDocument();
  fireEvent.click(screen.getByText('逐行问题 · 1 项'));
  expect(await screen.findByText('此卖出记录没有可配对买入。')).toBeInTheDocument();
});

it('distinguishes the same symbol from different markets within one currency', async () => {
  post.mockResolvedValue(analysis({ currencies: [{ ...currencyReview('CNY', 15), symbols: [
    { symbol: '000001', market: 'CN', name: '演示 A 股', realized_pnl: 10, fees: 1, open_quantity: 10 },
    { symbol: '000001', market: 'HK', name: '演示港股', realized_pnl: 5, fees: 1, open_quantity: 20 },
  ] }] }));
  render(<TradeStatementReview />);
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  expect(await screen.findByText('000001 · CN')).toBeInTheDocument();
  expect(screen.getByText('000001 · HK')).toBeInTheDocument();
  expect(screen.getByText('演示 A 股')).toBeInTheDocument();
  expect(screen.getByText('演示港股')).toBeInTheDocument();
});

it('lets the user correct the settlement currency after a failed analysis', async () => {
  post.mockRejectedValueOnce({ response: { data: { detail: '港股通请明确选择结算币种。' } } });
  render(<TradeStatementReview />);
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  expect(await screen.findByText('港股通请明确选择结算币种。')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始复盘' })).toBeEnabled();
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '港股通 / 缺失币种的结算币种' }));
  fireEvent.click(await screen.findByText('人民币 · CNY'));
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1].settlement_currency).toBe('CNY');
  expect(await screen.findByLabelText('交割单分析结果')).toBeInTheDocument();
});

it('rejects an oversized file and clears the previous choice without uploading', () => {
  render(<TradeStatementReview />);
  choose();
  const tooLarge = statementFile('large.xlsx');
  Object.defineProperty(tooLarge, 'size', { value: 2 * 1024 * 1024 + 1 });
  choose(tooLarge);
  expect(screen.getByText(/文件超过 2 MiB/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始复盘' })).toBeDisabled();
  expect(post).not.toHaveBeenCalled();
  expect(tooLarge.arrayBuffer).not.toHaveBeenCalled();
});

it('shows an empty report without inventing account returns', async () => {
  post.mockResolvedValue(analysis({ trade_count: 0, skipped_rows: 3, currencies: [], trades: [] }));
  render(<TradeStatementReview />);
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  expect(await screen.findByText('没有可分析的成交记录，请检查文件和下方逐行问题。')).toBeInTheDocument();
  expect(screen.queryByText(/已实现盈亏 ·/)).not.toBeInTheDocument();
});

it('does not submit a file still being read after the panel is unmounted', async () => {
  const file = statementFile();
  let finishReading!: (buffer: ArrayBuffer) => void;
  (file.arrayBuffer as jest.Mock).mockReturnValue(new Promise<ArrayBuffer>(resolve => { finishReading = resolve; }));
  const panel = render(<TradeStatementReview />);
  choose(file);
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  expect(screen.getByRole('status')).toHaveTextContent('正在核对成交并计算盈亏');
  panel.unmount();
  await act(async () => { finishReading(new ArrayBuffer(0)); });
  expect(post).not.toHaveBeenCalled();
});

it('clears account data and drops late results on account switches and logout', async () => {
  let finish!: (result: TradeStatementAnalysis) => void;
  post.mockReturnValueOnce(new Promise<TradeStatementAnalysis>(resolve => { finish = resolve; }));
  render(<TradeStatementReview />);
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  const signal = post.mock.calls[0][2].signal as AbortSignal;
  act(() => { authenticateSession(account('statement-b'), 'test-statement-b', 'https://example.test'); });
  expect(signal.aborted).toBe(true);
  expect(screen.getByRole('button', { name: '开始复盘' })).toBeDisabled();
  expect(screen.queryByText(/trades.csv ·/)).not.toBeInTheDocument();
  await act(async () => { finish(analysis()); });
  expect(screen.queryByLabelText('交割单分析结果')).not.toBeInTheDocument();
  choose();
  fireEvent.click(screen.getByRole('button', { name: '开始复盘' }));
  expect(await screen.findByLabelText('交割单分析结果')).toBeInTheDocument();
  act(() => { invalidateAuthSession(); });
  expect(screen.queryByLabelText('交割单分析结果')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始复盘' })).toBeDisabled();
});

it('downloads an explicitly marked demo template through authenticated apiGet', async () => {
  get.mockResolvedValue(new Blob([csv], { type: 'text/csv' }));
  const originalCreate = URL.createObjectURL;
  const originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = jest.fn(() => 'blob:demo');
  URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    render(<TradeStatementReview />);
    expect(screen.getByText(/模板使用虚构成交/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '下载演示模板' }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(get).toHaveBeenCalledWith('/api/portfolio/statement/template', expect.objectContaining({ responseType: 'blob', signal: expect.any(AbortSignal) }));
    expect(post).not.toHaveBeenCalled();
  } finally {
    click.mockRestore();
    URL.createObjectURL = originalCreate;
    URL.revokeObjectURL = originalRevoke;
  }
});

it('opens the reusable review drawer from its button', async () => {
  render(<TradeStatementReviewButton />);
  expect(screen.queryByLabelText('交割单文件')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '交割单复盘' }));
  expect(await screen.findByLabelText('交割单文件')).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
});
