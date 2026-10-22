import React from 'react';
import { finiteQuoteValue, formatQuotePercent, formatQuotePrice, quoteDirection } from './formatQuote';
import type { QuoteColorConvention } from './types';

export interface QuoteValueProps {
  value: number | null | undefined;
  kind?: 'price' | 'percent';
  currency?: string;
  /** CN: red up / green down. International: green up / red down. */
  colorConvention?: QuoteColorConvention;
  className?: string;
}

const DIRECTION_TEXT = { up: '上涨', down: '下跌', flat: '持平', missing: '暂无涨跌幅' };
const DIRECTION_ICON = { up: '↑', down: '↓', flat: '↔', missing: '' };

/** Text, signs and arrows preserve meaning when color cannot be perceived. */
export function QuoteValue({ value, kind = 'price', currency, colorConvention = 'cn', className }: QuoteValueProps) {
  const number = finiteQuoteValue(value);
  const direction = kind === 'percent' ? quoteDirection(number) : number === null ? 'missing' : 'flat';
  const formatted = kind === 'percent' ? formatQuotePercent(number) : formatQuotePrice(number, currency);
  const accessibleLabel = kind === 'price'
    ? number === null ? '暂无价格' : `价格 ${formatted}`
    : number === null ? DIRECTION_TEXT.missing : `${DIRECTION_TEXT[direction]} ${formatQuotePercent(Math.abs(number)).replace(/^\+/, '')}`;

  return (
    <span
      className={['dc-market-value', `dc-market-value--${kind}`, `dc-market-value--${direction}`,
        `dc-market-value--${colorConvention}`, className].filter(Boolean).join(' ')}
    >
      <span className="dc-market-sr-only">{accessibleLabel}</span>
      <span aria-hidden="true">
        {kind === 'percent' && direction !== 'missing' && (
          <span className="dc-market-value__direction">{DIRECTION_ICON[direction]} </span>
        )}
        {kind === 'price' && number !== null ? (
          <>{currency?.trim() && <span className="dc-market-value__currency">{currency.trim().toUpperCase()} </span>}
            <span className="dc-market-value__amount">{formatQuotePrice(number)}</span></>
        ) : formatted}
      </span>
    </span>
  );
}
