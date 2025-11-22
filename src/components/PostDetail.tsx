import React, { useState } from 'react';
import { 
  Card, 
  Typography, 
  Space, 
  Button, 
  Avatar, 
  Tag, 
  Divider, 
  Row, 
  Col, 
  Statistic,
  Modal,
  Rate,
  Input,
  message,
  Badge,
  Form,
  List
} from 'antd';
import { 
  FireOutlined, 
  StarOutlined, 
  MessageOutlined, 
  EyeOutlined,
  DollarOutlined,
  LikeOutlined,
  ShareAltOutlined,
  CrownOutlined,
  ArrowLeftOutlined,
  SendOutlined
} from '@ant-design/icons';
import ShareModal from './ShareModal';
import { Post, User, Comment as CommentType } from '../types';

const { Title, Text, Paragraph } = Typography;
const { TextArea } = Input;

interface PostDetailProps {
  post: Post;
  currentUser: User;
  comments: CommentType[];
  purchasedPosts: string[]; // 用户已购买的帖子ID列表
  onBack: () => void;
  onPurchase: (postId: string, amount: number) => void;
  onRate: (postId: string, rating: number, feedback: string) => void;
  onLike: (postId: string) => void;
  onShare: (postId: string) => void;
  onAddComment?: (postId: string, content: string) => void;
}

