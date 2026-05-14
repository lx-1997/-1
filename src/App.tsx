import React, { useCallback, useEffect, useRef, useState } from 'react';
import { App as AntdApp, Layout } from 'antd';
import { Routes, Route, Navigate } from 'react-router-dom';
import TradingLayout from './components/TradingLayout';
import Login from './components/Login';
import { AppState, CartItem, Comment, Post, Product, ProductVariant, Stock, ViewType } from './types';
import { getMarketQuotes, MarketQuote, MarketSymbolCandidate } from './services/marketDataService';
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
const AUTH_BYPASS_ENABLED = true;
const STOCK_POOL_STORAGE_KEY = 'deepfocus.stockPool.v1';

const rootViews = new Set<ViewType>([
  'home',
  'stocks',
  'shop',
  'profile',
  'cart',
  'orders',
  'ai-research',
  'agent-center',
  'data-sources',
  'research-workbench',
  'realtime-messages',
  'mcp-center',
  'skills',
  'earnings-calendar'
]);

const getProductVariant = (product: Product, variantId: string): ProductVariant => {
  return product.variants.find(variant => variant.id === variantId)
    || product.variants[0]
    || {
      id: 'default',
      name: '默认款式',
      sku: `${product.id}-default`,
      price: product.price,
      stock: product.stock,
      attributes: {}
    };
};

const toFiniteNumber = (value?: number | null): number | undefined => {
  return typeof value === 'number' && Number.isFinite(value) ? value : undefined;
};

const marketCurrency = (market?: string): string => {
  if (market === 'HK') return 'HKD';
  if (market === 'CN') return 'CNY';
  return 'USD';
};

const enrichDefaultStock = (stock: Stock): Stock => ({
  ...stock,
  market: stock.market || 'US',
  exchange: stock.exchange || 'US',
  currency: stock.currency || marketCurrency(stock.market || 'US'),
  isSubscribed: stock.isSubscribed ?? true,
  subscriptionTopics: stock.subscriptionTopics || ['price', 'news', 'earnings', 'research'],
  addedAt: stock.addedAt || new Date().toISOString()
});

const loadSavedStockPool = (): Stock[] | null => {
  if (typeof window === 'undefined') {
    return null;
  }

  try {
    const raw = window.localStorage.getItem(STOCK_POOL_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) {
      return null;
    }
    return parsed.map(stock => enrichDefaultStock(stock as Stock));
  } catch (error) {
    console.warn('Failed to load stock pool:', error);
    return null;
  }
};

const candidateToStock = (candidate: MarketSymbolCandidate): Stock => ({
  symbol: candidate.symbol,
  name: candidate.name,
  market: candidate.market,
  exchange: candidate.exchange || candidate.security_type,
  currency: marketCurrency(candidate.market),
  quoteId: candidate.quote_id || undefined,
  isSubscribed: true,
  subscriptionTopics: ['price', 'news', 'earnings', 'research'],
  addedAt: new Date().toISOString(),
  sector: candidate.security_type || (candidate.market === 'US' ? '美股' : candidate.market === 'HK' ? '港股' : 'A股'),
  marketCap: 0,
  currentPrice: 0,
  changePercent: 0,
  priceChange: 0,
  previousClose: 0,
  quoteVolume: 0,
  quoteProvider: candidate.provider,
  quoteProviderName: candidate.provider_name,
  quoteFetchedAt: new Date().toISOString(),
  quoteIsRealtime: false,
  quoteDelayNote: '已加入自选，等待行情刷新',
  description: `${candidate.name}（${candidate.symbol}）已加入自选股池，可订阅价格、新闻、财报和研究提醒。`,
  focusLevel: 'medium',
  totalPosts: 0,
  totalPaidPosts: 0,
  communityScore: 50
});

