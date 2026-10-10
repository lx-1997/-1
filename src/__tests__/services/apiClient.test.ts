import axios, { AxiosError, AxiosHeaders, type InternalAxiosRequestConfig } from 'axios';
import { Capacitor } from '@capacitor/core';
import { apiGet, apiPost, apiTransport, getActiveApiBaseUrl } from '../../services/apiClient';
import { authenticateSession, getAuthSnapshot, invalidateAuthSession } from '../../state/authSession';
import type { AuthUser } from '../../services/authService';

jest.mock('@capacitor/core', () => ({ Capacitor: { isNativePlatform: jest.fn(() => false) } }));
const native = Capacitor.isNativePlatform as jest.Mock;
const user: AuthUser = { id: 'A', username: 'A', email: null, role: 'viewer', is_active: true, created_at: '' };
const fail = (config: InternalAxiosRequestConfig, status: number) => new AxiosError<{ detail: unknown }>('HTTP error', 'ERR_BAD_REQUEST', config, {}, { config, status, statusText: 'Error', headers: new AxiosHeaders(), data: { detail: 'Denied' } });
const ok = (config: InternalAxiosRequestConfig) => ({ config, status: 200, statusText: 'OK', headers: new AxiosHeaders(), data: { ok: true } });
beforeEach(() => { invalidateAuthSession(); localStorage.clear(); native.mockReturnValue(false); });
afterEach(() => invalidateAuthSession());

it('clears an APK session after an authenticated API 401 even though the renderer is localhost', async () => {
  native.mockReturnValue(true);
  authenticateSession(user, 'test-token', 'https://daocaijing.com');
  apiTransport.defaults.adapter = async config => { expect(config.url).toBe('https://daocaijing.com/api/private'); throw fail(config, 401); };
  await expect(apiGet('/api/private')).rejects.toMatchObject({ status: 401 });
  expect(getAuthSnapshot().account).toBeNull();
  expect(localStorage.getItem('auth_token')).toBeNull();
});

it('does not attach a production token to local fallback APIs or log out on their 401', async () => {
  authenticateSession(user, 'test-token', 'https://daocaijing.com');
  apiTransport.defaults.adapter = async config => {
    expect(config.headers.Authorization).toBeUndefined(); throw fail(config, 401);
  };
  await expect(apiGet('/api/private')).rejects.toMatchObject({ status: 401 });
  expect(getAuthSnapshot().token).toBe('test-token');
});

it('preserves network error identity so a write reaches a second API source', async () => {
  const attempted: string[] = [];
  apiTransport.defaults.adapter = async config => {
    attempted.push(config.url || '');
    if (attempted.length === 1) throw new AxiosError('Network Error', 'ERR_NETWORK', config);
    return ok(config);
  };
  await expect(apiPost('/api/save', { value: 1 })).resolves.toEqual({ ok: true });
  expect(attempted).toHaveLength(2); expect(new Set(attempted).size).toBe(2);
});

it.each(['ECONNABORTED', 'ETIMEDOUT'])('does not replay a write whose result is uncertain after %s', async code => {
  const adapter = jest.fn(async (config: InternalAxiosRequestConfig) => { throw new AxiosError('timeout', code, config); });
  apiTransport.defaults.adapter = adapter;
  await expect(apiPost('/api/save', {})).rejects.toMatchObject({ code });
  expect(adapter).toHaveBeenCalledTimes(1);
});

it('preserves Axios identity and readable validation messages for HTTP failures', async () => {
  apiTransport.defaults.adapter = async config => {
    const error = fail(config, 422); error.response!.data = { detail: [{ msg: 'field is required' }] }; throw error;
  };
  try { await apiPost('/api/save', {}); throw new Error('Expected rejection'); }
  catch (error) { expect(axios.isAxiosError(error)).toBe(true); expect(error).toMatchObject({ status: 422, message: 'field is required' }); }
});

it('cancels an in-flight response when the account changes', async () => {
  native.mockReturnValue(true); authenticateSession(user, 'test-token-a', 'https://daocaijing.com');
  let finish!: () => void;
  let signal: InternalAxiosRequestConfig['signal'];
  apiTransport.defaults.adapter = config => { signal = config.signal; return new Promise(resolve => { finish = () => resolve(ok(config)); }); };
  const request = apiGet('/api/private');
  await Promise.resolve(); await Promise.resolve();
  authenticateSession({ ...user, id: 'B' }, 'test-token-b', 'https://daocaijing.com');
  expect(signal?.aborted).toBe(true); finish();
  await expect(request).rejects.toMatchObject({ code: 'ERR_CANCELED' });
  expect(getAuthSnapshot().account?.id).toBe('B');
});


it('keeps stream requests on the authenticated API origin after an anonymous source wins a public read', async () => {
  authenticateSession(user, 'test-token-a', 'http://127.0.0.1:8300');
  apiTransport.defaults.adapter = async config => {
    if (config.url?.startsWith('http://127.0.0.1:8300')) throw new AxiosError('Network Error', 'ERR_NETWORK', config);
    return ok(config);
  };
  await apiGet('/api/public');
  expect(getActiveApiBaseUrl()).toBe('http://127.0.0.1:8300');
});
