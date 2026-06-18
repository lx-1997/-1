import React from 'react';
import { AppState, CartItem, Post, Product, Stock, ViewType } from '../types';
import { MarketSymbolCandidate } from '../services/marketService';
import { getWorkspaceSectionForView, MENU_TO_DEFAULT_VIEW } from '../config/workspaces';
import { lazyWithPreload, PreloadableComponent } from '../utils/lazyWithPreload';
import { MarketSegmentKey } from '../utils/marketSegments';

const StockList = lazyWithPreload(() => import('./StockList'));
const StockDetail = lazyWithPreload(() => import('./StockDetail'));
const StockTearSheet = lazyWithPreload(() => import('./StockTearSheet'));
const PortfolioReview = lazyWithPreload(() => import('./PortfolioReview'));
const MacroReview = lazyWithPreload(() => import('./MacroReview'));
const Briefing = lazyWithPreload(() => import('./Briefing'));
const StockCompare = lazyWithPreload(() => import('./StockCompare'));
const StockCommunity = lazyWithPreload(() => import('./StockCommunity'));
const CreatePost = lazyWithPreload(() => import('./CreatePost'));
const PostDetail = lazyWithPreload(() => import('./PostDetail'));
const RechargeHistory = lazyWithPreload(() => import('./RechargeHistory'));
const PlatformBalance = lazyWithPreload(() => import('./PlatformBalance'));
const ProductDetail = lazyWithPreload(() => import('./ProductDetail'));
const Cart = lazyWithPreload(() => import('./Cart'));
const Orders = lazyWithPreload(() => import('./Orders'));
const Shop = lazyWithPreload(() => import('./Shop'));
const HomePage = lazyWithPreload(() => import('./HomePage'));
const FinGptHub = lazyWithPreload(() => import('./FinGptHub'));
const InvestorAgentCenter = lazyWithPreload(() => import('./InvestorAgentCenter'));
const DulusAgentCenter = lazyWithPreload(() => import('./DulusAgentCenter'));
const DataSourceCenter = lazyWithPreload(() => import('./DataSourceCenter'));
const ResearchWorkbench = lazyWithPreload(() => import('./ResearchWorkbench'));
const RealtimeMessages = lazyWithPreload(() => import('./RealtimeMessages'));
const FinancialTerminal = lazyWithPreload(() => import('./FinancialTerminal'));
const CctvNewsCenter = lazyWithPreload(() => import('./CctvNewsCenter'));
const PeopleSpotlightCenter = lazyWithPreload(() => import('./PeopleSpotlightCenter'));
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
const BacktestCenter = lazyWithPreload(() => import('./BacktestCenter'));
const PremarketOpportunityCenter = lazyWithPreload(() => import('./PremarketOpportunityCenter'));
const RiskDashboard = lazyWithPreload(() => import('./RiskDashboard'));
const PositionMonitor = lazyWithPreload(() => import('./PositionMonitor'));
const MacroDashboard = lazyWithPreload(() => import('./MacroDashboard'));

// 空闲时优先预热的核心工作区（按 view key）。
const IDLE_PRELOAD_VIEWS: ViewType[] = ['stocks', 'ai-research'];

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

const renderFallbackHomePage = (props: MainContentProps): React.ReactElement => (
  <HomePage
    appState={props.appState}
    onStockSelect={props.onStockSelect}
    onProductClick={props.onProductClick}
    onAddToCart={props.onAddToCart}
    onViewChange={props.onViewChange}
    onAddStock={props.onAddStock}
    onRemoveStock={props.onRemoveStock}
    onToggleStockSubscription={props.onToggleStockSubscription}
    onRefreshMarketData={props.onRefreshMarketData}
    isMarketDataRefreshing={props.isMarketDataRefreshing}
  />
);

const SEGMENT_TO_VIEW: Record<MarketSegmentKey, ViewType> = {
  all: 'stocks',
  'a-share': 'a-share-market',
  global: 'global-market',
};

