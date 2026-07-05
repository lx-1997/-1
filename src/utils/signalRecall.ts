import { useCallback, useEffect, useState } from 'react';
import { apiGet, apiPost } from '../services/apiClient';
import type { RealtimeMessageRecord, RealtimeMessageSeverity } from '../services/eventService';

/**
 * 信号召回（retention）：把站内信号流接到「外部/本地通知」，
 * 盯的标的出信号时把用户叫回来。本模块负责偏好持久化 + 判定 + 浏览器本地通知，
 * 不依赖出网；邮件/Web Push 等外部通道作为后端订阅契约单独实现。
 */

export type RecallScope = 'watchlist' | 'all';
export type RecallPermission = 'default' | 'granted' | 'denied' | 'unsupported';

export interface RecallPrefs {
  /** 浏览器桌面通知总开关（需用户授予通知权限）。 */
  browserEnabled: boolean;
  /** 哪些级别触发召回。 */
  severities: RealtimeMessageSeverity[];
  /** 范围：仅自选股 / 全部信号。 */
  scope: RecallScope;
  /** 仅在标签页不可见（用户离开）时弹通知，避免在用时打扰。 */
  onlyWhenHidden: boolean;
}

export const RECALL_STORAGE_KEY = 'dfx_signal_recall_v1';
/** 偏好变更时广播，应用级监听据此决定是否连接 SSE。 */
export const RECALL_PREFS_EVENT = 'dfx-recall-prefs-changed';

export const DEFAULT_RECALL_PREFS: RecallPrefs = {
  browserEnabled: false,
  // 含 success：scope 默认 watchlist(只看自选)，自选股的利好快讯正是"盯盘"要的通知；
  // info(中性资讯)刻意不进默认——热门股一天十几条会把通知权限惹到 denied(永久失去该通道)。
  severities: ['success', 'warning', 'critical'],
  scope: 'watchlist',
  onlyWhenHidden: true,
};

/** 终端自选股的本地镜像（FinancialTerminal 写 bbt.watchlist）——订阅 Web Push 时兜底带上，
 * 避免"空 symbols + scope=watchlist"= 什么都匹配不到的死订阅。 */
export function readWatchlistFallback(): string[] {
  try {
    const raw = window.localStorage.getItem('bbt.watchlist');
    const arr = raw ? JSON.parse(raw) : [];
    return Array.isArray(arr) ? arr.filter((s): s is string => typeof s === 'string') : [];
  } catch {
    return [];
  }
}

export function loadRecallPrefs(): RecallPrefs {
  try {
    const raw = window.localStorage.getItem(RECALL_STORAGE_KEY);
    const parsed = raw ? JSON.parse(raw) : null;
    if (parsed && typeof parsed === 'object') {
      return {
        ...DEFAULT_RECALL_PREFS,
        ...parsed,
        severities:
          Array.isArray(parsed.severities) && parsed.severities.length
            ? parsed.severities
            : DEFAULT_RECALL_PREFS.severities,
      };
    }
  } catch {
    /* 存储不可用时用默认值 */
  }
  return { ...DEFAULT_RECALL_PREFS };
}

export function saveRecallPrefs(prefs: RecallPrefs): void {
  try {
    window.localStorage.setItem(RECALL_STORAGE_KEY, JSON.stringify(prefs));
    window.dispatchEvent(new Event(RECALL_PREFS_EVENT));
  } catch {
    /* 忽略 */
  }
}

export function getNotificationPermission(): RecallPermission {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'unsupported';
  }
  return Notification.permission as RecallPermission;
}

export async function requestBrowserPermission(): Promise<RecallPermission> {
  if (typeof window === 'undefined' || !('Notification' in window)) {
    return 'unsupported';
  }
  if (Notification.permission !== 'default') {
    return Notification.permission as RecallPermission;
  }
  try {
    return (await Notification.requestPermission()) as RecallPermission;
  } catch {
    return Notification.permission as RecallPermission;
  }
}

