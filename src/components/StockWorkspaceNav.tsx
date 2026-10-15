import React from 'react';

export default function StockWorkspaceNav({ onBack }: { onBack: () => void }) {
  return <nav className="bbt-stock-navigation" aria-label="个股研究导航">
    <button type="button" className="bbt-stock-back" onClick={onBack}><span aria-hidden="true">←</span> 返回自选</button>
  </nav>;
}
