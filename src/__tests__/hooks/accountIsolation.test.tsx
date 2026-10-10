import { act, renderHook } from '@testing-library/react';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession, updateAuthAccount, registerSessionCleanup } from '../../state/authSession';
import { useAccountAiHistory } from '../../hooks/useAccountAiHistory';
import { mapAuthUser, type AuthUser } from '../../services/authService';
import { saveCommunity } from '../../utils/communityPersistence';
import { createLoggedInState } from '../../state/appReducer';
import type { AiHistoryRecord } from '../../utils/aiConversationHistory';

const user = (id: string): AuthUser => ({ id, username: id, email: null, role: 'viewer', is_active: true, created_at: '2026-10-10' });
const answer: AiHistoryRecord = { id: 'a-history', title: 'A private question', question: 'A private question', answer: 'A private answer', mode: 'quick', updatedAt: 1 };
beforeEach(() => { invalidateAuthSession(); localStorage.clear(); });
afterEach(() => invalidateAuthSession());

it('isolates AI history on account switch, preserves it for the owner, and rejects late setters', () => {
  authenticateSession(user('A'), 'test-token-a', 'https://example.test');
  const { result, rerender } = renderHook(({ id }) => useAccountAiHistory<AiHistoryRecord>(id), { initialProps: { id: 'A' as string | null } });
  act(() => result.current[1]([answer]));
  const oldUpdate = result.current[1];
  act(() => authenticateSession(user('B'), 'test-token-b', 'https://example.test'));
  rerender({ id: 'B' });
  expect(result.current[0]).toEqual([]);
  act(() => oldUpdate([{ ...answer, id: 'late-answer', question: 'late' }]));
  expect(result.current[0]).toEqual([]);
  act(() => authenticateSession(user('A'), 'test-token-a2', 'https://example.test'));
  rerender({ id: 'A' });
  expect(result.current[0]).toMatchObject([answer]);
});

it('does not attribute an unowned legacy AI cache to the next login', () => {
  localStorage.setItem('df.ai.conversations.v1', JSON.stringify([answer]));
  authenticateSession(user('B'), 'test-token-b', 'https://example.test');
  const { result } = renderHook(() => useAccountAiHistory<AiHistoryRecord>('B'));
  expect(result.current[0]).toEqual([]);
  expect(localStorage.getItem('df.ai.conversations.v1')).not.toBeNull();
});

it('does not transfer community purchases, orders, balance or cart to a different account', () => {
  const stateA = createLoggedInState(mapAuthUser(user('A')));
  stateA.user!.balance = 137;
  stateA.purchasedPosts = ['private-purchase'];
  stateA.orders = [{ id: 'private-order' } as any];
  stateA.cart = [{ id: 'private-cart' } as any];
  saveCommunity(stateA);
  const stateB = createLoggedInState(mapAuthUser(user('B')));
  expect(stateB.purchasedPosts).toEqual([]);
  expect(stateB.orders).toEqual([]);
  expect(stateB.cart).toEqual([]);
  expect(stateB.user!.balance).toBe(0);
  const restoredA = createLoggedInState(mapAuthUser(user('A')));
  expect(restoredA.purchasedPosts).toEqual(['private-purchase']);
  expect(restoredA.orders[0].id).toBe('private-order');
  expect(restoredA.user!.balance).toBe(137);
});

it('does not let a late unauthorized response invalidate a replacement session', () => {
  authenticateSession(user('A'), 'test-token-a', 'https://example.test');
  authenticateSession(user('B'), 'test-token-b', 'https://example.test');
  expect(invalidateAuthSession('test-token-a')).toBe(false);
  expect(getAuthSnapshot().account?.id).toBe('B');
});


it('invalidates in-flight work when the same account loses its membership or role', () => {
  const member = { ...user('A'), membership: { tier: 'premium' as const, expires_at: '2026-12-10', days_left: 61 } };
  authenticateSession(member, 'test-a', 'https://example.test');
  const revision = getAuthSnapshot().revision;
  const cancel = jest.fn(); registerSessionCleanup(cancel);
  updateAuthAccount({ ...member, membership: null }, 'test-a');
  expect(cancel).toHaveBeenCalledTimes(1);
  expect(getAuthSnapshot().revision).toBe(revision + 1);
  expect(getAuthSnapshot().account?.id).toBe('A');
  const cancelRole = jest.fn(); registerSessionCleanup(cancelRole);
  updateAuthAccount({ ...user('A'), role: 'admin' }, 'test-a');
  expect(cancelRole).toHaveBeenCalledTimes(1);
});

it('does not cancel requests for a membership day counter update', () => {
  const member = { ...user('A'), membership: { tier: 'premium' as const, expires_at: '2026-12-10', days_left: 61 } };
  authenticateSession(member, 'test-a', 'https://example.test');
  const revision = getAuthSnapshot().revision;
  const cancel = jest.fn(); registerSessionCleanup(cancel);
  updateAuthAccount({ ...member, membership: { ...member.membership, days_left: 60 } }, 'test-a');
  expect(cancel).not.toHaveBeenCalled(); expect(getAuthSnapshot().revision).toBe(revision);
});
