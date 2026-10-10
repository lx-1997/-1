import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { fetchWatchlist, saveWatchlist } from '../services/watchlistService';
import { getAuthSnapshot, isAuthRevisionCurrent } from '../state/authSession';

interface WatchlistState { owner: string | null; symbols: string[]; names: Record<string, string>; ready: boolean; dirty: boolean }
const EMPTY_SYMBOLS: string[] = [];
function readGuest(defaults: string[], defaultNames: Record<string, string>) {
  let symbols = defaults, names = defaultNames;
  try {
    const saved = JSON.parse(localStorage.getItem('bbt.watchlist') || 'null');
    if (Array.isArray(saved)) symbols = saved.filter((symbol): symbol is string => typeof symbol === 'string');
    const savedNames = JSON.parse(localStorage.getItem('bbt.names') || 'null');
    if (savedNames && typeof savedNames === 'object' && !Array.isArray(savedNames)) names = { ...defaultNames, ...savedNames };
  } catch { /* Storage is optional. */ }
  return { symbols: [...symbols], names: { ...names } };
}

/** Own the hydration and debounced write together so a previous account cannot seed or save the next one's list. */
export function useAccountWatchlist(accountId: string | null, defaults: string[], defaultNames: Record<string, string>, onSaved?: (symbols: string[]) => void) {
  const sessionRevision = getAuthSnapshot().revision;
  const [state, setState] = useState<WatchlistState>(() => ({ owner: accountId, ...readGuest(defaults, defaultNames), ready: !accountId, dirty: false }));
  const savedCallback = useRef(onSaved); savedCallback.current = onSaved;
  const pending = useMemo(() => ({ symbols: EMPTY_SYMBOLS, names: defaultNames }), [defaultNames]);
  const visible = state.owner === accountId && state.ready ? state : pending;
  const watchlistRef = useRef(visible.symbols); watchlistRef.current = visible.symbols;
  const namesRef = useRef(visible.names); namesRef.current = visible.names;

  useLayoutEffect(() => {
    const controller = new AbortController();
    const revision = getAuthSnapshot().revision;
    const guest = readGuest(defaults, defaultNames);
    setState({ owner: accountId, ...(accountId ? pending : guest), ready: !accountId, dirty: false });
    if (accountId) {
      void fetchWatchlist(controller.signal).then(data => {
        if (controller.signal.aborted || !isAuthRevisionCurrent(revision)) return;
        // empty=false and symbols=[] is an intentional clear, not a first login.
        const firstLogin = data.empty === true;
        setState({ owner: accountId,
          symbols: firstLogin ? guest.symbols : data.symbols,
          names: firstLogin ? guest.names : { ...defaultNames, ...data.names },
          ready: true, dirty: firstLogin });
      }).catch(() => { /* Never overwrite a server list after failed hydration. */ });
    }
    return () => controller.abort();
  }, [accountId, sessionRevision, defaults, defaultNames, pending]);

  const change = useCallback((field: 'symbols' | 'names', value: React.SetStateAction<any>) => {
    if ((getAuthSnapshot().account?.id ?? null) !== accountId) return;
    setState(previous => {
      if (previous.owner !== accountId || !previous.ready) return previous;
      return { ...previous, [field]: typeof value === 'function' ? value(previous[field]) : value, dirty: true };
    });
  }, [accountId]);
  const setWatchlist = useCallback((value: React.SetStateAction<string[]>) => change('symbols', value), [change]);
  const setNames = useCallback((value: React.SetStateAction<Record<string, string>>) => change('names', value), [change]);

  useEffect(() => {
    if (state.owner !== accountId || !state.ready || !state.dirty) return;
    if (!accountId) {
      try { localStorage.setItem('bbt.watchlist', JSON.stringify(state.symbols)); localStorage.setItem('bbt.names', JSON.stringify(state.names)); } catch { /* Storage is optional. */ }
      return;
    }
    const controller = new AbortController();
    const revision = getAuthSnapshot().revision;
    const timer = window.setTimeout(() => {
      if (!isAuthRevisionCurrent(revision)) return;
      void saveWatchlist(state.symbols, state.names, controller.signal).then(() => {
        if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) savedCallback.current?.(state.symbols);
      }).catch(() => { /* Retain edits locally; a subsequent edit retries synchronization. */ });
    }, 800);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [accountId, state]);
  return { watchlist: visible.symbols, names: visible.names, setWatchlist, setNames, watchlistRef, namesRef };
}