const applyMarketQuotesToStocks = (stocks: Stock[], quotes: MarketQuote[]): Stock[] => {
  const quoteBySymbol = new Map(quotes.map(quote => [quote.symbol.toUpperCase(), quote]));

  return stocks.map(stock => {
    const quote = quoteBySymbol.get(stock.symbol.toUpperCase());
    if (!quote) {
      return stock;
    }

    return {
      ...stock,
      currentPrice: quote.price,
      changePercent: toFiniteNumber(quote.change_percent) ?? stock.changePercent,
      priceChange: toFiniteNumber(quote.change) ?? stock.priceChange,
      previousClose: toFiniteNumber(quote.previous_close) ?? stock.previousClose,
      quoteVolume: toFiniteNumber(quote.volume) ?? stock.quoteVolume,
      quoteProvider: quote.provider,
      quoteProviderName: quote.provider_name,
      quoteMarketTime: quote.market_time,
      quoteFetchedAt: quote.fetched_at,
      quoteIsRealtime: quote.is_realtime,
      quoteDelayNote: quote.delay_note
    };
  });
};

const createLoggedOutState = (): AppState => ({
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
  currentView: 'home',
  platformBalance: 0,
  rechargeHistory: [],
  products: [],
  cart: [],
  orders: [],
  selectedProduct: null
});

const createDemoState = (): AppState => {
  const savedStockPool = loadSavedStockPool();
  return {
    ...createLoggedOutState(),
    user: mockUser,
    stocks: (savedStockPool || mockStocks).map(stock => enrichDefaultStock({ ...stock })),
    posts: mockPosts,
    comments: mockComments,
    ratings: mockRatings,
    payments: mockPayments,
    rechargeHistory: mockRechargeHistory,
    platformBalance: mockRechargeHistory.reduce((sum, record) => sum + record.amount, 0),
    products: mockProducts,
    orders: mockOrders
  };
};

