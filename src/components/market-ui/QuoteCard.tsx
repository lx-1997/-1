import React, { useId } from 'react';
import { AsyncButton } from './AsyncButton';
import { QuoteStatus } from './QuoteStatus';
import { QuoteValue } from './QuoteValue';
import { QuoteItem } from './types';

export interface QuoteCardProps {
  item: QuoteItem;
  selected?: boolean;
  watched?: boolean;
  pending?: boolean;
  colorConvention?: 'cn' | 'international';
  onSelect?: (item: QuoteItem) => void;
  onToggleWatch?: (item: QuoteItem) => void | Promise<void>;
  onActionError?: (error: unknown) => void;
}

const marketNames = { CN: 'A 股', HK: '港股', US: '美股', OTHER: '其他市场' };

export function QuoteCard({ item, selected = false, watched = false, pending = false,
  colorConvention = 'cn', onSelect, onToggleWatch, onActionError }: QuoteCardProps) {
  const titleId = useId();
  // A failed refresh must never label retained quotes as live.
  const unavailable = item.quality === 'unavailable';
  return (
    <article className={`dc-market-card${selected ? ' dc-market-card--selected' : ''}`} aria-labelledby={titleId}>
      <div className="dc-market-card__header">
        <div className="dc-market-card__identity">
          <h3 id={titleId}>
            {onSelect ? <button type="button" className="dc-market-symbol-button"
              onClick={() => onSelect(item)} aria-current={selected ? 'true' : undefined}
              aria-label={`查看 ${item.name}（${item.symbol}）行情`}>{item.name}</button> : item.name}
          </h3>
          <p className="dc-market-card__symbol">{item.symbol}{item.market && ` · ${marketNames[item.market]}`}</p>
        </div>
        {onToggleWatch && <AsyncButton variant="quiet" busy={pending}
          onAction={() => onToggleWatch(item)} onError={onActionError}
          aria-pressed={watched} aria-label={`监控 ${item.name}（${item.symbol}）`}>
          <span aria-hidden="true">{watched ? '★' : '☆'}</span> {watched ? '监控中' : '监控'}
        </AsyncButton>}
      </div>
      <dl className="dc-market-card__quote">
        <div><dt>最新价</dt><dd><QuoteValue value={unavailable ? null : item.price} currency={item.currency} /></dd></div>
        <div><dt>涨跌幅</dt><dd><QuoteValue value={unavailable ? null : item.changePercent} kind="percent" colorConvention={colorConvention} /></dd></div>
      </dl>
      <QuoteStatus quality={item.quality} source={item.source} updatedAt={item.updatedAt} delayNote={item.delayNote} />
    </article>
  );
}
