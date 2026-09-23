import React, { useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  DatePicker,
  Form,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Switch,
  Table,
  Tag,
  Timeline,
  Typography,
  App as AntdApp,
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
import { runRiskBacktest, RiskBacktestRequest, RiskBacktestResponse } from '../services/riskService';

const { RangePicker } = DatePicker;
const { Paragraph, Text, Title } = Typography;

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

const buildChartData = (result: RiskBacktestResponse | null) => {
  if (!result) return [];
  const len = Math.min(
    result.dates.length,
    result.equity_curve.length,
    result.baseline_curve.length,
    result.benchmark_curve.length || result.dates.length,
  );
  return Array.from({ length: len }, (_, index) => ({
    date: result.dates[index],
    风控后: result.equity_curve[index],
    裸持有: result.baseline_curve[index],
    基准: result.benchmark_curve[index] ?? null,
  }));
};

const RiskBacktestPanel: React.FC = () => {
  const { message } = AntdApp.useApp();
  const [form] = Form.useForm();
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<RiskBacktestResponse | null>(null);
  const [error, setError] = useState('');

  const chartData = useMemo(() => buildChartData(result), [result]);
  const ruleHits = result?.metrics?.rule_hits || {};
  const dataSources = result?.data_sources || {};

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
      const request: RiskBacktestRequest = {
        name: values.name,
        market: values.market || 'US',
        symbols,
        start_date: start?.format('YYYY-MM-DD') || '2024-01-01',
        end_date: end?.format('YYYY-MM-DD') || '',
        initial_capital: values.initial_capital || 100000,
        benchmark: values.benchmark || 'SPY',
        rules: {
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
      const response = await runRiskBacktest(request);
      setResult(response);
      message.success('风控回测已完成');
    } catch (err: any) {
      if (err?.errorFields) {
        return;
      }
      const detail = err?.message || '风控回测失败';
      setError(detail);
      message.error(detail);
    } finally {
      setLoading(false);
    }
  };

  const stats = result
    ? [
        { title: '风控后收益', value: fmtPct(result.metrics.risk?.total_return_pct), color: result.metrics.risk?.total_return_pct >= 0 ? '#22c55e' : '#ef4444' },
        { title: '裸持有收益', value: fmtPct(result.metrics.baseline?.total_return_pct), color: result.metrics.baseline?.total_return_pct >= 0 ? '#22c55e' : '#ef4444' },
        { title: '回撤改善', value: fmtPct(result.metrics.improvement?.drawdown_reduction_pct), color: '#1677ff' },
        { title: '夏普变化', value: (result.metrics.improvement?.sharpe_delta ?? 0).toFixed(2), color: '#7c3aed' },
        { title: '止损/止盈', value: `${ruleHits.stop_loss || 0} / ${ruleHits.take_profit || 0}`, color: '#fa8c16' },
        { title: '熔断/拦截', value: `${ruleHits.daily_loss || 0} / ${ruleHits.allocation_block || 0}`, color: '#f43f5e' },
      ]
    : [];

  const tradeColumns = [
    { title: '日期', dataIndex: 'date', width: 108 },
    { title: '标的', dataIndex: 'symbol', width: 96 },
    { title: '动作', dataIndex: 'action', width: 72, render: (v: string) => <Tag color={v === 'buy' ? 'green' : 'red'}>{v}</Tag> },
    { title: '价格', dataIndex: 'price', width: 90 },
    { title: '数量', dataIndex: 'shares', width: 90 },
    { title: '金额', dataIndex: 'value', width: 110 },
    { title: '盈亏', dataIndex: 'pnl', width: 100, render: (v: number) => (typeof v === 'number' ? <Text style={{ color: v >= 0 ? '#22c55e' : '#ef4444' }}>{v >= 0 ? '+' : ''}{v.toFixed(2)}</Text> : '-') },
    { title: '原因', dataIndex: 'reason', ellipsis: true },
  ];

  return (
    <div>
      <Card size="small" style={{ marginBottom: 16 }}>
        <Title level={4} style={{ margin: 0 }}>风控回测</Title>
        <Paragraph type="secondary" style={{ marginBottom: 0 }}>
          用历史行情回放你的风控规则，直接看它有没有把回撤、单日亏损和仓位失控压住。
        </Paragraph>
      </Card>

      <Card size="small" title={<><SafetyCertificateOutlined /> 规则配置</>} style={{ marginBottom: 16 }}>
        <Form form={form} layout="vertical" size="small" initialValues={{
          name: '风控回测',
          market: 'US',
          benchmark: 'SPY',
          initial_capital: 100000,
          sector_map: '',
          ...DEFAULT_RULES,
        }}>
          <Form.Item name="name" label="回测名称" rules={[{ required: true }]}>
            <Input placeholder="如：NVDA 风控回测" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="market" label="市场">
                <Select options={[
                  { value: 'US', label: '美股' },
                  { value: 'CN', label: 'A股' },
                  { value: 'HK', label: '港股' },
                ]} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="benchmark" label="基准" initialValue="SPY">
                <Input />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="symbols" label="交易标的" rules={[{ required: true }]} extra="多个标的用逗号分隔，如 AAPL,MSFT,NVDA">
            <Input placeholder="AAPL, MSFT, NVDA" />
          </Form.Item>

          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="dateRange" label="回测区间" rules={[{ required: true }]}>
                <RangePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="initial_capital" label="初始资金">
                <InputNumber style={{ width: '100%' }} min={1000} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="cooldown_days" label="冷却天数">
                <InputNumber style={{ width: '100%' }} min={0} max={252} />
              </Form.Item>
            </Col>
          </Row>

          <Form.Item name="sector_map" label="行业映射" extra="可选：AAPL=科技,MSFT=科技,JPM=金融">
            <Input.TextArea rows={2} placeholder="AAPL=科技, MSFT=科技, JPM=金融" />
          </Form.Item>

          <Row gutter={12}>
            <Col span={6}><Form.Item name="max_position_size_pct" label="单仓上限(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col span={6}><Form.Item name="max_total_exposure_pct" label="总仓上限(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col span={6}><Form.Item name="max_sector_exposure_pct" label="行业上限(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col span={6}><Form.Item name="max_drawdown_pct" label="最大回撤(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
          </Row>
          <Row gutter={12}>
            <Col span={6}><Form.Item name="daily_loss_limit_pct" label="单日亏损(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col span={6}><Form.Item name="stop_loss_pct" label="止损(%)"><InputNumber style={{ width: '100%' }} min={0} max={100} /></Form.Item></Col>
            <Col span={6}><Form.Item name="take_profit_pct" label="止盈(%)"><InputNumber style={{ width: '100%' }} min={0} max={200} /></Form.Item></Col>
            <Col span={6}>
              <Form.Item name="allow_reentry" label="允许重入" valuePropName="checked">
                <Switch />
              </Form.Item>
            </Col>
          </Row>

          <Button type="primary" icon={<ThunderboltOutlined />} loading={loading} onClick={handleRun}>
            运行风控回测
          </Button>
        </Form>
      </Card>

      {error && <Alert type="error" showIcon message="回测失败" description={error} style={{ marginBottom: 16 }} />}

      {result && (
        <>
          <Card size="small" style={{ marginBottom: 16 }}>
            <Alert type="success" showIcon message={result.metrics.summary} style={{ marginBottom: 16 }} />
            <Row gutter={[12, 12]}>
              {stats.map(item => (
                <Col span={4} key={item.title}>
                  <Card size="small" bodyStyle={{ padding: '10px 12px' }}>
                    <Statistic title={item.title} value={item.value} valueStyle={{ color: item.color, fontWeight: 600, fontSize: 18 }} />
                  </Card>
                </Col>
              ))}
            </Row>
            <Space wrap style={{ marginTop: 12 }}>
              {Object.entries(ruleHits).map(([key, value]) => (
                <Tag key={key} color="blue">{key}: {value}</Tag>
              ))}
              {Object.entries(dataSources).map(([key, value]) => (
                <Tag key={key} color="geekblue">{key}: {value || 'unknown'}</Tag>
              ))}
            </Space>
          </Card>

          <Card size="small" title={<><BarChartOutlined /> 风控前后曲线</>} style={{ marginBottom: 16 }}>
            <div style={{ width: '100%', height: 320 }}>
              <ResponsiveContainer>
                <LineChart data={chartData}>
                  <CartesianGrid strokeDasharray="3 3" />
                  <XAxis dataKey="date" tick={{ fontSize: 11 }} />
                  <YAxis tickFormatter={value => `$${Math.round(value).toLocaleString()}`} />
                  <Tooltip formatter={(value: any) => fmtUsd(Number(value))} />
                  <Legend />
                  <Line type="monotone" dataKey="风控后" stroke="#1677ff" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="裸持有" stroke="#22c55e" dot={false} strokeWidth={2} />
                  <Line type="monotone" dataKey="基准" stroke="#f59e0b" dot={false} strokeWidth={2} />
                </LineChart>
              </ResponsiveContainer>
            </div>
          </Card>

          <Row gutter={16}>
            <Col span={10}>
              <Card size="small" title="风控事件" style={{ marginBottom: 16 }}>
                <Timeline
                  items={(result.events || []).slice(-12).map(item => ({
                    color: item.type === 'circuit_breaker' ? 'red' : item.type === 'exit' ? 'orange' : 'blue',
                    children: (
                      <div>
                        <Text strong>{item.date}</Text>
                        <div>{item.message}{item.symbol ? ` · ${item.symbol}` : ''}</div>
                      </div>
                    ),
                  }))}
                />
              </Card>
            </Col>
            <Col span={14}>
              <Card size="small" title="交易明细">
                <Table
                  rowKey={record => `${record.date}-${record.symbol}-${record.action}-${record.reason}`}
                  size="small"
                  pagination={{ pageSize: 8 }}
                  dataSource={result.trades_log}
                  columns={tradeColumns}
                  tableLayout="fixed"
                  scroll={{ x: 760 }}
                />
              </Card>
            </Col>
          </Row>
        </>
      )}
    </div>
  );
};

export default RiskBacktestPanel;
