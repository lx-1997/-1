import React from 'react';
import { Button, Card, Col, List, Progress, Row, Space, Tag, Typography } from 'antd';
import {
  CalendarOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  FireOutlined,
  GlobalOutlined,
  LineChartOutlined,
  SafetyCertificateOutlined,
  ShoppingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketDataService';
import Dashboard from './Dashboard';
import StockList from './StockList';
import Shop from './Shop';
import { formatQuoteSourceLine, formatQuoteTimestamp } from '../utils/marketData';

const { Paragraph, Text, Title } = Typography;

interface HomePageProps {
  appState: AppState;
  onStockSelect: (stock: any) => void;
  onProductClick: (product: any) => void;
  onAddToCart: (product: any, variantId: string, quantity: number) => void;
  onViewChange: (view: ViewType) => void;
  onAddStock: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock: (symbol: string) => void;
  onToggleStockSubscription: (symbol: string) => void;
  onRefreshMarketData: () => void;
  isMarketDataRefreshing: boolean;
}

const HomePage: React.FC<HomePageProps> = ({
  appState,
  onStockSelect,
  onProductClick,
  onAddToCart,
  onViewChange,
  onAddStock,
  onRemoveStock,
  onToggleStockSubscription,
  onRefreshMarketData,
  isMarketDataRefreshing
}) => {
  const strongestStock = [...appState.stocks].sort((a, b) => b.changePercent - a.changePercent)[0];
  const mostActiveStock = [...appState.stocks].sort((a, b) => b.communityScore - a.communityScore)[0];
  const quoteAnchor = appState.stocks.find(stock => stock.quoteProvider && stock.quoteProvider !== 'mock')
    || appState.stocks[0];
  const realQuoteCount = appState.stocks.filter(stock => stock.quoteProvider && stock.quoteProvider !== 'mock').length;
  const quoteCoverage = appState.stocks.length
    ? Math.round((realQuoteCount / appState.stocks.length) * 100)
    : 0;
  const evidenceCoverage = Math.min(
    100,
    Math.round((appState.posts.length / Math.max(appState.stocks.length * 2, 1)) * 100)
  );
  const focusStocks = [...appState.stocks]
    .sort((a, b) => {
      const scoreA = a.communityScore + Math.abs(a.changePercent) * 8 + a.totalPaidPosts * 4;
      const scoreB = b.communityScore + Math.abs(b.changePercent) * 8 + b.totalPaidPosts * 4;
      return scoreB - scoreA;
    })
    .slice(0, 5);
  type SignalTone = 'positive' | 'warning' | 'neutral';
  const signalQueue = [...appState.posts]
    .filter(post => post.status === 'published')
    .sort((a, b) => (
      Number(b.isPinned) - Number(a.isPinned)
      || Number(b.isHighlighted) - Number(a.isHighlighted)
      || b.qualityScore - a.qualityScore
      || new Date(b.publishTime).getTime() - new Date(a.publishTime).getTime()
    ))
    .slice(0, 4)
    .map(post => {
      const stock = appState.stocks.find(item => item.symbol === post.stockSymbol);
      const tone: SignalTone = post.category === 'regulatory'
        ? 'warning'
        : (post.qualityScore >= 85 || post.isHighlighted ? 'positive' : 'neutral');

      return {
        post,
        stock,
        tone,
        evidenceCount: Math.min(6, Math.max(2, post.tags.length + (post.isPaid ? 1 : 0))),
        action: post.category === 'earnings'
          ? '复核财报口径'
          : post.category === 'technical'
            ? '验证价格与技术信号'
            : '补充新闻/研报证据'
      };
    });
  const primarySignal = signalQueue[0];
  const sourceHealth: Array<{
    label: string;
    value: string;
    detail: string;
    percent: number;
    progressStatus: 'normal' | 'success' | 'active' | 'exception';
  }> = [
    {
      label: '行情',
      value: realQuoteCount > 0 ? `${realQuoteCount}/${appState.stocks.length} 已刷新` : '样例模式',
      detail: realQuoteCount > 0 ? '已有外部行情快照进入关注池' : '需要继续接入稳定行情源',
      percent: quoteCoverage,
      progressStatus: realQuoteCount > 0 ? 'success' : 'normal'
    },
    {
      label: '新闻/研报',
      value: appState.posts.length > 0 ? `${appState.posts.length} 条样例` : '待接入',
      detail: '目标是接 Reuters、Bloomberg、外资研报与自有资料',
      percent: Math.min(72, appState.posts.length * 14),
      progressStatus: 'active'
    },
    {
      label: '证据链',
      value: `${evidenceCoverage}% 覆盖`,
      detail: '每条 AI 结论都应能回跳到原文、行情和图表',
      percent: evidenceCoverage,
      progressStatus: evidenceCoverage >= 75 ? 'success' : 'normal'
    },
    {
      label: '新手解释层',
      value: '需产品化',
      detail: '把术语翻译成影响路径、风险点和下一步动作',
      percent: 46,
      progressStatus: 'normal'
    }
  ];
  const macroPulses = [
    {
      label: '美元流动性',
      value: '待接入',
      note: '美债利率、美元指数、资金流会影响成长股估值',
      tone: 'pending'
    },
    {
      label: 'AI 算力链',
      value: mostActiveStock?.symbol || 'NVDA',
      note: '把产业链新闻映射到 GPU、云厂商和电力侧',
      tone: 'watch'
    },
    {
      label: '财报窗口',
      value: '事件日历',
      note: '财报前后应自动聚合预期差、电话会和指引变化',
      tone: 'neutral'
    },
    {
      label: '监管/政策',
      value: '待补源',
      note: '宏观政策、出口管制与反垄断需要独立风险轨道',
      tone: 'pending'
    }
  ];
  const beginnerSteps = [
    {
      title: '先读主线',
      desc: '用一句话回答“今天为什么要看这些标的”。',
      key: 'realtime-messages' as ViewType
    },
    {
      title: '再看证据',
      desc: '把新闻、研报、公告和行情放进同一个证据链。',
      key: 'data-sources' as ViewType
    },
    {
      title: '最后让 AI 复核',
      desc: '输出结论、分歧、风险和需要继续验证的问题。',
      key: 'ai-research' as ViewType
    }
  ];
  const toneMeta = {
    positive: { color: 'green', label: '高置信' },
    warning: { color: 'gold', label: '需复核' },
    neutral: { color: 'blue', label: '观察' }
  } as const;

  const renderMarketStrip = () => (
    <div className="market-strip">
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>覆盖个股</span>
          <span>Universe</span>
        </div>
        <div className="market-strip-value">{appState.stocks.length}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>领涨标的</span>
          <span>{strongestStock?.symbol || '--'}</span>
        </div>
        <div className={`market-strip-value ${strongestStock && strongestStock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
          {strongestStock ? `${strongestStock.changePercent >= 0 ? '+' : ''}${strongestStock.changePercent.toFixed(2)}%` : '--'}
        </div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>最高热度</span>
          <span>{mostActiveStock?.symbol || '--'}</span>
        </div>
        <div className="market-strip-value">{mostActiveStock ? `${mostActiveStock.communityScore} 分` : '--'}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>信息条目</span>
          <span>Intelligence</span>
        </div>
        <div className="market-strip-value">{appState.posts.length}</div>
      </div>
      <div className="market-strip-item">
        <div className="market-strip-label">
          <span>行情源</span>
          <span>{quoteAnchor?.symbol || '--'}</span>
        </div>
        <div className="market-strip-value market-strip-value-sm">
          {quoteAnchor ? formatQuoteSourceLine(quoteAnchor) : '--'}
        </div>
        <div className="market-strip-note">{quoteAnchor ? formatQuoteTimestamp(quoteAnchor) : '待刷新'}</div>
      </div>
    </div>
  );

  if (appState.currentView === 'stocks') {
    return (
      <div className="home-shell">
        {renderMarketStrip()}
        <div className="page-heading-band">
          <Space direction="vertical" size={4}>
            <Text className="dashboard-eyebrow">STOCK RESEARCH</Text>
            <Title level={3} style={{ margin: 0 }}>个股研究池</Title>
            <Text type="secondary">沉淀关注标的、热度变化、社区研究和后续一键检测入口。</Text>
          </Space>
          <Button type="primary" icon={<ExperimentOutlined />} onClick={() => onViewChange('ai-research')}>
            一键检测
          </Button>
        </div>
        <StockList
          stocks={appState.stocks}
          onStockSelect={onStockSelect}
          onAddStock={onAddStock}
          onRemoveStock={onRemoveStock}
          onToggleSubscription={onToggleStockSubscription}
          onRefreshMarketData={onRefreshMarketData}
          isMarketDataRefreshing={isMarketDataRefreshing}
          showHeader={false}
        />
      </div>
    );
  }

  if (appState.currentView === 'shop') {
    return (
      <div className="home-shell">
        {renderMarketStrip()}
        <div className="page-heading-band">
          <Space direction="vertical" size={4}>
            <Text className="dashboard-eyebrow">RESEARCH MARKETPLACE</Text>
            <Title level={3} style={{ margin: 0 }}>研究订阅与模板库</Title>
            <Text type="secondary">把高质量研报、策略模板、数据包和专家服务包装成可复用投研资产。</Text>
          </Space>
          <Button icon={<ShoppingOutlined />} onClick={() => onViewChange('orders')}>
            查看订阅
          </Button>
        </div>
        <Shop
          products={appState.products}
          onProductClick={onProductClick}
          onAddToCart={onAddToCart}
          showHeader={false}
        />
      </div>
    );
  }

  const workbenchActions = [
    {
      key: 'stocks' as ViewType,
      title: '看今日异动',
      desc: '先从涨跌幅、热度和资料密度定位需要解释的标的。',
      icon: <LineChartOutlined />
    },
    {
      key: 'data-sources' as ViewType,
      title: '补充证据源',
      desc: '接入研报、新闻、公告、上传文件和自定义资料库。',
      icon: <DatabaseOutlined />
    },
    {
      key: 'ai-research' as ViewType,
      title: '生成可复核结论',
      desc: '让 AI 给出证据、分歧、风险和下一步验证动作。',
      icon: <ThunderboltOutlined />
    },
    {
      key: 'earnings-calendar' as ViewType,
      title: '排程宏观与财报',
      desc: '把财报、政策和宏观变量放进同一个事件日历。',
      icon: <CalendarOutlined />
    }
  ];

  return (
    <div className="home-shell">
      {renderMarketStrip()}

      <div className="research-workbench-hero">
        <div>
          <Text className="dashboard-eyebrow">AI-NATIVE INVESTMENT DESK</Text>
          <Title level={2} style={{ margin: '4px 0 8px' }}>信息驱动投研工作台</Title>
          <Paragraph type="secondary" style={{ margin: 0, maxWidth: 720 }}>
            先把行情、新闻、研报、财报和宏观变量压成可追溯信号，再让 AI 解释“发生了什么、影响谁、证据在哪、下一步查什么”。
          </Paragraph>
        </div>
        <div className="hero-intelligence-grid">
          <div>
            <span>今日主线</span>
            <strong>{primarySignal?.post.title || '等待新闻/研报接入'}</strong>
          </div>
          <div>
            <span>证据状态</span>
            <strong>{realQuoteCount > 0 ? '行情已刷新' : '样例数据'}</strong>
          </div>
          <div>
            <span>自定义</span>
            <strong>关注池 / 数据源 / Skills</strong>
          </div>
        </div>
      </div>

      <div className="workbench-action-grid">
        {workbenchActions.map(action => (
          <button
            key={action.key}
            type="button"
            className="workbench-action-card"
            onClick={() => onViewChange(action.key)}
          >
            <span className="workbench-action-icon">{action.icon}</span>
            <span>
              <strong>{action.title}</strong>
              <small>{action.desc}</small>
            </span>
          </button>
        ))}
      </div>

      <Row gutter={[16, 16]} className="workbench-panel-grid">
        <Col xs={24} xl={10}>
          <Card
            title={<Space><FireOutlined />高优先级关注池</Space>}
            extra={<Button type="link" onClick={() => onViewChange('stocks')}>进入个股池</Button>}
            className="workbench-panel-card"
          >
            <List
              dataSource={focusStocks}
              renderItem={stock => (
                <List.Item className="stock-priority-row" onClick={() => onStockSelect(stock)}>
                  <List.Item.Meta
                    title={<Space><Text strong>{stock.symbol}</Text><Text>{stock.name}</Text></Space>}
                    description={(
                      <Space size={6} wrap>
                        <Text type="secondary">{stock.sector}</Text>
                        <Tag>资料 {stock.totalPosts}</Tag>
                        <Tag color={stock.quoteProvider && stock.quoteProvider !== 'mock' ? 'green' : 'default'}>
                          {stock.quoteProvider && stock.quoteProvider !== 'mock' ? '外部行情' : '样例行情'}
                        </Tag>
                      </Space>
                    )}
                  />
                  <Space direction="vertical" align="end" size={2}>
                    <Text className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'} strong>
                      {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
                    </Text>
                    <Text type="secondary">{stock.communityScore} 热度</Text>
                  </Space>
                </List.Item>
              )}
            />
          </Card>
        </Col>

        <Col xs={24} xl={7}>
          <Card
            title={<Space><FileSearchOutlined />今日信息队列</Space>}
            extra={<Button type="link" onClick={() => onViewChange('realtime-messages')}>消息流</Button>}
            className="workbench-panel-card"
          >
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {signalQueue.map(signal => (
                <button
                  key={signal.post.id}
                  type="button"
                  className="signal-queue-item"
                  onClick={() => signal.stock ? onStockSelect(signal.stock) : onViewChange('data-sources')}
                >
                  <span className="signal-queue-head">
                    <Tag color={toneMeta[signal.tone].color}>{toneMeta[signal.tone].label}</Tag>
                    <Text strong>{signal.post.stockSymbol}</Text>
                    <Text type="secondary">{signal.evidenceCount} 条证据</Text>
                  </span>
                  <strong>{signal.post.title}</strong>
                  <small>{signal.action}</small>
                </button>
              ))}
              {signalQueue.length === 0 && (
                <Paragraph type="secondary" style={{ margin: 0 }}>
                  暂无信息队列。接入新闻、研报或上传资料后，这里会自动变成“今天先看什么”的工作台。
                </Paragraph>
              )}
            </Space>
          </Card>
        </Col>

        <Col xs={24} xl={7}>
          <Card
            title={<Space><SafetyCertificateOutlined />证据与 AI 健康度</Space>}
            className="workbench-panel-card"
          >
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {sourceHealth.map(item => (
                <div className="source-health-row" key={item.label}>
                  <div className="source-health-head">
                    <Text strong>{item.label}</Text>
                    <Text type="secondary">{item.value}</Text>
                  </div>
                  <Progress percent={item.percent} size="small" status={item.progressStatus} />
                  <Text type="secondary">{item.detail}</Text>
                </div>
              ))}
              <Button block icon={<ExperimentOutlined />} onClick={() => onViewChange('ai-research')}>
                让 AI 做一次可复核体检
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      <Row gutter={[16, 16]} className="workbench-panel-grid">
        <Col xs={24} xl={14}>
          <Card
            title={<Space><GlobalOutlined />宏观与行业脉冲</Space>}
            extra={<Button type="link" onClick={() => onViewChange('earnings-calendar')}>事件日历</Button>}
            className="workbench-panel-card"
          >
            <div className="macro-pulse-grid">
              {macroPulses.map(pulse => (
                <button
                  key={pulse.label}
                  type="button"
                  className={`macro-pulse-card ${pulse.tone}`}
                  onClick={() => onViewChange(pulse.tone === 'neutral' ? 'earnings-calendar' : 'data-sources')}
                >
                  <span>{pulse.label}</span>
                  <strong>{pulse.value}</strong>
                  <small>{pulse.note}</small>
                </button>
              ))}
            </div>
          </Card>
        </Col>

        <Col xs={24} xl={10}>
          <Card
            title={<Space><CheckCircleOutlined />小白可用的研究路径</Space>}
            extra={<Button type="link" onClick={() => onViewChange('skills')}>自定义</Button>}
            className="workbench-panel-card"
          >
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {beginnerSteps.map((step, index) => (
                <button
                  key={step.title}
                  type="button"
                  className="beginner-step-item"
                  onClick={() => onViewChange(step.key)}
                >
                  <span>{index + 1}</span>
                  <strong>{step.title}</strong>
                  <small>{step.desc}</small>
                </button>
              ))}
            </Space>
          </Card>
        </Col>
      </Row>

      <div className="home-section-title">
        <Space><LineChartOutlined />行情与社区概览</Space>
      </div>
      <Dashboard appState={appState} onStockSelect={onStockSelect} />
    </div>
  );
};

export default HomePage;
