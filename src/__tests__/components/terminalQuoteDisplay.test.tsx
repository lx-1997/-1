import React from 'react';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { TerminalQuoteName } from '../../components/terminal/TerminalQuoteName';
import { toTerminalQuoteDisplay } from '../../components/terminal/terminalQuoteDisplay';
import { QuoteStatus } from '../../components/market-ui/QuoteStatus';

const NOW = Date.parse('2026-10-12T02:03:04Z');
const quote = { price: 12.5, change_percent: null, provider: 'sina', provider_name: '新浪行情',
  fetched_at: '2026-10-12T02:03:04Z', is_realtime: false };

describe('terminal quote provenance adapter', () => {
  it.each([undefined, null, Number.NaN, Infinity, -Infinity])('keeps unavailable price %s unavailable', price => {
    const display = toTerminalQuoteDisplay({ ...quote, price }, { now: NOW });
    expect(display.price).toBeNull();
    expect(display.quality).toBe('unavailable');
  });

  it('preserves valid zero and missing percent separately', () => {
    const display = toTerminalQuoteDisplay({ ...quote, price: 0 }, { now: NOW });
    expect(display.price).toBe(0);
    expect(display.changePercent).toBeNull();
    expect(display.quality).toBe('delayed');
  });

  it('requires an explicit true realtime flag and never promotes recent fetches', () => {
    expect(toTerminalQuoteDisplay(quote, { now: NOW }).quality).toBe('delayed');
    expect(toTerminalQuoteDisplay({ ...quote, is_realtime: undefined }, { now: NOW }).quality).toBe('delayed');
    expect(toTerminalQuoteDisplay({ ...quote, is_realtime: true }, { now: NOW }).quality).toBe('live');
  });

  it.each(['mock', 'demo_quotes', 'sample', '模拟行情'])('prioritizes demo provenance %s over realtime and stale state', provider => {
    const display = toTerminalQuoteDisplay({ ...quote, provider, is_realtime: true }, { failed: true, retained: true, now: NOW });
    expect(display.quality).toBe('mock');
  });

  it.each([{ failed: true }, { retained: true }, { lastSuccessAt: NOW - 90_001 }])('marks retained quotes stale for %j', context => {
    expect(toTerminalQuoteDisplay({ ...quote, is_realtime: true }, { ...context, now: NOW }).quality).toBe('stale');
  });

  it('recognizes an old backend fetch even when the browser just received it', () => {
    expect(toTerminalQuoteDisplay({ ...quote, fetched_at: '2026-10-12T02:00:00Z' }, { lastSuccessAt: NOW, now: NOW }).quality).toBe('stale');
  });

  it('does not call a freshly fetched closed-market quote stale solely due to its market time', () => {
    const display = toTerminalQuoteDisplay({ ...quote, market_time: '2026-10-09T15:00:00+08:00' }, { lastSuccessAt: NOW, now: NOW });
    expect(display.quality).toBe('delayed');
    expect(display.updatedAt).toBe('2026-10-09T07:00:00.000Z');
    expect(display.delayNote).not.toMatch('接口获取时间');
  });

  it('identifies fetch time explicitly and preserves missing or ambiguous timestamps', () => {
    const fetched = toTerminalQuoteDisplay(quote, { now: NOW });
    expect(fetched.updatedAt).toBe(quote.fetched_at);
    expect(fetched.delayNote).toContain('时间为接口获取时间，非交易所报价时间');
    const missing = toTerminalQuoteDisplay({ price: 1 }, { lastSuccessAt: NOW, now: NOW });
    expect(missing.updatedAt).toBeNull();
    expect(missing.source).toBeUndefined();
    const ambiguous = toTerminalQuoteDisplay({ price: 1, market_time: '2026-10-12T10:03:04' }, { now: NOW });
    render(<QuoteStatus {...ambiguous} />);
    expect(screen.getByText('时间待核验')).toBeInTheDocument();
    expect(screen.getByText(/行情时间格式待核验/)).toBeInTheDocument();
  });

  it('shows the real source and fetch-time clarification in the production metadata', () => {
    render(<QuoteStatus {...toTerminalQuoteDisplay(quote, { now: NOW })} />);
    expect(screen.getByText('来源：新浪行情')).toBeInTheDocument();
    expect(screen.getByText('更新：2026/10/12 10:03:04 北京时间')).toHaveAttribute('datetime', '2026-10-12T02:03:04.000Z');
    expect(screen.getByText(/时间为接口获取时间，非交易所报价时间/)).toBeInTheDocument();
  });
});

describe('terminal native quote selection', () => {
  it('selects once for pointer, Enter and Space without invoking the row handler', async () => {
    const user = userEvent.setup();
    const select = jest.fn();
    const rowSelect = jest.fn();
    render(<div onClick={rowSelect}><TerminalQuoteName symbol="0700" name="腾讯控股" selected={false} onSelect={select} /></div>);
    const button = screen.getByRole('button', { name: '查看 腾讯控股（0700）行情' });
    await user.tab();
    expect(button).toHaveFocus();
    await user.keyboard('{Enter}');
    expect(select).toHaveBeenCalledTimes(1);
    await user.keyboard(' ');
    expect(select).toHaveBeenCalledTimes(2);
    await user.click(button);
    expect(select).toHaveBeenCalledTimes(3);
    expect(select).toHaveBeenLastCalledWith('0700');
    expect(rowSelect).not.toHaveBeenCalled();
  });

  it('exposes selection state and uses a non-submit button', () => {
    render(<TerminalQuoteName symbol="600519" name="贵州茅台" selected onSelect={() => {}} />);
    const button = screen.getByRole('button', { pressed: true });
    expect(button).toHaveAttribute('type', 'button');
  });
});
