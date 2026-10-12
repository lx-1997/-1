import React from 'react';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import InvestmentResearchCenter, {
  buildBacktestResearchSeed, InvestmentResearchCenterButton, InvestmentResearchReportView,
  researchTableCsv, type InvestmentResearchSeed,
} from '../../components/InvestmentResearchCenter';
import { apiDelete, apiGet, apiPost } from '../../services/apiClient';
import type { InvestmentResearchCatalog, InvestmentResearchJob, InvestmentResearchReport, InvestmentResearchTool } from '../../services/researchService';
import type { QuantLabResponse } from '../../services/quantService';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn(), apiPost: jest.fn(), apiPut: jest.fn(), apiDelete: jest.fn() }));
// This suite renders full Ant Design flows; allow concurrent local builds without
// changing the shorter assertion/polling timeouts that catch missing UI states.
jest.setTimeout(20000);
const get = apiGet as jest.Mock;
const post = apiPost as jest.Mock;
const del = apiDelete as jest.Mock;
const toolNames: Record<InvestmentResearchTool, string> = {
  performance: '收益体检', portfolio: '组合风险', factors: '因子研究', financials: '财务对比', institutions: '机构披露', funds: '基金穿透', bonds: '可转债筛选',
};
const catalog: InvestmentResearchCatalog = { tools: Object.keys(toolNames).map(tool => ({ tool: tool as InvestmentResearchTool, title: toolNames[tool as InvestmentResearchTool], available: true })), expires_in_minutes: 30 };
const account = (id: string): AuthUser => ({ id, username: id, email: null, role: 'viewer', is_active: true, created_at: '', membership: null });
const report = (tool: InvestmentResearchTool = 'performance'): InvestmentResearchReport => ({
  tool, title: `${toolNames[tool]}测试报告`, engine: 'isolated-test-engine',
  summary: [{ label: '区间收益', value: .125, unit: '%' }, { label: '首期 ROE', value: null, unit: '%' }],
  tables: [{ key: 'rows', title: '期间指标', columns: [{ key: 'name', label: '标的' }, { key: 'return', label: '收益率', type: 'percent' }, { key: 'missing', label: '待核实', type: 'number' }], rows: [{ name: '测试数据', return: .125, missing: null }] }],
  charts: [{ key: 'monthly', title: '月收益', type: 'heatmap', value_type: 'percent', labels: ['1'], row_labels: ['2026'], matrix: [[.025]] }],
  warnings: ['13F 是历史披露，不能当成实时持仓。'], methodology: ['每日数据按实际输入计算。'],
  sources: [{ name: '官方披露', url: 'https://www.sec.gov/', as_of: '2026-06-30', currency: 'USD', coverage: '仅本次上传' }],
});
const job = (state: InvestmentResearchJob['state'], tool: InvestmentResearchTool = 'performance', extra: Partial<InvestmentResearchJob> = {}): InvestmentResearchJob => ({
  id: 'job-private-a', tool, state, created_at: '2026-10-12T00:00:00Z', updated_at: '2026-10-12T00:01:00Z', ...(state === 'completed' ? { result: report(tool) } : {}), ...extra,
});
function dataFile(name = 'values.csv', content = 'date,value\n2026-01-01,1\n2026-01-02,1.01') {
  const file = new File([content], name, { type: 'text/csv' });
  Object.defineProperty(file, 'arrayBuffer', { value: jest.fn().mockResolvedValue(new Uint8Array(Array.from(content).map(character => character.charCodeAt(0))).buffer) });
  return file;
}
function choose(file = dataFile()) { fireEvent.change(screen.getByLabelText('研究数据文件'), { target: { files: [file] } }); return file; }
async function selectOption(label: string, option: string) {
  fireEvent.mouseDown(screen.getByRole('combobox', { name: label }));
  fireEvent.click(await screen.findByText(option));
}
async function ready() { await waitFor(() => expect(get).toHaveBeenCalledWith('/api/research-tools/catalog', expect.objectContaining({ signal: expect.any(AbortSignal) }))); }
function selectTool(tool: InvestmentResearchTool) {
  if (['financials', 'institutions'].includes(tool)) fireEvent.click(screen.getByRole('tab', { name: '公司研究' }));
  if (['funds', 'bonds'].includes(tool)) fireEvent.click(screen.getByRole('tab', { name: '资产筛选' }));
  fireEvent.click(screen.getByRole('button', { name: new RegExp(`^${toolNames[tool]}`) }));
}
function jsonInput(input: unknown) {
  fireEvent.click(screen.getByRole('radio', { name: '粘贴 JSON' }));
  fireEvent.change(screen.getByRole('textbox', { name: '完整 JSON 数据' }), { target: { value: JSON.stringify(input) } });
}
async function readBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(String(reader.result)); reader.onerror = reject; reader.readAsText(blob); });
}

