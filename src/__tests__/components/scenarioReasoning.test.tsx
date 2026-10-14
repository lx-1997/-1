import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import ScenarioReasoning from '../../components/stock-intelligence/ScenarioReasoning';
import StockIntelligencePanel from '../../components/StockIntelligencePanel';
import type { PlatformResearchClaim, ScenarioDriver, ScenarioReasoning as Reasoning, StockIntelligence } from '../../components/stock-intelligence/types';
import { apiGet } from '../../services/apiClient';
import { authenticateSession, invalidateAuthSession } from '../../state/authSession';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn() }));
const get = apiGet as jest.Mock;
const onSource = jest.fn();
const onClaim = jest.fn();

// Synthetic contract fixtures only: these values are not a real company's prices or research.
function fixture(): StockIntelligence {
  const claim = (id: string, text: string, freshness: PlatformResearchClaim['freshness'] = 'current', body_state: PlatformResearchClaim['body_state'] = 'full'): PlatformResearchClaim => ({
    id, dimension: 'business', topic: '测试续约情况', claim: text, transmission: '测试续约影响盈利', transmission_basis: 'source_explicit', watch: '测试财报核验续约', watch_basis: 'source_explicit',
    stance: id === 'counter' ? 'counter' : 'support', role: 'opinion', body_state, relation_ids: ['company'],
    citations: [{ id: `citation-${id}`, source_id: `source-${id}`, document_id: `doc-${id}`, document_revision: id === 'mismatch' ? 'wrong-revision' : 'v1', paragraph: 2, quote: text, start: 0, end: text.length }],
    source_ids: [`source-${id}`], document_ids: [`doc-${id}`], as_of: freshness === 'historical' ? '2024-01-01' : '2026-10-13', freshness, needs_review: true, verification: 'source_opinion',
  });
  const claims = [claim('support', '测试纪要：续约有改善线索'), claim('counter', '测试研报：客户预算收缩'), claim('history', '测试历史纪要：早期业务增长', 'historical'), claim('summary', '测试已有摘要：增长机会', 'current', 'summary_only'), claim('mismatch', '不可误绑的测试原文')];
  const keys = ['business', 'fundamentals', 'earnings', 'technical', 'capital', 'news'] as const;
  const labels = ['商业模式', '财务质量', '盈利预期', '技术趋势', '资金面', '消息面'];
  const drivers: ScenarioDriver[] = keys.map((key, index) => ({
    key, label: labels[index], assessment: key === 'business' ? 'mixed' : 'unknown',
    observed: [{ id: `observed-${key}`, label: key === 'capital' ? '测试主力资金缺口' : `${labels[index]}测试信息`, statement: key === 'capital' ? '尚未取得可核验的测试资金序列。' : key === 'technical' ? '测试收盘价高于 MA20，但日线动量不等于盈利变化。' : `${labels[index]}已取得的测试信息。`, value: key === 'capital' ? null : key === 'technical' ? 105 : 5, unit: key === 'capital' ? '元' : 'CNY', as_of: key === 'capital' ? null : '2026-10-13', status: key === 'capital' ? 'missing' : 'available', origin: 'public', role: 'fact', direction: key === 'technical' ? 'support' : 'unknown', source_ids: [key === 'capital' ? 'flow' : key === 'technical' ? 'history' : 'forecast'], platform_claim_ids: [] }],
    condition: key === 'technical' ? '测试交易确认需要趋势连续兑现。' : key === 'capital' ? '取得有日期和同币种的资金数据后再判断。' : `${labels[index]}需要兑现的测试变化。`,
    transmission: key === 'technical' || key === 'capital' ? '仅作为交易确认，不直接写入 EPS 或情景股价。' : `${labels[index]}到收入、利润或估值的条件传导。`,
    source_ids: [key === 'capital' ? 'flow' : key === 'technical' ? 'history' : 'forecast'],
    platform_claim_ids: key === 'business' ? ['support', 'history', 'summary', 'mismatch'] : [], counter_claim_ids: key === 'business' ? ['counter'] : [], context_claim_ids: [],
    watch: [`核验${labels[index]}测试条件`], invalidation: [`${labels[index]}测试失效条件`], limitations: key === 'capital' ? ['测试资金数据缺失，不能当作净流入零。'] : [],
  }));
  const reasoning: Reasoning = {
    version: 1, headline: '测试续约兑现才可能支撑盈利与倍数同时改善。', assessment: 'mixed',
    numeric_bridge: { mode: 'ready', price: 138, currency: 'CNY', reference_price: 102, reference_as_of: '2026-10-13', forecast_year: 2027, base_eps: 5, base_pe_ttm: 20, eps_factor: 1.15, pe_factor: 1.2, scenario_eps: 5.75, scenario_pe_ttm: 24, base_price: 100, eps_effect: 15, pe_effect: 20, combined_effect: 3, upside_pct: 35.29, source_ids: ['forecast'], formula: '测试 EPS 5.75 × PE 24 = 138 CNY', note: '测试固定压力假设，不代表预测或实现概率。', gaps: [] },
    drivers, source_ids: ['forecast'], platform_claim_ids: ['support'], counter_claim_ids: ['counter'], context_claim_ids: ['history', 'summary'], watch: ['先核验测试续约及利润兑现'], invalidation: ['测试预算持续收缩使乐观条件失效'], limitations: ['当前只读取了测试授权节选'],
  };
  return {
    symbol: '600519', name: '测试公司', market: 'CN', currency: 'CNY', generated_at: '2026-10-13', quote: { price: 102, source: '测试报价', as_of: '2026-10-13' }, summary: { headline: '测试深度研究', stance: '等待验证', coverage: { available: 5, total: 6 } },
    valuation: { status: 'ready', method: '测试 EPS × PE', horizon: '测试未来 12 个月', formula: '测试 EPS × PE', note: '测试压力情景', anchor: { forecast_eps: 5, forecast_year: 2027, pe_ttm: 20 }, source_ids: ['forecast'], scenarios: [
      { key: 'optimistic', label: '乐观', price: 138, upside_pct: 35.29, assumptions: ['测试 EPS 5.75', '测试 PE 假设 24'], reasons: ['旧原因保留给旧数据'], invalidation: ['旧失效条件'], source_ids: ['forecast'], reasoning },
      { key: 'base', label: '中性', price: 100, upside_pct: -1.96, assumptions: [], reasons: [], invalidation: [], source_ids: ['forecast'], reasoning: { ...reasoning, headline: '测试中性需要盈利按基准兑现。', numeric_bridge: { ...reasoning.numeric_bridge, price: 100, eps_factor: 1, pe_factor: 1, scenario_eps: 5, scenario_pe_ttm: 20, eps_effect: 0, pe_effect: 0, combined_effect: 0, upside_pct: -1.96, formula: '测试 EPS 5 × PE 20 = 100 CNY' } } },
      { key: 'pessimistic', label: '悲观', price: 68, upside_pct: -33.33, assumptions: [], reasons: [], invalidation: [], source_ids: ['forecast'], reasoning: { ...reasoning, headline: '测试悲观来自盈利和倍数双重承压。', numeric_bridge: { ...reasoning.numeric_bridge, price: 68, eps_factor: 0.85, pe_factor: 0.8, scenario_eps: 4.25, scenario_pe_ttm: 16, eps_effect: -15, pe_effect: -20, combined_effect: 3, upside_pct: -33.33, formula: '测试 EPS 4.25 × PE 16 = 68 CNY' } } },
    ] },
    dimensions: keys.map((key, index) => ({ key, label: labels[index], signal: 'neutral', headline: `${labels[index]}测试摘要`, metrics: [], evidence: [], source_ids: [] })),
    sources: [
      { id: 'forecast', label: '测试公开盈利预测', as_of: '2026-10-13', status: 'available', url: 'https://example.test/forecast' }, { id: 'history', label: '测试日线来源', as_of: '2026-10-13', status: 'available' }, { id: 'flow', label: '测试资金来源', as_of: null, status: 'unavailable' },
      ...claims.map(item => ({ id: item.source_ids[0], label: `${item.id}测试资料`, status: 'available', as_of: item.as_of, url: `https://example.test/${item.id}` })),
    ],
    platform_research: { status: 'partial', generated_at: '2026-10-13', coverage: [], documents: claims.map(item => ({ id: item.document_ids[0], revision: 'v1', channel: 'notes', relation_ids: ['company'], role: item.role, title: `${item.id}测试资料标题`, published_at: item.as_of, body_state: item.body_state, visible_chars: 100, read_chars: 100, source_id: item.source_ids[0], source_name: '测试平台', excerpt: item.claim, relevant_paragraphs: 1, selected_claims: 1 })), relations: [{ id: 'company', kind: 'company', label: '测试公司', symbol: '600519', basis: '测试明确主体', verification: 'verified', citation_ids: [], source_ids: [] }], claims, citations: claims.flatMap(item => item.citations), conflicts: [], scenario_links: [], gaps: ['测试未覆盖全库'], sources: [], method: '测试权限过滤后的可见资料' },
    scenario_reasoning: { version: 1, method: '测试文字资料用于验证价格假设，技术和资金用于交易确认。', coverage: { dimensions: 6, observed_dimensions: 5, current_company_claims: 2, background_claims: 2 }, source_ids: ['forecast'], limitations: ['测试未全库读取'] },
    evidence: [], catalysts: [], risks: ['测试风险'], data_gaps: ['测试资金序列缺失'],
  };
}
function reasoningProps(data = fixture()) { return { data, reasoning: data.valuation.scenarios[0].reasoning!, onSource, onClaim }; }
function expandDriver(root: HTMLElement, label: string) {
  const summary = within(root).getByText(label, { selector: 'b' }).closest('summary')!;
  const details = summary.closest('details')!;
  fireEvent.click(summary);
  return details;
}
beforeEach(() => {
  invalidateAuthSession(); localStorage.clear(); jest.clearAllMocks(); document.body.style.overflow = '';
  authenticateSession({ id: 'scenario-test', username: 'scenario-test', email: null, role: 'viewer', is_active: true, created_at: '2026-10-13', membership: null }, 'test-scenario-token', 'https://example.test');
  get.mockResolvedValue({ data: fixture() });
});
afterEach(() => invalidateAuthSession());

