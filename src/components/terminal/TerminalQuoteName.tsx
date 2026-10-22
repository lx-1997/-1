import React from 'react';

export interface TerminalQuoteNameProps {
  symbol: string;
  name: string;
  selected: boolean;
  onSelect: (symbol: string) => void;
}

/** A native keyboard target inside the pointer-selectable quote row. */
export function TerminalQuoteName({ symbol, name, selected, onSelect }: TerminalQuoteNameProps) {
  return (
    <button type="button" className="c-name-t bbt-qrow-select" aria-pressed={selected}
      aria-label={`查看 ${name}（${symbol}）行情`} title={`${symbol} ${name}`}
      onClick={event => { event.stopPropagation(); onSelect(symbol); }}>
      <b>{symbol}</b> {name}
    </button>
  );
}
