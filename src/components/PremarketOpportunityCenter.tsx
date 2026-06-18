import React, { useCallback, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Statistic,
  Table,
  Tag,
  Typography
} from 'antd';
import {
  AimOutlined,
  ApiOutlined,
  CheckCircleOutlined,
  FieldTimeOutlined,
  GlobalOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import DataCenter from './DataCenter';
import {
  getPremarketOpportunities,
  PremarketMappedAsset,
  PremarketOpportunityResponse,
  PremarketQuoteSignal,
  PremarketThemeSignal,
  PremarketThemeStance
} from '../services/marketService';

const { Paragraph, Text, Title } = Typography;

interface PremarketOpportunityCenterProps {
  appState: AppState;
}

const stanceColor: Record<PremarketThemeStance, string> = {
  重点机会: 'green',
  观察机会: 'cyan',
  中性观察: 'blue',
  风险回避: 'red'
};

const marketColor: Record<string, string> = {
  US: 'blue',
  HK: 'cyan',
  CN: 'red',
  OTHER: 'default'
};

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

const formatPercent = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  const sign = value > 0 ? '+' : '';
  return `${sign}${numberFormat.format(value)}%`;
};

const alertTypeForStance = (stance: PremarketThemeStance): 'success' | 'info' | 'warning' | 'error' => {
  if (stance === '重点机会') return 'success';
  if (stance === '观察机会') return 'info';
  if (stance === '风险回避') return 'error';
  return 'info';
};

