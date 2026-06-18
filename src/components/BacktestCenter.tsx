import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  DatePicker,
  Divider,
  Empty,
  Form,
  Input,
  InputNumber,
  message,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Table,
  Tabs,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import {
  BarChartOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  DashboardOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  FundOutlined,
  HistoryOutlined,
  LineChartOutlined,
  LoadingOutlined,
  PlayCircleOutlined,
  PlusOutlined,
  ReloadOutlined,
  RocketOutlined,
  StockOutlined,
  SyncOutlined,
  TableOutlined,
  ThunderboltOutlined,
  TrophyOutlined,
} from '@ant-design/icons';
import { getApiBaseUrls, apiPost, apiGet } from '../services/apiClient';
import CenterShell from './common/CenterShell';
import './BacktestCenter.css';

const { Text } = Typography;
const { RangePicker } = DatePicker;

const STRATEGY_OPTIONS = [
  { value: 'momentum', label: '动量策略', desc: '基于N日价格动量买入/卖出' },
  { value: 'mean_reversion', label: '均值回归', desc: 'Z-score超卖买入，回归均值卖出' },
  { value: 'trend_following', label: '趋势跟踪', desc: 'MACD金叉/死叉信号跟踪趋势' },
  { value: 'breakout', label: '突破策略', desc: '价格突破N日高点+放量买入' },
];

const STRATEGY_PARAMS: Record<string, { name: string; key: string; default: number; min: number; max: number; step: number }[]> = {
  momentum: [
    { name: '回看周期(天)', key: 'lookback', default: 20, min: 5, max: 120, step: 5 },
    { name: '持仓周期(天)', key: 'holding_period', default: 5, min: 1, max: 30, step: 1 },
  ],
  mean_reversion: [
    { name: '统计窗口(天)', key: 'window', default: 20, min: 5, max: 120, step: 5 },
    { name: '入场Z值', key: 'entry_z', default: 2.0, min: 1.0, max: 4.0, step: 0.5 },
    { name: '离场Z值', key: 'exit_z', default: 0.5, min: 0, max: 2.0, step: 0.5 },
    { name: '最短持仓(天)', key: 'holding_period', default: 10, min: 1, max: 30, step: 1 },
  ],
  trend_following: [
    { name: '快线周期', key: 'fast_ma', default: 20, min: 5, max: 60, step: 5 },
    { name: '慢线周期', key: 'slow_ma', default: 60, min: 20, max: 200, step: 10 },
    { name: '信号线周期', key: 'signal_ma', default: 9, min: 3, max: 30, step: 1 },
  ],
  breakout: [
    { name: '突破窗口(天)', key: 'window', default: 50, min: 10, max: 200, step: 10 },
    { name: '放量倍数', key: 'volume_mult', default: 1.5, min: 1.0, max: 5.0, step: 0.5 },
    { name: '持仓周期(天)', key: 'holding_period', default: 10, min: 1, max: 30, step: 1 },
  ],
};

interface BacktestRecord {
  id: string;
  name: string;
  market: string;
  strategy_type: string;
  symbols: string[];
  start_date: string;
  end_date: string;
  initial_capital: number;
  benchmark: string;
  parameters: Record<string, any>;
  status: string;
  progress: number;
  result: any;
  error: string | null;
  created_at: string;
  completed_at: string | null;
}

interface BtEvent {
  backtest_id: string;
  symbol?: string;
  detail?: string;
  status?: string;
  name?: string;
  strategy?: string;
  symbols?: string[];
  total_bars?: number;
  source?: string;
  trades?: number;
  return_pct?: number;
  sharpe?: number;
  max_dd_pct?: number;
  win_rate?: number;
  elapsed_ms?: number;
  metrics?: any;
  total_trades?: number;
  equity_curve?: number[];
  symbol_results?: any;
  error?: string;
}

