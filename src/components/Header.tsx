import React, { useState } from 'react';
import { Layout, Input, Button, Dropdown, Space, Avatar, Badge, Typography, message } from 'antd';
import {
  SearchOutlined,
  BellOutlined,
  UserOutlined,
  FireOutlined,
  MenuOutlined,
  DollarOutlined,
  ShoppingCartOutlined,
  LogoutOutlined,
  SettingOutlined,
  SyncOutlined
} from '@ant-design/icons';
import { AppState, ViewType } from '../types';
import RechargeModal from './RechargeModal';
import { formatQuoteSourceLine } from '../utils/marketData';

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
  onRefreshMarketData?: () => void;
  isMarketDataRefreshing?: boolean;
}

const Header: React.FC<HeaderProps> = ({
  appState,
  onLogout,
  onStockSelect,
  onRecharge,
  isMobile = false,
  onMobileMenuToggle,
  onViewChange,
  onRefreshMarketData,
  isMarketDataRefreshing = false
}) => {
  const [searchValue, setSearchValue] = useState('');
  const [rechargeModalVisible, setRechargeModalVisible] = useState(false);
  const cartItemCount = appState.cart?.reduce((sum, item) => sum + item.quantity, 0) || 0;
  const quoteAnchor = appState.stocks.find(stock => stock.quoteProvider && stock.quoteProvider !== 'mock')
    || appState.stocks[0];

  const handleSearch = (value: string) => {
    const keyword = value.trim().toLowerCase();
    if (!keyword) {
      return;
    }

    const matchedStock = appState.stocks.find(stock =>
      stock.symbol.toLowerCase().includes(keyword) ||
      stock.name.toLowerCase().includes(keyword)
    );

    if (!matchedStock) {
      message.warning('未找到匹配的股票');
      return;
    }

    onStockSelect(matchedStock);
    setSearchValue('');
  };

  const userMenuItems = [
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人资料'
    },
    {
      key: 'settings',
      icon: <SettingOutlined />,
      label: '设置'
    },
    {
      type: 'divider' as const
    },
    {
      key: 'logout',
      icon: <LogoutOutlined />,
      label: '退出登录'
    }
  ];

  return (
    <AntHeader
      className="terminal-header"
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

      <div className="terminal-brand">
        <span className="brand-mark">
          <FireOutlined />
        </span>
        <div className="brand-copy">
          <span className="brand-title">深度焦点</span>
          <span className="brand-subtitle">DeepFocus 投研终端</span>
        </div>
      </div>

      {/* 搜索框 - 移动端隐藏 */}
      {!isMobile && (
        <div className="header-search">
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
        <div className="header-metrics">
          <span className="market-pill">
            声誉 <strong>{appState.user?.reputation || 0}</strong>
          </span>
          <span className="market-pill positive">
            收益 <strong>¥{(appState.user?.totalEarnings || 0).toFixed(2)}</strong>
          </span>
          <span className="market-pill">
            余额 <strong>${appState.user?.balance?.toFixed(2) || '0.00'}</strong>
          </span>
          <span className="market-pill">
            平台 <strong>${appState.platformBalance?.toFixed(2) || '0.00'}</strong>
          </span>
          <span className="market-pill">
            行情 <strong>{formatQuoteSourceLine(quoteAnchor)}</strong>
          </span>
          <Button
            size="small"
            icon={<SyncOutlined spin={isMarketDataRefreshing} />}
            loading={isMarketDataRefreshing}
            onClick={onRefreshMarketData}
          >
            刷新
          </Button>
            <Button 
              type="primary" 
              size="small" 
              icon={<DollarOutlined />}
              onClick={() => setRechargeModalVisible(true)}
            >
              充值
            </Button>
        </div>
      )}

      {/* 通知和用户菜单 */}
      <Space className="header-actions" size={isMobile ? 'small' : 'small'}>
        {/* 购物车 */}
        <Badge count={cartItemCount} size="small" showZero={false}>
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
          menu={{
            items: userMenuItems,
            onClick: ({ key }) => {
              if (key === 'logout') {
                onLogout();
                return;
              }

              if (key === 'profile') {
                onViewChange?.('profile');
                return;
              }

              if (key === 'settings') {
                message.info('设置功能开发中');
              }
            }
          }}
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
