import axios, { AxiosRequestConfig, Method } from 'axios';

const configuredApiBaseUrl = process.env.REACT_APP_API_BASE_URL?.replace(/\/$/, '');

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

function isRetryableConnectionError(error: unknown): boolean {
  return axios.isAxiosError(error) && !error.response;
}

async function requestWithFallback<T>(
  method: Method,
  path: string,
  data?: unknown,
  config: AxiosRequestConfig = {}
): Promise<T> {
  let lastError: unknown;

  for (const apiBaseUrl of getApiBaseUrls()) {
    try {
      const response = await axios.request<T>({
        ...config,
        method,
        url: `${apiBaseUrl}${path}`,
        data,
        timeout: config.timeout ?? 20000
      });
      return response.data;
    } catch (error) {
      lastError = error;
      if (!isRetryableConnectionError(error)) {
        throw error;
      }
    }
  }

  throw lastError;
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
