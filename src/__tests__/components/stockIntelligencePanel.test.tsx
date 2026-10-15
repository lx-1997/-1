import React from 'react';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import StockIntelligencePanel from '../../components/StockIntelligencePanel';
import type { StockIntelligence } from '../../components/stock-intelligence/types';
import { apiGet } from '../../services/apiClient';
import type { AuthUser } from '../../services/authService';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession, updateAuthAccount } from '../../state/authSession';

jest.mock('../../services/apiClient', () => ({ apiGet: jest.fn() }));
const get = apiGet as jest.Mock;
const account = (id: string): AuthUser => ({
  id, username: id, email: null, role: 'viewer', is_active: true, created_at: '2026-10-13', membership: null,
});
const login = (id = 'research-a') => authenticateSession(account(id), `test-token-${id}`, 'https://example.test');
const panelProps = { symbol: '600519', name: '测试公司', loggedIn: true, username: 'research-a', onRequireLogin: jest.fn() };

// Explicit fixtures exercise the view contract; none of these prices are market data.
function intelligence(symbol = '600519', headline = '测试研究结论'): { data: StockIntelligence } {
  return {
    data: {
      symbol, name: '测试公司', market: 'CN', currency: 'CNY', generated_at: '2026-10-13T02:00:00Z',
      quote: { price: 100, change_percent: -1.25, as_of: '2026-10-13T01:59:00Z', source: '测试行情来源' },
      summary: { headline, stance: 'neutral', coverage: { available: 5, total: 6 } },
      valuation: {
        status: 'ready', method: '预测 EPS × 情景 PE', horizon: '未来 12 个月',
        anchor: { price: 100, pe_ttm: 20, forecast_eps: 5, forecast_year: 2027 },
        formula: '每股盈利 × 估值倍数', note: '测试估值假设，非价格承诺', source_ids: ['forecast', 'financials'],
        scenarios: [
          {
            key: 'optimistic', label: '乐观', price: 138, upside_pct: 38,
            assumptions: ['收入增长超过测试基准', '第二条测试盈利假设', '第三条测试估值假设', '第四条测试现金流假设', '第五条测试行业假设'],
            reasons: ['测试新品销量超预期', '第二条测试原因', '第三条测试原因', '第四条测试原因', '第五条测试原因'],
            invalidation: ['测试毛利率下降', '第二条测试失效条件', '第三条测试失效条件', '第四条测试失效条件', '第五条测试失效条件'],
            source_ids: ['forecast'],
          },
          { key: 'base', label: '中性', price: 100, upside_pct: 0, assumptions: ['测试利润按计划兑现'], reasons: ['测试估值保持稳定'], invalidation: ['测试业绩落后预算'], source_ids: ['financials'] },
          { key: 'pessimistic', label: '悲观', price: 68, upside_pct: -32, assumptions: ['测试利润及估值同时承压'], reasons: ['测试行业竞争加剧'], invalidation: ['测试订单恢复增长'], source_ids: ['forecast'] },
        ],
      },
      dimensions: [
        {
          key: 'business', label: '商业模式', signal: 'neutral', headline: '测试业务收入依赖订阅',
          metrics: [
            { label: '主营结构', value: '订阅', unit: '' }, { label: '测试指标二', value: 2, unit: '%' },
            { label: '测试指标三', value: 3, unit: '%' }, { label: '测试指标四', value: 4, unit: '%' },
            { label: '第五项测试指标', value: 5, unit: '%' }, { label: '第六项测试指标', value: 6, unit: '%' },
          ],
          evidence: ['测试收入有续约需求', '第二条商业测试依据', '第三条商业测试依据', '第四条商业测试依据', '第五条商业测试依据'],
          source_ids: ['profile'],
        },
        { key: 'fundamentals', label: '财务质量', signal: 'bullish', headline: '测试现金流足以覆盖利润', metrics: [{ label: '经营现金流', value: 12, unit: '亿元' }], evidence: ['测试公开财报提供现金流'], source_ids: ['financials'] },
        { key: 'earnings', label: '盈利预期', signal: 'neutral', headline: '测试盈利需要后续验证', metrics: [{ label: '预测 EPS', value: 5, unit: '元' }], evidence: ['测试预测不是实际盈利'], source_ids: ['forecast'] },
        { key: 'technical', label: '技术分析', signal: 'neutral', headline: '测试区间震荡', metrics: [{ label: 'MA20', value: 101, unit: '元' }], evidence: ['测试均线由历史数据计算'], source_ids: ['history'] },
        { key: 'capital', label: '资金面', signal: 'insufficient', headline: '测试资金流缺失', metrics: [{ label: '主力净流入', value: null, unit: '万元' }], evidence: ['测试没有资金方向证据'], source_ids: ['flow'] },
        { key: 'news', label: '消息面', signal: 'neutral', headline: '测试平台资讯提示新品发布', metrics: [{ label: '平台消息', value: 1, unit: '条' }], evidence: ['测试公告需要跟踪销售转化'], source_ids: ['platform-news'] },
      ],
      evidence: [
        { id: 'platform-news', origin: 'platform', kind: 'report', title: '测试平台研报线索', summary: '测试平台内容原文摘要', published_at: '2026-10-12', url: '/research/test-report', source: '稻草财经测试信息库' },
        { id: 'public-news', origin: 'public', kind: 'announcement', title: '测试公开公司公告', summary: '测试公告原文摘要', published_at: '2026-10-11', url: 'https://example.test/announcement', source: '测试交易所' },
        { id: 'unlinked', origin: 'public', kind: 'news', title: '测试无原文链接证据', summary: '测试聚合摘要', published_at: null, url: null, source: '测试公共源' },
      ],
      technical: { range: { lower: 85, upper: 115, horizon: '最近 20 个交易日', method: '测试历史收盘价格带' }, as_of: '2026-10-12', adjustment: '前复权' },
      catalysts: [{ event: '测试新品发布', watch: '验证实际销售与利润转化', source_ids: ['platform-news'] }],
      risks: ['测试资金流缺失，无法确认交易力量'], data_gaps: ['缺少测试主力资金数据'],
      sources: [
        { id: 'quote', label: '测试行情来源', url: 'https://example.test/quote', as_of: '2026-10-13', status: 'ok', freshness: 'current', fetched_at: '2026-10-13T02:00:00Z' },
        { id: 'financials', label: '测试公开财报', url: 'https://example.test/financials', as_of: '2026-06-30', status: 'ok', freshness: 'unknown', fetched_at: '2026-10-13T02:00:00Z' },
        { id: 'forecast', label: '测试盈利预测', url: 'https://example.test/forecast', as_of: '2026-10-12', status: 'ok', freshness: 'current', fetched_at: '2026-10-13T02:00:00Z' },
        { id: 'profile', label: '测试公司资料', url: null, as_of: '2026-06-30', status: 'ok', freshness: 'unknown', fetched_at: '2026-10-13T02:00:00Z' },
        { id: 'history', label: '测试历史行情', url: null, as_of: '2026-10-12', status: 'ok', freshness: 'current', fetched_at: '2026-10-13T02:00:00Z' },
        { id: 'flow', label: '测试资金流', url: null, as_of: null, status: 'unavailable', freshness: 'unknown', fetched_at: '2026-10-13T02:00:00Z' },
      ],
    },
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (failure: Error) => void;
  const promise = new Promise<T>((resolvePromise, rejectPromise) => { resolve = resolvePromise; reject = rejectPromise; });
  return { promise, resolve, reject };
}

function openInspector(name: string | RegExp, title?: string) {
  const trigger = screen.getByRole('button', { name });
  act(() => trigger.focus());
  fireEvent.click(trigger);
  const dialog = screen.getByRole('dialog', title ? { name: title } : undefined);
  expect(dialog).toHaveAttribute('aria-modal', 'true');
  return { trigger, dialog };
}
function closeInspector(dialog = screen.getByRole('dialog')) {
  fireEvent.click(within(dialog).getByRole('button', { name: '关闭详情' }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
}
function historyIntelligence() {
  const result = intelligence();
  result.data.technical = {
    ...result.data.technical, series_currency: 'CNY', source_ids: ['history'],
    series: [
      { date: '2026-05-05', close: 80 }, { date: '2026-06-01', close: 90 },
      { date: '2026-07-01', close: 95 }, { date: '2026-08-03', close: 100 },
      { date: '2026-09-01', close: 101 }, { date: '2026-10-12', close: 99 },
      { date: '2026-10-13', close: 100 },
    ],
  };
  return result;
}

function marketIntelligence() {
  const result = historyIntelligence();
  result.data.capital = {
    status: 'available', currency: 'CNY', amount_unit: '元', price_currency: 'CNY', price_adjustment: '未复权',
    as_of: '2026-10-13', fetched_at: '2026-10-13T02:00:00Z', period: { from: '2026-10-09', to: '2026-10-13' },
    series: [
      { date: '2026-10-09', main_net: -800000000, super_large_net: -200000000, large_net: 200000000, medium_net: null, small_net: null, close: 110 },
      { date: '2026-10-12', main_net: 500000000, super_large_net: null, large_net: null, medium_net: null, small_net: null, close: null },
      { date: '2026-10-13', main_net: 0, super_large_net: -100000000, large_net: null, medium_net: null, small_net: null, close: 90 },
    ],
    source_ids: ['flow'], note: '测试成交分类统计，不能证明特定机构买卖。',
  };
  result.data.broker_ratings = {
    status: 'available', distribution: [{ key: 'buy', label: '买入', count: 3 }, { key: 'hold', label: '持有', count: 2 }, { key: 'sell', label: '卖出', count: 1 }],
    samples: [
      { broker_id: 'broker-a', broker: '测试券商甲', rating: '买入', rating_key: 'buy', published_at: '2026-10-10', url: 'https://example.test/rating-a' },
      { broker_id: 'broker-b', broker: '测试券商乙', rating: '增持', rating_key: 'buy', published_at: '2026-10-09', url: 'https://example.test/rating-b' },
      { broker_id: 'broker-c', broker: '测试券商丙', rating: '买入', rating_key: 'buy', published_at: '2026-10-09', url: null },
      { broker_id: 'broker-d', broker: '测试券商丁', rating: '中性', rating_key: 'hold', published_at: '2026-10-08', url: null },
      { broker_id: 'broker-e', broker: '测试券商戊', rating: '持有', rating_key: 'hold', published_at: '2026-10-07', url: null },
      { broker_id: 'broker-f', broker: '测试券商己', rating: '减持', rating_key: 'sell', published_at: '2026-10-06', url: null },
    ],
    sample_count: 6, broker_count: 6, period: { from: '2026-10-06', to: '2026-10-10' }, as_of: '2026-10-10',
    source_ids: ['broker-ratings'], note: '测试每家机构最新一条公开评级；样本分布不是上涨概率。',
  };
  const flow = result.data.sources.find(source => source.id === 'flow')!;
  flow.status = 'available'; flow.as_of = '2026-10-13';
  result.data.sources.push({ id: 'broker-ratings', label: '测试券商评级源', url: 'https://example.test/ratings', status: 'available', as_of: '2026-10-10', fetched_at: '2026-10-13T02:00:00Z' });
  return result;
}

function flowReadout(panel: HTMLElement) {
  return panel.querySelector<HTMLElement>('[aria-live="polite"]')!;
}

beforeEach(() => {
  invalidateAuthSession(); localStorage.clear(); document.body.style.overflow = ''; jest.clearAllMocks(); get.mockReset();
  get.mockResolvedValue(intelligence());
  login();
});
afterEach(() => { invalidateAuthSession(); });

it('offers login without fetching protected analysis or opening login automatically', () => {
  invalidateAuthSession();
  const onRequireLogin = jest.fn();
  render(<StockIntelligencePanel {...panelProps} loggedIn={false} username={null} onRequireLogin={onRequireLogin} />);
  expect(get).not.toHaveBeenCalled(); expect(onRequireLogin).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: /登录/ }));
  expect(onRequireLogin).toHaveBeenCalledTimes(1);
});