function subscribeBacktestRun(backtestId: string, handlers: {
  onEvent: (e: BtEvent) => void;
  onDone: (e: BtEvent) => void;
  onError: (err: string) => void;
}): () => void {
  const apiBaseUrl = getApiBaseUrls()[0];
  let aborted = false;

  const run = async () => {
    try {
      const resp = await fetch(`${apiBaseUrl}/api/backtest/${backtestId}/run`, { method: 'POST' });
      if (!resp.ok) { handlers.onError(`HTTP ${resp.status}`); return; }
      const reader = resp.body?.getReader();
      if (!reader) { handlers.onError('无法读取流'); return; }
      const decoder = new TextDecoder();
      let buf = '';
      while (!aborted) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        let evType = '', evData = '';
        for (const line of lines) {
          if (line.startsWith('event: ')) evType = line.slice(7).trim();
          else if (line.startsWith('data: ')) evData = line.slice(6).trim();
          else if (line.trim() === '' && evData) {
            try {
              const parsed = JSON.parse(evData) as BtEvent;
              if (evType === 'bt_done') handlers.onDone(parsed);
              else handlers.onEvent(parsed);
            } catch { /* skip */ }
            evType = ''; evData = '';
          }
        }
      }
      reader.releaseLock();
    } catch (e: any) {
      if (!aborted) handlers.onError(e.message || 'SSE 连接失败');
    }
  };
  run();
  return () => { aborted = true; };
}

const statusColors: Record<string, string> = {
  pending: 'default', running: 'processing', completed: 'success', failed: 'error',
};

