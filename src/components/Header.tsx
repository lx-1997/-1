import React, { useState } from 'react';
import { Layout, Input, Button, Dropdown, Space, Avatar, Badge, Typography } from 'antd';
import {
  SearchOutlined,
  BellOutlined,
  UserOutlined,
  FireOutlined,
  MenuOutlined,
  DollarOutlined,
  ShoppingCartOutlined
} from '@ant-design/icons';
import { AppState, ViewType } from '../types';
import RechargeModal from './RechargeModal';

const { Header: AntHeader } = Layout;
const { Text } = Typography;

interface HeaderProps {
  appState: AppState;
  onLogout: () => void;
  onStockSelect: (stock: any) => void;
  onRecharge: (amount: number, method: string) => void;
  isMobile?: boolean;
  onMobileMenuToggle?: () => void;
  onViewChange?: (view: ViewType) => void;
}

const Header: React.FC<HeaderProps> = ({
  appState,
  onLogout,
  onStockSelect,
  onRecharge,
  isMobile = false,
  onMobileMenuToggle,
  onViewChange
}) => {
  const [searchValue, setSearchValue] = useState('');
  const [rechargeModalVisible, setRechargeModalVisible] = useState(false);

  const handleSearch = (value: string) => {
    if (value.trim()) {
      // 这里可以搜索股票
      console.log('搜索股票:', value.trim());
    }
  };

  const userMenuItems = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人资料'
    },
    {
      key: 'settings',
      icon: <UserOutlined />,
      label: '设置'
    },
    {
      type: 'divider' as const
    },
    {
      key: 'logout',
      icon: <UserOutlined />,
      label: '退出登录',
      onClick: onLogout
    }
  ];

  return (
    <AntHeader
      style={{
        background: '#fff',
        padding: isMobile ? '0 16px' : '0 24px',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        borderBottom: '1px solid #f0f0f0',
        height: '64px'
      }}
    >
      {/* 移动端菜单按钮 */}
      {isMobile && (
        <Button
          type="text"
          icon={<MenuOutlined />}
          onClick={onMobileMenuToggle}
          style={{ marginRight: '16px' }}
        />
      )}

      <div className="header-content" style={{ flex: isMobile ? 1 : 'none' }}>
        <div className="header-left">
          <div className="logo">
            <FireOutlined style={{ fontSize: '24px', color: '#ff4d4f', marginRight: '8px' }} />
            <span className="logo-text">深度焦点</span>
            {!isMobile && <span className="logo-subtitle">DeepFocus</span>}
          </div>
          {!isMobile && (
            <div className="slogan">
              告别噪音，专注价值
            </div>
          )}
        </div>
      </div>

      {/* 搜索框 - 移动端隐藏 */}
      {!isMobile && (
        <div style={{ flex: 1, maxWidth: '400px', margin: '0 24px' }}>
          <Input.Search
            placeholder="搜索股票代码或名称"
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value)}
            onSearch={handleSearch}
            enterButton={<SearchOutlined />}
            style={{ width: '100%' }}
            size="large"
          />
        </div>
      )}

      {/* 用户信息 - 移动端简化 */}
      {!isMobile && (
        <div style={{ margin: '0 24px' }}>
          <Space>
            <Text type="secondary">
              评分: {appState.user?.reputation || 0}
            </Text>
            <Text type="secondary">
              收益: ¥{appState.user?.totalEarnings || 0}
            </Text>
            <Text strong style={{ color: '#52c41a' }}>
              余额: ${appState.user?.balance?.toFixed(2) || '0.00'}
            </Text>
            <Text type="secondary" style={{ fontSize: '12px' }}>
              平台: ${appState.platformBalance?.toFixed(2) || '0.00'}
            </Text>
            <Button 
              type="primary" 
              size="small" 
              icon={<DollarOutlined />}
              onClick={() => setRechargeModalVisible(true)}
            >
              充值
            </Button>
          </Space>
        </div>
      )}

      {/* 通知和用户菜单 */}
      <Space size={isMobile ? 'small' : 'middle'}>
        {/* 购物车 */}
        <Badge count={appState.cart?.length || 0} size="small" showZero={false}>
          <Button
            type="text"
            icon={<ShoppingCartOutlined />}
            size={isMobile ? 'middle' : 'large'}
            style={{ color: '#666' }}
            onClick={() => onViewChange?.('cart')}
          />
        </Badge>
        
        {/* 通知 */}
        <Badge count={3} size="small">
          <Button
            type="text"
            icon={<BellOutlined />}
            size={isMobile ? 'middle' : 'large'}
            style={{ color: '#666' }}
          />
        </Badge>

        {/* 用户菜单 */}
        <Dropdown
          menu={{ items: userMenuItems }}
          placement="bottomRight"
          trigger={['click']}
        >
          <Button
            type="text"
            style={{
              height: '40px',
              display: 'flex',
              alignItems: 'center',
              gap: isMobile ? '4px' : '8px',
              padding: isMobile ? '0 4px' : '0 8px'
            }}
          >
            <Avatar
              size="small"
              src={appState.user?.avatar}
              icon={<UserOutlined />}
            />
            {!isMobile && <Text strong>{appState.user?.username}</Text>}
          </Button>
        </Dropdown>
      </Space>

      {/* 充值模态框 */}
      <RechargeModal
        visible={rechargeModalVisible}
        onCancel={() => setRechargeModalVisible(false)}
        onRecharge={(amount, method) => {
          onRecharge(amount, method);
          setRechargeModalVisible(false);
        }}
        currentBalance={appState.user?.balance || 0}
      />
    </AntHeader>
  );
};

export default Header;
