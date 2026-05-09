import React, { useState } from 'react';
import { 
  Card, 
  Tabs, 
  List, 
  Tag, 
  Typography, 
  Space, 
  Button, 
  Avatar, 
  Badge, 
  Divider,
  Row,
  Col,
  Statistic,
  Modal,
  Rate,
  Input,
  message
} from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  DollarOutlined,
  EyeOutlined,
  LikeOutlined,
  ShareAltOutlined,
  CrownOutlined,
  ArrowLeftOutlined,
  PlusOutlined,
  DatabaseOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { Stock, Post, Comment, ViewType } from '../types';
import StockRelatedData from './StockRelatedData';
import ResearchFlywheel from './ResearchFlywheel';
import { formatQuoteSourceLine, formatQuoteTimestamp, getQuoteDelayNote, getQuoteFreshnessLabel } from '../utils/marketData';

const { Title, Text, Paragraph } = Typography;
// const { TabPane } = Tabs; // 已弃用，使用items属性
const { TextArea } = Input;

interface StockCommunityProps {
  stock: Stock;
  posts: Post[];
  comments: Comment[];
  onBack: () => void;
  onCreatePost: (stock: Stock) => void;
  onPostClick: (post: Post) => void;
  onPurchase: (post: Post) => void;
  onRate: (post: Post, rating: number) => void;
  onLike: (post: Post) => void;
  onShare: (post: Post) => void;
  onViewChange?: (view: ViewType) => void;
}

