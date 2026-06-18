import React, { useState } from 'react';
import { Form, Input, Button, Card, Typography, Space } from 'antd';
import { UserOutlined, LockOutlined, MailOutlined, LoginOutlined, UserAddOutlined, FireOutlined } from '@ant-design/icons';

const { Title, Text } = Typography;

interface LoginProps {
  onLogin: (username: string, password: string) => void;
  onRegister?: (email: string, username: string, password: string) => void;
  isLoading: boolean;
  demoLoginEnabled?: boolean;
}

const Login: React.FC<LoginProps> = ({ onLogin, onRegister, isLoading, demoLoginEnabled = true }) => {
  const [form] = Form.useForm();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const registerEnabled = typeof onRegister === 'function';
  const isRegister = mode === 'register';

  const handleSubmit = (values: { email?: string; username: string; password: string }) => {
    if (isRegister && onRegister) {
      onRegister(values.email || '', values.username, values.password);
    } else {
      onLogin(values.username, values.password);
    }
  };

  const toggleMode = () => {
    setMode(prev => (prev === 'login' ? 'register' : 'login'));
    form.resetFields();
  };

  return (
    <div className="login-screen">
      <aside className="login-panel">
        <div className="login-brand">
          <span className="login-mark"><FireOutlined /></span>
          <div>
            <Title level={4} style={{ color: '#ffffff', margin: 0 }}>深度焦点</Title>
            <Text style={{ color: 'var(--text-muted)' }}>DeepFocus Investment Terminal</Text>
          </div>
        </div>

        <div className="login-hero">
          <h1>专业投研工作台</h1>
          <p>把观察池、证据库、深度报告和投研任务收束到一套清晰的投资操作界面。</p>
          <div className="login-terminal-lines">
            <div className="login-terminal-line">
              <span>FOCUS_POOL</span>
              <span>实时关注</span>
            </div>
            <div className="login-terminal-line">
              <span>RESEARCH_FLOW</span>
              <span>证据链路</span>
            </div>
            <div className="login-terminal-line">
              <span>AGENT_CENTER</span>
              <span>智能协作</span>
            </div>
          </div>
        </div>

        <Text style={{ color: 'var(--text-muted)', fontSize: 12 }}>
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
              {isRegister ? '注册账号' : '登录工作台'}
            </Title>
            <Text type="secondary">
              {isRegister ? '创建深度焦点投研账号' : '进入深度焦点投研终端'}
            </Text>
          </div>

          <Form
            form={form}
            name={isRegister ? 'register' : 'login'}
            onFinish={handleSubmit}
            autoComplete="off"
            size="large"
          >
            {isRegister && (
              <Form.Item
                name="email"
                rules={[
                  { required: true, message: '请输入邮箱' },
                  { type: 'email', message: '邮箱格式不正确' }
                ]}
              >
                <Input
                  prefix={<MailOutlined />}
                  placeholder="邮箱"
                  style={{ borderRadius: '8px' }}
                />
              </Form.Item>
            )}

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
                { min: isRegister ? 6 : 3, message: `密码至少${isRegister ? 6 : 3}个字符` }
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
                icon={isRegister ? <UserAddOutlined /> : <LoginOutlined />}
                style={{
                  width: '100%',
                  height: '48px',
                  borderRadius: '8px',
                  fontSize: '16px',
                  fontWeight: 'bold'
                }}
              >
                {isRegister ? '注册并进入' : '登录'}
              </Button>
            </Form.Item>
          </Form>

          {registerEnabled && (
            <div style={{ textAlign: 'center' }}>
              <Text type="secondary" style={{ fontSize: '12px' }}>
                {isRegister ? '已有账号？' : '还没有账号？'}
              </Text>{' '}
              <Button type="link" size="small" style={{ padding: 0, fontSize: '12px' }} onClick={toggleMode}>
                {isRegister ? '去登录' : '注册新账号'}
              </Button>
            </div>
          )}

          {demoLoginEnabled ? (
            <div style={{ textAlign: 'center' }}>
              <Text type="secondary" style={{ fontSize: '12px' }}>
                演示账号：用户名 demo，密码 demo
              </Text>
            </div>
          ) : (
            <div style={{ textAlign: 'center' }}>
              <Text type="secondary" style={{ fontSize: '12px' }}>
                演示登录已关闭，请接入真实认证服务
              </Text>
            </div>
          )}
        </Space>
      </Card>
      </main>
    </div>
  );
};

export default React.memo(Login);
