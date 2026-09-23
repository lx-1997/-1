import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { fetchMarketRiskRadar, type MarketRiskCompany } from '../services/marketRiskRadarService';
import { getPremarketOpportunities, type PremarketThemeSignal } from '../services/marketService';
import './TerminalInvestorCompass.css';

interface TerminalQuote {
  change_percent?: number | null;
}

interface TerminalInvestorCompassProps {
  symbols: string[];
  names: Record<string, string>;
  quotes: Record<string, TerminalQuote>;
  loggedIn: boolean;
  onSelectSymbol: (symbol: string) => void;
  onAddSymbol: (symbol: string, name: string) => void;
  onRequireLogin: () => void;
  onOpenMarket: () => void;
}

type HealthTone = 'green' | 'yellow' | 'red';

const normalizeSymbol = (value: string) => value.trim().toUpperCase().replace(/\.(SH|SZ|HK)$/, '');

const metricChipStyle: React.CSSProperties = {
  display: 'inline-flex',
  alignItems: 'center',
  gap: 4,
  padding: '2px 8px',
  borderRadius: 999,
  border: '1px solid var(--border-soft)',
  background: 'var(--surface-muted)',
  color: 'var(--text-soft)',
  fontSize: 10,
  fontWeight: 600,
  lineHeight: 1.4,
  whiteSpace: 'nowrap',
};

function healthFor(symbol: string, quote: TerminalQuote | undefined, companies: MarketRiskCompany[]) {
  const company = companies.find(item => normalizeSymbol(item.symbol) === normalizeSymbol(symbol));
  if (company?.risk_level === 'red' || company?.risk_level === 'orange') {
    return { tone: 'red' as const, label: '建议复核', reason: company.drivers[0] || '风险雷达发现多项需要优先核对的变化。' };
  }
  if (company?.risk_level === 'yellow') {
    return { tone: 'yellow' as const, label: '需要留意', reason: company.drivers[0] || '出现了值得继续验证的变化。' };
  }
  const change = Number(quote?.change_percent || 0);
  if (change <= -5) {
    return { tone: 'yellow' as const, label: '需要留意', reason: `今日波动 ${change.toFixed(1)}%，先确认是市场波动还是公司本身的变化。` };
  }
  return { tone: 'green' as const, label: '正常', reason: company ? '暂未发现高优先级风险信号。' : '目前没有触发需要优先复核的条件。' };
}

const TerminalInvestorCompass: React.FC<TerminalInvestorCompassProps> = ({
  symbols, names, quotes, loggedIn, onSelectSymbol, onAddSymbol, onRequireLogin, onOpenMarket,
}) => {
  const [companies, setCompanies] = useState<MarketRiskCompany[]>([]);
  const [themes, setThemes] = useState<PremarketThemeSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const symbolsKey = symbols.join(',');

  const load = useCallback(async () => {
    setLoading(true);
    const [radar, opportunities] = await Promise.allSettled([
      fetchMarketRiskRadar(),
      loggedIn ? getPremarketOpportunities() : Promise.resolve(null),
    ]);
    if (radar.status === 'fulfilled') setCompanies(radar.value.markets.flatMap(market => market.companies));
    if (opportunities.status === 'fulfilled' && opportunities.value) {
      setThemes(opportunities.value.themes.filter(theme => ['重点机会', '观察机会'].includes(theme.stance)).slice(0, 2));
    }
    setLoading(false);
  }, [loggedIn]);

  useEffect(() => { void load(); }, [load, symbolsKey]);

  const healthItems = useMemo(() => symbols.slice(0, 4).map(symbol => ({
    symbol,
    name: names[symbol] || symbol,
    ...healthFor(symbol, quotes[symbol], companies),
  })), [symbols, names, quotes, companies]);
  const watchCount = symbols.length;
  const riskCount = healthItems.filter(item => item.tone !== 'green').length;
  const opportunityCount = loggedIn ? themes.length : 0;

  return (
    <section className="bbt-investor-compass" aria-label="我的股票和市场机会">
      <div className="bbt-investor-compass-head">
        <div>
          <span>今天先看什么</span>
          <h2>先看和你有关的变化，再找下一笔机会</h2>
          <p>把自选、风险和市场信号收束成一个面板，不必先穿过一堆工具。</p>
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginTop: 8 }}>
            <span style={metricChipStyle}>自选 {watchCount}</span>
            <span style={metricChipStyle}>留意 {riskCount}</span>
            <span style={metricChipStyle}>{loggedIn ? `机会 ${opportunityCount}` : '登录后看机会'}</span>
          </div>
        </div>
        <button type="button" onClick={() => void load()} disabled={loading}>{loading ? '更新中…' : '更新'}</button>
      </div>
      <div className="bbt-investor-compass-grid">
        <div className="bbt-investor-compass-panel">
          <div className="bbt-investor-compass-title"><b>我的股票</b><small>只提示值得你花时间核对的变化</small></div>
          {healthItems.length ? <div className="bbt-investor-health-list">{healthItems.map(item => (
            <button className="bbt-investor-health" type="button" key={item.symbol} onClick={() => onSelectSymbol(item.symbol)}>
              <i className={item.tone} /><span><strong>{item.name}</strong><small>{item.label} · {item.reason}</small></span><em>→</em>
            </button>
          ))}</div> : <button className="bbt-investor-empty" type="button" onClick={onRequireLogin}>登录后添加关注股票，系统会自动帮你筛选变化 →</button>}
        </div>
        <div className="bbt-investor-compass-panel">
          <div className="bbt-investor-compass-title"><b>发现机会</b><small>先观察，再决定要不要深入研究</small></div>
          {loggedIn && themes.length ? themes.map(theme => {
            const asset = theme.mapped_assets[0];
            return <div className="bbt-investor-theme" key={theme.theme_key}>
              <strong>✦ {theme.theme_name}<small>{theme.stance}</small></strong>
              <p>{theme.evidence[0] || theme.suggested_action}</p>
              {asset && <button type="button" onClick={() => onAddSymbol(asset.symbol, asset.name)}>＋ 关注 {asset.name}</button>}
            </div>;
          }) : loggedIn ? <button className="bbt-investor-empty" type="button" onClick={onOpenMarket}>{loading ? '正在汇总跨市场信号…' : '今天先看市场复盘，机会会在信号充分后出现 →'}</button> : <button className="bbt-investor-empty" type="button" onClick={onRequireLogin}>登录后，按你的自选和市场信号发现新机会 →</button>}
        </div>
      </div>
      <div className="bbt-investor-compass-foot">红绿灯不预测涨跌，只把行情、风险与市场信号翻译成“今天要不要管”。</div>
    </section>
  );
};

export default TerminalInvestorCompass;
