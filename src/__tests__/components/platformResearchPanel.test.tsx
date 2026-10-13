import React from 'react';
import { act, fireEvent, render, screen, within } from '@testing-library/react';
import StockIntelligencePanel from '../../components/StockIntelligencePanel';
import PlatformResearchPanel from '../../components/stock-intelligence/PlatformResearchPanel';
import type {
  IntelligenceSource, PlatformResearch, PlatformResearchClaim, PlatformResearchCoverage,
  PlatformResearchDocument, StockIntelligence,
} from '../../components/stock-intelligence/types';
import { apiGet } from '../../services/apiClient';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, invalidateAuthSession, updateAuthAccount } from '../../state/authSession';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn() }));
const get = apiGet as jest.Mock;
const props = { symbol: '600519', name: '测试公司', loggedIn: true, username: 'research-a', onRequireLogin: jest.fn() };
const account = (id: string, tier?: string): AuthUser => ({
  id, username: id, email: null, role: 'viewer', is_active: true, created_at: '2026-10-13',
  membership: tier ? { tier } as AuthUser['membership'] : null,
});
const login = (id = 'research-a') => authenticateSession(account(id), `synthetic-token-${id}`, 'https://example.test');

// All companies, documents, quotations and prices below are declared synthetic
// fixtures. Citation revisions bind to this test's visible document revision.
function claim(id: string, overrides: Partial<PlatformResearchClaim> = {}): PlatformResearchClaim {
  const quotation = `测试公司${id}订单增长，带动收入改善；关注交付与现金回款。`;
  return {
    id, dimension: 'business', topic: `测试${id}经营主线`, claim: quotation,
    transmission: `测试${id}经营传导`, transmission_basis: 'source_explicit',
    watch: `核验测试${id}现金回款`, watch_basis: 'source_explicit',
    stance: 'support', role: 'fact', body_state: 'full', relation_ids: ['company'],
    citations: [{ id: `${id}-citation`, source_id: `${id}-source`, document_id: `${id}-document`,
      document_revision: `${id}-synthetic-revision`, paragraph: 4, quote: quotation, start: 130, end: 130 + quotation.length }],
    source_ids: [`${id}-source`], document_ids: [`${id}-document`], as_of: '2026-10-12',
    freshness: 'current', period: '2026H1', currency: 'CNY', needs_review: true,
    verification: 'source_reported', ...overrides,
  };
}
function coverage(overrides: Partial<PlatformResearchCoverage> = {}): PlatformResearchCoverage {
  return {
    key: 'site_articles', label: '测试平台文章', status: 'partial', scanned: 1200, matched: 4,
    read: 3, deduped: 1, complete: false, limit_hit: true, body_states: { full: 2, excerpt: 1 },
    read_chars: 18000, relevant_paragraphs: 7, selected_claims: 4,
    period: { from: '2026-06-30', to: '2026-10-12' },
    window: { days: 365, from: '2025-10-13', to: '2026-10-13' },
    source_ids: ['甲-source'], scope: 'request_visible', reason: '测试有界检索达到扫描上限，未声称全库读取',
    ...overrides,
  };
}
function research(items: PlatformResearchClaim[] = [claim('甲'), claim('乙', { dimension: 'fundamentals', stance: 'counter', role: 'opinion' }), claim('丙', { transmission_basis: 'rule_hypothesis', watch_basis: 'rule_hypothesis' })]): PlatformResearch {
  const documents: PlatformResearchDocument[] = items.map(item => ({
    id: item.document_ids[0], revision: item.citations[0].document_revision, channel: 'site_articles',
    relation_ids: item.relation_ids, role: item.role, title: `测试公司${item.id}原始资料`,
    published_at: item.as_of, body_state: item.body_state, visible_chars: 2400, read_chars: 2400,
    source_id: item.source_ids[0], source_name: `测试平台来源${item.id}`, excerpt: item.citations[0].quote,
    url: `https://example.test/research/${encodeURIComponent(item.id)}`, relevant_paragraphs: 3, selected_claims: 1,
  }));
  const sources: IntelligenceSource[] = items.map(item => ({
    id: item.source_ids[0], label: `测试平台来源${item.id}`, status: item.freshness === 'historical' ? 'stale' : 'available',
    as_of: item.as_of, fetched_at: '2026-10-13T02:00:00Z', url: `https://example.test/source/${encodeURIComponent(item.id)}`,
    freshness: item.freshness === 'historical' ? '测试历史快照，需复核' : '测试资料日期已提供',
  }));
  return {
    status: 'partial', generated_at: '2026-10-13T02:00:00Z', coverage: [coverage(), coverage({
      key: 'celebrity_views', label: '测试白名单观点', status: 'access_required', scanned: 0, matched: 0, read: 0,
      selected_claims: 0, deduped: 0, read_chars: 0, relevant_paragraphs: 0, body_states: {}, limit_hit: false,
      period: { from: null, to: null }, source_ids: [], scope: 'whitelisted', reason: '当前账号未授权，不返回受限标题或条数',
    })], documents, sources, claims: items,
    relations: [{ id: 'company', kind: 'company', label: '测试公司', symbol: '600519', basis: '测试主体对应', verification: 'verified', citation_ids: items.map(item => item.citations[0].id), source_ids: sources.map(item => item.id) }],
    citations: items.flatMap(item => item.citations), conflicts: [],
    scenario_links: ['optimistic', 'base', 'pessimistic'].map(key => ({
      key: key as 'optimistic' | 'base' | 'pessimistic',
      support_claim_ids: items.filter(item => item.stance === 'support' && item.freshness === 'current' && item.body_state === 'full').map(item => item.id),
      counter_claim_ids: items.filter(item => item.stance === 'counter' && item.freshness === 'current' && item.body_state === 'full').map(item => item.id),
      context_claim_ids: items.filter(item => item.freshness !== 'current' || item.body_state !== 'full').map(item => item.id),
      watch: ['核验测试经营兑现'], source_ids: sources.map(item => item.id),
    })), gaps: ['测试名人观点权限不足', '测试未缓存页面仍有覆盖缺口'],
    method: '测试合成取证：先核验账号权限，再读取可见全文；经营规则是假设，待核验。',
  };
}
function snapshot(platform = research(), headline = '测试平台合成研究快照'): { data: StockIntelligence } {
  return { data: {
    symbol: '600519', name: '测试公司', market: 'CN', currency: 'CNY', generated_at: '2026-10-13T02:00:00Z',
    quote: { price: 100, change_percent: 1, as_of: '2026-10-13', source: '测试行情' },
    summary: { headline, stance: '测试需要复核', coverage: { available: 2, total: 6 } },
    valuation: { status: 'sensitivity', method: '测试参考价情景敏感性', horizon: '测试未来12个月', formula: '测试固定参数',
      note: '合成测试价格，非实际行情或预测', anchor: { price: 100, pe_ttm: 20 }, source_ids: ['quote'],
      scenarios: [
        { key: 'optimistic', label: '乐观', price: 130, upside_pct: 30, assumptions: ['测试利润增长'], reasons: ['测试假设'], invalidation: ['测试回款不达标'], source_ids: ['quote'] },
        { key: 'base', label: '中性', price: 100, upside_pct: 0, assumptions: ['测试利润平稳'], reasons: ['测试观察'], invalidation: ['测试订单减少'], source_ids: ['quote'] },
        { key: 'pessimistic', label: '悲观', price: 70, upside_pct: -30, assumptions: ['测试利润承压'], reasons: ['测试压力'], invalidation: ['测试经营改善'], source_ids: ['quote'] },
      ] },
    dimensions: [], evidence: [], catalysts: [], risks: ['测试需复核'], data_gaps: platform.gaps,
    sources: [{ id: 'quote', label: '测试行情', status: 'available', as_of: '2026-10-13', url: null }, ...platform.sources],
    platform_research: platform,
  } };
}

