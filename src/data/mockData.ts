import { User, Stock, Post, Comment, Rating, Payment, RechargeRecord, Product, Order } from '../types';

// 模拟股票数据
export const mockStocks: Stock[] = [
  {
    symbol: 'TSLA',
    name: '特斯拉',
    sector: '汽车制造',
    marketCap: 850000000000,
    currentPrice: 245.67,
    changePercent: 2.34,
    priceChange: 5.62,
    previousClose: 240.05,
    quoteVolume: 0,
    quoteProvider: 'mock',
    quoteProviderName: '本地样例',
    quoteFetchedAt: '2024-10-01T08:00:00Z',
    quoteIsRealtime: false,
    quoteDelayNote: '等待行情接口刷新',
    description: '全球领先的电动汽车和清洁能源公司，专注于电动汽车、能源存储和太阳能板的生产和销售。',
    focusLevel: 'high',
    totalPosts: 156,
    totalPaidPosts: 23,
    communityScore: 85
  },
  {
    symbol: 'NVDA',
    name: '英伟达',
    sector: '半导体',
    marketCap: 1200000000000,
    currentPrice: 485.23,
    changePercent: -1.23,
    priceChange: -6.03,
    previousClose: 491.26,
    quoteVolume: 0,
    quoteProvider: 'mock',
    quoteProviderName: '本地样例',
    quoteFetchedAt: '2024-10-01T08:00:00Z',
    quoteIsRealtime: false,
    quoteDelayNote: '等待行情接口刷新',
    description: '全球领先的图形处理器和人工智能计算公司，在游戏、数据中心、自动驾驶等领域处于领先地位。',
    focusLevel: 'high',
    totalPosts: 89,
    totalPaidPosts: 15,
    communityScore: 78
  },
  {
    symbol: 'AAPL',
    name: '苹果',
    sector: '消费电子',
    marketCap: 2800000000000,
    currentPrice: 178.45,
    changePercent: 0.87,
    priceChange: 1.54,
    previousClose: 176.91,
    quoteVolume: 0,
    quoteProvider: 'mock',
    quoteProviderName: '本地样例',
    quoteFetchedAt: '2024-10-01T08:00:00Z',
    quoteIsRealtime: false,
    quoteDelayNote: '等待行情接口刷新',
    description: '全球领先的消费电子和软件公司，以iPhone、iPad、Mac等产品闻名，拥有强大的生态系统。',
    focusLevel: 'medium',
    totalPosts: 67,
    totalPaidPosts: 8,
    communityScore: 65
  },
  {
    symbol: 'MSFT',
    name: '微软',
    sector: '软件服务',
    marketCap: 2100000000000,
    currentPrice: 378.85,
    changePercent: 1.10,
    priceChange: 4.12,
    previousClose: 374.73,
    quoteVolume: 0,
    quoteProvider: 'mock',
    quoteProviderName: '本地样例',
    quoteFetchedAt: '2024-10-01T08:00:00Z',
    quoteIsRealtime: false,
    quoteDelayNote: '等待行情接口刷新',
    description: '全球领先的软件公司，Windows操作系统、Office办公软件、Azure云服务等产品占据市场主导地位。',
    focusLevel: 'medium',
    totalPosts: 45,
    totalPaidPosts: 6,
    communityScore: 58
  }
];

// 模拟用户数据
export const mockUser: User = {
  id: '1',
  username: 'demo',
  email: 'demo@example.com',
  avatar: 'https://api.dicebear.com/7.x/avataaars/svg?seed=1',
  reputation: 75,
  memberLevel: 'premium',
  joinDate: '2024-01-01',
  totalPosts: 12,
  totalEarnings: 156.80,
  followers: 89,
  following: 23,
  balance: 50.00 // 添加账户余额
};