const PremarketOpportunityCenter: React.FC<PremarketOpportunityCenterProps> = ({ appState }) => {
  const { message } = AntdApp.useApp();
  const [selectedThemeKey, setSelectedThemeKey] = useState<string | null>(null);

  const fetchData = useCallback(async () => {
    const data = await getPremarketOpportunities();
    setSelectedThemeKey(prev => (
      prev && data.themes.some(theme => theme.theme_key === prev)
        ? prev
        : data.themes[0]?.theme_key || null
    ));
    message.success(`盘前机会雷达已更新：${data.themes.length} 个主题`);
    return data;
  }, [message]);

  const getActiveTheme = (data: PremarketOpportunityResponse) => {
    if (!data.themes.length) return null;
    return data.themes.find(theme => theme.theme_key === selectedThemeKey) || data.themes[0];
  };

  const computeStats = (data: PremarketOpportunityResponse) => {
    const themes = data.themes || [];
    return {
      total: themes.length,
      opportunities: themes.filter(theme => ['重点机会', '观察机会'].includes(theme.stance)).length,
      risks: themes.filter(theme => theme.stance === '风险回避').length,
      mappedAssets: themes.reduce((sum, theme) => sum + theme.mapped_assets.length, 0)
    };
  };

  const themeColumns = [
    {
      title: '主题',
      key: 'theme',
      render: (_: unknown, item: PremarketThemeSignal) => (
        <button
          type="button"
          className="options-symbol-button"
          onClick={() => setSelectedThemeKey(item.theme_key)}
        >
          <Text strong>{item.theme_name}</Text>
          <Text type="secondary">{item.direction} · {item.confidence}置信</Text>
        </button>
      )
    },
    {
      title: '判断',
      key: 'stance',
      sorter: (a: PremarketThemeSignal, b: PremarketThemeSignal) => a.opportunity_score - b.opportunity_score,
      render: (_: unknown, item: PremarketThemeSignal) => (
        <Space direction="vertical" size={2}>
          <Tag color={stanceColor[item.stance]}>{item.stance}</Tag>
          <Progress
            percent={item.opportunity_score}
            size="small"
            status={item.stance === '风险回避' ? 'exception' : 'normal'}
          />
        </Space>
      )
    },
    {
      title: '风险',
      key: 'risk',
      sorter: (a: PremarketThemeSignal, b: PremarketThemeSignal) => a.risk_score - b.risk_score,
      render: (_: unknown, item: PremarketThemeSignal) => (
        <Space direction="vertical" size={2}>
          <Text>{item.risk_score}/100</Text>
          <Progress percent={item.risk_score} size="small" status={item.risk_score >= 72 ? 'exception' : 'normal'} />
        </Space>
      )
    },
    {
      title: '信号拆分',
      key: 'scores',
      render: (_: unknown, item: PremarketThemeSignal) => (
        <Space direction="vertical" size={0}>
          <Text type="secondary">美股 {item.us_impulse_score}</Text>
          <Text type="secondary">期权 {item.option_confirmation_score}</Text>
          <Text type="secondary">确认 {item.china_confirmation_score}</Text>
        </Space>
      )
    },
    {
      title: '映射',
      key: 'mapped',
      render: (_: unknown, item: PremarketThemeSignal) => (
        <Space wrap size={4}>
          {item.mapped_assets.slice(0, 3).map(asset => (
            <Tag key={asset.symbol} color={marketColor[asset.market]}>{asset.name}</Tag>
          ))}
        </Space>
      )
    }
  ];

  const quoteColumns = [
    {
      title: '资产',
      key: 'symbol',
      render: (_: unknown, item: PremarketQuoteSignal) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.symbol}</Text>
          <Text type="secondary">{item.role}</Text>
        </Space>
      )
    },
    {
      title: '涨跌',
      key: 'change',
      sorter: (a: PremarketQuoteSignal, b: PremarketQuoteSignal) => (a.change_percent || 0) - (b.change_percent || 0),
      render: (_: unknown, item: PremarketQuoteSignal) => (
        <Text className={(item.change_percent || 0) >= 0 ? 'quote-positive' : 'quote-negative'}>
          {formatPercent(item.change_percent)}
        </Text>
      )
    },
    {
      title: '来源',
      key: 'provider',
      render: (_: unknown, item: PremarketQuoteSignal) => <Text type="secondary">{item.provider || '--'}</Text>
    }
  ];

  const assetColumns = [
    {
      title: '映射标的',
      key: 'asset',
      render: (_: unknown, item: PremarketMappedAsset) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.name}</Text>
          <Text type="secondary">{item.symbol}</Text>
        </Space>
      )
    },
    {
      title: '角色',
      key: 'role',
      render: (_: unknown, item: PremarketMappedAsset) => <Tag color={marketColor[item.market]}>{item.role}</Tag>
    },
    {
      title: '原因',
      key: 'reason',
      render: (_: unknown, item: PremarketMappedAsset) => <Text>{item.reason}</Text>
    }
  ];

  return (
    <DataCenter
      className="options-signal-page"
      title="盘前机会雷达"
      fetchData={fetchData}
      refreshLabel="刷新雷达"
      emptyText="等待生成盘前机会雷达"
      renderContent={(data, refresh) => {
        const activeTheme = getActiveTheme(data);
        const stats = computeStats(data);
        return (
          <>
            <Row gutter={[14, 14]} className="options-stat-row">
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="风险偏好" value={data.regime_score || 0} suffix="/100" prefix={<GlobalOutlined />} />
                  <Text type="secondary">{data.market_phase || '等待数据'}</Text>
                </Card>
              </Col>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="机会主题" value={stats.opportunities} suffix="个" prefix={<ThunderboltOutlined />} />
                  <Text type="secondary">需开盘确认</Text>
                </Card>
              </Col>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="风险主题" value={stats.risks} suffix="个" prefix={<SafetyCertificateOutlined />} />
                  <Text type="secondary">优先排查</Text>
                </Card>
              </Col>
              <Col xs={12} md={6}>
                <Card>
                  <Statistic title="A/H映射" value={stats.mappedAssets} suffix="个" prefix={<AimOutlined />} />
                  <Text type="secondary">自选 {appState.stocks.length} 个</Text>
                </Card>
              </Col>
            </Row>

            {data.warnings.length ? (
              <Alert
                className="options-warning"
                type="warning"
                showIcon
                message={data.warnings.slice(0, 3).join('；')}
              />
            ) : null}
            <Row gutter={[14, 14]}>
              <Col xs={24} xl={15}>
                <Card title={<Space><ThunderboltOutlined />主题机会排序</Space>}>
                  <Table
                    rowKey="theme_key"
                    columns={themeColumns}
                    dataSource={data.themes}
                    pagination={false}
                    scroll={{ x: 960 }}
                    rowClassName={record => record.theme_key === activeTheme?.theme_key ? 'options-active-row' : ''}
                  />
                </Card>
              </Col>
              <Col xs={24} xl={9}>
                <Card title={<Space><CheckCircleOutlined />开盘确认</Space>}>
                  <Space direction="vertical" size={10}>
                    {data.confirmation_checklist.map(item => (
                      <Text key={item}>{item}</Text>
                    ))}
                  </Space>
                </Card>
              </Col>
            </Row>

            {activeTheme ? (
              <Card className="options-detail-card">
                <div className="options-detail-head">
                  <Space wrap>
                    <Title level={4}>{activeTheme.theme_name}</Title>
                    <Tag color={stanceColor[activeTheme.stance]}>{activeTheme.stance}</Tag>
                    <Tag>机会 {activeTheme.opportunity_score}/100</Tag>
                    <Tag color={activeTheme.risk_score >= 72 ? 'red' : 'blue'}>风险 {activeTheme.risk_score}/100</Tag>
                    <Tag>{activeTheme.confidence}置信</Tag>
                  </Space>
                  <Space wrap>
                    <Text>美股强度 {activeTheme.us_impulse_score}/100</Text>
                    <Text>期权确认 {activeTheme.option_confirmation_score}/100</Text>
                    <Text>确认资产 {activeTheme.china_confirmation_score}/100</Text>
                  </Space>
                </div>

                <Alert
                  className="options-forecast-panel"
                  type={alertTypeForStance(activeTheme.stance)}
                  showIcon
                  message={activeTheme.suggested_action}
                  description={(
                    <div className="options-tail-risk-content">
                      <div>
                        <Text strong>证据</Text>
                        <ul>
                          {activeTheme.evidence.slice(0, 6).map(item => <li key={item}>{item}</li>)}
                        </ul>
                      </div>
                      <div>
                        <Text strong>触发/失效</Text>
                        <ul>
                          {activeTheme.triggers.slice(0, 3).map(item => <li key={item}>{item}</li>)}
                          {activeTheme.invalidations.slice(0, 2).map(item => <li key={item}>{item}</li>)}
                        </ul>
                      </div>
                    </div>
                  )}
                />

                <Row gutter={[14, 14]}>
                  <Col xs={24} lg={12}>
                    <div className="premarket-detail-panel">
                      <div className="premarket-detail-panel-head"><Space><ApiOutlined />隔夜资产</Space></div>
                      <Table
                        rowKey={item => `${item.role}-${item.symbol}`}
                        columns={quoteColumns}
                        dataSource={activeTheme.leader_quotes}
                        pagination={false}
                        scroll={{ x: 520 }}
                      />
                    </div>
                  </Col>
                  <Col xs={24} lg={12}>
                    <div className="premarket-detail-panel">
                      <div className="premarket-detail-panel-head"><Space><FieldTimeOutlined />A/H 映射</Space></div>
                      <Table
                        rowKey="symbol"
                        columns={assetColumns}
                        dataSource={activeTheme.mapped_assets}
                        pagination={false}
                        scroll={{ x: 620 }}
                      />
                    </div>
                  </Col>
                </Row>
              </Card>
            ) : null}
          </>
        );
      }}
    />
  );
};

export default PremarketOpportunityCenter;
