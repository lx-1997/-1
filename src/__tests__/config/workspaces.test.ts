import {
  MENU_TO_DEFAULT_VIEW,
  VIEW_TO_WORKSPACE_MENU,
  WORKSPACE_MENU_KEYS,
  WORKSPACE_SECTIONS,
  getWorkspaceSectionForView
} from '../../config/workspaces';

describe('workspace configuration', () => {
  it('maps every workspace menu to its default view', () => {
    WORKSPACE_SECTIONS.forEach(section => {
      expect(WORKSPACE_MENU_KEYS).toContain(section.menuKey);
      expect(MENU_TO_DEFAULT_VIEW[section.menuKey]).toBe(section.views[0].view);
      expect(VIEW_TO_WORKSPACE_MENU[section.views[0].view]).toBe(section.menuKey);
    });
  });

  it('keeps workspace views unique and discoverable', () => {
    const views = WORKSPACE_SECTIONS.flatMap(section => section.views.map(item => item.view));
    expect(new Set(views).size).toBe(views.length);

    views.forEach(view => {
      expect(getWorkspaceSectionForView(view)?.views.some(item => item.view === view)).toBe(true);
    });
  });

  it('keeps auxiliary pages attached to their parent workspace menu', () => {
    expect(VIEW_TO_WORKSPACE_MENU['stock-detail']).toBe('stocks');
    expect(VIEW_TO_WORKSPACE_MENU['stock-community']).toBe('stocks');
    expect(VIEW_TO_WORKSPACE_MENU.cart).toBe('profile');
    expect(VIEW_TO_WORKSPACE_MENU.orders).toBe('profile');
  });
});