const StockCommunity: React.FC<StockCommunityProps> = ({
  stock,
  posts,
  comments,
  onBack,
  onCreatePost,
  onPostClick,
  onPurchase,
  onRate,
  onLike,
  onShare,
  onViewChange
}) => {
  const [activeTab, setActiveTab] = useState('network');
  const [isCreateModalVisible, setIsCreateModalVisible] = useState(false);
  const [isPurchaseModalVisible, setIsPurchaseModalVisible] = useState(false);
  const [isRatingModalVisible, setIsRatingModalVisible] = useState(false);
  const [selectedPost, setSelectedPost] = useState<Post | null>(null);
  const [rating, setRating] = useState(5);
  const [feedback, setFeedback] = useState('');

  // 按当前标的过滤帖子，避免个股页混入其他股票内容。
  const stockPosts = posts.filter(p => p.stockSymbol === stock.symbol);
  const stockPostIds = new Set(stockPosts.map(post => post.id));
  const stockComments = comments.filter(comment => stockPostIds.has(comment.postId));
  const newsPosts = stockPosts.filter(p => p.type === 'news');
  const discussionPosts = stockPosts.filter(p => p.type === 'discussion');
  const analysisPosts = stockPosts.filter(p => p.type === 'analysis');
  const paidPosts = stockPosts.filter(p => p.isPaid);

  const handlePostClick = (post: Post) => {
    // 直接跳转到帖子详情页面，让PostDetail组件处理付费逻辑
    onPostClick(post);
  };

  const handlePurchase = () => {
    if (selectedPost) {
      onPurchase(selectedPost);
      setIsPurchaseModalVisible(false);
      message.success('购买成功！');
    }
  };

  const handleRate = () => {
    if (selectedPost) {
      onRate(selectedPost, rating);
      setIsRatingModalVisible(false);
      message.success('评分提交成功！');
    }
  };

  const getCategoryColor = (category: string) => {
    switch (category) {
      case 'technical': return 'blue';
      case 'fundamental': return 'green';
      case 'market': return 'orange';
      case 'earnings': return 'red';
      case 'product': return 'purple';
      case 'regulatory': return 'cyan';
      default: return 'default';
    }
  };

  const getCategoryText = (category: string) => {
    switch (category) {
      case 'technical': return '技术';
      case 'fundamental': return '基本面';
      case 'market': return '市场';
      case 'earnings': return '财报';
      case 'product': return '产品';
      case 'regulatory': return '监管';
      default: return '其他';
    }
  };

  const renderPostList = (postList: Post[]) => (
    <List
      dataSource={postList}
      renderItem={(post) => (
        <List.Item
          style={{ cursor: 'pointer' }}
          onClick={() => handlePostClick(post)}
          actions={[
            <Button type="link" icon={<EyeOutlined />}>
              {post.views}
            </Button>,
            <Button
              type="link"
              icon={<LikeOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                onLike(post);
              }}
            >
              {post.likes}
            </Button>,
            <Button type="link" icon={<MessageOutlined />}>
              {post.comments}
            </Button>,
            <Button 
              type="link" 
              icon={<ShareAltOutlined />}
              onClick={(e) => {
                e.stopPropagation();
                onShare(post);
                try {
                  navigator.clipboard.writeText(`https://deepfocus.com/post/${post.id}`).then(() => {
                    message.success('链接已复制到剪贴板');
                  }).catch(() => {
                    message.error('复制失败');
                  });
                } catch (error) {
                  console.error('复制链接失败:', error);
                  message.error('复制失败');
                }
              }}
            >
              {post.shares}
            </Button>
          ]}
        >
          <List.Item.Meta
            avatar={
              <Badge
                count={post.isPaid ? <DollarOutlined style={{ color: '#faad14' }} /> : 0}
                offset={[-5, 5]}
              >
                <Avatar 
                  src={post.author.avatar}
                  size={40}
                />
              </Badge>
            }
            title={
              <Space>
                <Text strong style={{ fontSize: '16px' }}>{post.title}</Text>
                {post.isPaid && <Tag color="gold"><DollarOutlined /> ${post.price}</Tag>}
                <Tag color={getCategoryColor(post.category)}>
                  {getCategoryText(post.category)}
                </Tag>
              </Space>
            }
            description={
              <div>
                <Space style={{ marginBottom: '8px' }}>
                  <Text strong>{post.author.username}</Text>
                  <Tag color={post.author.reputation > 80 ? 'gold' : post.author.reputation > 60 ? 'blue' : 'default'}>
                    {post.author.reputation}分
                  </Tag>
                  <Text type="secondary">
                    {new Date(post.publishTime).toLocaleString()}
                  </Text>
                </Space>
                <Paragraph ellipsis={{ tooltip: post.summary }} style={{ margin: '8px 0' }}>
                  {post.summary}
                </Paragraph>
                <div>
                  {post.tags.map(tag => (
                    <Tag key={tag}>{tag}</Tag>
                  ))}
                </div>
              </div>
            }
          />
        </List.Item>
      )}
    />
  );

  return (
    <div className="stock-community-shell">
      {/* 返回按钮和股票信息 */}
      <Card style={{ marginBottom: '16px' }}>
        <Space style={{ marginBottom: '16px' }} wrap>
          <Button onClick={onBack} icon={<ArrowLeftOutlined />} size="small">
            返回
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => onCreatePost(stock)} size="small">
            发布
          </Button>
        </Space>

        <Row gutter={[16, 16]}>
          <Col xs={24} sm={8}>
            <div style={{ textAlign: 'center' }}>
              <Avatar 
                size={64}
                style={{ 
                  backgroundColor: stock.focusLevel === 'high' ? '#ff4d4f' : 
                                 stock.focusLevel === 'medium' ? '#faad14' : '#52c41a',
                  fontSize: '24px',
                  fontWeight: 'bold',
                  marginBottom: '12px'
                }}
              >
                {stock.symbol}
              </Avatar>
              <Title level={3}>{stock.name}</Title>
              <Text type="secondary">{stock.sector}</Text>
              <div className="stock-quote-meta">
                <Tag color={stock.quoteIsRealtime ? 'green' : stock.quoteProvider === 'mock' ? 'default' : 'blue'}>
                  {getQuoteFreshnessLabel(stock)}
                </Tag>
                <Text type="secondary">{formatQuoteSourceLine(stock)} · {formatQuoteTimestamp(stock)}</Text>
              </div>
            </div>
          </Col>
          <Col xs={24} sm={8}>
            <Row gutter={[8, 8]}>
              <Col xs={12}>
                <Statistic 
                  title="当前价格" 
                  value={stock.currentPrice} 
                  prefix="$" 
                  precision={2}
                  valueStyle={{ color: stock.changePercent >= 0 ? '#52c41a' : '#ff4d4f', fontSize: '16px' }}
                />
              </Col>
              <Col xs={12}>
                <Statistic 
                  title="涨跌幅" 
                  value={Number(stock.changePercent.toFixed(2))} 
                  suffix="%" 
                  precision={2}
                  valueStyle={{ color: stock.changePercent >= 0 ? '#52c41a' : '#ff4d4f', fontSize: '16px' }}
                  prefix={stock.changePercent >= 0 ? '+' : ''}
                />
              </Col>
            </Row>
          </Col>
          <Col xs={24} sm={8}>
            <Row gutter={[8, 8]}>
              <Col xs={12}>
                <Statistic 
                  title="总帖子" 
                  value={stock.totalPosts} 
                  suffix="篇"
                  valueStyle={{ fontSize: '16px' }}
                />
              </Col>
              <Col xs={12}>
                <Statistic 
                  title="付费内容" 
                  value={stock.totalPaidPosts} 
                  suffix="篇"
                  valueStyle={{ color: '#faad14', fontSize: '16px' }}
                />
              </Col>
            </Row>
            <div style={{ marginTop: '8px' }}>
              <Text type="secondary" style={{ fontSize: '12px' }}>社区评分: {stock.communityScore}分</Text>
            </div>
            {getQuoteDelayNote(stock) && (
              <div className="stock-quote-delay">
                {getQuoteDelayNote(stock)}
              </div>
            )}
          </Col>
        </Row>
      </Card>

      {/* 内容标签页 */}
      <Card>
        <Tabs 
          activeKey={activeTab} 
          onChange={setActiveTab}
          items={[
            {
              key: 'network',
              label: (
                <span>
                  <ThunderboltOutlined />
                  投研网络
                </span>
              ),
              children: (
                <ResearchFlywheel
                  stock={stock}
                  posts={stockPosts}
                  comments={stockComments}
                  onViewChange={onViewChange}
                />
              )
            },
            {
              key: 'news',
              label: (
                <span>
                  <FireOutlined />
                  资讯区 ({newsPosts.length})
                </span>
              ),
              children: renderPostList(newsPosts)
            },
            {
              key: 'analysis',
              label: (
                <span>
                  <StarOutlined />
                  分析区 ({analysisPosts.length})
                </span>
              ),
              children: renderPostList(analysisPosts)
            },
            {
              key: 'discussion',
              label: (
                <span>
                  <MessageOutlined />
                  讨论区 ({discussionPosts.length})
                </span>
              ),
              children: renderPostList(discussionPosts)
            },
            {
              key: 'data',
              label: (
                <span>
                  <DatabaseOutlined />
                  关联数据
                </span>
              ),
              children: <StockRelatedData stock={stock} onViewChange={onViewChange} />
            },
            {
              key: 'paid',
              label: (
                <span>
                  <DollarOutlined />
                  付费区 ({paidPosts.length})
                </span>
              ),
              children: renderPostList(paidPosts)
            }
          ]}
        />
      </Card>

      {/* 购买确认模态框 */}
      <Modal
        title="确认购买"
        open={isPurchaseModalVisible}
        onCancel={() => setIsPurchaseModalVisible(false)}
        footer={[
          <Button key="cancel" onClick={() => setIsPurchaseModalVisible(false)}>
            取消
          </Button>,
          <Button key="purchase" type="primary" onClick={handlePurchase}>
            确认购买 ${selectedPost?.price}
          </Button>
        ]}
      >
        {selectedPost && (
          <div style={{ textAlign: 'center' }}>
            <DollarOutlined style={{ fontSize: '48px', color: '#faad14', marginBottom: '16px' }} />
            <Title level={4}>购买确认</Title>
            <Paragraph>
              您即将购买 <Text strong>{selectedPost.title}</Text>
            </Paragraph>
            <Paragraph>
              价格: <Text strong style={{ color: '#faad14', fontSize: '18px' }}>
                ${selectedPost.price}
              </Text>
            </Paragraph>
            <Paragraph type="secondary">
              购买后可以查看完整内容，并对内容进行评分
            </Paragraph>
          </div>
        )}
      </Modal>

      {/* 评分模态框 */}
      <Modal
        title="内容评分"
        open={isRatingModalVisible}
        onCancel={() => setIsRatingModalVisible(false)}
        footer={[
          <Button key="cancel" onClick={() => setIsRatingModalVisible(false)}>
            取消
          </Button>,
          <Button key="submit" type="primary" onClick={handleRate}>
            提交评分
          </Button>
        ]}
      >
        <div>
          <div style={{ textAlign: 'center', marginBottom: '16px' }}>
            <Text>请为这篇内容评分：</Text>
            <div style={{ marginTop: '8px' }}>
              <Rate value={rating} onChange={setRating} />
            </div>
          </div>
          <div>
            <Text>反馈意见（可选）</Text>
            <TextArea
              rows={4}
              placeholder="请分享您对这篇内容的看法和建议..."
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
              style={{ marginTop: '8px' }}
            />
          </div>
        </div>
      </Modal>
    </div>
  );
};

export default StockCommunity;
