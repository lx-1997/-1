import React from 'react';
import { Button, Card, Col, List, Progress, Row, Space, Statistic, Tag, Typography } from 'antd';
import {
  CalendarOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FireOutlined,
  LineChartOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ShoppingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState, ViewType } from '../types';
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
}

const HomePage: React.FC<HomePageProps> = ({
  appState,
  onStockSelect,
  onProductClick,
  onAddToCart,
  onViewChange
}) => {
  const strongestStock = [...appState.stocks].sort((a, b) => b.changePercent - a.changePercent)[0];
  const mostActiveStock = [...appState.stocks].sort((a, b) => b.communityScore - a.communityScore)[0];
  const quoteAnchor = appState.stocks.find(stock => stock.quoteProvider && stock.quoteProvider !== 'mock')
    || appState.stocks[0];
  const paidPostCount = appState.posts.filter(post => post.isPaid).length;
  const premiumRevenue = appState.posts.reduce((sum, post) => sum + post.totalRevenue, 0);
  const focusStocks = [...appState.stocks]
    .sort((a, b) => {
      const scoreA = a.communityScore + Math.abs(a.changePercent) * 8 + a.totalPaidPosts * 4;
      const scoreB = b.communityScore + Math.abs(b.changePercent) * 8 + b.totalPaidPosts * 4;
      return scoreB - scoreA;
    })
    .slice(0, 5);

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
          <span>付费研究</span>
          <span>Premium</span>
        </div>
        <div className="market-strip-value">{paidPostCount}</div>
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
        <StockList stocks={appState.stocks} onStockSelect={onStockSelect} showHeader={false} />
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
            <Title level={3} style={{ margin: 0 }}>研究商城</Title>
            <Text type="secondary">把高质量研究、策略模板和专家服务包装成可购买的投研产品。</Text>
          </Space>
          <Button icon={<ShoppingOutlined />} onClick={() => onViewChange('orders')}>
            查看订单
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
      key: 'ai-research' as ViewType,
      title: '一键检测个股',
      desc: '串联 FinGPT、情绪、新闻、RAG、预测和 Agent 摘要。',
      icon: <ThunderboltOutlined />
    },
    {
      key: 'data-sources' as ViewType,
      title: '数据中心',
      desc: '管理上传文件、远端资料、公众号/雪球抓取和标签。',
      icon: <DatabaseOutlined />
    },
    {
      key: 'agent-center' as ViewType,
      title: '多 Agent 投研',
      desc: '让研究、情绪、情景、风险、报告 Agent 分工执行。',
      icon: <RobotOutlined />
    },
    {
      key: 'earnings-calendar' as ViewType,
      title: '财报日历',
      desc: '跟踪披露节奏、重点事件和复盘材料。',
      icon: <CalendarOutlined />
    }
  ];

  return (
    <div className="home-shell">
      {renderMarketStrip()}

      <div className="research-workbench-hero">
        <div>
          <Text className="dashboard-eyebrow">AI INVESTMENT PLATFORM</Text>
          <Title level={2} style={{ margin: '4px 0 8px' }}>专业投研 AI 工作台</Title>
          <Paragraph type="secondary" style={{ margin: 0, maxWidth: 720 }}>
            从数据接入、资料管理、个股检测到多 Agent 报告生成，所有能力围绕“投资者能看懂、能复核、能行动”的研究闭环组织。
          </Paragraph>
        </div>
        <Space wrap>
          <Tag color="green">模型可配置</Tag>
          <Tag color="blue">文件 + 文本输入</Tag>
          <Tag color="gold">Agent 队列</Tag>
        </Space>
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
                    description={`${stock.sector} · ${stock.totalPosts} 篇资料 · ${stock.totalPaidPosts} 篇付费研究`}
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
            title={<Space><LineChartOutlined />投研产能</Space>}
            className="workbench-panel-card"
          >
            <div className="platform-kpi-row">
              <Statistic title="内容资料" value={appState.posts.length} suffix="篇" />
              <Statistic title="付费转化" value={paidPostCount} suffix="篇" />
            </div>
            <Progress percent={Math.min(100, Math.round((paidPostCount / Math.max(appState.posts.length, 1)) * 100))} />
            <Paragraph type="secondary" style={{ marginTop: 12 }}>
              当前平台已沉淀 ${premiumRevenue.toFixed(2)} 研究收入，后续应把数据源、AI 解读和一键检测结果沉淀为可复核资产。
            </Paragraph>
          </Card>
        </Col>

        <Col xs={24} xl={7}>
          <Card
            title={<Space><SafetyCertificateOutlined />风控复核</Space>}
            className="workbench-panel-card"
          >
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <div className="workbench-check-line">
                <Text strong>证据链</Text>
                <Tag color="blue">数据中心</Tag>
              </div>
              <div className="workbench-check-line">
                <Text strong>模型口径</Text>
                <Tag color="green">可配置</Tag>
              </div>
              <div className="workbench-check-line">
                <Text strong>结论复核</Text>
                <Tag color="gold">Agent 报告</Tag>
              </div>
              <Button block icon={<ExperimentOutlined />} onClick={() => onViewChange('ai-research')}>
                开始一键检测
              </Button>
            </Space>
          </Card>
        </Col>
      </Row>

      <div className="home-section-title">
        <Space><LineChartOutlined />投研概览</Space>
      </div>
      <Dashboard appState={appState} onStockSelect={onStockSelect} />
    </div>
  );
};

export default HomePage;
