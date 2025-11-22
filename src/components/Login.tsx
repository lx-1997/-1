import React, { useState } from 'react';
import { Form, Input, Button, Card, Typography, Space, message } from 'antd';
import { UserOutlined, LockOutlined, LoginOutlined } from '@ant-design/icons';

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
    <div style={{
      height: '100vh',
      display: 'flex',
      justifyContent: 'center',
      alignItems: 'center',
      background: 'linear-gradient(135deg, #667eea 0%, #764ba2 100%)'
    }}>
      <Card
        style={{
          width: 400,
          boxShadow: '0 8px 32px rgba(0, 0, 0, 0.1)',
          borderRadius: '12px'
        }}
        styles={{ body: { padding: '40px' } }}
      >
        <Space direction="vertical" size="large" style={{ width: '100%' }}>
          <div style={{ textAlign: 'center' }}>
            <Title level={2} style={{ margin: 0, color: '#1890ff' }}>
              交易客户端
            </Title>
            <Text type="secondary">专业的金融交易平台</Text>
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
    </div>
  );
};

export default Login;