// 已通知过的消息 id：同一条信号即使被多处（应用级 + 信号流页）看到，也只弹一次。
const notifiedIds = new Set<string>();

function fireNotification(title: string, body: string, tag: string, url?: string | null): boolean {
  if (getNotificationPermission() !== 'granted' || typeof window === 'undefined' || !('Notification' in window)) {
    return false;
  }
  try {
    const notification = new Notification(title, { body, tag });
    notification.onclick = () => {
      window.focus();
      if (url) {
        window.open(url, '_blank', 'noopener');
      }
      notification.close();
    };
    return true;
  } catch {
    return false;
  }
}

/** 纯判定：这条信号在当前偏好下是否应该召回（不含「仅后台」与去重，便于单测）。 */
export function shouldRecall(message: RealtimeMessageRecord, watchlist: string[], prefs: RecallPrefs): boolean {
  if (!prefs.browserEnabled) {
    return false;
  }
  if (!prefs.severities.includes(message.severity)) {
    return false;
  }
  if (prefs.scope === 'watchlist') {
    if (!message.symbol || !watchlist.includes(message.symbol)) {
      return false;
    }
  }
  return true;
}

/**
 * 应用级调用：对一条新到的信号，按当前偏好决定是否把用户「叫回来」。
 * 返回是否真的弹了通知。读偏好用最新的 localStorage，避免多实例状态不同步。
 */
export function evaluateAndNotify(message: RealtimeMessageRecord, watchlist: string[]): boolean {
  const prefs = loadRecallPrefs();
  if (prefs.onlyWhenHidden && typeof document !== 'undefined' && !document.hidden) {
    return false;
  }
  if (!shouldRecall(message, watchlist, prefs)) {
    return false;
  }
  if (notifiedIds.has(message.id)) {
    return false;
  }
  notifiedIds.add(message.id);
  const title = `${message.symbol ? `${message.symbol} · ` : ''}${message.title}`;
  return fireNotification(title, message.content || '点击查看信号详情', message.id, message.url);
}

export function fireTestRecall(): boolean {
  return fireNotification('DeepFocus 召回测试', '盯的信号触发时，会这样把你叫回来。', 'recall-test');
}

/** 仅供测试：清空去重缓存。 */
export function __resetRecallDedup(): void {
  notifiedIds.clear();
}

// ===== Web Push（离线召回）：关掉页面/浏览器也能把用户叫回来 =====
// 页面内 Notification 只在标签页存活时有效；真正的离线召回靠 Service Worker + Web Push。
// 这是免费用户唯一不依赖付费/微信的离线触达通道（两日留存 15% 的头号修复）。
const PUSH_SW_URL = '/push-sw.js';
const WEBPUSH_KEY_PATH = '/api/realtime/recall/webpush-key';
const WEBPUSH_SUB_PATH = '/api/realtime/recall/subscriptions';

export function webPushSupported(): boolean {
  return (
    typeof window !== 'undefined' &&
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  );
}

/** VAPID 公钥（base64url）→ pushManager.subscribe 需要的 Uint8Array。 */
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const raw = window.atob(base64);
  const out = new Uint8Array(raw.length);
  for (let i = 0; i < raw.length; i += 1) {
    out[i] = raw.charCodeAt(i);
  }
  return out;
}

let _webPushConfig: { enabled: boolean; public_key: string } | null = null;

/** 读后端 VAPID 公钥配置（未配置→enabled=false，前端据此隐藏离线推送 UI）。结果缓存。 */
export async function getWebPushConfig(): Promise<{ enabled: boolean; public_key: string }> {
  if (_webPushConfig) {
    return _webPushConfig;
  }
  try {
    const cfg = await apiGet<{ enabled: boolean; public_key: string }>(WEBPUSH_KEY_PATH);
    _webPushConfig = cfg && typeof cfg === 'object' ? cfg : { enabled: false, public_key: '' };
  } catch {
    _webPushConfig = { enabled: false, public_key: '' };
  }
  return _webPushConfig;
}

