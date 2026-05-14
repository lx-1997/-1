import React, { useState, useEffect } from 'react';
import { Layout, Drawer } from 'antd';
import { AppState, Post, Stock, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketDataService';
import Sidebar from './Sidebar';
import Header from './Header';
import MainContent from './MainContent';

const { Sider, Content } = Layout;

interface TradingLayoutProps {
  appState: AppState;
  onLogout: () => void;
  onStockSelect: (stock: any) => void;
  onBackToStocks: () => void;
  onPostClick: (post: any) => void;
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
  onProductClick: (product: any) => void;
  onAddToCart: (product: any, variantId: string, quantity: number) => void;
  onUpdateCartQuantity: (itemId: string, quantity: number) => void;
  onRemoveFromCart: (itemId: string) => void;
  onCheckout: (items: any[]) => void;
  onOrderPay: (orderId: string, paymentMethod: 'wechat' | 'alipay') => void;
  onOrderCancel: (orderId: string) => void;
  onOrderRefund: (orderId: string) => void;
  onBuyNow: (product: any, variantId: string, quantity: number) => void;
  onAddStock: (candidate: MarketSymbolCandidate) => Promise<void> | void;
  onRemoveStock: (symbol: string) => void;
  onToggleStockSubscription: (symbol: string) => void;
  onRefreshMarketData: () => void;
  isMarketDataRefreshing: boolean;
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
  isMarketDataRefreshing
}) => {
  const [collapsed, setCollapsed] = useState(false);
  const [selectedMenu, setSelectedMenu] = useState('home');
  const [isMobile, setIsMobile] = useState(false);
  const [mobileMenuVisible, setMobileMenuVisible] = useState(false);

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

  const handleMenuSelect = (key: string) => {
    setSelectedMenu(key);
    if (isMobile) {
      setMobileMenuVisible(false);
    }
    
    // 如果切换到非股票相关菜单，清除选中的股票
    if (key !== 'home' && appState.selectedStock) {
      onBackToStocks();
    }
    
    // 根据菜单项切换视图，确保所有菜单项都能正确跳转
    switch (key) {
      case 'home':
        onViewChange('home');
        break;
      case 'stocks':
        onViewChange('stocks');
        break;
      case 'shop':
        onViewChange('shop');
        break;
      case 'profile':
        onViewChange('profile');
        break;
      case 'cart':
        onViewChange('cart');
        break;
      case 'orders':
        onViewChange('orders');
        break;
      case 'ai-research':
        onViewChange('ai-research');
        break;
      case 'agent-center':
        onViewChange('agent-center');
        break;
      case 'skills':
        onViewChange('skills');
        break;
      case 'data-sources':
        onViewChange('data-sources');
        break;
      case 'research-workbench':
        onViewChange('research-workbench');
        break;
      case 'realtime-messages':
        onViewChange('realtime-messages');
        break;
      case 'mcp-center':
        onViewChange('mcp-center');
        break;
      case 'earnings-calendar':
        onViewChange('earnings-calendar');
        break;
      case 'recharge-history':
      case 'platform-balance':
        onViewChange('home');
        break;
      default:
        onViewChange('home');
        break;
    }
  };

  const handleHeaderViewChange = (view: ViewType) => {
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
      profile: 'profile',
      home: 'home',
      stocks: 'stocks',
      shop: 'shop'
    };

    setSelectedMenu(menuByView[view] || 'home');
    onViewChange(view);
  };

  const handleStockShortcut = (stock: Stock) => {
    setSelectedMenu('stocks');
    if (isMobile) {
      setMobileMenuVisible(false);
    }
    onStockSelect(stock);
  };

  return (
    <Layout style={{ height: '100vh' }}>
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
      />

      <Layout>
        {/* 桌面端侧边栏 */}
        {!isMobile && (
          <Sider
            collapsible
            collapsed={collapsed}
            onCollapse={setCollapsed}
            width={240}
            collapsedWidth={80}
            style={{
              background: '#172026',
              borderRight: '1px solid rgba(255,255,255,0.08)'
            }}
          >
            <Sidebar
              selectedMenu={selectedMenu}
              onMenuSelect={handleMenuSelect}
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
              appState={appState}
              onStockSelect={handleStockShortcut}
            />
          </Drawer>
        )}

        {/* 主内容区域 */}
        <Content style={{ 
          background: '#eef2f5',
          padding: 0,
          minHeight: 'calc(100vh - 58px)',
          overflow: 'auto'
        }}>
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
        </Content>
      </Layout>
    </Layout>
  );
};

export default TradingLayout;
