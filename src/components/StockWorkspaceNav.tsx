import React from 'react';

export default function StockWorkspaceNav({ onBack, onAsk }: { onBack: () => void; onAsk?: () => void }) {
  return <nav className="bbt-stock-navigation" aria-label="个股研究导航">
    <button type="button" className="bbt-stock-back" onClick={onBack}><span aria-hidden="true">←</span> 返回自选</button>
    {onAsk && <button type="button" className="dc-stock-ask" onClick={onAsk}>带着这只股票问 AI <span aria-hidden="true">↗</span></button>}
  </nav>;
}
