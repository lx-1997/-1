import { WORKSPACE_SECTIONS } from '../src/config/workspaces';
import type { WorkspaceId } from '../src/config/workspaces';

const MENU_LABEL_BY_WORKSPACE: Record<WorkspaceId, string> = {
  research: 'Research',
  observe: 'Observe',
  equity: 'Equity',
  evidence: 'Evidence',
  strategy: 'Strategy Lab',
  risk: 'Risk',
  system: 'System'
};

export const E2E_WORKSPACE_SECTIONS = WORKSPACE_SECTIONS.map(section => ({
  menu: MENU_LABEL_BY_WORKSPACE[section.id],
  tablist: section.views.length > 1 ? `${section.title}工作区` : null,
  tabs: section.views.length > 1 ? section.views.map(item => item.label) : []
}));
