import React from 'react';
import { Form, Input, Button, Card, Typography, Space } from 'antd';
import { UserOutlined, LockOutlined, LoginOutlined, FireOutlined } from '@ant-design/icons';

const { Title, Text } = Typography;

interface LoginProps {
  onLogin: (username: string, password: string) => void;
  isLoading: boolean;
}

const Login: React.FC<LoginProps> = ({ onLogin, isLoading }) => {
  const [form] = Form.useForm();

  const handleSubmit = (values: { username: string; password: string }) => {
    onLogin(values.username, values.password);
  };

  return (
    <div className="login-screen">
      <aside className="login-panel">
        <div className="login-brand">
          <span className="login-mark"><FireOutlined /></span>
          <div>
            <Title level={4} style={{ color: '#fff', margin: 0 }}>深度焦点</Title>
            <Text style={{ color: '#9fb0bb' }}>DeepFocus Investment Terminal</Text>
          </div>
        </div>

        <div className="login-hero">
          <h1>专业投研工作台</h1>
          <p>把个股社区、付费研究、组合关注和智能投研工具收束到一套清晰的投资操作界面。</p>
          <div className="login-terminal-lines">
            <div className="login-terminal-line">
              <span>FOCUS_POOL</span>
              <span>实时关注</span>
            </div>
            <div className="login-terminal-line">
              <span>RESEARCH_FLOW</span>
              <span>投研内容</span>
            </div>
            <div className="login-terminal-line">
              <span>AGENT_CENTER</span>
              <span>智能协作</span>
            </div>
          </div>
        </div>

        <Text style={{ color: '#81929d', fontSize: 12 }}>
          Demo workspace · For investment research workflow
        </Text>
      </aside>

      <main className="login-form-zone">
      <Card
        className="login-card"
        styles={{ body: { padding: '34px' } }}
      >
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div>
            <Title level={3} style={{ margin: 0 }}>
              登录工作台
            </Title>
            <Text type="secondary">进入深度焦点投研终端</Text>
          </div>

          <Form
            form={form}
            name="login"
            onFinish={handleSubmit}
            autoComplete="off"
            size="large"
          >
            <Form.Item
              name="username"
              rules={[
                { required: true, message: '请输入用户名' },
                { min: 3, message: '用户名至少3个字符' }
              ]}
            >
              <Input
                prefix={<UserOutlined />}
                placeholder="用户名"
                style={{ borderRadius: '8px' }}
              />
            </Form.Item>

            <Form.Item
              name="password"
              rules={[
                { required: true, message: '请输入密码' },
                { min: 3, message: '密码至少3个字符' }
              ]}
            >
              <Input.Password
                prefix={<LockOutlined />}
                placeholder="密码"
                style={{ borderRadius: '8px' }}
              />
            </Form.Item>

            <Form.Item>
              <Button
                type="primary"
                htmlType="submit"
                loading={isLoading}
                icon={<LoginOutlined />}
                style={{
                  width: '100%',
                  height: '48px',
                  borderRadius: '8px',
                  fontSize: '16px',
                  fontWeight: 'bold'
                }}
              >
                登录
              </Button>
            </Form.Item>
          </Form>

          <div style={{ textAlign: 'center' }}>
            <Text type="secondary" style={{ fontSize: '12px' }}>
              演示账号：用户名 demo，密码 demo
            </Text>
          </div>
        </Space>
      </Card>
      </main>
    </div>
  );
};

export default Login;
