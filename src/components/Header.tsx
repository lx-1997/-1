import React, { useEffect, useState } from 'react';
import { Layout, Input, Button, Dropdown, Space, Avatar, Badge, Typography, message, Tooltip } from 'antd';
import {
  SearchOutlined,
  BellOutlined,
  UserOutlined,
  FireOutlined,
  MenuOutlined,
  DollarOutlined,
  LogoutOutlined,
  SettingOutlined,
  SyncOutlined,
  DatabaseOutlined,
  RobotOutlined
} from '@ant-design/icons';
import { AppState, Stock, ViewType } from '../types';
import RechargeModal from './RechargeModal';
import { formatQuoteSourceLine } from '../utils/marketData';
import { SystemReadiness, getSystemReadiness } from '../services/systemHealthService';
import { countStocksBySegment } from '../utils/marketSegments';

const { Header: AntHeader } = Layout;
const { Text } = Typography;

interface HeaderProps {
  appState: AppState;
  onLogout: () => void;
  onStockSelect: (stock: Stock) => void;
  onRecharge: (amount: number, method: string) => void;
  isMobile?: boolean;
  onMobileMenuToggle?: () => void;
  onViewChange?: (view: ViewType) => void;
  onRefreshMarketData?: () => void;
  isMarketDataRefreshing?: boolean;
  isDemoSession?: boolean;
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
  isMarketDataRefreshing = false,
  isDemoSession = false
}) => {
  const [searchValue, setSearchValue] = useState('');
  const [rechargeModalVisible, setRechargeModalVisible] = useState(false);
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null);
  const quoteAnchor = appState.stocks.find(stock => stock.quoteProvider && stock.quoteProvider !== 'mock')
    || appState.stocks[0];
  const connectedQuoteCount = appState.stocks.filter(stock => stock.quoteProvider && stock.quoteProvider !== 'mock').length;
  const marketSegmentCounts = countStocksBySegment(appState.stocks);
  const readinessTone = readiness?.status === 'ready' ? 'ready' : readiness?.status === 'degraded' ? 'degraded' : 'not-ready';
  const readinessTooltip = readiness
    ? [
        `系统就绪度 ${readiness.score}/100`,
        readiness.blockers.length ? `阻塞：${readiness.blockers.join('、')}` : '',
        readiness.warnings.length ? `提醒：${readiness.warnings.join('、')}` : ''
      ].filter(Boolean).join('\n')
    : '系统就绪度待检查';

  useEffect(() => {
    let mounted = true;

    const refreshReadiness = async () => {
      try {
        const nextReadiness = await getSystemReadiness();
        if (mounted) {
          setReadiness(nextReadiness);
        }
      } catch {
        if (mounted) {
          setReadiness(null);
        }
      }
    };

    void refreshReadiness();
    const timer = window.setInterval(refreshReadiness, 60000);
    return () => {
      mounted = false;
      window.clearInterval(timer);
    };
  }, []);

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
      label: '系统设置'
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
          <span className="brand-subtitle">Agent Workspace</span>
        </div>
      </div>

      {/* 搜索框 - 移动端隐藏 */}
      {!isMobile && (
        <div className="header-search">
          <Input.Search
            placeholder="搜索标的，加入 Agent 上下文"
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
        <div className="header-status-cluster">
          <Button
            size="small"
            icon={<RobotOutlined />}
            onClick={() => onViewChange?.('home')}
            className="header-agent-button"
          >
            Agent
          </Button>
          <Tooltip title={`A股 ${marketSegmentCounts.aShare} · 港美 ${marketSegmentCounts.global}`}>
            <span className="market-pill">
              观察 <strong>{appState.stocks.length}</strong>
            </span>
          </Tooltip>
          <Tooltip title={connectedQuoteCount > 0 ? `${connectedQuoteCount} 个外部行情源已接入` : '当前使用样例行情，可刷新或配置行情源'}>
            <span className="market-pill">
              <DatabaseOutlined />
              <strong>{connectedQuoteCount > 0 ? `${connectedQuoteCount} 源` : '样例'}</strong>
            </span>
          </Tooltip>
          <span className="market-pill">
            证据 <strong>{appState.posts.length}</strong>
          </span>
          {isDemoSession && (
            <span className="market-pill demo-mode-pill">
              演示会话
            </span>
          )}
          {readiness && (
            <Tooltip title={<span style={{ whiteSpace: 'pre-line' }}>{readinessTooltip}</span>}>
              <span className={`market-pill readiness-pill ${readinessTone}`}>
                就绪 <strong>{readiness.score}</strong>
              </span>
            </Tooltip>
          )}
          <Tooltip title={quoteAnchor ? formatQuoteSourceLine(quoteAnchor) : '行情待刷新'}>
            <Button
              size="small"
              icon={<SyncOutlined spin={isMarketDataRefreshing} />}
              loading={isMarketDataRefreshing}
              onClick={onRefreshMarketData}
              aria-label="刷新行情"
            >
              刷新
            </Button>
          </Tooltip>
          <Button
            type="primary"
            size="small"
            icon={<DollarOutlined />}
            onClick={() => setRechargeModalVisible(true)}
          >
            额度
          </Button>
        </div>
      )}

      {/* 通知和用户菜单 */}
      <Space className="header-actions" size={isMobile ? 'small' : 'small'}>
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
                onViewChange?.('profile');
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