it('starts with a compact overview containing all three prices, model inputs and the main risk', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  expect(get).toHaveBeenCalledWith('/api/stock/intelligence', expect.objectContaining({
    params: expect.objectContaining({ symbol: '600519', name: '测试公司' }), signal: expect.any(AbortSignal),
  }));
  for (const [label, price, upside] of [['乐观', '138.00', '+38.0%'], ['中性', '100.00', '0.0%'], ['悲观', '68.00', '-32.0%']]) {
    const scenario = within(screen.getByRole('article', { name: `${label}股价情景` }));
    expect(scenario.getByRole('button', { name: `查看${label}情景依据` })).toBeVisible();
    [label, price, upside, 'CNY'].forEach(value => expect(scenario.getByText(value)).toBeVisible());
  }
  expect(screen.getByText('测试资金流缺失，无法确认交易力量')).toBeVisible();
  expect(screen.getByRole('tab', { name: '研究概览' })).toHaveAttribute('aria-selected', 'true');
  expect(screen.getByRole('tabpanel', { name: '研究概览' })).toBeVisible();
  expect(screen.queryByText('第五条测试行业假设')).not.toBeInTheDocument();
  expect(screen.queryByText('测试新品销量超预期')).not.toBeInTheDocument();
  expect(screen.queryByText('测试收入有续约需求')).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('keeps the reference quote and dated fact context complete in the compact presentation', async () => {
  const result = intelligence();
  result.data.dimensions.find(item => item.key === 'fundamentals')!.metrics = [
    { label: '经营现金流/归母净利', value: 3.03, unit: '倍' },
    { label: '归母净利同比', value: -20.54, unit: '%' },
  ];
  result.data.sources.push({ id: 'cashflow', label: '测试现金流来源', status: 'ok', as_of: '2026-06-30' });
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  await screen.findByText('测试研究结论');
  const header = document.querySelector('.si-company-header')!;
  expect(within(header as HTMLElement).getByText('100.00')).toBeVisible();
  expect(within(header as HTMLElement).getByText('CNY')).toBeVisible();
  expect(within(header as HTMLElement).getByText('分析参考价 · 测试行情来源')).toBeVisible();
  expect(header.querySelector('time')).toHaveAttribute('datetime', result.data.quote.as_of);
  const support = within(screen.getByRole('heading', { name: '支持判断的事实' }).closest('section')!);
  expect(support.getByText('经营现金流 / 归母净利')).toBeVisible();
  expect(support.getByText('3.03')).toHaveTextContent('3.03倍');
  expect(support.getByText('同报告期现金转化比较，合并与归母口径仍有差异。')).toBeVisible();
  expect(support.getByText('2026-06-30')).toHaveAttribute('datetime', '2026-06-30');
  const challenge = within(screen.getByRole('heading', { name: '需要验证的反证' }).closest('section')!);
  expect(challenge.getByText('-20.54')).toHaveTextContent('-20.54%');
  expect(challenge.getByText('已披露盈利回落，未来盈利假设需要验证。')).toBeVisible();
  fireEvent.click(support.getByRole('button', { name: '查看依据 ↗' }));
  expect(screen.getByRole('dialog', { name: '测试公开财报' })).toBeVisible();
});

it('keeps positive fund-flow numbers in the matrix without promoting unverified dates or neutral observations to directional facts', async () => {
  const conditions = [
    { signal: 'neutral', as_of: null },
    { signal: 'neutral', as_of: '2026-10-12' },
    { signal: 'bullish', as_of: null },
  ];
  for (const condition of conditions) {
    const result = intelligence();
    const capital = result.data.dimensions.find(item => item.key === 'capital')!;
    capital.signal = condition.signal;
    capital.headline = '测试近 5 日主力统计净流入';
    capital.metrics = [{ label: '主力净流入', value: 42, unit: '万元' }];
    const source = result.data.sources.find(item => item.id === 'flow')!;
    source.status = 'ok'; source.as_of = condition.as_of;
    get.mockResolvedValue(result);
    const { unmount } = render(<StockIntelligencePanel {...panelProps} />);
    expect(await screen.findByText('测试研究结论')).toBeVisible();
    const support = within(screen.getByRole('heading', { name: '支持判断的事实' }).closest('section')!);
    const challenge = within(screen.getByRole('heading', { name: '需要验证的反证' }).closest('section')!);
    expect(support.queryByText(capital.headline)).not.toBeInTheDocument();
    expect(challenge.queryByText(capital.headline)).not.toBeInTheDocument();
    expect(support.queryByText('42万元')).not.toBeInTheDocument();
    expect(challenge.queryByText('42万元')).not.toBeInTheDocument();
    expect(support.getByText(/^暂无可核验的方向性支持信号/)).toBeVisible();
    const matrixRow = within(screen.getByRole('row', { name: /资金面/ }));
    expect(matrixRow.getByText('42')).toBeVisible();
    expect(matrixRow.getByText('主力净流入')).toBeVisible();
    expect(matrixRow.getByText(condition.as_of || '未提供时点')).toBeVisible();
    unmount();
  }
});

it.each(['乐观', '中性', '悲观'])('opens %s details without truncating assumptions, reasons or invalidation', async label => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const { trigger, dialog } = openInspector(`查看${label}情景依据`, `${label}情景`);
  const scenario = intelligence().data.valuation.scenarios.find(item => item.label === label)!;
  [...scenario.assumptions, ...scenario.reasons, ...scenario.invalidation].forEach(value => expect(within(dialog).getByText(value)).toBeVisible());
  expect(within(dialog).getByRole('heading', { name: '需要发生什么' })).toBeVisible();
  expect(within(dialog).getByRole('heading', { name: '哪些事实会推翻假设' })).toBeVisible();
  expect(screen.getByRole('tab', { name: '研究概览' })).toHaveAttribute('aria-selected', 'true');
  closeInspector(dialog);
  expect(trigger).toHaveFocus();
});