it('shows the supplied numeric price bridge separately from evidence and retains fixed-pressure semantics', () => {
  render(<ScenarioReasoning {...reasoningProps()} />);
  const bridge = within(screen.getByRole('region', { name: '价格形成过程' }));
  expect(bridge.getByText('固定压力假设')).toBeVisible();
  expect(bridge.getByText('测试 EPS 5.75 × PE 24 = 138 CNY')).toBeVisible();
  for (const [label, value] of [['基准 EPS × TTM PE', '100.00'], ['盈利单独作用', '+15.00'], ['倍数单独作用', '+20.00'], ['二者交互作用', '+3.00'], ['合计情景价', '138.00']]) {
    expect(bridge.getByText(label).parentElement).toHaveTextContent(value);
    expect(bridge.getByText(label).parentElement).toHaveTextContent('CNY');
  }
  expect(bridge.getByText(/经营资料用于检验假设是否成立/)).toBeVisible();
  expect(bridge.getByText('测试固定压力假设，不代表预测或实现概率。')).toBeVisible();
  fireEvent.click(bridge.getByRole('button', { name: '核对测试公开盈利预测来源' }));
  expect(onSource).toHaveBeenCalledWith('forecast');
});

it('keeps missing price inputs and fund observations as gaps instead of filling zero or deriving an equity target', () => {
  const props = reasoningProps();
  props.reasoning.numeric_bridge = { ...props.reasoning.numeric_bridge, mode: 'insufficient', price: null, base_eps: null, scenario_eps: null, gaps: ['测试未取得可核验 EPS'] };
  render(<ScenarioReasoning {...props} />);
  const bridge = within(screen.getByRole('region', { name: '价格形成过程' }));
  expect(bridge.getByText(/可核验的数值输入不足，暂不估价/)).toBeVisible();
  expect(bridge.getByText('测试未取得可核验 EPS')).toBeVisible();
  expect(bridge.queryByText('合计情景价')).not.toBeInTheDocument();
  const capital = expandDriver(screen.getByRole('region', { name: '六维情景传导' }), '资金面');
  expect(within(capital).getByText('数据缺口')).toBeVisible();
  expect(within(capital).getByText('测试资金数据缺失，不能当作净流入零。')).toBeVisible();
  expect(within(capital).getByText('对交易确认的传导')).toBeVisible();
  expect(capital).not.toHaveTextContent(/0\.00|净流入为零/);
});

