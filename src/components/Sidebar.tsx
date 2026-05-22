import React from 'react';
import { Menu, Typography, Space } from 'antd';
import {
  DashboardOutlined,
  UserOutlined,
  AuditOutlined,
  FileTextOutlined,
  ExperimentOutlined,
  RobotOutlined,
  DatabaseOutlined,
  FundProjectionScreenOutlined,
  ToolOutlined,
  CalendarOutlined,
  BarChartOutlined,
  ThunderboltOutlined,
  ApiOutlined,
  FolderOpenOutlined,
  GlobalOutlined,
  EyeOutlined,
  PartitionOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { countStocksBySegment } from '../utils/marketSegments';

const { Text } = Typography;

interface SidebarProps {
  selectedMenu: string;
  onMenuSelect: (key: string) => void;
  onMenuPreload?: (key: string) => void;
  onStockSelect: (stock: Stock) => void;
  appState: AppState;
}

const Sidebar: React.FC<SidebarProps> = ({
  selectedMenu,
  onMenuSelect,
  onMenuPreload,
  onStockSelect,
  appState
}) => {
  const segmentStats = countStocksBySegment(appState.stocks);

  const menuLabel = (key: string, label: string) => (
    <span
      onMouseEnter={() => onMenuPreload?.(key)}
      onFocus={() => onMenuPreload?.(key)}
    >
      {label}
    </span>
  );

  const menuItems = [
    {
      type: 'group' as const,
      label: '工作流',
      children: [
        {
          key: 'home',
          icon: <DashboardOutlined />,
          label: menuLabel('home', 'Agent Cockpit')
        },
        {
          key: 'stocks',
          icon: <EyeOutlined />,
          label: menuLabel('stocks', `观察池 ${segmentStats.all}`)
        },
        {
          key: 'research-workbench',
          icon: <FolderOpenOutlined />,
          label: menuLabel('research-workbench', '研报工作台')
        },
        {
          key: 'data-sources',
          icon: <DatabaseOutlined />,
          label: menuLabel('data-sources', '证据库')
        },
        {
          key: 'agent-center',
          icon: <RobotOutlined />,
          label: menuLabel('agent-center', 'Agent 任务')
        }
      ]
    },
    {
      type: 'group' as const,
      label: '决策',
      children: [
        {
          key: 'multi-market-decision',
          icon: <FundProjectionScreenOutlined />,
          label: menuLabel('multi-market-decision', '策略与组合')
        },
        {
          key: 'earnings-calendar',
          icon: <CalendarOutlined />,
          label: menuLabel('earnings-calendar', '事件日历')
        },
        {
          key: 'options-signal',
          icon: <BarChartOutlined />,
          label: menuLabel('options-signal', '期权雷达')
        },
        {
          key: 'realtime-messages',
          icon: <ThunderboltOutlined />,
          label: menuLabel('realtime-messages', '信号流')
        }
      ]
    },
    {
      type: 'group' as const,
      label: '专题',
      children: [
        {
          key: 'ai-research',
          icon: <ExperimentOutlined />,
          label: menuLabel('ai-research', '单标的体检')
        },
        {
          key: 'cn-earnings',
          icon: <FileTextOutlined />,
          label: menuLabel('cn-earnings', 'A股财报')
        },
        {
          key: 'shareholder-changes',
          icon: <AuditOutlined />,
          label: menuLabel('shareholder-changes', '股东变动')
        },
        {
          key: 'major-events',
          icon: <ThunderboltOutlined />,
          label: menuLabel('major-events', '重大事项')
        },
        {
          key: 'ai-supply-chain',
          icon: <PartitionOutlined />,
          label: menuLabel('ai-supply-chain', 'AI 供应链')
        },
        {
          key: 'customs-trade',
          icon: <GlobalOutlined />,
          label: menuLabel('customs-trade', '海关进出口')
        }
      ]
    },
    {
      type: 'group' as const,
      label: '系统',
      children: [
        {
          key: 'mcp-center',
          icon: <ApiOutlined />,
          label: menuLabel('mcp-center', '工具连接')
        },
        {
          key: 'skills',
          icon: <ToolOutlined />,
          label: menuLabel('skills', '技能编排')
        },
        {
          key: 'profile',
          icon: <UserOutlined />,
          label: menuLabel('profile', '系统设置')
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
          <span>核心标的</span>
          <span>Δ%</span>
        </div>
        <Space direction="vertical" size={2} style={{ width: '100%' }}>
          {hotStocks.map(stock => (
            <button
              key={stock.symbol}
              type="button"
              className="hot-stock-row"
              onMouseEnter={() => onMenuPreload?.('stock-community')}
              onFocus={() => onMenuPreload?.('stock-community')}
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
            </button>
          ))}
          {hotStocks.length === 0 && (
            <Text className="sidebar-empty-note">暂无跟踪标的</Text>
          )}
        </Space>
      </div>
    </div>
  );
};

export default Sidebar;