const BacktestCenter: React.FC = () => {
  const [records, setRecords] = useState<BacktestRecord[]>([]);
  const [loading, setLoading] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form] = Form.useForm();
  const [strategyType, setStrategyType] = useState('momentum');
  const [runningId, setRunningId] = useState<string | null>(null);
  const [runEvents, setRunEvents] = useState<BtEvent[]>([]);
  const [runProgress, setRunProgress] = useState(0);
  const [detailId, setDetailId] = useState<string | null>(null);
  const [runResult, setRunResult] = useState<BtEvent | null>(null);
  const [loadError, setLoadError] = useState('');

  const loadRecords = useCallback(async () => {
    setLoading(true);
    try {
      setLoadError('');
      const data: any = await apiGet('/api/backtest');
      setRecords(Array.isArray(data.backtests) ? data.backtests : Array.isArray(data) ? data : []);
    } catch (error: any) {
      const detail = error?.message || '回测服务暂不可用';
      setRecords([]);
      setLoadError(detail);
      console.warn('Backtest records load failed:', error);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadRecords(); }, [loadRecords]);

  const createAndRun = useCallback(async () => {
    try {
      const vals = await form.validateFields();
      const params: Record<string, any> = {};
      (STRATEGY_PARAMS[strategyType] || []).forEach(p => {
        if (vals[p.key] !== undefined) params[p.key] = vals[p.key];
      });
      params.commission = vals.commission || 0.001;
      params.slippage = vals.slippage || 0.0005;

      const symbols = (vals.symbols || '').split(/[,，\s]+/).filter(Boolean).map((s: string) => s.toUpperCase().trim());
      if (!symbols.length) { message.error('请输入至少一个标的代码'); return; }

      const [start, end] = vals.dateRange || [];
      const bt: any = await apiPost('/api/backtest', {
        name: vals.name, market: vals.market || 'US', strategy_type: strategyType,
        symbols, start_date: start?.format('YYYY-MM-DD') || '2024-01-01',
        end_date: end?.format('YYYY-MM-DD') || '', initial_capital: vals.initial_capital || 100000,
        benchmark: vals.benchmark || 'SPY', parameters: params,
      });

      setModalOpen(false);
      setRunningId(bt.id);
      setRunEvents([]);
      setRunProgress(0);
      setRunResult(null);

      const unsub = subscribeBacktestRun(bt.id, {
        onEvent: (e) => {
          setRunEvents(prev => [...prev, e]);
          setRunProgress(prev => Math.max(prev, e.elapsed_ms ? 95 : (e.total_bars ? 20 : prev)));
        },
        onDone: (e) => {
          setRunResult(e);
          setRunProgress(100);
          setRunningId(null);
          loadRecords();
        },
        onError: (err) => {
          message.error(err);
          setRunningId(null);
          loadRecords();
        },
      });
    } catch (e: any) {
      if (e?.errorFields) return;
      message.error(e?.message || '创建回测失败');
    }
  }, [form, strategyType, loadRecords]);

  const deleteRecord = useCallback(async (id: string) => {
    try {
      await apiPost(`/api/backtest/${id}`, {}, { method: 'DELETE' } as any);
      message.success('已删除');
      loadRecords();
    } catch (e: any) { message.error(e?.message); }
  }, [loadRecords]);

  const detailRecord = records.find(r => r.id === detailId);

  const tableColumns = [
    { title: '名称', dataIndex: 'name', key: 'name', width: 180, ellipsis: true },
    { title: '策略', dataIndex: 'strategy_type', key: 'strategy_type', width: 100,
      render: (v: string) => {
        const opt = STRATEGY_OPTIONS.find(o => o.value === v);
        return <Tag>{opt?.label || v}</Tag>;
      }},
    { title: '标的', dataIndex: 'symbols', key: 'symbols', width: 132,
      render: (v: string[]) => (
        <Space wrap size={[4, 4]}>
          {(v || []).slice(0, 3).map(s => <Tag key={s} color="blue">{s}</Tag>)}
        </Space>
      ) },
    { title: '初始资金', dataIndex: 'initial_capital', key: 'capital', width: 100,
      render: (v: number) => `$${(v || 0).toLocaleString()}` },
    { title: '状态', dataIndex: 'status', key: 'status', width: 90,
      render: (v: string, r: BacktestRecord) => (
        <Space className="backtest-status-cell">
          <Badge status={statusColors[v] as any} text={v === 'completed' ? '完成' : v === 'running' ? '运行中' : v === 'failed' ? '失败' : '待执行'} />
          {r.status === 'running' && <Progress percent={r.progress} showInfo={false} size="small" style={{ width: 48 }} />}
        </Space>
      )},
    { title: '收益', key: 'return', width: 90,
      render: (_: any, r: BacktestRecord) => {
        const m = r.result?.metrics;
        if (!m) return <Text type="secondary">-</Text>;
        const v = m.total_return_pct;
        const color = v > 0 ? '#22c55e' : v < 0 ? '#ef4444' : '#a1a1aa';
        return <Text style={{ color, fontWeight: 600 }}>{v > 0 ? '+' : ''}{v?.toFixed(1)}%</Text>;
      }},
    { title: '夏普', key: 'sharpe', width: 70,
      render: (_: any, r: BacktestRecord) => {
        const s = r.result?.metrics?.sharpe_ratio;
        return s !== undefined ? <Text>{Number(s).toFixed(2)}</Text> : <Text type="secondary">-</Text>;
      }},
    { title: '最大回撤', key: 'dd', width: 90,
      render: (_: any, r: BacktestRecord) => {
        const d = r.result?.metrics?.max_drawdown_pct;
        return d !== undefined ? <Text type="danger">{Number(d).toFixed(1)}%</Text> : <Text type="secondary">-</Text>;
      }},
    { title: '交易次数', key: 'trades', width: 80,
      render: (_: any, r: BacktestRecord) => <Text>{r.result?.total_trades || '-'}</Text> },
    { title: '创建时间', dataIndex: 'created_at', key: 'created', width: 120,
      render: (v: string) => v?.slice(0, 16)?.replace('T', ' ') },
    { title: '', key: 'actions', width: 86, fixed: 'right' as const,
      render: (_: any, r: BacktestRecord) => (
        <Space size="small">
          <Tooltip title="查看详情"><Button size="small" icon={<LineChartOutlined />} onClick={() => setDetailId(r.id)} /></Tooltip>
          <Tooltip title="删除"><Button size="small" danger icon={<DeleteOutlined />} onClick={() => deleteRecord(r.id)} /></Tooltip>
        </Space>
      )},
  ];

  const metricCards = detailRecord?.result?.metrics ? [
    { title: '总收益', value: `${detailRecord.result.metrics.total_return_pct > 0 ? '+' : ''}${detailRecord.result.metrics.total_return_pct?.toFixed(2)}%`, color: detailRecord.result.metrics.total_return_pct >= 0 ? '#22c55e' : '#ef4444' },
    { title: '年化收益', value: `${detailRecord.result.metrics.annualized_return?.toFixed(2)}%` },
    { title: '夏普比率', value: detailRecord.result.metrics.sharpe_ratio?.toFixed(2) },
    { title: '最大回撤', value: `${detailRecord.result.metrics.max_drawdown_pct?.toFixed(2)}%`, color: '#ef4444' },
    { title: '索提诺比率', value: detailRecord.result.metrics.sortino_ratio?.toFixed(2) },
    { title: '卡玛比率', value: detailRecord.result.metrics.calmar_ratio?.toFixed(2) },
    { title: '胜率', value: `${detailRecord.result.metrics.win_rate?.toFixed(1)}%` },
    { title: '盈亏比', value: detailRecord.result.metrics.profit_factor?.toFixed(2) },
    { title: 'Alpha', value: detailRecord.result.metrics.alpha?.toFixed(4) },
    { title: 'Beta', value: detailRecord.result.metrics.beta?.toFixed(4) },
    { title: '信息比率', value: detailRecord.result.metrics.information_ratio?.toFixed(4) },
    { title: '总交易', value: detailRecord.result?.total_trades || 0 },
  ] : [];

  const eqCurve = detailRecord?.result?.equity_curve as number[] | undefined;
  const eqStart = eqCurve?.[0] || detailRecord?.initial_capital || 100000;
  const eqEnd = eqCurve?.[eqCurve.length - 1] || eqStart;

  return (
    <CenterShell
      icon={<ExperimentOutlined />}
      title="策略回测中心"
      subtitle={<><RocketOutlined /> 4种策略引擎 · yfinance真实数据 · SSE实时反馈</>}
      actions={(
        <Space>
          <Button icon={<ReloadOutlined />} onClick={loadRecords}>刷新</Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>新建回测</Button>
        </Space>
      )}
    >

      {runningId && (
        <Card size="small" className="backtest-running-card">
          <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
            <SyncOutlined spin style={{ fontSize: 18, color: '#1677ff' }} />
            <div style={{ flex: 1 }}>
              <Text strong>回测运行中...</Text>
              <Progress percent={runProgress} size="small" style={{ margin: '4px 0 0' }} />
            </div>
          </div>
          {runEvents.length > 0 && (
            <Timeline style={{ marginTop: 12, fontSize: 12 }} items={runEvents.slice(-8).map(e => ({
              color: e.status === 'error' ? 'red' : e.status === 'done' ? 'green' : 'blue',
              children: <Text style={{ fontSize: 11 }}>{e.detail || `${e.symbol}: ${e.status}`}</Text>,
            }))} />
          )}
          {runResult && (
            <div className="backtest-run-result">
              <Row gutter={16}>
                <Col span={6}><Statistic title="收益" value={`${(runResult.metrics?.total_return_pct || 0) > 0 ? '+' : ''}${runResult.metrics?.total_return_pct?.toFixed(2)}%`} valueStyle={{ color: (runResult.metrics?.total_return_pct || 0) >= 0 ? '#22c55e' : '#ef4444' }} /></Col>
                <Col span={6}><Statistic title="夏普" value={runResult.metrics?.sharpe_ratio?.toFixed(2)} /></Col>
                <Col span={6}><Statistic title="最大回撤" value={`${runResult.metrics?.max_drawdown_pct?.toFixed(2)}%`} valueStyle={{ color: '#ef4444' }} /></Col>
                <Col span={6}><Statistic title="交易次数" value={runResult.total_trades} /></Col>
              </Row>
            </div>
          )}
        </Card>
      )}

      {loadError && (
        <Alert
          className="backtest-service-warning"
          type="warning"
          showIcon
          message="回测服务暂不可用"
          description={loadError}
        />
      )}

      <div className="backtest-table-shell">
        <Table
          rowKey="id"
          columns={tableColumns}
          dataSource={records}
          loading={loading}
          tableLayout="fixed"
          scroll={{ x: 1116 }}
          size="small"
          pagination={{ pageSize: 20 }}
          locale={{ emptyText: <Empty description="暂无回测记录，点击右上角「新建回测」开始" /> }}
          onRow={r => ({ onClick: () => setDetailId(r.id), style: { cursor: 'pointer' } })}
        />
      </div>

      <Modal
        title={<><PlusOutlined /> 新建策略回测</>}
        open={modalOpen}
        onOk={createAndRun}
        onCancel={() => setModalOpen(false)}
        okText="创建并运行"
        width={600}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" size="small">
          <Form.Item name="name" label="回测名称" rules={[{ required: true }]}>
            <Input placeholder="如: NVDA 动量策略回测" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="market" label="市场" initialValue="US">
                <Select options={[{ value: 'US', label: '美股' }, { value: 'CN', label: 'A股' }, { value: 'HK', label: '港股' }]} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item label="策略类型">
                <Select value={strategyType} onChange={setStrategyType} options={STRATEGY_OPTIONS.map(o => ({ value: o.value, label: o.label }))} />
              </Form.Item>
            </Col>
          </Row>
          <Form.Item name="symbols" label="交易标的" rules={[{ required: true }]} extra="多个标的用逗号分隔，如: AAPL,MSFT,NVDA">
            <Input placeholder="AAPL, MSFT, NVDA" />
          </Form.Item>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="dateRange" label="回测区间" rules={[{ required: true }]}>
                <RangePicker style={{ width: '100%' }} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="initial_capital" label="初始资金" initialValue={100000}>
                <InputNumber style={{ width: '100%' }} formatter={v => `$ ${v}`.replace(/\B(?=(\d{3})+(?!\d))/g, ',')} parser={v => Number((v || '').replace(/[^\d.]/g, ''))} />
              </Form.Item>
            </Col>
            <Col span={6}>
              <Form.Item name="benchmark" label="基准" initialValue="SPY">
                <Input placeholder="SPY" />
              </Form.Item>
            </Col>
          </Row>
          <Text strong style={{ fontSize: 12 }}>策略参数 ({STRATEGY_OPTIONS.find(o => o.value === strategyType)?.label})</Text>
          <Text type="secondary" style={{ fontSize: 11, display: 'block', marginBottom: 8 }}>
            {STRATEGY_OPTIONS.find(o => o.value === strategyType)?.desc}
          </Text>
          <Row gutter={12}>
            {(STRATEGY_PARAMS[strategyType] || []).map(p => (
              <Col span={8} key={p.key}>
                <Form.Item name={p.key} label={p.name} initialValue={p.default}>
                  <InputNumber style={{ width: '100%' }} min={p.min} max={p.max} step={p.step} />
                </Form.Item>
              </Col>
            ))}
          </Row>
          <Row gutter={12}>
            <Col span={12}>
              <Form.Item name="commission" label="手续费" initialValue={0.001} extra="如 0.001 = 0.1%">
                <InputNumber style={{ width: '100%' }} min={0} max={0.05} step={0.0005} />
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item name="slippage" label="滑点" initialValue={0.0005} extra="如 0.0005 = 0.05%">
                <InputNumber style={{ width: '100%' }} min={0} max={0.02} step={0.0001} />
              </Form.Item>
            </Col>
          </Row>
        </Form>
      </Modal>

      <Modal
        title={detailRecord ? `${detailRecord.name} · 详细报告` : '回测详情'}
        open={!!detailId}
        onCancel={() => setDetailId(null)}
        width={900}
        footer={null}
        destroyOnHidden
      >
        {detailRecord?.result ? (
          <div>
            <div style={{ marginBottom: 16, background: '#f8f8f8', padding: 12, borderRadius: 8 }}>
              <Row gutter={16}>
                <Col span={4}><Tag color="purple">{STRATEGY_OPTIONS.find(o => o.value === detailRecord.strategy_type)?.label}</Tag></Col>
                <Col span={14}><Text type="secondary">{detailRecord.symbols?.join(' · ')} | {detailRecord.start_date} → {detailRecord.end_date}</Text></Col>
                <Col span={6} style={{ textAlign: 'right' }}>
                  <Text type="secondary">数据来源: {detailRecord.result.data_sources || 'N/A'}</Text>
                </Col>
              </Row>
            </div>

            <Row gutter={[12, 12]}>
              {metricCards.map((m, i) => (
                <Col span={6} key={i}>
                  <Card size="small" bodyStyle={{ padding: '10px 12px' }}>
                    <Statistic
                      title={m.title}
                      value={m.value}
                      valueStyle={{ fontSize: m.value.length > 10 ? 14 : 18, color: m.color || '#262626', fontWeight: 600 }}
                    />
                  </Card>
                </Col>
              ))}
            </Row>

            {eqCurve && eqCurve.length > 1 && (
              <Card size="small" title={<><FundOutlined /> 权益曲线</>} style={{ marginTop: 16 }}>
                <div style={{ width: '100%', height: 200, position: 'relative', background: '#fafafa', borderRadius: 8, overflow: 'hidden' }}>
                  <svg width="100%" height="100%" viewBox={`0 0 800 200`} preserveAspectRatio="none">
                    {(() => {
                      const min = Math.min(...eqCurve);
                      const max = Math.max(...eqCurve);
                      const range = max - min || 1;
                      const points = eqCurve.map((v, i) => `${(i / (eqCurve.length - 1)) * 780 + 10},${200 - ((v - min) / range) * 180 - 10}`);
                      const color = eqEnd >= eqStart ? '#22c55e' : '#ef4444';
                      return (
                        <>
                          <polyline fill="none" stroke={color} strokeWidth="2" points={points.join(' ')} />
                          <line x1="10" y1={200 - ((eqStart - min) / range) * 180 - 10} x2="790" y2={200 - ((eqStart - min) / range) * 180 - 10} stroke="#d4d4d8" strokeWidth="1" strokeDasharray="4,4" />
                          {eqCurve.filter((_, i) => i % Math.max(1, Math.floor(eqCurve.length / 8)) === 0).map((v, i) => (
                            <text key={i} x={(i * Math.max(1, Math.floor(eqCurve.length / 8)) / (eqCurve.length - 1)) * 780 + 10} y="198" fontSize="8" fill="#a1a1aa" textAnchor="middle">
                              ${v.toFixed(0)}
                            </text>
                          ))}
                        </>
                      );
                    })()}
                  </svg>
                </div>
              </Card>
            )}

            {detailRecord.result.trades_log && detailRecord.result.trades_log.length > 0 && (
              <Card size="small" title={<><TableOutlined /> 交易记录</>} style={{ marginTop: 16 }}>
                <Table
                  rowKey={(_r: any, i?: number) => `${_r?.date || ''}-${i ?? 0}`}
                  size="small"
                  pagination={{ pageSize: 10 }}
                  dataSource={detailRecord.result.trades_log}
                  tableLayout="fixed"
                  scroll={{ x: 760 }}
                  columns={[
                    { title: '日期', dataIndex: 'date', width: 100 },
                    { title: '操作', dataIndex: 'action', width: 80, render: (v: string) => <Tag color={v.includes('buy') ? 'green' : 'red'}>{v}</Tag> },
                    { title: '价格', dataIndex: 'price', width: 80 },
                    { title: '数量', dataIndex: 'shares', width: 80 },
                    { title: '金额', dataIndex: 'value', width: 100 },
                    { title: '盈亏', dataIndex: 'pnl', width: 80, render: (v: number) => v ? <Text style={{ color: v >= 0 ? '#22c55e' : '#ef4444' }}>{v >= 0 ? '+' : ''}{v?.toFixed(2)}</Text> : '-' },
                    { title: '原因', dataIndex: 'reason', width: 180, ellipsis: true },
                  ]}
                />
              </Card>
            )}
          </div>
        ) : (
          <Empty description={detailRecord?.status === 'running' ? '正在运行中...' : '暂无回测结果'} />
        )}
      </Modal>
    </CenterShell>
  );
};

export default BacktestCenter;
