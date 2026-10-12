import React from 'react';
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { App as AntdApp } from 'antd';
import DataSourceCenter from '../../components/DataSourceCenter';
import ResearchWorkbench from '../../components/ResearchWorkbench';
import * as infrastructure from '../../services/infrastructureService';
import * as research from '../../services/researchService';
import { getMarketDataLayers } from '../../services/marketService';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';
import type { AppState } from '../../types';

// Keep both result readers and the full formatter real; replace remote SDK I/O.
// The new export widget is a small prop probe so these tests check which exact
// completed content and source identity the actual entry sends to the exporter.
jest.mock('../../components/common/AiInterpretationExportActions', () => ({
  __esModule: true,
  default: ({ text, disabled, scopeKey }: { text: string; disabled?: boolean; scopeKey?: string }) => (
    <section data-testid="interpretation-export" data-scope={scopeKey}>
      <textarea aria-label="导出全文" readOnly value={text} />
      <button type="button" disabled={disabled}>复制文字</button>
      <button type="button" disabled={disabled}>复制完整图片</button>
    </section>
  ),
}));
jest.mock('../../services/infrastructureService', () => ({
  ...jest.requireActual('../../services/infrastructureService'),
  listDataSources: jest.fn(), listDataItems: jest.fn(), listDataTags: jest.fn(),
  getCorpusStats: jest.fn(), interpretDataItem: jest.fn(), updateDataItem: jest.fn(),
}));
jest.mock('../../services/marketService', () => ({
  ...jest.requireActual('../../services/marketService'), getMarketDataLayers: jest.fn(async () => null),
}));
jest.mock('../../services/researchService', () => ({
  ...jest.requireActual('../../services/researchService'),
  listProfessionalReports: jest.fn(), listProfessionalMetrics: jest.fn(), listWorkbenchDownloads: jest.fn(),
  searchResearchReports: jest.fn(), searchResearchWorkbenchReports: jest.fn(),
  ingestProfessionalReportUrl: jest.fn(), analyzeProfessionalReport: jest.fn(), visionAnalyzeReport: jest.fn(),
  queryProfessionalRag: jest.fn(),
}));

const stock = { symbol: '600519.SH', name: '贵州茅台', market: 'CN', sector: '', marketCap: 1,
  currentPrice: 1, changePercent: 0, description: '', focusLevel: 'high', totalPosts: 0,
  totalPaidPosts: 0, communityScore: 0 };
const appState = { stocks: [stock], selectedStock: stock, posts: [], currentView: 'research-workbench' } as unknown as AppState;
const source = { id: 'source-1', name: '资料源', category: 'research', source_type: 'upload',
  description: '', status: 'active', config: {}, module_refs: [], items_count: 1 };
const item = { id: 'article-1', source_id: source.id, source_name: source.name, source_type: 'upload',
  source_category: 'research', title: '完整资料解读', symbol: stock.symbol, text: '资料原文',
  text_preview: '资料预览', tags: [], metadata: {}, credibility_score: 0.9, collected_at: '2026-10-12' };
const report = { id: 'report-1', title: '完整研报解读', symbol: stock.symbol, report_type: 'research',
  parser: 'pdf', char_count: 1000, metadata: {}, metrics_count: 7, chunks_count: 8,
  created_at: '2026-10-12', updated_at: '2026-10-12' };
const eastmoney = { id: 'em-1', source: 'eastmoney', title: report.title, org: '机构',
  date: '2026-10-12', market: 'CN', symbol: stock.symbol, pdf_url: '/fixture.pdf', preview_url: '/preview' };
const analysis = { report, summary: '完整分析正文\n尾段判断必须导出', confidence: 0.8,
  key_metrics: Array.from({ length: 7 }, (_, index) => ({ id: `metric-${index}`, report_id: report.id,
    metric_label: `指标${index + 1}`, raw_value: `完整数值${index + 1}`, period: '2026Q3',
    source_page: index + 1, source_excerpt: `指标证据${index + 1}`, confidence: 0.8, metadata: {} })),
  quality_flags: Array.from({ length: 5 }, (_, index) => `完整红旗${index + 1}`),
  risks: Array.from({ length: 5 }, (_, index) => `完整风险${index + 1}`),
  follow_up_questions: Array.from({ length: 5 }, (_, index) => `完整追问${index + 1}`),
  citations: Array.from({ length: 7 }, (_, index) => ({ citation_id: `cite-${index + 1}`, kind: 'chunk',
    source_id: `source-${index}`, report_id: report.id, report_title: report.title, title: '引用',
    page: index + 1, text: `完整引用证据${index + 1}`, score: 0.8, metadata: {} })),
  disclaimer: '专业研报原有免责声明' };