const renderStockList = (
  props: MainContentProps,
  segment: MarketSegmentKey = 'all'
): React.ReactElement => (
  <StockList
    stocks={props.appState.stocks}
    onStockSelect={props.onStockSelect}
    onAddStock={props.onAddStock}
    onRemoveStock={props.onRemoveStock}
    onToggleSubscription={props.onToggleStockSubscription}
    onRefreshMarketData={props.onRefreshMarketData}
    isMarketDataRefreshing={props.isMarketDataRefreshing}
    marketSegment={segment}
    onMarketSegmentChange={(next) => props.onViewChange(SEGMENT_TO_VIEW[next])}
  />
);

type ViewConfig = {
  /** 该视图的主组件，用于按需预热（hover/聚焦时提前加载 chunk）。 */
  component: PreloadableComponent<React.ComponentType<any>>;
  /** 打开本视图时顺带预热的相邻组件（如打开列表时预热详情页）。 */
  also?: Array<PreloadableComponent<React.ComponentType<any>>>;
  condition?: (props: MainContentProps) => boolean;
  render: (props: MainContentProps) => React.ReactElement;
};

const renderWorkspaceSection = (props: MainContentProps, content: React.ReactElement): React.ReactElement => {
  // 首页是统一入口，自带工作区导航网格，无需再套一层工作区 Tab 条。
  if (props.appState.currentView === 'home') {
    return content;
  }

  const section = getWorkspaceSectionForView(props.appState.currentView);
  if (!section || section.views.length <= 1) {
    return content;
  }

  return (
    <div className="workspace-section-frame">
      <div className="workspace-section-tabs" role="tablist" aria-label={`${section.title}工作区`}>
        {section.views.map(item => (
          <button
            key={item.view}
            type="button"
            className={`workspace-section-tab${props.appState.currentView === item.view ? ' active' : ''}`}
            onMouseEnter={() => void preloadMainContentModules(item.view)}
            onFocus={() => void preloadMainContentModules(item.view)}
            onClick={() => props.onViewChange(item.view)}
          >
            <strong>{item.label}</strong>
          </button>
        ))}
      </div>
      <div className="workspace-section-body">
        {content}
      </div>
    </div>
  );
};