beforeEach(() => {
  invalidateAuthSession(); jest.clearAllMocks();
  get.mockImplementation((url: string) => url === '/api/research-tools/catalog' ? Promise.resolve(catalog) : url.includes('/templates/') ? Promise.resolve(new Blob(['dataset_note,date,value\n虚构演示,2026-01-01,1'])) : Promise.resolve(job('completed')));
  post.mockImplementation((_url: string, body: { tool: InvestmentResearchTool }) => Promise.resolve(job('completed', body.tool)));
  del.mockResolvedValue({ state: 'cancelled' });
  authenticateSession(account('research-a'), 'test-research-a', 'https://example.test');
});
afterEach(() => { jest.useRealTimers(); });

it('keeps the initial workspace empty and submits a selected CSV only after explicit Run and currency confirmation', async () => {
  render(<InvestmentResearchCenter />); await ready();
  expect(screen.getByText(/尚未分析/)).toBeInTheDocument();
  expect(screen.queryByLabelText('研究报告')).not.toBeInTheDocument();
  const file = choose();
  expect(file.arrayBuffer).not.toHaveBeenCalled(); expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(await screen.findByText('请明确选择数据的统一币种。')).toBeInTheDocument();
  expect(post).not.toHaveBeenCalled();
  await selectOption('收益币种', '人民币 · CNY');
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post).toHaveBeenCalledWith('/api/research-tools/jobs', expect.objectContaining({ tool: 'performance', input: { file_name: 'values.csv', content_base64: btoa('date,value\n2026-01-01,1\n2026-01-02,1.01'), currency: 'CNY' }, request_id: expect.any(String) }), expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(await screen.findByLabelText('研究报告')).toBeInTheDocument();
});

it.each(Object.keys(toolNames) as InvestmentResearchTool[])('routes %s through its real job API without automatic sample input', async tool => {
  render(<InvestmentResearchCenter />); await ready(); selectTool(tool);
  jsonInput({ dataset_note: 'explicit test fixture', records: [{ value: 1 }] });
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1]).toEqual(expect.objectContaining({ tool, input: { dataset_note: 'explicit test fixture', records: [{ value: 1 }] } }));
  expect(await screen.findByText(`${toolNames[tool]}测试报告`)).toBeInTheDocument();
});

it('rejects malformed JSON and oversized files before submission', async () => {
  render(<InvestmentResearchCenter />); await ready();
  const large = dataFile(); Object.defineProperty(large, 'size', { value: 2 * 1024 * 1024 + 1 }); choose(large);
  expect(screen.getByText(/文件超过 2 MiB/)).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
  expect(large.arrayBuffer).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('radio', { name: '粘贴 JSON' }));
  fireEvent.change(screen.getByRole('textbox', { name: '完整 JSON 数据' }), { target: { value: '{not valid' } });
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(await screen.findByText(/JSON 格式不正确/)).toBeInTheDocument(); expect(post).not.toHaveBeenCalled();
});

