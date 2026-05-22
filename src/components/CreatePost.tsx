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
            <span>记录研究假设</span>
            <Text type="secondary">为 {stock.name} ({stock.symbol}) 沉淀证据、观点和复盘条件</Text>
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
              保存记录
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
            <Input placeholder="请输入研究标题" size="large" />
          </Form.Item>

          <Form.Item
            name="summary"
            label="摘要"
            rules={[{ required: true, message: '请输入摘要' }]}
          >
            <TextArea
              rows={3}
              placeholder="写下核心结论、证据线索或待验证假设"
            />
          </Form.Item>

          <Form.Item
            name="content"
            label="正文"
            rules={[{ required: true, message: '请输入正文' }]}
          >
            <TextArea
              rows={10}
              placeholder="请输入证据、推理、反证和复盘条件，支持 Markdown 格式"
            />
          </Form.Item>

          <Row gutter={16}>
            <Col span={12}>
              <Form.Item
                name="type"
                label="记录类型"
                rules={[{ required: true, message: '请选择记录类型' }]}
              >
                <Select placeholder="选择记录类型">
                  <Option value="news">资讯证据</Option>
                  <Option value="analysis">研究观点</Option>
                  <Option value="discussion">讨论记录</Option>
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

          <Form.Item label="访问权限">
            <Space direction="vertical" style={{ width: '100%' }}>
              <Space>
                <Switch 
                  checked={isPaid} 
                  onChange={setIsPaid}
                  checkedChildren={<DollarOutlined />}
                  unCheckedChildren={<EyeOutlined />}
                />
                <Text>{isPaid ? '深度报告' : '公开记录'}</Text>
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
                  深度报告适合放入完整模型、证据引用和复盘框架
                </>
              ) : (
                <>
                  <EyeOutlined style={{ color: '#52c41a' }} />
                  公开记录适合沉淀线索、假设和可验证问题
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