it('shows comparable dated sell-side EPS ranges as samples rather than probabilities or a replacement target', () => {
  const props = reasoningProps();
  props.reasoning.numeric_bridge.forecast_sample = { year: 2027, currency: 'CNY', eps_low: 4.5, eps_median: 5, eps_high: 5.3, broker_count: 3, as_of: '2026-10-12', source_ids: ['forecast'], scenario_vs_sample: 'above', note: '测试同年度样本，不代表实现概率。' };
  render(<ScenarioReasoning {...props} />);
  const sample = within(screen.getByRole('region', { name: '卖方盈利样本对照' }));
  expect(sample.getByText(/2027 年 EPS：4.50 — 5.30 CNY/)).toBeVisible();
  expect(sample.getByText(/中位数 5.00 CNY · 3 家机构样本/)).toBeVisible();
  expect(sample.getByText(/截至 2026-10-12 · 情景 EPS 高于本次样本上沿/)).toBeVisible();
  expect(sample.getByText('测试同年度样本，不代表实现概率。')).toBeVisible();
  expect(screen.getByText('合计情景价').parentElement).toHaveTextContent('138.00');
  fireEvent.click(sample.getByRole('button', { name: '核对测试公开盈利预测来源' }));
  expect(onSource).toHaveBeenCalledWith('forecast');
});

