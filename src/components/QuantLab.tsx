import React, { useMemo, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Button,
  Card,
  Col,
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
  ConfigProvider,
  theme as antTheme,
} from 'antd';
import {
  BarChartOutlined,
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
import { runQuantLab, QuantLabResponse, QuantStrategyKey } from '../services/quantService';
import { useTheme } from '../context/ThemeContext';
import './QuantLab.css';

const { RangePicker } = DatePicker;
const { Paragraph, Text, Title } = Typography;

const STRATEGY_OPTIONS: Array<{ value: QuantStrategyKey; label: string; desc: string }> = [
  { value: 'momentum', label: '动量', desc: '顺势追踪近中期表现更强的标的' },
  { value: 'mean_reversion', label: '均值回归', desc: '寻找超涨/超跌后的反转机会' },
  { value: 'trend_following', label: '趋势跟踪', desc: '用均线差和趋势强度构建仓位' },
  { value: 'breakout', label: '突破', desc: '突破高点并放量后给出进攻信号' },
  { value: 'defensive', label: '防守', desc: '波动和回撤优先级更高的低风险策略' },
];

const DEFAULT_RULES = {
  max_position_size_pct: 20,
  max_total_exposure_pct: 100,
  max_sector_exposure_pct: 40,
  max_drawdown_pct: 15,
  daily_loss_limit_pct: 5,
  stop_loss_pct: 8,
  take_profit_pct: 15,
  cooldown_days: 5,
  allow_reentry: false,
};

const fmtPct = (value?: number) => `${(value ?? 0) >= 0 ? '+' : ''}${(value ?? 0).toFixed(2)}%`;
const fmtUsd = (value?: number) => `$${Math.round(value ?? 0).toLocaleString()}`;

const parseSectorMap = (text: string): Record<string, string> => {
  const map: Record<string, string> = {};
  text.split(/[\n,;]+/).forEach(part => {
    const trimmed = part.trim();
    if (!trimmed) return;
    const [symbol, sector] = trimmed.split(/[:=]/).map(v => v.trim());
    if (symbol && sector) {
      map[symbol.toUpperCase()] = sector;
    }
  });
  return map;
};

const buildChartData = (result: QuantLabResponse | null) => {
  if (!result) return [];
  const len = Math.min(
    result.backtest.dates.length,
    result.backtest.equity_curve.length,
    result.backtest.baseline_curve.length,
    result.backtest.benchmark_curve.length || result.backtest.dates.length,
  );
  return Array.from({ length: len }, (_, index) => ({
    date: result.backtest.dates[index],
    量化组合: result.backtest.equity_curve[index],
    裸持有: result.backtest.baseline_curve[index],
    基准: result.backtest.benchmark_curve[index] ?? null,
  }));
};

interface QuantLabProps {
  appState?: Pick<AppState, 'stocks'>;
  defaultSymbols?: string[];
}

const QuantLabContent: React.FC<QuantLabProps> = ({ appState, defaultSymbols: preferredSymbols }) => {
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<QuantLabResponse | null>(null);
  const [error, setError] = useState('');
  const [strategyKey, setStrategyKey] = useState<QuantStrategyKey>('momentum');

  const defaultSymbols = useMemo(() => {
    const stockSymbols = (preferredSymbols?.length ? preferredSymbols : appState?.stocks?.map(stock => stock.symbol) || []).slice(0, 6);
    return stockSymbols.length ? stockSymbols.join(', ') : 'AAPL, MSFT, NVDA';
  }, [appState?.stocks, preferredSymbols]);

  const chartData = useMemo(() => buildChartData(result), [result]);
  const backtest = result?.backtest;
  const signals = result?.signals || [];
  const orders = result?.orders || [];
  const riskSummary = result?.risk_summary;

  const handleRun = async () => {
    try {
      const values = await form.validateFields();
      const symbols = String(values.symbols || '')
        .split(/[,，\s]+/)
        .map((s: string) => s.trim().toUpperCase())
        .filter(Boolean);
      if (!symbols.length) {
        message.error('请输入至少一个标的代码');
        return;
      }

      const [start, end] = values.dateRange || [];
      const request = {
        name: values.name,
        market: values.market || 'US',
        strategy_key: strategyKey,
        symbols,
        start_date: start?.format('YYYY-MM-DD') || '2024-01-01',
        end_date: end?.format('YYYY-MM-DD') || '',
        benchmark: values.benchmark || 'SPY',
        initial_capital: values.initial_capital || 100000,
        lookback: values.lookback || 20,
        top_n: values.top_n || 5,
        allow_short: !!values.allow_short,
        risk_rules: {
          max_position_size_pct: values.max_position_size_pct,
          max_total_exposure_pct: values.max_total_exposure_pct,
          max_sector_exposure_pct: values.max_sector_exposure_pct,
          max_drawdown_pct: values.max_drawdown_pct,
          daily_loss_limit_pct: values.daily_loss_limit_pct,
          stop_loss_pct: values.stop_loss_pct,
          take_profit_pct: values.take_profit_pct,
          cooldown_days: values.cooldown_days,
          allow_reentry: !!values.allow_reentry,
        },
        sector_map: parseSectorMap(values.sector_map || ''),
      };

      setLoading(true);
      setError('');
      const response = await runQuantLab(request);
      setResult(response);
      message.success('量化系统快照已生成');
    } catch (err: any) {
      if (err?.errorFields) {
        return;
      }
      const detail = err?.message || '量化系统运行失败';
      setError(detail);
      message.error(detail);
    } finally {
      setLoading(false);
    }
  };

  const summaryCards = result
    ? [
        { title: '信号数量', value: signals.length, hint: `${orders.filter(order => order.side === 'buy').length} 买 · ${orders.filter(order => order.side === 'sell').length} 卖` },
        { title: '目标仓位', value: fmtPct(result.allocation.gross_exposure_pct), hint: `现金缓冲 ${fmtPct(result.allocation.cash_buffer_pct)}` },
        { title: '风控后收益', value: fmtPct(backtest?.metrics?.risk?.total_return_pct), hint: `裸持有 ${fmtPct(backtest?.metrics?.baseline?.total_return_pct)}` },
        { title: '最大回撤', value: fmtPct(backtest?.metrics?.risk?.max_drawdown_pct), hint: `回撤改善 ${fmtPct(backtest?.metrics?.improvement?.drawdown_reduction_pct)}` },
        { title: '组合开仓', value: result.portfolio_context.current_open_count ?? 0, hint: `市值 ${fmtUsd(result.portfolio_context.current_total_value)}` },
        { title: '当前盈亏', value: fmtPct(result.portfolio_context.current_total_pnl_pct), hint: `策略 ${result.strategy_label}` },
      ]
    : [];

  const signalColumns = [
    {
      title: '标的',
      key: 'symbol',
      width: 120,
      render: (_: unknown, row: QuantLabResponse['signals'][number]) => (
        <Space direction="vertical" size={0}>
          <Text strong>{row.symbol}</Text>
          <Text type="secondary">{row.sector || '未分组'}</Text>
        </Space>
      ),
    },
    {
      title: '动作',
      dataIndex: 'action',
      key: 'action',
      width: 84,
      render: (value: string) => <Tag color={value === 'buy' ? 'green' : value === 'sell' ? 'red' : 'default'}>{value}</Tag>,
    },
    {
      title: '评分',
      dataIndex: 'score',
      key: 'score',
      width: 150,
      render: (score: number) => <Progress percent={Math.max(0, Math.min(100, score + 50))} size="small" />,
    },
    {
      title: '当前 / 目标',
      key: 'weights',
      width: 130,
      render: (_: unknown, row: QuantLabResponse['signals'][number]) => (
        <Text>{row.current_weight.toFixed(1)}% / {row.target_weight.toFixed(1)}%</Text>
      ),
    },
    {
      title: '最新价',
      dataIndex: 'latest_close',
      key: 'latest_close',
      width: 100,
      render: (value: number) => fmtUsd(value),
    },
    {
      title: '置信度',
      dataIndex: 'confidence',
      key: 'confidence',
      width: 100,
      render: (value: number) => `${Math.round(value * 100)}%`,
    },
    {
      title: '理由',
      dataIndex: 'reasons',
      key: 'reasons',
      ellipsis: true,
      render: (value: string[]) => <Space wrap size={[4, 4]}>{(value || []).slice(0, 2).map(item => <Tag key={item}>{item}</Tag>)}</Space>,
    },
    {
      title: '风险',
      dataIndex: 'risk_flags',
      key: 'risk_flags',
      ellipsis: true,
      render: (value: string[]) => <Space wrap size={[4, 4]}>{(value || []).slice(0, 2).map(item => <Tag key={item} color="orange">{item}</Tag>)}</Space>,
    },
  ];

  const orderColumns = [
    { title: '标的', dataIndex: 'symbol', key: 'symbol', width: 100 },
    { title: '方向', dataIndex: 'side', key: 'side', width: 72, render: (v: string) => <Tag color={v === 'buy' ? 'green' : v === 'sell' ? 'red' : 'default'}>{v}</Tag> },
    { title: '当前', dataIndex: 'current_weight', key: 'current_weight', width: 90, render: (v: number) => `${v.toFixed(1)}%` },
    { title: '目标', dataIndex: 'target_weight', key: 'target_weight', width: 90, render: (v: number) => `${v.toFixed(1)}%` },
    { title: '变化', dataIndex: 'delta_weight', key: 'delta_weight', width: 90, render: (v: number) => <Text style={{ color: v >= 0 ? '#22c55e' : '#ef4444' }}>{v >= 0 ? '+' : ''}{v.toFixed(1)}%</Text> },
    { title: '数量', dataIndex: 'quantity', key: 'quantity', width: 90, render: (v: number) => v.toFixed(2) },
    { title: '名义', dataIndex: 'notional', key: 'notional', width: 110, render: (v: number) => fmtUsd(v) },
    { title: '原因', dataIndex: 'reason', key: 'reason', ellipsis: true },
  ];

  const portfolioColumns = [
    { title: '代码', dataIndex: 'symbol', key: 'symbol', width: 100 },
    { title: '名称', dataIndex: 'name', key: 'name', width: 100 },
    { title: '方向', dataIndex: 'direction', key: 'direction', width: 70, render: (v: string) => <Tag color={v === 'long' ? 'green' : 'red'}>{v}</Tag> },
    { title: '市值', dataIndex: 'notional_value', key: 'notional_value', width: 110, render: (v: number) => fmtUsd(v) },
    { title: '盈亏', dataIndex: 'unrealized_pnl_pct', key: 'unrealized_pnl_pct', width: 90, render: (v: number) => <Text style={{ color: v >= 0 ? '#22c55e' : '#ef4444' }}>{fmtPct(v)}</Text> },
  ];

  return (
    <CenterShell
      className="quant-lab"
      icon={<BarChartOutlined />}
      title="QuantLab 量化系统"
      subtitle={<><SafetyCertificateOutlined /> 策略扫描 · 回测 · 纸上实盘</>}
      actions={<Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={handleRun}>运行量化</Button>}
    >
      <Card size="small" style={{ marginBottom: 16 }}>
        <Form
          form={form}
          layout="vertical"
          size="small"
          initialValues={{
            name: '量化系统',
            market: 'US',
            benchmark: 'SPY',
            initial_capital: 100000,
            lookback: 20,
            top_n: 5,
            sector_map: '',
            symbols: defaultSymbols,
            ...DEFAULT_RULES,
          }}
        >
          <Row gutter={12}>
            <Col xs={24} md={10}>
              <Form.Item name="name" label="方案名称" rules={[{ required: true }]}>
                <Input />
              </Form.Item>
            </Col>
            <Col xs={12} md={7}>
              <Form.Item name="market" label="市场">
                <Select options={[{ value: 'US', label: '美股' }, { value: 'CN', label: 'A股' }, { value: 'HK', label: '港股' }]} />
              </Form.Item>
            </Col>
            <Col xs={12} md={7}>
              <Form.Item label="策略" required>
                <Select value={strategyKey} onChange={setStrategyKey} options={STRATEGY_OPTIONS.map(option => ({ value: option.value, label: option.label }))} />
              </Form.Item>
            </Col>
          </Row>

          <Paragraph type="secondary" style={{ marginBottom: 8 }}>
            {STRATEGY_OPTIONS.find(option => option.value === strategyKey)?.desc}
          </Paragraph>

          <Form.Item name="symbols" label="交易标的" rules={[{ required: true }]} extra="多个标的用逗号分隔。默认取当前自选前几只。">
            <Input placeholder="AAPL, MSFT, NVDA" />
          </Form.Item>

          <Row gutter={12}>
            <Col xs={24} md={12}>
              <Form.Item name="dateRange" label="回测区间" rules={[{ required: true }]}>
                <RangePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="benchmark" label="基准">
                <Input />
              </Form.Item>
            </Col>
            <Col xs={12} md={6}>
              <Form.Item name="initial_capital" label="初始资金">
                <InputNumber style={{ width: '100%' }} min={1000} />
              </Form.Item>
            </Col>
          </Row>

          <Row gutter={12}>
            <Col xs={12} md={6}><Form.Item name="lookback" label="回看窗口"><InputNumber style={{ width: '100%' }} min={5} max={252} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="top_n" label="选前几名"><InputNumber style={{ width: '100%' }} min={1} max={50} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="allow_short" label="允许做空" valuePropName="checked"><Switch /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="allow_reentry" label="允许重入" valuePropName="checked"><Switch /></Form.Item></Col>
          </Row>

          <Form.Item name="sector_map" label="行业映射" extra="可选：AAPL=科技,MSFT=科技,JPM=金融">
            <Input.TextArea rows={2} placeholder="AAPL=科技, MSFT=科技, JPM=金融" />
          </Form.Item>

          <Row gutter={12}>
            <Col xs={12} md={6}><Form.Item name="max_position_size_pct" label="单仓上限(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="max_total_exposure_pct" label="总仓上限(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="max_sector_exposure_pct" label="行业上限(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="max_drawdown_pct" label="最大回撤(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
          </Row>
          <Row gutter={12}>
            <Col xs={12} md={6}><Form.Item name="daily_loss_limit_pct" label="单日亏损(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="stop_loss_pct" label="止损(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="take_profit_pct" label="止盈(%)"><InputNumber style={{ width: '100%' }} min={0} max={200} /></Form.Item></Col>
            <Col xs={12} md={6}><Form.Item name="cooldown_days" label="冷却天数"><InputNumber style={{ width: '100%' }} min={0} max={252} /></Form.Item></Col>
          </Row>
        </Form>
      </Card>

      {error && <Alert type="error" showIcon message="量化系统运行失败" description={error} style={{ marginBottom: 16 }} />}

      {result && (
        <>
          <Card size="small" style={{ marginBottom: 16 }}>
            <Alert type="success" showIcon message={result.backtest.metrics.summary} style={{ marginBottom: 16 }} />
            <Row gutter={[12, 12]}>
              {summaryCards.map(item => (
                <Col xs={12} md={8} xl={4} key={item.title}>
                  <Card size="small" bodyStyle={{ padding: '10px 12px' }}>
                    <Statistic title={item.title} value={item.value} valueStyle={{ fontSize: 18, fontWeight: 600 }} />
                    <Text type="secondary" style={{ fontSize: 12 }}>{item.hint}</Text>
                  </Card>
                </Col>
              ))}
            </Row>
            <Space wrap style={{ marginTop: 12 }}>
              <Tag color="blue">策略 {result.strategy_label}</Tag>
              <Tag color="green">买入 {result.allocation.buy_count}</Tag>
              <Tag color="red">卖出 {result.allocation.sell_count}</Tag>
              <Tag color="orange">持有 {result.allocation.hold_count}</Tag>
              {result.notes.map(note => <Tag key={note}>{note}</Tag>)}
            </Space>
          </Card>

          <Card size="small" title={<><BarChartOutlined /> 组合曲线</>} style={{ marginBottom: 16 }}>
            <div style={{ width: '100%', height: 320 }}>
              <ResponsiveContainer>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tickFormatter={value => `$${Math.round(value).toLocaleString()}`} />
                  <Tooltip formatter={(value: any) => fmtUsd(Number(value))} />
                  <Legend />
                  <Line type="monotone" dataKey="量化组合" stroke="#1677ff" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="裸持有" stroke="#22c55e" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="基准" stroke="#f59e0b" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Row gutter={16}>
            <Col xs={24} xl={14}>
              <Card size="small" title="候选信号" style={{ marginBottom: 16 }}>
                <Table
                  rowKey="symbol"
                  size="small"
                  pagination={{ pageSize: 8 }}
                  dataSource={signals}
                  columns={signalColumns}
                  tableLayout="fixed"
                  scroll={{ x: 980 }}
                />
              </Card>
              <Card size="small" title="执行计划">
                <Table
                  rowKey="symbol"
                  size="small"
                  pagination={{ pageSize: 8 }}
                  dataSource={orders}
                  columns={orderColumns}
                  tableLayout="fixed"
                  scroll={{ x: 820 }}
                />
              </Card>
            </Col>
            <Col xs={24} xl={10}>
              {riskSummary && (
                <Card size="small" title="当前组合" style={{ marginBottom: 16 }}>
                  <Row gutter={12}>
                    <Col span={8}><Statistic title="市值" value={fmtUsd(result.portfolio_context.current_total_value)} /></Col>
                    <Col span={8}><Statistic title="开仓数" value={result.portfolio_context.current_open_count} /></Col>
                    <Col span={8}><Statistic title="总盈亏" value={fmtPct(result.portfolio_context.current_total_pnl_pct)} /></Col>
                  </Row>
                  {Array.isArray(riskSummary.open_positions) && riskSummary.open_positions.length > 0 && (
                    <Table
                      rowKey="id"
                      size="small"
                      pagination={false}
                      style={{ marginTop: 12 }}
                      dataSource={riskSummary.open_positions.slice(0, 5)}
                      columns={portfolioColumns}
                      tableLayout="fixed"
                    />
                  )}
                </Card>
              )}

              <Card size="small" title="纸上回放">
                <Timeline
                  items={(backtest?.events || []).slice(-12).map(event => ({
                    color: event.type === 'circuit_breaker' ? 'red' : event.type === 'exit' ? 'orange' : 'blue',
                    children: (
                      <div>
                        <Text strong>{event.date}</Text>
                        <div>{event.message}{event.symbol ? ` · ${event.symbol}` : ''}</div>
                      </div>
                    ),
                  }))}
                />
              </Card>
            </Col>
          </Row>
        </>
      )}
    </CenterShell>
  );
};

const QuantLab: React.FC<QuantLabProps> = (props) => {
  const { theme } = useTheme();
  return (
    <ConfigProvider
      theme={{
        algorithm: theme === 'dark' ? antTheme.darkAlgorithm : antTheme.defaultAlgorithm,
        token: {
          colorPrimary: '#10a37f',
          borderRadius: 8,
          fontSize: 13,
        },
      }}
    >
      <AntdApp>
        <QuantLabContent {...props} />
      </AntdApp>
    </ConfigProvider>
  );
};

export default QuantLab;