const VIEW_RENDER_CONFIG: Record<ViewType, ViewConfig> = {
  'product-detail': {
    component: ProductDetail,
    condition: (props) => !!props.appState.selectedProduct,
    render: (props) => (
      <ProductDetail
        product={props.appState.selectedProduct!}
        onBack={() => props.onViewChange('shop')}
        onAddToCart={props.onAddToCart}
        onBuyNow={props.onBuyNow}
      />
    ),
  },
  'cart': {
    component: Cart,
    render: (props) => (
      <Cart
        cartItems={props.appState.cart}
        onUpdateQuantity={props.onUpdateCartQuantity}
        onRemoveItem={props.onRemoveFromCart}
        onCheckout={props.onCheckout}
        onBack={() => props.onViewChange('shop')}
      />
    ),
  },
  'orders': {
    component: Orders,
    render: (props) => (
      <Orders
        orders={props.appState.orders}
        onPay={props.onOrderPay}
        onCancel={props.onOrderCancel}
        onRefund={props.onOrderRefund}
      />
    ),
  },
  'ai-research': {
    component: FinGptHub,
    render: (props) => (
      <div className="view-enter" key="ai-research">
        <FinGptHub appState={props.appState} />
      </div>
    ),
  },
  'agent-center': {
    component: InvestorAgentCenter,
    render: (props) => <InvestorAgentCenter appState={props.appState} />,
  },
  'dulus-agent': {
    component: DulusAgentCenter,
    render: (props) => <DulusAgentCenter appState={props.appState} />,
  },
  'skills': {
    component: SkillCenter,
    render: (props) => <SkillCenter appState={props.appState} />,
  },
  'data-sources': {
    component: DataSourceCenter,
    render: (props) => <DataSourceCenter appState={props.appState} />,
  },
  'research-workbench': {
    component: ResearchWorkbench,
    render: (props) => <ResearchWorkbench appState={props.appState} onViewChange={props.onViewChange} />,
  },
  'realtime-messages': {
    component: RealtimeMessages,
    render: (props) => <RealtimeMessages appState={props.appState} />,
  },
  'financial-terminal': {
    component: FinancialTerminal,
    render: (props) => <FinancialTerminal appState={props.appState} />,
  },
  'cctv-news': {
    component: CctvNewsCenter,
    render: (props) => <CctvNewsCenter appState={props.appState} />,
  },
  'people-spotlight': {
    component: PeopleSpotlightCenter,
    render: () => <PeopleSpotlightCenter />,
  },
  'mcp-center': {
    component: McpCenter,
    render: (props) => <McpCenter appState={props.appState} />,
  },
  'earnings-calendar': {
    component: EarningsCalendar,
    render: (props) => <EarningsCalendar appState={props.appState} onStockSelect={props.onStockSelect} />,
  },
  'cn-earnings': {
    component: CnEarningsCenter,
    render: (props) => <CnEarningsCenter appState={props.appState} onStockSelect={props.onStockSelect} />,
  },
  'shareholder-changes': {
    component: ShareholderChangeCenter,
    render: (props) => <ShareholderChangeCenter appState={props.appState} onStockSelect={props.onStockSelect} />,
  },
  'major-events': {
    component: MajorEventCenter,
    render: (props) => <MajorEventCenter appState={props.appState} onStockSelect={props.onStockSelect} />,
  },
  'multi-market-decision': {
    component: MultiMarketDecisionCenter,
    render: (props) => <MultiMarketDecisionCenter appState={props.appState} />,
  },
  'premarket-opportunity': {
    component: PremarketOpportunityCenter,
    render: (props) => <PremarketOpportunityCenter appState={props.appState} />,
  },
  'ai-supply-chain': {
    component: AiSupplyChainCycleCenter,
    render: (props) => <AiSupplyChainCycleCenter appState={props.appState} />,
  },
  'customs-trade': {
    component: CustomsTradeCenter,
    render: (props) => <CustomsTradeCenter appState={props.appState} />,
  },
  'options-signal': {
    component: OptionsSignalCenter,
    render: (props) => <OptionsSignalCenter appState={props.appState} />,
  },
  'backtest-center': {
    component: BacktestCenter,
    render: () => <BacktestCenter />,
  },
  'risk-dashboard': {
    component: RiskDashboard,
    also: [PositionMonitor, MacroDashboard],
    render: () => <RiskDashboard />,
  },
  'position-monitor': {
    component: PositionMonitor,
    render: () => <PositionMonitor />,
  },
  'market-dashboard': {
    component: MacroDashboard,
    render: () => <MacroDashboard />,
  },
  'profile': {
    component: ProfileSettings,
    render: (props) => <ProfileSettings appState={props.appState} />,
  },
  'create-post': {
    component: CreatePost,
    render: (props) => {
      if (!props.appState.selectedStock) {
        return renderStockList(props);
      }
      return (
        <CreatePost
          stock={props.appState.selectedStock}
          onSave={props.onSavePost}
          onCancel={() => {
            if (props.appState.selectedStock) {
              props.onViewChange('stock-community');
            } else {
              props.onViewChange('stocks');
            }
          }}
        />
      );
    },
  },
  'post-detail': {
    component: PostDetail,
    condition: (props) => !!props.appState.selectedPost,
    render: (props) => (
      <PostDetail
        post={props.appState.selectedPost!}
        currentUser={props.appState.user!}
        comments={props.appState.comments}
        purchasedPosts={props.appState.purchasedPosts}
        likedPostIds={props.appState.likedPosts}
        onBack={() => {
          if (props.appState.selectedStock) {
            props.onViewChange('stock-community');
          } else {
            props.onViewChange('stocks');
          }
        }}
        onPurchase={props.onPurchase}
        onRate={props.onRate}
        onLike={props.onLike}
        onShare={props.onShare}
        onAddComment={props.onAddComment}
      />
    ),
  },
  'portfolio-review': {
    component: PortfolioReview,
    render: () => <PortfolioReview />,
  },
  'macro-review': {
    component: MacroReview,
    render: () => <MacroReview />,
  },
  'briefing': {
    component: Briefing,
    render: (props) => (
      <Briefing
        onViewChange={props.onViewChange}
        symbols={(props.appState.stocks || []).slice(0, 8).map((s) => s.symbol)}
      />
    ),
  },
  'stock-compare': {
    component: StockCompare,
    render: (props) => (
      <StockCompare
        symbols={(props.appState.stocks || []).slice(0, 6).map((s) => s.symbol)}
        caps={(props.appState.stocks || []).slice(0, 6).map((s) => s.marketCap)}
      />
    ),
  },
  'stock-tear-sheet': {
    component: StockTearSheet,
    also: [StockDetail],
    condition: (props) => !!props.appState.selectedStock,
    render: (props) => (
      <StockTearSheet
        stock={props.appState.selectedStock!}
        onBack={props.onBackToStocks}
        onViewChange={props.onViewChange}
      />
    ),
  },
  'stock-detail': {
    component: StockDetail,
    also: [StockCommunity],
    condition: (props) => !!props.appState.selectedStock,
    render: (props) => (
      <StockDetail
        stock={props.appState.selectedStock!}
        posts={props.appState.posts}
        comments={props.appState.comments}
        onBack={props.onBackToStocks}
        onCreatePost={props.onCreatePost}
        onPostClick={props.onPostClick}
      />
    ),
  },
  'stock-community': {
    component: StockCommunity,
    also: [PostDetail, CreatePost],
    condition: (props) => !!props.appState.selectedStock,
    render: (props) => (
      <StockCommunity
        stock={props.appState.selectedStock!}
        posts={props.appState.posts}
        comments={props.appState.comments}
        likedPostIds={props.appState.likedPosts}
        onBack={props.onBackToStocks}
        onCreatePost={(stock) => {
          props.onStockSelect(stock);
          props.onCreatePost();
        }}
        onPostClick={props.onPostClick}
        onPurchase={(post) => props.onPurchase(post.id, post.price)}
        onRate={(post, rating) => props.onRate(post.id, rating, '')}
        onLike={(post) => props.onLike(post.id)}
        onShare={(post) => props.onShare(post.id)}
        onViewChange={props.onViewChange}
      />
    ),
  },
  'home': {
    component: HomePage,
    render: renderFallbackHomePage,
  },
  'stocks': {
    component: StockList,
    render: (props) => renderStockList(props, 'all'),
  },
  'a-share-market': {
    component: StockList,
    render: (props) => renderStockList(props, 'a-share'),
  },
  'global-market': {
    component: StockList,
    render: (props) => renderStockList(props, 'global'),
  },
  'shop': {
    component: Shop,
    render: (props) => (
      <Shop
        products={props.appState.products}
        onProductClick={props.onProductClick}
        onAddToCart={props.onAddToCart}
      />
    ),
  },
};

