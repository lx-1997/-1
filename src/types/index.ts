// 基础类型
export interface User {
  id: string;
  username: string;
  email: string;
  avatar: string;
  reputation: number; // 用户评分
  memberLevel: 'free' | 'premium' | 'vip'; // 会员等级
  joinDate: string;
  totalPosts: number;
  totalEarnings: number; // 总收益
  followers: number;
  following: number;
  balance: number; // 账户余额
}

export interface Stock {
  symbol: string;
  name: string;
  market?: 'US' | 'HK' | 'CN' | 'OTHER';
  exchange?: string;
  currency?: string;
  quoteId?: string;
  isSubscribed?: boolean;
  subscriptionTopics?: Array<'price' | 'news' | 'earnings' | 'research' | 'alerts'>;
  addedAt?: string;
  sector: string;
  marketCap: number;
  currentPrice: number;
  changePercent: number;
  priceChange?: number;
  previousClose?: number;
  quoteVolume?: number;
  quoteProvider?: string;
  quoteProviderName?: string;
  quoteMarketTime?: string | null;
  quoteFetchedAt?: string;
  quoteIsRealtime?: boolean;
  quoteDelayNote?: string;
  description: string;
  focusLevel: 'high' | 'medium' | 'low'; // 关注度
  totalPosts: number;
  totalPaidPosts: number;
  communityScore: number; // 社区活跃度评分
}

// 内容相关类型
export interface Post {
  id: string;
  author: User;
  stockSymbol: string;
  title: string;
  content: string;
  summary: string;
  type: 'news' | 'analysis' | 'discussion' | 'qa';
  category: 'technical' | 'fundamental' | 'market' | 'earnings' | 'product' | 'regulatory';
  tags: string[];
  publishTime: string;
  updateTime: string;
  
  // 付费相关
  isPaid: boolean;
  price: number; // 付费价格
  paidViewers: number; // 付费查看人数
  totalRevenue: number; // 总收入
  
  // 互动数据
  likes: number;
  comments: number;
  shares: number;
  views: number;
  
  // 质量评分
  qualityScore: number; // 基于付费用户反馈的质量评分
  totalRatings: number; // 总评分数量
  
  // 状态
  status: 'draft' | 'published' | 'archived' | 'flagged';
  isPinned: boolean;
  isHighlighted: boolean;
}

export interface Comment {
  id: string;
  postId: string;
  author: User;
  content: string;
  publishTime: string;
  likes: number;
  replies: Comment[];
  isPaid: boolean; // 是否付费评论
}

export interface Rating {
  id: string;
  postId: string;
  userId: string;
  rating: number; // 1-5星评分
  feedback: string; // 反馈内容
  ratingTime: string;
  isAnonymous: boolean;
}

export interface Payment {
  id: string;
  userId: string;
  postId: string;
  amount: number;
  paymentTime: string;
  status: 'pending' | 'completed' | 'failed' | 'refunded';
}

// 充值记录
export interface RechargeRecord {
  id: string;
  userId: string;
  amount: number;
  method: string;
  status: 'pending' | 'success' | 'failed';
  createdAt: string;
  transactionId?: string;
}

// 商城相关类型
export interface ProductVariant {
  id: string;
  name: string; // 款式名称，如"红色-大号"、"蓝色-中号"
  sku: string; // 商品SKU
  price: number; // 价格
  stock: number; // 库存
  image?: string; // 款式图片
  attributes: Record<string, string>; // 属性，如 { color: '红色', size: '大号' }
}

export interface Product {
  id: string;
  name: string;
  description: string;
  images: string[]; // 商品图片列表
  category: string; // 商品分类
  price: number; // 基础价格
  originalPrice?: number; // 原价（用于显示折扣）
  variants: ProductVariant[]; // 商品款式
  stock: number; // 总库存
  sales: number; // 销量
  rating: number; // 评分（1-5）
  ratingCount: number; // 评价数量
  tags: string[]; // 标签
  status: 'on_sale' | 'out_of_stock' | 'draft'; // 状态
  createdAt: string;
  updatedAt: string;
}

export interface CartItem {
  id: string;
  productId: string;
  product: Product;
  variantId: string;
  variant: ProductVariant;
  quantity: number;
  addedAt: string;
}

export interface Order {
  id: string;
  userId: string;
  items: OrderItem[];
  totalAmount: number;
  paymentMethod: 'wechat' | 'alipay' | 'balance'; // 支付方式
  paymentStatus: 'pending' | 'paid' | 'failed' | 'refunded'; // 支付状态
  orderStatus: 'pending' | 'processing' | 'shipped' | 'delivered' | 'cancelled'; // 订单状态
  shippingAddress?: ShippingAddress;
  createdAt: string;
  paidAt?: string;
  transactionId?: string; // 支付交易ID
}

export interface OrderItem {
  id: string;
  productId: string;
  productName: string;
  variantId: string;
  variantName: string;
  quantity: number;
  price: number;
  image?: string;
}

export interface ShippingAddress {
  id: string;
  name: string;
  phone: string;
  province: string;
  city: string;
  district: string;
  address: string;
  postalCode?: string;
  isDefault: boolean;
}

// 视图类型
export type ViewType = 'home' | 'stocks' | 'stock-detail' | 'stock-community' | 'post-detail' | 'profile' | 'create-post' | 'shop' | 'product-detail' | 'cart' | 'orders' | 'ai-research' | 'agent-center' | 'data-sources' | 'research-workbench' | 'realtime-messages' | 'mcp-center' | 'skills' | 'earnings-calendar';

// 应用状态
export interface AppState {
  user: User | null;
  selectedStock: Stock | null;
  selectedPost: Post | null; // 添加选中的帖子
  stocks: Stock[];
  posts: Post[];
  comments: Comment[];
  ratings: Rating[];
  payments: Payment[];
  purchasedPosts: string[]; // 用户已购买的帖子ID列表
  isLoading: boolean;
  currentView: ViewType;
  platformBalance: number; // 平台总余额
  rechargeHistory: RechargeRecord[]; // 充值记录
  // 商城相关
  products: Product[]; // 商品列表
  cart: CartItem[]; // 购物车
  orders: Order[]; // 订单列表
  selectedProduct: Product | null; // 当前选中的商品
}