const vision = { title: '视觉研报', summary: '视觉完整正文', confidence: 0.8, rating: '买入',
  target_price: '2000元', pages_analyzed: 8, provider: 'fixture-cloud', mode: 'vision',
  key_points: Array.from({ length: 7 }, (_, index) => `视觉要点${index + 1}`),
  risks: Array.from({ length: 5 }, (_, index) => `视觉风险${index + 1}`), disclaimer: '视觉原有免责声明' };
const user = (id: string) => ({ id, username: id, email: null, role: 'viewer' as const,
  is_active: true, created_at: '' });
const originalFetch = global.fetch;
const originalGetComputedStyle = window.getComputedStyle;

beforeAll(() => {
  // rc-table asks for pseudo-element styles, which jsdom deliberately omits.
  window.getComputedStyle = (element) => originalGetComputedStyle(element);
});
afterAll(() => { window.getComputedStyle = originalGetComputedStyle; global.fetch = originalFetch; });
beforeEach(() => {
  jest.useFakeTimers();
  jest.resetAllMocks();
  invalidateAuthSession();
  localStorage.clear();
  authenticateSession(user('A'), 'token-a', 'https://example.test');
  global.fetch = jest.fn(async () => ({ ok: true, json: async () => ({ credentialsAvailable: true, jobs: [] }) })) as any;
  (infrastructure.listDataSources as jest.Mock).mockResolvedValue([source]);
  (infrastructure.listDataItems as jest.Mock).mockResolvedValue([item]);
  (infrastructure.listDataTags as jest.Mock).mockResolvedValue([]);
  (infrastructure.getCorpusStats as jest.Mock).mockResolvedValue({ total: 0, symbol_coverage: [], source_quality: [], freshness: {} });
  (getMarketDataLayers as jest.Mock).mockResolvedValue(null);
  (infrastructure.interpretDataItem as jest.Mock).mockResolvedValue({ item, interpretation: '生成正文' });
  (infrastructure.updateDataItem as jest.Mock).mockResolvedValue(item);
  (research.listProfessionalReports as jest.Mock).mockResolvedValue([report]);
  (research.listProfessionalMetrics as jest.Mock).mockResolvedValue([]);
  (research.listWorkbenchDownloads as jest.Mock).mockResolvedValue({ files: [], summary: {} });
  (research.searchResearchReports as jest.Mock).mockResolvedValue({ items: [eastmoney], warnings: [] });
  (research.searchResearchWorkbenchReports as jest.Mock).mockResolvedValue({ items: [] });
  (research.ingestProfessionalReportUrl as jest.Mock).mockResolvedValue(report);
  (research.analyzeProfessionalReport as jest.Mock).mockResolvedValue(analysis);
  (research.visionAnalyzeReport as jest.Mock).mockResolvedValue(vision);
  (research.queryProfessionalRag as jest.Mock).mockResolvedValue({ answer: '引用问答完整正文\n引用问答最后一段',
    metrics: analysis.key_metrics, citations: analysis.citations, confidence: 0.8,
    missing: ['待补证据1', '待补证据2', '待补证据3', '待补证据4'], disclaimer: '引用问答原有免责声明' });
});
afterEach(() => { cleanup(); invalidateAuthSession(); jest.clearAllTimers(); jest.useRealTimers(); });

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}
function buttonsWithText(label: string): HTMLButtonElement[] {
  // These readers contain many unrelated Antd controls. Text-based lookup of
  // the actual button avoids recomputing every control's CSS-dependent
  // accessible name in jsdom, while preserving the real click/state flow.
  return Array.from(new Set(screen.getAllByText(label)
    .map(element => element.closest('button'))
    .filter((button): button is HTMLButtonElement => button !== null)));
}
function buttonWithText(label: string): HTMLButtonElement {
  const buttons = buttonsWithText(label);
  expect(buttons).toHaveLength(1);
  return buttons[0];
}
async function openDataSourceInterpretation() {
  render(<DataSourceCenter appState={appState} />);
  await screen.findByText(item.title);
  await act(async () => { fireEvent.click(buttonWithText('AI 解读')); });
}
async function openWorkbench() {
  render(<AntdApp><ResearchWorkbench appState={appState} onViewChange={jest.fn()} /></AntdApp>);
  await waitFor(() => expect(buttonWithText('一键 AI 分析')).toBeEnabled());
}
function expectCompleteProfessionalExport() {
  const exported = (within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true }) as HTMLTextAreaElement).value;
  for (const content of ['尾段判断必须导出', '完整数值7', '指标证据7', '完整红旗5', '完整风险5',
    '完整追问5', '完整引用证据7', '专业研报原有免责声明']) expect(exported).toContain(content);
  return screen.getByTestId('interpretation-export');
}