function direct(data = snapshot().data) {
  const callbacks = { onSource: jest.fn(), onClaim: jest.fn(), onCoverage: jest.fn(), onConflict: jest.fn() };
  render(<PlatformResearchPanel data={data} {...callbacks} />);
  return callbacks;
}
async function integrated(value = snapshot()) {
  get.mockResolvedValue(value);
  render(<StockIntelligencePanel {...props} />);
  await screen.findByText(value.data.summary.headline);
  return screen.getByRole('region', { name: '平台研究主线' });
}
function open(trigger: HTMLElement, title: string) {
  act(() => trigger.focus()); fireEvent.click(trigger);
  return screen.getByRole('dialog', { name: title });
}
function body(dialog: HTMLElement) { return dialog.querySelector<HTMLDivElement>('.si-inspector-body')!; }
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>(done => { resolve = done; });
  return { promise, resolve };
}

beforeEach(() => {
  invalidateAuthSession(); localStorage.clear(); document.body.style.overflow = ''; jest.clearAllMocks(); get.mockReset();
  login(); get.mockResolvedValue(snapshot());
});
afterEach(() => { invalidateAuthSession(); });

it('shows only three distinct citation tracks and makes additional evidence expandable', () => {
  const first = claim('甲');
  const duplicate = { ...first, id: '同段不同维度', dimension: 'earnings', topic: '重复的盈利视角' };
  direct(snapshot(research([first, duplicate, claim('乙'), claim('丙'), claim('丁', { transmission_basis: 'rule_hypothesis' })])).data);
  const panel = screen.getByRole('region', { name: '平台研究主线' });
  expect(within(panel).getAllByRole('button', { name: '完整论据 ↗' })).toHaveLength(3);
  expect(within(panel).queryByText('重复的盈利视角')).not.toBeInTheDocument();
  expect(within(panel).getByRole('button', { name: /测试丁经营主线/ })).not.toBeVisible();
  fireEvent.click(within(panel).getByText('更多与背景线索'));
  expect(within(panel).getByRole('button', { name: /测试丁经营主线/ })).toBeVisible();
});