it('opens the valuation formula and structured anchor in the method inspector', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  expect(screen.queryByText('每股盈利 × 估值倍数')).not.toBeInTheDocument();
  const { dialog } = openInspector('估值方法与关键假设', '估值方法与关键假设');
  const content = within(dialog);
  ['每股盈利 × 估值倍数', '当前 TTM PE', '2027 年度预测 EPS', '测试估值假设，非价格承诺'].forEach(value => expect(content.getByText(value)).toBeVisible());
  expect(content.getByText('20')).toBeVisible();
  expect(content.getByText('5')).toBeVisible();
});

it('shows all six dimension rows and complete metrics and evidence in their inspector', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '多维分析' }));
  const panel = within(screen.getByRole('tabpanel', { name: '多维分析' }));
  expect(panel.getAllByRole('button', { name: /^查看.+研究依据$/ })).toHaveLength(6);
  expect(panel.getByText(/最近 20 个交易日/)).toBeVisible();
  expect(screen.queryByText('第五项测试指标')).not.toBeInTheDocument();
  const { dialog } = openInspector('查看商业模式研究依据', '商业模式');
  const business = intelligence().data.dimensions[0];
  [...business.evidence, ...business.metrics.map(metric => metric.label)].forEach(value => expect(within(dialog).getByText(value)).toBeVisible());
  closeInspector(dialog);
  const capital = openInspector('查看资金面研究依据', '资金面').dialog;
  expect(within(capital).getByText('测试没有资金方向证据')).toBeVisible();
  expect(within(capital).getByText('—')).toBeVisible();
});

it('opens a matching dimension from the overview matrix without losing the current tab', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const { trigger, dialog } = openInspector('查看商业模式研究依据', '商业模式');
  expect(within(dialog).getByText('测试业务收入依赖订阅')).toBeVisible();
  expect(screen.getByRole('tab', { name: '研究概览' })).toHaveAttribute('aria-selected', 'true');
  closeInspector(dialog);
  expect(trigger).toHaveFocus();
});

it('keeps evidence rows concise and exposes original links and provenance through inspectors', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '资讯与来源' }));
  expect(screen.queryByText('测试公告原文摘要')).not.toBeInTheDocument();
  const announcement = openInspector('测试公开公司公告', '关联资料').dialog;
  expect(within(announcement).getByText('测试公告原文摘要')).toBeVisible();
  const original = within(announcement).getByRole('link', { name: /^打开资料原文/ });
  expect(original).toHaveAttribute('href', 'https://example.test/announcement');
  expect(original).toHaveAttribute('rel', expect.stringContaining('noreferrer'));
  closeInspector(announcement);
  const report = openInspector('测试平台研报线索', '关联资料').dialog;
  expect(within(report).getByText('测试平台内容原文摘要')).toBeVisible();
  expect(within(report).getByRole('link', { name: /^打开资料原文/ })).toHaveAttribute('href', '/research/test-report');
  closeInspector(report);
  const unlinked = openInspector('测试无原文链接证据', '关联资料').dialog;
  expect(within(unlinked).queryByRole('link', { name: /^打开资料原文/ })).not.toBeInTheDocument();
  closeInspector(unlinked);
  fireEvent.click(screen.getByText('数据来源与时间'));
  const source = openInspector('测试盈利预测 ↗', '测试盈利预测').dialog;
  expect(within(source).getByText('资料时点')).toBeVisible();
  expect(within(source).getByText('2026-10-12')).toBeVisible();
  expect(within(source).getByRole('link', { name: /^打开原始来源/ })).toHaveAttribute('href', 'https://example.test/forecast');
});

it('traces a scenario source inside the inspector and returns to the same scenario scroll position', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  document.body.style.overflow = 'auto';
  const { trigger, dialog } = openInspector('查看乐观情景依据', '乐观情景');
  const body = dialog.querySelector<HTMLDivElement>('.si-inspector-body')!;
  body.scrollTop = 143;
  fireEvent.click(within(dialog).getByRole('link', { name: '测试盈利预测' }));
  const source = screen.getByRole('dialog', { name: '测试盈利预测' });
  expect(within(source).getByText('2026-10-12')).toBeVisible();
  expect(within(source).getByRole('link', { name: /^打开原始来源/ })).toHaveAttribute('href', 'https://example.test/forecast');
  expect(screen.getByRole('tab', { name: '研究概览' })).toHaveAttribute('aria-selected', 'true');
  fireEvent.click(within(source).getByRole('button', { name: /返回乐观情景/ }));
  const restored = screen.getByRole('dialog', { name: '乐观情景' });
  expect(within(restored).getByText('第五条测试行业假设')).toBeVisible();
  expect(restored.querySelector<HTMLDivElement>('.si-inspector-body')!.scrollTop).toBe(143);
  expect(within(restored).queryByRole('button', { name: /返回/ })).not.toBeInTheDocument();
  closeInspector(restored);
  expect(trigger).toHaveFocus();
  expect(document.body.style.overflow).toBe('auto');
});

it('keeps keyboard focus in the inspector and restores the trigger on Escape or native cancellation', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const { trigger, dialog } = openInspector('查看乐观情景依据', '乐观情景');
  const heading = within(dialog).getByRole('heading', { name: '乐观情景' });
  const last = within(dialog).getByRole('link', { name: '测试盈利预测' });
  expect(heading).toHaveFocus();
  fireEvent.keyDown(heading, { key: 'Tab', shiftKey: true }); expect(last).toHaveFocus();
  fireEvent.keyDown(last, { key: 'Tab' }); expect(within(dialog).getByRole('button', { name: '关闭详情' })).toHaveFocus();
  fireEvent.keyDown(dialog, { key: 'Escape' });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(trigger).toHaveFocus(); expect(document.body.style.overflow).toBe('');
  const method = openInspector('估值方法与关键假设');
  fireEvent(method.dialog, new Event('cancel', { cancelable: true }));
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(method.trigger).toHaveFocus();
});