it('exports the saved source interpretation and the full latest edited text', async () => {
  const saved = `${'已保存全文。'.repeat(160)}\n已保存最后一行`;
  (infrastructure.listDataItems as jest.Mock).mockResolvedValue([{ ...item, metadata: { ai_interpretation: saved } }]);
  await openDataSourceInterpretation();
  expect(within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true })).toHaveValue(saved);
  const dialog = screen.getByRole('dialog', { name: `AI 解读：${item.title}`, hidden: true });
  const editor = within(dialog).getAllByRole('textbox', { hidden: true }).find(input => input.getAttribute('aria-label') !== '导出全文')!;
  const edited = `${'编辑后完整正文。'.repeat(160)}\n编辑后最后一行`;
  fireEvent.change(editor, { target: { value: edited } });
  expect(within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true })).toHaveValue(edited);
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).toContain('article-1');
  expect(infrastructure.interpretDataItem).not.toHaveBeenCalled();
  expect(within(dialog).getByRole('button', { name: /保存解读/, hidden: true })).toBeInTheDocument();
});

it('makes the full saved interpretation available from the source details reader', async () => {
  const saved = `${'详情中的完整解读。'.repeat(160)}\n详情最后一行`;
  (infrastructure.listDataItems as jest.Mock).mockResolvedValue([{ ...item, metadata: { ai_interpretation: saved } }]);
  render(<DataSourceCenter appState={appState} />);
  await screen.findByText(item.title);
  await act(async () => { fireEvent.click(buttonWithText('查看')); });
  expect(within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true })).toHaveValue(saved);
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).toContain('data-source-saved:article-1');
  expect(infrastructure.interpretDataItem).not.toHaveBeenCalled();
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
});

it('blocks source exports during regeneration and gives the completed response a new identity', async () => {
  (infrastructure.listDataItems as jest.Mock).mockResolvedValue([{ ...item, metadata: { ai_interpretation: '之前已完成的解读' } }]);
  const waiting = deferred<any>();
  (infrastructure.interpretDataItem as jest.Mock).mockReturnValue(waiting.promise);
  await openDataSourceInterpretation();
  const oldScope = screen.getByTestId('interpretation-export').getAttribute('data-scope');
  const dialog = screen.getByRole('dialog', { name: `AI 解读：${item.title}`, hidden: true });
  await act(async () => { fireEvent.click(within(dialog).getByRole('button', { name: /重新解读/, hidden: true })); });
  expect(within(screen.getByTestId('interpretation-export')).getByRole('button', { name: '复制文字', hidden: true })).toBeDisabled();
  expect(within(screen.getByTestId('interpretation-export')).getByRole('button', { name: '复制完整图片', hidden: true })).toBeDisabled();
  await act(async () => { waiting.resolve({ item, interpretation: '完整新响应\n新响应最后一行' }); });
  expect(within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true })).toHaveValue('完整新响应\n新响应最后一行');
  expect(within(screen.getByTestId('interpretation-export')).getByRole('button', { name: '复制文字', hidden: true })).toBeEnabled();
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).not.toBe(oldScope);
});

it('closes source exports on account change and ignores the previous account late interpretation', async () => {
  const waiting = deferred<any>();
  (infrastructure.interpretDataItem as jest.Mock).mockReturnValue(waiting.promise);
  await openDataSourceInterpretation();
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  await act(async () => { waiting.resolve({ item, interpretation: 'A 账户的迟到私有解读' }); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog', { name: `AI 解读：${item.title}` })).not.toBeInTheDocument();
});

it('quick professional analysis exports all metrics, risks, questions and citations beyond the abbreviated reader', async () => {
  await openWorkbench();
  await act(async () => { fireEvent.click(buttonWithText('一键 AI 分析')); });
  expectCompleteProfessionalExport();
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).toContain('research-quick:em:em-1');
  expect(buttonWithText('转专业模式深入')).toBeInTheDocument();
  expect(buttonWithText('发送给 Agent')).toBeInTheDocument();
});

it('visual fallback exports every returned point, risk, rating and the original disclaimer after completion', async () => {
  (research.ingestProfessionalReportUrl as jest.Mock).mockRejectedValue({ response: { data: { detail: '报告文本为空' } } });
  const waiting = deferred<any>();
  (research.visionAnalyzeReport as jest.Mock).mockReturnValue(waiting.promise);
  await openWorkbench();
  await act(async () => { fireEvent.click(buttonWithText('一键 AI 分析')); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
  await act(async () => { waiting.resolve(vision); });
  const exported = (within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true }) as HTMLTextAreaElement).value;
  for (const value of ['视觉完整正文', '视觉要点7', '视觉风险5', '买入', '2000元', '视觉原有免责声明']) expect(exported).toContain(value);
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).toContain('research-vision:em:em-1');
});

