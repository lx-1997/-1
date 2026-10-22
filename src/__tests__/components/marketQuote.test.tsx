import React from 'react';
import { render, screen } from '@testing-library/react';
import { QuoteStatus } from '../../components/market-ui/QuoteStatus';
import { QuoteValue } from '../../components/market-ui/QuoteValue';
import { formatQuotePercent, formatQuotePrice, formatQuoteTime } from '../../components/market-ui/formatQuote';

describe('QuoteValue', () => {
  it.each([null, undefined, Number.NaN, Infinity, -Infinity])('renders %s as unavailable instead of zero', value => {
    const { container } = render(<QuoteValue value={value} />);
    expect(screen.getByText('暂无价格')).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"]')).toHaveTextContent('—');
  });

  it('keeps zero valid, preserves quote precision and accepts a provider currency label', () => {
    expect(formatQuotePrice(0, 'CNY')).toBe('CNY 0.00');
    expect(formatQuotePrice(1.2345, 'USD')).toBe('USD 1.2345');
    expect(formatQuotePrice(1.2, 'points/unit')).toBe('POINTS/UNIT 1.20');
    render(<QuoteValue value={0} currency="HKD" />);
    expect(screen.getByText('价格 HKD 0.00')).toBeInTheDocument();
  });

  it('formats percentage points with signed text and a direction arrow', () => {
    const { container } = render(<QuoteValue value={1.25} kind="percent" />);
    expect(formatQuotePercent(1.25)).toBe('+1.25%');
    expect(screen.getByText('上涨 1.25%')).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"]')).toHaveTextContent('↑ +1.25%');
    expect(container.querySelector('.dc-market-value')).toHaveClass('dc-market-value--cn', 'dc-market-value--up');
  });

  it('retains accessible direction when the international color convention is chosen', () => {
    const { container } = render(<QuoteValue value={-1.25} kind="percent" colorConvention="international" />);
    expect(screen.getByText('下跌 1.25%')).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"]')).toHaveTextContent('↓ −1.25%');
    expect(container.querySelector('.dc-market-value')).toHaveClass('dc-market-value--international', 'dc-market-value--down');
  });

  it('treats negative zero as flat and reports missing percent distinctly', () => {
    const { container, rerender } = render(<QuoteValue value={-0} kind="percent" />);
    expect(screen.getByText('持平 0.00%')).toBeInTheDocument();
    expect(container.querySelector('.dc-market-value')).toHaveClass('dc-market-value--flat');
    expect(formatQuotePercent(-0)).toBe('0.00%');
    rerender(<QuoteValue value={null} kind="percent" />);
    expect(screen.getByText('暂无涨跌幅')).toBeInTheDocument();
    expect(container.querySelector('[aria-hidden="true"]')).toHaveTextContent('—');
  });
});

describe('QuoteStatus', () => {
  it.each([
    ['live', '实时行情'], ['delayed', '延迟行情'], ['stale', '缓存行情'],
    ['mock', '演示数据 · 非真实行情'], ['unavailable', '暂无行情'],
  ] as const)('labels %s with explicit text independent of icon and color', (quality, text) => {
    const { container } = render(<QuoteStatus quality={quality} />);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(container.querySelector(`.dc-market-status__badge--${quality}`)).toBeInTheDocument();
  });

  it('shows source and the precise Beijing timestamp without promoting delayed data to live', () => {
    render(<QuoteStatus quality="delayed" source="行情供应商" updatedAt="2026-10-12T02:03:04Z" delayNote="延迟 15 分钟" />);
    expect(screen.getByText('延迟行情')).toBeInTheDocument();
    expect(screen.getByText('来源：行情供应商')).toBeInTheDocument();
    const time = screen.getByText('更新：2026/10/12 10:03:04 北京时间');
    expect(time).toHaveAttribute('datetime', '2026-10-12T02:03:04.000Z');
    expect(screen.getByText('延迟 15 分钟')).toBeInTheDocument();
    expect(screen.queryByText('实时行情')).not.toBeInTheDocument();
  });

  it.each(['garbage', '2026-10-12T10:00:00', '2026-02-30T01:00:00Z', '2026-10-12T25:00:00Z'])('handles invalid or ambiguous time %s', updatedAt => {
    render(<QuoteStatus quality="stale" updatedAt={updatedAt} />);
    expect(screen.getByText('时间待核验')).toBeInTheDocument();
    expect(formatQuoteTime(updatedAt)).toBeNull();
  });

  it('keeps compact metadata accessible and does not present an invented update time', () => {
    const { container } = render(<QuoteStatus quality="mock" source="示例" compact delayNote="用于组件演示" />);
    expect(screen.getByText('演示数据 · 非真实行情')).toBeInTheDocument();
    expect(screen.getByText('来源：示例').parentElement).toHaveClass('dc-market-sr-only');
    expect(screen.getByText('更新时间未提供')).toBeInTheDocument();
    expect(container.querySelector('time')).toBeNull();
  });
});
