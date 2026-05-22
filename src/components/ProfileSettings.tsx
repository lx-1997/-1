import React from 'react';
import { Avatar, Button, Card, Col, Input, Row, Space, Switch, Tag, Typography } from 'antd';
import {
  BellOutlined,
  DatabaseOutlined,
  LockOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  UserOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import ModelSettingsPanel from './settings/ModelSettingsPanel';

const { Text, Title } = Typography;

interface ProfileSettingsProps {
  appState: AppState;
}

const ProfileSettings: React.FC<ProfileSettingsProps> = ({ appState }) => {
  const user = appState.user;

  return (
    <div className="profile-settings-page">
      <section className="profile-settings-hero">
        <Space size={14} align="center">
          <Avatar size={58} src={user?.avatar} icon={<UserOutlined />} />
          <div>
            <Title level={3}>系统设置</Title>
            <Text>{user?.username || '投资者'} · {user?.email || '本地演示账户'}</Text>
            <div className="profile-settings-tags">
              <Tag color="cyan">DeepFocus</Tag>
              <Tag color="green">全局模型</Tag>
              <Tag color="blue">本机配置</Tag>
            </div>
          </div>
        </Space>
        <Button type="primary" icon={<SettingOutlined />}>设置中心</Button>
      </section>

      <section className="profile-settings-section">
        <ModelSettingsPanel />
      </section>

      <Row gutter={[14, 14]}>
        <Col xs={24} lg={10}>
          <Card className="profile-settings-card" title={<Space><UserOutlined />账户资料</Space>}>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <label>
                <Text type="secondary">用户名</Text>
                <Input value={user?.username || 'demo'} readOnly />
              </label>
              <label>
                <Text type="secondary">邮箱</Text>
                <Input value={user?.email || 'demo@deepfocus.local'} readOnly />
              </label>
              <label>
                <Text type="secondary">账户余额</Text>
                <Input value={`$${(user?.balance || 0).toFixed(2)}`} readOnly />
              </label>
            </Space>
          </Card>
        </Col>

        <Col xs={24} lg={14}>
          <Card className="profile-settings-card" title={<Space><DatabaseOutlined />工作台偏好</Space>}>
            <div className="profile-settings-list">
              <div>
                <strong>默认打开研报工作台</strong>
                <Text type="secondary">进入系统后优先回到证据与研报处理区。</Text>
              </div>
              <Switch defaultChecked />
            </div>
            <div className="profile-settings-list">
              <div>
                <strong>消息流提醒</strong>
                <Text type="secondary">关键标的、任务完成和数据源异常时通知。</Text>
              </div>
              <Switch defaultChecked />
            </div>
            <div className="profile-settings-list">
              <div>
                <strong>深色工作区</strong>
                <Text type="secondary">在沉浸式工具页面使用深色外壳，减少白屏割裂。</Text>
              </div>
              <Switch defaultChecked />
            </div>
          </Card>

          <Card className="profile-settings-card" title={<Space><SafetyCertificateOutlined />安全与权限</Space>}>
            <Row gutter={[12, 12]}>
              <Col xs={24} md={8}>
                <div className="profile-settings-metric">
                  <LockOutlined />
                  <strong>本地凭证</strong>
                  <Text type="secondary">API Key 和 Cookie 不提交到远端仓库。</Text>
                </div>
              </Col>
              <Col xs={24} md={8}>
                <div className="profile-settings-metric">
                  <BellOutlined />
                  <strong>提醒</strong>
                  <Text type="secondary">3 条待处理系统通知。</Text>
                </div>
              </Col>
              <Col xs={24} md={8}>
                <div className="profile-settings-metric">
                  <DatabaseOutlined />
                  <strong>资料</strong>
                  <Text type="secondary">{appState.posts.length} 条研究资料。</Text>
                </div>
              </Col>
            </Row>
          </Card>
        </Col>
      </Row>
    </div>
  );
};

export default ProfileSettings;