it('accepts SEC CIK only on explicit Run and preserves disclosure choices', async () => {
  render(<InvestmentResearchCenter />); await ready(); selectTool('institutions');
  fireEvent.change(screen.getByRole('textbox', { name: '机构 / 披露主体 CIK' }), { target: { value: '0001067983' } });
  fireEvent.click(screen.getByRole('checkbox', { name: '比较上期披露' }));
  expect(post).not.toHaveBeenCalled();
  await selectOption('披露类型', 'Form 4 内部人交易');
  expect(screen.queryByRole('checkbox', { name: '比较上期披露' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1].input).toEqual({ cik: '0001067983', form: '4', compare_previous: false });
  expect(await screen.findByText(/数据日期：2026-06-30/)).toBeInTheDocument();
  expect(screen.getByText('13F 是历史披露，不能当成实时持仓。')).toBeInTheDocument();
});

it('queries SEC quarterly financials with explicit stock codes and never combines a file or old records', async () => {
  render(<InvestmentResearchCenter />); await ready(); selectTool('financials');
  expect(screen.getByRole('radio', { name: '按美股代码查询' })).toBeChecked();
  fireEvent.change(screen.getByRole('textbox', { name: '美股代码' }), { target: { value: 'aapl, MSFT aapl' } });
  expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '开始分析' })); await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1]).toEqual(expect.objectContaining({ tool: 'financials', input: { symbols: ['AAPL', 'MSFT'] } }));
  expect(await screen.findByLabelText('研究报告')).toBeInTheDocument();
});

it('requires dates, currency and ordinary allocation for live portfolio history and submits only that chosen path', async () => {
  render(<InvestmentResearchCenter />); await ready(); selectTool('portfolio');
  choose(dataFile()); fireEvent.click(screen.getByRole('radio', { name: '按资产代码查行情' }));
  fireEvent.change(screen.getByRole('textbox', { name: '资产代码' }), { target: { value: 'AAPL,MSFT' } });
  fireEvent.change(screen.getByLabelText('开始日期'), { target: { value: '2025-01-01' } }); fireEvent.change(screen.getByLabelText('结束日期'), { target: { value: '2026-01-01' } });
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(await screen.findByText('请明确选择行情的统一估值币种。')).toBeInTheDocument();
  await selectOption('统一估值币种', '美元 · USD'); fireEvent.change(screen.getByRole('textbox', { name: '资产配置' }), { target: { value: 'AAPL,60\nMSFT,40' } });
  fireEvent.click(screen.getByRole('button', { name: '开始分析' })); await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1].input).toEqual({ symbols: ['AAPL', 'MSFT'], start_date: '2025-01-01', end_date: '2026-01-01', currency: 'USD', weights: [{ symbol: 'AAPL', weight: .6 }, { symbol: 'MSFT', weight: .4 }], fx_normalized: false });
});

it('uses portfolio weights already provided in CSV and converts optional ordinary percentage allocations once', async () => {
  render(<InvestmentResearchCenter />); await ready(); selectTool('portfolio');
  choose(dataFile('prices.csv', 'date,symbol,close,weight\n2026-01-01,OWN_A,10,0.6\n2026-01-01,OWN_B,20,0.4'));
  await selectOption('统一估值币种', '人民币 · CNY');
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0][1].input.weights).toBeUndefined();
  await screen.findByLabelText('研究报告');
  fireEvent.change(screen.getByRole('textbox', { name: '资产配置' }), { target: { value: 'OWN_A,60\nOWN_B,40%' } });
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(2));
  expect(post.mock.calls[1][1].input.weights).toEqual([{ symbol: 'OWN_A', weight: .6 }, { symbol: 'OWN_B', weight: .4 }]);
});

it('shows actionable dependency and job failures instead of a fake report', async () => {
  get.mockResolvedValueOnce({ ...catalog, tools: catalog.tools.map(item => ({ ...item, available: false, reason: '分析依赖尚未就绪' })) });
  render(<InvestmentResearchCenter />);
  expect(await screen.findByText('分析依赖尚未就绪')).toBeInTheDocument(); jsonInput({ returns: [] });
  expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled(); expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '刷新状态' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '开始分析' })).toBeEnabled());
  post.mockResolvedValueOnce(job('failed', 'performance', { error: '净值不足 30 个日期，请补充每日数据。' }));
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(await screen.findByText('净值不足 30 个日期，请补充每日数据。')).toBeInTheDocument();
  expect(screen.queryByLabelText('研究报告')).not.toBeInTheDocument();
});