it('isolates modal keydowns from native terminal shortcuts while preserving scrolling and shortcuts after close', async () => {
  const terminalShortcut = jest.fn();
  window.addEventListener('keydown', terminalShortcut);
  try {
    render(<StockIntelligencePanel {...panelProps} />);
    expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
    const { trigger, dialog } = openInspector('查看乐观情景依据', '乐观情景');
    const heading = within(dialog).getByRole('heading', { name: '乐观情景' });
    const down = new KeyboardEvent('keydown', { key: 'ArrowDown', bubbles: true, cancelable: true });
    fireEvent(heading, down);
    expect(down.defaultPrevented).toBe(false);
    expect(terminalShortcut).not.toHaveBeenCalled();
    expect(screen.getByRole('dialog', { name: '乐观情景' })).toBeVisible();
    const escape = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
    fireEvent(heading, escape);
    expect(escape.defaultPrevented).toBe(true);
    expect(terminalShortcut).not.toHaveBeenCalled();
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
    fireEvent.keyDown(trigger, { key: 'ArrowDown' });
    expect(terminalShortcut).toHaveBeenCalledTimes(1);
    expect(terminalShortcut).toHaveBeenLastCalledWith(expect.objectContaining({ key: 'ArrowDown' }));
  } finally {
    window.removeEventListener('keydown', terminalShortcut);
  }
});

it.each([
  { label: 'absent', series: undefined },
  { label: 'empty', series: [] },
  { label: 'one valid day', series: [{ date: '2026-10-13', close: 100 }] },
  { label: 'invalid dates and prices', series: [{ date: '2026-02-30', close: 90 }, { date: '2026-10-12', close: Number.NaN }, { date: '2026-10-13', close: 0 }] },
])('shows no invented day chart when usable history is insufficient: $label', async ({ series }) => {
  const result = intelligence();
  result.data.technical = { ...result.data.technical, series, series_currency: 'CNY' };
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const history = within(screen.getByRole('region', { name: '历史价格走势' }));
  expect(history.getByText('暂无可用日线')).toBeVisible();
  expect(history.queryByRole('img')).not.toBeInTheDocument();
  expect(history.queryByRole('group', { name: '历史图周期' })).not.toBeInTheDocument();
});

it('uses only supplied dates and closing prices for history keyboard inspection and period changes', async () => {
  const result = historyIntelligence();
  result.data.technical!.series!.push({ date: '2026-02-30', close: 999 }, { date: '2026-10-12', close: Number.NaN });
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const history = within(screen.getByRole('region', { name: '历史价格走势' }));
  const chart = history.getByRole('img', { name: /历史收盘图/ });
  const periods = within(history.getByRole('group', { name: '历史图周期' }));
  function readout(date: string, close: number) {
    expect(history.getByText(`${date} 收盘`).parentElement).toHaveTextContent(close.toFixed(2));
    expect(history.getByText(`${date} 收盘`).parentElement).toHaveTextContent('CNY');
  }
  readout('2026-10-13', 100);
  expect(periods.getByRole('button', { name: '3月' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.keyDown(chart, { key: 'ArrowLeft' }); readout('2026-10-12', 99);
  fireEvent.keyDown(chart, { key: 'ArrowRight' }); readout('2026-10-13', 100);
  fireEvent.keyDown(chart, { key: 'Home' }); readout('2026-08-03', 100);
  fireEvent.click(periods.getByRole('button', { name: '1月' }));
  expect(periods.getByRole('button', { name: '1月' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.keyDown(chart, { key: 'Home' }); readout('2026-10-12', 99);
  fireEvent.keyDown(chart, { key: 'End' }); readout('2026-10-13', 100);
  fireEvent.click(periods.getByRole('button', { name: '1年' }));
  fireEvent.keyDown(chart, { key: 'Home' }); readout('2026-05-05', 80);
  fireEvent.keyDown(chart, { key: 'ArrowLeft' }); readout('2026-05-05', 80);
  fireEvent.keyDown(chart, { key: 'End' }); readout('2026-10-13', 100);
  expect(history.getByText('7 个交易日')).toBeVisible();
  expect(history.queryByText('2026-02-30')).not.toBeInTheDocument();
  expect(history.queryByText('999.00')).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(1);
});

it('retains a stale historical curve and its actual dates while explicitly marking delayed data', async () => {
  const result = historyIntelligence();
  result.data.technical!.as_of = '2026-09-11';
  result.data.technical!.series = [
    { date: '2026-08-03', close: 100 }, { date: '2026-09-10', close: 99 }, { date: '2026-09-11', close: 100 },
  ];
  const source = result.data.sources.find(item => item.id === 'history')!;
  source.status = 'stale'; source.as_of = '2026-09-11'; source.freshness = 'stale';
  // A recent fetch must not replace the historical sample's older observation date.
  source.fetched_at = '2026-10-13T02:00:00Z';
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const history = within(screen.getByRole('region', { name: '历史价格走势' }));
  expect(history.getByText(/历史快照 · 数据滞后/)).toBeVisible();
  const chart = history.getByRole('img', { name: /历史收盘图/ });
  expect(chart).toBeVisible();
  expect(history.getByText('2026-09-11 收盘')).toBeVisible();
  expect(history.queryByText('2026-10-13 收盘')).not.toBeInTheDocument();
  fireEvent.keyDown(chart, { key: 'ArrowLeft' });
  expect(history.getByText('2026-09-10 收盘').parentElement).toHaveTextContent('99.00');
  fireEvent.keyDown(chart, { key: 'End' });
  expect(history.getByText('2026-09-11 收盘').parentElement).toHaveTextContent('100.00');
  expect(history.getByText('3 个交易日')).toBeVisible();
  const inspector = openInspector(/^查看行情依据/, '测试历史行情').dialog;
  expect(within(inspector).getByText('数据滞后')).toBeVisible();
  expect(within(inspector).getByText('2026-09-11')).toBeVisible();
  expect(within(inspector).getByText(/2026\/10\/13/)).toBeVisible();
});

it('does not borrow the quote currency for history whose currency is unknown', async () => {
  const result = historyIntelligence(); result.data.technical!.series_currency = null;
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const history = within(screen.getByRole('region', { name: '历史价格走势' }));
  expect(history.getByText('币种未核验')).toBeVisible();
  expect(history.queryByText('CNY')).not.toBeInTheDocument();
});

it('preserves a distinct verified history currency and opens its price-data source in place', async () => {
  const result = historyIntelligence(); result.data.technical!.series_currency = 'USD';
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const history = within(screen.getByRole('region', { name: '历史价格走势' }));
  expect(history.getByText('USD')).toBeVisible();
  expect(history.getByText(/历史价格以 USD 计价/)).toBeVisible();
  const { trigger, dialog } = openInspector(/^查看行情依据/, '测试历史行情');
  expect(within(dialog).getByText('2026-10-12')).toBeVisible();
  expect(screen.getByRole('tab', { name: '研究概览' })).toHaveAttribute('aria-selected', 'true');
  closeInspector(dialog); expect(trigger).toHaveFocus();
});

it('keeps the complete risk gaps and catalyst checklist reachable without extending the overview', async () => {
  const result = intelligence();
  result.data.risks.push('第二条测试风险', '第三条测试风险', '第四条测试风险');
  result.data.data_gaps.push('第二项测试数据缺口', '第三项测试数据缺口');
  result.data.catalysts.push({ event: '第二个测试催化事件', watch: '第二个测试验证指标', source_ids: ['forecast'] });
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  expect(screen.queryByText('第四条测试风险')).not.toBeInTheDocument();
  const risks = openInspector(/^全部风险/, '风险反证与数据缺口').dialog;
  [...result.data.risks, ...result.data.data_gaps].forEach(value => expect(within(risks).getByText(value)).toBeVisible());
  closeInspector(risks);
  const catalysts = openInspector(/^验证清单/, '催化与验证清单').dialog;
  result.data.catalysts.forEach(item => {
    expect(within(catalysts).getByText(item.event)).toBeVisible();
    expect(within(catalysts).getByText(item.watch)).toBeVisible();
  });
});

it('supports keyboard tab activation, wraparound, Home and End with consistent ARIA panel associations', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  const tabs = within(screen.getByRole('tablist', { name: '个股研究内容' })).getAllByRole('tab');
  expect(tabs).toHaveLength(3);
  function expectSelection(index: number) {
    tabs.forEach((tab, current) => {
      expect(tab).toHaveAttribute('aria-selected', String(current === index));
      expect(tab).toHaveAttribute('tabindex', current === index ? '0' : '-1');
      const controlled = document.getElementById(tab.getAttribute('aria-controls')!);
      expect(controlled).toHaveAttribute('aria-labelledby', tab.id);
      if (current === index) expect(controlled).toBeVisible();
      else expect(controlled).not.toBeVisible();
    });
  }
  expectSelection(0);
  act(() => tabs[0].focus());
  fireEvent.keyDown(tabs[0], { key: 'ArrowRight' }); expectSelection(1); expect(tabs[1]).toHaveFocus();
  fireEvent.keyDown(tabs[1], { key: 'ArrowRight' }); expectSelection(2); expect(tabs[2]).toHaveFocus();
  fireEvent.keyDown(tabs[2], { key: 'ArrowRight' }); expectSelection(0); expect(tabs[0]).toHaveFocus();
  fireEvent.keyDown(tabs[0], { key: 'ArrowLeft' }); expectSelection(2); expect(tabs[2]).toHaveFocus();
  fireEvent.keyDown(tabs[2], { key: 'Home' }); expectSelection(0); expect(tabs[0]).toHaveFocus();
  fireEvent.keyDown(tabs[0], { key: 'End' }); expectSelection(2); expect(tabs[2]).toHaveFocus();
  expect(get).toHaveBeenCalledTimes(1);
});

it('allows all platform and public evidence rows to be reached through expand and source filters', async () => {
  const result = intelligence();
  for (const origin of ['platform', 'public'] as const) {
    for (let index = 1; index <= 7; index += 1) result.data.evidence.push({ id: `${origin}-extra-${index}`, origin, kind: 'news', title: `${origin} 第 ${index} 条额外测试证据`, source: '测试来源', url: null });
  }
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '资讯与来源' }));
  expect(screen.queryByRole('button', { name: 'platform 第 7 条额外测试证据' })).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'public 第 7 条额外测试证据' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '查看全部 8 条平台信息' }));
  fireEvent.click(screen.getByRole('button', { name: '查看全部 9 条公开信息' }));
  for (const origin of ['platform', 'public']) {
    for (let index = 1; index <= 7; index += 1) expect(screen.getByRole('button', { name: `${origin} 第 ${index} 条额外测试证据` })).toBeVisible();
  }
  const filters = within(screen.getByRole('group', { name: '资讯来源筛选' }));
  fireEvent.click(filters.getByRole('button', { name: /^平台$/ }));
  expect(filters.getByRole('button', { name: /^平台$/ })).toHaveAttribute('aria-pressed', 'true');
  expect(screen.queryByRole('button', { name: '测试公开公司公告' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '测试平台研报线索' })).toBeVisible();
  fireEvent.click(filters.getByRole('button', { name: /^公开$/ }));
  expect(screen.queryByRole('button', { name: '测试平台研报线索' })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '测试公开公司公告' })).toBeVisible();
});

