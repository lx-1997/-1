import React, { useCallback, useEffect, useRef, useReducer, useState } from 'react';
import { App as AntdApp, Layout } from 'antd';
import { Routes, Route, Navigate } from 'react-router-dom';
import TradingLayout from './components/TradingLayout';
import Login from './components/Login';
import AIChatPanel from './components/AIChatPanel';
import SignalRecallListener from './components/SignalRecallListener';
import AppUpdateChecker from './components/AppUpdateChecker';
import { ModuleContextProvider } from './contexts/ModuleContext';
import { CartItem, Comment, Post, Product, Stock, User, ViewType } from './types';
import { getMarketQuotes, MarketSymbolCandidate } from './services/marketService';
import * as authService from './services/authService';
import { formatErrorMessage } from './services/apiClient';
import { mockUser } from './data/mockData';
import { appReducer, getInitialState, createDemoState, createLoggedInState, getProductVariant } from './state/appReducer';
import { applyMarketQuotesToStocks, candidateToStock, STOCK_POOL_STORAGE_KEY } from './utils/stockPool';
import { saveCommunity } from './utils/communityPersistence';
import { track, trackPageview } from './utils/analytics';

const { Content } = Layout;
// 鉴权旁路仅在显式 REACT_APP_AUTH_BYPASS=true 时开启——移除"dev 环境默认免登录"，杜绝任何非显式绕过登录。
const AUTH_BYPASS_ENABLED = process.env.REACT_APP_AUTH_BYPASS?.toLowerCase() === 'true';
// 离线演示登录（含 admin/admin 兜底）默认关闭，仅显式 REACT_APP_DEMO_LOGIN=true 才启用，消除默认后门。
const DEMO_LOGIN_ENABLED = process.env.REACT_APP_DEMO_LOGIN?.toLowerCase() === 'true';

// 行情轮询会高频触发；仅在 dev 且告警内容变化时打印一次，避免控制台刷屏。
let lastMarketWarningSignature = '';
function logMarketWarningsOnce(warnings: string[]): void {
  if (process.env.NODE_ENV === 'production' || !warnings || warnings.length === 0) {
    return;
  }
  const signature = [...warnings].sort().join('|');  // 排序后比对，避免后端异步告警顺序变化导致重复打印
  if (signature === lastMarketWarningSignature) {
    return;
  }
  lastMarketWarningSignature = signature;
  console.warn('Market data warnings:', warnings);
}

