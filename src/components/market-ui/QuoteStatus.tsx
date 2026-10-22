import React from 'react';
import { formatQuoteTime } from './formatQuote';
import type { QuoteQuality } from './types';

export interface QuoteStatusProps {
  quality: QuoteQuality;
  source?: string;
  updatedAt?: string | null;
  delayNote?: string;
  /** Preserve metadata for assistive technology while showing a concise badge. */
  compact?: boolean;
}

const STATUS: Record<QuoteQuality, { text: string; icon: string }> = {
  live: { text: '实时行情', icon: '●' },
  delayed: { text: '延迟行情', icon: '◷' },
  stale: { text: '缓存行情', icon: '⏱' },
  mock: { text: '演示数据 · 非真实行情', icon: '◇' },
  unavailable: { text: '暂无行情', icon: '—' },
};

/** The source remains explicit; a recent timestamp alone never implies realtime. */
export function QuoteStatus({ quality, source, updatedAt, delayNote, compact = false }: QuoteStatusProps) {
  const status = STATUS[quality];
  const time = formatQuoteTime(updatedAt);
  const detailClass = compact ? 'dc-market-sr-only' : 'dc-market-status__details';
  return (
    <span className={`dc-market-status${compact ? ' dc-market-status--compact' : ''}`}>
      <span className={`dc-market-status__badge dc-market-status__badge--${quality}`}>
        <span aria-hidden="true">{status.icon} </span>{status.text}
      </span>
      <span className={detailClass}>
        {source?.trim() && <span className="dc-market-status__source">来源：{source.trim()}</span>}
        {time ? (
          <time className="dc-market-status__time" dateTime={time.iso}>更新：{time.label}</time>
        ) : (
          <span className="dc-market-status__time">{updatedAt ? '时间待核验' : '更新时间未提供'}</span>
        )}
        {delayNote?.trim() && <span className="dc-market-status__note">{delayNote.trim()}</span>}
      </span>
    </span>
  );
}
