// 移动端底部导航承载内容型一级入口；自选仍保留在侧栏/「更多」中，避免把研究资料藏起来。
export type MobileNavView = 'market' | 'stocks' | 'articles' | 'notes' | 'reports' | 'ai';

export function getMobileNavView(sideNavKey: string, feedFilter: string): MobileNavView {
  if (sideNavKey === 'ai') return 'ai';
  if (sideNavKey === 'stocks' || feedFilter === '自选') return 'stocks';
  if (sideNavKey === 'articles' || feedFilter === '文章') return 'articles';
  if (sideNavKey === 'notes' || feedFilter === '机构纪要') return 'notes';
  if (sideNavKey === 'reports' || feedFilter === '研报') return 'reports';
  return 'market';
}

export function getMobileNavTarget(view: Exclude<MobileNavView, 'ai'>): { sideNavKey: string; feedFilter: string } {
  if (view === 'stocks') return { sideNavKey: 'stocks', feedFilter: '自选' };
  if (view === 'articles') return { sideNavKey: 'articles', feedFilter: '文章' };
  if (view === 'notes') return { sideNavKey: 'notes', feedFilter: '机构纪要' };
  if (view === 'reports') return { sideNavKey: 'reports', feedFilter: '研报' };
  return { sideNavKey: 'market', feedFilter: '快讯' };
}
