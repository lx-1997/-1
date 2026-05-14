import React, { useState } from 'react';
import { 
  Card, 
  List, 
  Tag, 
  Typography, 
  Space, 
  Button, 
  Avatar, 
  Badge, 
  Divider,
  Input,
  Select,
  Row,
  Col
} from 'antd';
import { 
  TeamOutlined, 
  TrophyOutlined, 
  LikeOutlined, 
  MessageOutlined,
  CrownOutlined,
  StarOutlined,
  FireOutlined,
  RiseOutlined
} from '@ant-design/icons';
import { Post } from '../types';

const { Title, Text, Paragraph } = Typography;
const { Search } = Input;
const { Option } = Select;

interface ExpertClubProps {
  posts: Post[];
}

const ExpertClub: React.FC<ExpertClubProps> = ({ posts }) => {
  const [searchText, setSearchText] = useState('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');

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

  const getPostTypeText = (type: string) => {
    switch (type) {
      case 'news': return '资讯';
      case 'analysis': return '分析';
      case 'discussion': return '讨论';
      case 'qa': return '问答';
      default: return '其他';
    }
  };

  const filteredPosts = posts.filter(post => {
    const matchesSearch = post.title.toLowerCase().includes(searchText.toLowerCase()) ||
                         post.content.toLowerCase().includes(searchText.toLowerCase());
    const matchesCategory = selectedCategory === 'all' || post.category === selectedCategory;
    
    return matchesSearch && matchesCategory;
  });

  return (
    <div>
      <Card 
        title={
          <Space>
            <TeamOutlined style={{ color: '#1890ff' }} />
            <span>专家俱乐部</span>
            <Text type="secondary">高质量社区讨论，专业投研交流</Text>
          </Space>
        }
        extra={
          <Button type="primary" icon={<TrophyOutlined />}>
            发布分析
          </Button>
        }
      >
        {/* 筛选和搜索 */}
        <Row gutter={16} style={{ marginBottom: 16 }}>
          <Col span={12}>
            <Search
              placeholder="搜索帖子内容..."
              value={searchText}
              onChange={(e) => setSearchText(e.target.value)}
            />
          </Col>
          <Col span={8}>
            <Select
              value={selectedCategory}
              onChange={setSelectedCategory}
              style={{ width: '100%' }}
            >
              <Option value="all">全部分类</Option>
              <Option value="technical">技术分析</Option>
              <Option value="fundamental">基本面</Option>
              <Option value="market">市场分析</Option>
              <Option value="earnings">财报分析</Option>
              <Option value="product">产品分析</Option>
              <Option value="regulatory">监管分析</Option>
            </Select>
          </Col>
        </Row>

        {/* 帖子列表 */}
        <List
          dataSource={filteredPosts}
          renderItem={(post) => (
            <List.Item
              actions={[
                <Button type="link" icon={<LikeOutlined />}>
                  {post.likes}
                </Button>,
                <Button type="link" icon={<MessageOutlined />}>
                  {post.comments}
                </Button>
              ]}
            >
              <List.Item.Meta
                avatar={
                  <Badge
                    count={post.isPaid ? <FireOutlined style={{ color: '#ff4d4f' }} /> : 0}
                    offset={[-5, 5]}
                  >
                    <Avatar 
                      src={post.author.avatar}
                      size={48}
                    />
                  </Badge>
                }
                title={
                  <Space>
                    <Text strong>{post.title}</Text>
                    {post.isPinned && <Tag color="red">置顶</Tag>}
                    {post.isHighlighted && <Tag color="gold">精华</Tag>}
                    <Tag color="blue">
                      {getCategoryText(post.category)}
                    </Tag>
                    <Tag color="green">
                      {getPostTypeText(post.type)}
                    </Tag>
                  </Space>
                }
                description={
                  <div>
                    <Space>
                      <Text strong>{post.author.username}</Text>
                      <Tag color={post.author.reputation > 80 ? 'gold' : post.author.reputation > 60 ? 'blue' : 'default'}>
                        {post.author.reputation}分
                      </Tag>
                      <Text type="secondary">
                        {new Date(post.publishTime).toLocaleString()}
                      </Text>
                    </Space>
                    <Paragraph ellipsis={{ tooltip: post.summary }} style={{ marginTop: 8 }}>
                      {post.summary}
                    </Paragraph>
                    <div style={{ marginTop: 8 }}>
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
      </Card>
    </div>
  );
};

export default ExpertClub;
