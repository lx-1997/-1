import React, { useCallback, useEffect, useId, useRef, useState, useSyncExternalStore } from 'react';
import { Alert, Button, Checkbox, Collapse, ConfigProvider, Drawer, Empty, Input, InputNumber, Radio, Select, Space, Spin, Table, Tabs } from 'antd';
import { DownloadOutlined, ExperimentOutlined, UploadOutlined } from '@ant-design/icons';
import { Bar, BarChart, CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import {
  cancelInvestmentResearchJob, createInvestmentResearchJob, getInvestmentResearchCatalog,
  getInvestmentResearchJob, getInvestmentResearchTemplate,
  type InvestmentResearchCatalog, type InvestmentResearchCategory, type InvestmentResearchChart,
  type InvestmentResearchJob, type InvestmentResearchReport, type InvestmentResearchTable, type InvestmentResearchTool,
} from '../services/researchService';
import { getAuthSnapshot, isAuthRevisionCurrent, registerSessionCleanup, subscribeAuth } from '../state/authSession';
import type { QuantLabResponse } from '../services/quantService';
import './InvestmentResearchCenter.css';

const MAX_BYTES = 2 * 1024 * 1024;
const TOOLS: Array<{ tool: InvestmentResearchTool; category: InvestmentResearchCategory; title: string; description: string; input: string }> = [
  { tool: 'performance', category: 'strategy', title: '收益体检', description: '把收益、回撤与基准放在一起核对', input: '至少 30 个日期的每日净值，或小数形式的日收益。成交盈亏不能代替账户日收益。' },
  { tool: 'portfolio', category: 'strategy', title: '组合风险', description: '看清风险集中在哪里、哪些资产一起跌', input: '至少 60 个共同日期的复权收盘价，最多 20 个资产；权重为小数且合计 1。全部价格须统一估值币种。' },
  { tool: 'factors', category: 'strategy', title: '因子研究', description: '按时间划分样本，检验因子是否经得住验证', input: '至少 180 个日期、5 个标的的日线 OHLCV；最多 20 个标的、5,000 行。样本按时间分开，不保证未来收益。' },
  { tool: 'financials', category: 'company', title: '财务对比', description: '跨报告期追踪利润、现金流与财务质量', input: '输入美股代码读取 SEC 最近 8 个季度报表，或上传自己的报表指标。季度金额必须是独立单季额；同行只比较相同报告期，首期平均余额指标可以缺失。' },
  { tool: 'institutions', category: 'company', title: '机构披露', description: '查看公开持仓与披露后的变化', input: '输入 SEC CIK 查询，或导入规范披露 CSV / 官方 XML / JSON。13F 是已披露的历史持仓，不是实时仓位。' },
  { tool: 'funds', category: 'assets', title: '基金穿透', description: '识别基金间重仓重合与组合的间接敞口', input: '持仓权重为小数，每只基金注明披露日期。部分披露不会被放大到 100%；披露日期不兼容时不计算可比重合度。' },
  { tool: 'bonds', category: 'assets', title: '可转债筛选', description: '对比价格、转股溢价与条款风险', input: '提供转债价格、转股价、正股价和数据日期。未披露的条款保留为待核实，不能仅凭面值推算精确到期收益率。' },
];
const CATEGORIES: Array<{ key: InvestmentResearchCategory; label: string }> = [
  { key: 'strategy', label: '策略研究' }, { key: 'company', label: '公司研究' }, { key: 'assets', label: '资产筛选' },
];
const NUMBERS = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 4 });
const PERCENTAGES = new Intl.NumberFormat('zh-CN', { maximumFractionDigits: 2 });
const COLORS = ['#628fe5', '#b493e4', '#42ad9b', '#d9a34c', '#e0787d'];

export interface InvestmentResearchDraft {
  mode: 'file' | 'json' | 'sec' | 'seed' | 'live'; file: File | null; json: string; currency: string; name: string;
  symbols: string; startDate: string; endDate: string;
  seedInput?: Record<string, unknown>; seedLabel?: string; seedNote?: string;
  riskFreeRate: number | null; weights: string; fxNormalized: boolean; periodType: '' | 'quarterly' | 'annual';
  cik: string; form: '13F-HR' | '4'; comparePrevious: boolean; valueUnit: '' | 'USD' | 'USD_thousands';
  reportDate: string; filingDate: string; positions: string; directPositions: string;
  maxPrice: number | null; maxPremium: number | null; minVolume: number | null; excludeRedemption: boolean;
}
const emptyDraft = (): InvestmentResearchDraft => ({
  mode: 'file', file: null, json: '', currency: '', name: '', symbols: '', startDate: '', endDate: '', riskFreeRate: null, weights: '', fxNormalized: false,
  periodType: '', cik: '', form: '13F-HR', comparePrevious: false, valueUnit: '', reportDate: '', filingDate: '',
  positions: '', directPositions: '', maxPrice: null, maxPremium: null, minVolume: null, excludeRedemption: true,
});
export interface InvestmentResearchSeed { tool: InvestmentResearchTool; input: Record<string, unknown>; label: string; note?: string }
function emptyDrafts(seed?: InvestmentResearchSeed): Record<InvestmentResearchTool, InvestmentResearchDraft> {
  return Object.fromEntries(TOOLS.map(item => [item.tool, { ...emptyDraft(), mode: seed?.tool === item.tool ? 'seed' : item.tool === 'institutions' ? 'sec' : item.tool === 'financials' ? 'live' : 'file', ...(seed?.tool === item.tool ? { seedInput: seed.input, seedLabel: seed.label, seedNote: seed.note, name: String(seed.input.name || '') } : {}) }])) as Record<InvestmentResearchTool, InvestmentResearchDraft>;
}

