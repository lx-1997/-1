import { getAuthSnapshot, isAuthRevisionCurrent } from '../state/authSession';
import { WORKSPACE_SECTIONS } from '../config/workspaces';
import type { ViewType } from '../types';

export const PLATFORM_RESULT_EVENT = 'df:platform-result';
export const WATCHLIST_UPDATED_EVENT = 'df:watchlist-updated';

export interface PlatformAction {
  id: string;
  title: string;
  capability_id: string;
  method: string;
  path: string;
  arguments: Record<string, unknown>;
  status: 'completed' | 'needs_confirmation' | 'failed';
  confirmation_token?: string;
  result?: unknown;
  error?: string;
}
export interface PlatformAssistantResult {
  platform_actions?: PlatformAction[];
  navigation?: { view: string; symbol?: string };
}
export interface PlatformResultDetail extends PlatformAssistantResult {
  revision: number;
  requestId: string;
}

const knownViews = new Set<string>([
  ...WORKSPACE_SECTIONS.flatMap(section => section.views.map(item => item.view)),
  'stock-detail', 'stock-community', 'post-detail', 'create-post', 'shop', 'product-detail', 'cart', 'orders',
]);
const delivered = new Set<string>();
let pendingNavigation: PlatformResultDetail | null = null;
let navigator: ((detail: PlatformResultDetail) => void) | null = null;

export function isPlatformView(view: string): view is ViewType { return knownViews.has(view); }
export function createAssistantRequestId(): string {
  return globalThis.crypto?.randomUUID?.() || `assistant-${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/** Publish only receipts belonging to the account that initiated the request. */
export function publishPlatformResult(result: PlatformAssistantResult, revision: number, requestId: string): void {
  if (!isAuthRevisionCurrent(revision) || typeof window === 'undefined') return;
  const actions = Array.isArray(result.platform_actions) ? result.platform_actions.filter(action =>
    action && typeof action.id === 'string' && ['completed', 'needs_confirmation', 'failed'].includes(action.status)) : [];
  const navigation = result.navigation && isPlatformView(result.navigation.view) ? result.navigation : undefined;
  if (!actions.length && !navigation) return;
  const key = JSON.stringify([revision, requestId, actions.map(action => [action.id, action.status]), navigation]);
  if (delivered.has(key)) return;
  delivered.add(key);
  if (delivered.size > 100) delivered.delete(delivered.values().next().value!);
  const detail: PlatformResultDetail = { platform_actions: actions, navigation, revision, requestId };
  window.dispatchEvent(new CustomEvent<PlatformResultDetail>(PLATFORM_RESULT_EVENT, { detail }));
  if (actions.some(action => action.status === 'completed' && !['GET', 'HEAD'].includes((action.method || '').toUpperCase())
    && /\/watchlist(?:$|\/|\?)/.test(action.path))) {
    window.dispatchEvent(new CustomEvent(WATCHLIST_UPDATED_EVENT, { detail: { revision, accountId: getAuthSnapshot().account?.id } }));
  }
  if (navigation) {
    if (navigator) navigator(detail);
    else pendingNavigation = detail;
  }
}

/** A queued navigation lets the terminal lazily open the full platform workspace. */
export function registerPlatformNavigator(callback: (detail: PlatformResultDetail) => void): () => void {
  navigator = callback;
  if (pendingNavigation && isAuthRevisionCurrent(pendingNavigation.revision)) callback(pendingNavigation);
  pendingNavigation = null;
  return () => { if (navigator === callback) navigator = null; };
}
