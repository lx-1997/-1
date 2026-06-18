import React from 'react';
import { Empty, Typography, Space, Tag } from 'antd';
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
import CollapsibleSection from './CollapsibleSection';
import CenterShell from './common/CenterShell';

const { Text } = Typography;

interface DashboardProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

const Dashboard: React.FC<DashboardProps> = ({ appState, onStockSelect }) => {
  const user = appState.user;
  const stocks = appState.stocks;
  const posts = appState.posts;

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
      label: '研究记录',
      value: totalPosts,
      note: `${paidPosts} 篇深度报告`,
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
    <CenterShell
      eyebrow="INVESTMENT WORKSPACE"
      title={`欢迎回来，${user?.username ?? ''}`}
      subtitle="聚合关注池、研究资产和证据信号，优先处理高价值标的。"
      actions={
        <Space size={8} wrap>
          <Tag color="blue">Premium</Tag>
          <Tag color="cyan">DeepFocus</Tag>
        </Space>
      }
    >
      <CollapsibleSection
        title={<><RiseOutlined /> 核心指标概览</>}
        extra={<Text type="secondary">{appState.stocks.length} 个标的 · {totalPosts} 篇研究</Text>}
        defaultOpen={true}
        level={1}
      >
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
      </CollapsibleSection>

      <div className="work-grid">
        <CollapsibleSection
          title={<><FireOutlined /> 热门关注池</>}
          extra={<Text type="secondary">按活跃度和行情质量排序</Text>}
          defaultOpen={true}
          level={2}
        >
          {stocks.length === 0 ? (
            <Empty description="暂无跟踪标的" />
          ) : (
            <div className="terminal-table">
              {stocks.slice(0, 8).map(stock => (
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
          )}
        </CollapsibleSection>

        <CollapsibleSection
          title={<><MessageOutlined /> 最新投研动态</>}
          extra={<Text type="secondary">{posts.length} 篇</Text>}
          defaultOpen={true}
          level={2}
        >
          {posts.length === 0 ? (
            <Empty description="暂无投研动态" />
          ) : (
            <div className="content-list">
              {posts.slice(0, 8).map(post => (
                <article className="content-feed-item" key={post.id}>
                  <div className="content-feed-title">{post.title}</div>
                  <div className="content-feed-summary">{post.summary}</div>
                  <div className="content-feed-meta">
                    <span><MessageOutlined /> {post.comments}</span>
                    <span><StarOutlined /> {post.likes}</span>
                    <span>{post.stockSymbol}</span>
                    {post.isPaid && <Tag color="gold"><DollarOutlined /> 深度报告 ${post.price}</Tag>}
                  </div>
                </article>
              ))}
            </div>
          )}
        </CollapsibleSection>
      </div>
    </CenterShell>
  );
};

const DashboardMemo = React.memo(Dashboard);
export default DashboardMemo;