const App: React.FC = () => {
  const { message } = AntdApp.useApp();
  const stocksRef = useRef<Stock[]>([]);
  const [isMarketDataRefreshing, setIsMarketDataRefreshing] = useState(false);
  const [appState, setAppState] = useState<AppState>(() =>
    AUTH_BYPASS_ENABLED ? createDemoState() : createLoggedOutState()
  );

  useEffect(() => {
    stocksRef.current = appState.stocks;
  }, [appState.stocks]);

  useEffect(() => {
    if (!appState.user || typeof window === 'undefined') {
      return;
    }

    window.localStorage.setItem(STOCK_POOL_STORAGE_KEY, JSON.stringify(appState.stocks));
  }, [appState.stocks, appState.user]);

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
        if (response.warnings.length > 0) {
          console.warn('Market data warnings:', response.warnings);
        }
        return;
      }

      setAppState(prev => {
        const updatedStocks = applyMarketQuotesToStocks(prev.stocks, response.quotes);
        const selectedStock = prev.selectedStock
          ? updatedStocks.find(stock => stock.symbol === prev.selectedStock?.symbol) || prev.selectedStock
          : null;

        return {
          ...prev,
          stocks: updatedStocks,
          selectedStock
        };
      });

      if (response.warnings.length > 0) {
        console.warn('Market data warnings:', response.warnings);
      }

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
  }, [message]);

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

  const handleLogin = async (username: string, password: string) => {
    setAppState(prev => ({ ...prev, isLoading: true }));
    
    // 模拟登录验证 - 支持多个账号
    if ((username === 'admin' && password === 'admin') || 
        (username === 'demo' && password === 'demo')) {
      setTimeout(() => {
        const nextState = createDemoState();
        setAppState(nextState);
        message.success('登录成功！欢迎使用深度焦点个股投研智库');
        void refreshMarketQuotes(nextState.stocks, { notify: true });
      }, 1000);
    } else {
      setTimeout(() => {
        setAppState(prev => ({ ...prev, isLoading: false }));
        message.error('用户名或密码错误');
      }, 1000);
    }
  };

  const handleLogout = () => {
    if (AUTH_BYPASS_ENABLED) {
      setAppState(createDemoState());
      message.success('已重置演示会话');
      return;
    }

    setAppState(createLoggedOutState());
    message.success('已退出登录');
  };

  // 选择股票
  const handleStockSelect = (stock: Stock) => {
    setAppState(prev => ({
      ...prev,
      selectedStock: stock,
      selectedPost: null,
      selectedProduct: null,
      currentView: 'stock-community' // 默认进入社区页面
    }));
  };

  const handleAddStock = async (candidate: MarketSymbolCandidate) => {
    const nextStock = candidateToStock(candidate);
    let shouldRefresh = false;

    setAppState(prev => {
      const exists = prev.stocks.some(stock => stock.symbol.toUpperCase() === nextStock.symbol.toUpperCase());
      if (exists) {
        return {
          ...prev,
          stocks: prev.stocks.map(stock =>
            stock.symbol.toUpperCase() === nextStock.symbol.toUpperCase()
              ? {
                  ...stock,
                  isSubscribed: true,
                  subscriptionTopics: stock.subscriptionTopics || nextStock.subscriptionTopics
                }
              : stock
          )
        };
      }

      shouldRefresh = true;
      return {
        ...prev,
        stocks: [nextStock, ...prev.stocks]
      };
    });

    message.success(`${candidate.name} 已加入自选股池并开启订阅`);
    if (shouldRefresh) {
      await refreshMarketQuotes([nextStock], { notify: false });
    }
  };

  const handleRemoveStock = (symbol: string) => {
    setAppState(prev => ({
      ...prev,
      stocks: prev.stocks.filter(stock => stock.symbol !== symbol),
      selectedStock: prev.selectedStock?.symbol === symbol ? null : prev.selectedStock,
      currentView: prev.selectedStock?.symbol === symbol ? 'stocks' : prev.currentView
    }));
    message.success(`${symbol} 已从自选股池移除`);
  };

  const handleToggleStockSubscription = (symbol: string) => {
    let subscribed = false;
    setAppState(prev => ({
      ...prev,
      stocks: prev.stocks.map(stock => {
        if (stock.symbol !== symbol) {
          return stock;
        }
        subscribed = !(stock.isSubscribed ?? true);
        return {
          ...stock,
          isSubscribed: subscribed,
          subscriptionTopics: stock.subscriptionTopics?.length ? stock.subscriptionTopics : ['price', 'news', 'earnings', 'research']
        };
      }),
      selectedStock: prev.selectedStock?.symbol === symbol
        ? {
            ...prev.selectedStock,
            isSubscribed: subscribed
          }
        : prev.selectedStock
    }));
    message.success(subscribed ? `${symbol} 已开启订阅` : `${symbol} 已暂停订阅`);
  };

  // 返回股票列表
  const handleBackToStocks = () => {
    setAppState(prev => ({
      ...prev,
      selectedStock: null,
      selectedPost: null,
      currentView: 'stocks'
    }));
  };

  // 查看内容详情
  const handlePostClick = (post: Post) => {
    setAppState(prev => ({
      ...prev,
      currentView: 'post-detail',
      selectedProduct: null,
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

  const handleSavePost = (postDraft: Partial<Post>) => {
    if (!appState.user || !appState.selectedStock) {
      message.error('请先选择个股后再发布内容');
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

    setAppState(prev => ({
      ...prev,
      posts: [newPost, ...prev.posts],
      selectedPost: newPost,
      stocks: prev.stocks.map(stock =>
        stock.symbol === newPost.stockSymbol
          ? {
              ...stock,
              totalPosts: stock.totalPosts + 1,
              totalPaidPosts: stock.totalPaidPosts + (newPost.isPaid ? 1 : 0)
            }
          : stock
      ),
      currentView: 'stock-community'
    }));

    message.success('内容发布成功！');
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
      }],
      posts: prev.posts.map(post =>
        post.id === postId
          ? {
              ...post,
              paidViewers: post.paidViewers + 1,
              totalRevenue: post.totalRevenue + amount
            }
          : post
      ),
      selectedPost: prev.selectedPost?.id === postId
        ? {
            ...prev.selectedPost,
            paidViewers: prev.selectedPost.paidViewers + 1,
            totalRevenue: prev.selectedPost.totalRevenue + amount
          }
        : prev.selectedPost
    }));

    message.success(`购买成功！支付金额：$${amount.toFixed(2)}，余额：$${(appState.user.balance - amount).toFixed(2)}`);
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

    setAppState(prev => ({
      ...prev,
      ratings: [...prev.ratings, newRating],
      posts: prev.posts.map(updatePostRating),
      selectedPost: prev.selectedPost ? updatePostRating(prev.selectedPost) : prev.selectedPost
    }));

    message.success(`评分提交成功！评分：${rating}星`);
  };

  // 点赞
  const handleLike = (postId: string) => {
    setAppState(prev => ({
      ...prev,
      posts: prev.posts.map(post => 
        post.id === postId 
          ? { ...post, likes: post.likes + 1 }
          : post
      ),
      selectedPost: prev.selectedPost?.id === postId
        ? { ...prev.selectedPost, likes: prev.selectedPost.likes + 1 }
        : prev.selectedPost
    }));
    message.success('点赞成功！');
  };

  // 分享
  const handleShare = (postId: string) => {
    setAppState(prev => ({
      ...prev,
      posts: prev.posts.map(post => 
        post.id === postId 
          ? { ...post, shares: post.shares + 1 }
          : post
      ),
      selectedPost: prev.selectedPost?.id === postId
        ? { ...prev.selectedPost, shares: prev.selectedPost.shares + 1 }
        : prev.selectedPost
    }));
  };

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

    setAppState(prev => ({
      ...prev,
      comments: [newComment, ...prev.comments],
      posts: prev.posts.map(post =>
        post.id === postId
          ? { ...post, comments: post.comments + 1 }
          : post
      ),
      selectedPost: prev.selectedPost?.id === postId
        ? { ...prev.selectedPost, comments: prev.selectedPost.comments + 1 }
        : prev.selectedPost
    }));

    message.success('评论发布成功！');
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
  const handleProductClick = (product: Product) => {
    setAppState(prev => ({
      ...prev,
      selectedProduct: product,
      selectedPost: null,
      selectedStock: null,
      currentView: 'product-detail'
    }));
  };

  const handleAddToCart = (product: Product, variantId: string, quantity: number) => {
    const variant = getProductVariant(product, variantId);
    const existingItem = appState.cart.find(item => item.productId === product.id && item.variantId === variant.id);

    if (variant.stock <= 0) {
      message.warning('该商品暂时无库存');
      return;
    }

    if (existingItem && existingItem.quantity + quantity > variant.stock) {
      message.warning(`库存不足，当前购物车已有 ${existingItem.quantity} 件，库存 ${variant.stock} 件`);
      return;
    }

    if (!existingItem && quantity > variant.stock) {
      message.warning(`库存不足，当前库存 ${variant.stock} 件`);
      return;
    }

    const cartItem: CartItem = {
      id: `cart_${Date.now()}`,
      productId: product.id,
      product: product,
      variantId: variant.id,
      variant: variant,
      quantity: quantity,
      addedAt: new Date().toISOString()
    };
    
    setAppState(prev => ({
      ...prev,
      cart: existingItem
        ? prev.cart.map(item =>
            item.id === existingItem.id
              ? { ...item, quantity: item.quantity + quantity }
              : item
          )
        : [...prev.cart, cartItem]
    }));
    
    message.success(existingItem ? '已更新购物车数量' : '已添加到购物车');
  };

  const handleUpdateCartQuantity = (itemId: string, quantity: number) => {
    const cartItem = appState.cart.find(item => item.id === itemId);
    if (cartItem && quantity > cartItem.variant.stock) {
      message.warning(`库存不足，当前库存 ${cartItem.variant.stock} 件`);
      return;
    }

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
    if (!appState.user) {
      message.error('请先登录');
      return;
    }

    if (items.length === 0) {
      message.warning('请选择要结算的商品');
      return;
    }

    // 创建订单
    const order: any = {
      id: `order_${Date.now()}`,
      userId: appState.user.id,
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
  };

  // 视图切换
  const handleViewChange = (view: ViewType) => {
    setAppState(prev => ({
      ...prev,
      currentView: view,
      selectedStock: rootViews.has(view) ? null : prev.selectedStock,
      selectedPost: rootViews.has(view) ? null : prev.selectedPost,
      selectedProduct: rootViews.has(view) ? null : prev.selectedProduct
    }));
  };

  return (
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
                  isLoading={appState.isLoading}
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
