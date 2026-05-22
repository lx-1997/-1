import axios, { AxiosRequestConfig, Method } from 'axios';

const configuredApiBaseUrl = process.env.REACT_APP_API_BASE_URL?.replace(/\/$/, '');
let preferredApiBaseUrl: string | null = null;

function uniqueValues(values: string[]): string[] {
  return values.filter((value, index, array) => value && array.indexOf(value) === index);
}

export function getApiBaseUrls(): string[] {
  const candidates: string[] = [];

  if (configuredApiBaseUrl) {
    candidates.push(configuredApiBaseUrl);
  }

  if (typeof window !== 'undefined') {
    const { protocol, hostname } = window.location;
    if (hostname && hostname !== 'localhost' && hostname !== '127.0.0.1') {
      candidates.push(`${protocol}//${hostname}:8300`);
    }
  }

  candidates.push('http://127.0.0.1:8300');
  candidates.push('http://localhost:8300');

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
