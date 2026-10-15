import React from 'react';

interface StockItem { symbol: string; name: string; }
interface Props {
  activeSymbol: string;
  activeName: string;
  stocks: StockItem[];
  onSelect: (symbol: string) => void;
  onBack: () => void;
}

export default function StockWorkspaceNav({ activeSymbol, activeName, stocks, onSelect, onBack }: Props) {
  const index = stocks.findIndex(stock => stock.symbol === activeSymbol);
  const options = index < 0 ? [{ symbol: activeSymbol, name: activeName }, ...stocks] : stocks;
  return <nav className="bbt-stock-navigation" aria-label="个股研究导航">
    <button type="button" className="bbt-stock-back" onClick={onBack}><span aria-hidden="true">←</span> 返回自选</button>
    <span className="bbt-stock-nav-label">个股研究</span>
    <div className="bbt-stock-switcher">
      <button type="button" aria-label="研究上一只自选股" title="上一只自选股" disabled={index <= 0} onClick={() => { if (index > 0) onSelect(stocks[index - 1].symbol); }}><span aria-hidden="true">‹</span></button>
      <select aria-label="切换研究股票" value={activeSymbol} onChange={event => { if (event.target.value !== activeSymbol) onSelect(event.target.value); }} onKeyDown={event => { if (['ArrowDown', 'ArrowUp', 'ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) event.stopPropagation(); }}>
        {options.map(stock => <option value={stock.symbol} key={stock.symbol}>{stock.name || stock.symbol} · {stock.symbol}</option>)}
      </select>
      <button type="button" aria-label="研究下一只自选股" title="下一只自选股" disabled={index < 0 || index >= stocks.length - 1} onClick={() => { if (index >= 0 && index < stocks.length - 1) onSelect(stocks[index + 1].symbol); }}><span aria-hidden="true">›</span></button>
    </div>
  </nav>;
}
