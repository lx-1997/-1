import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { fetchWatchlist, saveWatchlist } from '../services/watchlistService';
import { getAuthSnapshot, isAuthRevisionCurrent } from '../state/authSession';
import { WATCHLIST_UPDATED_EVENT } from '../utils/platformAssistant';

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
  const hydrationGeneration = useRef(0);
  const [retry, setRetry] = useState(0);
  const [loadRetry, setLoadRetry] = useState(0);
  const [loadError, setLoadError] = useState('');
  const [saveError, setSaveError] = useState('');
  const [saving, setSaving] = useState(false);
  const inFlight = useRef<AbortController | null>(null);
  const retrySync = useCallback(() => {
    if (loadError) setLoadRetry(value => value + 1);
    else { setSaveError(''); setRetry(value => value + 1); }
  }, [loadError]);
  const pending = useMemo(() => ({ symbols: EMPTY_SYMBOLS, names: defaultNames }), [defaultNames]);
  const visible = state.owner === accountId && state.ready ? state : pending;
  const watchlistRef = useRef(visible.symbols); watchlistRef.current = visible.symbols;
  const namesRef = useRef(visible.names); namesRef.current = visible.names;

  useLayoutEffect(() => {
    const controller = new AbortController();
    const generation = ++hydrationGeneration.current;
    const revision = getAuthSnapshot().revision;
    setLoadError('');
    const guest = readGuest(defaults, defaultNames);
    setState({ owner: accountId, ...(accountId ? pending : guest), ready: !accountId, dirty: false });
    if (accountId) {
      void fetchWatchlist(controller.signal).then(data => {
        if (controller.signal.aborted || generation !== hydrationGeneration.current || !isAuthRevisionCurrent(revision)) return;
        // empty=false and symbols=[] is an intentional clear, not a first login.
        const firstLogin = data.empty === true;
        setState({ owner: accountId,
          symbols: firstLogin ? guest.symbols : data.symbols,
          names: firstLogin ? guest.names : { ...defaultNames, ...data.names },
          ready: true, dirty: firstLogin });
      }).catch(() => {
        if (!controller.signal.aborted && generation === hydrationGeneration.current && isAuthRevisionCurrent(revision)) {
          setLoadError('自选加载失败，请重试后再编辑，避免覆盖云端列表。');
        }
      });
    }
    return () => controller.abort();
  }, [accountId, sessionRevision, defaults, defaultNames, pending, loadRetry]);

  useEffect(() => {
    let controller: AbortController | null = null;
    const refresh = (event: Event) => {
      const detail = (event as CustomEvent<{ revision: number; accountId: string }>).detail;
      if (!accountId || detail?.accountId !== accountId || !isAuthRevisionCurrent(detail.revision)) return;
      controller?.abort();
      controller = new AbortController();
      const generation = ++hydrationGeneration.current;
      const request = controller;
      // Cancel any pending local save before reloading the assistant's server mutation.
      setState(previous => previous.owner === accountId ? { ...previous, dirty: false } : previous);
      void fetchWatchlist(request.signal).then(data => {
        if (request.signal.aborted || generation !== hydrationGeneration.current || !isAuthRevisionCurrent(detail.revision)) return;
        setState({ owner: accountId, symbols: data.symbols, names: { ...defaultNames, ...data.names }, ready: true, dirty: false });
      }).catch(() => { /* Leave the current list visible if refresh fails. */ });
    };
    window.addEventListener(WATCHLIST_UPDATED_EVENT, refresh);
    return () => { controller?.abort(); window.removeEventListener(WATCHLIST_UPDATED_EVENT, refresh); };
  }, [accountId, defaultNames]);

  const change = useCallback((field: 'symbols' | 'names', value: React.SetStateAction<any>) => {
    if ((getAuthSnapshot().account?.id ?? null) !== accountId) return;
    setState(previous => {
      if (previous.owner !== accountId || !previous.ready) return previous;
      return { ...previous, [field]: typeof value === 'function' ? value(previous[field]) : value, dirty: true };
    });
  }, [accountId]);
  const setWatchlist = useCallback((value: React.SetStateAction<string[]>) => change('symbols', value), [change]);
  const setNames = useCallback((value: React.SetStateAction<Record<string, string>>) => change('names', value), [change]);

  // Cancel on identity change, not on every edit: aborting a POST cannot undo a
  // server commit. Serialize snapshots so a slower old write cannot win last.
  useEffect(() => {
    setSaveError(''); setSaving(false);
    return () => { inFlight.current?.abort(); inFlight.current = null; };
  }, [accountId, sessionRevision]);

  useEffect(() => {
    if (state.owner !== accountId || !state.ready || !state.dirty) return;
    if (!accountId) {
      try { localStorage.setItem('bbt.watchlist', JSON.stringify(state.symbols)); localStorage.setItem('bbt.names', JSON.stringify(state.names)); } catch { /* Storage is optional. */ }
      return;
    }
    if (inFlight.current || saveError) return;
    const revision = getAuthSnapshot().revision;
    const timer = window.setTimeout(() => {
      if (!isAuthRevisionCurrent(revision)) return;
      const controller = new AbortController();
      inFlight.current = controller;
      setSaving(true);
      void saveWatchlist(state.symbols, state.names, controller.signal).then(() => {
        if (controller.signal.aborted || !isAuthRevisionCurrent(revision)) return;
        setState(previous => previous === state ? { ...previous, dirty: false } : previous);
        savedCallback.current?.(state.symbols);
      }).catch(() => {
        if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) {
          setSaveError('自选尚未同步到云端，当前修改已保留在本页，请重试。');
        }
      }).finally(() => {
        if (inFlight.current !== controller) return;
        inFlight.current = null;
        if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) {
          setSaving(false); setRetry(value => value + 1);
        }
      });
    }, 800);
    return () => window.clearTimeout(timer);
  }, [accountId, state, retry, saveError]);
  return { watchlist: visible.symbols, names: visible.names, setWatchlist, setNames, watchlistRef, namesRef,
    ready: state.owner === accountId && state.ready, loadError, saveError,
    syncing: saving || (state.owner === accountId && state.dirty && !!accountId && !saveError), retrySync };
}
