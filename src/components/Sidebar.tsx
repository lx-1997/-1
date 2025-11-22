import React from 'react';
import { Menu, Typography, Space, Divider } from 'antd';
import {
  DashboardOutlined,
  UserOutlined,
  DollarOutlined,
  BankOutlined,
  ShoppingCartOutlined,
  FileTextOutlined
} from '@ant-design/icons';
import { AppState } from '../types';

const { Text } = Typography;

interface SidebarProps {
  selectedMenu: string;
  onMenuSelect: (key: string) => void;
  appState: AppState;
}

const Sidebar: React.FC<SidebarProps> = ({
  selectedMenu,
  onMenuSelect,
  appState
}) => {
  const menuItems = [
    {
      key: 'home',
      icon: <DashboardOutlined />,
      label: '首页'
    },
    {
      key: 'cart',
      icon: <ShoppingCartOutlined />,
      label: '购物车'
    },
    {
      key: 'orders',
      icon: <FileTextOutlined />,
      label: '我的订单'
    },
    {
      key: 'profile',
      icon: <UserOutlined />,
      label: '个人中心'
    },
    {
      key: 'recharge-history',
      icon: <DollarOutlined />,
      label: '充值记录'
    },
    {
      key: 'platform-balance',
      icon: <BankOutlined />,
      label: '平台余额'
    }
  ];

  // 热门股票列表
  const hotStocks = appState.stocks.slice(0, 5);

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* 主导航菜单 */}
      <Menu
        mode="inline"
        selectedKeys={[selectedMenu]}
        items={menuItems}
        onClick={({ key }) => onMenuSelect(key)}
        style={{
          height: '100%',
          borderRight: 0,
          fontSize: '14px'
        }}
      />

      <Divider style={{ margin: '16px 0' }} />

      {/* 热门股票 */}
      <div style={{ padding: '0 16px' }}>
        <Text strong style={{ fontSize: '14px', marginBottom: '12px', display: 'block' }}>
          热门股票
        </Text>
        <Space direction="vertical" style={{ width: '100%' }}>
          {hotStocks.map(stock => (
            <div
              key={stock.symbol}
              style={{
                padding: '8px 12px',
                background: '#f5f5f5',
                borderRadius: '6px',
                cursor: 'pointer',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center'
              }}
              onClick={() => onMenuSelect('home')}
            >
              <div>
                <Text strong style={{ fontSize: '12px' }}>
                  {stock.symbol}
                </Text>
                <br />
                <Text type="secondary" style={{ fontSize: '11px' }}>
                  {stock.name}
                </Text>
              </div>
              <Text
                type={stock.changePercent >= 0 ? 'success' : 'danger'}
                style={{ fontSize: '12px' }}
              >
                {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
              </Text>
            </div>
          ))}
        </Space>
      </div>
    </div>
  );
};

export default Sidebar;
