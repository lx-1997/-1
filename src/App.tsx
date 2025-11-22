import React, { useState } from 'react';
import { Layout, message } from 'antd';
import { Routes, Route } from 'react-router-dom';
import TradingLayout from './components/TradingLayout';
import Login from './components/Login';
import { AppState, User, ViewType } from './types';
import { 
  mockUser, 
  mockStocks,
  mockPosts,
  mockComments,
  mockRatings,
  mockPayments,
  mockRechargeHistory,
  mockProducts,
  mockOrders
} from './data/mockData';

const { Content } = Layout;

const App: React.FC = () => {
  const [appState, setAppState] = useState<AppState>({
    user: null,
    selectedStock: null,
    selectedPost: null,
    stocks: [],
    posts: [],
    comments: [],
    ratings: [],
    payments: [],
    purchasedPosts: [], // 用户已购买的帖子ID列表
    isLoading: false,
    currentView: 'stocks',
    platformBalance: 0, // 平台总余额
    rechargeHistory: [], // 充值记录
    // 商城相关
    products: [],
    cart: [],
    orders: [],
    selectedProduct: null
  });

  const handleLogin = async (username: string, password: string) => {
    setAppState(prev => ({ ...prev, isLoading: true }));
    
    // 模拟登录验证 - 支持多个账号
    if ((username === 'admin' && password === 'admin') || 
        (username === 'demo' && password === 'demo')) {
      setTimeout(() => {
        setAppState(prev => ({
          ...prev,
          user: mockUser,
          stocks: mockStocks,
          posts: mockPosts,
          comments: mockComments,
          ratings: mockRatings,
          payments: mockPayments,
          rechargeHistory: mockRechargeHistory,
          platformBalance: mockRechargeHistory.reduce((sum, record) => sum + record.amount, 0),
          products: mockProducts,
          orders: mockOrders,
          isLoading: false
        }));
        message.success('登录成功！欢迎使用深度焦点个股投研智库');
      }, 1000);
    } else {
      setTimeout(() => {
        setAppState(prev => ({ ...prev, isLoading: false }));
        message.error('用户名或密码错误');
      }, 1000);
    }
  };

  const handleLogout = () => {
    setAppState({
      user: null,
      selectedStock: null,
      selectedPost: null,
      stocks: [],
      posts: [],
      comments: [],
      ratings: [],
      payments: [],
      purchasedPosts: [],
      isLoading: false,
      currentView: 'stocks',
      platformBalance: 0,
      rechargeHistory: [],
      products: [],
      cart: [],
      orders: [],
      selectedProduct: null
    });
    message.success('已退出登录');
  };

  // 选择股票
  const handleStockSelect = (stock: any) => {
    setAppState(prev => ({
      ...prev,
      selectedStock: stock,
      currentView: 'stock-community' // 默认进入社区页面
    }));
  };

  // 返回股票列表
  const handleBackToStocks = () => {
    setAppState(prev => ({
      ...prev,
      selectedStock: null,
      currentView: 'stocks'
    }));
  };

  // 查看内容详情
  const handlePostClick = (post: any) => {
    setAppState(prev => ({
      ...prev,
      currentView: 'post-detail',
      selectedPost: post // 添加选中的帖子到状态中
    }));
  };

  // 创建新内容
  const handleCreatePost = () => {
    setAppState(prev => ({
      ...prev,
      currentView: 'create-post'
    }));
  };

  // 购买内容
  const handlePurchase = (postId: string, amount: number) => {
    if (!appState.user) {
      message.error('请先登录');
      return;
    }

    // 检查余额是否足够
    if (appState.user.balance < amount) {
      message.error(`余额不足！当前余额：$${appState.user.balance.toFixed(2)}，需要：$${amount.toFixed(2)}`);
      return;
    }

    // 检查是否已经购买过
    if (appState.purchasedPosts.includes(postId)) {
      message.warning('您已经购买过此内容');
      return;
    }

    // 扣减余额并记录购买
    setAppState(prev => ({
      ...prev,
      user: prev.user ? {
        ...prev.user,
        balance: prev.user.balance - amount
      } : null,
      purchasedPosts: [...prev.purchasedPosts, postId],
      payments: [...prev.payments, {
        id: `payment_${Date.now()}`,
        userId: prev.user!.id,
        postId: postId,
        amount: amount,
        paymentTime: new Date().toISOString(),
        status: 'completed'
      }]
    }));

    message.success(`购买成功！支付金额：$${amount.toFixed(2)}，余额：$${(appState.user.balance - amount).toFixed(2)}`);
  };

  // 评分
  const handleRate = (postId: string, rating: number, feedback: string) => {
    // 这里应该调用评分API
    message.success(`评分提交成功！评分：${rating}星`);
  };

  // 点赞
  const handleLike = (postId: string) => {
    console.log('handleLike被调用，帖子ID:', postId);
    // 更新帖子的点赞数
    setAppState(prev => ({
      ...prev,
      posts: prev.posts.map(post => 
        post.id === postId 
          ? { ...post, likes: post.likes + 1 }
          : post
      )
    }));
    message.success('点赞成功！');
  };

  // 分享
  const handleShare = (postId: string) => {
    console.log('handleShare被调用，帖子ID:', postId);
    // 更新帖子的分享数
    setAppState(prev => ({
      ...prev,
      posts: prev.posts.map(post => 
        post.id === postId 
          ? { ...post, shares: post.shares + 1 }
          : post
      )
    }));
  };

  // 充值
  const handleRecharge = (amount: number, method: string) => {
    if (!appState.user) {
      message.error('请先登录');
      return;
    }

    // 生成交易ID
    const transactionId = `${method.toUpperCase()}${Date.now()}`;
    const newRechargeRecord = {
      id: `recharge_${Date.now()}`,
      userId: appState.user.id,
      amount,
      method,
      status: 'success' as const,
      createdAt: new Date().toISOString(),
      transactionId
    };

    // 模拟充值成功
    setAppState(prev => ({
      ...prev,
      user: prev.user ? {
        ...prev.user,
        balance: prev.user.balance + amount
      } : null,
      rechargeHistory: [newRechargeRecord, ...prev.rechargeHistory],
      platformBalance: prev.platformBalance + amount
    }));

    message.success(`充值成功！充值金额：$${amount.toFixed(2)}，支付方式：${method}，当前余额：$${(appState.user.balance + amount).toFixed(2)}`);
  };

  // 商城相关处理函数
  const handleProductClick = (product: any) => {
    setAppState(prev => ({
      ...prev,
      selectedProduct: product,
      currentView: 'product-detail'
    }));
  };

  const handleAddToCart = (product: any, variantId: string, quantity: number) => {
    const variant = product.variants.find((v: any) => v.id === variantId) || product.variants[0];
    const cartItem = {
      id: `cart_${Date.now()}`,
      productId: product.id,
      product: product,
      variantId: variantId,
      variant: variant,
      quantity: quantity,
      addedAt: new Date().toISOString()
    };
    
    setAppState(prev => ({
      ...prev,
      cart: [...prev.cart, cartItem]
    }));
    
    message.success('已添加到购物车');
  };

  const handleUpdateCartQuantity = (itemId: string, quantity: number) => {
    setAppState(prev => ({
      ...prev,
      cart: prev.cart.map(item => 
        item.id === itemId ? { ...item, quantity } : item
      )
    }));
  };

  const handleRemoveFromCart = (itemId: string) => {
    setAppState(prev => ({
      ...prev,
      cart: prev.cart.filter(item => item.id !== itemId)
    }));
    message.success('已从购物车移除');
  };

  const handleCheckout = (items: any[]) => {
    // 创建订单
    const order: any = {
      id: `order_${Date.now()}`,
      userId: appState.user!.id,
      items: items.map(item => ({
        id: `item_${Date.now()}_${Math.random()}`,
        productId: item.productId,
        productName: item.product.name,
        variantId: item.variantId,
        variantName: item.variant.name,
        quantity: item.quantity,
        price: item.variant.price,
        image: item.product.images[0]
      })),
      totalAmount: items.reduce((sum, item) => sum + item.variant.price * item.quantity, 0),
      paymentMethod: 'wechat' as const,
      paymentStatus: 'pending' as const,
      orderStatus: 'pending' as const,
      createdAt: new Date().toISOString()
    };

    setAppState(prev => ({
      ...prev,
      orders: [order, ...prev.orders],
      cart: prev.cart.filter(item => !items.find(i => i.id === item.id)),
      currentView: 'orders'
    }));

    message.success('订单创建成功，请完成支付');
  };

  const handleOrderPay = (orderId: string, paymentMethod: 'wechat' | 'alipay') => {
    const order = appState.orders.find(o => o.id === orderId);
    if (!order) return;

    // 模拟支付成功
    setTimeout(() => {
      setAppState(prev => ({
        ...prev,
        orders: prev.orders.map(o => 
          o.id === orderId 
            ? { 
                ...o, 
                paymentStatus: 'paid' as const,
                orderStatus: 'processing' as const,
                paidAt: new Date().toISOString(),
                transactionId: `${paymentMethod.toUpperCase()}${Date.now()}`
              }
            : o
        )
      }));
      message.success('支付成功！');
    }, 1000);
  };

  const handleOrderCancel = (orderId: string) => {
    setAppState(prev => ({
      ...prev,
      orders: prev.orders.map(o => 
        o.id === orderId 
          ? { ...o, orderStatus: 'cancelled' as const }
          : o
      )
    }));
    message.success('订单已取消');
  };

  const handleOrderRefund = (orderId: string) => {
    setAppState(prev => ({
      ...prev,
      orders: prev.orders.map(o => 
        o.id === orderId 
          ? { ...o, paymentStatus: 'refunded' as const }
          : o
      )
    }));
    message.success('退款申请已提交');
  };

  const handleBuyNow = (product: any, variantId: string, quantity: number) => {
    const variant = product.variants.find((v: any) => v.id === variantId) || product.variants[0];
    const items = [{
      id: `temp_${Date.now()}`,
      productId: product.id,
      product: product,
      variantId: variantId,
      variant: variant,
      quantity: quantity,
      addedAt: new Date().toISOString()
    }];
    handleCheckout(items);
  };

  // 视图切换
  const handleViewChange = (view: ViewType) => {
    setAppState(prev => ({
      ...prev,
      currentView: view
    }));
  };

  return (
    <Layout className="trading-layout">
      <Content>
        <Routes>
          <Route 
            path="/login" 
            element={
              <Login 
                onLogin={handleLogin}
                isLoading={appState.isLoading}
              />
            } 
          />
          <Route 
            path="/*" 
            element={
              appState.user ? (
                <TradingLayout
                  appState={appState}
                  onLogout={handleLogout}
                  onStockSelect={handleStockSelect}
                  onBackToStocks={handleBackToStocks}
                  onPostClick={handlePostClick}
                  onCreatePost={handleCreatePost}
                  onPurchase={handlePurchase}
                  onRate={handleRate}
                  onLike={handleLike}
                  onShare={handleShare}
                  onRecharge={handleRecharge}
                  onViewChange={handleViewChange}
                  onProductClick={handleProductClick}
                  onAddToCart={handleAddToCart}
                  onUpdateCartQuantity={handleUpdateCartQuantity}
                  onRemoveFromCart={handleRemoveFromCart}
                  onCheckout={handleCheckout}
                  onOrderPay={handleOrderPay}
                  onOrderCancel={handleOrderCancel}
                  onOrderRefund={handleOrderRefund}
                  onBuyNow={handleBuyNow}
                />
              ) : (
                <Login 
                  onLogin={handleLogin}
                  isLoading={appState.isLoading}
                />
              )
            } 
          />
        </Routes>
      </Content>
    </Layout>
  );
};

export default App;
