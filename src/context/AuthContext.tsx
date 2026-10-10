import React, { createContext, useCallback, useContext, useEffect, useRef, useSyncExternalStore } from 'react';
import { fetchAccount } from '../services/authService';
import { getAuthSnapshot, subscribeAuth, updateAuthAccount, patchAuthAccount, restoreAuthSession, isAuthRevisionCurrent } from '../state/authSession';
import type { AuthUser } from '../services/authService';

const AuthContext = createContext<{ refresh: () => Promise<AuthUser | null> } | null>(null);
export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const pending = useRef<{ token: string; promise: Promise<AuthUser | null> } | null>(null);
  const session = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot);
  const refresh = useCallback(async () => {
    const { token, revision } = getAuthSnapshot();
    if (!token) return null;
    if (pending.current?.token === token) return pending.current.promise;
    const promise = fetchAccount().then(account => {
      if (account && isAuthRevisionCurrent(revision) && updateAuthAccount(account, token)) return account;
      return null;
    }).finally(() => { if (pending.current?.promise === promise) pending.current = null; });
    pending.current = { token, promise };
    return promise;
  }, []);
  useEffect(() => { if (session.token) void refresh(); }, [session.token, refresh]);
  useEffect(() => {
    const sync = (event: StorageEvent) => {
      if (!event.key || ['auth_token', 'df_session_cache_v1', 'df_auth_api_origin'].includes(event.key)) restoreAuthSession();
    };
    window.addEventListener('storage', sync);
    return () => window.removeEventListener('storage', sync);
  }, []);
  return <AuthContext.Provider value={{ refresh }}>{children}</AuthContext.Provider>;
};

export function useAuth() {
  const context = useContext(AuthContext);
  const session = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot);
  const accountId = session.account?.id;
  const patchAccount = useCallback((patch: Partial<AuthUser>) => patchAuthAccount(patch, accountId), [accountId]);
  if (!context) throw new Error('useAuth requires AuthProvider');
  return { ...session, refresh: context.refresh, patchAccount };
}
