/** Legacy unowned caches are deliberately not assigned to whichever account logs in next. */
export function accountStorageKey(base: string, accountId: string | null): string {
  return `${base}:v2:${accountId ? `user:${encodeURIComponent(accountId)}` : 'guest'}`;
}
export function readAccountStorage<T>(base: string, accountId: string | null, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(accountStorageKey(base, accountId));
    return raw ? JSON.parse(raw) as T : fallback;
  } catch { return fallback; }
}
export function writeAccountStorage(base: string, accountId: string | null, value: unknown): void {
  try { window.localStorage.setItem(accountStorageKey(base, accountId), JSON.stringify(value)); } catch { /* quota/private mode */ }
}