/**
 * 订阅 Web Push：注册 push-sw → 取 VAPID 公钥 → pushManager.subscribe → 上报后端订阅契约。
 * 全程 best-effort：不支持/未配置/未授权/失败都安静返回 false，绝不抛错打断调用方。
 */
export async function subscribeWebPush(opts?: {
  symbols?: string[];
  severities?: RealtimeMessageSeverity[];
  scope?: RecallScope;
}): Promise<boolean> {
  if (!webPushSupported() || getNotificationPermission() !== 'granted') {
    return false;
  }
  try {
    const cfg = await getWebPushConfig();
    if (!cfg.enabled || !cfg.public_key) {
      return false;
    }
    const reg = await navigator.serviceWorker.register(PUSH_SW_URL);
    await navigator.serviceWorker.ready;
    let sub = await reg.pushManager.getSubscription();
    if (!sub) {
      sub = await reg.pushManager.subscribe({
        userVisibleOnly: true,
        applicationServerKey: urlBase64ToUint8Array(cfg.public_key),
      });
    }
    const prefs = loadRecallPrefs();
    const symbols = opts?.symbols && opts.symbols.length ? opts.symbols : readWatchlistFallback();
    await apiPost(WEBPUSH_SUB_PATH, {
      channel: 'webpush',
      address: JSON.stringify(sub),
      symbols,  // 不传/传空 → 用终端自选股兜底，杜绝 scope=watchlist 下的"死订阅"
      severities: opts?.severities ?? prefs.severities,
      scope: opts?.scope ?? prefs.scope,
      label: 'browser-webpush',
    });
    return true;
  } catch {
    return false;
  }
}

/**
 * 一键用账号邮箱订阅召回（服务端从登录态直取邮箱+自选，前端零输入）。
 * 国内 Chrome 的 Web Push 端点在 Google FCM、境内服务器送不到——邮箱是「开启盯盘」时
 * 顺手能拿到的最可靠离线兜底通道。best-effort：未登录/无邮箱/失败都安静返回 false。
 */
export async function subscribeEmailRecall(): Promise<boolean> {
  try {
    const out = await apiPost<{ ok: boolean }>('/api/realtime/recall/subscribe-email', {});
    return !!(out && out.ok);
  } catch {
    return false;
  }
}

/** 当前浏览器是否已建立离线 Web Push 订阅。 */
export async function getWebPushSubscribed(): Promise<boolean> {
  if (!webPushSupported()) {
    return false;
  }
  try {
    const regs = await navigator.serviceWorker.getRegistrations();
    for (const reg of regs) {
      const sw = reg.active || reg.waiting || reg.installing;
      if (sw && sw.scriptURL && sw.scriptURL.indexOf('push-sw.js') !== -1) {
        return !!(await reg.pushManager.getSubscription());
      }
    }
    return false;
  } catch {
    return false;
  }
}

/** 召回设置面板用的小 hook：偏好状态 + 持久化 + 通知权限。 */
export function useRecallPrefs() {
  const [prefs, setPrefsState] = useState<RecallPrefs>(loadRecallPrefs);
  const [permission, setPermission] = useState<RecallPermission>(getNotificationPermission);

  useEffect(() => {
    saveRecallPrefs(prefs);
  }, [prefs]);

  const setPrefs = useCallback((next: Partial<RecallPrefs>) => {
    setPrefsState(prev => ({ ...prev, ...next }));
  }, []);

  const enableBrowser = useCallback(async () => {
    const perm = await requestBrowserPermission();
    setPermission(perm);
    setPrefsState(prev => ({ ...prev, browserEnabled: perm === 'granted' }));
    if (perm === 'granted') {
      // 同时订阅 Web Push → 关掉页面也能离线召回（best-effort，未配置/失败仅退回页面内通知）
      void subscribeWebPush();
    }
  }, []);

  const disableBrowser = useCallback(() => {
    setPrefsState(prev => ({ ...prev, browserEnabled: false }));
  }, []);

  return { prefs, setPrefs, permission, enableBrowser, disableBrowser };
}