// 模拟内容数据
export const mockPosts: Post[] = [
  {
    id: '1',
    author: mockUser,
    stockSymbol: 'TSLA',
    title: '深度分析：特斯拉FSD技术进展与投资价值',
    content: '基于最新的技术专利分析和路测数据，我认为特斯拉的FSD技术将在未来2年内实现重大突破。从技术角度来看，特斯拉在计算机视觉、神经网络训练和硬件集成方面具有显著优势...',
    summary: '深入分析特斯拉FSD自动驾驶技术的技术进展、市场前景和投资价值，基于最新专利数据和路测结果。',
    type: 'analysis',
    category: 'technical',
    tags: ['FSD', '自动驾驶', '技术分析', '投资价值'],
    publishTime: '2024-10-01T08:00:00Z',
    updateTime: '2024-10-01T08:00:00Z',
    isPaid: true,
    price: 9.99,
    paidViewers: 45,
    totalRevenue: 449.55,
    likes: 156,
    comments: 23,
    shares: 12,
    views: 892,
    qualityScore: 92,
    totalRatings: 38,
    status: 'published',
    isPinned: true,
    isHighlighted: true
  },
  {
    id: '2',
    author: mockUser,
    stockSymbol: 'NVDA',
    title: '英伟达AI芯片市场分析：H200发布后的竞争格局',
    content: '英伟达最新发布的H200 AI芯片在性能上相比前代产品有显著提升，这进一步巩固了其在AI计算领域的领先地位...',
    summary: '分析英伟达H200芯片发布后的市场竞争格局，评估其在AI基础设施领域的长期优势。',
    type: 'analysis',
    category: 'product',
    tags: ['AI芯片', 'H200', '市场竞争', '技术优势'],
    publishTime: '2024-10-01T07:30:00Z',
    updateTime: '2024-10-01T07:30:00Z',
    isPaid: false,
    price: 0,
    paidViewers: 0,
    totalRevenue: 0,
    likes: 89,
    comments: 15,
    shares: 8,
    views: 456,
    qualityScore: 78,
    totalRatings: 12,
    status: 'published',
    isPinned: false,
    isHighlighted: true
  },
  {
    id: '3',
    author: mockUser,
    stockSymbol: 'TSLA',
    title: '特斯拉Q3财报解读：交付量超预期的背后',
    content: '特斯拉第三季度交付量达到43.5万辆，超出市场预期的42万辆。这一成绩的取得主要得益于中国市场需求强劲...',
    summary: '解读特斯拉Q3财报亮点，分析交付量超预期的原因和未来增长潜力。',
    type: 'news',
    category: 'earnings',
    tags: ['Q3财报', '交付量', '中国市场', '增长潜力'],
    publishTime: '2024-10-01T06:00:00Z',
    updateTime: '2024-10-01T06:00:00Z',
    isPaid: true,
    price: 4.99,
    paidViewers: 67,
    totalRevenue: 334.33,
    likes: 234,
    comments: 31,
    shares: 19,
    views: 1234,
    qualityScore: 88,
    totalRatings: 52,
    status: 'published',
    isPinned: false,
    isHighlighted: false
  },
  {
    id: '4',
    author: mockUser,
    stockSymbol: 'NVDA',
    title: '英伟达股价分析：AI浪潮下的投资机会',
    content: '英伟达作为AI芯片领域的领导者，在ChatGPT等大语言模型爆发的背景下，其数据中心业务迎来了前所未有的增长机遇。从技术面来看，英伟达的股价在突破前期高点后，形成了良好的上升趋势。基本面方面，公司Q3财报显示数据中心收入同比增长206%，游戏业务也保持稳定增长。建议投资者关注回调机会，长期持有。',
    summary: '分析英伟达在AI浪潮下的投资价值，从技术面和基本面两个维度评估投资机会。',
    type: 'analysis',
    category: 'technical',
    tags: ['AI芯片', '技术分析', '投资机会', '长期持有'],
    publishTime: '2024-10-01T05:30:00Z',
    updateTime: '2024-10-01T05:30:00Z',
    isPaid: false,
    price: 0,
    paidViewers: 0,
    totalRevenue: 0,
    likes: 89,
    comments: 12,
    shares: 6,
    views: 567,
    qualityScore: 82,
    totalRatings: 15,
    status: 'published',
    isPinned: false,
    isHighlighted: true
  }
];

// 模拟评论数据
export const mockComments: Comment[] = [
  {
    id: '1',
    postId: '1',
    author: mockUser,
    content: '分析很深入，特别是对FSD技术进展的评估很有见地。',
    publishTime: '2024-10-01T09:00:00Z',
    likes: 12,
    replies: [],
    isPaid: false
  },
  {
    id: '2',
    postId: '1',
    author: mockUser,
    content: '请问您认为FSD完全自动驾驶还需要多长时间？',
    publishTime: '2024-10-01T10:00:00Z',
    likes: 5,
    replies: [],
    isPaid: false
  }
];

// 模拟评分数据
export const mockRatings: Rating[] = [
  {
    id: '1',
    postId: '1',
    userId: '2',
    rating: 5,
    feedback: '内容质量很高，分析深入，值得付费阅读。',
    ratingTime: '2024-10-01T11:00:00Z',
    isAnonymous: false
  },
  {
    id: '2',
    postId: '1',
    userId: '3',
    rating: 4,
    feedback: '整体不错，但希望能有更多具体的数据支撑。',
    ratingTime: '2024-10-01T12:00:00Z',
    isAnonymous: true
  }
];