it('does not describe sensitivity as a forecast EPS and keeps technical evidence a trade-confirmation chain', () => {
  const props = reasoningProps();
  props.reasoning.numeric_bridge = { ...props.reasoning.numeric_bridge, mode: 'sensitivity', base_eps: null, scenario_eps: null, formula: '测试参考价 × 盈利变化 × 倍数变化' };
  render(<ScenarioReasoning {...props} />);
  const bridge = within(screen.getByRole('region', { name: '价格形成过程' }));
  expect(bridge.getByText('当前价格基准')).toBeVisible();
  expect(bridge.queryByText('情景 EPS')).not.toBeInTheDocument();
  const technical = expandDriver(screen.getByRole('region', { name: '六维情景传导' }), '技术趋势');
  expect(within(technical).getByText('交易确认维度，不直接改写 EPS 或情景价')).toBeVisible();
  expect(within(technical).getByText('对交易确认的传导')).toBeVisible();
  expect(within(technical).getByText('测试交易确认需要趋势连续兑现。')).toBeVisible();
  expect(within(technical).getByText('仅作为交易确认，不直接写入 EPS 或情景股价。')).toBeVisible();
});

it('separates company support and counterevidence, downgrades history and summaries, and rejects revision mismatches', () => {
  render(<ScenarioReasoning {...reasoningProps()} />);
  const business = expandDriver(screen.getByRole('region', { name: '六维情景传导' }), '商业模式');
  const support = within(business.querySelector<HTMLElement>('[data-evidence-role="support"]')!);
  const counter = within(business.querySelector<HTMLElement>('[data-evidence-role="counter"]')!);
  const context = within(business.querySelector<HTMLElement>('[data-evidence-role="context"]')!);
  expect(support.getByRole('button', { name: '查看论据：测试纪要：续约有改善线索' })).toBeVisible();
  expect(counter.getByRole('button', { name: '查看论据：测试研报：客户预算收缩' })).toBeVisible();
  expect(support.queryByText('测试历史纪要：早期业务增长')).not.toBeInTheDocument();
  expect(support.queryByText('测试已有摘要：增长机会')).not.toBeInTheDocument();
  expect(context.getByRole('button', { name: '查看论据：测试历史纪要：早期业务增长' })).toBeVisible();
  expect(context.getByRole('button', { name: '查看论据：测试已有摘要：增长机会' })).toBeVisible();
  expect(context.getByText(/2024-01-01 · 不作当前方向依据/)).toBeVisible();
  expect(screen.queryByRole('button', { name: /不可误绑的测试原文/ })).not.toBeInTheDocument();
  fireEvent.click(counter.getByRole('button', { name: '查看论据：测试研报：客户预算收缩' }));
  expect(onClaim).toHaveBeenCalledWith('counter');
});

