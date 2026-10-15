import React from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import StockWorkspaceNav from '../../components/StockWorkspaceNav';

const stocks = [{ symbol: '600519', name: '贵州茅台' }, { symbol: '002594', name: '比亚迪' }, { symbol: 'TSLA', name: '特斯拉' }];
const props = () => ({ activeSymbol: '002594', activeName: '比亚迪', stocks, onSelect: jest.fn(), onBack: jest.fn() });

it('changes the researched stock without editing the watchlist', () => {
  const handlers = props();
  render(<StockWorkspaceNav {...handlers} />);
  fireEvent.change(screen.getByRole('combobox', { name: '切换研究股票' }), { target: { value: 'TSLA' } });
  expect(handlers.onSelect).toHaveBeenCalledWith('TSLA');
  expect(stocks).toHaveLength(3);
});

it('moves to the previous and next watchlist entries and respects the ends', () => {
  const handlers = props();
  const view = render(<StockWorkspaceNav {...handlers} />);
  fireEvent.click(screen.getByRole('button', { name: '研究上一只自选股' }));
  fireEvent.click(screen.getByRole('button', { name: '研究下一只自选股' }));
  expect(handlers.onSelect.mock.calls).toEqual([['600519'], ['TSLA']]);
  view.rerender(<StockWorkspaceNav {...handlers} activeSymbol="600519" />);
  expect(screen.getByRole('button', { name: '研究上一只自选股' })).toBeDisabled();
  view.rerender(<StockWorkspaceNav {...handlers} activeSymbol="TSLA" />);
  expect(screen.getByRole('button', { name: '研究下一只自选股' })).toBeDisabled();
});

it('keeps an external stock selected without treating it as the first watchlist entry', () => {
  const handlers = props();
  render(<StockWorkspaceNav {...handlers} activeSymbol="AAPL" activeName="苹果" />);
  expect(screen.getByRole('combobox')).toHaveValue('AAPL');
  expect(screen.getByRole('option', { name: '苹果 · AAPL' })).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '研究上一只自选股' })).toBeDisabled();
  expect(screen.getByRole('button', { name: '研究下一只自选股' })).toBeDisabled();
});

it('provides an explicit back action and prevents select keys reaching global stock shortcuts', () => {
  const handlers = props();
  const globalKey = jest.fn();
  window.addEventListener('keydown', globalKey);
  try {
    render(<StockWorkspaceNav {...handlers} />);
    fireEvent.click(screen.getByRole('button', { name: /返回自选/ }));
    expect(handlers.onBack).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'ArrowDown' });
    expect(globalKey).not.toHaveBeenCalled();
    fireEvent.keyDown(screen.getByRole('combobox'), { key: 'Escape' });
    expect(globalKey).toHaveBeenCalledTimes(1);
  } finally { window.removeEventListener('keydown', globalKey); }
});
