import React from 'react';
import { AppState, CartItem, Post, Product, Stock, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketDataService';
import { lazyWithPreload } from '../utils/lazyWithPreload';

const StockList = lazyWithPreload(() => import('./StockList'));
const StockDetail = lazyWithPreload(() => import('./StockDetail'));
const StockCommunity = lazyWithPreload(() => import('./StockCommunity'));
const CreatePost = lazyWithPreload(() => import('./CreatePost'));
const PostDetail = lazyWithPreload(() => import('./PostDetail'));
const RechargeHistory = lazyWithPreload(() => import('./RechargeHistory'));
const PlatformBalance = lazyWithPreload(() => import('./PlatformBalance'));
const ProductDetail = lazyWithPreload(() => import('./ProductDetail'));
const Cart = lazyWithPreload(() => import('./Cart'));
const Orders = lazyWithPreload(() => import('./Orders'));
const HomePage = lazyWithPreload(() => import('./HomePage'));
const FinGptHub = lazyWithPreload(() => import('./FinGptHub'));
const InvestorAgentCenter = lazyWithPreload(() => import('./InvestorAgentCenter'));
const DataSourceCenter = lazyWithPreload(() => import('./DataSourceCenter'));
const ResearchWorkbench = lazyWithPreload(() => import('./ResearchWorkbench'));
const RealtimeMessages = lazyWithPreload(() => import('./RealtimeMessages'));
const McpCenter = lazyWithPreload(() => import('./McpCenter'));
const SkillCenter = lazyWithPreload(() => import('./SkillCenter'));
const EarningsCalendar = lazyWithPreload(() => import('./EarningsCalendar'));
const CnEarningsCenter = lazyWithPreload(() => import('./CnEarningsCenter'));
const ShareholderChangeCenter = lazyWithPreload(() => import('./ShareholderChangeCenter'));
const MajorEventCenter = lazyWithPreload(() => import('./MajorEventCenter'));
const ProfileSettings = lazyWithPreload(() => import('./ProfileSettings'));
const MultiMarketDecisionCenter = lazyWithPreload(() => import('./MultiMarketDecisionCenter'));
const AiSupplyChainCycleCenter = lazyWithPreload(() => import('./AiSupplyChainCycleCenter'));
const CustomsTradeCenter = lazyWithPreload(() => import('./CustomsTradeCenter'));
const OptionsSignalCenter = lazyWithPreload(() => import('./OptionsSignalCenter'));

const modulePreloaders: Record<string, Array<() => Promise<unknown>>> = {
  home: [HomePage.preload, StockList.preload],
  stocks: [HomePage.preload, StockList.preload, StockCommunity.preload, StockDetail.preload],
  'a-share-market': [HomePage.preload, StockList.preload, StockCommunity.preload, StockDetail.preload],
  'global-market': [HomePage.preload, StockList.preload, StockCommunity.preload, StockDetail.preload],
  shop: [HomePage.preload, ProductDetail.preload, Cart.preload, Orders.preload],
  profile: [ProfileSettings.preload],
  cart: [Cart.preload, Orders.preload],
  orders: [Orders.preload],
  'ai-research': [FinGptHub.preload],
  'agent-center': [InvestorAgentCenter.preload],
  skills: [SkillCenter.preload],
  'data-sources': [DataSourceCenter.preload],
  'research-workbench': [ResearchWorkbench.preload],
  'realtime-messages': [RealtimeMessages.preload],
  'mcp-center': [McpCenter.preload],
  'earnings-calendar': [EarningsCalendar.preload],
  'cn-earnings': [CnEarningsCenter.preload],
  'shareholder-changes': [ShareholderChangeCenter.preload],
  'major-events': [MajorEventCenter.preload],
  'multi-market-decision': [MultiMarketDecisionCenter.preload],
  'options-signal': [OptionsSignalCenter.preload],
  'ai-supply-chain': [AiSupplyChainCycleCenter.preload],
  'customs-trade': [CustomsTradeCenter.preload],
  'stock-community': [StockCommunity.preload, PostDetail.preload, CreatePost.preload],
  'stock-detail': [StockDetail.preload, StockCommunity.preload],
  'create-post': [CreatePost.preload],
  'post-detail': [PostDetail.preload],
  'product-detail': [ProductDetail.preload]
};

const idlePreloadOrder = [
  'home',
  'stocks',
  'a-share-market',
  'global-market',
  'agent-center',
  'earnings-calendar',
  'ai-supply-chain',
  'customs-trade',
  'options-signal',
  'ai-research',
  'data-sources',
  'realtime-messages',
  'mcp-center',
  'skills',
  'shop',
  'profile'
];

const runPreloaders = async (preloaders: Array<() => Promise<unknown>>, concurrency = 4) => {
  const uniquePreloaders = Array.from(new Set(preloaders));
  let cursor = 0;
  const workerCount = Math.min(concurrency, uniquePreloaders.length);

  await Promise.all(Array.from({ length: workerCount }, async () => {
    while (cursor < uniquePreloaders.length) {
      const preload = uniquePreloaders[cursor];
      cursor += 1;
      try {
        await preload();
      } catch (error) {
        console.warn('Module preload failed:', error);
      }
    }
  }));
};

export function preloadMainContentModules(key: string): Promise<void> {
  const preloaders = modulePreloaders[key] || [];
  return runPreloaders(preloaders);
}

export function preloadCoreWorkspaceModules(): Promise<void> {
  const preloaders = idlePreloadOrder.reduce<Array<() => Promise<unknown>>>((acc, key) => (
    acc.concat(modulePreloaders[key] || [])
  ), []);
  return runPreloaders(preloaders, 3);
}

interface MainContentProps {
  selectedMenu: string;
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
  onBackToStocks: () => void;
  onPostClick: (post: Post) => void;
  onCreatePost: () => void;
  onPurchase: (postId: string, amount: number) => void;
  onRate: (postId: string, rating: number, feedback: string) => void;
  onLike: (postId: string) => void;
  onShare: (postId: string) => void;
  onAddComment: (postId: string, content: string) => void;
  onViewChange: (view: ViewType) => void;
  onSavePost: (post: Partial<Post>) => void;
  isMobile?: boolean;
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
}

const MainContent: React.FC<MainContentProps> = ({
  selectedMenu,
  appState,
  onStockSelect,
  onBackToStocks,
  onPostClick,
  onCreatePost,
  onPurchase,
  onRate,
  onLike,
  onShare,
  onAddComment,
  onViewChange,
  onSavePost,
  isMobile = false,
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
  // 优先处理特殊视图（这些视图优先级最高，直接返回，不进入菜单逻辑）

  // 资产详情视图
  if (appState.currentView === 'product-detail' && appState.selectedProduct) {
    return (
      <ProductDetail
        product={appState.selectedProduct}
        onBack={() => onViewChange('shop')}
        onAddToCart={onAddToCart}
        onBuyNow={onBuyNow}
      />
    );
  }

  // 资产单视图
  if (appState.currentView === 'cart') {
    return (
      <Cart
        cartItems={appState.cart}
        onUpdateQuantity={onUpdateCartQuantity}
        onRemoveItem={onRemoveFromCart}
        onCheckout={onCheckout}
        onBack={() => onViewChange('shop')}
      />
    );
  }

  // 订单视图
  if (appState.currentView === 'orders') {
    return (
      <Orders
        orders={appState.orders}
        onPay={onOrderPay}
        onCancel={onOrderCancel}
        onRefund={onOrderRefund}
      />
    );
  }

  if (appState.currentView === 'ai-research') {
    return <FinGptHub appState={appState} />;
  }

  if (appState.currentView === 'agent-center') {
    return <InvestorAgentCenter appState={appState} />;
  }

  if (appState.currentView === 'skills') {
    return <SkillCenter appState={appState} />;
  }

  if (appState.currentView === 'data-sources') {
    return <DataSourceCenter appState={appState} />;
  }

  if (appState.currentView === 'research-workbench') {
    return <ResearchWorkbench appState={appState} onViewChange={onViewChange} />;
  }

  if (appState.currentView === 'realtime-messages') {
    return <RealtimeMessages appState={appState} />;
  }

  if (appState.currentView === 'mcp-center') {
    return <McpCenter appState={appState} />;
  }

  if (appState.currentView === 'earnings-calendar') {
    return <EarningsCalendar appState={appState} onStockSelect={onStockSelect} />;
  }

  if (appState.currentView === 'cn-earnings') {
    return <CnEarningsCenter appState={appState} onStockSelect={onStockSelect} />;
  }

  if (appState.currentView === 'shareholder-changes') {
    return <ShareholderChangeCenter appState={appState} onStockSelect={onStockSelect} />;
  }

  if (appState.currentView === 'major-events') {
    return <MajorEventCenter appState={appState} onStockSelect={onStockSelect} />;
  }

  if (appState.currentView === 'multi-market-decision') {
    return <MultiMarketDecisionCenter appState={appState} />;
  }

  if (appState.currentView === 'ai-supply-chain') {
    return <AiSupplyChainCycleCenter appState={appState} />;
  }

  if (appState.currentView === 'customs-trade') {
    return <CustomsTradeCenter appState={appState} />;
  }

  if (appState.currentView === 'options-signal') {
    return <OptionsSignalCenter appState={appState} />;
  }

  if (appState.currentView === 'profile') {
    return <ProfileSettings appState={appState} />;
  }

  // 创建帖子视图
  if (appState.currentView === 'create-post') {
    if (!appState.selectedStock) {
      return (
        <StockList
          stocks={appState.stocks}
          onStockSelect={onStockSelect}
          onAddStock={onAddStock}
          onRemoveStock={onRemoveStock}
          onToggleSubscription={onToggleStockSubscription}
          onRefreshMarketData={onRefreshMarketData}
          isMarketDataRefreshing={isMarketDataRefreshing}
        />
      );
    }

    return (
      <CreatePost
        stock={appState.selectedStock}
        onSave={onSavePost}
        onCancel={() => {
          if (appState.selectedStock) {
            onViewChange('stock-community');
          } else {
            onViewChange('stocks');
          }
        }}
      />
    );
  }

  // 帖子详情视图
  if (appState.currentView === 'post-detail' && appState.selectedPost) {
    return (
      <PostDetail
        post={appState.selectedPost}
        currentUser={appState.user!}
        comments={appState.comments}
        purchasedPosts={appState.purchasedPosts}
        onBack={() => {
          if (appState.selectedStock) {
            onViewChange('stock-community');
          } else {
            onViewChange('stocks');
          }
        }}
        onPurchase={onPurchase}
        onRate={onRate}
        onLike={onLike}
        onShare={onShare}
        onAddComment={onAddComment}
      />
    );
  }

  // 股票详情视图
  if (appState.currentView === 'stock-detail' && appState.selectedStock) {
    return (
      <StockDetail
        stock={appState.selectedStock}
        posts={appState.posts}
        comments={appState.comments}
        onBack={onBackToStocks}
        onCreatePost={onCreatePost}
        onPostClick={onPostClick}
      />
    );
  }
  
  // 股票社区视图
  if (appState.currentView === 'stock-community' && appState.selectedStock) {
    return (
      <StockCommunity
        stock={appState.selectedStock}
        posts={appState.posts}
        comments={appState.comments}
        onBack={onBackToStocks}
        onCreatePost={(stock) => {
          onStockSelect(stock);
          onCreatePost();
        }}
        onPostClick={onPostClick}
        onPurchase={(post) => onPurchase(post.id, post.price)}
        onRate={(post, rating) => onRate(post.id, rating, '')}
        onLike={(post) => onLike(post.id)}
        onShare={(post) => onShare(post.id)}
        onViewChange={onViewChange}
      />
    );
  }

  switch (selectedMenu) {
    case 'profile':
      return <ProfileSettings appState={appState} />;
    
    case 'recharge-history':
      return (
        <RechargeHistory
          rechargeHistory={appState.rechargeHistory}
          platformBalance={appState.platformBalance}
        />
      );
    
    case 'platform-balance':
      return (
        <PlatformBalance
          platformBalance={appState.platformBalance}
          totalRecharged={appState.rechargeHistory
            .filter(record => record.status === 'success')
            .reduce((sum, record) => sum + record.amount, 0)}
          totalSpent={appState.payments
            .filter(payment => payment.status === 'completed')
            .reduce((sum, payment) => sum + payment.amount, 0)}
          activeUsers={appState.rechargeHistory
            .filter(record => record.status === 'success')
            .map(record => record.userId)
            .filter((value, index, self) => self.indexOf(value) === index).length}
          totalPosts={appState.posts.length}
          paidPosts={appState.posts.filter(post => post.isPaid).length}
        />
      );
  }

  // 首页视图 - 整合仪表盘、个股专区、商城
  if (
    appState.currentView === 'home' ||
    appState.currentView === 'stocks' ||
    appState.currentView === 'a-share-market' ||
    appState.currentView === 'global-market' ||
    appState.currentView === 'shop' ||
    selectedMenu === 'dashboard' ||
    selectedMenu === 'stocks' ||
    selectedMenu === 'a-share-market' ||
    selectedMenu === 'global-market' ||
    selectedMenu === 'shop' ||
    selectedMenu === 'home'
  ) {
    return (
      <HomePage
        appState={appState}
        onStockSelect={onStockSelect}
        onProductClick={onProductClick}
        onAddToCart={onAddToCart}
        onViewChange={onViewChange}
        onAddStock={onAddStock}
        onRemoveStock={onRemoveStock}
        onToggleStockSubscription={onToggleStockSubscription}
        onRefreshMarketData={onRefreshMarketData}
        isMarketDataRefreshing={isMarketDataRefreshing}
      />
    );
  }

  return (
    <HomePage
      appState={appState}
      onStockSelect={onStockSelect}
      onProductClick={onProductClick}
      onAddToCart={onAddToCart}
      onViewChange={onViewChange}
      onAddStock={onAddStock}
      onRemoveStock={onRemoveStock}
      onToggleStockSubscription={onToggleStockSubscription}
      onRefreshMarketData={onRefreshMarketData}
      isMarketDataRefreshing={isMarketDataRefreshing}
    />
  );
};

export default MainContent;