it('features the direct company fact and keeps both context lines readable and traceable behind the disclosure', async () => {
  const directFact = claim('公司事实');
  const industry = claim('行业背景', { role: 'context', relation_ids: ['industry'] });
  const peer = claim('同行背景', { role: 'context', relation_ids: ['peer'] });
  const result = research([industry, directFact, peer]);
  result.relations.push(
    { id: 'industry', kind: 'industry', label: '测试新能源行业', basis: '测试行业正文关联', verification: 'context', citation_ids: [industry.citations[0].id], source_ids: industry.source_ids },
    { id: 'peer', kind: 'peer', label: '测试同行公司', symbol: '000001', basis: '测试同行正文提及', verification: 'candidate', citation_ids: [peer.citations[0].id], source_ids: peer.source_ids },
  );
  const panel = await integrated(snapshot(result));
  expect(within(panel).getAllByRole('button', { name: '完整论据 ↗' })).toHaveLength(1);
  expect(within(panel).getByRole('heading', { name: directFact.topic })).toBeVisible();
  expect(within(panel).queryByRole('heading', { name: industry.topic })).not.toBeInTheDocument();
  expect(within(panel).queryByRole('heading', { name: peer.topic })).not.toBeInTheDocument();
  const industryEntry = within(panel).getByRole('button', { name: new RegExp(industry.topic) });
  const peerEntry = within(panel).getByRole('button', { name: new RegExp(peer.topic) });
  expect(industryEntry).not.toBeVisible(); expect(peerEntry).not.toBeVisible();
  fireEvent.click(within(panel).getByText('更多与背景线索'));
  expect(industryEntry).toBeVisible(); expect(peerEntry).toBeVisible();
  expect(industryEntry).toHaveTextContent(industry.claim);
  expect(peerEntry).toHaveTextContent(peer.claim);
  const detail = open(industryEntry, '平台完整论据');
  expect(within(detail).getByText(industry.citations[0].quote)).toBeVisible();
  expect(within(detail).getByText('背景线索')).toBeVisible();
  expect(within(detail).getByText('测试新能源行业 · 行业 · 关联背景')).toBeVisible();
  fireEvent.click(within(detail).getByRole('button', { name: '关闭详情' }));
  expect(industryEntry).toHaveFocus(); expect(peerEntry).toBeVisible();
});

