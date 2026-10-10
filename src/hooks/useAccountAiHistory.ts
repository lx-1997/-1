import { useCallback, useLayoutEffect, useState } from 'react';
import { getAuthSnapshot } from '../state/authSession';
import { readAccountStorage, writeAccountStorage } from '../utils/accountStorage';
import { normalizeAiConversationHistory, type AiHistoryRecord } from '../utils/aiConversationHistory';

const HISTORY_KEY = 'df.ai.conversations';
export function useAccountAiHistory<T extends AiHistoryRecord>(accountId: string | null) {
  const load = useCallback(() => {
    const raw = readAccountStorage<unknown>(HISTORY_KEY, accountId, []);
    return Array.isArray(raw) ? normalizeAiConversationHistory(raw as T[], 30) : [];
  }, [accountId]);
  const [owned, setOwned] = useState(() => ({ owner: accountId, records: load() }));
  useLayoutEffect(() => { setOwned({ owner: accountId, records: load() }); }, [accountId, load]);
  const update = useCallback((value: React.SetStateAction<T[]>) => {
    // A transport from the previous session must not write into the next user's history.
    if ((getAuthSnapshot().account?.id ?? null) !== accountId) return;
    setOwned(previous => {
      const records = previous.owner === accountId ? previous.records : load();
      const next = normalizeAiConversationHistory(typeof value === 'function' ? value(records) : value, 30);
      writeAccountStorage(HISTORY_KEY, accountId, next);
      return { owner: accountId, records: next };
    });
  }, [accountId, load]);
  // Never render the previous owner's answers even for one commit during a switch.
  return [owned.owner === accountId ? owned.records : [], update] as const;
}
