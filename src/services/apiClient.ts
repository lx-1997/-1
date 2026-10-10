import axios, { AxiosRequestConfig, Method } from 'axios';
import { Capacitor } from '@capacitor/core';
import { getAuthSnapshot, invalidateAuthSession, isAuthRevisionCurrent, registerSessionCleanup } from '../state/authSession';

const configuredApiBaseUrl = process.env.REACT_APP_API_BASE_URL?.replace(/\/$/, '');
let preferredApiBaseUrl: string | null = null;
let authenticatedApiBaseUrl: string | null = null;

// 前端专属请求标识：网页端 API 调用都带它，nginx 校验，挡裸 curl/脚本扒接口
export const DF_WEB_TOKEN = ['dfw', '2vQ9', 'k7Rm'].join('_');  // 拼接，避免整串明文

// An isolated client avoids attaching account tokens to unrelated third-party Axios calls.
export const apiTransport = axios.create();

function requestOrigin(url: string): string | null {
  try { return new URL(url, typeof window !== 'undefined' ? window.location.origin : undefined).origin; } catch { return null; }
}
export function sessionOwnsRequest(url: string): boolean {
  const session = getAuthSnapshot();
  const expected = session.apiOrigin || configuredApiBaseUrl || getApiBaseUrls()[0];
  return !/^https?:\/\//i.test(url) || requestOrigin(url) === requestOrigin(expected || '');
}

apiTransport.interceptors.request.use(config => {
  const { token } = getAuthSnapshot();
  if (token && config.headers && sessionOwnsRequest(config.url || '')) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, error => Promise.reject(error));

apiTransport.interceptors.response.use(response => response, error => {
  if (!axios.isAxiosError(error)) return Promise.reject(error);
  // Keep Axios' error identity/code/cancellation information so fallback can distinguish
  // an unreachable source from an HTTP rejection or an uncertain timed-out write.
  if (!error.response) return Promise.reject(error);
  const reqUrl = error.config?.url || '';
  const isAuthAttempt = /\/api\/auth\/(login|register)/.test(reqUrl);
  const data = error.response.data as { detail?: unknown; error?: string; message?: string } | undefined;
  const detail = data?.detail;
  if (error.response.status === 401 && !isAuthAttempt && sessionOwnsRequest(reqUrl)) {
    const authorization = error.config?.headers?.Authorization;
    const sentToken = typeof authorization === 'string' ? authorization.replace(/^Bearer\s+/i, '') : null;
    // A response from a previous or anonymous session cannot invalidate a newer login.
    if (sentToken && invalidateAuthSession(sentToken)) {
      const reason = typeof detail === 'string' && /挤下线|其他设备登录/.test(detail)
        ? detail : '登录已过期，请重新登录';
      if (!/\/api\/auth\/me/.test(reqUrl)) {
        window.dispatchEvent(new CustomEvent('df:auth-kicked', { detail: reason }));
      }
    }
  }
  const enriched = error as typeof error & { status?: number; detail?: unknown };
  enriched.status = error.response.status;
  enriched.detail = detail;
  enriched.message = formatErrorMessage(error);
  return Promise.reject(enriched);
});

function formatErrorMessage(error: unknown): string {
  if (axios.isAxiosError(error)) {
    const d = error.response?.data?.detail;
    // FastAPI/pydantic 校验错误：detail 是 [{loc,msg,type},...] → 取 msg 拼成可读文案
    if (Array.isArray(d)) {
      const msgs = d.map((x: any) => (x && typeof x === 'object') ? (x.msg || '') : String(x)).filter(Boolean);
      if (msgs.length) return msgs.join('；');
    } else if (d && typeof d === 'object') {
      return (d as any).msg || JSON.stringify(d);
    } else if (typeof d === 'string' && d) {
      return d;
    }
    return error.response?.data?.error
      || error.response?.data?.message
      || `请求失败 (${error.response?.status || '网络错误'})`;
  }
  if (error instanceof Error) return error.message;
  return '未知错误';
}

export { formatErrorMessage };

function uniqueValues(values: string[]): string[] {
  return values.filter((value, index, array) => value && array.indexOf(value) === index);
}

export function getApiBaseUrls(): string[] {
  const candidates: string[] = [];

  if (configuredApiBaseUrl) {
    candidates.push(configuredApiBaseUrl);
  }

  // Capacitor APK 内的页面 hostname 是 localhost，但 API 不在手机本机。
  // 原生网络通道已绕过 WebView CORS，因此只保留构建时写入的正式 API，
  // 不要再竞速 127.0.0.1:8300 / localhost:8300 这两条必然失败的地址。
  if (Capacitor.isNativePlatform()) {
    return uniqueValues(candidates.length ? candidates : ['https://daocaijing.com']);
  }

  let onRealDomain = false;
  if (typeof window !== 'undefined') {
    const { protocol, hostname } = window.location;
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1') {
      // 同源优先：经 nginx /api 反代，适配 IP / 域名 / http / https，避免跨域与混合内容
      onRealDomain = true;
      candidates.push(window.location.origin);
      // 仅 http(裸 IP 直连、未架 nginx)场景保留 :8300 直连回退。
      // https 正式域名下后端只绑 127.0.0.1，:8300 永远连不通——竞速它=每个 GET 白发一路注定失败的请求。
      if (protocol === 'http:') {
        candidates.push(`${protocol}//${hostname}:8300`);
      }
    }
  }

  // 本机回退仅限本地/桌面环境（localhost 或 Electron file://）。
  // 正式域名访问时绝不竞速 127.0.0.1——既没意义，又会把生产 token 发给开发机上恰好在跑的本地后端
  //（其 401 曾误清生产登录态），还多打两路无谓请求。
  if (!onRealDomain) {
    candidates.push('http://127.0.0.1:8300');
    candidates.push('http://localhost:8300');
  }

  return uniqueValues(candidates);
}