it('labels source statements, condition rules and forecasts without treating them as verified facts', () => {
  const forecast = claim('预测', { role: 'opinion', verification: 'source_forecast', transmission_basis: 'rule_hypothesis' });
  direct(snapshot(research([forecast])).data);
  expect(screen.getByText('来源观点')).toBeVisible();
  expect(screen.getByText('预测观点 · 尚未兑现')).toBeVisible();
  expect(screen.getByText('规则假设 · 需验证')).toBeVisible();
  expect(screen.queryByText('已核实事实')).not.toBeInTheDocument();
});

it.each([
  ['full', '可见全文'], ['excerpt', '可见节选'], ['teaser', '仅导语'], ['summary_only', '仅摘要'], ['title_only', '仅标题'],
] as const)('labels the actual %s reading boundary', (state, label) => {
  direct(snapshot(research([claim('甲', { body_state: state })])).data);
  expect(screen.getByText(label)).toBeVisible();
});

it('ignores historical and summary-only directions even if a legacy overview link claims support', () => {
  const old = claim('历史', { freshness: 'historical', as_of: '2024-06-30' });
  const summary = claim('摘要', { role: 'analysis', body_state: 'summary_only' });
  const result = research([old, summary]);
  result.scenario_links.forEach(link => { link.support_claim_ids = ['历史', '摘要']; });
  direct(snapshot(result).data);
  expect(screen.getByText('历史资料 · 仅作背景')).toBeVisible();
  expect(screen.getAllByText('仅供研究背景，未作为当前情景的方向依据。')).toHaveLength(2);
  expect(screen.queryByText(/乐观 · 条件支持/)).not.toBeInTheDocument();
});

it('keeps full sector text with only a company context relation out of directional scenario groups', async () => {
  const related = claim('正文关联', { role: 'context' });
  const result = research([related]); result.relations[0].verification = 'context';
  result.scenario_links[0].support_claim_ids = [related.id]; result.scenario_links[0].context_claim_ids = [];
  const panel = await integrated(snapshot(result));
  expect(within(panel).getByText('公司关联')).toBeVisible();
  expect(within(panel).getByText('仅供研究背景，未作为当前情景的方向依据。')).toBeVisible();
  expect(within(panel).queryByText(/乐观 · 条件支持/)).not.toBeInTheDocument();
  const scenario = open(screen.getByRole('button', { name: '查看乐观情景依据' }), '乐观情景');
  const support = within(scenario).getByRole('heading', { name: '支持该情景的条件' }).parentElement!;
  expect(within(support).queryByRole('button', { name: `${related.claim} ↗` })).not.toBeInTheDocument();
  const background = within(scenario).getByRole('heading', { name: '历史、摘要与关联背景' }).parentElement!;
  expect(within(background).getByRole('button', { name: `${related.claim} ↗` })).toBeVisible();
});

it('shows gated coverage and partial limits separately from the retrieval window and source publication dates', () => {
  direct();
  fireEvent.click(screen.getByText('各类平台资料的读取范围'));
  const rows = screen.getAllByRole('row');
  const article = rows.find(row => within(row).queryByText('测试平台文章'))!;
  expect(within(article).getByText('最近 365 天')).toBeVisible();
  expect(within(article).getByText('资料日期：2026-06-30 — 2026-10-12')).toBeVisible();
  expect(within(article).getByText('部分覆盖')).toBeVisible();
  expect(within(article).getByText('可见全文 2 · 可见节选 1')).toBeVisible();
  const gated = rows.find(row => within(row).queryByText('测试白名单观点'))!;
  expect(within(gated).getByText('当前账号未授权')).toBeVisible();
  expect(within(gated).getByText('无可读内容')).toBeVisible();
  expect(within(gated).getByText('无可核验时点', { exact: false })).toBeVisible();
});