it('does not manufacture scenario prices from missing inputs or the historical range', async () => {
  const result = intelligence();
  const missing: { data: StockIntelligence } = {
    data: {
      ...result.data, quote: { ...result.data.quote, price: null },
      valuation: {
        ...result.data.valuation, status: 'insufficient',
        anchor: { price: null, pe_ttm: null, forecast_eps: null, forecast_year: null },
        scenarios: result.data.valuation.scenarios.map(scenario => ({ ...scenario, price: null, upside_pct: null })),
        note: '测试缺少可核验盈利锚点，暂不估价',
      },
    },
  };
  get.mockResolvedValue(missing);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  expect(screen.getAllByText('暂不估价')).toHaveLength(3);
  const method = openInspector('估值方法与关键假设').dialog;
  expect(within(method).getByText('测试缺少可核验盈利锚点，暂不估价')).toBeVisible();
  expect(document.body).not.toHaveTextContent(/NaN|Infinity|CNY 0\.00/);
});

it('labels sensitivity calculations separately from supported forward valuation', async () => {
  const result = intelligence();
  result.data.valuation.status = 'sensitivity';
  result.data.valuation.note = '测试仅对当前盈利做敏感性测算，盈利预测尚缺';
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  expect(screen.getByText('估值敏感性')).toBeVisible();
  expect(screen.getByText(/未来 12 个月/)).toBeVisible();
  const method = openInspector('估值方法与关键假设').dialog;
  expect(within(method).getByText('测试仅对当前盈利做敏感性测算，盈利预测尚缺')).toBeVisible();
  expect(screen.queryByText('条件估值')).not.toBeInTheDocument();
});

it('rejects invalid numeric prices rather than displaying zero, NaN or infinity targets', async () => {
  const result = intelligence();
  result.data.quote.price = Number.NaN;
  result.data.valuation.scenarios[0].price = Number.POSITIVE_INFINITY;
  result.data.valuation.scenarios[1].price = Number.NaN;
  result.data.valuation.scenarios[2].price = 0;
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  expect(screen.getAllByText('暂不估价')).toHaveLength(3);
  expect(document.body).not.toHaveTextContent(/NaN|Infinity/);
  expect(screen.queryByText('0.00')).not.toBeInTheDocument();
});

it('lets a failed request be retried and replaces the error with the real response', async () => {
  get.mockRejectedValueOnce(new Error('测试网络暂时中断')).mockResolvedValueOnce(intelligence('600519', '重试后的测试结论'));
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试网络暂时中断')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '重试' }));
  expect(await screen.findByText('重试后的测试结论')).toBeInTheDocument();
  expect(screen.queryByText('测试网络暂时中断')).not.toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(2);
});

it.each(['success', 'failure'])('ignores an old-symbol late %s after the new stock has loaded', async outcome => {
  const first = deferred<ReturnType<typeof intelligence>>();
  get.mockImplementation((_url: string, config: { params: { symbol: string } }) => (
    config.params.symbol === '600519' ? first.promise : Promise.resolve(intelligence('000001', '第二只股票测试结论'))
  ));
  const { rerender } = render(<StockIntelligencePanel {...panelProps} />);
  const oldSignal = get.mock.calls[0][1].signal as AbortSignal;
  rerender(<StockIntelligencePanel {...panelProps} symbol="000001" name="第二只测试公司" />);
  expect(await screen.findByText('第二只股票测试结论')).toBeInTheDocument();
  expect(oldSignal.aborted).toBe(true);
  await act(async () => {
    if (outcome === 'success') first.resolve(intelligence('600519', '旧股票延迟结论'));
    else first.reject(new Error('旧股票延迟错误'));
    await first.promise.catch(() => undefined);
  });
  expect(screen.getByText('第二只股票测试结论')).toBeInTheDocument();
  expect(screen.queryByText('旧股票延迟结论')).not.toBeInTheDocument();
  expect(screen.queryByText('旧股票延迟错误')).not.toBeInTheDocument();
});

