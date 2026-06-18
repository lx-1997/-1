import React, { Suspense, startTransition, useCallback, useEffect, useRef, useState } from 'react';
import { throttle } from '../utils/debounce';
import { Layout, Drawer, Spin } from 'antd';
import { AppState, CartItem, Post, Product, Stock, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketService';
import { MENU_TO_DEFAULT_VIEW, STOCK_CONTEXT_MENU_KEYS, VIEW_TO_WORKSPACE_MENU } from '../config/workspaces';
import Sidebar from './Sidebar';
import Header from './Header';
import CommandPalette from './CommandPalette';
import MainContent, { preloadCoreWorkspaceModules, preloadMainContentModules } from './MainContent';

const { Sider, Content } = Layout;

const WorkspaceFallback: React.FC = () => (
  <div className="workspace-loading-state">
    <div className="skeleton-shimmer" style={{ width: 120, height: 6, marginBottom: 4 }} />
    <div className="skeleton-shimmer" style={{ width: 200, height: 8 }} />
  </div>
);

type IdleWindow = Window & typeof globalThis & {
  requestIdleCallback?: (callback: () => void, options?: { timeout: number }) => number;
  cancelIdleCallback?: (handle: number) => void;
};

const scheduleIdleWork = (callback: () => void, timeout = 1800) => {
  if (typeof window === 'undefined') {
    return () => undefined;
  }

  const idleWindow = window as IdleWindow;
  if (idleWindow.requestIdleCallback && idleWindow.cancelIdleCallback) {
    const handle = idleWindow.requestIdleCallback(callback, { timeout });
    return () => idleWindow.cancelIdleCallback?.(handle);
  }

  const handle = window.setTimeout(callback, timeout);
  return () => window.clearTimeout(handle);
};

interface TradingLayoutProps {
  appState: AppState;
  onLogout: () => void;
  onStockSelect: (stock: Stock) => void;
  onBackToStocks: () => void;
  onPostClick: (post: Post) => void;
  onCreatePost: () => void;
  onPurchase: (postId: string, amount: number) => void;
  onRate: (postId: string, rating: number, feedback: string) => void;
  onLike: (postId: string) => void;
  onShare: (postId: string) => void;
  onAddComment: (postId: string, content: string) => void;
  onRecharge: (amount: number, method: string) => void;
  onViewChange: (view: ViewType) => void;
  onSavePost: (post: Partial<Post>) => void;
  // 商城相关
  onProductClick: (product: Product) => void;
  onAddToCart: (product: Product, variantId: string, quantity: number) => void;
  onUpdateCartQuantity: (itemId: string, quantity: number) => void;
  onRemoveFromCart: (itemId: string) => void;
  onCheckout: (items: CartItem[]) => void;
  onOrderPay: (orderId: string, paymentMethod: 'wechat' | 'alipay') => void;
  onOrderCancel: (orderId: string) => void;
  onOrderRefund: (orderId: string) => void;
  onBuyNow: (product: Product, variantId: string, quantity: number) => void;
  onAddStock: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock: (symbol: string) => void;
  onToggleStockSubscription: (symbol: string) => void;
  onRefreshMarketData: () => void;
  isMarketDataRefreshing: boolean;
  isDemoSession?: boolean;
  chatPanelOpen?: boolean;
  onToggleChatPanel?: () => void;
}

const TradingLayout: React.FC<TradingLayoutProps> = ({
  appState,
  onLogout,
  onStockSelect,
  onBackToStocks,
  onPostClick,
  onCreatePost,
  onPurchase,
  onRate,
  onLike,
  onShare,
  onAddComment,
  onRecharge,
  onViewChange,
  onSavePost,
  onProductClick,
  onAddToCart,
  onUpdateCartQuantity,
  onRemoveFromCart,
  onCheckout,
  onOrderPay,
  onOrderCancel,
  onOrderRefund,
  onBuyNow,
  onAddStock,
  onRemoveStock,
  onToggleStockSubscription,
  onRefreshMarketData,
  isMarketDataRefreshing,
  isDemoSession = false,
  chatPanelOpen,
  onToggleChatPanel
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [selectedMenu, setSelectedMenu] = useState('home');
  const [isMobile, setIsMobile] = useState(false);
  const [mobileMenuVisible, setMobileMenuVisible] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const preloadedMenusRef = useRef<Set<string>>(new Set());

  // ⌘K / Ctrl+K 唤起命令面板（Codex / Claude Code 式键盘跳转）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
        e.preventDefault();
        setPaletteOpen(value => !value);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, []);

  // 检测屏幕尺寸
  useEffect(() => {
    const checkIsMobile = throttle(() => {
      setIsMobile(window.innerWidth < 768);
      if (window.innerWidth >= 768) {
        setMobileMenuVisible(false);
      }
    }, 150);

    checkIsMobile();
    window.addEventListener('resize', checkIsMobile);
    return () => {
      window.removeEventListener('resize', checkIsMobile);
      checkIsMobile.cancel?.();
    };
  }, []);

  useEffect(() => {
    const nextMenu = VIEW_TO_WORKSPACE_MENU[appState.currentView];
    if (nextMenu && nextMenu !== selectedMenu) {
      setSelectedMenu(nextMenu);
    }
  }, [appState.currentView, selectedMenu]);

  const preloadMenu = useCallback((key: string) => {
    if (preloadedMenusRef.current.has(key)) {
      return;
    }

    preloadedMenusRef.current.add(key);
    void preloadMainContentModules(key);
  }, []);

  useEffect(() => {
    preloadMenu(appState.currentView);
  }, [appState.currentView, preloadMenu]);

  useEffect(() => scheduleIdleWork(() => {
    void preloadCoreWorkspaceModules();
  }, 3200), []);

  const handleMenuSelect = (key: string) => {
    preloadMenu(key);
    setSelectedMenu(key);
    if (isMobile) {
      setMobileMenuVisible(false);
    }
    
    startTransition(() => {
      // 如果切换到非股票相关菜单，清除选中的股票
      if (!STOCK_CONTEXT_MENU_KEYS.includes(key) && appState.selectedStock) {
        onBackToStocks();
      }

      onViewChange(MENU_TO_DEFAULT_VIEW[key] || 'home');
    });
  };

  const handleHeaderViewChange = (view: ViewType) => {
    preloadMenu(view);
    setSelectedMenu(VIEW_TO_WORKSPACE_MENU[view] || 'home');
    startTransition(() => onViewChange(view));
  };

  const handleStockShortcut = (stock: Stock) => {
    preloadMenu('stock-community');
    setSelectedMenu('stocks');
    if (isMobile) {
      setMobileMenuVisible(false);
    }
    startTransition(() => onStockSelect(stock));
  };

  return (
    <Layout className="trading-layout" style={{ height: '100vh' }}>
      {/* 顶部导航栏 */}
      <Header
        appState={appState}
        onLogout={onLogout}
        onStockSelect={handleStockShortcut}
        onRecharge={onRecharge}
        isMobile={isMobile}
        onMobileMenuToggle={() => setMobileMenuVisible(!mobileMenuVisible)}
        onViewChange={handleHeaderViewChange}
        onRefreshMarketData={onRefreshMarketData}
        isMarketDataRefreshing={isMarketDataRefreshing}
        isDemoSession={isDemoSession}
        chatPanelOpen={chatPanelOpen}
        onToggleChatPanel={onToggleChatPanel}
        onOpenCommandPalette={() => setPaletteOpen(true)}
      />

      <Layout>
        {/* 桌面端侧边栏 */}
        {!isMobile && (
          <Sider
            className="workspace-sider"
            collapsible
            collapsed={collapsed}
            onCollapse={setCollapsed}
            width={240}
            collapsedWidth={80}
          >
            <Sidebar
              selectedMenu={selectedMenu}
              onMenuSelect={handleMenuSelect}
              onMenuPreload={preloadMenu}
              appState={appState}
              onStockSelect={handleStockShortcut}
            />
          </Sider>
        )}

        {/* 移动端抽屉菜单 */}
        {isMobile && (
          <Drawer
            title="菜单"
            placement="left"
            onClose={() => setMobileMenuVisible(false)}
            open={mobileMenuVisible}
            width={280}
            styles={{ body: { padding: 0 } }}
          >
            <Sidebar
              selectedMenu={selectedMenu}
              onMenuSelect={handleMenuSelect}
              onMenuPreload={preloadMenu}
              appState={appState}
              onStockSelect={handleStockShortcut}
            />
          </Drawer>
        )}

        {/* 主内容区域 */}
        <Content style={{ 
          background: 'var(--app-bg)',
          padding: 0,
          minHeight: 'calc(100vh - 52px)',
          overflow: 'auto'
        }} className={`workspace-content workspace-content-${appState.currentView}`}>
          <Suspense fallback={<WorkspaceFallback />}>
            <MainContent
              selectedMenu={selectedMenu}
              appState={appState}
              onStockSelect={onStockSelect}
              onBackToStocks={onBackToStocks}
              onPostClick={onPostClick}
              onCreatePost={onCreatePost}
              onPurchase={onPurchase}
              onRate={onRate}
              onLike={onLike}
              onShare={onShare}
              onAddComment={onAddComment}
              onViewChange={handleHeaderViewChange}
              onSavePost={onSavePost}
              isMobile={isMobile}
              onProductClick={onProductClick}
              onAddToCart={onAddToCart}
              onUpdateCartQuantity={onUpdateCartQuantity}
              onRemoveFromCart={onRemoveFromCart}
              onCheckout={(items) => {
                onCheckout(items);
                setSelectedMenu('orders');
              }}
              onOrderPay={onOrderPay}
              onOrderCancel={onOrderCancel}
              onOrderRefund={onOrderRefund}
              onBuyNow={(product, variantId, quantity) => {
                onBuyNow(product, variantId, quantity);
                setSelectedMenu('orders');
              }}
              onAddStock={onAddStock}
              onRemoveStock={onRemoveStock}
              onToggleStockSubscription={onToggleStockSubscription}
              onRefreshMarketData={onRefreshMarketData}
              isMarketDataRefreshing={isMarketDataRefreshing}
            />
          </Suspense>
        </Content>
      </Layout>

      <CommandPalette
        open={paletteOpen}
        onClose={() => setPaletteOpen(false)}
        onNavigate={handleHeaderViewChange}
        onSelectStock={handleStockShortcut}
        stocks={appState.stocks}
      />
    </Layout>
  );
};

const arePropsEqual = (prev: TradingLayoutProps, next: TradingLayoutProps): boolean => {
  return (
    prev.appState.currentView === next.appState.currentView &&
    prev.appState.stocks === next.appState.stocks &&
    prev.appState.user === next.appState.user &&
    prev.appState.selectedStock === next.appState.selectedStock &&
    prev.appState.selectedPost === next.appState.selectedPost &&
    prev.appState.posts === next.appState.posts &&
    prev.appState.cart === next.appState.cart &&
    prev.appState.orders === next.appState.orders &&
    prev.appState.isLoading === next.appState.isLoading &&
    prev.appState.platformBalance === next.appState.platformBalance &&
    prev.appState.selectedProduct === next.appState.selectedProduct &&
    prev.isMarketDataRefreshing === next.isMarketDataRefreshing &&
    prev.isDemoSession === next.isDemoSession
  );
};

export default React.memo(TradingLayout, arePropsEqual);
