import React, { useEffect, useState } from 'react';
import { Alert, Card, Spin, Table, Tag, Typography } from 'antd';
import { apiGet } from '../services/apiClient';

type Metrics = { return_pct: number; max_drawdown_pct: number; sharpe: number | null; avg_exposure_pct: number; cost_pct_initial: number };
type Candidate = { key: string; name: string; holdout: Metrics; screen_passed: boolean; checks: Record<string, boolean> };
type Report = { available: boolean; reason?: string; evaluation: string; as_of: string; split_date: string; holdout_days: number; cost_bps_per_side: number; candidates: Candidate[]; benchmarks: { buy_hold: Metrics; fixed_half: Metrics }; limitations: string[] };
const checkNames: Record<string, string> = { return_vs_trend: '收益超过趋势对照', return_vs_exposure_matched: '收益超过同敞口对照', sharpe_vs_trend: '夏普改善', drawdown_vs_trend: '回撤不恶化', fold_consistency: '多数分段改善', cost_stress: '10bp成本压力通过' };

export default function MacroInsightStudy() {
  const [report, setReport] = useState<Report | null>(null);
  const [error, setError] = useState('');
  useEffect(() => {
    const controller = new AbortController();
    apiGet<Report>('/api/market-dashboard/insight-study', { signal: controller.signal })
      .then(r => { if (!controller.signal.aborted) setReport(r); })
      .catch(e => { if (!controller.signal.aborted) setError(e.message); });
    return () => controller.abort();
  }, []);
  if (error) return <Alert type="error" message={error} showIcon />;
  if (!report) return <Spin tip="加载封存回测报告…" />;
  if (!report.available) return <Alert message={report.reason || '回测报告不可用'} showIcon />;
  const passed = report.candidates.filter(c => c.screen_passed).length;
  const rows = [...report.candidates.map(c => ({ ...c, ...c.holdout })),
    { key: 'buy_hold', name: '买入持有对照', ...report.benchmarks.buy_hold },
    { key: 'fixed_half', name: '固定50%敞口对照', ...report.benchmarks.fixed_half }];
  return <Card title="跨模块洞察 · 回测证据">
    <Alert type={passed ? 'info' : 'warning'} showIcon
      message={passed ? '历史筛选通过，仍待新的前瞻验证' : '本轮没有候选通过筛选'}
      description="固定检验四指数共振、量价确认及指数同步。所有失败结果保留；历史筛选不赋予交易信号资格。" />
    <Typography.Paragraph style={{ marginTop: 16 }}>
      {report.evaluation}。评估区间 {report.split_date} 至 {report.as_of}，共 {report.holdout_days} 个交易日；收盘生成信号，下一交易日开盘调仓，单边成本 {report.cost_bps_per_side}bp，计入首尾交易。
    </Typography.Paragraph>
    <Table dataSource={rows} pagination={false} size="small" scroll={{ x: 760 }} columns={[
      { title: '规则 / 对照', dataIndex: 'name', key: 'name' },
      { title: '区间净收益', dataIndex: 'return_pct', render: (v: number) => `${v.toFixed(2)}%` },
      { title: '最大回撤', dataIndex: 'max_drawdown_pct', render: (v: number) => `${v.toFixed(2)}%` },
      { title: '夏普', dataIndex: 'sharpe', render: (v: number | null) => v === null ? '—' : v.toFixed(2) },
      { title: '平均敞口', dataIndex: 'avg_exposure_pct', render: (v: number) => `${v}%` },
      { title: '成本/初始净值', dataIndex: 'cost_pct_initial', render: (v: number) => `${v.toFixed(2)}%` },
    ]} />
    {report.candidates.filter(c => c.key !== 'trend').map(c => <div key={c.key} style={{ marginTop: 16 }}>
      <Typography.Text strong>{c.name}：</Typography.Text>
      {Object.entries(c.checks).map(([key, ok]) => <Tag key={key} color={ok ? 'green' : 'default'}>{ok ? '通过' : '未通过'} · {checkNames[key] || key}</Tag>)}
    </div>)}
    <Typography.Paragraph type="secondary" style={{ marginTop: 16 }}>
      规则固定：收盘高于20日均线；共振要求四指数至少三个满足；量价确认额外要求成交量超过20日均量；指数同步同时要求沪深300和至少两个其他指数满足。满足时研究敞口100%，否则25%。
    </Typography.Paragraph>
    {report.limitations.map((text, i) => <Typography.Paragraph type="secondary" key={i} style={{ marginBottom: 4 }}>{text}</Typography.Paragraph>)}
  </Card>;
}
