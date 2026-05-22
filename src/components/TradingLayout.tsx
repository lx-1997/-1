import React, { Suspense, startTransition, useCallback, useEffect, useRef, useState } from 'react';
import { Layout, Drawer, Spin } from 'antd';
import { AppState, CartItem, Post, Product, Stock, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketDataService';
import Sidebar from './Sidebar';
import Header from './Header';
import MainContent, { preloadCoreWorkspaceModules, preloadMainContentModules } from './MainContent';
import { getMarketSegmentForStock } from '../utils/marketSegments';

const { Sider, Content } = Layout;

const menuByView: Partial<Record<ViewType, string>> = {
  cart: 'cart',
  orders: 'orders',
  'ai-research': 'ai-research',
  'agent-center': 'agent-center',
  skills: 'skills',
  'data-sources': 'data-sources',
  'research-workbench': 'research-workbench',
  'realtime-messages': 'realtime-messages',
  'mcp-center': 'mcp-center',
  'earnings-calendar': 'earnings-calendar',
  'cn-earnings': 'cn-earnings',
  'shareholder-changes': 'shareholder-changes',
  'major-events': 'major-events',
  'multi-market-decision': 'multi-market-decision',
  'options-signal': 'options-signal',
  'ai-supply-chain': 'ai-supply-chain',
  'customs-trade': 'customs-trade',
  profile: 'profile',
  home: 'home',
  stocks: 'stocks',
  'a-share-market': 'a-share-market',
  'global-market': 'global-market',
  shop: 'shop'
};

const viewByMenu: Partial<Record<string, ViewType>> = {
  home: 'home',
  stocks: 'stocks',
  'a-share-market': 'a-share-market',
  'global-market': 'global-market',
  shop: 'shop',
  profile: 'profile',
  cart: 'cart',
  orders: 'orders',
  'ai-research': 'ai-research',
  'agent-center': 'agent-center',
  skills: 'skills',
  'data-sources': 'data-sources',
  'research-workbench': 'research-workbench',
  'realtime-messages': 'realtime-messages',
  'mcp-center': 'mcp-center',
  'earnings-calendar': 'earnings-calendar',
  'cn-earnings': 'cn-earnings',
  'shareholder-changes': 'shareholder-changes',
  'major-events': 'major-events',
  'multi-market-decision': 'multi-market-decision',
  'options-signal': 'options-signal',
  'ai-supply-chain': 'ai-supply-chain',
  'customs-trade': 'customs-trade'
};

const WorkspaceFallback: React.FC = () => (
  <div className="workspace-loading-state">
    <Spin size="large" />
    <span>正在加载工作台...</span>
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
  isDemoSession = false
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [selectedMenu, setSelectedMenu] = useState('home');
  const [isMobile, setIsMobile] = useState(false);
  const [mobileMenuVisible, setMobileMenuVisible] = useState(false);
  const preloadedMenusRef = useRef<Set<string>>(new Set());

  // 检测屏幕尺寸
  useEffect(() => {
    const checkIsMobile = () => {
      setIsMobile(window.innerWidth < 768);
      if (window.innerWidth >= 768) {
        setMobileMenuVisible(false);
      }
    };

    checkIsMobile();
    window.addEventListener('resize', checkIsMobile);
    return () => window.removeEventListener('resize', checkIsMobile);
  }, []);

  useEffect(() => {
    const nextMenu = menuByView[appState.currentView];
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
  }), []);

  const handleMenuSelect = (key: string) => {
    preloadMenu(key);
    setSelectedMenu(key);
    if (isMobile) {
      setMobileMenuVisible(false);
    }
    
    startTransition(() => {
      // 如果切换到非股票相关菜单，清除选中的股票
      if (key !== 'home' && appState.selectedStock) {
        onBackToStocks();
      }

      onViewChange(viewByMenu[key] || 'home');
    });
  };

  const handleHeaderViewChange = (view: ViewType) => {
    preloadMenu(view);
    setSelectedMenu(menuByView[view] || 'home');
    startTransition(() => onViewChange(view));
  };

  const handleStockShortcut = (stock: Stock) => {
    preloadMenu('stock-community');
    setSelectedMenu(getMarketSegmentForStock(stock) === 'a-share' ? 'a-share-market' : 'global-market');
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
          minHeight: 'calc(100vh - 58px)',
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
    </Layout>
  );
};

export default TradingLayout;
