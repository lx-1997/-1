import type { ViewType } from '../types';

export type WorkspaceId =
  | 'research'
  | 'observe'
  | 'equity'
  | 'evidence'
  | 'strategy'
  | 'risk'
  | 'system';

export type WorkspaceGroup = 'workspace' | 'system';

export interface WorkspaceView {
  view: ViewType;
  label: string;
  detail: string;
}

export interface WorkspaceSection {
  id: WorkspaceId;
  menuKey: string;
  sidebarLabelKey: string;
  group: WorkspaceGroup;
  title: string;
  description: string;
  views: WorkspaceView[];
}

export const WORKSPACE_SECTIONS: WorkspaceSection[] = [
  {
    id: 'research',
    menuKey: 'home',
    sidebarLabelKey: 'sidebar.research',
    group: 'workspace',
    title: '投研',
    description: '两个 Agent：分析师做单视角深度研究，圆桌做多角色辩论；数据与记忆打通',
    views: [
      { view: 'home', label: '工作台', detail: '分析师 / 圆桌 对话' },
      { view: 'briefing', label: '晨报', detail: '宏观 × 组合 速览' },
      { view: 'agent-center', label: '深度任务', detail: '分析师后台多步 Run' },
      { view: 'dulus-agent', label: '圆桌', detail: '多角色辩论（完整）' },
      { view: 'research-workbench', label: '研报库', detail: '研报抓取 / 入库' }
    ]
  },
  {
    id: 'observe',
    menuKey: 'stocks',
    sidebarLabelKey: 'sidebar.observe',
    group: 'workspace',
    title: '观察',
    description: '市场、事件、官方新闻和实时信号统一入口',
    views: [
      { view: 'stocks', label: '观察池', detail: '自选标的' },
      { view: 'a-share-market', label: 'A股', detail: '本土市场' },
      { view: 'global-market', label: '全球', detail: '海外市场' },
      { view: 'macro-review', label: '宏观', detail: '风险偏好' },
      { view: 'premarket-opportunity', label: '盘前', detail: '机会雷达' },
      { view: 'earnings-calendar', label: '日历', detail: '财报 / 催化' },
      { view: 'cctv-news', label: '央视', detail: '官方信号' },
      { view: 'people-spotlight', label: '人物', detail: '焦点人物发言' },
      { view: 'realtime-messages', label: '信号流', detail: '推送 / 告警' },
      { view: 'financial-terminal', label: '终端', detail: '彭博式实时面板' }
    ]
  },
  {
    id: 'equity',
    menuKey: 'ai-research',
    sidebarLabelKey: 'sidebar.equityResearch',
    group: 'workspace',
    title: '个股',
    description: '把财报、股东、重大事项收进同一个标的研究面',
    views: [
      { view: 'ai-research', label: '体检', detail: '单标的诊断' },
      { view: 'stock-tear-sheet', label: '速览', detail: '一页纸全景' },
      { view: 'stock-compare', label: '对比', detail: '多标的 PK' },
      { view: 'cn-earnings', label: '财报', detail: 'A股公告' },
      { view: 'shareholder-changes', label: '股东', detail: '增减持' },
      { view: 'major-events', label: '事项', detail: '重大风险' }
    ]
  },
  {
    id: 'evidence',
    menuKey: 'data-sources',
    sidebarLabelKey: 'sidebar.evidence',
    group: 'workspace',
    title: '证据',
    description: '资料、数据源和引用证据的统一仓库',
    views: [
      { view: 'data-sources', label: '证据库', detail: '资料 / 数据源' }
    ]
  },
  {
    id: 'strategy',
    menuKey: 'multi-market-decision',
    sidebarLabelKey: 'sidebar.strategyLab',
    group: 'workspace',
    title: '策略',
    description: '组合、回测、期权和产业专题作为策略插件管理',
    views: [
      { view: 'multi-market-decision', label: '组合', detail: '多市场' },
      { view: 'backtest-center', label: '回测', detail: '验证策略' },
      { view: 'options-signal', label: '期权', detail: '异动雷达' },
      { view: 'ai-supply-chain', label: '供应链', detail: 'AI 产业' },
      { view: 'customs-trade', label: '海关', detail: '进出口' }
    ]
  },
  {
    id: 'risk',
    menuKey: 'risk-dashboard',
    sidebarLabelKey: 'sidebar.risk',
    group: 'workspace',
    title: '风险',
    description: '持仓、宏观和风险仪表盘合并成一个风险工作区',
    views: [
      { view: 'risk-dashboard', label: '仪表盘', detail: '风险摘要' },
      { view: 'portfolio-review', label: '组合速判', detail: '集中度 / 止损' },
      { view: 'position-monitor', label: '持仓', detail: '头寸 / 盈亏' },
      { view: 'market-dashboard', label: '宏观', detail: '指标面板' }
    ]
  },
  {
    id: 'system',
    menuKey: 'profile',
    sidebarLabelKey: 'sidebar.systemHub',
    group: 'system',
    title: '系统',
    description: '模型、工具连接和技能编排集中放在系统区',
    views: [
      { view: 'profile', label: '设置', detail: '模型 / 权限' },
      { view: 'mcp-center', label: '工具', detail: '连接状态' },
      { view: 'skills', label: '技能', detail: '编排调用' }
    ]
  }
];

export const WORKSPACE_MENU_KEYS = WORKSPACE_SECTIONS.map(section => section.menuKey);

const AUXILIARY_VIEW_TO_WORKSPACE_MENU: Partial<Record<ViewType, string>> = {
  'stock-detail': 'stocks',
  'stock-community': 'stocks',
  'post-detail': 'stocks',
  'create-post': 'stocks',
  shop: 'profile',
  'product-detail': 'profile',
  cart: 'profile',
  orders: 'profile'
};

export const VIEW_TO_WORKSPACE_MENU = {
  ...WORKSPACE_SECTIONS.reduce<Partial<Record<ViewType, string>>>((acc, section) => {
  section.views.forEach(item => {
    acc[item.view] = section.menuKey;
  });
  return acc;
  }, {}),
  ...AUXILIARY_VIEW_TO_WORKSPACE_MENU
};

export const MENU_TO_DEFAULT_VIEW = WORKSPACE_SECTIONS.reduce<Record<string, ViewType>>((acc, section) => {
  acc[section.menuKey] = section.views[0].view;
  return acc;
}, {});

export const STOCK_CONTEXT_MENU_KEYS = [
  'home',
  'stocks',
  'ai-research',
  'data-sources',
  'multi-market-decision',
  'risk-dashboard'
];

export function getWorkspaceSectionForView(view: ViewType): WorkspaceSection | undefined {
  return WORKSPACE_SECTIONS.find(section => section.views.some(item => item.view === view));
}