it('clears old-account results immediately and rejects its late refresh after an account switch', async () => {
  const refreshA = deferred<ReturnType<typeof intelligence>>();
  const initialB = deferred<ReturnType<typeof intelligence>>();
  let aRequests = 0;
  get.mockImplementation(() => {
    if (getAuthSnapshot().account?.id === 'research-b') return initialB.promise;
    aRequests += 1;
    return aRequests === 1 ? Promise.resolve(intelligence('600519', 'A 账号测试私有结论')) : refreshA.promise;
  });
  const { rerender } = render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('A 账号测试私有结论')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新分析' }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  const oldSignal = get.mock.calls[1][1].signal as AbortSignal;
  act(() => {
    login('research-b');
    rerender(<StockIntelligencePanel {...panelProps} username="research-b" />);
  });
  expect(screen.queryByText('A 账号测试私有结论')).not.toBeInTheDocument();
  expect(oldSignal.aborted).toBe(true);
  await act(async () => { refreshA.resolve(intelligence('600519', 'A 账号延迟刷新')); await refreshA.promise; });
  expect(screen.queryByText('A 账号延迟刷新')).not.toBeInTheDocument();
  await act(async () => { initialB.resolve(intelligence('600519', 'B 账号测试结论')); await initialB.promise; });
  expect(await screen.findByText('B 账号测试结论')).toBeInTheDocument();
});

it('removes protected results on logout even before a parent loggedIn prop update', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  openInspector('查看乐观情景依据', '乐观情景');
  expect(document.body.style.overflow).toBe('hidden');
  act(() => { invalidateAuthSession(); });
  expect(screen.queryByText('测试研究结论')).not.toBeInTheDocument();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('第五条测试原因')).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe('');
  expect(screen.getByRole('button', { name: /登录/ })).toBeInTheDocument();
  expect(get).toHaveBeenCalledTimes(1);
});

it.each(['stock', 'account'])('clears an open inspector immediately when the %s changes', async change => {
  const pending = deferred<ReturnType<typeof intelligence>>();
  get.mockResolvedValueOnce(intelligence()).mockImplementation(() => pending.promise);
  const { rerender } = render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  openInspector('查看乐观情景依据', '乐观情景');
  expect(screen.getByText('第五条测试原因')).toBeVisible();
  act(() => {
    if (change === 'account') {
      login('research-b'); rerender(<StockIntelligencePanel {...panelProps} username="research-b" />);
    } else rerender(<StockIntelligencePanel {...panelProps} symbol="000001" name="第二只测试公司" />);
  });
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  expect(screen.queryByText('第五条测试原因')).not.toBeInTheDocument();
  expect(document.body.style.overflow).toBe('');
  await act(async () => { pending.resolve(intelligence(change === 'stock' ? '000001' : '600519', '更新范围后的测试结论')); await pending.promise; });
  expect(await screen.findByText('更新范围后的测试结论')).toBeVisible();
  expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
});

it('invalidates visible analysis and in-flight refreshes when the same account loses membership', async () => {
  const member = { ...account('research-a'), membership: { tier: 'premium' as const, expires_at: '2026-12-01', days_left: 49 } };
  authenticateSession(member, 'test-member', 'https://example.test');
  const pendingRefresh = deferred<ReturnType<typeof intelligence>>();
  const afterDowngrade = deferred<ReturnType<typeof intelligence>>();
  get.mockResolvedValueOnce(intelligence('600519', '会员账号测试结论'))
    .mockImplementationOnce(() => pendingRefresh.promise).mockImplementationOnce(() => afterDowngrade.promise);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('会员账号测试结论')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '刷新分析' }));
  await waitFor(() => expect(get).toHaveBeenCalledTimes(2));
  const oldSignal = get.mock.calls[1][1].signal as AbortSignal;
  act(() => { updateAuthAccount({ ...member, membership: null }, 'test-member'); });
  expect(screen.queryByText('会员账号测试结论')).not.toBeInTheDocument();
  expect(oldSignal.aborted).toBe(true);
  await act(async () => { pendingRefresh.resolve(intelligence('600519', '权限变更前的延迟结论')); await pendingRefresh.promise; });
  expect(screen.queryByText('权限变更前的延迟结论')).not.toBeInTheDocument();
  await act(async () => { afterDowngrade.resolve(intelligence('600519', '重新核验权限后的测试结论')); await afterDowngrade.promise; });
  expect(await screen.findByText('重新核验权限后的测试结论')).toBeInTheDocument();
});

it('keeps unsafe evidence URLs out of actionable original links', async () => {
  const result = intelligence();
  result.data.evidence.push(
    { ...result.data.evidence[0], id: 'unsafe-js', title: '测试脚本地址证据', url: 'javascript:alert(1)' },
    { ...result.data.evidence[0], id: 'unsafe-data', title: '测试内嵌页面证据', url: 'data:text/html,example' },
  );
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('tab', { name: '资讯与来源' }));
  for (const title of ['测试脚本地址证据', '测试内嵌页面证据']) {
    const dialog = openInspector(title, '关联资料').dialog;
    expect(within(dialog).getByText('测试平台内容原文摘要')).toBeVisible();
    expect(within(dialog).queryByRole('link', { name: /^打开资料原文/ })).not.toBeInTheDocument();
    expect(dialog.querySelector('a[href^="javascript:"], a[href^="data:"]')).toBeNull();
    closeInspector(dialog);
  }
});

it('keeps missing daily fund flow and broker ratings empty instead of constructing example charts or counts', async () => {
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const flow = within(screen.getByRole('region', { name: '资金与股价联动' }));
  const ratings = within(screen.getByRole('region', { name: '券商评级分布' }));
  expect(flow.getByText('暂无可核验的日资金序列')).toBeVisible();
  expect(ratings.getByText('暂无可核验的评级分布')).toBeVisible();
  expect(flow.queryByRole('img')).not.toBeInTheDocument();
  expect(ratings.queryByRole('img')).not.toBeInTheDocument();
  expect(ratings.queryByRole('button', { name: /^查看买入评级样本/ })).not.toBeInTheDocument();
});

it('preserves positive, negative and zero daily amounts and uses only the same flow row closing price', async () => {
  const result = marketIntelligence();
  // The independently adjusted technical price must not replace an absent flow-row price.
  result.data.technical!.series = [{ date: '2026-10-09', close: 1100 }, { date: '2026-10-12', close: 9999 }, { date: '2026-10-13', close: 900 }];
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const panel = screen.getByRole('region', { name: '资金与股价联动' });
  const flow = within(panel);
  const chart = flow.getByRole('img', { name: /每日资金净额与同日收盘图/ });
  expect(flow.getByText('-3.00')).toBeVisible();
  expect(flow.getByText('净流入')).toBeVisible(); expect(flow.getByText('净流出')).toBeVisible();
  expect(flow.getByText(/价格：未复权，右轴 CNY/)).toBeVisible();
  expect(flow.getByText(result.data.capital!.note)).not.toBeVisible();
  fireEvent.click(flow.getByText('统计口径与数据覆盖'));
  expect(flow.getByText(result.data.capital!.note)).toBeVisible();
  expect(flowReadout(panel)).toHaveTextContent('2026-10-13');
  expect(within(flowReadout(panel)).getByText('0.00 亿元')).toBeVisible();
  expect(within(flowReadout(panel)).getByText('90.00 CNY')).toBeVisible();
  fireEvent.keyDown(chart, { key: 'ArrowLeft' });
  expect(flowReadout(panel)).toHaveTextContent('2026-10-12');
  expect(within(flowReadout(panel)).getByText('+5.00 亿元')).toBeVisible();
  expect(within(flowReadout(panel)).getByText('未提供可比价格')).toBeVisible();
  expect(flowReadout(panel)).not.toHaveTextContent(/110\.00|90\.00|9999/);
  fireEvent.keyDown(chart, { key: 'Home' });
  expect(flowReadout(panel)).toHaveTextContent('2026-10-09');
  expect(within(flowReadout(panel)).getByText('-8.00 亿元')).toBeVisible();
  expect(within(flowReadout(panel)).getByText('110.00 CNY')).toBeVisible();
  fireEvent.keyDown(chart, { key: 'ArrowRight' });
  expect(flowReadout(panel)).toHaveTextContent('2026-10-12');
  fireEvent.keyDown(chart, { key: 'End' });
  expect(flowReadout(panel)).toHaveTextContent('2026-10-13');
  expect(get).toHaveBeenCalledTimes(1);
});

