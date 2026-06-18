import React from 'react';
import { useT } from '../i18n/useT';
import {
  DashboardOutlined,
  RobotOutlined,
  DatabaseOutlined,
  FundProjectionScreenOutlined,
  ToolOutlined,
  EyeOutlined,
  SafetyCertificateOutlined,
  FormOutlined,
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import { WORKSPACE_SECTIONS, WorkspaceGroup, WorkspaceId } from '../config/workspaces';
import { countStocksBySegment } from '../utils/marketSegments';
import './Sidebar.css';

const workspaceIcons: Record<WorkspaceId, React.ReactNode> = {
  research: <RobotOutlined />,
  observe: <EyeOutlined />,
  equity: <DashboardOutlined />,
  evidence: <DatabaseOutlined />,
  strategy: <FundProjectionScreenOutlined />,
  risk: <SafetyCertificateOutlined />,
  system: <ToolOutlined />,
};

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
  const t = useT();
  const [pinnedOpen, setPinnedOpen] = React.useState(true);
  const segmentStats = countStocksBySegment(appState.stocks);
  const hotStocks = appState.stocks.slice(0, 5);

  const renderGroup = (group: WorkspaceGroup) => (
    WORKSPACE_SECTIONS
      .filter(section => section.group === group)
      .map(section => {
        const active = selectedMenu === section.menuKey;
        const count = section.id === 'observe' ? segmentStats.all : undefined;
        return (
          <button
            key={section.menuKey}
            type="button"
            className={`dfx-sidebar-item${active ? ' active' : ''}`}
            title={t(section.sidebarLabelKey)}
            onMouseEnter={() => onMenuPreload?.(section.menuKey)}
            onFocus={() => onMenuPreload?.(section.menuKey)}
            onClick={() => onMenuSelect(section.menuKey)}
          >
            <span className="dfx-sidebar-item-icon">{workspaceIcons[section.id]}</span>
            <span className="dfx-sidebar-item-label">{t(section.sidebarLabelKey)}</span>
            {count !== undefined && <span className="dfx-sidebar-item-count">{count}</span>}
          </button>
        );
      })
  );

  return (
    <div className="dfx-sidebar">
      <button
        type="button"
        className="dfx-sidebar-new"
        title={t('sidebar.research')}
        onMouseEnter={() => onMenuPreload?.('home')}
        onClick={() => onMenuSelect('home')}
      >
        <FormOutlined />
        <span className="dfx-sidebar-new-label">新对话</span>
      </button>

      <nav className="dfx-sidebar-nav">
        <div className="dfx-sidebar-group-label">{t('sidebar.workspace')}</div>
        {renderGroup('workspace')}
        <div className="dfx-sidebar-group-label">{t('sidebar.system')}</div>
        {renderGroup('system')}
      </nav>

      <div className="dfx-sidebar-pinned">
        <button
          type="button"
          className="dfx-sidebar-group-label dfx-sidebar-pinned-toggle"
          onClick={() => setPinnedOpen(v => !v)}
          aria-expanded={pinnedOpen}
        >
          <span>{t('sidebar.coreStocks')}</span>
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
          <div className="dfx-sidebar-empty">{t('sidebar.noStocks')}</div>
        )}
      </div>
    </div>
  );
};

export default React.memo(Sidebar);