// 模拟支付数据
export const mockPayments: Payment[] = [
  {
    id: '1',
    userId: '2',
    postId: '1',
    amount: 9.99,
    paymentTime: '2024-10-01T09:30:00Z',
    status: 'completed'
  },
  {
    id: '2',
    userId: '3',
    postId: '1',
    amount: 9.99,
    paymentTime: '2024-10-01T10:15:00Z',
    status: 'completed'
  }
];

// 模拟充值记录数据
export const mockRechargeHistory: RechargeRecord[] = [
  {
    id: '1',
    userId: '1',
    amount: 100,
    method: 'alipay',
    status: 'success',
    createdAt: '2024-09-28T14:30:00Z',
    transactionId: 'ALI202409281430001'
  },
  {
    id: '2',
    userId: '1',
    amount: 50,
    method: 'wechatpay',
    status: 'success',
    createdAt: '2024-09-25T09:15:00Z',
    transactionId: 'WX202409250915001'
  },
  {
    id: '3',
    userId: '1',
    amount: 200,
    method: 'bankcard',
    status: 'success',
    createdAt: '2024-09-20T16:45:00Z',
    transactionId: 'BANK202409201645001'
  },
  {
    id: '4',
    userId: '2',
    amount: 80,
    method: 'alipay',
    status: 'success',
    createdAt: '2024-09-18T11:20:00Z',
    transactionId: 'ALI202409181120002'
  },
  {
    id: '5',
    userId: '3',
    amount: 150,
    method: 'wechatpay',
    status: 'success',
    createdAt: '2024-09-15T13:10:00Z',
    transactionId: 'WX202409151310003'
  }
];

