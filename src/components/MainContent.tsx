import React from 'react';
import { AppState, ViewType } from '../types';
import Dashboard from './Dashboard';
import StockList from './StockList';
import StockDetail from './StockDetail';
import StockCommunity from './StockCommunity';
import ExpertClub from './ExpertClub';
import CreatePost from './CreatePost';
import PostDetail from './PostDetail';
import RechargeHistory from './RechargeHistory';
import PlatformBalance from './PlatformBalance';
import Shop from './Shop';
import ProductDetail from './ProductDetail';
import Cart from './Cart';
import Orders from './Orders';
import HomePage from './HomePage';

interface MainContentProps {
  selectedMenu: string;
  appState: AppState;
  onStockSelect: (stock: any) => void;
  onBackToStocks: () => void;
  onPostClick: (post: any) => void;
  onCreatePost: () => void;
  onPurchase: (post: any) => void;
  onRate: (post: any, rating: number) => void;
  onLike: (post: any) => void;
  onShare: (post: any) => void;
  onViewChange: (view: ViewType) => void;
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
  onViewChange,
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
  
  // 调试：打印当前状态
  console.log('MainContent render:', { selectedMenu, currentView: appState.currentView });
  
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

  // 购物车视图 - 检查 currentView 或 selectedMenu
  if (appState.currentView === 'cart' || selectedMenu === 'cart') {
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

  // 订单视图 - 检查 currentView 或 selectedMenu
  if (appState.currentView === 'orders' || selectedMenu === 'orders') {
    return (
      <Orders
        orders={appState.orders}
        onPay={onOrderPay}
        onCancel={onOrderCancel}
        onRefund={onOrderRefund}
      />
    );
  }

  // 创建帖子视图
  if (appState.currentView === 'create-post') {
    return (
      <CreatePost
        stock={appState.selectedStock!}
        onSave={(post) => {
          console.log('保存帖子:', post);
          if (appState.selectedStock) {
            onViewChange('stock-community');
          } else {
            onViewChange('stocks');
          }
        }}
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
        onPurchase={(postId: string, amount: number) => onPurchase({ id: postId, price: amount })}
        onRate={(postId: string, rating: number, feedback: string) => onRate({ id: postId }, rating)}
        onLike={(postId: string) => onLike(postId)}
        onShare={(postId: string) => onShare(postId)}
        onAddComment={(postId: string, content: string) => {
          console.log('添加评论:', { postId, content });
        }}
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
        onPurchase={onPurchase}
        onRate={onRate}
        onLike={onLike}
        onShare={onShare}
      />
    );
  }

  // 首页视图 - 整合仪表盘、个股专区、商城
  if (selectedMenu === 'dashboard' || selectedMenu === 'stocks' || selectedMenu === 'shop' || selectedMenu === 'home') {
    return (
      <HomePage
        appState={appState}
        onStockSelect={onStockSelect}
        onProductClick={onProductClick}
        onAddToCart={onAddToCart}
      />
    );
  }

  // 根据选中的菜单项显示不同内容（确保只显示一个模块）
  // 严格按照 selectedMenu 来决定显示哪个模块
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
    
    default:
      // 默认显示股票列表
      return (
        <StockList
          stocks={appState.stocks}
          onStockSelect={(stock) => {
            onStockSelect(stock);
          }}
        />
      );
  }
};

export default MainContent;