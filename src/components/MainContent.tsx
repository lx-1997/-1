import React from 'react';
import { AppState, Post, ViewType } from '../types';
import StockList from './StockList';
import StockDetail from './StockDetail';
import StockCommunity from './StockCommunity';
import ExpertClub from './ExpertClub';
import CreatePost from './CreatePost';
import PostDetail from './PostDetail';
import RechargeHistory from './RechargeHistory';
import PlatformBalance from './PlatformBalance';
import ProductDetail from './ProductDetail';
import Cart from './Cart';
import Orders from './Orders';
import HomePage from './HomePage';
import FinGptHub from './FinGptHub';
import InvestorAgentCenter from './InvestorAgentCenter';
import DataSourceCenter from './DataSourceCenter';
import SkillCenter from './SkillCenter';
import EarningsCalendar from './EarningsCalendar';

interface MainContentProps {
  selectedMenu: string;
  appState: AppState;
  onStockSelect: (stock: any) => void;
  onBackToStocks: () => void;
  onPostClick: (post: any) => void;
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
  onProductClick: (product: any) => void;
  onAddToCart: (product: any, variantId: string, quantity: number) => void;
  onUpdateCartQuantity: (itemId: string, quantity: number) => void;
  onRemoveFromCart: (itemId: string) => void;
  onCheckout: (items: any[]) => void;
  onOrderPay: (orderId: string, paymentMethod: 'wechat' | 'alipay') => void;
  onOrderCancel: (orderId: string) => void;
  onOrderRefund: (orderId: string) => void;
  onBuyNow: (product: any, variantId: string, quantity: number) => void;
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
  onBuyNow
}) => {
  // 优先处理特殊视图（这些视图优先级最高，直接返回，不进入菜单逻辑）

  // 商品详情视图
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

  // 购物车视图
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

  if (appState.currentView === 'earnings-calendar') {
    return <EarningsCalendar appState={appState} onStockSelect={onStockSelect} />;
  }

  // 创建帖子视图
  if (appState.currentView === 'create-post') {
    if (!appState.selectedStock) {
      return (
        <StockList
          stocks={appState.stocks}
          onStockSelect={onStockSelect}
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
      return (
        <ExpertClub
          posts={appState.posts}
        />
      );
    
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
    appState.currentView === 'shop' ||
    selectedMenu === 'dashboard' ||
    selectedMenu === 'stocks' ||
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
    />
  );
};

export default MainContent;
