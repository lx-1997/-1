import axios, { AxiosRequestConfig, Method } from 'axios';

const configuredApiBaseUrl = process.env.REACT_APP_API_BASE_URL?.replace(/\/$/, '');
let preferredApiBaseUrl: string | null = null;

// 前端专属请求标识：网页端 API 调用都带它，nginx 校验，挡裸 curl/脚本扒接口
export const DF_WEB_TOKEN = ['dfw', '2vQ9', 'k7Rm'].join('_');  // 拼接，避免整串明文

axios.interceptors.request.use(config => {
  const token = localStorage.getItem('auth_token');
  if (token && config.headers) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
}, error => Promise.reject(error));

axios.interceptors.response.use(
  response => response,
  error => {
    if (axios.isAxiosError(error)) {
      if (error.response?.status === 401) {
        const reqUrl = error.config?.url || '';
        // 只有「当前站点自己」的 401 才允许动全局登录态。并行回退源（如开发机上的
        // http://127.0.0.1:8300 本地后端）不认生产 token 会回 401——那与当前会话无关，
        // 绝不能因此清 token/跳转（否则生产页面会被本机开发后端“误踢”下线）。
        const sameOrigin = !/^https?:\/\//i.test(reqUrl)
          || (typeof window !== 'undefined' && reqUrl.startsWith(window.location.origin + '/'));
        if (!sameOrigin) {
          return Promise.reject(new Error('登录已过期，请重新登录'));
        }
        // 登录/注册接口的 401 = 凭证错误：交回调用方在表单内提示，绝不跳转/清 token。
        const isAuthAttempt = /\/api\/auth\/(login|register)/.test(reqUrl);
        const detail: string = (error.response?.data as any)?.detail || '';
        // 单设备登录被挤下线：清 token + 广播事件（让页面优雅提示并切登录态），不强制跳转 /login。
        if (/挤下线|其他设备登录/.test(detail)) {
          localStorage.removeItem('auth_token');
          try { window.dispatchEvent(new CustomEvent('df:auth-kicked', { detail })); } catch { /* */ }
          return Promise.reject(new Error(detail));
        }
        // /api/auth/me 的 401 = 启动校验存量 token 失效（如自然过期）：清掉即可，调用方自会落到未登录态；
        // 整页跳 /login 反而造成一次无谓刷新（终端在任意路径都能渲染登录入口）。
        if (/\/api\/auth\/me/.test(reqUrl)) {
          localStorage.removeItem('auth_token');
          return Promise.reject(new Error('登录已过期，请重新登录'));
        }
        if (!isAuthAttempt) {
          // 后台/可选请求（离线召回补订阅、自选同步等）对未登录或令牌失效回 401 时，绝不能整页跳 /login：
          // 终端在任意路径都内联渲染登录入口，而 window.location.href='/login' 的整页 reload 会让刚 401 的
          // 那个后台请求随新页面重新发起 → 再 401 → 再 reload → 无限刷新死循环（线上实测每秒数次、500+ 请求）。
          const hadToken = !!localStorage.getItem('auth_token');
          localStorage.removeItem('auth_token');
          if (hadToken) {
            // 确有令牌却被拒 = 登录确实过期：软清登录态 + 广播事件（页面据此切未登录态并提示），不刷新。
            try { window.dispatchEvent(new CustomEvent('df:auth-kicked', { detail: '登录已过期，请重新登录' })); } catch { /* */ }
          }
          // 匿名（无 token）命中受保护端点的 401 属预期：静默拒绝即可，不提示、不跳转、不刷新。
          return Promise.reject(new Error('登录已过期，请重新登录'));
        }
        // 落到下方统一抽取后端 detail（如「用户名或密码错误」）。
      }
      const status = error.response?.status;
      const detail = (error.response?.data as any)?.detail;
      const message = detail
        || error.response?.data?.error
        || error.response?.data?.message
        || `请求失败 (${status || '网络错误'})`;
      // ⭐保留 status/detail 到 Error 上：否则非 401 错误被拍平成纯 new Error(message)，调用方 e.response.status 全失效——
      // AI 解读的 402(非会员→升级弹窗) / 403(匿名→登录注册弹窗) 一直没触发，只能掉到 setAiError 显示一行死提示。
      const err = new Error(message) as Error & { status?: number; detail?: unknown; response?: unknown };
      err.status = status;
      err.detail = detail;
      err.response = error.response;  // 保留原始 response → 全站 e.response.status / e.response.data.detail 的判断继续可用
      return Promise.reject(err);
    }
    return Promise.reject(error);
  }
);

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
  return axios.isAxiosError(error) && !error.response;
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
  const response = await axios.request<T>({
    ...config,
    method,
    url: `${apiBaseUrl}${path}`,
    data,
    timeout: config.timeout ?? 20000,
    headers: { ...(config.headers || {}), 'X-DF-Web': DF_WEB_TOKEN },  // 前端专属标识，挡裸 curl 扒接口
    signal: signal || config.signal
  });
  return response.data;
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

    apiBaseUrls.forEach((apiBaseUrl, index) => {
      requestFromApiBase<T>(apiBaseUrl, 'GET', path, undefined, config, controllers[index]?.signal)
        .then(response => {
          if (settled) {
            return;
          }

          settled = true;
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

export function apiPatch<T>(path: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('PATCH', path, data, config);
}

export function apiDelete<T = void>(path: string, config?: AxiosRequestConfig): Promise<T> {
  return requestWithFallback<T>('DELETE', path, undefined, config);
}
