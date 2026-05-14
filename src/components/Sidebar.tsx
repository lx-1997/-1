import React from 'react';
import { Menu, Typography, Space } from 'antd';
import {
  DashboardOutlined,
  UserOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  RobotOutlined,
  DatabaseOutlined,
  ToolOutlined,
  CalendarOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  FolderOpenOutlined
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
      label: '今日工作流',
      children: [
        {
          key: 'home',
          icon: <DashboardOutlined />,
          label: '信息雷达'
        },
        {
          key: 'ai-research',
          icon: <ExperimentOutlined />,
          label: 'AI 投研体检'
        },
        {
          key: 'agent-center',
          icon: <RobotOutlined />,
          label: '报告工厂'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '个股与宏观',
      children: [
        {
          key: 'stocks',
          icon: <DashboardOutlined />,
          label: '个股池'
        },
        {
          key: 'earnings-calendar',
          icon: <CalendarOutlined />,
          label: '事件日历'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '证据与连接',
      children: [
        {
          key: 'realtime-messages',
          icon: <ThunderboltOutlined />,
          label: '新闻 / 消息流'
        },
        {
          key: 'data-sources',
          icon: <DatabaseOutlined />,
          label: '数据源与研报'
        },
        {
          key: 'research-workbench',
          icon: <FolderOpenOutlined />,
          label: '研报工作台'
        },
        {
          key: 'mcp-center',
          icon: <ApiOutlined />,
          label: '外部工具连接'
        },
        {
          key: 'skills',
          icon: <ToolOutlined />,
          label: '自定义 Skills'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '研究订阅',
      children: [
        {
          key: 'shop',
          icon: <FileTextOutlined />,
          label: '研报 / 模板库'
        }
      ]
    },
    {
      type: 'group' as const,
      label: '账户',
      children: [
        {
          key: 'profile',
          icon: <UserOutlined />,
          label: '个人设置'
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
