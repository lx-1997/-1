import React from 'react';
import {
  DashboardOutlined,
  BarChartOutlined,
  RobotOutlined,
  DatabaseOutlined,
  FundProjectionScreenOutlined,
  ToolOutlined,
  EyeOutlined,
  SafetyCertificateOutlined,
  FormOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { countStocksBySegment } from '../utils/marketSegments';
import './Sidebar.css';

const PRIMARY_NAV = [
  { key: 'home', label: '首页', icon: <DashboardOutlined /> },
  { key: 'stocks', label: '我的股票', icon: <EyeOutlined />, count: true },
  { key: 'premarket-opportunity', label: '发现机会', icon: <ThunderboltOutlined /> },
];

const ADVANCED_NAV = [
  { key: 'ai-research', label: '深度分析', icon: <RobotOutlined /> },
  { key: 'financial-terminal', label: '市场数据', icon: <DashboardOutlined /> },
  { key: 'risk-dashboard', label: '组合与风险', icon: <SafetyCertificateOutlined /> },
  { key: 'quant-lab', label: 'QuantLab 量化', icon: <BarChartOutlined /> },
  { key: 'data-sources', label: '资料与来源', icon: <DatabaseOutlined /> },
  { key: 'multi-market-decision', label: '策略与回测', icon: <FundProjectionScreenOutlined /> },
  { key: 'profile', label: '设置', icon: <ToolOutlined /> },
];

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
  appState,
}) => {
  const [pinnedOpen, setPinnedOpen] = React.useState(true);
  const [toolsOpen, setToolsOpen] = React.useState(() => !PRIMARY_NAV.some(item => item.key === selectedMenu));
  const segmentStats = countStocksBySegment(appState.stocks);
  const hotStocks = appState.stocks.slice(0, 5);

  React.useEffect(() => {
    if (!PRIMARY_NAV.some(item => item.key === selectedMenu)) setToolsOpen(true);
  }, [selectedMenu]);

  const renderItem = (item: { key: string; label: string; icon: React.ReactNode; count?: boolean }) => (
    <button
      key={item.key}
      type="button"
      className={`dfx-sidebar-item${selectedMenu === item.key ? ' active' : ''}`}
      title={item.label}
      onMouseEnter={() => onMenuPreload?.(item.key)}
      onFocus={() => onMenuPreload?.(item.key)}
      onClick={() => onMenuSelect(item.key)}
    >
      <span className="dfx-sidebar-item-icon">{item.icon}</span>
      <span className="dfx-sidebar-item-label">{item.label}</span>
      {item.count && <span className="dfx-sidebar-item-count">{segmentStats.all}</span>}
    </button>
  );

  return (
    <div className="dfx-sidebar">
      <button
        type="button"
        className="dfx-sidebar-new"
        title="问 AI"
        onMouseEnter={() => onMenuPreload?.('home')}
        onClick={() => onMenuSelect('home')}
      >
        <FormOutlined />
        <span className="dfx-sidebar-new-label">问 AI</span>
      </button>

      <nav className="dfx-sidebar-nav">
        <div className="dfx-sidebar-group-label">开始</div>
        {PRIMARY_NAV.map(renderItem)}
        <button
          type="button"
          className={`dfx-sidebar-tools-toggle${toolsOpen ? ' open' : ''}`}
          onClick={() => setToolsOpen(value => !value)}
          aria-expanded={toolsOpen}
        >
          <ToolOutlined />
          <span>专业工具</span>
          <span className="dfx-sidebar-pinned-caret">›</span>
        </button>
        {toolsOpen && <div className="dfx-sidebar-tools-list">{ADVANCED_NAV.map(renderItem)}</div>}
      </nav>

      <div className="dfx-sidebar-pinned">
        <button
          type="button"
          className="dfx-sidebar-group-label dfx-sidebar-pinned-toggle"
          onClick={() => setPinnedOpen(v => !v)}
          aria-expanded={pinnedOpen}
        >
          <span>我的自选</span>
          <span className={`dfx-sidebar-pinned-caret${pinnedOpen ? ' open' : ''}`}>›</span>
        </button>
        {pinnedOpen && hotStocks.map(stock => (
          <button
            key={stock.symbol}
            type="button"
            className="dfx-sidebar-stock"
            onMouseEnter={() => onMenuPreload?.('stock-community')}
            onFocus={() => onMenuPreload?.('stock-community')}
            onClick={() => onStockSelect(stock)}
          >
            <span className="dfx-sidebar-stock-copy">
              <span className="dfx-sidebar-stock-sym">{stock.symbol}</span>
              <span className="dfx-sidebar-stock-name">{stock.name}</span>
            </span>
            <span className={`dfx-sidebar-stock-chg ${stock.changePercent >= 0 ? 'quote-positive' : 'quote-negative'}`}>
              {stock.changePercent >= 0 ? '+' : ''}{stock.changePercent.toFixed(2)}%
            </span>
          </button>
        ))}
        {pinnedOpen && hotStocks.length === 0 && (
          <div className="dfx-sidebar-empty">还没有关注股票</div>
        )}
      </div>
    </div>
  );
};

export default React.memo(Sidebar);