function getPrioritizedApiBaseUrls(): string[] {
  const apiBaseUrls = getApiBaseUrls();
  if (!preferredApiBaseUrl || !apiBaseUrls.includes(preferredApiBaseUrl)) {
    return apiBaseUrls;
  }

  return [
    preferredApiBaseUrl,
    ...apiBaseUrls.filter(apiBaseUrl => apiBaseUrl !== preferredApiBaseUrl)
  ];
}

function isRetryableConnectionError(error: unknown): boolean {
  return axios.isAxiosError(error) && !error.response && !axios.isCancel(error)
    && !['ECONNABORTED', 'ETIMEDOUT', 'ERR_CANCELED'].includes(error.code || '');
}

async function requestWithFallback<T>(
  method: Method,
  path: string,
  data?: unknown,
  config: AxiosRequestConfig = {}
): Promise<T> {
  if (method.toUpperCase() === 'GET') {
    return requestReadWithFallback<T>(path, config);
  }

  let lastError: unknown;

  for (const apiBaseUrl of getPrioritizedApiBaseUrls()) {
    try {
      const response = await requestFromApiBase<T>(apiBaseUrl, method, path, data, config);
      preferredApiBaseUrl = apiBaseUrl;
      if (/^\/api\/auth\/(login|register)$/.test(path)) authenticatedApiBaseUrl = apiBaseUrl;
      return response;
    } catch (error) {
      lastError = error;
      if (!isRetryableConnectionError(error)) {
        throw error;
      }
    }
  }

  throw lastError;
}

async function requestFromApiBase<T>(
  apiBaseUrl: string,
  method: Method,
  path: string,
  data: unknown,
  config: AxiosRequestConfig,
  signal?: AbortSignal
): Promise<T> {
  const session = getAuthSnapshot();
  const controller = new AbortController();
  const parentSignal = signal || config.signal;
  const abort = () => controller.abort();
  parentSignal?.addEventListener?.('abort', abort, { once: true });
  if (parentSignal?.aborted) controller.abort();
  const unregister = registerSessionCleanup(abort);
  try {
    const response = await apiTransport.request<T>({
      ...config,
      method,
      url: `${apiBaseUrl}${path}`,
      data,
      timeout: config.timeout ?? 20000,
      headers: { ...(config.headers || {}), 'X-DF-Web': DF_WEB_TOKEN },  // 前端专属标识，挡裸 curl 扒接口
      signal: controller.signal
    });
    if (!isAuthRevisionCurrent(session.revision)) throw new axios.CanceledError('Account changed');
    return response.data;
  } finally {
    unregister();
    parentSignal?.removeEventListener?.('abort', abort);
  }
}

async function requestReadWithFallback<T>(
  path: string,
  config: AxiosRequestConfig = {}
): Promise<T> {
  const apiBaseUrls = getPrioritizedApiBaseUrls();

  if (apiBaseUrls.length === 0) {
    throw new Error('No API base URLs configured');
  }

  if (apiBaseUrls.length === 1) {
    const response = await requestFromApiBase<T>(apiBaseUrls[0], 'GET', path, undefined, config);
    preferredApiBaseUrl = apiBaseUrls[0];
    return response;
  }

  return new Promise<T>((resolve, reject) => {
    let pending = apiBaseUrls.length;
    let settled = false;
    const errors: Array<{ apiBaseUrl: string; error: unknown }> = [];
    const controllers = apiBaseUrls.map(() => (
      typeof AbortController !== 'undefined' ? new AbortController() : null
    ));

    const cancelAll = () => {
      if (settled) return;
      settled = true;
      controllers.forEach(controller => controller?.abort());
      reject(new axios.CanceledError('Request cancelled'));
    };
    config.signal?.addEventListener?.('abort', cancelAll, { once: true });
    const removeAbortListener = () => config.signal?.removeEventListener?.('abort', cancelAll);
    if (config.signal?.aborted) { cancelAll(); removeAbortListener(); return; }
    apiBaseUrls.forEach((apiBaseUrl, index) => {
      requestFromApiBase<T>(apiBaseUrl, 'GET', path, undefined, config, controllers[index]?.signal)
        .then(response => {
          if (settled) {
            return;
          }

          settled = true;
          removeAbortListener();
          preferredApiBaseUrl = apiBaseUrl;
          controllers.forEach((controller, controllerIndex) => {
            if (controllerIndex !== index) {
              controller?.abort();
            }
          });
          resolve(response);
        })
        .catch(error => {
          if (settled) {
            return;
          }

          errors.push({ apiBaseUrl, error });
          pending -= 1;

          if (pending === 0) {
            settled = true;
            removeAbortListener();
            const nonRetryable = errors.find(item => !isRetryableConnectionError(item.error));
            reject(nonRetryable?.error || errors[errors.length - 1]?.error || new Error('API request failed'));
          }
        });
    });
  });
}

export function apiGet<T>(path: string, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('GET', path, undefined, config);
}

export function apiPost<T>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('POST', path, data, config);
}

export function apiPut<T>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('PUT', path, data, config);
}

export function apiPatch<T>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('PATCH', path, data, config);
}

export function apiDelete<T = void>(path: string, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('DELETE', path, undefined, config);
}

export function getAuthenticationApiBaseUrl(): string | null { return authenticatedApiBaseUrl || getActiveApiBaseUrl(); }

export function getActiveApiBaseUrl(): string {
  const origin = getAuthSnapshot().apiOrigin;
  return (origin && getApiBaseUrls().find(base => requestOrigin(base) === requestOrigin(origin)))
    || getPrioritizedApiBaseUrls()[0] || '';
}
