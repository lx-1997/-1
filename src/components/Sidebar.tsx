import React from 'react';
import { Menu, Typography, Space } from 'antd';
import {
  DashboardOutlined,
  UserOutlined,
  DollarOutlined,
  BankOutlined,
  ShoppingCartOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  RobotOutlined,
  DatabaseOutlined,
  ToolOutlined,
  CalendarOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';

const { Text } = Typography;

interface SidebarProps {
  selectedMenu: string;
  onMenuSelect: (key: string) => void;
  onStockSelect: (stock: Stock) => void;
  appState: AppState;
}

const Sidebar: React.FC<SidebarProps> = ({
  selectedMenu,
  onMenuSelect,
  onStockSelect,
  appState
}) => {
  const menuItems = [
    {
      type: 'group' as const,
      label: '投研中枢',
      children: [
        {
          key: 'home',
          icon: <DashboardOutlined />,
          label: 'AI 工作台'
        },
        {
          key: 'ai-research',
          icon: <ExperimentOutlined />,
          label: '一键检测'
        },
        {
          key: 'agent-center',
          icon: <RobotOutlined />,
          label: '多 Agent 调度'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '个股研究',
      children: [
        {
          key: 'stocks',
          icon: <DashboardOutlined />,
          label: '个股池'
        },
        {
          key: 'earnings-calendar',
          icon: <CalendarOutlined />,
          label: '财报日历'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '数据与证据',
      children: [
        {
          key: 'data-sources',
          icon: <DatabaseOutlined />,
          label: '数据中心'
        },
        {
          key: 'skills',
          icon: <ToolOutlined />,
          label: '能力 / Skills'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '商业系统',
      children: [
        {
          key: 'shop',
          icon: <ShoppingCartOutlined />,
          label: '研究商城'
        },
        {
          key: 'orders',
          icon: <FileTextOutlined />,
          label: '订单'
        },
        {
          key: 'cart',
          icon: <ShoppingCartOutlined />,
          label: '购物车'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '账户运营',
      children: [
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
      ]
    }
  ];

  // 热门股票列表
  const hotStocks = appState.stocks.slice(0, 5);

  return (
    <div className="workspace-sidebar">
      {/* 主导航菜单 */}
      <Menu
        mode="inline"
        selectedKeys={[selectedMenu]}
        items={menuItems}
        onClick={({ key }) => onMenuSelect(key)}
      />

      <div className="sidebar-divider" />

      {/* 热门股票 */}
      <div className="sidebar-section">
        <div className="sidebar-section-title">
          <span>重点跟踪</span>
          <span>Δ%</span>
        </div>
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {hotStocks.map(stock => (
            <div
              key={stock.symbol}
              className="hot-stock-row"
              onClick={() => onStockSelect(stock)}
            >
              <div>
                <Text className="hot-stock-symbol">
                  {stock.symbol}
                </Text>
                <Text className="hot-stock-name">
                  {stock.name}
                </Text>
              </div>
              <Text
                className={stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}
                style={{ fontSize: 12, fontWeight: 700 }}
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