export function buildBacktestResearchSeed(result: QuantLabResponse | null): InvestmentResearchSeed | undefined {
  if (!result) return undefined;
  const { dates, equity_curve, benchmark_curve } = result.backtest;
  const valid = dates.map((date, index) => ({ date, value: equity_curve[index], index })).filter(row => /^\d{4}-\d{2}-\d{2}$/.test(row.date) && Number.isFinite(Date.parse(row.date)) && new Date(row.date).toISOString().slice(0, 10) === row.date && Number.isFinite(row.value) && row.value > 0);
  // Do not bridge an invalid/missing valuation day when deriving daily returns.
  if (valid.length < 30 || valid.length !== dates.length || equity_curve.length !== dates.length) return undefined;
  const provenance = result.backtest.data_source_details?.[result.benchmark] || result.data_source_details?.[result.benchmark];
  const realBenchmark = provenance?.is_synthetic === false && !!provenance.source && !/synthetic|demo|mock|fallback|simulat/i.test(`${provenance.source} ${provenance.quality || ''}`) && benchmark_curve.length === dates.length && benchmark_curve.every(value => Number.isFinite(value) && value > 0);
  return { tool: 'performance', label: '用当前回测生成报告', input: {
    name: `${result.name || result.strategy_label} · 历史回测`, _source_name: `QuantLab 历史回测 · ${result.generated_at}`,
    net_values: valid.map(row => ({ date: row.date, value: row.value, ...(realBenchmark ? { benchmark_value: benchmark_curve[row.index] } : {}) })),
  }, note: `已选当前历史回测完整的 ${valid.length} 个日期。${realBenchmark ? `基准 ${result.benchmark} 具有真实行情来源，已带入同日序列。` : '未带入缺少可核实真实来源或完整净值的基准。'} 这是历史模拟结果，请确认估值币种后运行。` };
}
function parseObject(text: string): Record<string, unknown> {
  if (new Blob([text]).size > MAX_BYTES) throw new Error('JSON 超过 2 MiB，请缩小数据范围。');
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error('JSON 格式不正确，请检查括号、引号和逗号。'); }
  if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('请输入包含数据字段的 JSON 对象。');
  return value as Record<string, unknown>;
}
function parseArray(text: string, label: string): unknown[] {
  let value: unknown;
  try { value = JSON.parse(text); } catch { throw new Error(`${label}格式不正确，请输入 JSON 数组。`); }
  if (!Array.isArray(value)) throw new Error(`${label}应为 JSON 数组。`);
  return value;
}
function parseAllocation(text: string, label: string): unknown[] {
  if (text.trim().startsWith('[')) return parseArray(text, label);
  return text.trim().split(/\n|;/).filter(line => line.trim()).map(line => {
    const parts = line.trim().split(/[,，\t]/).map(value => value.trim());
    const percent = parts[1]?.replace(/%$/, '');
    if (parts.length !== 2 || !parts[0] || !percent || !Number.isFinite(Number(percent)) || Number(percent) <= 0 || Number(percent) > 100) throw new Error(`${label}请每行填写“代码或名称,权重%”，例如“你的代码,60”；权重应大于 0 且不超过 100%。`);
    return { symbol: parts[0], weight: Number(percent) / 100 };
  });
}
/** File contents are encoded only after the explicit Run action. */
export async function buildInvestmentResearchInput(tool: InvestmentResearchTool, draft: InvestmentResearchDraft): Promise<Record<string, unknown>> {
  if (draft.mode === 'json') return parseObject(draft.json);
  if (draft.mode === 'live') {
    const symbols = Array.from(new Set(draft.symbols.trim().toUpperCase().split(/[,，;\s]+/).filter(Boolean)));
    const maximum = tool === 'financials' ? 5 : 20;
    if (!symbols.length || symbols.length > maximum || symbols.some(symbol => !/^[A-Z0-9][A-Z0-9.^:-]{0,19}$/.test(symbol))) throw new Error(`请填写 1–${maximum} 个有效${tool === 'financials' ? '美股' : '资产'}代码，用逗号或空格分开。`);
    if (tool === 'financials') return { symbols };
    if (tool !== 'portfolio' && tool !== 'factors') throw new Error('此工具不支持按代码获取行情。');
    if (!draft.startDate || !draft.endDate || draft.startDate >= draft.endDate) throw new Error('请填写开始和结束日期，开始日期应早于结束日期。');
    if (!draft.currency) throw new Error('请明确选择行情的统一估值币种。');
    if (tool === 'portfolio' && !draft.weights.trim()) throw new Error('按代码查询组合行情时，请填写资产配置，权重合计 100%。');
    return { symbols, start_date: draft.startDate, end_date: draft.endDate, currency: draft.currency, ...(tool === 'portfolio' ? { weights: parseAllocation(draft.weights, '资产配置'), fx_normalized: false } : {}) };
  }
  if (draft.mode === 'seed') {
    if (tool !== 'performance' || !draft.seedInput) throw new Error('当前回测数据已清除，请重新打开入口。');
    if (!draft.currency) throw new Error('请明确选择当前回测使用的估值币种。');
    return { ...draft.seedInput, currency: draft.currency, ...(draft.name.trim() ? { name: draft.name.trim() } : {}), ...(draft.riskFreeRate !== null ? { risk_free_rate: draft.riskFreeRate / 100 } : {}) };
  }
  if (draft.mode === 'sec') {
    if (tool !== 'institutions' || !/^\d{1,10}$/.test(draft.cik.trim())) throw new Error('请输入 1–10 位数字的 SEC CIK。');
    return { cik: draft.cik.trim(), form: draft.form, compare_previous: draft.form === '13F-HR' && draft.comparePrevious };
  }
  const file = draft.file;
  if (!file) throw new Error('请先选择自己的数据文件。');
  if (file.size > MAX_BYTES) throw new Error('文件超过 2 MiB，请缩小数据范围。');
  if (!(tool === 'institutions' ? /\.(csv|json|xml)$/i : /\.(csv|json)$/i).test(file.name)) throw new Error(tool === 'institutions' ? '请选择 CSV、JSON 或官方 XML 文件。' : '请选择 CSV 或 JSON 数据文件。');
  const input: Record<string, unknown> = {};
  // A complete JSON file owns its own options, just like the JSON editor.
  if (!/\.json$/i.test(file.name)) {
    if (tool === 'performance' || tool === 'portfolio') {
      if (!draft.currency) throw new Error('请明确选择数据的统一币种。');
      input.currency = draft.currency;
    }
    if (tool === 'performance') {
      if (draft.name.trim()) input.name = draft.name.trim();
      if (draft.riskFreeRate !== null) input.risk_free_rate = draft.riskFreeRate / 100;
    }
    if (tool === 'portfolio') {
      if (draft.weights.trim()) input.weights = parseAllocation(draft.weights, '资产配置');
      input.fx_normalized = draft.fxNormalized;
    }
    if (tool === 'financials') {
      if (!draft.periodType) throw new Error('请明确选择独立季度或年度报表口径。');
      input.period_type = draft.periodType;
    }
    if (tool === 'institutions') {
      input.form = draft.form;
      if (draft.form === '13F-HR') {
        if (!draft.valueUnit) throw new Error('请确认 13F 持仓金额是美元还是千美元。');
        input.value_unit = draft.valueUnit;
        if (/\.xml$/i.test(file.name) && !draft.reportDate) throw new Error('13F XML 请补充报告期截止日。');
      }
      if (draft.reportDate) input.report_date = draft.reportDate;
      if (draft.filingDate) input.filing_date = draft.filingDate;
    }
    if (tool === 'funds') {
      if (draft.positions.trim()) input.positions = parseAllocation(draft.positions, '基金配置');
      if (draft.directPositions.trim()) input.direct_positions = parseAllocation(draft.directPositions, '直接持股');
    }
    if (tool === 'bonds') {
      input.filters = {
        exclude_redemption: draft.excludeRedemption,
        ...(draft.maxPrice !== null ? { max_price: draft.maxPrice } : {}),
        ...(draft.maxPremium !== null ? { max_premium: draft.maxPremium / 100 } : {}),
        ...(draft.minVolume !== null ? { min_volume: draft.minVolume } : {}),
      };
    }
  }
  const bytes = new Uint8Array(await file.arrayBuffer());
  const parts: string[] = [];
  for (let offset = 0; offset < bytes.length; offset += 32768) parts.push(String.fromCharCode(...Array.from(bytes.subarray(offset, offset + 32768))));
  return { ...input, file_name: file.name, content_base64: btoa(parts.join('')) };
}

