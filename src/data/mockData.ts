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

// 模拟研究订阅与模板数据
export const mockProducts: Product[] = [
  {
    id: 'prod_1',
    name: '美股 AI 算力链研报包',
    description: '覆盖 GPU、云厂商、服务器、网络设备、电力与液冷环节，包含核心标的逻辑、产业链传导图、关键风险和可复核资料清单。',
    images: [
      'https://dummyimage.com/900x600/172026/ffffff.png&text=AI+Compute+Research',
      'https://dummyimage.com/900x600/167c80/ffffff.png&text=Evidence+Map',
      'https://dummyimage.com/900x600/2f6f9f/ffffff.png&text=Stock+Coverage'
    ],
    category: '研报包',
    price: 1299,
    originalPrice: 1699,
    variants: [
      {
        id: 'var_1_1',
        name: '标准版：PDF报告',
        sku: 'AI-COMPUTE-PDF',
        price: 1299,
        stock: 999,
        attributes: { delivery: 'PDF', update: '一次性交付' }
      },
      {
        id: 'var_1_2',
        name: '专业版：PDF + 模型表',
        sku: 'AI-COMPUTE-PRO',
        price: 1899,
        stock: 500,
        attributes: { delivery: '报告+模型', update: '月度更新' }
      },
      {
        id: 'var_1_3',
        name: '团队版：含闭门纪要',
        sku: 'AI-COMPUTE-TEAM',
        price: 2999,
        stock: 120,
        attributes: { delivery: '报告+模型+纪要', update: '月度更新' }
      }
    ],
    stock: 999,
    sales: 126,
    rating: 4.8,
    ratingCount: 48,
    tags: ['AI算力', '产业链', '可复核'],
    status: 'on_sale',
    createdAt: '2024-09-15T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_2',
    name: '宏观流动性周报订阅',
    description: '每周跟踪美债利率、美元指数、通胀、就业、联储预期和全球资金流，输出对成长股、周期股与港股流动性的影响路径。',
    images: [
      'https://dummyimage.com/900x600/167c80/ffffff.png&text=Macro+Pulse',
      'https://dummyimage.com/900x600/172026/ffffff.png&text=Liquidity+Dashboard'
    ],
    category: '宏观订阅',
    price: 399,
    originalPrice: 499,
    variants: [
      {
        id: 'var_2_1',
        name: '月度订阅',
        sku: 'MACRO-WEEKLY-MONTH',
        price: 399,
        stock: 999,
        attributes: { cycle: '1个月', delivery: '周报' }
      },
      {
        id: 'var_2_2',
        name: '季度订阅',
        sku: 'MACRO-WEEKLY-QUARTER',
        price: 999,
        stock: 999,
        attributes: { cycle: '3个月', delivery: '周报+月度复盘' }
      }
    ],
    stock: 999,
    sales: 214,
    rating: 4.9,
    ratingCount: 67,
    tags: ['宏观', '流动性', '周报'],
    status: 'on_sale',
    createdAt: '2024-09-20T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_3',
    name: '个股深度尽调模板',
    description: '面向小白和专业用户的个股研究模板，内置商业模式、财务质量、估值、催化剂、风险、证据来源和 AI 复核问题清单。',
    images: [
      'https://dummyimage.com/900x600/2f6f9f/ffffff.png&text=Stock+Template',
      'https://dummyimage.com/900x600/172026/ffffff.png&text=Due+Diligence'
    ],
    category: '研究模板',
    price: 199,
    variants: [
      {
        id: 'var_3_1',
        name: 'Notion版',
        sku: 'DD-TEMPLATE-NOTION',
        price: 199,
        stock: 999,
        attributes: { format: 'Notion', scope: '单人使用' }
      },
      {
        id: 'var_3_2',
        name: 'Excel + Markdown版',
        sku: 'DD-TEMPLATE-XLSX-MD',
        price: 299,
        stock: 999,
        attributes: { format: 'Excel+Markdown', scope: '单人使用' }
      }
    ],
    stock: 999,
    sales: 386,
    rating: 4.7,
    ratingCount: 92,
    tags: ['模板', '小白友好', 'AI复核'],
    status: 'on_sale',
    createdAt: '2024-08-01T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_4',
    name: '财报电话会速读包',
    description: '财报发布后聚合业绩摘要、电话会要点、管理层指引、卖方分歧和关键图表，并标注需要继续追问的变量。',
    images: [
      'https://dummyimage.com/900x600/b7791f/ffffff.png&text=Earnings+Brief'
    ],
    category: '事件服务',
    price: 599,
    originalPrice: 799,
    variants: [
      {
        id: 'var_4_1',
        name: '单公司速读',
        sku: 'EARNINGS-BRIEF-SINGLE',
        price: 599,
        stock: 300,
        attributes: { scope: '单公司', delivery: '24小时内' }
      },
      {
        id: 'var_4_2',
        name: '行业对比速读',
        sku: 'EARNINGS-BRIEF-SECTOR',
        price: 1299,
        stock: 120,
        attributes: { scope: '同业3-5家公司', delivery: '48小时内' }
      }
    ],
    stock: 300,
    sales: 88,
    rating: 4.6,
    ratingCount: 31,
    tags: ['财报', '电话会', '速读'],
    status: 'on_sale',
    createdAt: '2024-09-10T00:00:00Z',
    updatedAt: '2024-10-01T00:00:00Z'
  },
  {
    id: 'prod_5',
    name: '自定义数据源接入服务',
    description: '为团队接入自有研报库、Webhook、行情 API、RSS 或内部文件夹，并配置标签、权限、同步频率和 AI 检索范围。',
    images: [
      'https://dummyimage.com/900x600/12805c/ffffff.png&text=Data+Connector'
    ],
    category: '数据服务',
    price: 2999,
    variants: [
      {
        id: 'var_5_1',
        name: '标准接入',
        sku: 'DATA-CONNECTOR-STD',
        price: 2999,
        stock: 50,
        attributes: { source: '1个数据源', support: '基础配置' }
      },
      {
        id: 'var_5_2',
        name: '团队接入',
        sku: 'DATA-CONNECTOR-TEAM',
        price: 7999,
        stock: 20,
        attributes: { source: '最多5个数据源', support: '标签+权限+同步策略' }
      }
    ],
    stock: 50,
    sales: 24,
    rating: 4.8,
    ratingCount: 18,
    tags: ['数据源', '自定义', '企业服务'],
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
        productName: '个股深度尽调模板',
        variantId: 'var_3_1',
        variantName: 'Notion版',
        quantity: 1,
        price: 199,
        image: mockProducts[2].images[0]
      }
    ],
    totalAmount: 199,
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
        productName: '财报电话会速读包',
        variantId: 'var_4_1',
        variantName: '单公司速读',
        quantity: 1,
        price: 599,
        image: mockProducts[3].images[0]
      }
    ],
    totalAmount: 599,
    paymentMethod: 'wechat',
    paymentStatus: 'paid',
    orderStatus: 'shipped',
    createdAt: '2024-09-28T14:20:00Z',
    paidAt: '2024-09-28T14:21:00Z',
    transactionId: 'WX202409281421001'
  }
];