it('passes the selected claim, source, coverage and conflict identities to the parent', () => {
  const result = research();
  result.conflicts.push({ id: 'conflict', dimension: 'fundamentals', topic: '测试利润观点', claim_ids: ['甲', '乙'], type: 'unverified_comparability', summary: '测试比较口径尚未核验', verification: 'candidate', source_ids: ['甲-source', '乙-source'] });
  const callbacks = direct(snapshot(result).data);
  fireEvent.click(screen.getAllByRole('button', { name: '完整论据 ↗' })[0]);
  expect(callbacks.onClaim).toHaveBeenCalledWith('甲');
  fireEvent.click(screen.getByRole('button', { name: /测试平台来源甲/ }));
  expect(callbacks.onSource).toHaveBeenCalledWith('甲-source');
  fireEvent.click(screen.getByRole('button', { name: '检索覆盖 ↗' }));
  expect(callbacks.onCoverage).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: /测试利润观点 · 比较口径待核/ }));
  expect(callbacks.onConflict).toHaveBeenCalledWith('conflict');
});

it('returns from a claim source to its original scroll and restores the overview trigger focus on close', async () => {
  const panel = await integrated();
  const trigger = within(panel).getAllByRole('button', { name: '完整论据 ↗' })[0];
  const detail = open(trigger, '平台完整论据');
  body(detail).scrollTop = 218;
  expect(within(detail).getByText('测试公司甲原始资料 · 2026-10-12 · 第 4 段')).toBeVisible();
  fireEvent.click(within(detail).getByRole('button', { name: '查看此段来源 ↗' }));
  const source = screen.getByRole('dialog', { name: '测试平台来源甲' });
  expect(within(source).getByRole('link', { name: '打开原始来源 ↗' })).toHaveAttribute('href', 'https://example.test/source/%E7%94%B2');
  fireEvent.click(within(source).getByRole('button', { name: '← 返回平台完整论据' }));
  const restored = screen.getByRole('dialog', { name: '平台完整论据' });
  expect(body(restored).scrollTop).toBe(218);
  fireEvent.click(within(restored).getByRole('button', { name: '关闭详情' }));
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe('');
});