it('polls queued tasks and cancels the actual server job, stopping further status requests', async () => {
  jest.useFakeTimers(); post.mockResolvedValueOnce(job('queued'));
  render(<InvestmentResearchCenter />); await ready(); jsonInput({ returns: [] });
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(await screen.findByText('已提交，等待执行')).toBeInTheDocument();
  expect(screen.getByRole('textbox', { name: '完整 JSON 数据' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '取消分析' }));
  expect(await screen.findByText('任务已取消。')).toBeInTheDocument();
  expect(del).toHaveBeenCalledWith('/api/research-tools/jobs/job-private-a', expect.objectContaining({ signal: expect.any(AbortSignal) }));
  await act(async () => { jest.advanceTimersByTime(5000); });
  expect(get.mock.calls.filter(call => call[0].includes('/jobs/'))).toHaveLength(0);
  expect(screen.queryByLabelText('研究报告')).not.toBeInTheDocument();
});

it('retries a failed status query for the same job rather than creating duplicate analysis', async () => {
  jest.useFakeTimers(); post.mockResolvedValueOnce(job('queued'));
  get.mockImplementation((url: string) => url.endsWith('/catalog') ? Promise.resolve(catalog) : Promise.reject(new Error('网络暂时中断')));
  render(<InvestmentResearchCenter />); await ready(); jsonInput({ returns: [] }); fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await screen.findByText('已提交，等待执行');
  await act(async () => { jest.advanceTimersByTime(1200); });
  expect(await screen.findByText('网络暂时中断')).toBeInTheDocument();
  get.mockResolvedValueOnce(job('completed'));
  fireEvent.click(screen.getByRole('button', { name: '重试任务状态' }));
  expect(await screen.findByLabelText('研究报告')).toBeInTheDocument(); expect(post).toHaveBeenCalledTimes(1);
});

