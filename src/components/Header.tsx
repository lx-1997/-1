import React, { useEffect, useState } from 'react';
import { Layout, Input, Button, Dropdown, Space, Avatar, Typography, message, Tooltip } from 'antd';
import {
  SearchOutlined,
  BellOutlined,
  UserOutlined,
  MenuOutlined,
  DollarOutlined,
  LogoutOutlined,
  SettingOutlined,
  SyncOutlined,
  DatabaseOutlined,
  ThunderboltOutlined,
  SunOutlined,
  MoonOutlined,
  MoreOutlined,
  RobotOutlined,
  ShopOutlined,
  ProfileOutlined
} from '@ant-design/icons';
import { AppState, Stock, ViewType } from '../types';
import RechargeModal from './RechargeModal';
import { SystemReadiness, getSystemReadiness } from '../services/infrastructureService';
import { countStocksBySegment } from '../utils/marketSegments';
import { useTheme } from '../context/ThemeContext';
import { useT } from '../i18n/useT';
import './Header.css';

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
  chatPanelOpen?: boolean;
  onToggleChatPanel?: () => void;
  onOpenCommandPalette?: () => void;
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
  isDemoSession = false,
  chatPanelOpen,
  onToggleChatPanel,
  onOpenCommandPalette
}) => {
  const t = useT();
  const [searchValue, setSearchValue] = useState('');
  const [rechargeModalVisible, setRechargeModalVisible] = useState(false);
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null);
  const { theme, toggleTheme } = useTheme();
  const connectedQuoteCount = appState.stocks.filter(stock => stock.quoteProvider && stock.quoteProvider !== 'mock').length;
  const marketSegmentCounts = countStocksBySegment(appState.stocks);
  const readinessTone = readiness?.status === 'ready' ? 'ready' : readiness?.status === 'degraded' ? 'degraded' : 'not-ready';

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
      key: 'balance',
      icon: <DollarOutlined />,
      label: (
        <span className="dfx-hd-balance">
          <small>账户余额 · 点击充值</small>
          <strong>${(appState.user?.balance ?? 0).toFixed(2)}</strong>
        </span>
      )
    },
    {
      type: 'divider' as const
    },
    {
      key: 'shop',
      icon: <ShopOutlined />,
      label: '研究商城'
    },
    {
      key: 'orders',
      icon: <ProfileOutlined />,
      label: '我的订单'
    },
    {
      type: 'divider' as const
    },
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

  const statusPanel = (
    <div className="dfx-hd-status-panel">
      <div className="dfx-hd-row">
        <span className="dfx-hd-row-label"><DatabaseOutlined />观察标的</span>
        <span className="dfx-hd-row-value">
          {appState.stocks.length}
          <Text type="secondary" style={{ fontSize: 11, fontWeight: 400 }}>
            A股 {marketSegmentCounts.aShare} · 港美 {marketSegmentCounts.global}
          </Text>
        </span>
      </div>
      <div className="dfx-hd-row">
        <span className="dfx-hd-row-label"><DatabaseOutlined />行情源</span>
        <span className="dfx-hd-row-value">
          {connectedQuoteCount > 0 ? `${connectedQuoteCount} 个已接入` : '样例数据'}
          <Button
            size="small"
            type="text"
            icon={<SyncOutlined spin={isMarketDataRefreshing} />}
            onClick={onRefreshMarketData}
            aria-label="刷新行情"
            style={{ color: 'var(--text-muted)' }}
          />
        </span>
      </div>
      <div className="dfx-hd-row">
        <span className="dfx-hd-row-label">
          <span className={`dfx-hd-dot ${readinessTone}`} />系统就绪度
        </span>
        <span className="dfx-hd-row-value">{readiness ? `${readiness.score}/100` : '待检查'}</span>
      </div>
      {isDemoSession && (
        <div className="dfx-hd-row">
          <span className="dfx-hd-row-label">运行模式</span>
          <span className="dfx-hd-row-value">演示会话</span>
        </div>
      )}
      {readiness?.blockers?.length ? (
        <div className="dfx-hd-note block">阻塞：{readiness.blockers.join('、')}</div>
      ) : null}
      {readiness?.warnings?.length ? (
        <div className="dfx-hd-note warn">提醒：{readiness.warnings.join('、')}</div>
      ) : null}
    </div>
  );

  return (
    <AntHeader className="terminal-header">
      {isMobile && (
        <Button
          type="text"
          icon={<MenuOutlined />}
          onClick={onMobileMenuToggle}
          style={{ color: 'var(--text)', fontSize: 18 }}
        />
      )}

      <div className="terminal-brand">
        <span className="brand-mark">
          <ThunderboltOutlined />
        </span>
        <div className="brand-copy">
          <span className="brand-title">{t('app.title')}</span>
          <span className="brand-subtitle">{t('app.subtitle')}</span>
        </div>
      </div>

      {!isMobile && (
        <div className="header-search">
          <Input
            placeholder={t('header.search')}
            value={searchValue}
            onChange={(e) => setSearchValue(e.target.value)}
            onPressEnter={() => handleSearch(searchValue)}
            prefix={<SearchOutlined style={{ color: 'var(--text-soft)' }} />}
            allowClear
            size="middle"
          />
        </div>
      )}

      <Space className="header-actions" size={4}>
        <Tooltip title={chatPanelOpen ? '关闭 AI 助手' : 'AI 助手'}>
          <Button
            type={chatPanelOpen ? 'primary' : 'text'}
            icon={<RobotOutlined />}
            size="middle"
            onClick={onToggleChatPanel}
            aria-label="AI 助手"
            style={{ color: chatPanelOpen ? undefined : 'var(--text-muted)' }}
          />
        </Tooltip>
        <Dropdown
          placement="bottomRight"
          trigger={['click']}
          dropdownRender={() => (
            <div
              className="dfx-hd-more-pop"
              style={{
                background: 'var(--surface)',
                border: '1px solid var(--border)',
                borderRadius: 12,
                boxShadow: '0 10px 30px rgba(0, 0, 0, 0.22)',
                padding: 10,
                minWidth: 264,
              }}
            >
              {statusPanel}
              <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginTop: 8, paddingTop: 8, borderTop: '1px solid var(--border)' }}>
                {!isMobile && (
                  <Button type="text" block icon={<SearchOutlined />} onClick={onOpenCommandPalette} style={{ justifyContent: 'flex-start', textAlign: 'left', color: 'var(--text-muted)' }}>
                    命令面板 ⌘K
                  </Button>
                )}
                <Button type="text" block icon={<BellOutlined />} onClick={() => onViewChange?.('realtime-messages')} style={{ justifyContent: 'flex-start', textAlign: 'left', color: 'var(--text-muted)' }}>
                  信号流
                </Button>
                <Button type="text" block icon={theme === 'dark' ? <SunOutlined /> : <MoonOutlined />} onClick={toggleTheme} style={{ justifyContent: 'flex-start', textAlign: 'left', color: 'var(--text-muted)' }}>
                  {theme === 'dark' ? '浅色模式' : '深色模式'}
                </Button>
              </div>
            </div>
          )}
        >
          <Button type="text" icon={<MoreOutlined />} size="middle" aria-label="更多" style={{ color: 'var(--text-muted)' }} />
        </Dropdown>

        <Dropdown
          menu={{
            items: userMenuItems,
            onClick: ({ key }) => {
              if (key === 'logout') {
                onLogout();
                return;
              }
              if (key === 'balance') {
                setRechargeModalVisible(true);
                return;
              }
              if (key === 'shop' || key === 'orders') {
                onViewChange?.(key);
                return;
              }
              if (key === 'profile' || key === 'settings') {
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
              height: 36,
              display: 'flex',
              alignItems: 'center',
              gap: isMobile ? 4 : 8,
              padding: isMobile ? '0 4px' : '0 8px',
              color: 'var(--text)'
            }}
          >
            <Avatar
              size={28}
              src={appState.user?.avatar}
              icon={<UserOutlined />}
              style={{ backgroundColor: 'var(--accent)' }}
            />
            {!isMobile && <Text style={{ color: 'var(--text)', fontSize: 13 }}>{appState.user?.username}</Text>}
          </Button>
        </Dropdown>
      </Space>

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

export default React.memo(Header);