import React from 'react';

const SECTIONS: Record<string, [string, string, string]> = {
  stocks: ['WATCHLIST', '我的股票', '从自选出发，跟踪价格变化，核对每一条研究线索。'],
  market: ['MARKET / RESEARCH', '市场快讯', '跟踪市场正在发生什么，从消息回到事实。'],
  articles: ['INSIGHTS', '深度文章', '留一点时间，读懂事件背后的逻辑。'],
  notes: ['INSTITUTIONAL NOTES', '机构纪要', '把机构观点放回上下文，与其他来源交叉验证。'],
  reports: ['RESEARCH LIBRARY', '投行研报', '从原始研报出发，提炼观点，追溯依据。'],
};

interface Props {
  section: string;
  anonymous: boolean;
  connection: string;
  onSearch: () => void;
  onReview: () => void;
  onResearch: () => void;
}

/** Navigation only: no synthetic prices, signals or connectivity claims. */
export default function WorkspaceIntro({ section, anonymous, connection, onSearch, onReview, onResearch }: Props) {
  const page = SECTIONS[section];
  if (!page) return null;
  if (anonymous && section === 'market') return (
    <section className="dc-intro" aria-labelledby="dc-intro-title">
      <div className="dc-intro-copy">
        <span className="dc-eyebrow">DAOCAIJING <span>/</span> 投研工作台</span>
        <h1 id="dc-intro-title">市场变化，<br />从这里看清<span>。</span></h1>
        <p>跟踪重要消息，聚焦你关注的公司。<br />查看原始依据，再形成自己的判断。</p>
        <div className="dc-intro-actions">
          <button type="button" className="dc-action-primary" onClick={onSearch}>开始研究一只股票 <span aria-hidden="true">↗</span></button>
          <button type="button" className="dc-action-secondary" onClick={onReview}>查看每日复盘 <span aria-hidden="true">→</span></button>
        </div>
        <div className="dc-intro-caption">A 股 · 港股 · 美股 <span>研究，从一个好问题开始</span></div>
      </div>
      <div className="dc-research-path">
        <div className="dc-path-heading"><span>今天，从哪里开始</span><span>RESEARCH FLOW</span></div>
        <button type="button" onClick={onReview}><span className="dc-path-number">01</span><span><strong>先看市场全貌</strong><small>快讯与每日复盘，建立市场背景</small></span><span aria-hidden="true">↗</span></button>
        <button type="button" onClick={onSearch}><span className="dc-path-number">02</span><span><strong>聚焦一家公司</strong><small>行情、事件与研报，放在一起看</small></span><span aria-hidden="true">↗</span></button>
        <button type="button" onClick={onResearch}><span className="dc-path-number">03</span><span><strong>带着证据问 AI</strong><small>梳理逻辑，也追问风险与分歧</small></span><span aria-hidden="true">↗</span></button>
        <div className="dc-path-foot"><span>来源可追溯 · 观点可核对</span><span>独立判断</span></div>
      </div>
    </section>
  );
  return (
    <header className="dc-page-heading">
      <div><span className="dc-eyebrow">{page[0]}</span><h1>{page[1]}</h1><p>{page[2]}</p></div>
      <span className="dc-page-status">{connection}</span>
    </header>
  );
}
