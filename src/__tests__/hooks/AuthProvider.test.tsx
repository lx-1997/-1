import React from 'react';
import { act, render, screen } from '@testing-library/react';
import { AuthProvider, useAuth } from '../../context/AuthContext';
import { fetchAccount, type AuthUser } from '../../services/authService';
import { authenticateSession, invalidateAuthSession, getAuthSnapshot, patchAuthAccount } from '../../state/authSession';

jest.mock('../../services/authService', () => ({ fetchAccount: jest.fn() }));
const fetch = fetchAccount as jest.MockedFunction<typeof fetchAccount>;
const account = (id: string): AuthUser => ({ id, username: id, email: null, role: 'viewer', is_active: true, created_at: '' });
function View() { const auth = useAuth(); return <div>{auth.account?.username || 'signed out'}</div>; }
beforeEach(() => { invalidateAuthSession(); localStorage.clear(); jest.clearAllMocks(); });
afterEach(() => invalidateAuthSession());

it('shares login/logout transitions with all consumers', async () => {
  fetch.mockResolvedValue(account('A'));
  render(<AuthProvider><View /><View /></AuthProvider>);
  expect(screen.getAllByText('signed out')).toHaveLength(2);
  await act(async () => authenticateSession(account('A'), 'test-a', 'https://example.test'));
  expect(screen.getAllByText('A')).toHaveLength(2);
  expect(fetch).toHaveBeenCalledTimes(1);
  act(() => invalidateAuthSession('test-a'));
  expect(screen.getAllByText('signed out')).toHaveLength(2);
});

it('ignores a delayed account validation from the previous login', async () => {
  let resolveA!: (value: AuthUser) => void;
  fetch.mockImplementationOnce(() => new Promise(resolve => { resolveA = resolve; }));
  fetch.mockResolvedValueOnce(account('B'));
  authenticateSession(account('A'), 'test-a', 'https://example.test');
  render(<AuthProvider><View /></AuthProvider>);
  await act(async () => authenticateSession(account('B'), 'test-b', 'https://example.test'));
  await act(async () => resolveA({ ...account('A'), username: 'late A' }));
  expect(screen.getByText('B')).toBeInTheDocument();
  expect(getAuthSnapshot().token).toBe('test-b');
});

it('reflects logout from another tab through the shared store', async () => {
  fetch.mockResolvedValue(account('A'));
  authenticateSession(account('A'), 'test-a', 'https://example.test');
  await act(async () => { render(<AuthProvider><View /></AuthProvider>); });
  localStorage.removeItem('auth_token');
  act(() => window.dispatchEvent(new StorageEvent('storage', { key: 'auth_token', newValue: null })));
  expect(screen.getByText('signed out')).toBeInTheDocument();
});


it('does not overwrite a newer permission update with delayed validation for the same token', async () => {
  let resolve!: (value: AuthUser) => void;
  fetch.mockImplementationOnce(() => new Promise(done => { resolve = done; }));
  authenticateSession(account('A'), 'test-a', 'https://example.test');
  render(<AuthProvider><View /></AuthProvider>);
  act(() => patchAuthAccount({ membership: { tier: 'premium', expires_at: '2026-12-10', days_left: 61 } }, 'A'));
  await act(async () => resolve({ ...account('A'), membership: null }));
  expect(getAuthSnapshot().account?.membership?.tier).toBe('premium');
});