it.each(['quote', 'document', 'source'])('does not open a platform claim whose %s no longer binds the visible citation', field => {
  const props = reasoningProps();
  const claim = props.data.platform_research!.claims.find(item => item.id === 'support')!;
  if (field === 'quote') claim.citations = [{ ...claim.citations[0], quote: '不能冒充原文的测试改写' }];
  if (field === 'document') claim.document_ids = ['doc-counter'];
  if (field === 'source') claim.source_ids = ['source-counter'];
  render(<ScenarioReasoning {...props} />);
  const business = expandDriver(screen.getByRole('region', { name: '六维情景传导' }), '商业模式');
  expect(within(business).queryByRole('button', { name: '查看论据：测试纪要：续约有改善线索' })).not.toBeInTheDocument();
  expect(within(business).queryByRole('button', { name: /不能冒充原文/ })).not.toBeInTheDocument();
  expect(within(business).getByRole('button', { name: '查看论据：测试研报：客户预算收缩' })).toBeVisible();
});

it('exposes every scenario reason on the overview and restores expanded reasoning after inspecting its exact source', async () => {
  render(<StockIntelligencePanel symbol="600519" name="测试公司" loggedIn username="scenario-test" onRequireLogin={jest.fn()} />);
  expect(await screen.findByText('测试深度研究')).toBeVisible();
  const data = fixture();
  for (const scenario of data.valuation.scenarios) {
    const card = within(screen.getByRole('article', { name: `${scenario.label}股价情景` }));
    expect(card.getByText(scenario.reasoning!.headline)).toBeVisible();
    expect(card.getByRole('button', { name: `查看${scenario.label}情景依据` })).toBeVisible();
    expect(card.getByText('原因与具体依据')).toBeVisible();
  }
  fireEvent.click(screen.getByRole('button', { name: '查看乐观情景依据' }));
  const dialog = screen.getByRole('dialog', { name: '乐观情景' });
  const driver = expandDriver(dialog, '商业模式');
  await waitFor(() => expect(driver).toHaveAttribute('open'));
  // A toggle event records native details expansion before the nested source view replaces it.
  fireEvent(driver, new Event('toggle'));
  const body = dialog.querySelector<HTMLDivElement>('.si-inspector-body')!;
  body.scrollTop = 320;
  fireEvent.click(within(driver).getByRole('button', { name: '查看论据：测试纪要：续约有改善线索' }));
  const claim = screen.getByRole('dialog', { name: '平台完整论据' });
  expect(within(claim).getByText('测试纪要：续约有改善线索', { selector: 'blockquote' })).toBeVisible();
  fireEvent.click(within(claim).getByRole('button', { name: '查看此段来源 ↗' }));
  const source = screen.getByRole('dialog', { name: 'support测试资料' });
  expect(within(source).getByText('support测试资料标题')).toBeVisible();
  expect(within(source).getByRole('link', { name: '打开原始来源 ↗' })).toHaveAttribute('href', 'https://example.test/support');
  expect(within(source).queryByText('测试研报：客户预算收缩')).not.toBeInTheDocument();
  fireEvent.click(within(source).getByRole('button', { name: '← 返回平台完整论据' }));
  fireEvent.click(within(screen.getByRole('dialog', { name: '平台完整论据' })).getByRole('button', { name: '← 返回乐观情景' }));
  const restored = screen.getByRole('dialog', { name: '乐观情景' });
  expect(within(restored).getByText('商业模式', { selector: 'b' }).closest('details')).toHaveAttribute('open');
  expect(restored.querySelector<HTMLDivElement>('.si-inspector-body')!.scrollTop).toBe(320);
  fireEvent.keyDown(within(restored).getByRole('heading', { name: '乐观情景' }), { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '查看乐观情景依据' })).toHaveFocus();
});

it('keeps six logical summaries in reading order and immediately clears expanded protected content on logout', async () => {
  render(<StockIntelligencePanel symbol="600519" name="测试公司" loggedIn username="scenario-test" onRequireLogin={jest.fn()} />);
  expect(await screen.findByText('测试深度研究')).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: '查看乐观情景依据' }));
  const dialog = screen.getByRole('dialog', { name: '乐观情景' });
  const summaries = Array.from(dialog.querySelectorAll('.si-reasoning-driver > summary'));
  expect(summaries.map(summary => summary.querySelector('b')?.textContent)).toEqual(['商业模式', '财务质量', '盈利预期', '技术趋势', '资金面', '消息面']);
  expandDriver(dialog, '盈利预期');
  expect(within(dialog).getByText('核验盈利预期测试条件')).toBeVisible();
  act(() => invalidateAuthSession());
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('测试续约兑现才可能支撑盈利与倍数同时改善。')).not.toBeInTheDocument();
  expect(screen.queryByText('核验盈利预期测试条件')).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe('');
});