function messageOf(error: unknown, fallback: string): string {
  const candidate = error as { response?: { data?: { detail?: unknown } }; message?: string };
  const detail = candidate?.response?.data?.detail;
  return typeof detail === 'string' ? detail : candidate?.message || fallback;
}
export function formatResearchValue(value: unknown, percent = false): string {
  if (value === null || value === undefined) return '—';
  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return '—';
    if (percent) return `${PERCENTAGES.format(value * 100)}%`;
    // Daily covariance and other small nonzero values must not appear as zero.
    if (value === 0) return '0';
    return Math.abs(value) < .0001 ? value.toExponential(2) : NUMBERS.format(value);
  }
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}
function csvCell(value: unknown): string {
  let text = value === null || value === undefined ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value);
  if (typeof value === 'string' && /^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replace(/"/g, '""')}"`;
}
export function researchTableCsv(table: InvestmentResearchTable): string {
  return '\uFEFF' + [table.columns.map(column => csvCell(column.type === 'percent' ? `${column.label}（小数，0.1=10%）` : column.label)).join(','), ...table.rows.map(row => table.columns.map(column => csvCell(row[column.key])).join(','))].join('\r\n');
}
function download(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const revoke = URL.revokeObjectURL.bind(URL);
  const link = document.createElement('a'); link.href = url; link.download = filename;
  document.body.appendChild(link); link.click(); link.remove();
  window.setTimeout(() => revoke(url), 1000);
}
const safeName = (value: string) => value.replace(/[<>:"/\\|?*\u0000-\u001f]/g, '_').trim().slice(0, 80) || '研究报告';
function safeSourceUrl(value: string | undefined): string | undefined {
  if (!value) return undefined;
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.toString() : undefined; } catch { return undefined; }
}

function ReportChart({ chart }: { chart: InvestmentResearchChart }) {
  if (chart.type === 'heatmap') {
    const matrix = chart.matrix || [];
    if (!matrix.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可展示的图表数据" />;
    const labels = chart.labels || [];
    return <div className="research-heatmap-scroll"><table className="research-heatmap" aria-label={chart.title}>
      <thead><tr><th scope="col">维度</th>{labels.map((label, index) => <th scope="col" key={index}>{label}</th>)}</tr></thead>
      <tbody>{matrix.map((row, rowIndex) => <tr key={rowIndex}><th scope="row">{chart.row_labels?.[rowIndex] || labels[rowIndex] || rowIndex + 1}</th>{row.map((value, column) => <td key={column} style={{ background: typeof value === 'number' ? `rgba(${value >= 0 ? '75, 133, 220' : '206, 120, 94'},${Math.min(.55, Math.abs(value) * .55 + .06)})` : undefined }}>{formatResearchValue(value, chart.value_type === 'percent')}</td>)}</tr>)}</tbody>
    </table></div>;
  }
  const data = chart.data || [];
  const series = chart.series || [];
  if (!data.length || !series.length) return <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="没有可展示的图表数据" />;
  const shared = <><CartesianGrid strokeDasharray="3 3" opacity={.18} /><XAxis dataKey={chart.x_key || 'date'} tick={{ fontSize: 10 }} minTickGap={28} /><YAxis width={58} tick={{ fontSize: 10 }} tickFormatter={value => formatResearchValue(value, chart.value_type === 'percent')} /><Tooltip formatter={(value: unknown) => `${formatResearchValue(value, chart.value_type === 'percent')}${chart.unit && chart.value_type !== 'percent' ? ` ${chart.unit}` : ''}`} /><Legend wrapperStyle={{ fontSize: 12, paddingTop: 10 }} /></>;
  return <div className="research-chart" role="img" aria-label={chart.title}>{chart.unit && <p className="research-chart-unit">单位：{chart.unit}</p>}<ResponsiveContainer width="100%" height={280}>
    {chart.type === 'bar' ? <BarChart data={data}>{shared}{series.map((item, index) => <Bar key={item.key} dataKey={item.key} name={item.label} fill={COLORS[index % COLORS.length]} />)}</BarChart> : <LineChart data={data}>{shared}{series.map((item, index) => <Line key={item.key} type="monotone" dataKey={item.key} name={item.label} stroke={COLORS[index % COLORS.length]} dot={false} strokeWidth={2} connectNulls={false} />)}</LineChart>}
  </ResponsiveContainer></div>;
}

function ReportWarnings({ warnings }: { warnings: string[] }) {
  if (!warnings.length) return null;
  const disclosureFields: Record<string, string> = { RevenueFromContractWithCustomerExcludingAssessedTax: '营收', Revenues: '营收', SalesRevenueNet: '营收', NetIncomeLoss: '净利润', ProfitLoss: '净利润', NetCashProvidedByUsedInOperatingActivities: '经营现金流', PaymentsToAcquirePropertyPlantAndEquipment: '资本支出' };
  const groups = new Map<string, { text: string; count: number; fields: Set<string> }>();
  warnings.forEach(text => {
    // Summarize repeated per-company/per-period caveats, keeping every original below.
    const summary = text.replace(/^(?:SEC\s+)?[A-Z0-9.^:-]{1,20}\s+\d{4}(?:-\d{2}-\d{2}|Q[1-4])\s+/, '');
    const fieldMatch = summary.match(/^([A-Za-z]+)\s+(.+)$/);
    const field = fieldMatch && disclosureFields[fieldMatch[1]];
    const key = field ? fieldMatch![2] : summary;
    const group = groups.get(key);
    if (group) { group.count += 1; if (field) group.fields.add(field); }
    else groups.set(key, { text: key, count: 1, fields: new Set(field ? [field] : []) });
  });
  return <div className="research-warning-review"><Alert type="warning" showIcon message={`需核对 ${warnings.length} 条数据与使用说明`} description={<ul>{Array.from(groups.values()).map((item, index) => <li key={index}>{item.fields.size > 0 ? `${Array.from(item.fields).join('、')}：` : ''}{item.text}{item.count > 1 && <strong className="research-warning-count">（{item.count} 条；涉及标的与日期见完整说明）</strong>}</li>)}</ul>} />
    {warnings.length > 1 && <Collapse size="small" items={[{ key: 'warnings', label: `完整说明 · ${warnings.length} 条`, children: <ol>{warnings.map((item, index) => <li key={index}>{item}</li>)}</ol> }]} />}
  </div>;
}

function ReportTable({ table, reportTitle }: { table: InvestmentResearchTable; reportTitle: string }) {
  const content = <div className="research-table-body">
    <div className="research-table-actions"><span>{table.rows.length} 条记录 · 缺失项保留为 —</span><Button size="small" aria-label={`导出 ${table.title} CSV`} onClick={() => download(new Blob([researchTableCsv(table)], { type: 'text/csv;charset=utf-8' }), `${safeName(reportTitle)}-${safeName(table.title)}.csv`)}>导出 CSV</Button></div>
    <Table size="small" rowKey="_research_row_key" dataSource={table.rows.map((row, index) => ({ ...row, _research_row_key: `${table.key}-${index}` }))}
      columns={table.columns.map(column => ({ title: column.label, dataIndex: column.key, key: column.key, align: column.type === 'number' || column.type === 'percent' ? 'right' as const : 'left' as const, render: (value: unknown) => formatResearchValue(value, column.type === 'percent') }))}
      pagination={{ pageSize: 10, showSizeChanger: false, hideOnSinglePage: true }} scroll={{ x: Math.max(480, table.columns.length * 125) }} locale={{ emptyText: '没有符合条件的记录' }} />
  </div>;
  const collapsible = table.collapsible || table.key === 'financial_history';
  if (collapsible) return <Collapse className="research-history-collapse" defaultActiveKey={table.default_collapsed === false ? ['history'] : []} items={[{ key: 'history', label: `${table.title} · ${table.rows.length} 条记录`, children: content }]} />;
  return <section className="research-report-card" aria-label={table.title}><h3>{table.title}</h3>{content}</section>;
}

export function InvestmentResearchReportView({ report }: { report: InvestmentResearchReport }) {
  const latestComparison = report.tables.find(table => table.key === 'latest_comparison');
  return <section className="research-report" aria-label="研究报告">
    <div className="research-report-heading"><h2>{report.title}</h2><Space wrap>
      <Button aria-label="导出报告 JSON" icon={<DownloadOutlined aria-hidden="true" />} onClick={() => download(new Blob([JSON.stringify(report, null, 2)], { type: 'application/json' }), `${safeName(report.title)}.json`)}>导出报告 JSON</Button>
      {report.report_html && <Button aria-label="下载完整 HTML 报告" onClick={() => download(new Blob([report.report_html!], { type: 'text/html;charset=utf-8' }), `${safeName(report.title)}.html`)}>完整报告</Button>}
    </Space></div>
    {!!report.summary.length && <div className="research-summary">{report.summary.map((item, index) => <div key={index} className="research-summary-item"><span>{item.label}</span><strong>{formatResearchValue(item.value, item.unit === '%')}{item.unit && item.unit !== '%' ? <small> {item.unit}</small> : null}</strong></div>)}</div>}
    <ReportWarnings warnings={report.warnings} />
    {latestComparison && <ReportTable table={latestComparison} reportTitle={report.title} />}
    {report.charts.map(chart => <section className="research-report-card" key={chart.key}><h3>{chart.title}</h3><ReportChart chart={chart} /></section>)}
    {report.tables.filter(table => table !== latestComparison).map(table => <ReportTable key={table.key} table={table} reportTitle={report.title} />)}
    <div className="research-report-card"><h3>数据来源与时点</h3>{report.sources.length ? <ul className="research-sources">{report.sources.map((source, index) => {
      const url = safeSourceUrl(source.url);
      return <li key={index}><strong>{url ? <a href={url} target="_blank" rel="noopener noreferrer">{source.name}</a> : source.name}</strong><span>{[source.as_of && `数据日期：${source.as_of}`, source.coverage, source.currency].filter(Boolean).join(' · ') || '来源未提供时点'}</span></li>;
    })}</ul> : <p>报告未提供来源明细，请核对上传文件的来源。</p>}</div>
    {!!report.methodology.length && <Collapse items={[{ key: 'methodology', label: '计算口径与方法', children: <ul>{report.methodology.map((item, index) => <li key={index}>{item}</li>)}</ul> }]} />}
  </section>;
}

function DraftFields({ tool, draft, change }: { tool: InvestmentResearchTool; draft: InvestmentResearchDraft; change: (changes: Partial<InvestmentResearchDraft>) => void }) {
  if (draft.mode === 'json') return <label className="research-field research-field-wide"><span>完整 JSON 数据</span><Input.TextArea aria-label="完整 JSON 数据" value={draft.json} onChange={event => change({ json: event.target.value })} rows={9} placeholder="粘贴自己的完整数据对象；字段定义可参考下载的模板与数据要求。" /></label>;
  if (draft.mode === 'live' && tool === 'financials') return <div className="research-fields">
    <label className="research-field research-field-wide"><span>美股代码（1–5 个，逗号或空格分开）</span><Input aria-label="美股代码" value={draft.symbols} onChange={event => change({ symbols: event.target.value })} placeholder="请输入要比较的真实美股代码" /></label>
    <p className="research-field-help research-field-wide">点击「开始分析」后读取 SEC 公开披露的最近 8 个季度数据，再进行财务对比。报告保留真实财年期末和披露日期；尚未配置访问条件或来源缺失时会明确提示，可改用文件导入。</p>
  </div>;
  if (draft.mode === 'sec') return <div className="research-fields">
    <label className="research-field"><span>机构 / 披露主体 CIK</span><Input aria-label="机构 / 披露主体 CIK" inputMode="numeric" value={draft.cik} onChange={event => change({ cik: event.target.value })} placeholder="输入 SEC 的数字 CIK" /></label>
    <label className="research-field"><span>披露类型</span><Select aria-label="披露类型" value={draft.form} onChange={form => change({ form })} options={[{ value: '13F-HR', label: '13F 季度持仓' }, { value: '4', label: 'Form 4 内部人交易' }]} /></label>
    {draft.form === '13F-HR' && <Checkbox checked={draft.comparePrevious} onChange={event => change({ comparePrevious: event.target.checked })}>比较上期披露</Checkbox>}
    <p className="research-field-help research-field-wide">仅在点击「开始分析」后查询 SEC。未配置合适访问身份时，请改用官方披露文件。</p>
  </div>;
  if (draft.file && /\.json$/i.test(draft.file.name)) return <p className="research-field-help">JSON 文件须包含完整数据对象及所需币种、权重或报告口径。</p>;
  return <div className="research-fields">
    {draft.mode === 'live' && <>
      <label className="research-field research-field-wide"><span>资产代码（逗号或空格分开）</span><Input aria-label="资产代码" value={draft.symbols} onChange={event => change({ symbols: event.target.value })} placeholder="填写要研究的真实资产代码" /></label>
      <label className="research-field"><span>开始日期</span><Input aria-label="开始日期" type="date" value={draft.startDate} onChange={event => change({ startDate: event.target.value })} /></label>
      <label className="research-field"><span>结束日期</span><Input aria-label="结束日期" type="date" value={draft.endDate} onChange={event => change({ endDate: event.target.value })} /></label>
      <p className="research-field-help research-field-wide">点击「开始分析」后获取真实历史行情。无法确认来源、复权或币种时会提示修正，缺失行情不会被替换成模拟价格。</p>
    </>}
    {(tool === 'performance' || tool === 'portfolio' || draft.mode === 'live') && <label className="research-field"><span>{tool === 'performance' ? '收益币种' : '统一估值币种'}</span><Select aria-label={tool === 'performance' ? '收益币种' : '统一估值币种'} value={draft.currency || undefined} placeholder="请选择" onChange={currency => change({ currency })} options={[{ value: 'CNY', label: '人民币 · CNY' }, { value: 'USD', label: '美元 · USD' }, { value: 'HKD', label: '港元 · HKD' }]} /></label>}
    {tool === 'performance' && <>
      <label className="research-field"><span>报告名称（可选）</span><Input aria-label="报告名称" value={draft.name} onChange={event => change({ name: event.target.value })} /></label>
      <label className="research-field"><span>年化无风险利率 %（可选）</span><InputNumber aria-label="年化无风险利率 %" min={0} max={100} value={draft.riskFreeRate} onChange={riskFreeRate => change({ riskFreeRate })} /></label>
    </>}
    {tool === 'portfolio' && <>
      <label className="research-field research-field-wide"><span>资产配置（每行「代码,权重 %」，合计 100%）</span><Input.TextArea aria-label="资产配置" value={draft.weights} onChange={event => change({ weights: event.target.value })} rows={3} placeholder={'格式：你的资产代码,60\n另一资产代码,40'} /><small className="research-field-help">CSV 已有 weight 列（小数权重，合计 1）可留空；也支持完整的小数权重 JSON 数组。价格中的代码须与配置一致。</small></label>
      {draft.mode !== 'live' && <Checkbox className="research-field-wide" checked={draft.fxNormalized} onChange={event => change({ fxNormalized: event.target.checked })}>跨币种价格已换算到同一币种，且复权口径一致</Checkbox>}
    </>}
    {tool === 'financials' && <label className="research-field"><span>报表口径</span><Select aria-label="报表口径" value={draft.periodType || undefined} placeholder="请明确选择" onChange={periodType => change({ periodType })} options={[{ value: 'quarterly', label: '独立单季金额' }, { value: 'annual', label: '完整年度金额' }]} /></label>}
    {tool === 'institutions' && <>
      <label className="research-field"><span>披露类型</span><Select aria-label="披露类型" value={draft.form} onChange={form => change({ form })} options={[{ value: '13F-HR', label: '13F 季度持仓' }, { value: '4', label: 'Form 4 内部人交易' }]} /></label>
      {draft.form === '13F-HR' && <label className="research-field"><span>原始持仓金额单位</span><Select aria-label="原始持仓金额单位" value={draft.valueUnit || undefined} placeholder="请核对原文件" onChange={valueUnit => change({ valueUnit })} options={[{ value: 'USD', label: '美元' }, { value: 'USD_thousands', label: '千美元' }]} /></label>}
      <label className="research-field"><span>报告期截止日（13F XML 必填）</span><Input aria-label="报告期截止日" type="date" value={draft.reportDate} onChange={event => change({ reportDate: event.target.value })} /></label>
      <label className="research-field"><span>披露日（文件未注明时补充）</span><Input aria-label="披露日" type="date" value={draft.filingDate} onChange={event => change({ filingDate: event.target.value })} /></label>
    </>}
    {tool === 'funds' && <>
      <label className="research-field research-field-wide"><span>基金配置（可选，每行「基金名称或代码,权重 %」）</span><Input.TextArea aria-label="基金配置" value={draft.positions} onChange={event => change({ positions: event.target.value })} rows={3} placeholder="你的基金名称或代码,60" /><small className="research-field-help">名称须与文件中的 fund 一致；留空按基金等权并在报告注明。也支持小数权重 JSON 数组。</small></label>
      <label className="research-field research-field-wide"><span>直接持股（可选，每行「股票代码,权重 %」）</span><Input.TextArea aria-label="直接持股" value={draft.directPositions} onChange={event => change({ directPositions: event.target.value })} rows={3} placeholder="你的股票代码,10" /></label>
    </>}
    {tool === 'bonds' && <>
      <label className="research-field"><span>最高价格（可选）</span><InputNumber aria-label="最高价格" min={0} value={draft.maxPrice} onChange={maxPrice => change({ maxPrice })} /></label>
      <label className="research-field"><span>最高转股溢价率 %（可选）</span><InputNumber aria-label="最高转股溢价率 %" value={draft.maxPremium} onChange={maxPremium => change({ maxPremium })} /></label>
      <label className="research-field"><span>最低成交量（可选，按原数据单位）</span><InputNumber aria-label="最低成交量" min={0} value={draft.minVolume} onChange={minVolume => change({ minVolume })} /></label>
      <Checkbox checked={draft.excludeRedemption} onChange={event => change({ excludeRedemption: event.target.checked })}>排除已触发强赎的转债</Checkbox>
    </>}
  </div>;
}

export default function InvestmentResearchCenter({ seed, initialTool = 'performance' }: { seed?: InvestmentResearchSeed; initialTool?: InvestmentResearchTool }) {
  const revision = useSyncExternalStore(subscribeAuth, () => getAuthSnapshot().revision);
  const id = `research-${useId().replace(/:/g, '')}`;
  const [category, setCategory] = useState<InvestmentResearchCategory>(TOOLS.find(item => item.tool === (seed?.tool || initialTool))?.category || 'strategy');
  const [tool, setTool] = useState<InvestmentResearchTool>(seed?.tool || initialTool);
  const [drafts, setDrafts] = useState(() => emptyDrafts(seed));
  const [catalog, setCatalog] = useState<InvestmentResearchCatalog | null>(null);
  const [catalogError, setCatalogError] = useState('');
  const [catalogTick, setCatalogTick] = useState(0);
  const [error, setError] = useState('');
  const [notice, setNotice] = useState('');
  const [job, setJob] = useState<InvestmentResearchJob | null>(null);
  const [report, setReport] = useState<InvestmentResearchReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [downloading, setDownloading] = useState(false);
  const [pollFailed, setPollFailed] = useState(false);
  const catalogRequest = useRef<AbortController | null>(null);
  const request = useRef<AbortController | null>(null);
  const templateRequest = useRef<AbortController | null>(null);
  const cancellationRequest = useRef<AbortController | null>(null);
  const activeJob = useRef<string | null>(null);
  const generation = useRef(0);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const draft = drafts[tool];
  const config = TOOLS.find(item => item.tool === tool)!;
  const availability = catalog?.tools.find(item => item.tool === tool);
  const changeDraft = (changes: Partial<InvestmentResearchDraft>) => {
    if (busy || downloading) return;
    setDrafts(current => ({ ...current, [tool]: { ...current[tool], ...changes } }));
    setError(''); setNotice(''); setReport(null); setJob(null);
  };

  const stopLocalWork = useCallback((deleteJob = true) => {
    generation.current += 1;
    if (timer.current) clearTimeout(timer.current);
    request.current?.abort(); templateRequest.current?.abort(); catalogRequest.current?.abort(); cancellationRequest.current?.abort();
    const oldJob = activeJob.current; activeJob.current = null;
    if (deleteJob && oldJob && getAuthSnapshot().account) void cancelInvestmentResearchJob(oldJob).catch(() => {});
  }, []);

  useEffect(() => {
    const unregister = registerSessionCleanup(() => {
      stopLocalWork(); setDrafts(emptyDrafts()); setReport(null); setJob(null); setCatalog(null);
      setBusy(false); setCancelling(false); setDownloading(false); setError(''); setNotice(''); setPollFailed(false);
    });
    return () => { unregister(); stopLocalWork(); };
  }, [revision, stopLocalWork]);
  useEffect(() => {
    if (!getAuthSnapshot().account) { setCatalog(null); return; }
    const controller = new AbortController(); catalogRequest.current = controller;
    setCatalog(null); setCatalogError('');
    getInvestmentResearchCatalog(controller.signal).then(value => {
      if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) setCatalog(value);
    }).catch(failure => {
      if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) setCatalogError(messageOf(failure, '工具状态加载失败，请重试。'));
    });
    return () => controller.abort();
  }, [revision, catalogTick]);

  const selectTool = (next: InvestmentResearchTool) => {
    if (busy || downloading) return;
    stopLocalWork(); setTool(next); setReport(null); setJob(null); setError(''); setNotice(''); setPollFailed(false);
  };
  const receiveJob = (next: InvestmentResearchJob, attempt: number, controller: AbortController) => {
    if (generation.current !== attempt || controller.signal.aborted || !isAuthRevisionCurrent(revision)) return;
    setJob(next); setPollFailed(false);
    if (next.state === 'completed') {
      setBusy(false);
      if (next.result) setReport(next.result);
      else setError('任务已结束，但没有返回可展示报告。请重新分析。');
    } else if (next.state === 'failed' || next.state === 'cancelled') {
      setBusy(false); setError(next.state === 'failed' ? next.error || '分析失败，请核对数据后重试。' : '任务已取消。');
    } else timer.current = setTimeout(() => void poll(next.id, attempt, controller), 1200);
  };
  const poll = async (jobId: string, attempt: number, controller: AbortController) => {
    try { receiveJob(await getInvestmentResearchJob(jobId, controller.signal), attempt, controller); }
    catch (failure) {
      if (generation.current === attempt && !controller.signal.aborted && isAuthRevisionCurrent(revision)) {
        setError(messageOf(failure, '读取任务状态失败，可重试状态查询或取消任务。')); setPollFailed(true);
      }
    }
  };
  const run = async () => {
    if (busy || downloading || !availability?.available || !getAuthSnapshot().account) return;
    stopLocalWork(); const attempt = generation.current; const controller = new AbortController(); request.current = controller;
    setBusy(true); setReport(null); setJob(null); setError(''); setNotice(''); setPollFailed(false);
    try {
      const input = await buildInvestmentResearchInput(tool, draft);
      if (generation.current !== attempt || controller.signal.aborted || !isAuthRevisionCurrent(revision)) return;
      const requestId = globalThis.crypto?.randomUUID?.() || `research-${Date.now()}-${Math.random().toString(36).slice(2)}`;
      const next = await createInvestmentResearchJob({ tool, input, request_id: requestId }, controller.signal);
      if (generation.current !== attempt || controller.signal.aborted || !isAuthRevisionCurrent(revision)) {
        if (isAuthRevisionCurrent(revision)) void cancelInvestmentResearchJob(next.id).catch(() => {});
        return;
      }
      activeJob.current = next.id; receiveJob(next, attempt, controller);
    } catch (failure) {
      if (generation.current === attempt && !controller.signal.aborted && isAuthRevisionCurrent(revision)) {
        setBusy(false); setError(messageOf(failure, '分析未能启动，请检查输入后重试。'));
      }
    }
  };
  const cancel = async () => {
    generation.current += 1; if (timer.current) clearTimeout(timer.current); request.current?.abort();
    const attempt = generation.current; const controller = new AbortController(); cancellationRequest.current = controller;
    const jobId = activeJob.current; setCancelling(true); setError('');
    if (!jobId) { setBusy(false); setCancelling(false); setNotice('已停止本次提交或等待，后续结果不会在此页面显示。'); return; }
    try {
      await cancelInvestmentResearchJob(jobId, controller.signal);
      if (controller.signal.aborted || generation.current !== attempt || !isAuthRevisionCurrent(revision)) return;
      activeJob.current = null; setJob(current => current ? { ...current, state: 'cancelled' } : null);
      setBusy(false); setReport(null); setNotice('任务已取消。');
    } catch (failure) {
      if (!controller.signal.aborted && generation.current === attempt && isAuthRevisionCurrent(revision)) { setError(messageOf(failure, '取消尚未确认，请重试。')); setPollFailed(true); }
    } finally { if (!controller.signal.aborted && generation.current === attempt && isAuthRevisionCurrent(revision)) setCancelling(false); }
  };
  const downloadTemplate = async () => {
    const controller = new AbortController(); templateRequest.current = controller;
    setDownloading(true); setError('');
    try {
      const blob = await getInvestmentResearchTemplate(tool, controller.signal);
      if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) download(blob, `${config.title}-虚构演示模板.csv`);
    } catch (failure) { if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) setError(messageOf(failure, '模板下载失败，请重试。')); }
    finally { if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) setDownloading(false); }
  };
  const chooseFile = (file: File | undefined) => {
    if (!file) return;
    setReport(null); setJob(null); setError(''); setNotice('');
    if (file.size > MAX_BYTES) { changeDraft({ file: null }); setError('文件超过 2 MiB，请缩小数据范围。'); return; }
    if (!(tool === 'institutions' ? /\.(csv|json|xml)$/i : /\.(csv|json)$/i).test(file.name)) { changeDraft({ file: null }); setError('文件格式不支持，请选择所列的数据格式。'); return; }
    changeDraft({ file });
  };

  if (!getAuthSnapshot().account) return <Alert type="info" showIcon message="登录后使用投研工具" description="账户切换后已清除本页输入与结果。" />;
  return <div className="investment-research-center">
    <header className="research-workspace-intro"><span className="research-workspace-mark" aria-hidden="true"><ExperimentOutlined /></span><div><h2>从数据到一份可核对的研究</h2><p>选择研究问题，导入自己的数据，再查看结果与计算口径。</p></div></header>
    <p className="research-privacy">选择文件不会自动上传。点击「开始分析」才会提交；结果为账户私有临时数据，{catalog?.expires_in_minutes || 30} 分钟后过期。关闭或退出会清除本页数据，不会写入持仓或下单。</p>
    <Tabs activeKey={category} onChange={value => { if (!busy && !downloading) { setCategory(value as InvestmentResearchCategory); selectTool(TOOLS.find(item => item.category === value)!.tool); } }} items={CATEGORIES.map(item => ({ key: item.key, label: item.label, disabled: busy || downloading }))} />
    <div className="research-tool-grid" role="group" aria-label="选择研究工具">{TOOLS.filter(item => item.category === category).map(item => <button type="button" key={item.tool} className={`research-tool-card${tool === item.tool ? ' selected' : ''}`} aria-pressed={tool === item.tool} disabled={busy || downloading} onClick={() => selectTool(item.tool)}><strong>{item.title}</strong><span>{item.description}</span></button>)}</div>
    {catalogError ? <Alert type="error" showIcon message="工具状态暂不可用" description={catalogError} action={<Button size="small" onClick={() => setCatalogTick(value => value + 1)}>重试工具状态</Button>} /> : !catalog ? <div className="research-catalog-loading" role="status"><Spin size="small" />正在确认工具状态…</div> : null}
    {catalog && (!availability || !availability.available || availability.reason) && <Alert type={availability?.available ? 'info' : 'warning'} showIcon message={availability?.available ? '数据访问说明' : '本工具暂不可用'} description={availability?.reason || '服务尚未提供此工具，请刷新工具状态。'} action={<Button size="small" disabled={busy} onClick={() => setCatalogTick(value => value + 1)}>刷新状态</Button>} />}
    <section className="research-input-card" aria-label={`${config.title}输入`}>
      <div className="research-section-heading"><h3>{config.title}</h3><Button aria-label="下载虚构数据模板" icon={<DownloadOutlined aria-hidden="true" />} loading={downloading} disabled={busy} onClick={() => void downloadTemplate()}>下载模板</Button></div>
      <p className="research-input-help">{config.input}</p>
      <small className="research-template-note">模板中的数据为虚构演示。下载不会填充或启动分析，需明确选择文件后使用。</small>
      <ConfigProvider componentDisabled={busy || downloading}><fieldset className="research-input-fieldset" disabled={busy || downloading}><legend className="research-sr-only">数据输入</legend>
        <Radio.Group aria-label="数据输入方式" value={draft.mode} onChange={event => changeDraft({ mode: event.target.value })} options={[...(['financials', 'portfolio', 'factors'].includes(tool) ? [{ value: 'live', label: tool === 'financials' ? '按美股代码查询' : '按资产代码查行情' }] : []), { value: 'file', label: '上传数据文件' }, { value: 'json', label: '粘贴 JSON' }, ...(draft.seedInput ? [{ value: 'seed', label: '当前回测数据' }] : []), ...(tool === 'institutions' ? [{ value: 'sec', label: '查询 SEC 披露' }] : [])]} />
        {draft.mode === 'seed' && <Alert type="info" showIcon message="核对当前回测数据" description={draft.seedNote || draft.seedLabel} />}
        {draft.mode === 'file' && <div className="research-file-area"><label className="research-sr-only" htmlFor={`${id}-file`}>研究数据文件</label><input id={`${id}-file`} ref={fileInput} className="research-file-input" type="file" accept={tool === 'institutions' ? '.csv,.json,.xml' : '.csv,.json'} disabled={busy || downloading} onChange={event => { chooseFile(event.target.files?.[0]); event.target.value = ''; }} /><Button aria-label="选择研究数据文件" icon={<UploadOutlined aria-hidden="true" />} disabled={busy || downloading} onClick={() => fileInput.current?.click()}>选择数据文件</Button><span>{draft.file ? `${draft.file.name} · ${Math.max(1, Math.ceil(draft.file.size / 1024))} KB` : `${tool === 'institutions' ? 'CSV / JSON / 官方 XML' : 'CSV / JSON'} · 最大 2 MiB / 5,000 行`}</span></div>}
        <DraftFields tool={tool} draft={draft} change={changeDraft} />
      </fieldset></ConfigProvider>
      <div className="research-run-actions"><Button type="primary" aria-label="开始分析" icon={<ExperimentOutlined aria-hidden="true" />} disabled={!availability?.available || busy || downloading || (draft.mode === 'file' && !draft.file)} onClick={() => void run()}>开始分析</Button>{busy && <Button aria-label="取消分析" danger loading={cancelling} onClick={() => void cancel()}>取消分析</Button>}</div>
    </section>
    {error && <Alert type="error" showIcon message="分析尚未完成" description={error} action={pollFailed && activeJob.current ? <Button size="small" onClick={() => { setError(''); setPollFailed(false); const controller = new AbortController(); request.current = controller; void poll(activeJob.current!, generation.current, controller); }}>重试任务状态</Button> : undefined} />}
    {notice && <Alert type="info" showIcon message={notice} />}
    {busy && <div className="research-job-progress" role="status"><Spin /><div><strong>{job?.state === 'queued' ? '已提交，等待执行' : job?.state === 'running' ? '正在分析数据' : '正在准备提交'}</strong><span>{job?.stage || '可随时取消。任务结束后会在这里显示报告。'}</span>{job && <small>任务 {job.id} · 最后更新 {job.updated_at}</small>}</div></div>}
    {report && <InvestmentResearchReportView report={report} />}
    {!report && !busy && !error && !notice && <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="尚未分析。请选择自己的数据或明确选择演示模板后运行。" />}
  </div>;
}

export function InvestmentResearchCenterButton({ size = 'middle', seed, initialTool, label }: { size?: 'small' | 'middle' | 'large'; seed?: InvestmentResearchSeed; initialTool?: InvestmentResearchTool; label?: string }) {
  const [open, setOpen] = useState(false);
  const revision = useSyncExternalStore(subscribeAuth, () => getAuthSnapshot().revision);
  const seedScope = useRef({ seed, revision });
  if (seedScope.current.seed !== seed) seedScope.current = { seed, revision };
  const currentSeed = seedScope.current.revision === revision ? seed : undefined;
  useEffect(() => registerSessionCleanup(() => setOpen(false)), [revision]);
  return <><Button className="investment-research-entry" size={size} aria-label={currentSeed?.label || label || '投研工具'} icon={<ExperimentOutlined aria-hidden="true" />} onClick={() => setOpen(true)}>{currentSeed?.label || label || '投研工具'}</Button><Drawer title="投研工具" open={open} onClose={() => setOpen(false)} destroyOnClose width="min(1180px, 100vw)" className="investment-research-drawer">{open && <InvestmentResearchCenter seed={currentSeed} initialTool={initialTool} />}</Drawer></>;
}