it('professional workbench exports its complete completed response beside the existing share action', async () => {
  await openWorkbench();
  fireEvent.click(buttonWithText('专业模式'));
  const buttons = buttonsWithText('生成投委会摘要');
  await act(async () => { fireEvent.click(buttons[buttons.length - 1]); });
  expectCompleteProfessionalExport();
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).toContain('research-professional:report-1');
  expect(within(screen.getByTestId('interpretation-export')).getByRole('button', { name: '复制文字', hidden: true })).toBeEnabled();
});

it('hides professional exports while generating and ignores a late previous-account result', async () => {
  const waiting = deferred<any>();
  (research.analyzeProfessionalReport as jest.Mock).mockReturnValueOnce(waiting.promise);
  await openWorkbench();
  fireEvent.click(buttonWithText('专业模式'));
  const buttons = buttonsWithText('生成投委会摘要');
  await act(async () => { fireEvent.click(buttons[buttons.length - 1]); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  await act(async () => { waiting.resolve(analysis); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
});

it('exports the completed citation answer with every metric, citation and evidence gap', async () => {
  await openWorkbench();
  fireEvent.click(buttonWithText('专业模式'));
  const waiting = deferred<any>();
  (research.queryProfessionalRag as jest.Mock).mockReturnValueOnce(waiting.promise);
  await act(async () => { fireEvent.click(buttonWithText('查询引用')); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
  await act(async () => { waiting.resolve({ answer: '引用问答完整正文\n引用问答最后一段',
    metrics: analysis.key_metrics, citations: analysis.citations, confidence: 0.8,
    missing: ['待补证据1', '待补证据2', '待补证据3', '待补证据4'], disclaimer: '引用问答原有免责声明' }); });
  const exported = (within(screen.getByTestId('interpretation-export')).getByRole('textbox', { name: '导出全文', hidden: true }) as HTMLTextAreaElement).value;
  for (const value of ['引用问答最后一段', '完整数值7', '完整引用证据7', '待补证据4', '引用问答原有免责声明']) expect(exported).toContain(value);
  expect(screen.getByTestId('interpretation-export').getAttribute('data-scope')).toContain('research-rag:report-1');
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
});

it('discards a citation answer completed after its account has changed', async () => {
  await openWorkbench();
  fireEvent.click(buttonWithText('专业模式'));
  const waiting = deferred<any>();
  (research.queryProfessionalRag as jest.Mock).mockReturnValueOnce(waiting.promise);
  await act(async () => { fireEvent.click(buttonWithText('查询引用')); });
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  await act(async () => { waiting.resolve({ answer: 'A 账户迟到的引用问答', metrics: [], citations: [],
    missing: [], confidence: 0.8, disclaimer: '' }); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
  expect(screen.queryByText('A 账户迟到的引用问答')).not.toBeInTheDocument();
});

it('clears workbench results and its interpretation cache on account change without exporting a late response', async () => {
  await openWorkbench();
  await act(async () => { fireEvent.click(buttonWithText('一键 AI 分析')); });
  expectCompleteProfessionalExport();
  act(() => authenticateSession(user('B'), 'token-b', 'https://example.test'));
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
  const waiting = deferred<any>();
  (research.analyzeProfessionalReport as jest.Mock).mockReturnValueOnce(waiting.promise);
  await act(async () => { fireEvent.click(buttonWithText('一键 AI 分析')); });
  expect(research.analyzeProfessionalReport).toHaveBeenCalledTimes(2);
  act(() => authenticateSession(user('C'), 'token-c', 'https://example.test'));
  await act(async () => { waiting.resolve(analysis); });
  expect(screen.queryByTestId('interpretation-export')).not.toBeInTheDocument();
});
