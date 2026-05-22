import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Checkbox,
  Col,
  Empty,
  Progress,
  Row,
  Select,
  Slider,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  App as AntdApp
} from 'antd';
import {
  CheckCircleOutlined,
  DeploymentUnitOutlined,
  FundProjectionScreenOutlined,
  PartitionOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import {
  BacktestPlan,
  CandidateOpinion,
  DecisionMarket,
  DecisionMode,
  DecisionRiskProfile,
  DecisionDataProfile,
  DecisionHorizon,
  DecisionDependency,
  DecisionModuleStatus,
  MarketStyleSignal,
  MultiMarketDecisionResponse,
  runMultiMarketDecision
} from '../services/multiMarketDecisionService';

const { Paragraph, Text, Title } = Typography;

interface MultiMarketDecisionCenterProps {
  appState: AppState;
}

const marketOptions: Array<{ label: string; value: DecisionMarket }> = [
  { label: 'A股', value: 'CN' },
  { label: '港股', value: 'HK' },
  { label: '美股', value: 'US' }
];

const marketLabel: Record<DecisionMarket, string> = {
  CN: 'A股',
  HK: '港股',
  US: '美股',
  OTHER: '其他'
};

const actionMeta: Record<CandidateOpinion['action'], { color: string; text: string }> = {
  重点跟踪: { color: 'green', text: '重点跟踪' },
  观察: { color: 'blue', text: '观察' },
  回避: { color: 'red', text: '回避' }
};

const statusMeta: Record<DecisionModuleStatus['status'], { color: string; text: string }> = {
  ready: { color: 'green', text: '就绪' },
  partial: { color: 'orange', text: '待配置' },
  planned: { color: 'default', text: '规划中' }
};

const regimeMeta: Record<MarketStyleSignal['risk_regime'], { color: string }> = {
  进攻: { color: 'green' },
  均衡: { color: 'blue' },
  防守: { color: 'orange' }
};

const MultiMarketDecisionCenter: React.FC<MultiMarketDecisionCenterProps> = ({ appState }) => {
  const { message } = AntdApp.useApp();
  const [markets, setMarkets] = useState<DecisionMarket[]>(['CN', 'HK', 'US']);
  const [horizon, setHorizon] = useState<DecisionHorizon>('10日');
  const [mode, setMode] = useState<DecisionMode>('research');
  const [riskProfile, setRiskProfile] = useState<DecisionRiskProfile>('稳健');
  const [dataProfile, setDataProfile] = useState<DecisionDataProfile>('china_stable');
  const [minScore, setMinScore] = useState(58);
  const [result, setResult] = useState<MultiMarketDecisionResponse | null>(null);
  const [loading, setLoading] = useState(false);

  const selectedStocks = useMemo(() => {
    return appState.stocks.filter(stock => markets.includes((stock.market || 'US') as DecisionMarket));
  }, [appState.stocks, markets]);

  const runDecision = async () => {
    if (markets.length === 0) {
      message.warning('至少选择一个市场');
      return;
    }
    setLoading(true);
    try {
      const data = await runMultiMarketDecision({
        markets,
        horizon,
        mode,
        risk_profile: riskProfile,
        data_profile: dataProfile,
        objective: '综合分析 A股、港股、美股市场风格、板块意见、个股候选、大涨前概率排序和回测验证优先级。',
        stocks: selectedStocks,
        min_score: minScore
      });
      setResult(data);
      message.success('多市场决策快照已生成');
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认后端 API 已启动';
      message.error(`生成失败：${detail}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void runDecision();
  }, []);

  const candidateColumns = [
    {
      title: '标的',
      key: 'symbol',
      render: (_: unknown, item: CandidateOpinion) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.name}</Text>
          <Text type="secondary">{item.symbol} · {marketLabel[item.market]}</Text>
        </Space>
      )
    },
    {
      title: '板块',
      dataIndex: 'sector',
      key: 'sector'
    },
    {
      title: '动作',
      key: 'action',
      render: (_: unknown, item: CandidateOpinion) => (
        <Tag color={actionMeta[item.action].color}>{actionMeta[item.action].text}</Tag>
      )
    },
    {
      title: '综合分',
      dataIndex: 'score',
      key: 'score',
      sorter: (a: CandidateOpinion, b: CandidateOpinion) => a.score - b.score,
      render: (score: number) => <Progress percent={score} size="small" />
    },
    {
      title: '大涨概率',
      dataIndex: 'surge_probability',
      key: 'surge_probability',
      sorter: (a: CandidateOpinion, b: CandidateOpinion) => a.surge_probability - b.surge_probability,
      render: (value: number) => <Text strong>{Math.round(value * 100)}%</Text>
    },
    {
      title: '证据',
      key: 'evidence',
      render: (_: unknown, item: CandidateOpinion) => (
        <Space direction="vertical" size={2}>
          {item.evidence.slice(0, 3).map(line => <Text key={line}>{line}</Text>)}
        </Space>
      )
    },
    {
      title: '失效条件',
      key: 'invalidation',
      render: (_: unknown, item: CandidateOpinion) => (
        <Space wrap size={[4, 4]}>
          {item.invalidation.slice(0, 3).map(line => <Tag key={line}>{line}</Tag>)}
        </Space>
      )
    }
  ];

  const moduleColumns = [
    {
      title: '模块',
      key: 'name',
      render: (_: unknown, item: DecisionModuleStatus) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.name}</Text>
          <Text type="secondary">{marketLabel[item.market]} · {item.purpose}</Text>
        </Space>
      )
    },
    {
      title: '状态',
      key: 'status',
      render: (_: unknown, item: DecisionModuleStatus) => (
        <Tag color={statusMeta[item.status].color}>{statusMeta[item.status].text}</Tag>
      )
    },
    {
      title: '就绪度',
      dataIndex: 'readiness',
      key: 'readiness',
      render: (readiness: number) => <Progress percent={readiness} size="small" />
    },
    {
      title: '研究/回测',
      key: 'engines',
      render: (_: unknown, item: DecisionModuleStatus) => (
        <Space direction="vertical" size={0}>
          <Text>{item.research_engine}</Text>
          <Text type="secondary">{item.backtest_engine}</Text>
        </Space>
      )
    },
    {
      title: '数据源',
      key: 'data',
      render: (_: unknown, item: DecisionModuleStatus) => (
        <Space wrap>
          {item.data_sources.map(source => <Tag key={source}>{source}</Tag>)}
        </Space>
      )
    },
    {
      title: '待配置',
      key: 'blocked',
      render: (_: unknown, item: DecisionModuleStatus) => (
        item.blocked_by.length ? (
          <Space direction="vertical" size={2}>
            {item.blocked_by.map(line => <Text key={line} type="secondary">{line}</Text>)}
          </Space>
        ) : <Tag color="green">无硬阻塞</Tag>
      )
    }
  ];

  const dependencyColumns = [
    {
      title: '依赖',
      key: 'name',
      render: (_: unknown, item: DecisionDependency) => (
        <Space direction="vertical" size={0}>
          <Text strong>{item.name}</Text>
          <Text type="secondary">{item.role}</Text>
        </Space>
      )
    },
    {
      title: '市场',
      key: 'markets',
      render: (_: unknown, item: DecisionDependency) => (
        <Space wrap>
          {item.markets.map(market => <Tag key={market}>{marketLabel[market]}</Tag>)}
        </Space>
      )
    },
    {
      title: '国内可跑',
      key: 'mainland',
      render: (_: unknown, item: DecisionDependency) => (
        <Tag color={item.mainland_ready ? 'green' : 'red'}>{item.mainland_ready ? '是' : '否'}</Tag>
      )
    },
    {
      title: '安装/配置',
      key: 'install',
      render: (_: unknown, item: DecisionDependency) => (
        <Space direction="vertical" size={0}>
          <Text code>{item.install_hint || '内置/手工配置'}</Text>
          {item.config_keys.length > 0 && <Text type="secondary">{item.config_keys.join(' / ')}</Text>}
        </Space>
      )
    },
    {
      title: '说明',
      dataIndex: 'notes',
      key: 'notes'
    }
  ];

  const backtestColumns = [
    {
      title: '市场',
      key: 'market',
      render: (_: unknown, item: BacktestPlan) => <Tag>{marketLabel[item.market]}</Tag>
    },
    {
      title: '引擎',
      dataIndex: 'engine',
      key: 'engine'
    },
    {
      title: '数据要求',
      key: 'data_requirements',
      render: (_: unknown, item: BacktestPlan) => (
        <Space wrap>
          {item.data_requirements.map(line => <Tag key={line}>{line}</Tag>)}
        </Space>
      )
    },
    {
      title: '交易规则',
      key: 'trading_rules',
      render: (_: unknown, item: BacktestPlan) => (
        <Space wrap>
          {item.trading_rules.map(line => <Tag key={line}>{line}</Tag>)}
        </Space>
      )
    },
    {
      title: '下一步',
      dataIndex: 'next_step',
      key: 'next_step'
    }
  ];

  return (
    <div className="multi-market-page">
      <div className="multi-market-toolbar">
        <div>
          <Title level={3}>多市场智能选股与回测中心</Title>
          <Paragraph type="secondary">
            A股、港股、美股分模块运行，统一输出市场风格、板块意见、个股候选和大涨前概率排序。
          </Paragraph>
        </div>
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={runDecision}
        >
          生成决策快照
        </Button>
      </div>

      <Row gutter={[14, 14]} className="multi-market-controls">
        <Col xs={24} lg={8}>
          <Card>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Text strong>市场模块</Text>
              <Checkbox.Group
                options={marketOptions}
                value={markets}
                onChange={values => setMarkets(values as DecisionMarket[])}
              />
              <Text type="secondary">当前样本：{selectedStocks.length} 个自选标的</Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Text strong>分析参数</Text>
              <Space wrap>
                <Select
                  value={horizon}
                  style={{ width: 112 }}
                  onChange={value => setHorizon(value)}
                  options={['5日', '10日', '20日', '60日'].map(value => ({ value, label: value }))}
                />
                <Select
                  value={mode}
                  style={{ width: 112 }}
                  onChange={setMode}
                  options={[
                    { value: 'research', label: '研究' },
                    { value: 'backtest', label: '回测' },
                    { value: 'paper', label: '模拟' }
                  ]}
                />
                <Select
                  value={riskProfile}
                  style={{ width: 112 }}
                  onChange={setRiskProfile}
                  options={['保守', '稳健', '进取', '专业'].map(value => ({ value, label: value }))}
                />
              </Space>
              <div>
                <Text type="secondary">候选最低分：{minScore}</Text>
                <Slider min={0} max={90} value={minScore} onChange={setMinScore} />
              </div>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Text strong>大陆网络模式</Text>
              <Select
                value={dataProfile}
                onChange={setDataProfile}
                options={[
                  { value: 'china_stable', label: '中国大陆稳定运行' },
                  { value: 'offline', label: '纯本地/离线数据' },
                  { value: 'mixed', label: '混合数据源' }
                ]}
              />
              <Alert
                type="success"
                showIcon
                message="不把 yfinance、OpenBB、Polygon、Alpaca、OpenAI 作为硬依赖"
              />
            </Space>
          </Card>
        </Col>
      </Row>

      {result ? (
        <>
          <Row gutter={[14, 14]} className="multi-market-stats">
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="系统就绪度" value={result.readiness_score} suffix="/100" prefix={<CheckCircleOutlined />} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="候选标的" value={result.candidates.length} prefix={<ThunderboltOutlined />} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="市场模块" value={result.modules.length} prefix={<DeploymentUnitOutlined />} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="回测计划" value={result.backtest_plan.length} prefix={<FundProjectionScreenOutlined />} />
              </Card>
            </Col>
          </Row>

          <Alert
            className="multi-market-summary"
            type="info"
            showIcon
            message={result.summary}
            description={result.disclaimer}
          />

          {result.warnings.length > 0 && (
            <Alert
              className="multi-market-summary"
              type="warning"
              showIcon
              message={result.warnings.join('；')}
            />
          )}

          <Row gutter={[14, 14]}>
            {result.market_styles.map(style => (
              <Col xs={24} lg={8} key={style.market}>
                <Card
                  title={
                    <Space>
                      <PartitionOutlined />
                      <span>{marketLabel[style.market]}风格</span>
                    </Space>
                  }
                  extra={<Tag color={regimeMeta[style.risk_regime].color}>{style.risk_regime}</Tag>}
                >
                  <Space direction="vertical" size={10} style={{ width: '100%' }}>
                    <Text strong>{style.label}</Text>
                    <Progress percent={style.trend_score} size="small" />
                    <Space wrap>
                      {style.dominant_factors.map(factor => <Tag key={factor}>{factor}</Tag>)}
                    </Space>
                    <Paragraph type="secondary">{style.rationale}</Paragraph>
                    <Space wrap>
                      {style.sector_opinions.map(sector => (
                        <Tag key={sector.name} color={sector.stance === '强势' ? 'green' : sector.stance === '回避' ? 'red' : 'blue'}>
                          {sector.name} {sector.score}
                        </Tag>
                      ))}
                    </Space>
                  </Space>
                </Card>
              </Col>
            ))}
          </Row>

          <Card className="multi-market-actions" title={<Space><SafetyCertificateOutlined />组合动作</Space>}>
            <Space direction="vertical" size={8}>
              {result.portfolio_actions.map(action => <Text key={action}>{action}</Text>)}
            </Space>
          </Card>

          <Tabs
            className="multi-market-tabs"
            items={[
              {
                key: 'candidates',
                label: '候选与交易意见',
                children: (
                  <Table
                    rowKey={item => `${item.market}-${item.symbol}`}
                    columns={candidateColumns}
                    dataSource={result.candidates}
                    pagination={{ pageSize: 8 }}
                    scroll={{ x: 1100 }}
                  />
                )
              },
              {
                key: 'modules',
                label: '市场模块',
                children: (
                  <Table
                    rowKey="key"
                    columns={moduleColumns}
                    dataSource={result.modules}
                    pagination={false}
                    scroll={{ x: 1000 }}
                  />
                )
              },
              {
                key: 'backtest',
                label: '回测验证',
                children: (
                  <Table
                    rowKey="market"
                    columns={backtestColumns}
                    dataSource={result.backtest_plan}
                    pagination={false}
                    scroll={{ x: 1000 }}
                  />
                )
              },
              {
                key: 'dependencies',
                label: '中国可跑依赖',
                children: (
                  <Table
                    rowKey="name"
                    columns={dependencyColumns}
                    dataSource={result.dependencies}
                    pagination={false}
                    scroll={{ x: 1000 }}
                  />
                )
              }
            ]}
          />
        </>
      ) : (
        <Card>
          <Empty description="等待生成多市场决策快照" />
        </Card>
      )}
    </div>
  );
};

export default MultiMarketDecisionCenter;