it('jumps from the first-screen platform count to a focused evidence section after scenarios and history', async () => {
  const panel = await integrated();
  const anchor = panel.closest<HTMLElement>('.si-platform-anchor')!;
  const scroll = jest.fn(); anchor.scrollIntoView = scroll;
  const entry = screen.getByRole('button', { name: '查看平台研究主线' });
  expect(entry).toHaveTextContent('平台 3 份资料');
  const scenario = screen.getByRole('article', { name: '乐观股价情景' });
  const history = screen.getByRole('region', { name: '历史价格走势' });
  expect(scenario.compareDocumentPosition(anchor) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(history.compareDocumentPosition(anchor) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  fireEvent.click(entry);
  expect(anchor).toHaveFocus();
  expect(scroll).toHaveBeenCalledWith({ block: 'start', behavior: 'smooth' });
  expect(screen.getByRole('tab', { name: '研究概览' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(1);
});

it('traces only the dimension-linked platform claim and returns through the source to the original dimension', async () => {
  const result = snapshot();
  result.data.dimensions = [{ key: 'business', label: '商业模式', signal: 'neutral', headline: '测试公司业务结构',
    metrics: [{ label: '主营业务', value: '合成测试业务' }], evidence: ['测试经营结构需核对'],
    source_ids: ['甲-source'], platform_claim_ids: ['甲'] }];
  await integrated(result);
  const trigger = screen.getByRole('button', { name: '查看商业模式研究依据' });
  const dimension = open(trigger, '商业模式'); body(dimension).scrollTop = 68;
  const argumentsSection = within(dimension).getByRole('heading', { name: '平台段落论据' }).closest('section')!;
  expect(within(argumentsSection).getByRole('button', { name: `${claim('甲').claim} ↗` })).toBeVisible();
  expect(within(argumentsSection).queryByRole('button', { name: `${claim('乙').claim} ↗` })).not.toBeInTheDocument();
  fireEvent.click(within(argumentsSection).getByRole('button', { name: `${claim('甲').claim} ↗` }));
  const detail = screen.getByRole('dialog', { name: '平台完整论据' }); body(detail).scrollTop = 124;
  fireEvent.click(within(detail).getByRole('button', { name: '查看此段来源 ↗' }));
  const source = screen.getByRole('dialog', { name: '测试平台来源甲' });
  expect(within(source).getByText(claim('甲').citations[0].quote)).toBeVisible();
  fireEvent.click(within(source).getByRole('button', { name: '← 返回平台完整论据' }));
  const returnedClaim = screen.getByRole('dialog', { name: '平台完整论据' });
  expect(body(returnedClaim).scrollTop).toBe(124);
  fireEvent.click(within(returnedClaim).getByRole('button', { name: '← 返回商业模式' }));
  const returnedDimension = screen.getByRole('dialog', { name: '商业模式' });
  expect(body(returnedDimension).scrollTop).toBe(68);
  fireEvent.keyDown(returnedDimension, { key: 'Escape' }); expect(trigger).toHaveFocus();
});

it('shows only this source document current revision quotes and deduplicates identical citation identities', async () => {
  const result = research([claim('甲')]);
  const current = result.citations[0];
  result.citations.push(
    { ...current },
    { ...current, id: 'old-revision', document_revision: 'previous-account-or-document-revision', quote: '不展示旧版本正文引用' },
    { ...current, id: 'foreign-document', document_id: 'another-document', quote: '不展示别的文档正文引用' },
    { ...current, id: 'foreign-source', source_id: 'another-source', quote: '不展示别的来源正文引用' },
  );
  const panel = await integrated(snapshot(result));
  const source = open(within(panel).getByRole('button', { name: /测试平台来源甲/ }), '测试平台来源甲');
  expect(within(source).getAllByText(current.quote)).toHaveLength(1);
  expect(within(source).getByText('第 4 段 · 本次可见引用')).toBeVisible();
  expect(within(source).getByText(/文本版本 甲-synthetic-revision/)).toBeVisible();
  expect(within(source).queryByText('不展示旧版本正文引用')).not.toBeInTheDocument();
  expect(within(source).queryByText('不展示别的文档正文引用')).not.toBeInTheDocument();
  expect(within(source).queryByText('不展示别的来源正文引用')).not.toBeInTheDocument();
});

it('supports scenario → claim → source → claim → scenario without losing either scroll position', async () => {
  await integrated();
  const trigger = screen.getByRole('button', { name: '查看乐观情景依据' });
  const scenario = open(trigger, '乐观情景'); body(scenario).scrollTop = 111;
  fireEvent.click(within(scenario).getByRole('button', { name: `${claim('甲').claim} ↗` }));
  const detail = screen.getByRole('dialog', { name: '平台完整论据' }); body(detail).scrollTop = 222;
  fireEvent.click(within(detail).getByRole('button', { name: '查看此段来源 ↗' }));
  fireEvent.click(within(screen.getByRole('dialog', { name: '测试平台来源甲' })).getByRole('button', { name: '← 返回平台完整论据' }));
  const returnedClaim = screen.getByRole('dialog', { name: '平台完整论据' });
  expect(body(returnedClaim).scrollTop).toBe(222);
  fireEvent.click(within(returnedClaim).getByRole('button', { name: '← 返回乐观情景' }));
  const returnedScenario = screen.getByRole('dialog', { name: '乐观情景' });
  expect(body(returnedScenario).scrollTop).toBe(111);
  fireEvent.keyDown(returnedScenario, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus();
});

it('exposes permission and scan gaps through the coverage inspector rather than claiming whole-platform completeness', async () => {
  const panel = await integrated();
  const trigger = within(panel).getByRole('button', { name: '检索覆盖 ↗' });
  const dialog = open(trigger, '平台取证范围与缺口');
  expect(within(dialog).getByText('测试平台文章 · 部分覆盖')).toBeVisible();
  expect(within(dialog).getByText('测试白名单观点 · 当前账号未授权')).toBeVisible();
  expect(within(dialog).getByText(/实际资料日期 2026-06-30 — 2026-10-12/)).toBeVisible();
  const articleScope = within(dialog).getByText('测试平台文章 · 部分覆盖').closest('section')!;
  expect(within(articleScope).getByText(/检索范围：最近 365 天/)).toBeVisible();
  expect(within(dialog).getByText('测试未缓存页面仍有覆盖缺口')).toBeVisible();
  fireEvent.keyDown(dialog, { key: 'Escape' }); expect(trigger).toHaveFocus();
});

it('keeps historical and summary claims in the scenario background even if their linked direction is stale', async () => {
  const old = claim('历史', { freshness: 'historical', as_of: '2024-06-30' });
  const summary = claim('摘要', { role: 'analysis', body_state: 'summary_only' });
  const result = research([claim('甲'), old, summary]);
  result.scenario_links[0].support_claim_ids = ['甲', '历史', '摘要'];
  result.scenario_links[0].context_claim_ids = ['历史', '摘要'];
  await integrated(snapshot(result));
  const scenario = open(screen.getByRole('button', { name: '查看乐观情景依据' }), '乐观情景');
  const support = within(scenario).getByRole('heading', { name: '支持该情景的条件' }).parentElement!;
  expect(within(support).getByRole('button', { name: `${claim('甲').claim} ↗` })).toBeVisible();
  expect(within(support).queryByRole('button', { name: `${old.claim} ↗` })).not.toBeInTheDocument();
  expect(within(support).queryByRole('button', { name: `${summary.claim} ↗` })).not.toBeInTheDocument();
  const background = within(scenario).getByRole('heading', { name: '历史、摘要与关联背景' }).parentElement!;
  expect(within(background).getByRole('button', { name: `${old.claim} ↗` })).toBeVisible();
  expect(within(background).getByRole('button', { name: `${summary.claim} ↗` })).toBeVisible();
});

it('shows differing period and unknown currency as a comparison question, with each source claim traceable', async () => {
  const first = claim('甲', { period: '2026H1', currency: 'CNY' });
  const second = claim('乙', { period: '2025FY', currency: null, stance: 'counter', role: 'opinion' });
  const result = research([first, second]);
  result.conflicts.push({ id: 'comparison', dimension: 'fundamentals', topic: '测试利润口径', claim_ids: ['甲', '乙'], type: 'unverified_comparability', summary: '测试主体、报告期和币种仍需核验，暂不能确认同口径矛盾。', verification: 'candidate', source_ids: ['甲-source', '乙-source'] });
  const panel = await integrated(snapshot(result));
  const trigger = within(panel).getByRole('button', { name: /测试利润口径 · 比较口径待核/ });
  const conflict = open(trigger, '平台观点对照'); body(conflict).scrollTop = 99;
  expect(within(conflict).getByText(/报告期 2026H1 · 币种 CNY/)).toBeVisible();
  expect(within(conflict).getByText(/报告期 2025FY · 币种 未核验/)).toBeVisible();
  expect(within(conflict).getByText(/先核对同一主体、业务、指标、报告期和币种/)).toBeVisible();
  fireEvent.click(within(conflict).getAllByRole('button', { name: '查看这条完整论据 ↗' })[1]);
  const detail = screen.getByRole('dialog', { name: '平台完整论据' });
  expect(within(detail).getByText(second.citations[0].quote)).toBeVisible();
  fireEvent.click(within(detail).getByRole('button', { name: '← 返回平台观点对照' }));
  expect(body(screen.getByRole('dialog', { name: '平台观点对照' })).scrollTop).toBe(99);
});

it('renders source quotations literally and never turns unsafe source URLs into links', async () => {
  const quotation = '<img src=x onerror="window.leaked=true"><script>window.leaked=true</script>测试原文';
  const unsafe = claim('安全', { claim: quotation });
  unsafe.citations[0] = { ...unsafe.citations[0], quote: quotation, end: 130 + quotation.length };
  const result = research([unsafe]); result.sources[0].url = 'javascript:alert(document.cookie)';
  const panel = await integrated(snapshot(result));
  const detail = open(within(panel).getByRole('button', { name: '完整论据 ↗' }), '平台完整论据');
  expect(within(detail).getByText(quotation)).toBeVisible();
  expect(detail.querySelector('script')).toBeNull(); expect(detail.querySelector('img')).toBeNull();
  fireEvent.click(within(detail).getByRole('button', { name: '查看此段来源 ↗' }));
  const source = screen.getByRole('dialog', { name: '测试平台来源安全' });
  expect(within(source).getByText(quotation)).toBeVisible();
  expect(source.querySelector('script')).toBeNull(); expect(source.querySelector('img')).toBeNull();
  expect(within(source).queryByRole('link', { name: /打开原始来源/ })).not.toBeInTheDocument();
  expect(within(source).getByText('该记录未提供可访问的原文链接。')).toBeVisible();
});

it('clears the previous account platform dialog and ignores its late refresh when a new account loads', async () => {
  const oldRefresh = deferred<{ data: StockIntelligence }>();
  const newAccount = deferred<{ data: StockIntelligence }>();
  get.mockResolvedValueOnce(snapshot(research([claim('账号甲')]), '账号甲合成研究'))
    .mockImplementationOnce(() => oldRefresh.promise).mockImplementationOnce(() => newAccount.promise);
  render(<StockIntelligencePanel {...props} />);
  await screen.findByText('账号甲合成研究');
  fireEvent.click(screen.getByRole('button', { name: '刷新分析' }));
  const oldSignal = get.mock.calls[1][1].signal as AbortSignal;
  open(screen.getByRole('button', { name: '完整论据 ↗' }), '平台完整论据');
  act(() => login('research-b'));
  expect(oldSignal.aborted).toBe(true);
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('测试公司账号甲原始资料')).not.toBeInTheDocument();
  await act(async () => { oldRefresh.resolve(snapshot(research([claim('泄漏账号甲')]), '禁止晚到旧账号')); await oldRefresh.promise; });
  expect(screen.queryByText('禁止晚到旧账号')).not.toBeInTheDocument();
  await act(async () => { newAccount.resolve(snapshot(research([claim('账号乙')]), '账号乙合成研究')); await newAccount.promise; });
  expect(await screen.findByText('账号乙合成研究')).toBeVisible();
  expect(screen.queryByText(/泄漏账号甲/)).not.toBeInTheDocument();
});

it('removes member platform full-text claims immediately on an account entitlement revision', async () => {
  const restricted = deferred<{ data: StockIntelligence }>();
  act(() => authenticateSession(account('research-a', 'premium'), 'synthetic-paid-token', 'https://example.test'));
  get.mockResolvedValueOnce(snapshot(research([claim('会员全文')]))).mockImplementationOnce(() => restricted.promise);
  render(<StockIntelligencePanel {...props} />);
  await screen.findByText('测试平台合成研究快照');
  open(screen.getByRole('button', { name: '完整论据 ↗' }), '平台完整论据');
  act(() => updateAuthAccount(account('research-a'), 'synthetic-paid-token'));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText(/测试公司会员全文/)).not.toBeInTheDocument();
  const empty = research([]); empty.coverage = [coverage({ status: 'access_required', read: 0, scanned: 0, matched: 0, body_states: {}, source_ids: [], reason: '测试账号权限已变更' })];
  await act(async () => { restricted.resolve(snapshot(empty)); await restricted.promise; });
  expect(await screen.findByText('当前账号未找到可读的匹配证据')).toBeVisible();
  expect(screen.queryByText(/会员全文订单/)).not.toBeInTheDocument();
});

it('presents absent platform data as an uncompleted retrieval rather than fabricating a research line', () => {
  const result = snapshot().data; delete result.platform_research;
  direct(result);
  expect(screen.getByText('平台资料待检索')).toBeVisible();
  expect(screen.queryByRole('button', { name: '完整论据 ↗' })).not.toBeInTheDocument();
});
