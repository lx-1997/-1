import type { AuthUser } from '../services/authService';

export const AUTH_TOKEN_KEY = 'auth_token';
export const AUTH_CACHE_KEY = 'df_session_cache_v1';
const AUTH_ORIGIN_KEY = 'df_auth_api_origin';

export interface AuthSnapshot {
  account: AuthUser | null;
  token: string | null;
  apiOrigin: string | null;
  revision: number;
}

function readStorage(key: string): string | null {
  try { return typeof window === 'undefined' ? null : window.localStorage.getItem(key); } catch { return null; }
}
function writeStorage(key: string, value: string | null): void {
  try {
    if (typeof window === 'undefined') return;
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch { /* In-memory sessions still work when storage is unavailable. */ }
}
function restored(): Omit<AuthSnapshot, 'revision'> {
  const token = readStorage(AUTH_TOKEN_KEY);
  let account: AuthUser | null = null;
  if (token) {
    try {
      const cache = JSON.parse(readStorage(AUTH_CACHE_KEY) || 'null');
      if (cache && typeof cache.u === 'string' && cache.u) {
        account = { id: cache.i || cache.u, username: cache.u, email: null,
          role: cache.r || 'viewer', is_active: true, created_at: cache.c || '',
          membership: cache.m || null, trial_claimable: !!cache.t };
      }
    } catch { /* A malformed cache must never create an authenticated view. */ }
  }
  return { token, account, apiOrigin: token ? readStorage(AUTH_ORIGIN_KEY) : null };
}

let snapshot: AuthSnapshot = { ...restored(), revision: 0 };
const listeners = new Set<() => void>();
const cleanups = new Set<() => void>();
export function getAuthSnapshot(): AuthSnapshot { return snapshot; }
export function subscribeAuth(listener: () => void): () => void {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}
export function registerSessionCleanup(cleanup: () => void): () => void {
  cleanups.add(cleanup);
  return () => { cleanups.delete(cleanup); };
}
export function isAuthRevisionCurrent(revision: number): boolean { return snapshot.revision === revision; }

export function getAuthorizationScope(account: AuthUser | null): string {
  if (!account) return 'guest';
  // Membership days_left changes daily without changing access. Only access boundaries
  // invalidate transports and permission-sensitive snapshots.
  return JSON.stringify([account.role, account.is_active, account.membership?.tier || 'none', account.username]);
}

function publish(next: Omit<AuthSnapshot, 'revision'>): void {
  const changed = next.token !== snapshot.token || next.account?.id !== snapshot.account?.id
    || next.apiOrigin !== snapshot.apiOrigin
    || getAuthorizationScope(next.account) !== getAuthorizationScope(snapshot.account);
  if (changed) {
    for (const cleanup of Array.from(cleanups)) cleanup();
    cleanups.clear();
  }
  snapshot = { ...next, revision: snapshot.revision + (changed ? 1 : 0) };
  for (const listener of Array.from(listeners)) listener();
}
function persistAccount(account: AuthUser | null): void {
  writeStorage(AUTH_CACHE_KEY, account ? JSON.stringify({ i: account.id, u: account.username,
    m: account.membership ?? null, r: account.role, t: !!account.trial_claimable, c: account.created_at || '' }) : null);
}
export function authenticateSession(account: AuthUser, token: string, apiOrigin: string | null): void {
  // Cancel the previous account's transports before publishing its replacement.
  publish({ account, token, apiOrigin });
  writeStorage(AUTH_TOKEN_KEY, token);
  writeStorage(AUTH_ORIGIN_KEY, apiOrigin);
  persistAccount(account);
}
export function updateAuthAccount(account: AuthUser, expectedToken: string | null): boolean {
  if (!expectedToken || snapshot.token !== expectedToken) return false;
  publish({ ...snapshot, account });
  persistAccount(account);
  return true;
}
export function patchAuthAccount(patch: Partial<AuthUser>, expectedAccountId?: string): void {
  const account = snapshot.account;
  if (!account || (expectedAccountId && account.id !== expectedAccountId)) return;
  updateAuthAccount({ ...account, ...patch }, snapshot.token);
}
export function invalidateAuthSession(expectedToken?: string | null): boolean {
  if (expectedToken !== undefined && snapshot.token !== expectedToken) return false;
  publish({ account: null, token: null, apiOrigin: null });
  writeStorage(AUTH_TOKEN_KEY, null);
  writeStorage(AUTH_ORIGIN_KEY, null);
  persistAccount(null);
  return true;
}
export function restoreAuthSession(): void { publish(restored()); }
