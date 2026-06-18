import { useCallback, useEffect, useState } from 'react';
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
  severities: ['warning', 'critical'],
  scope: 'watchlist',
  onlyWhenHidden: true,
};

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
  }, []);

  const disableBrowser = useCallback(() => {
    setPrefsState(prev => ({ ...prev, browserEnabled: false }));
  }, []);

  return { prefs, setPrefs, permission, enableBrowser, disableBrowser };
}
