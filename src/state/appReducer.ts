import { AppState, CartItem, Post, Product, ProductVariant, Stock, User, ViewType } from '../types';
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
} from '../data/mockData';
import {
  DEFAULT_STOCK_SUBSCRIPTION_TOPICS,
  STOCK_POOL_STORAGE_KEY,
  enrichDefaultStock
} from '../utils/stockPool';
import { loadSavedCommunity } from '../utils/communityPersistence';

export type AppAction =
  | { type: 'LOGIN' }
  | { type: 'LOGIN_SUCCESS'; payload: User }
  | { type: 'LOGOUT' }
  | { type: 'SET_VIEW'; payload: ViewType }
  | { type: 'SELECT_STOCK'; payload: Stock; view?: ViewType }
  | { type: 'BACK_TO_STOCKS' }
  | { type: 'SELECT_POST'; payload: Post }
  | { type: 'SELECT_PRODUCT'; payload: Product }
  | { type: 'CREATE_POST' }
  | { type: 'LIKE_POST'; payload: string }
  | { type: 'SHARE_POST'; payload: string }
  | { type: 'ADD_TO_CART'; payload: { product: Product; variantId: string; quantity: number } }
  | { type: 'REMOVE_FROM_CART'; payload: string }
  | { type: 'CHECKOUT'; payload: CartItem[] }
  | { type: 'RECHARGE'; payload: { amount: number; method: string } }
  | { type: 'ORDER_PAY'; payload: { orderId: string; paymentMethod: 'wechat' | 'alipay' } }
  | { type: 'ORDER_CANCEL'; payload: string }
  | { type: 'ORDER_REFUND'; payload: string }
  | { type: 'REMOVE_STOCK'; payload: string }
  | { type: 'TOGGLE_STOCK_SUBSCRIPTION'; payload: string }
  | { type: 'SET_LOADING'; payload: boolean }
  | { type: 'SET_APP_STATE'; payload: Partial<AppState> };

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

export const createLoggedOutState = (): AppState => ({
  user: null,
  selectedStock: null,
  selectedPost: null,
  stocks: [],
  posts: [],
  comments: [],
  ratings: [],
  payments: [],
  purchasedPosts: [],
  likedPosts: [],
  isLoading: false,
  currentView: 'home',
  platformBalance: 0,
  rechargeHistory: [],
  products: [],
  cart: [],
  orders: [],
  selectedProduct: null
});

export const createDemoState = (): AppState => {
  const savedStockPool = loadSavedStockPool();
  // 社区/商城 UGC 本地持久化：刷新后恢复用户的发帖/评论/点赞/购买/下单与余额，
  // 缺省回退到 mock 种子数据。
  const savedCommunity = loadSavedCommunity();
  const user = savedCommunity?.userBalance != null
    ? { ...mockUser, balance: savedCommunity.userBalance }
    : mockUser;
  return {
    ...createLoggedOutState(),
    user,
    stocks: (savedStockPool || mockStocks).map(stock => enrichDefaultStock({ ...stock })),
    posts: savedCommunity?.posts ?? mockPosts,
    comments: savedCommunity?.comments ?? mockComments,
    ratings: savedCommunity?.ratings ?? mockRatings,
    payments: savedCommunity?.payments ?? mockPayments,
    purchasedPosts: savedCommunity?.purchasedPosts ?? [],
    likedPosts: savedCommunity?.likedPosts ?? [],
    rechargeHistory: mockRechargeHistory,
    platformBalance: mockRechargeHistory.reduce((sum, record) => sum + record.amount, 0),
    products: mockProducts,
    cart: savedCommunity?.cart ?? [],
    orders: savedCommunity?.orders ?? mockOrders
  };
};

/**
 * 真实登录成功后的初始状态：复用 demo 的本地持久化恢复（自选/社区/余额），
 * 但把账户换成后端返回的真实用户。余额优先取本地已存的社区余额，否则用账号自带值。
 */
export const createLoggedInState = (user: User): AppState => {
  const base = createDemoState();
  const savedCommunity = loadSavedCommunity();
  const balance = savedCommunity?.userBalance != null ? savedCommunity.userBalance : user.balance;
  return { ...base, user: { ...user, balance } };
};

