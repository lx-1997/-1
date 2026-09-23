import { getMobileNavTarget, getMobileNavView } from '../../utils/mobileNavigation';

describe('mobileNavigation', () => {
  it('AI 工作区优先显示 AI 为当前项', () => {
    expect(getMobileNavView('ai', '快讯')).toBe('ai');
  });

  it('切回快讯、自选、研报时同时给出侧栏和内容状态', () => {
    expect(getMobileNavTarget('market')).toEqual({ sideNavKey: 'market', feedFilter: '快讯' });
    expect(getMobileNavTarget('stocks')).toEqual({ sideNavKey: 'stocks', feedFilter: '自选' });
    expect(getMobileNavTarget('articles')).toEqual({ sideNavKey: 'articles', feedFilter: '文章' });
    expect(getMobileNavTarget('notes')).toEqual({ sideNavKey: 'notes', feedFilter: '机构纪要' });
    expect(getMobileNavTarget('reports')).toEqual({ sideNavKey: 'reports', feedFilter: '研报' });
  });

  it('侧栏状态与内容筛选任一命中即可正确高亮', () => {
    expect(getMobileNavView('stocks', '快讯')).toBe('stocks');
    expect(getMobileNavView('market', '自选')).toBe('stocks');
    expect(getMobileNavView('reports', '快讯')).toBe('reports');
    expect(getMobileNavView('market', '研报')).toBe('reports');
    expect(getMobileNavView('articles', '快讯')).toBe('articles');
    expect(getMobileNavView('market', '文章')).toBe('articles');
    expect(getMobileNavView('notes', '快讯')).toBe('notes');
    expect(getMobileNavView('market', '机构纪要')).toBe('notes');
    expect(getMobileNavView('market', '快讯')).toBe('market');
  });
});
