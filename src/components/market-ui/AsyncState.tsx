import React from 'react';
import { AsyncButton } from './AsyncButton';

export type MarketLoadStatus = 'loading' | 'ready' | 'error';

export interface AsyncStateProps {
  status: MarketLoadStatus;
  hasData?: boolean;
  error?: string;
  onRetry?: () => void | Promise<void>;
  children: React.ReactNode;
}

/** Initial loading replaces content; background work keeps the last known data visible. */
export function AsyncState({ status, hasData = false, error, onRetry, children }: AsyncStateProps) {
  if (status === 'loading' && !hasData) {
    return (
      <div className="dc-market-loading">
        <span role="status" className="dc-market-sr-only">正在加载行情</span>
        <div className="dc-market-grid" aria-hidden="true">
          {[0, 1, 2].map(key => (
            <div className="dc-market-skeleton-card" key={key}>
              <div className="dc-market-skeleton" />
              <div className="dc-market-skeleton dc-market-skeleton--price" />
              <div className="dc-market-skeleton dc-market-skeleton--short" />
            </div>
          ))}
        </div>
      </div>
    );
  }
  return (
    <>
      {status === 'error' && (
        <div className="dc-market-error">
          <p role="alert">{error || '行情加载失败，请稍后重试'}{hasData && '。当前显示上次获取的数据。'}</p>
          {onRetry && <AsyncButton onAction={onRetry}>重试</AsyncButton>}
        </div>
      )}
      {status === 'loading' && hasData && <p role="status" className="dc-market-update">正在更新行情，当前显示上次获取的数据</p>}
      {(status !== 'error' || hasData) && children}
    </>
  );
}