export const getProductVariant = (product: Product, variantId: string): ProductVariant => {
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

export const getInitialState = (): AppState => {
  // 仅显式 REACT_APP_AUTH_BYPASS=true 才进演示态；其余一律登出态（移除"dev 默认免登录"，与 App.tsx 对齐）。
  const AUTH_BYPASS_ENABLED = process.env.REACT_APP_AUTH_BYPASS?.toLowerCase() === 'true';
  return AUTH_BYPASS_ENABLED ? createDemoState() : createLoggedOutState();
};

export function appReducer(state: AppState, action: AppAction): AppState {
  switch (action.type) {
    case 'LOGIN':
      return createDemoState();

    case 'LOGIN_SUCCESS':
      return createLoggedInState(action.payload);

    case 'LOGOUT':
      return createLoggedOutState();

    case 'SET_VIEW':
      return {
        ...state,
        currentView: action.payload,
        selectedStock: null,
        selectedPost: null,
        selectedProduct: null
      };

    case 'SELECT_STOCK':
      return {
        ...state,
        selectedStock: action.payload,
        selectedPost: null,
        selectedProduct: null,
        currentView: action.view || 'stock-community'
      };

    case 'BACK_TO_STOCKS':
      return {
        ...state,
        selectedStock: null,
        selectedPost: null,
        currentView: 'stocks'
      };

    case 'SELECT_POST':
      return {
        ...state,
        currentView: 'post-detail',
        selectedProduct: null,
        selectedPost: action.payload
      };

    case 'SELECT_PRODUCT':
      return {
        ...state,
        selectedProduct: action.payload,
        selectedPost: null,
        selectedStock: null,
        currentView: 'product-detail'
      };

    case 'CREATE_POST':
      return {
        ...state,
        currentView: 'create-post'
      };

    case 'LIKE_POST': {
      const postId = action.payload;
      // 点赞去重：已赞则取消（likes-1），未赞则点赞（likes+1），避免无限刷赞产生虚假数据。
      const alreadyLiked = state.likedPosts.includes(postId);
      const delta = alreadyLiked ? -1 : 1;
      const applyDelta = (likes: number) => Math.max(0, likes + delta);
      return {
        ...state,
        likedPosts: alreadyLiked
          ? state.likedPosts.filter(id => id !== postId)
          : [...state.likedPosts, postId],
        posts: state.posts.map(post =>
          post.id === postId
            ? { ...post, likes: applyDelta(post.likes) }
            : post
        ),
        selectedPost: state.selectedPost?.id === postId
          ? { ...state.selectedPost, likes: applyDelta(state.selectedPost.likes) }
          : state.selectedPost
      };
    }

    case 'SHARE_POST': {
      const postId = action.payload;
      return {
        ...state,
        posts: state.posts.map(post =>
          post.id === postId
            ? { ...post, shares: post.shares + 1 }
            : post
        ),
        selectedPost: state.selectedPost?.id === postId
          ? { ...state.selectedPost, shares: state.selectedPost.shares + 1 }
          : state.selectedPost
      };
    }

    case 'ADD_TO_CART': {
      const { product, variantId, quantity } = action.payload;
      const variant = getProductVariant(product, variantId);
      const existingItem = state.cart.find(item => item.productId === product.id && item.variantId === variant.id);

      if (variant.stock <= 0) {
        return state;
      }

      if (existingItem && existingItem.quantity + quantity > variant.stock) {
        return state;
      }

      if (!existingItem && quantity > variant.stock) {
        return state;
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

      return {
        ...state,
        cart: existingItem
          ? state.cart.map(item =>
              item.id === existingItem.id
                ? { ...item, quantity: item.quantity + quantity }
                : item
            )
          : [...state.cart, cartItem]
      };
    }

    case 'REMOVE_FROM_CART':
      return {
        ...state,
        cart: state.cart.filter(item => item.id !== action.payload)
      };

    case 'CHECKOUT': {
      const items = action.payload;
      if (!state.user) {
        return state;
      }

      if (items.length === 0) {
        return state;
      }

      const order: AppState['orders'][number] = {
        id: `order_${Date.now()}`,
        userId: state.user.id,
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

      return {
        ...state,
        orders: [order, ...state.orders],
        cart: state.cart.filter(item => !items.find(i => i.id === item.id)),
        currentView: 'orders'
      };
    }

    case 'RECHARGE': {
      const { amount, method } = action.payload;
      if (!state.user) return state;

      const transactionId = `${method.toUpperCase()}${Date.now()}`;
      const newRechargeRecord = {
        id: `recharge_${Date.now()}`,
        userId: state.user.id,
        amount,
        method,
        status: 'success' as const,
        createdAt: new Date().toISOString(),
        transactionId
      };

      return {
        ...state,
        user: { ...state.user, balance: state.user.balance + amount },
        rechargeHistory: [newRechargeRecord, ...state.rechargeHistory],
        platformBalance: state.platformBalance + amount
      };
    }

    case 'ORDER_PAY': {
      const { orderId, paymentMethod } = action.payload;
      return {
        ...state,
        orders: state.orders.map(o =>
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
      };
    }

    case 'ORDER_CANCEL':
      return {
        ...state,
        orders: state.orders.map(o =>
          o.id === action.payload
            ? { ...o, orderStatus: 'cancelled' as const }
            : o
        )
      };

    case 'ORDER_REFUND':
      return {
        ...state,
        orders: state.orders.map(o =>
          o.id === action.payload
            ? { ...o, paymentStatus: 'refunded' as const }
            : o
        )
      };

    case 'REMOVE_STOCK': {
      const symbol = action.payload;
      return {
        ...state,
        stocks: state.stocks.filter(stock => stock.symbol !== symbol),
        selectedStock: state.selectedStock?.symbol === symbol ? null : state.selectedStock,
        currentView: state.selectedStock?.symbol === symbol ? 'stocks' : state.currentView
      };
    }

    case 'TOGGLE_STOCK_SUBSCRIPTION': {
      const symbol = action.payload;
      return {
        ...state,
        stocks: state.stocks.map(stock => {
          if (stock.symbol !== symbol) return stock;
          return {
            ...stock,
            isSubscribed: !(stock.isSubscribed ?? true),
            subscriptionTopics: stock.subscriptionTopics?.length
              ? stock.subscriptionTopics
              : DEFAULT_STOCK_SUBSCRIPTION_TOPICS
          };
        }),
        selectedStock: state.selectedStock?.symbol === symbol
          ? {
              ...state.selectedStock,
              isSubscribed: !(state.selectedStock.isSubscribed ?? true),
              subscriptionTopics: state.selectedStock.subscriptionTopics?.length
                ? state.selectedStock.subscriptionTopics
                : DEFAULT_STOCK_SUBSCRIPTION_TOPICS
            }
          : state.selectedStock
      };
    }

    case 'SET_LOADING':
      return {
        ...state,
        isLoading: action.payload
      };

    case 'SET_APP_STATE':
      return {
        ...state,
        ...action.payload
      };

    default:
      return state;
  }
}