it.each([
  { label: 'different currencies', currency: 'CNY', price_currency: 'HKD' },
  { label: 'unknown flow currency', currency: null, price_currency: 'CNY' },
  { label: 'unknown price currency', currency: 'CNY', price_currency: null },
  { label: 'matching but unverified currency codes', currency: 'UNKNOWN', price_currency: 'UNKNOWN' },
])('does not overlay incompatible prices for $label', async ({ currency, price_currency }) => {
  const result = marketIntelligence();
  result.data.capital!.currency = currency; result.data.capital!.price_currency = price_currency;
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const panel = screen.getByRole('region', { name: '资金与股价联动' });
  const flow = within(panel);
  expect(flow.getByRole('img', { name: /每日资金净额与同日收盘图/ })).toBeVisible();
  expect(flow.getByText(/价格币种未核验或不一致，未叠加价格线/)).toBeVisible();
  expect(within(flowReadout(panel)).getByText('未提供可比价格')).toBeVisible();
  expect(panel.querySelector('.si-flow-price-line')).toBeNull();
  expect(flow.queryByText(/^同日收盘 · /)).not.toBeInTheDocument();
});

it.each(['unverified amount unit', 'invalid dates and amounts'])('does not plot daily money from %s', async issue => {
  const result = marketIntelligence();
  if (issue === 'unverified amount unit') result.data.capital!.amount_unit = '万元';
  else result.data.capital!.series = [
    { date: '2026-02-30', main_net: 100000000, super_large_net: null, large_net: null, medium_net: null, small_net: null, close: 110 },
    { date: '2026-10-13', main_net: Number.NaN, super_large_net: null, large_net: null, medium_net: null, small_net: null, close: 90 },
  ];
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const flow = within(screen.getByRole('region', { name: '资金与股价联动' }));
  expect(flow.getByText('暂无可核验的日资金序列')).toBeVisible();
  expect(flow.queryByRole('img')).not.toBeInTheDocument();
});

it('preserves dated rows with only another flow category and identifies partial coverage without filling zero', async () => {
  const result = marketIntelligence();
  result.data.capital!.series.unshift({ date: '2026-10-08', main_net: null, super_large_net: null, large_net: 300000000, medium_net: null, small_net: null, close: 120 });
  result.data.capital!.period.from = '2026-10-08';
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const panel = screen.getByRole('region', { name: '资金与股价联动' });
  const flow = within(panel);
  const chart = flow.getByRole('img', { name: /每日资金净额与同日收盘图/ });
  expect(flow.getByText(/4 个有效交易日 · 3 日有分类数据/)).toBeVisible();
  fireEvent.keyDown(chart, { key: 'Home' });
  expect(flowReadout(panel)).toHaveTextContent('2026-10-08');
  expect(within(flowReadout(panel)).getByText('未提供')).toBeVisible();
  expect(flowReadout(panel)).not.toHaveTextContent('0.00 亿元');
  expect(within(flowReadout(panel)).getByText('120.00 CNY')).toBeVisible();
  const categories = within(flow.getByRole('group', { name: '资金分类' }));
  expect(categories.queryByRole('button', { name: '小单' })).not.toBeInTheDocument();
  fireEvent.click(categories.getByRole('button', { name: '大单' }));
  const largeChart = flow.getByRole('img', { name: /每日资金净额与同日收盘图/ });
  fireEvent.keyDown(largeChart, { key: 'Home' });
  expect(within(flowReadout(panel)).getByText('+3.00 亿元')).toBeVisible();
});

it('keeps interval and category controls usable when a selected flow category has no data in that interval', async () => {
  get.mockResolvedValue(marketIntelligence());
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const panel = screen.getByRole('region', { name: '资金与股价联动' });
  const flow = within(panel);
  const categories = within(flow.getByRole('group', { name: '资金分类' }));
  fireEvent.click(categories.getByRole('button', { name: '大单' }));
  fireEvent.change(flow.getByRole('slider', { name: '资金图开始交易日' }), { target: { value: '1' } });
  expect(flow.getByText('当前区间暂无大单净额')).toBeVisible();
  expect(flow.queryByRole('img')).not.toBeInTheDocument();
  expect(flow.queryByText('0.00')).not.toBeInTheDocument();
  expect(flow.getAllByText('未提供').some(element => element.textContent === '未提供')).toBe(true);
  expect(flow.getByRole('slider', { name: '资金图结束交易日' })).toBeVisible();
  expect(categories.getByRole('button', { name: '主力' })).toBeVisible();
  fireEvent.click(categories.getByRole('button', { name: '主力' }));
  expect(flow.getByRole('img', { name: /每日资金净额与同日收盘图/ })).toBeVisible();
  expect(flow.getByText('+5.00')).toBeVisible();
  fireEvent.change(flow.getByRole('slider', { name: '资金图开始交易日' }), { target: { value: '0' } });
  fireEvent.click(categories.getByRole('button', { name: '大单' }));
  expect(flow.getByRole('img', { name: /每日资金净额与同日收盘图/ })).toBeVisible();
  expect(flow.getByText('+2.00')).toBeVisible();
});

it('uses real trading-record periods and brush boundaries without refetching or inventing covered days', async () => {
  const result = marketIntelligence();
  const dates = ['2026-09-23', '2026-09-24', '2026-09-25', '2026-09-28', '2026-09-29', '2026-09-30', '2026-10-09', '2026-10-12', '2026-10-13'];
  const amounts = [-1, -2, 0, 4, -5, 6, 0, 8, -9];
  result.data.capital!.series = dates.map((date, index) => ({ date, main_net: amounts[index] * 1e8, super_large_net: null, large_net: null, medium_net: null, small_net: null, close: 100 + index }));
  result.data.capital!.period.from = dates[0];
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const panel = screen.getByRole('region', { name: '资金与股价联动' });
  const flow = within(panel);
  const periods = within(flow.getByRole('group', { name: '资金图交易日区间' }));
  expect(flow.getByText('9 个有效交易日')).toBeVisible();
  fireEvent.click(periods.getByRole('button', { name: '5日' }));
  expect(periods.getByRole('button', { name: '5日' })).toHaveAttribute('aria-pressed', 'true');
  expect(flow.getByText('5 个有效交易日')).toBeVisible();
  expect(flow.getByText('0.00')).toBeVisible();
  fireEvent.keyDown(flow.getByRole('img', { name: /每日资金净额与同日收盘图/ }), { key: 'Home' });
  expect(flowReadout(panel)).toHaveTextContent('2026-09-29');
  fireEvent.click(periods.getByRole('button', { name: '60日' }));
  expect(periods.getByRole('button', { name: '60日' })).toHaveAttribute('aria-pressed', 'true');
  expect(flow.getByText('9 个有效交易日')).toBeVisible();
  fireEvent.click(periods.getByRole('button', { name: '20日' }));
  expect(periods.getByRole('button', { name: '20日' })).toHaveAttribute('aria-pressed', 'true');
  const start = flow.getByRole('slider', { name: '资金图开始交易日' });
  const end = flow.getByRole('slider', { name: '资金图结束交易日' });
  fireEvent.change(start, { target: { value: '2' } });
  fireEvent.change(end, { target: { value: '4' } });
  expect(flow.getByText('3 个有效交易日')).toBeVisible();
  expect(flow.getByText('-1.00')).toBeVisible();
  expect(end).toHaveValue('4');
  fireEvent.change(start, { target: { value: '8' } });
  expect(start).toHaveValue('4');
  expect(flow.getByText('1 个有效交易日')).toBeVisible();
  fireEvent.click(flow.getByRole('button', { name: '全部 9 日' }));
  expect(flow.getByText('9 个有效交易日')).toBeVisible();
  expect(start).toHaveValue('0'); expect(end).toHaveValue('8');
  expect(get).toHaveBeenCalledTimes(1);
});