// ---- 预加载：全部从上面的单一注册表派生，新增视图无需再单独维护预加载表 ----

/** 把任意 key（view 或工作区 menuKey）解析成已注册的 view。 */
function resolveView(key: string): ViewType | undefined {
  if (key in VIEW_RENDER_CONFIG) {
    return key as ViewType;
  }
  return MENU_TO_DEFAULT_VIEW[key];
}

function preloadersForView(view: ViewType): Array<() => Promise<unknown>> {
  const config = VIEW_RENDER_CONFIG[view];
  if (!config) {
    return [];
  }
  return [config.component, ...(config.also ?? [])].map(component => component.preload);
}

export function preloadMainContentModules(key: string): Promise<void> {
  const view = resolveView(key);
  return runPreloaders(view ? preloadersForView(view) : []);
}

export function preloadCoreWorkspaceModules(): Promise<void> {
  const preloaders = IDLE_PRELOAD_VIEWS.flatMap(preloadersForView);
  return runPreloaders(preloaders, 2);
}

const MainContent: React.FC<MainContentProps> = (props) => {
  const { selectedMenu, appState } = props;

  const config = VIEW_RENDER_CONFIG[appState.currentView];
  if (config && (!config.condition || config.condition(props))) {
    return renderWorkspaceSection(props, config.render(props));
  }

  if (selectedMenu === 'profile') {
    return renderWorkspaceSection(props, <ProfileSettings appState={appState} />);
  }

  if (selectedMenu === 'recharge-history') {
    return renderWorkspaceSection(props, (
      <RechargeHistory
        rechargeHistory={appState.rechargeHistory}
        platformBalance={appState.platformBalance}
      />
    ));
  }

  if (selectedMenu === 'platform-balance') {
    return renderWorkspaceSection(props, (
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
    ));
  }

  return renderWorkspaceSection(props, renderFallbackHomePage(props));
};

export default React.memo(MainContent);