const PostDetail: React.FC<PostDetailProps> = ({ 
  post, 
  currentUser, 
  comments,
  purchasedPosts,
  onBack, 
  onPurchase, 
  onRate, 
  onLike,
  onShare,
  onAddComment
}) => {
  // 调试信息
  console.log('PostDetail组件渲染，帖子ID:', post.id);
  console.log('onLike函数:', typeof onLike);
  console.log('onShare函数:', typeof onShare);
  const [isPurchaseModalVisible, setIsPurchaseModalVisible] = useState(false);
  const [isRatingModalVisible, setIsRatingModalVisible] = useState(false);
  const [isShareModalVisible, setIsShareModalVisible] = useState(false);
  const [rating, setRating] = useState(5);
  const [feedback, setFeedback] = useState('');
  const [newComment, setNewComment] = useState('');

  // 检查用户是否已购买此帖子
  const hasPurchased = purchasedPosts.includes(post.id);

  // 滚动到评论区
  const scrollToComments = () => {
    const commentsElement = document.getElementById('comments-section');
    if (commentsElement) {
      commentsElement.scrollIntoView({ behavior: 'smooth' });
    }
  };

  const getPostTypeIcon = (type: string) => {
    switch (type) {
      case 'news': return <FireOutlined style={{ color: '#ff4d4f' }} />;
      case 'analysis': return <StarOutlined style={{ color: '#faad14' }} />;
      case 'discussion': return <MessageOutlined style={{ color: '#1890ff' }} />;
      case 'qa': return <CrownOutlined style={{ color: '#722ed1' }} />;
      default: return <StarOutlined />;
    }
  };

  const getPostTypeText = (type: string) => {
    switch (type) {
      case 'news': return '资讯';
      case 'analysis': return '分析';
      case 'discussion': return '讨论';
      case 'qa': return '问答';
      default: return '其他';
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

  const handlePurchase = () => {
    onPurchase(post.id, post.price);
    setIsPurchaseModalVisible(false);
  };

  const handleRate = () => {
    onRate(post.id, rating, feedback);
    setIsRatingModalVisible(false);
    message.success('评分提交成功！感谢您的反馈。');
  };

  const handleAddComment = () => {
    if (newComment.trim()) {
      onAddComment?.(post.id, newComment);
      setNewComment('');
      message.success('评论发布成功！');
    }
  };

  // 获取当前帖子的评论
  const postComments = comments.filter(comment => comment.postId === post.id);

  const renderContent = () => {
    if (post.isPaid && !hasPurchased) {
      return (
        <Card style={{ textAlign: 'center', padding: '40px' }}>
          <DollarOutlined style={{ fontSize: '64px', color: '#faad14', marginBottom: '16px' }} />
          <Title level={3}>付费内容</Title>
          <Paragraph>
            这是一篇付费内容，需要支付 <Text strong style={{ color: '#faad14', fontSize: '18px' }}>
              ${post.price}
            </Text> 才能查看完整内容
          </Paragraph>
          <Paragraph type="secondary">
            付费查看后，您可以对内容进行评分，帮助作者提升内容质量
          </Paragraph>
          <Button 
            type="primary" 
            size="large" 
            icon={<DollarOutlined />}
            onClick={() => setIsPurchaseModalVisible(true)}
          >
            立即购买
          </Button>
        </Card>
      );
    }

    return (
      <div>
        <Paragraph style={{ fontSize: '16px', lineHeight: 1.8 }}>
          {post.content}
        </Paragraph>
        
        {post.isPaid && hasPurchased && (
          <Card style={{ marginTop: 16, background: '#f6ffed', borderColor: '#b7eb8f' }}>
            <Space>
              <DollarOutlined style={{ color: '#52c41a' }} />
              <Text strong>您已购买此内容</Text>
            </Space>
            <div style={{ marginTop: 8 }}>
              <Text type="secondary">如果觉得内容有价值，请给作者评分：</Text>
              <Button 
                type="link" 
                icon={<StarOutlined />}
                onClick={() => setIsRatingModalVisible(true)}
              >
                立即评分
              </Button>
            </div>
          </Card>
        )}
      </div>
    );
  };

  return (
    <div>
      {/* 返回按钮和基本信息 */}
      <Card style={{ marginBottom: 16 }}>
        <Space style={{ marginBottom: 16 }}>
          <Button onClick={onBack} icon={<ArrowLeftOutlined />}>
            返回
          </Button>
        </Space>

        <div style={{ marginBottom: 16 }}>
          <Space>
            <Badge
              count={post.isPaid ? <DollarOutlined style={{ color: '#faad14' }} /> : 0}
              offset={[-5, 5]}
            >
              <Avatar 
                src={post.author.avatar}
                size={48}
              />
            </Badge>
            <div>
              <Text strong style={{ fontSize: '16px' }}>{post.author.username}</Text>
              <br />
              <Tag color={post.author.reputation > 80 ? 'gold' : post.author.reputation > 60 ? 'blue' : 'default'}>
                {post.author.reputation}分
              </Tag>
              <Text type="secondary" style={{ marginLeft: 8 }}>
                {new Date(post.publishTime).toLocaleString()}
              </Text>
            </div>
          </Space>
        </div>

        <Title level={2}>
          {post.title}
          {post.isPaid && <Tag color="gold" style={{ marginLeft: 8 }}>付费内容</Tag>}
          {post.isPinned && <Tag color="red" style={{ marginLeft: 8 }}>置顶</Tag>}
          {post.isHighlighted && <Tag color="blue" style={{ marginLeft: 8 }}>精华</Tag>}
        </Title>

        <Space wrap style={{ marginBottom: 16 }}>
          <Tag color={getCategoryColor(post.category)}>
            {getCategoryText(post.category)}
          </Tag>
          <Tag color="blue">
            {getPostTypeIcon(post.type)}
            {getPostTypeText(post.type)}
          </Tag>
          {post.tags.map(tag => (
            <Tag key={tag}>{tag}</Tag>
          ))}
        </Space>

        <Paragraph style={{ fontSize: '16px', color: '#666' }}>
          {post.summary}
        </Paragraph>

        <Row gutter={16} style={{ marginTop: 16 }}>
          <Col span={6}>
            <div style={{ textAlign: 'center', padding: '16px', border: '1px solid #d9d9d9', borderRadius: '6px' }}>
              <EyeOutlined style={{ fontSize: '24px', color: '#666', marginBottom: '8px' }} />
              <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#666' }}>{post.views}</div>
              <div style={{ fontSize: '14px', color: '#999' }}>浏览量</div>
            </div>
          </Col>
          <Col span={6}>
            <Button
              type="text"
              style={{ 
                width: '100%',
                height: '80px',
                border: '2px solid #52c41a',
                backgroundColor: '#f6ffed',
                borderRadius: '6px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '16px'
              }}
              onClick={() => {
                console.log('=== 点赞统计被点击 ===');
                console.log('帖子ID:', post.id);
                console.log('onLike函数类型:', typeof onLike);
                console.log('onLike函数:', onLike);
                try {
                  onLike(post.id);
                  console.log('onLike调用成功');
                } catch (error) {
                  console.error('onLike调用失败:', error);
                }
              }}
            >
              <LikeOutlined style={{ fontSize: '24px', color: '#52c41a', marginBottom: '8px' }} />
              <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#52c41a' }}>{post.likes}</div>
              <div style={{ fontSize: '14px', color: '#52c41a' }}>点赞</div>
            </Button>
          </Col>
          <Col span={6}>
            <Button
              type="text"
              style={{ 
                width: '100%',
                height: '80px',
                border: '2px solid #1890ff',
                backgroundColor: '#f0f9ff',
                borderRadius: '6px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '16px'
              }}
              onClick={scrollToComments}
            >
              <MessageOutlined style={{ fontSize: '24px', color: '#1890ff', marginBottom: '8px' }} />
              <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#1890ff' }}>{post.comments}</div>
              <div style={{ fontSize: '14px', color: '#1890ff' }}>评论</div>
            </Button>
          </Col>
          <Col span={6}>
            <Button
              type="text"
              style={{ 
                width: '100%',
                height: '80px',
                border: '2px solid #faad14',
                backgroundColor: '#fffbe6',
                borderRadius: '6px',
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                padding: '16px'
              }}
              onClick={() => {
                console.log('=== 分享统计被点击 ===');
                console.log('帖子ID:', post.id);
                console.log('onShare函数类型:', typeof onShare);
                console.log('onShare函数:', onShare);
                try {
                  onShare(post.id);
                  console.log('onShare调用成功');
                  setIsShareModalVisible(true);
                  console.log('分享模态框已打开');
                } catch (error) {
                  console.error('onShare调用失败:', error);
                }
              }}
            >
              <ShareAltOutlined style={{ fontSize: '24px', color: '#faad14', marginBottom: '8px' }} />
              <div style={{ fontSize: '18px', fontWeight: 'bold', color: '#faad14' }}>{post.shares}</div>
              <div style={{ fontSize: '14px', color: '#faad14' }}>分享</div>
            </Button>
          </Col>
        </Row>

        {/* 操作按钮 */}
        <div style={{ marginTop: 16, textAlign: 'center' }}>
          <Space size="middle" wrap>
            <Button 
              type="primary" 
              icon={<LikeOutlined />}
              onClick={() => {
                console.log('点赞按钮被点击，帖子ID:', post.id);
                onLike(post.id);
              }}
              style={{ 
                cursor: 'pointer',
                pointerEvents: 'auto',
                zIndex: 1000
              }}
            >
              点赞 ({post.likes})
            </Button>
            <Button 
              icon={<ShareAltOutlined />}
              onClick={() => {
                console.log('分享按钮被点击，帖子ID:', post.id);
                onShare(post.id);
                setIsShareModalVisible(true);
              }}
              style={{ 
                cursor: 'pointer',
                pointerEvents: 'auto',
                zIndex: 1000
              }}
            >
              分享 ({post.shares})
            </Button>
            <Button 
              icon={<MessageOutlined />}
              onClick={scrollToComments}
              style={{ 
                cursor: 'pointer',
                pointerEvents: 'auto',
                zIndex: 1000
              }}
            >
              评论 ({post.comments})
            </Button>
            {post.isPaid && hasPurchased && (
              <Button 
                icon={<StarOutlined />}
                onClick={() => setIsRatingModalVisible(true)}
              >
                评分
              </Button>
            )}
          </Space>
        </div>

        {post.isPaid && (
          <Row gutter={16} style={{ marginTop: 16 }}>
            <Col span={8}>
              <Statistic 
                title="价格" 
                value={post.price} 
                prefix="$" 
                valueStyle={{ color: '#faad14' }}
              />
            </Col>
            <Col span={8}>
              <Statistic 
                title="付费人数" 
                value={post.paidViewers} 
                prefix={<DollarOutlined />}
              />
            </Col>
            <Col span={8}>
              <Statistic 
                title="总收入" 
                value={post.totalRevenue} 
                prefix="$"
                valueStyle={{ color: '#52c41a' }}
              />
            </Col>
          </Row>
        )}

        {post.qualityScore > 0 && (
          <div style={{ marginTop: 16 }}>
            <Text type="secondary">内容质量评分: </Text>
            <Rate disabled defaultValue={post.qualityScore / 20} />
            <Text type="secondary" style={{ marginLeft: 8 }}>
              {post.qualityScore.toFixed(1)}分 ({post.totalRatings}人评分)
            </Text>
          </div>
        )}
      </Card>

      {/* 内容区域 */}
      <Card title="内容详情">
        {renderContent()}
      </Card>

      {/* 评论区域 */}
      <Card 
        id="comments-section"
        title={`评论 (${postComments.length})`} 
        style={{ marginTop: 16 }}
      >
        {/* 发表评论 */}
        <div style={{ marginBottom: 24 }}>
          <Space.Compact style={{ width: '100%' }}>
            <TextArea
              rows={3}
              placeholder="写下您的评论..."
              value={newComment}
              onChange={(e) => setNewComment(e.target.value)}
              style={{ resize: 'none' }}
            />
            <Button 
              type="primary" 
              icon={<SendOutlined />}
              onClick={handleAddComment}
              disabled={!newComment.trim()}
            >
              发布
            </Button>
          </Space.Compact>
        </div>

        {/* 评论列表 */}
        {postComments.length > 0 ? (
          <List
            dataSource={postComments}
            renderItem={(comment) => (
              <List.Item>
                <Card size="small" style={{ width: '100%' }}>
                  <Space align="start" style={{ width: '100%' }}>
                    <Avatar src={comment.author.avatar} />
                    <div style={{ flex: 1 }}>
                      <Space style={{ marginBottom: 8 }}>
                        <Text strong>{comment.author.username}</Text>
                        <Tag color={comment.author.reputation > 80 ? 'gold' : comment.author.reputation > 60 ? 'blue' : 'default'}>
                          {comment.author.reputation}分
                        </Tag>
                        {comment.isPaid && <Tag color="gold">付费评论</Tag>}
                      </Space>
                      <Paragraph style={{ margin: '8px 0' }}>
                        {comment.content}
                      </Paragraph>
                      <Space>
                        <Text type="secondary" style={{ fontSize: '12px' }}>
                          {new Date(comment.publishTime).toLocaleString()}
                        </Text>
                        <Button type="link" size="small" icon={<LikeOutlined />}>
                          {comment.likes}
                        </Button>
                      </Space>
                    </div>
                  </Space>
                </Card>
              </List.Item>
            )}
          />
        ) : (
          <div style={{ textAlign: 'center', padding: '40px 0', color: '#999' }}>
            <MessageOutlined style={{ fontSize: '48px', marginBottom: '16px' }} />
            <div>暂无评论，来抢沙发吧！</div>
          </div>
        )}
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
          <Button 
            key="purchase" 
            type="primary" 
            onClick={handlePurchase}
            disabled={currentUser.balance < post.price}
          >
            确认购买 ${post.price}
          </Button>
        ]}
      >
        <div style={{ textAlign: 'center' }}>
          <DollarOutlined style={{ fontSize: '48px', color: '#faad14', marginBottom: '16px' }} />
          <Title level={4}>购买确认</Title>
          <Paragraph>
            您即将购买 <Text strong>{post.title}</Text>
          </Paragraph>
          <Paragraph>
            价格: <Text strong style={{ color: '#faad14', fontSize: '18px' }}>
              ${post.price}
            </Text>
          </Paragraph>
          <Paragraph>
            当前余额: <Text strong style={{ color: currentUser.balance >= post.price ? '#52c41a' : '#ff4d4f', fontSize: '16px' }}>
              ${currentUser.balance.toFixed(2)}
            </Text>
          </Paragraph>
          {currentUser.balance < post.price && (
            <Paragraph type="danger">
              余额不足，无法购买此内容
            </Paragraph>
          )}
          <Paragraph type="secondary">
            购买后可以查看完整内容，并对内容进行评分
          </Paragraph>
        </div>
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
          <Form.Item label="反馈意见（可选）">
            <TextArea
              rows={4}
              placeholder="请分享您对这篇内容的看法和建议..."
              value={feedback}
              onChange={(e) => setFeedback(e.target.value)}
            />
          </Form.Item>
        </div>
      </Modal>

      {/* 分享模态框 */}
      <ShareModal
        visible={isShareModalVisible}
        onCancel={() => setIsShareModalVisible(false)}
        post={post}
      />
    </div>
  );
};

export default PostDetail;