// 模拟商品数据
export const mockProducts: Product[] = [
  {
    id: 'prod_1',
    name: 'iPhone 15 Pro Max 256GB',
    description: '全新iPhone 15 Pro Max，采用钛金属材质，配备A17 Pro芯片，支持5G网络。6.7英寸超视网膜XDR显示屏，4800万像素主摄像头，支持ProRes视频录制。',
    images: [
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/iphone-15-pro-max-natural-titanium_AV1?wid=940&hei=1112&fmt=png-alpha&qlt=90',
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/iphone-15-pro-max-natural-titanium_AV2?wid=940&hei=1112&fmt=png-alpha&qlt=90',
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/iphone-15-pro-max-natural-titanium_AV3?wid=940&hei=1112&fmt=png-alpha&qlt=90'
    ],
    category: '电子产品',
    price: 9999,
    originalPrice: 10999,
    variants: [
      {
        id: 'var_1_1',
        name: '深空黑色-256GB',
        sku: 'IP15PM-BLK-256',
        price: 9999,
        stock: 50,
        attributes: { color: '深空黑色', storage: '256GB' }
      },
      {
        id: 'var_1_2',
        name: '自然钛金属色-256GB',
        sku: 'IP15PM-TIT-256',
        price: 9999,
        stock: 30,
        attributes: { color: '自然钛金属色', storage: '256GB' }
      },
      {
        id: 'var_1_3',
        name: '深空黑色-512GB',
        sku: 'IP15PM-BLK-512',
        price: 11999,
        stock: 20,
        attributes: { color: '深空黑色', storage: '512GB' }
      }
    ],
    stock: 100,
    sales: 1250,
    rating: 4.8,
    ratingCount: 356,
    tags: ['热销', '新品', '5G'],
    status: 'on_sale',
    createdAt: '2024-09-15T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_2',
    name: 'MacBook Pro 14英寸 M3芯片',
    description: 'MacBook Pro 14英寸，搭载M3芯片，8核CPU和10核GPU，16GB统一内存，512GB SSD。Liquid Retina XDR显示屏，支持P3广色域。',
    images: [
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/mbp14-spacegray-select-202310?wid=940&hei=1112&fmt=png-alpha&qlt=90',
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/mbp14-spacegray-select-202310-2?wid=940&hei=1112&fmt=png-alpha&qlt=90'
    ],
    category: '电子产品',
    price: 14999,
    originalPrice: 15999,
    variants: [
      {
        id: 'var_2_1',
        name: '深空灰色-512GB',
        sku: 'MBP14-SG-512',
        price: 14999,
        stock: 25,
        attributes: { color: '深空灰色', storage: '512GB' }
      },
      {
        id: 'var_2_2',
        name: '银色-512GB',
        sku: 'MBP14-SLV-512',
        price: 14999,
        stock: 15,
        attributes: { color: '银色', storage: '512GB' }
      }
    ],
    stock: 40,
    sales: 680,
    rating: 4.9,
    ratingCount: 189,
    tags: ['专业', '高性能', 'M3芯片'],
    status: 'on_sale',
    createdAt: '2024-09-20T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_3',
    name: 'AirPods Pro 2代',
    description: 'AirPods Pro 2代，主动降噪，空间音频，MagSafe充电盒。H2芯片带来更强大的降噪和音质体验。',
    images: [
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/MQD83?wid=940&hei=1112&fmt=png-alpha&qlt=90',
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/MQD83-2?wid=940&hei=1112&fmt=png-alpha&qlt=90'
    ],
    category: '电子产品',
    price: 1899,
    variants: [
      {
        id: 'var_3_1',
        name: '标准版',
        sku: 'APP2-STD',
        price: 1899,
        stock: 200,
        attributes: {}
      }
    ],
    stock: 200,
    sales: 3200,
    rating: 4.7,
    ratingCount: 892,
    tags: ['热销', '降噪', '无线'],
    status: 'on_sale',
    createdAt: '2024-08-01T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_4',
    name: 'Apple Watch Series 9',
    description: 'Apple Watch Series 9，45mm表壳，GPS+蜂窝网络，S9 SiP芯片，全天候视网膜显示屏。',
    images: [
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/watch-selection-hero-202309_GEO_CN?wid=940&hei=1112&fmt=png-alpha&qlt=90'
    ],
    category: '电子产品',
    price: 3199,
    originalPrice: 3499,
    variants: [
      {
        id: 'var_4_1',
        name: '星光色-45mm',
        sku: 'AW9-STL-45',
        price: 3199,
        stock: 80,
        attributes: { color: '星光色', size: '45mm' }
      },
      {
        id: 'var_4_2',
        name: '午夜色-45mm',
        sku: 'AW9-MID-45',
        price: 3199,
        stock: 60,
        attributes: { color: '午夜色', size: '45mm' }
      }
    ],
    stock: 140,
    sales: 890,
    rating: 4.6,
    ratingCount: 234,
    tags: ['智能手表', '健康监测', 'GPS'],
    status: 'on_sale',
    createdAt: '2024-09-10T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_5',
    name: 'iPad Pro 12.9英寸 M2芯片',
    description: 'iPad Pro 12.9英寸，M2芯片，256GB存储，Liquid Retina XDR显示屏，支持Apple Pencil和Magic Keyboard。',
    images: [
      'https://store.storeimages.cdn-apple.com/8756/as-images.apple.com/is/ipad-pro-12-select-202210?wid=940&hei=1112&fmt=png-alpha&qlt=90'
    ],
    category: '电子产品',
    price: 8999,
    variants: [
      {
        id: 'var_5_1',
        name: '深空灰色-256GB',
        sku: 'IPAD12-SG-256',
        price: 8999,
        stock: 35,
        attributes: { color: '深空灰色', storage: '256GB' }
      },
      {
        id: 'var_5_2',
        name: '银色-256GB',
        sku: 'IPAD12-SLV-256',
        price: 8999,
        stock: 25,
        attributes: { color: '银色', storage: '256GB' }
      }
    ],
    stock: 60,
    sales: 450,
    rating: 4.8,
    ratingCount: 167,
    tags: ['平板电脑', 'M2芯片', '专业'],
    status: 'on_sale',
    createdAt: '2024-08-15T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  }
];

// 模拟订单数据
export const mockOrders: Order[] = [
  {
    id: 'order_1',
    userId: '1',
    items: [
      {
        id: 'item_1',
        productId: 'prod_3',
        productName: 'AirPods Pro 2代',
        variantId: 'var_3_1',
        variantName: '标准版',
        quantity: 1,
        price: 1899,
        image: mockProducts[2].images[0]
      }
    ],
    totalAmount: 1899,
    paymentMethod: 'alipay',
    paymentStatus: 'paid',
    orderStatus: 'delivered',
    createdAt: '2024-09-25T10:30:00Z',
    paidAt: '2024-09-25T10:31:00Z',
    transactionId: 'ALI202409251031001'
  },
  {
    id: 'order_2',
    userId: '1',
    items: [
      {
        id: 'item_2',
        productId: 'prod_4',
        productName: 'Apple Watch Series 9',
        variantId: 'var_4_1',
        variantName: '星光色-45mm',
        quantity: 1,
        price: 3199,
        image: mockProducts[3].images[0]
      }
    ],
    totalAmount: 3199,
    paymentMethod: 'wechat',
    paymentStatus: 'paid',
    orderStatus: 'shipped',
    createdAt: '2024-09-28T14:20:00Z',
    paidAt: '2024-09-28T14:21:00Z',
    transactionId: 'WX202409281421001'
  }
];
