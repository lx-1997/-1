import React, {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { createPortal } from 'react-dom';
import {
  CheckCircleIcon,
  CloseIcon,
  ErrorCircleIcon,
  InfoCircleIcon,
  WarningTriangleIcon,
} from './icons';
import { cx } from './types';

export type ToastType = 'success' | 'error' | 'warning' | 'info';

export interface ToastOptions {
  type?: ToastType;
  title: string;
  description?: string;
  /** 展示时长（毫秒）。<=0 表示常驻，须手动关闭。默认 4000。 */
  duration?: number;
}

export interface ToastApi {
  show: (options: ToastOptions) => number;
  success: (title: string, description?: string, duration?: number) => number;
  error: (title: string, description?: string, duration?: number) => number;
  warning: (title: string, description?: string, duration?: number) => number;
  info: (title: string, description?: string, duration?: number) => number;
}

interface ToastItem {
  id: number;
  type: ToastType;
  title: string;
  description?: string;
  duration: number;
  leaving: boolean;
}

const MAX_VISIBLE = 4;
const LEAVE_ANIMATION_MS = 180;
const DEFAULT_DURATION = 4000;

const ToastContext = createContext<ToastApi | null>(null);

/** 读取全局通知 API。必须在 <ToastProvider> 内使用。 */
export function useToast(): ToastApi {
  const api = useContext(ToastContext);
  if (!api) {
    throw new Error('useToast 必须在 <ToastProvider> 内使用');
  }
  return api;
}

const TYPE_ICON: Record<ToastType, React.ComponentType<{ size?: number }>> = {
  success: CheckCircleIcon,
  error: ErrorCircleIcon,
  warning: WarningTriangleIcon,
  info: InfoCircleIcon,
};

/**
 * 全局通知。
 *
 * 行为契约：
 * - 视口 aria-live="polite"；错误类条目以 role="alert" 立即播报；
 * - 最多同时可见 4 条，超出时丢弃最早一条；
 * - 指针悬停暂停自动关闭计时，移出后按剩余时长恢复；
 * - duration <= 0 表示常驻，只能手动关闭。
 */
export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const seqRef = useRef(0);
  // id -> { timeoutId, remaining, startedAt }
  const timersRef = useRef(new Map<number, { timeoutId: number; remaining: number; startedAt: number }>());
  const leaveTimersRef = useRef(new Map<number, number>());

  const clearTimer = useCallback((id: number) => {
    const entry = timersRef.current.get(id);
    if (entry) {
      window.clearTimeout(entry.timeoutId);
      timersRef.current.delete(id);
    }
  }, []);

  const remove = useCallback((id: number) => {
    clearTimer(id);
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, [clearTimer]);

  const dismiss = useCallback((id: number) => {
    clearTimer(id);
    setItems((prev) =>
      prev.map((item) => (item.id === id ? { ...item, leaving: true } : item)),
    );
    const leaveTimer = window.setTimeout(() => {
      leaveTimersRef.current.delete(id);
      remove(id);
    }, LEAVE_ANIMATION_MS);
    leaveTimersRef.current.set(id, leaveTimer);
  }, [clearTimer, remove]);

  const scheduleDismiss = useCallback((id: number, duration: number) => {
    if (duration <= 0) return;
    const startedAt = Date.now();
    const timeoutId = window.setTimeout(() => dismiss(id), duration);
    timersRef.current.set(id, { timeoutId, remaining: duration, startedAt });
  }, [dismiss]);

  const show = useCallback((options: ToastOptions): number => {
    const id = ++seqRef.current;
    const type = options.type ?? 'info';
    const duration = options.duration ?? DEFAULT_DURATION;

    setItems((prev) => {
      const active = prev.filter((item) => !item.leaving);
      // 超出容量：丢弃最早的一条（含其计时器）。
      if (active.length >= MAX_VISIBLE) {
        const oldest = active[0];
        clearTimer(oldest.id);
        const leaveTimer = leaveTimersRef.current.get(oldest.id);
        if (leaveTimer) window.clearTimeout(leaveTimer);
        return [
          ...prev.filter((item) => item.id !== oldest.id),
          { id, type, title: options.title, description: options.description, duration, leaving: false },
        ];
      }
      return [
        ...prev,
        { id, type, title: options.title, description: options.description, duration, leaving: false },
      ];
    });

    scheduleDismiss(id, duration);
    return id;
  }, [clearTimer, scheduleDismiss]);

  const api = useMemo<ToastApi>(() => ({
    show,
    success: (title, description, duration) => show({ type: 'success', title, description, duration }),
    error: (title, description, duration) => show({ type: 'error', title, description, duration }),
    warning: (title, description, duration) => show({ type: 'warning', title, description, duration }),
    info: (title, description, duration) => show({ type: 'info', title, description, duration }),
  }), [show]);

  // 卸载时清理全部计时器，避免悬挂回调。
  useEffect(() => {
    const timers = timersRef.current;
    const leaveTimers = leaveTimersRef.current;
    return () => {
      timers.forEach((entry) => window.clearTimeout(entry.timeoutId));
      timers.clear();
      leaveTimers.forEach((timeoutId) => window.clearTimeout(timeoutId));
      leaveTimers.clear();
    };
  }, []);

  const pause = (id: number) => {
    const entry = timersRef.current.get(id);
    if (!entry) return;
    window.clearTimeout(entry.timeoutId);
    entry.remaining = Math.max(0, entry.remaining - (Date.now() - entry.startedAt));
  };

  const resume = (id: number) => {
    const entry = timersRef.current.get(id);
    if (!entry || entry.remaining <= 0) return;
    const startedAt = Date.now();
    entry.startedAt = startedAt;
    entry.timeoutId = window.setTimeout(() => dismiss(id), entry.remaining);
  };

  if (typeof document === 'undefined') {
    return <>{children}</>;
  }

  return (
    <ToastContext.Provider value={api}>
      {children}
      {createPortal(
        <div className="dui-toast-viewport" aria-live="polite" aria-atomic="false">
          {items.map((item) => {
            const Icon = TYPE_ICON[item.type];
            return (
              <div
                key={item.id}
                role={item.type === 'error' ? 'alert' : 'status'}
                className={cx('dui-toast', `dui-toast--${item.type}`, item.leaving && 'dui-toast--leaving')}
                onMouseEnter={() => pause(item.id)}
                onMouseLeave={() => resume(item.id)}
                data-dui-toast-type={item.type}
              >
                <span className="dui-toast__icon" aria-hidden="true">
                  <Icon size={16} />
                </span>
                <div className="dui-toast__content">
                  <div className="dui-toast__title">{item.title}</div>
                  {item.description && <div className="dui-toast__desc">{item.description}</div>}
                </div>
                <button
                  type="button"
                  className="dui-toast__close"
                  aria-label="关闭通知"
                  onClick={() => dismiss(item.id)}
                >
                  <CloseIcon size={12} />
                </button>
              </div>
            );
          })}
        </div>,
        document.body,
      )}
    </ToastContext.Provider>
  );
}
