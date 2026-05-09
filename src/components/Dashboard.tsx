import React from 'react';
import { Typography, Space, Tag } from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  DollarOutlined,
  RiseOutlined,
  UserOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { formatQuoteSourceLine, formatQuoteTimestamp } from '../utils/marketData';

const { Text } = Typography;

interface DashboardProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

const Dashboard: React.FC<DashboardProps> = ({ appState, onStockSelect }) => {
  const user = appState.user;
  const stocks = appState.stocks;
  const posts = appState.posts;

  // 计算统计数据
  const totalPosts = posts.length;
  const paidPosts = posts.filter(p => p.isPaid).length;
  const totalViews = posts.reduce((sum, p) => sum + p.views, 0);
  const totalLikes = posts.reduce((sum, p) => sum + p.likes, 0);
  const activeUsers = appState.rechargeHistory
    .filter(record => record.status === 'success')
    .map(record => record.userId)
    .filter((value, index, self) => self.indexOf(value) === index).length;

  const metricItems = [
    {
      label: '声誉评分',
      value: user?.reputation || 0,
      note: user?.memberLevel === 'vip' ? 'VIP 会员' : 'Premium 用户',
      icon: <StarOutlined />
    },
    {
      label: '发布内容',
      value: totalPosts,
      note: `${paidPosts} 篇付费研究`,
      icon: <MessageOutlined />
    },
    {
      label: '内容触达',
      value: totalViews.toLocaleString('zh-CN'),
      note: `${totalLikes.toLocaleString('zh-CN')} 次点赞`,
      icon: <RiseOutlined />
    },
    {
      label: '平台活跃',
      value: activeUsers,
      note: `平台余额 $${appState.platformBalance.toFixed(2)}`,
      icon: <UserOutlined />
    }
  ];

  return (
    <div className="dashboard-shell">
      <div className="dashboard-header">
        <div>
          <div className="dashboard-eyebrow">INVESTMENT WORKSPACE</div>
          <h2 className="dashboard-title">欢迎回来，{user?.username}</h2>
          <div className="dashboard-subtitle">聚合关注池、付费研究和社区信号，优先处理高价值标的。</div>
        </div>
        <Space size={8} wrap>
          <Tag color="blue">Premium</Tag>
          <Tag color="cyan">DeepFocus</Tag>
        </Space>
      </div>

      <div className="metric-grid">
        {metricItems.map(item => (
          <div className="metric-tile" key={item.label}>
            <div className="metric-label">
              {item.icon}
              <span>{item.label}</span>
            </div>
            <div className="metric-value">{item.value}</div>
            <div className="metric-note">{item.note}</div>
          </div>
        ))}
      </div>

      <div className="work-grid">
        <section className="terminal-panel">
          <div className="terminal-panel-header">
            <div className="terminal-panel-title">
              <FireOutlined />
              <span>热门关注池</span>
            </div>
            <Text type="secondary">按社区热度排序</Text>
          </div>
          <div className="terminal-table">
            {stocks.slice(0, 6).map(stock => (
              <div className="terminal-row" key={stock.symbol} onClick={() => onStockSelect(stock)}>
                <div>
                  <span className="instrument-symbol">{stock.symbol}</span>
                  <span className="instrument-name">{stock.name} · {stock.sector}</span>
                </div>
                <div>
                  <span className="instrument-symbol">${stock.currentPrice.toFixed(2)}</span>
                  <span className={`instrument-name ${stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
                    {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
                  </span>
                </div>
                <div className="instrument-meta">{formatQuoteSourceLine(stock)}</div>
                <div className="instrument-meta">{formatQuoteTimestamp(stock)}</div>
                <div className="instrument-meta">{stock.communityScore} 活跃度</div>
              </div>
            ))}
          </div>
        </section>

        <section className="terminal-panel">
          <div className="terminal-panel-header">
            <div className="terminal-panel-title">
              <MessageOutlined />
              <span>最新投研</span>
            </div>
            <Text type="secondary">{posts.length} 篇</Text>
          </div>
          <div className="content-list">
            {posts.slice(0, 5).map(post => (
              <article className="content-feed-item" key={post.id}>
                <div className="content-feed-title">{post.title}</div>
                <div className="content-feed-summary">{post.summary}</div>
                <div className="content-feed-meta">
                  <span><MessageOutlined /> {post.comments}</span>
                  <span><StarOutlined /> {post.likes}</span>
                  <span>{post.stockSymbol}</span>
                  {post.isPaid && <Tag color="gold"><DollarOutlined /> ${post.price}</Tag>}
                </div>
              </article>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
};

export default Dashboard;
