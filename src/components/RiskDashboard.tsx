import React, { useCallback } from 'react';
import {
  Alert,
  Card,
  Col,
  Descriptions,
  Progress,
  Row,
  Statistic,
  Table,
  Tag,
  Typography,
  App as AntdApp,
} from 'antd';
import {
  ArrowDownOutlined,
  ArrowUpOutlined,
  ExclamationCircleOutlined,
  SafetyCertificateOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import DataCenter from './DataCenter';
import { getRiskSummary, RiskSummaryResponse, RiskAlert, PositionRecord } from '../services/riskService';

const { Title, Text } = Typography;

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const usdFormat = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const fmtUsd = (v: number): string => usdFormat.format(v);
const fmtNum = (v: number): string => numberFormat.format(v);
const fmtPct = (v: number): string => `${v >= 0 ? '+' : ''}${v.toFixed(2)}%`;

const riskLevelColor: Record<string, string> = {
  low: 'green',
  medium: 'orange',
  high: 'red',
};

const alertLevelColor: Record<string, string> = {
  info: 'blue',
  warning: 'orange',
  danger: 'red',
};

const alertLevelIcon: Record<string, React.ReactNode> = {
  info: <ExclamationCircleOutlined />,
  warning: <WarningOutlined />,
  danger: <ExclamationCircleOutlined />,
};

const positionColumns = [
  {
    title: '代码',
    dataIndex: 'symbol',
    key: 'symbol',
    width: 100,
  },
  {
    title: '名称',
    dataIndex: 'name',
    key: 'name',
    width: 100,
  },
  {
    title: '方向',
    dataIndex: 'direction',
    key: 'direction',
    width: 60,
    render: (d: string) => (
      <Tag color={d === 'long' ? 'green' : 'red'}>
        {d === 'long' ? '多头' : '空头'}
      </Tag>
    ),
  },
  {
    title: '持仓量',
    dataIndex: 'quantity',
    key: 'quantity',
    width: 70,
    render: (v: number) => fmtNum(v),
  },
  {
    title: '入场价',
    dataIndex: 'entry_price',
    key: 'entry_price',
    width: 80,
    render: (v: number) => fmtNum(v),
  },
  {
    title: '现价',
    dataIndex: 'current_price',
    key: 'current_price',
    width: 80,
    render: (v: number | null) => v ? fmtNum(v) : '--',
  },
  {
    title: '名义市值',
    dataIndex: 'notional_value',
    key: 'notional_value',
    width: 100,
    render: (v: number) => fmtUsd(v),
  },
  {
    title: '未实现盈亏',
    dataIndex: 'unrealized_pnl',
    key: 'unrealized_pnl',
    width: 120,
    render: (v: number, record: PositionRecord) => (
      <span style={{ color: v >= 0 ? '#52c41a' : '#ff4d4f' }}>
        {fmtUsd(v)} ({fmtPct(record.unrealized_pnl_pct)})
      </span>
    ),
  },
  {
    title: '止损',
    dataIndex: 'stop_loss',
    key: 'stop_loss',
    width: 80,
    render: (v: number | null) => v ? <Text type="danger">{fmtNum(v)}</Text> : '--',
  },
  {
    title: '止盈',
    dataIndex: 'take_profit',
    key: 'take_profit',
    width: 80,
    render: (v: number | null) => v ? <Text type="success">{fmtNum(v)}</Text> : '--',
  },
  {
    title: '行业',
    dataIndex: 'sector',
    key: 'sector',
    width: 80,
  },
];

function RiskDashboard() {
  const renderContent = useCallback((data: RiskSummaryResponse, refresh: () => void) => {
    const { portfolio, var: varResult, alerts, risk_limits } = data;
    const positions = data.open_positions || [];

    // 无持仓时给出有价值的空状态（而非一整屏 0），说明仪表盘能力 + 引导添加头寸。
    if (positions.length === 0) {
      return (
        <div style={{ textAlign: 'center', padding: '48px 24px', maxWidth: 560, margin: '0 auto' }}>
          <SafetyCertificateOutlined style={{ fontSize: 44, color: 'var(--accent, #1677ff)', marginBottom: 16 }} />
          <Title level={4} style={{ marginBottom: 8 }}>暂无持仓</Title>
          <Text type="secondary" style={{ display: 'block', lineHeight: 1.8 }}>
            添加头寸后，这里会实时呈现 <b>组合市值 / 盈亏</b>、<b>VaR(95% 单日)</b>、<b>多空与净敞口</b>、
            <b>行业集中度</b> 与 <b>风险预警</b>，并对照你设定的风险限额给出越界提示。
          </Text>
          <div style={{
            marginTop: 20, display: 'inline-flex', flexWrap: 'wrap', gap: 8, justifyContent: 'center',
          }}>
            <Tag color="blue">组合 VaR</Tag>
            <Tag color="green">多空敞口</Tag>
            <Tag color="orange">行业集中度</Tag>
            <Tag color="red">越界预警</Tag>
            <Tag>Sharpe / 胜率</Tag>
          </div>
          <Text type="secondary" style={{ display: 'block', marginTop: 20, fontSize: 13 }}>
            在「持仓监控」中添加头寸即可启用本仪表盘。
          </Text>
        </div>
      );
    }

    return (
      <div>
        {alerts.length > 0 && (
          <div style={{ marginBottom: 16 }}>
            {alerts.map((alert, idx) => (
              <Alert
                key={idx}
                type={alert.level === 'danger' ? 'error' : alert.level === 'warning' ? 'warning' : 'info'}
                showIcon
                icon={alertLevelIcon[alert.level]}
                message={alert.message}
                style={{ marginBottom: 8 }}
                banner
              />
            ))}
          </div>
        )}

        <Row gutter={[16, 16]}>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="组合总市值"
                value={portfolio.total_value}
                precision={0}
                prefix="$"
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="总盈亏"
                value={portfolio.total_pnl}
                precision={0}
                prefix="$"
                valueStyle={{ color: portfolio.total_pnl >= 0 ? '#52c41a' : '#ff4d4f' }}
                suffix={portfolio.total_pnl_pct !== 0 ? `(${fmtPct(portfolio.total_pnl_pct)})` : ''}
              />
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="VaR (95% 单日)"
                value={varResult.var_pct}
                precision={2}
                suffix="%"
                valueStyle={{ color: '#faad14' }}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                金额: {fmtUsd(varResult.var_amount)}
              </Text>
            </Card>
          </Col>
          <Col span={6}>
            <Card size="small">
              <Statistic
                title="持仓数"
                value={portfolio.open_count}
                suffix={`/ ${portfolio.position_count}`}
              />
              <Text type="secondary" style={{ fontSize: 12 }}>
                胜率: {portfolio.win_rate}%
              </Text>
            </Card>
          </Col>
        </Row>

        <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
          <Col span={12}>
            <Card title="多空敞口" size="small">
              <Row gutter={16}>
                <Col span={8}>
                  <Statistic
                    title="多头"
                    value={portfolio.direction_exposure.long}
                    precision={0}
                    prefix="$"
                    valueStyle={{ color: '#52c41a' }}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="空头"
                    value={portfolio.direction_exposure.short}
                    precision={0}
                    prefix="$"
                    valueStyle={{ color: '#ff4d4f' }}
                  />
                </Col>
                <Col span={8}>
                  <Statistic
                    title="净敞口"
                    value={portfolio.direction_exposure.net}
                    precision={0}
                    prefix="$"
                    valueStyle={{ color: portfolio.direction_exposure.net >= 0 ? '#52c41a' : '#ff4d4f' }}
                  />
                </Col>
              </Row>
            </Card>
          </Col>
          <Col span={12}>
            <Card title="风险指标" size="small">
              <Descriptions column={2} size="small">
                <Descriptions.Item label="Sharpe Ratio">
                  {varResult.sharpe_ratio.toFixed(2)}
                </Descriptions.Item>
                <Descriptions.Item label="年化波动">
                  {varResult.std_dev.toFixed(2)}%
                </Descriptions.Item>
                <Descriptions.Item label="集中度风险">
                  <Tag color={riskLevelColor[portfolio.concentration_risk] || 'default'}>
                    {portfolio.concentration_risk === 'low' ? '低' : portfolio.concentration_risk === 'medium' ? '中' : '高'}
                  </Tag>
                </Descriptions.Item>
                <Descriptions.Item label="VaR方法">
                  {varResult.method === 'parametric' ? '参数法' : varResult.method}
                </Descriptions.Item>
              </Descriptions>
            </Card>
          </Col>
        </Row>

        {Object.keys(portfolio.sector_exposure).length > 0 && (
          <Card title="行业敞口" size="small" style={{ marginTop: 16 }}>
            <Row gutter={[16, 8]}>
              {Object.entries(portfolio.sector_exposure).map(([sector, pct]) => (
                <Col span={6} key={sector}>
                  <div style={{ marginBottom: 4 }}>
                    <Text>{sector}</Text>
                    <Progress
                      percent={pct}
                      size="small"
                      strokeColor={pct > 40 ? '#ff4d4f' : pct > 20 ? '#faad14' : '#52c41a'}
                    />
                  </div>
                </Col>
              ))}
            </Row>
          </Card>
        )}

        {portfolio.risk_flags.length > 0 && (
          <Card size="small" style={{ marginTop: 16 }}>
            {portfolio.risk_flags.map((flag, idx) => (
              <Tag color="orange" key={idx} style={{ marginBottom: 4 }}>{flag}</Tag>
            ))}
          </Card>
        )}

        <Card title={`持仓明细 (${positions.length})`} style={{ marginTop: 16 }}>
          <Table
            dataSource={positions}
            columns={positionColumns}
            rowKey="id"
            size="small"
            scroll={{ x: 1000 }}
            pagination={{ pageSize: 20 }}
          />
        </Card>
      </div>
    );
  }, []);

  return (
    <DataCenter<RiskSummaryResponse>
      title="风险管理仪表盘"
      subtitle="组合风险、VaR、行业敞口与实时预警"
      fetchData={getRiskSummary}
      renderContent={renderContent}
      emptyText="暂无持仓数据，请先在持仓监控中添加头寸"
      refreshLabel="刷新风险数据"
    />
  );
}

export default RiskDashboard;