it('counts real broker-rating records and supports filtered samples, original links and return from their source', async () => {
  const result = marketIntelligence();
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const ratings = within(screen.getByRole('region', { name: '券商评级分布' }));
  expect(ratings.getByRole('img', { name: '券商评级分布，共 6 条最新评级样本' })).toBeVisible();
  for (const group of result.data.broker_ratings!.distribution) {
    const button = ratings.getByRole('button', { name: `查看${group.label}评级样本` });
    expect(button).toHaveTextContent(`${group.count} 条`);
  }
  expect(ratings.getByText('6 家机构 · 每机构最新一条')).toBeVisible();
  const { trigger, dialog } = openInspector('查看买入评级样本', '券商评级样本');
  expect(within(dialog).getByText(result.data.broker_ratings!.note)).toBeVisible();
  expect(within(dialog).getAllByRole('article')).toHaveLength(3);
  ['测试券商甲', '测试券商乙', '测试券商丙'].forEach(broker => expect(within(dialog).getByText(broker)).toBeVisible());
  expect(within(dialog).queryByText('测试券商丁')).not.toBeInTheDocument();
  expect(within(dialog).getAllByRole('link', { name: /^打开评级原始来源/ })[0]).toHaveAttribute('href', 'https://example.test/rating-a');
  fireEvent.click(within(dialog).getByRole('link', { name: '测试券商评级源' }));
  const source = screen.getByRole('dialog', { name: '测试券商评级源' });
  expect(within(source).getByText('2026-10-10')).toBeVisible();
  fireEvent.click(within(source).getByRole('button', { name: /返回券商评级样本/ }));
  const restored = screen.getByRole('dialog', { name: '券商评级样本' });
  expect(within(restored).getAllByRole('article')).toHaveLength(3);
  closeInspector(restored); expect(trigger).toHaveFocus();
  const all = openInspector(/^查看全部 6 条评级样本/, '券商评级样本').dialog;
  expect(within(all).getAllByRole('article')).toHaveLength(result.data.broker_ratings!.samples.length);
  result.data.broker_ratings!.samples.forEach(sample => expect(within(all).getByText(sample.broker)).toBeVisible());
});

it.each(['reported total mismatch', 'missing records', 'category mismatch', 'duplicate broker', 'broker count mismatch', 'invalid publication date', 'fractional category count', 'unverified source status'])('does not display a broker distribution with %s', async issue => {
  const result = marketIntelligence();
  const ratings = result.data.broker_ratings!;
  if (issue === 'reported total mismatch') ratings.sample_count = 7;
  if (issue === 'missing records') ratings.samples.pop();
  if (issue === 'category mismatch') ratings.samples[0].rating_key = 'hold';
  if (issue === 'duplicate broker') ratings.samples[1].broker_id = ratings.samples[0].broker_id;
  if (issue === 'broker count mismatch') ratings.broker_count = 3;
  if (issue === 'invalid publication date') ratings.samples[0].published_at = '2026-02-30';
  if (issue === 'fractional category count') ratings.distribution[0].count = 3.5;
  if (issue === 'unverified source status') ratings.status = 'insufficient';
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const distribution = within(screen.getByRole('region', { name: '券商评级分布' }));
  expect(distribution.getByText('暂无可核验的评级分布')).toBeVisible();
  expect(distribution.queryByRole('img')).not.toBeInTheDocument();
  expect(distribution.queryByRole('button', { name: /^查看买入评级样本/ })).not.toBeInTheDocument();
});

it('describes tied broker-rating groups without claiming a single majority view', async () => {
  const result = marketIntelligence();
  const ratings = result.data.broker_ratings!;
  ratings.distribution.forEach(group => { group.count = 2; });
  ratings.samples.forEach((sample, index) => {
    const group = ratings.distribution[Math.floor(index / 2)];
    sample.rating_key = group.key; sample.rating = group.label;
  });
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const distribution = within(screen.getByRole('region', { name: '券商评级分布' }));
  expect(distribution.getByRole('img', { name: '券商评级分布，共 6 条最新评级样本' })).toBeVisible();
  expect(distribution.getByText('样本中最多（并列）')).toBeVisible();
  expect(distribution.getByText('买入 / 持有 / 卖出')).toBeVisible();
  expect(distribution.queryByText('样本中最多')).not.toBeInTheDocument();
});

it('retains historical fund and broker evidence with explicit stale labels and original observation dates', async () => {
  const result = marketIntelligence();
  const capital = result.data.capital!;
  capital.status = 'stale'; capital.as_of = '2026-09-11'; capital.period = { from: '2026-09-09', to: '2026-09-11' };
  capital.series.forEach((point, index) => { point.date = `2026-09-${String(9 + index).padStart(2, '0')}`; });
  const flowSource = result.data.sources.find(source => source.id === 'flow')!;
  flowSource.status = 'stale'; flowSource.as_of = '2026-09-11'; flowSource.fetched_at = '2026-10-13T02:00:00Z';
  const broker = result.data.broker_ratings!;
  broker.status = 'stale'; broker.as_of = '2026-06-29'; broker.period = { from: '2026-06-24', to: '2026-06-29' };
  broker.samples.forEach((sample, index) => { sample.published_at = `2026-06-${24 + index}`; });
  const ratingSource = result.data.sources.find(source => source.id === 'broker-ratings')!;
  ratingSource.status = 'stale'; ratingSource.as_of = '2026-06-29'; ratingSource.fetched_at = '2026-10-13T02:00:00Z';
  get.mockResolvedValue(result);
  render(<StockIntelligencePanel {...panelProps} />);
  expect(await screen.findByText('测试研究结论')).toBeVisible();
  const flowPanel = screen.getByRole('region', { name: '资金与股价联动' });
  const flow = within(flowPanel);
  expect(flow.getByText('历史快照 · 数据滞后')).toBeVisible();
  expect(flow.getByRole('img', { name: /每日资金净额与同日收盘图/ })).toBeVisible();
  expect(flowReadout(flowPanel)).toHaveTextContent('2026-09-11');
  expect(flowReadout(flowPanel)).not.toHaveTextContent('2026-10-13');
  const fundSource = openInspector(/^查看资金依据/, '测试资金流').dialog;
  expect(within(fundSource).getByText('数据滞后')).toBeVisible();
  expect(within(fundSource).getByText('2026-09-11')).toBeVisible();
  closeInspector(fundSource);
  const ratings = within(screen.getByRole('region', { name: '券商评级分布' }));
  expect(ratings.getByText('历史样本 · 数据滞后')).toBeVisible();
  expect(ratings.getByRole('img', { name: '券商评级分布，共 6 条最新评级样本' })).toBeVisible();
  expect(ratings.getByText(/样本日期 2026-06-24 — 2026-06-29/)).toBeVisible();
  const brokerSource = openInspector(/^查看评级来源/, '测试券商评级源').dialog;
  expect(within(brokerSource).getByText('数据滞后')).toBeVisible();
  expect(within(brokerSource).getByText('2026-06-29')).toBeVisible();
});
