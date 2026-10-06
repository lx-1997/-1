import React, { useEffect, useMemo, useRef, useState } from 'react';
import dayjs from 'dayjs';
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Collapse,
  Col,
  ConfigProvider,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  theme as antTheme,
} from 'antd';
import {
  BarChartOutlined,
  CloseCircleOutlined,
  DatabaseOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import CenterShell from './common/CenterShell';
import { AppState } from '../types';
import {
  cancelQuantLabJob,
  getQuantLabJob,
  QuantJobResponse,
  QuantLabRequest,
  QuantLabResponse,
  QuantMarket,
  QuantStrategyKey,
  startQuantLabJob,
} from '../services/quantService';
import { ACCENT_THEME_OPTIONS, useTheme } from '../context/ThemeContext';
import './QuantLab.css';

const { RangePicker } = DatePicker;
const { Paragraph, Text } = Typography;

const STRATEGY_OPTIONS: Array<{ value: QuantStrategyKey; label: string; desc: string }> = [
  { value: 'momentum', label: '动量', desc: '以相对强度、趋势和中期收益构建滚动信号' },
  { value: 'mean_reversion', label: '均值回归', desc: '寻找价格偏离与短期超买超卖后的反转机会' },
  { value: 'trend_following', label: '趋势跟踪', desc: '用均线结构、趋势强度和波动约束构建仓位' },
  { value: 'breakout', label: '突破', desc: '结合区间突破、量能和相对强度筛选进攻信号' },
  { value: 'defensive', label: '防守', desc: '优先控制波动、回撤与价格偏离的低风险策略' },
];

const MARKET_BENCHMARKS: Record<QuantMarket, string> = {
  AUTO: 'SPY',
  US: 'SPY',
  CN: '000300',
  HK: 'HSI',
};

const DEFAULT_RULES = {
  max_position_size_pct: 20,
  max_total_exposure_pct: 100,
  max_short_exposure_pct: 30,
  max_sector_exposure_pct: 40,
  max_drawdown_pct: 15,
  daily_loss_limit_pct: 5,
  stop_loss_pct: 8,
  take_profit_pct: 15,
  cooldown_days: 5,
  allow_reentry: false,
};

const BEGINNER_PRESETS: Array<{ key: string; label: string; description: string; strategy: QuantStrategyKey; values: Record<string, unknown> }> = [
  { key: 'steady', label: '稳健入门', description: '低换手、只做多、保留现金缓冲，适合第一次回测。', strategy: 'defensive', values: { lookback: 20, top_n: 4, rebalance_frequency: 'monthly', allow_short: false, max_position_size_pct: 20, max_total_exposure_pct: 80, max_drawdown_pct: 12 } },
  { key: 'trend', label: '趋势成长', description: '用趋势与相对强度筛选强势标的，每周检查一次。', strategy: 'trend_following', values: { lookback: 60, top_n: 5, rebalance_frequency: 'weekly', allow_short: false, max_position_size_pct: 25, max_total_exposure_pct: 100, max_drawdown_pct: 15 } },
  { key: 'reversal', label: '均值回归', description: '观察短期偏离后的回归机会，适合学习信号与风险。', strategy: 'mean_reversion', values: { lookback: 20, top_n: 4, rebalance_frequency: 'weekly', allow_short: false, max_position_size_pct: 20, max_total_exposure_pct: 80, max_drawdown_pct: 12 } },
];

const ACTION_LABELS: Record<string, string> = {
  buy: '买入', sell: '卖出', short: '做空', cover: '平空', hold: '观望',
};

const ACTION_COLORS: Record<string, string> = {
  buy: 'green', sell: 'volcano', short: 'red', cover: 'cyan', hold: 'default',
};

const fmtPct = (value?: number, signed = true) => `${signed && (value ?? 0) >= 0 ? '+' : ''}${(value ?? 0).toFixed(2)}%`;
const fmtMoney = (value?: number) => Math.round(value ?? 0).toLocaleString('zh-CN');
const sleep = (ms: number) => new Promise(resolve => window.setTimeout(resolve, ms));

const parseSectorMap = (text: string): Record<string, string> => {
  const map: Record<string, string> = {};
  text.split(/[\n,;]+/).forEach(part => {
    const [symbol, sector] = part.trim().split(/[:=]/).map(value => value.trim());
    if (symbol && sector) map[symbol.toUpperCase()] = sector;
  });
  return map;
};

const buildPlatformContext = (stocks: AppState['stocks'] | undefined, posts: AppState['posts'] | undefined, symbols: string[]) => {
  const wanted = new Set(symbols.map(symbol => symbol.toUpperCase()));
  const postsBySymbol = (posts || []).reduce<Record<string, AppState['posts']>>((acc, post) => {
    const symbol = String(post.stockSymbol || '').trim().toUpperCase();
    if (symbol && wanted.has(symbol)) (acc[symbol] ||= []).push(post);
    return acc;
  }, {});
  return (stocks || []).reduce<Record<string, Record<string, unknown>>>((acc, stock) => {
    const symbol = String(stock.symbol || '').trim().toUpperCase();
    if (!symbol || !wanted.has(symbol)) return acc;
    const stockPosts = postsBySymbol[symbol] || [];
    const qualityScores = stockPosts.map(post => Number(post.qualityScore)).filter(score => Number.isFinite(score) && score > 0);
    acc[symbol] = { name: stock.name, sector: stock.sector, market: stock.market, community_score: stock.communityScore, focus_level: stock.focusLevel, total_posts: stock.totalPosts, total_paid_posts: stock.totalPaidPosts, change_percent: stock.changePercent, recent_post_count: stockPosts.length, analysis_post_count: stockPosts.filter(post => post.type === 'analysis').length, avg_quality_score: qualityScores.length ? qualityScores.reduce((sum, score) => sum + score, 0) / qualityScores.length : undefined, top_tags: Array.from(new Set(stockPosts.flatMap(post => post.tags || []))).slice(0, 6) };
    return acc;
  }, {});
};

const buildChartData = (result: QuantLabResponse | null) => {
  if (!result) return [];
  const { dates, equity_curve, baseline_curve, benchmark_curve } = result.backtest;
  return dates.map((date, index) => ({
    date,
    量化组合: equity_curve[index] ?? null,
    等权持有: baseline_curve[index] ?? null,
    基准: benchmark_curve[index] ?? null,
  }));
};

interface QuantLabProps {
  appState?: Pick<AppState, 'stocks' | 'posts'>;
  defaultSymbols?: string[];
}

const QuantLabContent: React.FC<QuantLabProps> = ({ appState, defaultSymbols: preferredSymbols }) => {
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [cancelling, setCancelling] = useState(false);
  const [job, setJob] = useState<QuantJobResponse | null>(null);
  const [result, setResult] = useState<QuantLabResponse | null>(null);
  const [error, setError] = useState('');
  const [strategyKey, setStrategyKey] = useState<QuantStrategyKey>('momentum');
  const activeJobRef = useRef<string | null>(null);
  const mountedRef = useRef(true);
  const allowShort = Form.useWatch('allow_short', form);

  const platformStocks = useMemo(() => appState?.stocks || [], [appState?.stocks]);

  const defaultSymbols = useMemo(() => {
    const symbols = (preferredSymbols?.length
      ? preferredSymbols
      : appState?.stocks?.map(stock => stock.symbol) || []).slice(0, 6);
    return symbols.length ? symbols.join(', ') : 'AAPL, MSFT, NVDA';
  }, [appState?.stocks, preferredSymbols]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      const jobId = activeJobRef.current;
      activeJobRef.current = null;
      if (jobId) void cancelQuantLabJob(jobId).catch(() => undefined);
    };
  }, []);

  useEffect(() => {
    if (!form.isFieldTouched('symbols')) form.setFieldValue('symbols', defaultSymbols);
  }, [defaultSymbols, form]);

  const chartData = useMemo(() => buildChartData(result), [result]);
  const backtest = result?.backtest;
  const signals = result?.signals || [];
  const orders = result?.orders || [];
  const riskSummary = result?.risk_summary;
  const execution = backtest?.metrics.execution;

  const buildRequest = async (): Promise<QuantLabRequest | null> => {
    const values = await form.validateFields();
    const symbols = String(values.symbols || '')
      .split(/[,，\s]+/)
      .map((symbol: string) => symbol.trim().toUpperCase())
      .filter(Boolean);
    if (!symbols.length) {
      message.error('请输入至少一个标的代码');
      return null;
    }
    const [start, end] = values.dateRange || [];
    return {
      name: values.name,
      market: (values.market || 'AUTO') as QuantMarket,
      strategy_key: strategyKey,
      symbols,
      start_date: start.format('YYYY-MM-DD'),
      end_date: end.format('YYYY-MM-DD'),
      benchmark: String(values.benchmark || 'SPY').trim().toUpperCase(),
      initial_capital: values.initial_capital,
      lookback: values.lookback,
      top_n: values.top_n,
      allow_short: !!values.allow_short,
      rebalance_frequency: values.rebalance_frequency,
      commission_bps: values.commission_bps,
      slippage_bps: values.slippage_bps,
      short_borrow_bps: values.short_borrow_bps,
      min_trade_notional: values.min_trade_notional,
      risk_rules: {
        max_position_size_pct: values.max_position_size_pct,
        max_total_exposure_pct: values.max_total_exposure_pct,
        max_short_exposure_pct: values.max_short_exposure_pct,
        max_sector_exposure_pct: values.max_sector_exposure_pct,
        max_drawdown_pct: values.max_drawdown_pct,
        daily_loss_limit_pct: values.daily_loss_limit_pct,
        stop_loss_pct: values.stop_loss_pct,
        take_profit_pct: values.take_profit_pct,
        cooldown_days: values.cooldown_days,
        allow_reentry: !!values.allow_reentry,
      },
      sector_map: parseSectorMap(values.sector_map || ''),
      platform_context: buildPlatformContext(platformStocks, appState?.posts, symbols),
    };
  };

  const applyBeginnerPreset = (preset: typeof BEGINNER_PRESETS[number]) => {
    setStrategyKey(preset.strategy);
    form.setFieldsValue(preset.values);
    message.success(`已应用「${preset.label}」`);
  };

  const usePlatformWatchlist = () => {
    if (!platformStocks.length) {
      message.info('当前没有可用的平台观察池，请先在观察池添加标的。');
      return;
    }
    const selected = platformStocks.slice(0, 10);
    form.setFieldsValue({ symbols: selected.map(stock => stock.symbol).join(', '), sector_map: selected.map(stock => `${stock.symbol}=${stock.sector || '未分类'}`).join(', ') });
    message.success(`已载入平台观察池 ${selected.length} 个标的，并同步行业与社区信息`);
  };

  const handleRun = async () => {
    try {
      const request = await buildRequest();
      if (!request) return;
      setLoading(true);
      setError('');
      setResult(null);
      const created = await startQuantLabJob(request);
      activeJobRef.current = created.job_id;
      if (mountedRef.current) setJob(created);
      let current = created;
      while (activeJobRef.current === created.job_id && ['pending', 'running'].includes(current.status)) {
        await sleep(800);
        if (activeJobRef.current !== created.job_id) return;
        current = await getQuantLabJob(created.job_id);
        if (mountedRef.current) setJob(current);
      }
      if (activeJobRef.current !== created.job_id) return;
      if (current.status === 'completed' && current.result) {
        setResult(current.result);
        message.success('量化研究与回测已完成');
      } else if (current.status === 'cancelled') {
        message.info('量化任务已取消');
      } else {
        throw new Error(current.error || '量化任务执行失败');
      }
    } catch (err: any) {
      if (err?.errorFields) return;
      const detail = err?.message || '量化系统运行失败';
      if (mountedRef.current) setError(detail);
      message.error(detail);
    } finally {
      activeJobRef.current = null;
      if (mountedRef.current) setLoading(false);
    }
  };

  const handleCancel = async () => {
    const jobId = activeJobRef.current;
    if (!jobId) return;
    activeJobRef.current = null;
    setCancelling(true);
    try {
      const cancelled = await cancelQuantLabJob(jobId);
      if (mountedRef.current) setJob(cancelled);
      message.info('量化任务已取消');
    } catch (err: any) {
      message.error(err?.message || '取消失败');
    } finally {
      if (mountedRef.current) {
        setCancelling(false);
        setLoading(false);
      }
    }
  };

  const handleMarketChange = (market: QuantMarket) => {
    form.setFieldValue('benchmark', MARKET_BENCHMARKS[market]);
  };

  const summaryCards = result ? [
    { title: '净敞口', value: fmtPct(result.allocation.net_exposure_pct), hint: `总敞口 ${fmtPct(result.allocation.gross_exposure_pct, false)}` },
    { title: '策略收益', value: fmtPct(backtest?.metrics.risk.total_return_pct), hint: `等权 ${fmtPct(backtest?.metrics.baseline.total_return_pct)}` },
    { title: '最大回撤', value: fmtPct(backtest?.metrics.risk.max_drawdown_pct, false), hint: `改善 ${fmtPct(backtest?.metrics.improvement.drawdown_reduction_pct)}` },
    { title: '夏普比率', value: (backtest?.metrics.risk.sharpe_ratio ?? 0).toFixed(2), hint: `Sortino ${(backtest?.metrics.risk.sortino_ratio ?? 0).toFixed(2)}` },
    { title: '交易成本', value: fmtMoney(execution?.transaction_cost), hint: `${execution?.rebalance_count ?? 0} 次再平衡` },
    { title: '换手率', value: fmtPct((execution?.turnover_ratio ?? 0) * 100, false), hint: execution?.signal_timing || '' },
  ] : [];

  const signalColumns = [
    { title: '标的', key: 'symbol', width: 120, render: (_: unknown, row: QuantLabResponse['signals'][number]) => <Space direction="vertical" size={0}><Text strong>{row.symbol}</Text><Text type="secondary">{row.market} · {row.sector || '未分组'}</Text></Space> },
    { title: '动作', dataIndex: 'action', key: 'action', width: 82, render: (value: string) => <Tag color={ACTION_COLORS[value]}>{ACTION_LABELS[value] || value}</Tag> },
    { title: '评分', dataIndex: 'score', key: 'score', width: 145, render: (score: number) => <Space direction="vertical" size={0} style={{ width: '100%' }}><Text>{score >= 0 ? '+' : ''}{score.toFixed(1)}</Text><Progress percent={Math.max(0, Math.min(100, score + 50))} size="small" showInfo={false} /></Space> },
    { title: '平台上下文', key: 'platform', width: 185, render: (_: unknown, row: QuantLabResponse['signals'][number]) => {
      const ctx = row.platform_context || {};
      const items = [typeof ctx.community_score === 'number' ? `社区 ${ctx.community_score.toFixed(0)}` : '', ctx.focus_level ? `关注 ${String(ctx.focus_level)}` : '', typeof ctx.total_posts === 'number' ? `内容 ${ctx.total_posts}` : '', typeof ctx.avg_quality_score === 'number' ? `质量 ${ctx.avg_quality_score.toFixed(0)}` : ''].filter(Boolean);
      return <Space wrap size={[4, 4]}>{items.length ? items.map(item => <Tag key={item} color="blue">{item}</Tag>) : <Text type="secondary">未匹配观察池</Text>}</Space>;
    } },

    { title: '当前 / 目标', key: 'weights', width: 130, render: (_: unknown, row: QuantLabResponse['signals'][number]) => <Text>{row.current_weight.toFixed(1)}% / {row.target_weight.toFixed(1)}%</Text> },
    { title: '最新价', dataIndex: 'latest_close', key: 'latest_close', width: 100, render: (value: number) => value.toLocaleString('zh-CN', { maximumFractionDigits: 4 }) },
    { title: '置信度', dataIndex: 'confidence', key: 'confidence', width: 90, render: (value: number) => `${Math.round(value * 100)}%` },
    { title: '依据', dataIndex: 'reasons', key: 'reasons', width: 260, render: (value: string[]) => <Space wrap size={[4, 4]}>{(value || []).slice(0, 3).map(item => <Tag key={item}>{item}</Tag>)}</Space> },
    { title: '风险', dataIndex: 'risk_flags', key: 'risk_flags', width: 220, render: (value: string[]) => (value || []).length ? <Space wrap size={[4, 4]}>{value.slice(0, 2).map(item => <Tag key={item} color="orange">{item}</Tag>)}</Space> : <Text type="secondary">暂无显著标记</Text> },
  ];

  const orderColumns = [
    { title: '标的', dataIndex: 'symbol', key: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', key: 'side', width: 76, render: (value: string) => <Tag color={ACTION_COLORS[value]}>{ACTION_LABELS[value] || value}</Tag> },
    { title: '当前', dataIndex: 'current_weight', key: 'current_weight', width: 84, render: (value: number) => `${value.toFixed(1)}%` },
    { title: '目标', dataIndex: 'target_weight', key: 'target_weight', width: 84, render: (value: number) => `${value.toFixed(1)}%` },
    { title: '变化', dataIndex: 'delta_weight', key: 'delta_weight', width: 84, render: (value: number) => <Text style={{ color: value >= 0 ? '#22c55e' : '#ef4444' }}>{value >= 0 ? '+' : ''}{value.toFixed(1)}%</Text> },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 100, render: (value: number) => value.toFixed(2) },
    { title: '名义金额', dataIndex: 'notional', key: 'notional', width: 110, render: (value: number) => fmtMoney(value) },
    { title: '原因', dataIndex: 'reason', key: 'reason', width: 260, ellipsis: true },
  ];

  const sourceRows = result ? Object.entries(result.data_source_details).map(([symbol, detail]) => ({ symbol, ...detail })) : [];
  const sourceColumns = [
    { title: '标的', dataIndex: 'symbol', key: 'symbol', width: 100 },
    { title: '市场', dataIndex: 'market', key: 'market', width: 72 },
    { title: '真实数据源', dataIndex: 'source_name', key: 'source_name', width: 180 },
    { title: '复权口径', dataIndex: 'adjustment', key: 'adjustment', width: 130 },
    { title: '日线数', dataIndex: 'total_bars', key: 'total_bars', width: 80 },
    { title: '覆盖区间', key: 'range', width: 190, render: (_: unknown, row: any) => `${row.start_date || '-'} → ${row.end_date || '-'}` },
    { title: '质量', key: 'quality', width: 130, render: (_: unknown, row: any) => <Tag color={row.is_synthetic ? 'red' : 'green'}>{row.is_synthetic ? '模拟数据' : '已验证真实行情'}</Tag> },
    { title: '提示', dataIndex: 'warnings', key: 'warnings', width: 260, render: (value: string[]) => (value || []).join('；') || '—' },
  ];

  const tradeColumns = [
    { title: '日期', dataIndex: 'date', key: 'date', width: 110 },
    { title: '标的', dataIndex: 'symbol', key: 'symbol', width: 90 },
    { title: '动作', dataIndex: 'action', key: 'action', width: 76, render: (value: string) => <Tag color={ACTION_COLORS[value]}>{ACTION_LABELS[value] || value}</Tag> },
    { title: '成交价', dataIndex: 'price', key: 'price', width: 100 },
    { title: '名义金额', dataIndex: 'value', key: 'value', width: 110, render: (value: number) => fmtMoney(value) },
    { title: '佣金', dataIndex: 'cost', key: 'cost', width: 90 },
    { title: '已实现盈亏', dataIndex: 'pnl', key: 'pnl', width: 110, render: (value: number) => <Text style={{ color: value >= 0 ? '#22c55e' : '#ef4444' }}>{fmtMoney(value)}</Text> },
    { title: '原因', dataIndex: 'reason', key: 'reason', width: 260, ellipsis: true },
  ];

  return (
    <CenterShell
      className="quant-lab"
      icon={<BarChartOutlined />}
      title="QuantLab 量化系统"
      subtitle={<><SafetyCertificateOutlined /> 真实行情 · 滚动回测 · 成本与风控</>}
      actions={loading ? (
        <Button danger icon={<CloseCircleOutlined />} loading={cancelling} onClick={handleCancel}>取消任务</Button>
      ) : (
        <Button type="primary" icon={<ThunderboltOutlined />} onClick={handleRun}>运行量化</Button>
      )}
    >
      <Alert
        type="info"
        showIcon
        message="研究口径"
        description="只接受真实历史行情，数据不足会直接停止；信号使用 T-1 收盘数据并在 T 日收盘执行，回测计入佣金、滑点和借券费。跨市场组合采用常汇率收益口径，不包含汇率损益。"
        style={{ marginBottom: 16 }}
      />

      <Card size="small" title="研究配置" style={{ marginBottom: 16 }}>
        <div className="quant-beginner-panel">
          <div className="quant-beginner-header"><div><Text strong>新手向导</Text><Text type="secondary"> 先选一个目标，系统会填好可解释的参数。</Text></div><Button size="small" onClick={usePlatformWatchlist} disabled={!platformStocks.length}>使用平台观察池</Button></div>
          <div className="quant-preset-grid">{BEGINNER_PRESETS.map(preset => <button key={preset.key} type="button" className={`quant-preset-option${strategyKey === preset.strategy ? ' is-active' : ''}`} onClick={() => applyBeginnerPreset(preset)}><strong>{preset.label}</strong><span>{preset.description}</span></button>)}</div>
          <Text type="secondary" className="quant-beginner-note">平台上下文会使用观察池的社区活跃度、关注等级、研究数量、内容质量和行业标签，只对最新信号做轻量叠加；历史回测仍只使用行情。</Text>
        </div>
        <Form
          form={form}
          layout="vertical"
          size="small"
          initialValues={{
            name: '量化研究方案',
            market: 'AUTO',
            benchmark: 'SPY',
            initial_capital: 100000,
            lookback: 20,
            top_n: 5,
            allow_short: false,
            rebalance_frequency: 'weekly',
            commission_bps: 3,
            slippage_bps: 5,
            short_borrow_bps: 100,
            min_trade_notional: 100,
            sector_map: '',
            symbols: defaultSymbols,
            dateRange: [dayjs().subtract(1, 'year'), dayjs()],
            ...DEFAULT_RULES,
          }}
        >
          <Row gutter={12}>
            <Col xs={24} md={14}><Form.Item name="name" label="方案名称" rules={[{ required: true, message: '请输入方案名称' }]}><Input /></Form.Item></Col>
            <Col xs={24} md={10}><Form.Item name="market" label="市场识别"><Select onChange={handleMarketChange} options={[{ value: 'AUTO', label: '自动识别（支持混合）' }, { value: 'US', label: '美股' }, { value: 'CN', label: 'A股' }, { value: 'HK', label: '港股' }]} /></Form.Item></Col>
          </Row>
          <Form.Item label="策略方向" required>
            <div className="quant-strategy-grid" role="radiogroup" aria-label="选择策略方向">
              {STRATEGY_OPTIONS.map(option => (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={strategyKey === option.value}
                  className={`quant-strategy-option${strategyKey === option.value ? ' is-active' : ''}`}
                  onClick={() => setStrategyKey(option.value)}
                >
                  <strong>{option.label}</strong>
                  <span>{option.desc}</span>
                </button>
              ))}
            </div>
          </Form.Item>
          <Form.Item name="symbols" label={<Space size={8}>交易标的{platformStocks.length > 0 && <Button type="link" size="small" onClick={usePlatformWatchlist} style={{ padding: 0 }}>载入观察池</Button>}</Space>} rules={[{ required: true, message: '请输入至少一个标的代码' }]} extra="多个代码用逗号或空格分隔；A股支持 600519 / 000001.SZ，港股支持 00700 / 00700.HK，美股支持 AAPL。"><Input placeholder="AAPL, MSFT, 600519, 00700" /></Form.Item>

          <Row gutter={12}>
            <Col xs={24} md={12}><Form.Item name="dateRange" label="回测区间" rules={[{ required: true, message: '请选择回测开始和结束日期' }]}><RangePicker allowClear={false} style={{ width: '100%' }} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="benchmark" label="比较基准" rules={[{ required: true, message: '请输入比较基准' }]}><Input /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="initial_capital" label="初始资金（计价单位）"><InputNumber style={{ width: '100%' }} min={1000} /></Form.Item></Col>
          </Row>

          <Row gutter={12}>
            <Col xs={12} md={6}><Form.Item name="lookback" label="回看窗口"><InputNumber style={{ width: '100%' }} min={5} max={252} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="top_n" label="最多持仓"><InputNumber style={{ width: '100%' }} min={1} max={50} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="rebalance_frequency" label="再平衡频率"><Select options={[{ value: 'daily', label: '每日' }, { value: 'weekly', label: '每周' }, { value: 'monthly', label: '每月' }]} /></Form.Item></Col>
            <Col xs={12} md={3}><Form.Item name="allow_short" label="允许做空" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={12} md={3}><Form.Item name="allow_reentry" label="风控后重入" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>
          <Collapse
            className="quant-advanced-collapse"
            items={[{
              key: 'advanced',
              label: '高级设置：执行成本与风控边界（可选）',
              children: <>
                <Row gutter={12}>
                  <Col xs={12} md={6}><Form.Item name="commission_bps" label="单边佣金 (bps)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="slippage_bps" label="单边滑点 (bps)"><InputNumber style={{ width: '100%' }} min={0} max={500} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="short_borrow_bps" label="年化借券费 (bps)"><InputNumber disabled={!allowShort} style={{ width: '100%' }} min={0} max={5000} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="min_trade_notional" label="最小交易金额"><InputNumber style={{ width: '100%' }} min={0} /></Form.Item></Col>
                </Row>
                <Form.Item name="sector_map" label="行业映射（可选）" extra="用于行业敞口约束，例如 AAPL=科技,MSFT=科技,JPM=金融"><Input.TextArea rows={2} placeholder="AAPL=科技, MSFT=科技, JPM=金融" /></Form.Item>
                <Row gutter={12}>
                  <Col xs={12} md={6}><Form.Item name="max_position_size_pct" label="单仓上限 (%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="max_total_exposure_pct" label="总敞口上限 (%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="max_short_exposure_pct" label="空头上限 (%)"><InputNumber disabled={!allowShort} style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="max_sector_exposure_pct" label="行业上限 (%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                </Row>
                <Row gutter={12}>
                  <Col xs={12} md={6}><Form.Item name="max_drawdown_pct" label="最大回撤 (%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={6}><Form.Item name="daily_loss_limit_pct" label="单日亏损 (%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={4}><Form.Item name="stop_loss_pct" label="止损 (%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
                  <Col xs={12} md={4}><Form.Item name="take_profit_pct" label="止盈 (%)"><InputNumber style={{ width: '100%' }} min={0} max={200} /></Form.Item></Col>
                  <Col xs={12} md={4}><Form.Item name="cooldown_days" label="冷却天数"><InputNumber style={{ width: '100%' }} min={0} max={252} /></Form.Item></Col>
                </Row>
              </>
            }]}
          />
        </Form>
      </Card>

      {(loading || job) && !result && (
        <Card size="small" className="quant-job-card" style={{ marginBottom: 16 }}>
          <Space direction="vertical" size={8} style={{ width: '100%' }}>
            <Space wrap><Tag color={job?.status === 'failed' ? 'red' : job?.status === 'cancelled' ? 'default' : 'processing'}>{job?.status === 'pending' ? '排队中' : job?.status === 'running' ? '运行中' : job?.status === 'failed' ? '失败' : job?.status === 'cancelled' ? '已取消' : '准备中'}</Tag><Text>{job?.stage || '正在创建任务'}</Text></Space>
            <Progress percent={job?.progress || 1} status={job?.status === 'failed' ? 'exception' : job?.status === 'cancelled' ? 'normal' : 'active'} />
          </Space>
        </Card>
      )}

      {error && <Alert type="error" showIcon message="量化研究未完成" description={error} style={{ marginBottom: 16 }} />}

      {result && backtest && (
        <>
          <Card size="small" style={{ marginBottom: 16 }}>
            <Alert type="success" showIcon message={backtest.metrics.summary} style={{ marginBottom: 16 }} />
            <Row gutter={[12, 12]}>
              {summaryCards.map(item => <Col xs={12} md={8} xl={4} key={item.title}><Card size="small" className="quant-stat-card"><Statistic title={item.title} value={item.value} valueStyle={{ fontSize: 18, fontWeight: 600 }} /><Text type="secondary" className="quant-stat-hint">{item.hint}</Text></Card></Col>)}
            </Row>
            <Space wrap style={{ marginTop: 12 }}>
              <Tag color="blue">{result.strategy_label}</Tag>
              <Tag color="green">买入/平空 {result.allocation.buy_count}</Tag>
              <Tag color="red">卖出/做空 {result.allocation.sell_count}</Tag>
              <Tag>做空 {result.allocation.short_count}</Tag>
              <Tag>现金缓冲 {fmtPct(result.allocation.cash_buffer_pct, false)}</Tag>
              {result.notes.map(note => <Tag key={note}>{note}</Tag>)}
            </Space>
            {result.platform_context?.matched_symbols ? <Alert type="info" showIcon style={{ marginTop: 12 }} message={`已融合平台观察池 ${result.platform_context.matched_symbols} 个标的`} description="平台信息只影响最新候选信号的轻量叠加，不改变历史行情回测；社区热度越高，越需要结合回撤和成交成本审慎判断。" /> : null}
          </Card>

          <Card size="small" title={<><BarChartOutlined /> 组合净值曲线</>} style={{ marginBottom: 16 }}>
            <div className="quant-chart">
              <ResponsiveContainer>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} minTickGap={28} />
                  <YAxis tickFormatter={value => fmtMoney(Number(value))} width={72} />
                  <Tooltip formatter={(value: any) => fmtMoney(Number(value))} />
                  <Legend />
                  <Line type="monotone" dataKey="量化组合" stroke="#1677ff" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="等权持有" stroke="#22c55e" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="基准" stroke="#f59e0b" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Card size="small" title={<><DatabaseOutlined /> 行情来源与质量</>} style={{ marginBottom: 16 }}>
            <Table rowKey="symbol" size="small" pagination={false} dataSource={sourceRows} columns={sourceColumns} tableLayout="fixed" scroll={{ x: 1240 }} />
          </Card>

          <Row gutter={16}>
            <Col xs={24} xl={14}>
              <Card size="small" title="最新候选信号" style={{ marginBottom: 16 }}><Table rowKey="symbol" size="small" pagination={{ pageSize: 8 }} dataSource={signals} columns={signalColumns} tableLayout="fixed" scroll={{ x: 1240 }} /></Card>
              <Card size="small" title="目标执行计划" style={{ marginBottom: 16 }}><Table rowKey="symbol" size="small" pagination={{ pageSize: 8 }} dataSource={orders} columns={orderColumns} tableLayout="fixed" scroll={{ x: 1000 }} /></Card>
            </Col>
            <Col xs={24} xl={10}>
              {execution && <Card size="small" title="执行假设" style={{ marginBottom: 16 }}>
                <Row gutter={[12, 12]}>
                  <Col span={8}><Statistic title="佣金" value={`${execution.commission_bps} bps`} /></Col>
                  <Col span={8}><Statistic title="滑点" value={`${execution.slippage_bps} bps`} /></Col>
                  <Col span={8}><Statistic title="借券费" value={`${execution.short_borrow_bps} bps`} /></Col>
                  <Col span={12}><Statistic title="成交名义额" value={fmtMoney(execution.turnover_notional)} /></Col>
                  <Col span={12}><Statistic title="借券成本" value={fmtMoney(execution.borrow_cost)} /></Col>
                </Row>
                <Paragraph type="secondary" style={{ marginTop: 12, marginBottom: 0 }}>{execution.signal_timing}；{execution.currency_mode === 'constant_currency' ? '跨市场常汇率口径，不含汇率损益' : '单市场本币口径'}。</Paragraph>
              </Card>}

              {riskSummary && (
                <Card size="small" title="当前组合上下文" style={{ marginBottom: 16 }}>
                  <Row gutter={12}>
                    <Col span={8}><Statistic title="市值" value={fmtMoney(result.portfolio_context.current_total_value)} /></Col>
                    <Col span={8}><Statistic title="开仓数" value={result.portfolio_context.current_open_count} /></Col>
                    <Col span={8}><Statistic title="总盈亏" value={fmtPct(result.portfolio_context.current_total_pnl_pct)} /></Col>
                  </Row>
                </Card>
              )}

              <Card size="small" title="风控事件" style={{ marginBottom: 16 }}>
                {backtest.events.length ? <Timeline items={backtest.events.slice(-12).map(event => ({ color: event.type === 'circuit_breaker' ? 'red' : event.type === 'exit' ? 'orange' : 'blue', children: <div><Text strong>{event.date}</Text><div>{event.message}{event.symbol ? ` · ${event.symbol}` : ''}</div></div> }))} /> : <Text type="secondary">本次回测未触发止损、止盈或组合熔断。</Text>}
              </Card>
            </Col>
          </Row>

          <Card size="small" title="成交审计（最近 100 笔）" style={{ marginBottom: 16 }}>
            <Table rowKey={(_, index) => String(index)} size="small" pagination={{ pageSize: 10 }} dataSource={backtest.trades_log.slice(-100)} columns={tradeColumns} tableLayout="fixed" scroll={{ x: 1000 }} />
          </Card>

          <Alert type="warning" showIcon message="使用边界" description={`${backtest.disclaimer} ${result.disclaimer}`} />
        </>
      )}
    </CenterShell>
  );
};

const QuantLab: React.FC<QuantLabProps> = props => {
  const { theme, accentTheme } = useTheme();
  const accentOption = ACCENT_THEME_OPTIONS.find(option => option.value === accentTheme) || ACCENT_THEME_OPTIONS[0];
  return (
    <ConfigProvider theme={{ algorithm: theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm, token: { colorPrimary: theme === 'dark' ? accentOption.dark : accentOption.light, borderRadius: 10, fontSize: 13 } }}>
      <AntdApp><QuantLabContent {...props} /></AntdApp>
    </ConfigProvider>
  );
};

export default QuantLab;