const App: React.FC = () => {
  const { message } = AntdApp.useApp();
  const stocksRef = useRef<Stock[]>([]);
  const [isMarketDataRefreshing, setIsMarketDataRefreshing] = useState(false);
  const [chatPanelOpen, setChatPanelOpen] = useState(false);
  const [appState, dispatch] = useReducer(appReducer, undefined, getInitialState);

  useEffect(() => {
    stocksRef.current = appState.stocks;
  }, [appState.stocks]);

  // 全站行为采集：进站一次 pageview + 每次视图切换记一条 tab 流水（增长分析 DAU/留存的数据底座）
  useEffect(() => { trackPageview(); }, []);
  useEffect(() => {
    if (appState.currentView) track('tab', appState.currentView);
  }, [appState.currentView]);

  useEffect(() => {
    if (!appState.user || typeof window === 'undefined') {
      return;
    }

    window.localStorage.setItem(STOCK_POOL_STORAGE_KEY, JSON.stringify(appState.stocks));
  }, [appState.stocks, appState.user]);

  // 社区/商城 UGC 本地持久化：写操作（发帖/评论/点赞/评分/购买/下单/余额变动）后落盘，刷新不丢。
  useEffect(() => {
    if (!appState.user || typeof window === 'undefined') {
      return;
    }
    saveCommunity(appState);
  }, [
    appState.user,
    appState.posts,
    appState.comments,
    appState.ratings,
    appState.payments,
    appState.purchasedPosts,
    appState.likedPosts,
    appState.cart,
    appState.orders,
  ]);

  const refreshMarketQuotes = useCallback(async (
    stocksToRefresh: Stock[],
    options: { notify?: boolean } = {}
  ) => {
    const symbols = stocksToRefresh.map(stock => stock.symbol);
    if (symbols.length === 0) {
      return;
    }

    setIsMarketDataRefreshing(true);
    try {
      const response = await getMarketQuotes(symbols);

      if (response.quotes.length === 0) {
        if (options.notify) {
          message.warning('行情接口暂未返回可用报价，当前继续显示本地样例数据');
        }
        logMarketWarningsOnce(response.warnings);
        return;
      }

      const updatedStocks = applyMarketQuotesToStocks(stocksRef.current, response.quotes);
      const currentSelected = stocksRef.current.find(
        stock => stock.symbol === appState.selectedStock?.symbol
      );
      const selectedStock = appState.selectedStock
        ? updatedStocks.find(stock => stock.symbol === appState.selectedStock?.symbol) || currentSelected || appState.selectedStock
        : null;
      dispatch({ type: 'SET_APP_STATE', payload: { stocks: updatedStocks, selectedStock } });

      logMarketWarningsOnce(response.warnings);

      if (options.notify) {
        const hasRealtimeQuote = response.quotes.some(quote => quote.is_realtime);
        message.success(`${hasRealtimeQuote ? '实时' : '最新'}行情已更新：${response.quotes.length} 个标的`);
      }
    } catch (error) {
      console.warn('Market data refresh failed:', error);
      if (options.notify) {
        message.warning('行情服务暂不可用，当前继续显示本地样例数据');
      }
    } finally {
      setIsMarketDataRefreshing(false);
    }
  }, [message, appState.selectedStock]);

  useEffect(() => {
    if (!appState.user) {
      return undefined;
    }

    const timer = window.setInterval(() => {
      if (stocksRef.current.length > 0) {
        void refreshMarketQuotes(stocksRef.current);
      }
    }, 120000);

    return () => window.clearInterval(timer);
  }, [appState.user, refreshMarketQuotes]);

  // 演示账号在后端不可达/无该账号时的离线兜底（仅这两个账号、且演示开关开启时）。
  const tryDemoFallback = (username: string, password: string): boolean => {
    const isDemoCreds = (username === 'admin' && password === 'admin')
      || (username === 'demo' && password === 'demo');
    if (!DEMO_LOGIN_ENABLED || !isDemoCreds) {
      return false;
    }
    const nextState = createDemoState();
    dispatch({ type: 'LOGIN' });
    message.success('已进入演示会话（未连接后端认证）');
    void refreshMarketQuotes(nextState.stocks, { notify: true });
    return true;
  };

  const enterAuthedSession = (user: User) => {
    const nextState = createLoggedInState(user);
    dispatch({ type: 'LOGIN_SUCCESS', payload: user });
    message.success(`登录成功！欢迎，${user.username}`);
    void refreshMarketQuotes(nextState.stocks, { notify: true });
  };

  const handleLogin = async (username: string, password: string) => {
    dispatch({ type: 'SET_LOADING', payload: true });
    try {
      const { user } = await authService.login(username, password);
      enterAuthedSession(user);
    } catch (error) {
      if (tryDemoFallback(username, password)) {
        return;
      }
      dispatch({ type: 'SET_LOADING', payload: false });
      message.error(formatErrorMessage(error));
    }
  };

  const handleRegister = async (email: string, username: string, password: string) => {
    dispatch({ type: 'SET_LOADING', payload: true });
    try {
      const { user } = await authService.register(email, username, password);
      enterAuthedSession(user);
    } catch (error) {
      dispatch({ type: 'SET_LOADING', payload: false });
      message.error(formatErrorMessage(error));
    }
  };

  const handleLogout = () => {
    authService.logout();  // 清理 JWT，避免残留令牌

    if (AUTH_BYPASS_ENABLED) {
      dispatch({ type: 'SET_APP_STATE', payload: createDemoState() });
      message.success('已重置演示会话');
      return;
    }

    dispatch({ type: 'LOGOUT' });
    message.success('已退出登录');
  };

  // 选择股票
  const handleStockSelect = useCallback((stock: Stock, view?: ViewType) => {
    dispatch({ type: 'SELECT_STOCK', payload: stock, view });
  }, []);

  const handleAddStock = async (candidate: MarketSymbolCandidate) => {
    const nextStock = candidateToStock(candidate);
    const existsBeforeAdd = stocksRef.current.some(
      stock => stock.symbol.toUpperCase() === nextStock.symbol.toUpperCase()
    );

    dispatch({ type: 'SET_APP_STATE', payload: {
      stocks: (() => {
        const exists = appState.stocks.some(stock => stock.symbol.toUpperCase() === nextStock.symbol.toUpperCase());
        if (exists) {
          return appState.stocks.map(stock =>
            stock.symbol.toUpperCase() === nextStock.symbol.toUpperCase()
              ? {
                  ...stock,
                  isSubscribed: true,
                  subscriptionTopics: stock.subscriptionTopics || nextStock.subscriptionTopics
                }
              : stock
          );
        }
        return [nextStock, ...appState.stocks];
      })()
    }});

    message.success(`${candidate.name} 已加入观察池并开启监控`);
    if (!existsBeforeAdd) {
      await refreshMarketQuotes([nextStock], { notify: false });
    }
  };

  const handleRemoveStock = useCallback((symbol: string) => {
    dispatch({ type: 'REMOVE_STOCK', payload: symbol });
    message.success(`${symbol} 已从观察池移除`);
  }, [message]);

  const handleToggleStockSubscription = useCallback((symbol: string) => {
    const stock = stocksRef.current.find(s => s.symbol === symbol);
    const willSubscribed = !(stock?.isSubscribed ?? true);
    dispatch({ type: 'TOGGLE_STOCK_SUBSCRIPTION', payload: symbol });
    message.success(willSubscribed ? `${symbol} 已开启监控` : `${symbol} 已暂停监控`);
  }, [message]);

  // 返回股票列表
  const handleBackToStocks = useCallback(() => {
    dispatch({ type: 'BACK_TO_STOCKS' });
  }, []);

  const handlePostClick = useCallback((post: Post) => {
    dispatch({ type: 'SELECT_POST', payload: post });
  }, []);

  const handleCreatePost = useCallback(() => {
    dispatch({ type: 'CREATE_POST' });
  }, []);

  const handleSavePost = (postDraft: Partial<Post>) => {
    if (!appState.user || !appState.selectedStock) {
      message.error('请先选择标的后再保存研究记录');
      return;
    }

    const isPaid = Boolean(postDraft.isPaid);
    const price = isPaid ? Number(postDraft.price || 0) : 0;
    const newPost: Post = {
      id: `post_${Date.now()}`,
      author: appState.user,
      stockSymbol: appState.selectedStock.symbol,
      title: postDraft.title || '未命名内容',
      content: postDraft.content || '',
      summary: postDraft.summary || '',
      type: postDraft.type || 'news',
      category: postDraft.category || 'market',
      tags: postDraft.tags || [],
      publishTime: new Date().toISOString(),
      updateTime: new Date().toISOString(),
      isPaid,
      price,
      paidViewers: 0,
      totalRevenue: 0,
      likes: 0,
      comments: 0,
      shares: 0,
      views: 0,
      qualityScore: 0,
      totalRatings: 0,
      status: 'published',
      isPinned: false,
      isHighlighted: false
    };

    dispatch({ type: 'SET_APP_STATE', payload: {
      posts: [newPost, ...appState.posts],
      selectedPost: newPost,
      stocks: appState.stocks.map(stock =>
        stock.symbol === newPost.stockSymbol
          ? {
              ...stock,
              totalPosts: stock.totalPosts + 1,
              totalPaidPosts: stock.totalPaidPosts + (newPost.isPaid ? 1 : 0)
            }
          : stock
      ),
      currentView: 'stock-community'
    }});

    message.success('研究记录已保存');
  };

  // 开通深度报告
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

    // 检查是否已经开通过
    if (appState.purchasedPosts.includes(postId)) {
      message.warning('您已经开通过这份深度报告');
      return;
    }

    // 扣减余额并记录开通
    dispatch({ type: 'SET_APP_STATE', payload: {
      user: appState.user ? {
        ...appState.user,
        balance: appState.user.balance - amount
      } : null,
      purchasedPosts: [...appState.purchasedPosts, postId],
      payments: [...appState.payments, {
        id: `payment_${Date.now()}`,
        userId: appState.user!.id,
        postId: postId,
        amount: amount,
        paymentTime: new Date().toISOString(),
        status: 'completed'
      }],
      posts: appState.posts.map(post =>
        post.id === postId
          ? {
              ...post,
              paidViewers: post.paidViewers + 1,
              totalRevenue: post.totalRevenue + amount
            }
          : post
      ),
      selectedPost: appState.selectedPost?.id === postId
        ? {
            ...appState.selectedPost,
            paidViewers: appState.selectedPost.paidViewers + 1,
            totalRevenue: appState.selectedPost.totalRevenue + amount
          }
        : appState.selectedPost
    }});

    message.success(`深度报告已开通，支付金额：$${amount.toFixed(2)}，余额：$${(appState.user.balance - amount).toFixed(2)}`);
  };

  // 评分
  const handleRate = (postId: string, rating: number, feedback: string) => {
    if (!appState.user) {
      message.error('请先登录');
      return;
    }

    const newRating = {
      id: `rating_${Date.now()}`,
      postId,
      userId: appState.user.id,
      rating,
      feedback,
      ratingTime: new Date().toISOString(),
      isAnonymous: false
    };

    const updatePostRating = (post: Post): Post => {
      if (post.id !== postId) {
        return post;
      }

      const totalRatings = post.totalRatings + 1;
      const qualityScore = ((post.qualityScore * post.totalRatings) + rating * 20) / totalRatings;
      return {
        ...post,
        totalRatings,
        qualityScore
      };
    };

    dispatch({ type: 'SET_APP_STATE', payload: {
      ratings: [...appState.ratings, newRating],
      posts: appState.posts.map(updatePostRating),
      selectedPost: appState.selectedPost ? updatePostRating(appState.selectedPost) : appState.selectedPost
    }});

    message.success(`评分提交成功！评分：${rating}星`);
  };

  // 点赞
  const handleLike = useCallback((postId: string) => {
    dispatch({ type: 'LIKE_POST', payload: postId });
    message.success('点赞成功！');
  }, [message]);

  const handleShare = useCallback((postId: string) => {
    dispatch({ type: 'SHARE_POST', payload: postId });
  }, []);

  const handleAddComment = (postId: string, content: string) => {
    if (!appState.user) {
      message.error('请先登录');
      return;
    }

    const newComment: Comment = {
      id: `comment_${Date.now()}`,
      postId,
      author: appState.user,
      content,
      publishTime: new Date().toISOString(),
      likes: 0,
      replies: [],
      isPaid: false
    };

    dispatch({ type: 'SET_APP_STATE', payload: {
      comments: [newComment, ...appState.comments],
      posts: appState.posts.map(post =>
        post.id === postId
          ? { ...post, comments: post.comments + 1 }
          : post
      ),
      selectedPost: appState.selectedPost?.id === postId
        ? { ...appState.selectedPost, comments: appState.selectedPost.comments + 1 }
        : appState.selectedPost
    }});

    message.success('讨论记录已保存');
  };

  // 充值
  const handleRecharge = useCallback((amount: number, method: string) => {
    const newBalance = (appState.user?.balance ?? 0) + amount;
    dispatch({ type: 'RECHARGE', payload: { amount, method } });
    message.success(`充值成功！充值金额：$${amount.toFixed(2)}，支付方式：${method}，当前余额：$${newBalance.toFixed(2)}`);
  }, [message, appState.user]);

  // 研究资产相关处理函数
  const handleProductClick = useCallback((product: Product) => {
    dispatch({ type: 'SELECT_PRODUCT', payload: product });
  }, []);

  const handleAddToCart = (product: Product, variantId: string, quantity: number) => {
    const variant = getProductVariant(product, variantId);
    const existingItem = appState.cart.find(item => item.productId === product.id && item.variantId === variant.id);

    if (variant.stock <= 0) {
      message.warning('该研究资产暂时无可用名额');
      return;
    }

    if (existingItem && existingItem.quantity + quantity > variant.stock) {
      message.warning(`可用名额不足，当前资产单已有 ${existingItem.quantity} 项，可用 ${variant.stock} 项`);
      return;
    }

    if (!existingItem && quantity > variant.stock) {
      message.warning(`可用名额不足，当前可用 ${variant.stock} 项`);
      return;
    }

    dispatch({ type: 'ADD_TO_CART', payload: { product, variantId, quantity } });

    message.success(existingItem ? '已更新资产单数量' : '已加入资产单');
  };

  const handleUpdateCartQuantity = (itemId: string, quantity: number) => {
    const cartItem = appState.cart.find(item => item.id === itemId);
    if (cartItem && quantity > cartItem.variant.stock) {
      message.warning(`可用名额不足，当前可用 ${cartItem.variant.stock} 项`);
      return;
    }

    dispatch({ type: 'SET_APP_STATE', payload: {
      cart: appState.cart.map(item =>
        item.id === itemId ? { ...item, quantity } : item
      )
    }});
  };

  const handleRemoveFromCart = useCallback((itemId: string) => {
    dispatch({ type: 'REMOVE_FROM_CART', payload: itemId });
    message.success('已从资产单移除');
  }, [message]);

  const handleOrderCancel = useCallback((orderId: string) => {
    dispatch({ type: 'ORDER_CANCEL', payload: orderId });
    message.success('订单已取消');
  }, [message]);

  const handleOrderRefund = useCallback((orderId: string) => {
    dispatch({ type: 'ORDER_REFUND', payload: orderId });
    message.success('退款申请已提交');
  }, [message]);

  const handleCheckout = useCallback((items: CartItem[]) => {
    if (!appState.user) {
      message.error('请先登录');
      return;
    }

    if (items.length === 0) {
      message.warning('请选择要开通的研究资产');
      return;
    }

    dispatch({ type: 'CHECKOUT', payload: items });
    message.success('资产订单已创建，请完成支付');
  }, [message, appState.user]);

  const handleBuyNow = useCallback((product: Product, variantId: string, quantity: number) => {
    const variant = getProductVariant(product, variantId);
    const items = [{
      id: `temp_${Date.now()}`,
      productId: product.id,
      product: product,
      variantId: variant.id,
      variant: variant,
      quantity: quantity,
      addedAt: new Date().toISOString()
    }];
    handleCheckout(items);
  }, [handleCheckout]);

  const handleOrderPay = (orderId: string, paymentMethod: 'wechat' | 'alipay') => {
    const order = appState.orders.find(o => o.id === orderId);
    if (!order) return;

    setTimeout(() => {
      dispatch({ type: 'ORDER_PAY', payload: { orderId, paymentMethod } });
      message.success('支付成功！');
    }, 1000);
  };

  const handleViewChange = useCallback((view: ViewType) => {
    dispatch({ type: 'SET_VIEW', payload: view });
  }, []);

  return (
    <ModuleContextProvider>
      <Layout className="trading-layout">
        <Content>
          <Routes>
            <Route
              path="/login"
              element={
                appState.user ? (
                  <Navigate to="/" replace />
                ) : (
                  <Login
                    onLogin={handleLogin}
                    onRegister={handleRegister}
                    isLoading={appState.isLoading}
                    demoLoginEnabled={DEMO_LOGIN_ENABLED}
                  />
                )
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
                    onAddComment={handleAddComment}
                    onRecharge={handleRecharge}
                    onViewChange={handleViewChange}
                    onSavePost={handleSavePost}
                    onProductClick={handleProductClick}
                    onAddToCart={handleAddToCart}
                    onUpdateCartQuantity={handleUpdateCartQuantity}
                    onRemoveFromCart={handleRemoveFromCart}
                    onCheckout={handleCheckout}
                    onOrderPay={handleOrderPay}
                    onOrderCancel={handleOrderCancel}
                    onOrderRefund={handleOrderRefund}
                    onBuyNow={handleBuyNow}
                    onAddStock={handleAddStock}
                    onRemoveStock={handleRemoveStock}
                    onToggleStockSubscription={handleToggleStockSubscription}
                    onRefreshMarketData={() => refreshMarketQuotes(appState.stocks, { notify: true })}
                    isMarketDataRefreshing={isMarketDataRefreshing}
                    isDemoSession={AUTH_BYPASS_ENABLED || appState.user?.id === mockUser.id}
                    chatPanelOpen={chatPanelOpen}
                    onToggleChatPanel={() => setChatPanelOpen(v => !v)}
                  />
                ) : (
                  <Login
                    onLogin={handleLogin}
                    onRegister={handleRegister}
                    isLoading={appState.isLoading}
                    demoLoginEnabled={DEMO_LOGIN_ENABLED}
                  />
                )
              }
            />
          </Routes>
        </Content>
      </Layout>
      <AIChatPanel open={chatPanelOpen} onClose={() => setChatPanelOpen(false)} />
      {appState.user && <SignalRecallListener stocks={appState.stocks} />}
      <AppUpdateChecker />
    </ModuleContextProvider>
  );
};

export default App;