it('clears a pending cancellation on account switch and ignores its late success', async () => {
  post.mockResolvedValueOnce(job('queued'));
  let finish!: (value: unknown) => void; del.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  render(<InvestmentResearchCenter />); await ready(); jsonInput({ returns: [] }); fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await screen.findByText('已提交，等待执行'); fireEvent.click(screen.getByRole('button', { name: '取消分析' }));
  await waitFor(() => expect(del).toHaveBeenCalledTimes(1));
  const signal = del.mock.calls[0][1].signal as AbortSignal;
  act(() => { authenticateSession(account('research-b'), 'test-research-b', 'https://example.test'); });
  expect(signal.aborted).toBe(true);
  await act(async () => { finish({ state: 'cancelled' }); });
  expect(screen.queryByText('任务已取消。')).not.toBeInTheDocument(); expect(screen.queryByLabelText('研究报告')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
});

it('clears private inputs/results and ignores a late old-account status response after switching accounts', async () => {
  jest.useFakeTimers(); post.mockResolvedValueOnce(job('queued'));
  let finish!: (value: InvestmentResearchJob) => void;
  get.mockImplementation((url: string) => url.endsWith('/catalog') ? Promise.resolve(catalog) : new Promise(resolve => { finish = resolve; }));
  render(<InvestmentResearchCenter />); await ready(); jsonInput({ returns: [{ date: '2026-01-01', return: .1 }] }); fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await screen.findByText('已提交，等待执行');
  await act(async () => { jest.advanceTimersByTime(1200); });
  const signal = get.mock.calls.find(call => call[0].includes('/jobs/'))![1].signal as AbortSignal;
  act(() => { authenticateSession(account('research-b'), 'test-research-b', 'https://example.test'); });
  expect(signal.aborted).toBe(true);
  expect(del).toHaveBeenCalledWith('/api/research-tools/jobs/job-private-a', expect.any(Object));
  expect(screen.queryByRole('textbox', { name: '完整 JSON 数据' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
  await act(async () => { finish(job('completed')); });
  expect(screen.queryByLabelText('研究报告')).not.toBeInTheDocument();
  act(() => { invalidateAuthSession(); });
  expect(screen.getByText('登录后使用投研工具')).toBeInTheDocument();
});

it('aborts unfinished file reading on unmount before a job can be submitted', async () => {
  let finish!: (buffer: ArrayBuffer) => void;
  const file = dataFile('complete.json', '{}'); (file.arrayBuffer as jest.Mock).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const panel = render(<InvestmentResearchCenter />); await ready(); choose(file); fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  panel.unmount(); await act(async () => { finish(new ArrayBuffer(0)); }); expect(post).not.toHaveBeenCalled();
});

it('cleans a late created job on close without displaying its result', async () => {
  let finish!: (value: InvestmentResearchJob) => void;
  post.mockReturnValueOnce(new Promise(resolve => { finish = resolve; }));
  const panel = render(<InvestmentResearchCenter />); await ready(); jsonInput({ returns: [] }); fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1)); const signal = post.mock.calls[0][2].signal as AbortSignal;
  panel.unmount(); expect(signal.aborted).toBe(true);
  await act(async () => { finish(job('completed')); });
  expect(del).toHaveBeenCalledWith('/api/research-tools/jobs/job-private-a', expect.any(Object));
});

it('downloads a clearly fictitious template through the authenticated helper without filling or running it', async () => {
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
  URL.createObjectURL = jest.fn(() => 'blob:research-template'); URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    render(<InvestmentResearchCenter />); await ready();
    expect(screen.getByText(/模板中的数据为虚构演示/)).toBeInTheDocument(); fireEvent.click(screen.getByRole('button', { name: '下载虚构数据模板' }));
    await waitFor(() => expect(click).toHaveBeenCalledTimes(1));
    expect(get).toHaveBeenCalledWith('/api/research-tools/templates/performance', expect.objectContaining({ responseType: 'blob', signal: expect.any(AbortSignal) }));
    expect(post).not.toHaveBeenCalled(); expect(screen.getByRole('button', { name: '开始分析' })).toBeDisabled();
  } finally { click.mockRestore(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
});

it('renders decimal percentages once, keeps missing values and exports the full report safely', async () => {
  const result = { ...report(), report_html: '<html><script>window.researchAttack=1</script><body>下载内容</body></html>' };
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
  const blobs: Blob[] = []; URL.createObjectURL = jest.fn(blob => { blobs.push(blob as Blob); return 'blob:research-export'; }); URL.revokeObjectURL = jest.fn();
  const downloads: string[] = []; const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) { downloads.push(this.download); });
  try {
    render(<InvestmentResearchReportView report={result} />);
    expect(screen.getAllByText('12.5%')).toHaveLength(2); expect(screen.getAllByText('—')).toHaveLength(2); expect(screen.getByText('2.5%')).toBeInTheDocument();
    expect(document.querySelector('script')).toBeNull();
    fireEvent.click(screen.getByRole('button', { name: '导出报告 JSON' }));
    fireEvent.click(screen.getByRole('button', { name: '导出 期间指标 CSV' }));
    fireEvent.click(screen.getByRole('button', { name: '下载完整 HTML 报告' }));
    expect(downloads).toEqual(['收益体检测试报告.json', '收益体检测试报告-期间指标.csv', '收益体检测试报告.html']);
    expect(JSON.parse(await readBlob(blobs[0]))).toEqual(result); expect(await readBlob(blobs[1])).toContain('"0.125"'); expect(await readBlob(blobs[2])).toEqual(result.report_html);
  } finally { click.mockRestore(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
});

it('neutralizes spreadsheet formulas in text while preserving negative numeric values', () => {
  const csv = researchTableCsv({ key: 'safe', title: 'safe', columns: [{ key: 'name', label: '=header' }, { key: 'value', label: '数值' }], rows: [{ name: '=cmd(1)', value: -3 }, { name: ' +SUM(1)', value: .2 }, { name: '-text', value: 0 }, { name: '@formula', value: null }] });
  expect(csv).toContain('"\'=header"'); expect(csv).toContain('"\'=cmd(1)","-3"'); expect(csv).toContain('"\' +SUM(1)"'); expect(csv).toContain('"\'-text"'); expect(csv).toContain('"\'@formula"');
});

it('shows nonzero daily covariance distinctly from zero and exports original numbers without display rounding', async () => {
  const result: InvestmentResearchReport = { ...report('portfolio'), summary: [], charts: [], warnings: [], tables: [{
    key: 'covariance', title: '日收益协方差',
    columns: [{ key: 'pair', label: '资产组合' }, { key: 'covariance', label: '协方差', type: 'number' }, { key: 'weight', label: '权重', type: 'percent' }],
    rows: [{ pair: 'A / B', covariance: .00003, weight: .125 }, { pair: 'A / C', covariance: -.00003, weight: .125 }, { pair: 'B / C', covariance: 0, weight: .125 }],
  }] };
  const originalCreate = URL.createObjectURL, originalRevoke = URL.revokeObjectURL;
  const blobs: Blob[] = []; URL.createObjectURL = jest.fn(blob => { blobs.push(blob as Blob); return 'blob:research-covariance'; }); URL.revokeObjectURL = jest.fn();
  const click = jest.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  try {
    render(<InvestmentResearchReportView report={result} />);
    expect(screen.getByRole('cell', { name: '3.00e-5' })).toBeInTheDocument();
    expect(screen.getByRole('cell', { name: '-3.00e-5' })).toBeInTheDocument();
    expect(screen.getAllByRole('cell', { name: '0' })).toHaveLength(1);
    expect(screen.getAllByRole('cell', { name: '12.5%' })).toHaveLength(3);
    fireEvent.click(screen.getByRole('button', { name: '导出报告 JSON' }));
    fireEvent.click(screen.getByRole('button', { name: '导出 日收益协方差 CSV' }));
    expect(JSON.parse(await readBlob(blobs[0]))).toEqual(result);
    const csv = await readBlob(blobs[1]);
    expect(csv).toContain('"A / B","0.00003","0.125"');
    expect(csv).toContain('"A / C","-0.00003","0.125"');
    expect(csv).toContain('"B / C","0","0.125"');
    expect(csv).not.toContain('3.00e-5');
  } finally { click.mockRestore(); URL.createObjectURL = originalCreate; URL.revokeObjectURL = originalRevoke; }
});

it('opens a backtest seed for review but requires an explicit currency and Run, and drops that seed on account change', async () => {
  const seed: InvestmentResearchSeed = { tool: 'performance', label: '用当前回测生成报告', input: { net_values: [{ date: '2026-01-01', value: 1.1 }], name: '历史回测' }, note: '只有历史模拟数据。' };
  render(<InvestmentResearchCenterButton seed={seed} />); fireEvent.click(screen.getByRole('button', { name: seed.label })); await ready();
  expect(screen.getByText('只有历史模拟数据。')).toBeInTheDocument(); expect(post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  expect(await screen.findByText('请明确选择当前回测使用的估值币种。')).toBeInTheDocument();
  await selectOption('收益币种', '美元 · USD'); fireEvent.click(screen.getByRole('button', { name: '开始分析' }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1)); expect(post.mock.calls[0][1].input).toEqual({ ...seed.input, currency: 'USD' });
  act(() => { authenticateSession(account('research-b'), 'test-research-b', 'https://example.test'); });
  expect(screen.queryByRole('button', { name: seed.label })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '投研工具' }));
  expect(await screen.findByLabelText('研究数据文件')).toBeInTheDocument(); expect(screen.queryByText('只有历史模拟数据。')).not.toBeInTheDocument();
});

it('never bridges missing or invalid backtest valuation days and only prefills a complete provenance-backed series', () => {
  const source = { is_synthetic: false, source: 'official-provider', quality: 'historical' };
  const dates = Array.from({ length: 31 }, (_, index) => `2026-01-${String(index + 1).padStart(2, '0')}`);
  const values = dates.map((_, index) => 1 + index * .001);
  const result = { name: '真实历史回测', strategy_label: '策略', generated_at: '2026-10-12', benchmark: 'INDEX', data_source_details: { INDEX: source }, backtest: { dates, equity_curve: values, benchmark_curve: values, data_source_details: {} } } as unknown as QuantLabResponse;
  const seed = buildBacktestResearchSeed(result)!;
  expect(seed.input.net_values).toEqual(dates.map((date, index) => ({ date, value: values[index], benchmark_value: values[index] })));
  expect(seed.input.currency).toBeUndefined();
  const synthetic = buildBacktestResearchSeed({ ...result, data_source_details: { INDEX: { ...source, is_synthetic: true } } })!;
  expect(synthetic.input.net_values).toEqual(dates.map((date, index) => ({ date, value: values[index] })));
  const incomplete = buildBacktestResearchSeed({ ...result, backtest: { ...result.backtest, benchmark_curve: [1] } })!;
  expect(incomplete.input.net_values).toEqual(synthetic.input.net_values); expect(buildBacktestResearchSeed(null)).toBeUndefined();
  expect(buildBacktestResearchSeed({ ...result, backtest: { ...result.backtest, equity_curve: [1, 0, ...values.slice(2)] } })).toBeUndefined();
  expect(buildBacktestResearchSeed({ ...result, backtest: { ...result.backtest, equity_curve: values.slice(1) } })).toBeUndefined();
  expect(buildBacktestResearchSeed({ ...result, backtest: { ...result.backtest, dates: [...dates.slice(0, 30), '2026-02-30'] } })).toBeUndefined();
  expect(buildBacktestResearchSeed({ ...result, backtest: { ...result.backtest, dates: dates.slice(0, 29), equity_curve: values.slice(0, 29) } })).toBeUndefined();
});

it('summarizes repeated financial caveats without hiding coverage or risk and retains every original detail', async () => {
  render(<InvestmentResearchReportView report={{ ...report('financials'), warnings: [
    'AAPL 2025-03-31 有缺失财务项目，相关比率为空，未用 0 填充。',
    'AAPL 2025-06-30 有缺失财务项目，相关比率为空，未用 0 填充。',
    '基金仅披露 60%，未披露部分保留为未知，不归一化。',
    '样本尾部仍是收益，不表示未来没有损失风险。',
  ] }} />);
  expect(screen.getByText('需核对 4 条数据与使用说明')).toBeInTheDocument();
  expect(screen.getByText('有缺失财务项目，相关比率为空，未用 0 填充。')).toBeInTheDocument();
  expect(screen.getByText('基金仅披露 60%，未披露部分保留为未知，不归一化。')).toBeInTheDocument();
  expect(screen.getByText('样本尾部仍是收益，不表示未来没有损失风险。')).toBeInTheDocument();
  expect(screen.queryByText(/^AAPL 2025-03-31/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('完整说明 · 4 条'));
  expect(await screen.findByText('AAPL 2025-03-31 有缺失财务项目，相关比率为空，未用 0 填充。')).toBeInTheDocument();
  expect(screen.getByText('AAPL 2025-06-30 有缺失财务项目，相关比率为空，未用 0 填充。')).toBeInTheDocument();
});

it('keeps latest comparison visible and expands the unified financial history explicitly', async () => {
  render(<InvestmentResearchReportView report={{ ...report('financials'), tables: [
    { key: 'latest_comparison', title: '最新同期企业比较', columns: [{ key: 'name', label: '企业' }], rows: [{ name: '最新结果' }] },
    { key: 'financial_history', title: '完整财务历史', collapsible: true, default_collapsed: true, columns: [{ key: 'name', label: '企业' }], rows: [{ name: '历史明细' }] },
  ] }} />);
  expect(screen.getByText('最新结果')).toBeInTheDocument(); expect(screen.queryByText('历史明细')).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('完整财务历史 · 1 条记录'));
  expect(await screen.findByText('历史明细')).toBeInTheDocument(); expect(screen.getByRole('button', { name: '导出 完整财务历史 CSV' })).toBeInTheDocument();
});

it('combines repeated SEC concept caveats into a plain-language summary while retaining the source concepts in details', async () => {
  render(<InvestmentResearchReportView report={{ ...report('financials'), warnings: [
    'AAPL 2026-03-28 NetIncomeLoss 使用本期已披露前期累计差分，请核对重述。',
    'AAPL 2026-06-27 NetCashProvidedByUsedInOperatingActivities 使用本期已披露前期累计差分，请核对重述。',
  ] }} />);
  expect(screen.getByText('净利润、经营现金流：使用本期已披露前期累计差分，请核对重述。')).toBeInTheDocument();
  expect(screen.queryByText(/AAPL 2026-03-28 NetIncomeLoss/)).not.toBeInTheDocument();
  fireEvent.click(screen.getByText('完整说明 · 2 条'));
  expect(await screen.findByText(/AAPL 2026-03-28 NetIncomeLoss/)).toBeInTheDocument();
  expect(screen.getByText(/AAPL 2026-06-27 NetCashProvidedByUsedInOperatingActivities/)).toBeInTheDocument();
});
