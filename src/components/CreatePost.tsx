import React, { useState } from 'react';
import { 
  Card, 
  Form, 
  Input, 
  Select, 
  Button, 
  Space, 
  Typography, 
  Switch, 
  InputNumber,
  Tag,
  message,
  Divider,
  Row,
  Col
} from 'antd';
import { 
  SaveOutlined, 
  EyeOutlined, 
  DollarOutlined,
  StarOutlined,
  TagOutlined
} from '@ant-design/icons';
import { Post, Stock } from '../types';

const { Title, Text } = Typography;
const { TextArea } = Input;
const { Option } = Select;

interface CreatePostProps {
  stock: Stock;
  onSave: (post: Partial<Post>) => void;
  onCancel: () => void;
}

const CreatePost: React.FC<CreatePostProps> = ({ stock, onSave, onCancel }) => {
  const [form] = Form.useForm();
  const [isPaid, setIsPaid] = useState(false);
  const [tags, setTags] = useState<string[]>([]);
  const [inputTag, setInputTag] = useState('');

  const handleSubmit = (values: any) => {
    const postData: Partial<Post> = {
      ...values,
      stockSymbol: stock.symbol,
      tags,
      isPaid,
      publishTime: new Date().toISOString(),
      updateTime: new Date().toISOString(),
      status: 'published',
      likes: 0,
      comments: 0,
      shares: 0,
      views: 0,
      paidViewers: 0,
      totalRevenue: 0,
      qualityScore: 0,
      totalRatings: 0,
      isPinned: false,
      isHighlighted: false
    };

    onSave(postData);
    message.success('内容发布成功！');
  };

  const handleAddTag = () => {
    if (inputTag && !tags.includes(inputTag)) {
      setTags([...tags, inputTag]);
      setInputTag('');
    }
  };

  const handleRemoveTag = (tagToRemove: string) => {
    setTags(tags.filter(tag => tag !== tagToRemove));
  };

  return (
    <div>
      <Card 
        title={
          <Space>
            <StarOutlined style={{ color: '#1890ff' }} />
            <span>发布内容</span>
            <Text type="secondary">为 {stock.name} ({stock.symbol}) 发布新内容</Text>
          </Space>
        }
        extra={
          <Space>
            <Button onClick={onCancel}>取消</Button>
            <Button 
              type="primary" 
              icon={<SaveOutlined />}
              onClick={() => form.submit()}
            >
              发布
            </Button>
          </Space>
        }
      >
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          initialValues={{
            type: 'news',
            category: 'market'
          }}
        >
          <Form.Item
            name="title"
            label="标题"
            rules={[{ required: true, message: '请输入标题' }]}
          >
            <Input placeholder="请输入内容标题" size="large" />
          </Form.Item>

          <Form.Item
            name="summary"
            label="摘要"
            rules={[{ required: true, message: '请输入摘要' }]}
          >
            <TextArea 
              rows={3} 
              placeholder="请输入内容摘要，简要描述主要内容" 
            />
          </Form.Item>

          <Form.Item
            name="content"
            label="内容"
            rules={[{ required: true, message: '请输入内容' }]}
          >
            <TextArea 
              rows={10} 
              placeholder="请输入详细内容，支持 Markdown 格式" 
            />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="type"
                label="内容类型"
                rules={[{ required: true, message: '请选择内容类型' }]}
              >
                <Select placeholder="选择内容类型">
                  <Option value="news">资讯</Option>
                  <Option value="analysis">分析</Option>
                  <Option value="discussion">讨论</Option>
                  <Option value="qa">问答</Option>
                </Select>
              </Form.Item>
            </Col>
            <Col span={12}>
              <Form.Item
                name="category"
                label="分类"
                rules={[{ required: true, message: '请选择分类' }]}
              >
                <Select placeholder="选择分类">
                  <Option value="technical">技术</Option>
                  <Option value="fundamental">基本面</Option>
                  <Option value="market">市场</Option>
                  <Option value="earnings">财报</Option>
                  <Option value="product">产品</Option>
                  <Option value="regulatory">监管</Option>
                </Select>
              </Form.Item>
            </Col>
          </Row>

          <Form.Item label="标签">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space>
                <Input
                  placeholder="输入标签"
                  value={inputTag}
                  onChange={(e) => setInputTag(e.target.value)}
                  onPressEnter={handleAddTag}
                  style={{ width: 200 }}
                />
                <Button onClick={handleAddTag} icon={<TagOutlined />}>
                  添加
                </Button>
              </Space>
              <div>
                {tags.map(tag => (
                  <Tag
                    key={tag}
                    closable
                    onClose={() => handleRemoveTag(tag)}
                    style={{ marginBottom: 8 }}
                  >
                    {tag}
                  </Tag>
                ))}
              </div>
            </Space>
          </Form.Item>

          <Divider />

          <Form.Item label="付费设置">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space>
                <Switch 
                  checked={isPaid} 
                  onChange={setIsPaid}
                  checkedChildren={<DollarOutlined />}
                  unCheckedChildren={<EyeOutlined />}
                />
                <Text>{isPaid ? '付费内容' : '免费内容'}</Text>
              </Space>
              
              {isPaid && (
                <Form.Item
                  name="price"
                  label="价格"
                  rules={[{ required: true, message: '请输入价格' }]}
                >
                  <InputNumber
                    min={0.01}
                    max={999.99}
                    step={0.01}
                    placeholder="0.00"
                    prefix="$"
                    style={{ width: 200 }}
                  />
                </Form.Item>
              )}
            </Space>
          </Form.Item>

          <Divider />

          <div style={{ textAlign: 'center', color: '#666' }}>
            <Text>
              {isPaid ? (
                <>
                  <DollarOutlined style={{ color: '#faad14' }} />
                  付费内容将获得更多曝光，用户付费查看后可以评分，有助于提升您的声誉
                </>
              ) : (
                <>
                  <EyeOutlined style={{ color: '#52c41a' }} />
                  免费内容有助于建立声誉，获得更多关注者
                </>
              )}
            </Text>
          </div>
        </Form>
      </Card>
    </div>
  );
};

export default CreatePost;
