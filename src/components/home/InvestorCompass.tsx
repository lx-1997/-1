import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { PlusOutlined, ReloadOutlined, SafetyCertificateOutlined, ThunderboltOutlined } from '@ant-design/icons';
import type { Stock, ViewType } from '../../types';
import type { MarketSymbolCandidate, PremarketThemeSignal } from '../../services/marketService';
import { getPremarketOpportunities } from '../../services/marketService';
import type { MarketRiskCompany, MarketRiskRadarResponse } from '../../services/marketRiskRadarService';
import { fetchMarketRiskRadar } from '../../services/marketRiskRadarService';
import { getBriefing, type BriefingResponse } from '../../services/researchService';
import './InvestorCompass.css';

type HealthTone = 'green' | 'yellow' | 'red';

interface InvestorCompassProps {
  stocks: Stock[];
  onStockSelect: (stock: Stock, view?: ViewType) => void;
  onViewChange: (view: ViewType) => void;
  onAddStock: (candidate: MarketSymbolCandidate) => Promise<void> | void;
}

interface HealthItem {
  stock: Stock;
  tone: HealthTone;
  label: string;
  reason: string;
}

function normalizeSymbol(value: string): string {
  return value.trim().toUpperCase().replace(/\.(SH|SZ|HK)$/, '');
}

function statusLabel(tone: HealthTone): string {
  return tone === 'green' ? '正常' : tone === 'yellow' ? '需要留意' : '建议复核';
}

function getHealth(stock: Stock, companies: MarketRiskCompany[]): HealthItem {
  const matched = companies.find(company => normalizeSymbol(company.symbol) === normalizeSymbol(stock.symbol));
  if (matched?.risk_level === 'red' || matched?.risk_level === 'orange') {
    return {
      stock,
      tone: 'red',
      label: '建议复核',
      reason: matched.drivers[0] || '风险雷达发现多项需要优先核对的信号。',
    };
  }
  if (matched?.risk_level === 'yellow') {
    return {
      stock,
      tone: 'yellow',
      label: '需要留意',
      reason: matched.drivers[0] || '风险雷达提示存在需要继续验证的变化。',
    };
  }
  if (stock.changePercent <= -5) {
    return {
      stock,
      tone: 'yellow',
      label: '需要留意',
      reason: `今日波动 ${stock.changePercent.toFixed(1)}%，先确认是市场波动还是公司基本面变化。`,
    };
  }
  return {
    stock,
    tone: 'green',
    label: '正常',
    reason: matched ? '暂未发现风险雷达中的高优先级问题。' : '目前没有触发需要优先复核的条件。',
  };
}

function toCandidate(theme: PremarketThemeSignal, index: number): MarketSymbolCandidate | null {
  const asset = theme.mapped_assets[index];
  if (!asset) return null;
  return {
    symbol: asset.symbol,
    code: asset.symbol,
    name: asset.name,
    market: asset.market,
    exchange: '',
    security_type: 'stock',
    provider: 'premarket-opportunity',
    provider_name: '市场机会雷达',
  };
}

const InvestorCompass: React.FC<InvestorCompassProps> = ({ stocks, onStockSelect, onViewChange, onAddStock }) => {
  const [briefing, setBriefing] = useState<BriefingResponse | null>(null);
  const [radar, setRadar] = useState<MarketRiskRadarResponse | null>(null);
  const [themes, setThemes] = useState<PremarketThemeSignal[]>([]);
  const [riskThemes, setRiskThemes] = useState<PremarketThemeSignal[]>([]);
  const [loading, setLoading] = useState(true);
  const [updatedAt, setUpdatedAt] = useState<string | null>(null);

  const symbolsKey = useMemo(() => stocks.map(stock => stock.symbol).join(','), [stocks]);

  const load = useCallback(async () => {
    setLoading(true);
    const symbols = symbolsKey.split(',').filter(Boolean);
    const [briefingResult, radarResult, opportunitiesResult] = await Promise.allSettled([
      getBriefing(symbols),
      fetchMarketRiskRadar(),
      getPremarketOpportunities(),
    ]);
    if (briefingResult.status === 'fulfilled') setBriefing(briefingResult.value);
    if (radarResult.status === 'fulfilled') setRadar(radarResult.value);
    if (opportunitiesResult.status === 'fulfilled') {
      setThemes(opportunitiesResult.value.themes.filter(theme => ['重点机会', '观察机会'].includes(theme.stance)).slice(0, 2));
      setRiskThemes(opportunitiesResult.value.risk_watchlist.slice(0, 2));
      setUpdatedAt(opportunitiesResult.value.generated_at);
    }
    setLoading(false);
  }, [symbolsKey]);

  useEffect(() => { void load(); }, [load]);

  const allCompanies = useMemo(() => radar?.markets.flatMap(market => market.companies) || [], [radar]);
  const health = useMemo(() => stocks.slice(0, 5).map(stock => getHealth(stock, allCompanies)), [stocks, allCompanies]);
  const counts = useMemo(() => health.reduce<Record<HealthTone, number>>((acc, item) => {
    acc[item.tone] += 1;
    return acc;
  }, { green: 0, yellow: 0, red: 0 }), [health]);

  const addCandidate = (theme: PremarketThemeSignal, index: number) => {
    const candidate = toCandidate(theme, index);
    if (candidate) void onAddStock(candidate);
  };

  return (
    <section className="dfx-compass" aria-label="我的股票红绿灯与市场机会">
      <div className="dfx-compass-head">
        <div>
          <span className="dfx-home-card-eyebrow">MY INVESTMENT COMPASS</span>
          <h2>今天我的股票怎么样？</h2>
          <p>先看与你有关的变化，再发现观察池之外的新机会和风险。</p>
        </div>
        <button type="button" className="dfx-compass-refresh" onClick={() => void load()} disabled={loading}>
          <ReloadOutlined spin={loading} /> 更新
        </button>
      </div>

      {briefing && (
        <div className="dfx-compass-market-note">
          <SafetyCertificateOutlined />
          <span><strong>市场环境：{briefing.macro_verdict}</strong>{briefing.headline || briefing.macro.narrative}</span>
          <button type="button" onClick={() => onViewChange('briefing')}>看晨报 →</button>
        </div>
      )}

      <div className="dfx-compass-grid">
        <div className="dfx-compass-panel">
          <div className="dfx-compass-panel-head">
            <div><h3>我的股票红绿灯</h3><span>不是涨跌预测，只提示值得你花时间核对的变化</span></div>
            {health.length > 0 && <div className="dfx-compass-counts"><b className="green">{counts.green} 正常</b><b className="yellow">{counts.yellow} 留意</b><b className="red">{counts.red} 复核</b></div>}
          </div>
          {health.length ? <div className="dfx-compass-health-list">
            {health.map(item => <button key={item.stock.symbol} type="button" className="dfx-compass-health" onClick={() => onStockSelect(item.stock, 'stock-tear-sheet')}>
              <span className={`dfx-compass-light ${item.tone}`} />
              <span className="dfx-compass-health-title"><strong>{item.stock.name || item.stock.symbol}</strong><small>{item.stock.symbol} · {item.label}</small></span>
              <span className="dfx-compass-health-reason">{item.reason}</span><span className="dfx-compass-arrow">→</span>
            </button>)}
          </div> : <button type="button" className="dfx-compass-empty" onClick={() => onViewChange('stocks')}>添加关注股票后，这里会每天替你筛掉无关信息 →</button>}
        </div>

        <div className="dfx-compass-panel dfx-compass-discovery">
          <div className="dfx-compass-panel-head"><div><h3>市场正在发生什么</h3><span>从全市场中筛选，再给你候选和风险</span></div><button type="button" onClick={() => onViewChange('premarket-opportunity')}>全部雷达 →</button></div>
          {themes.length > 0 || riskThemes.length > 0 ? <div className="dfx-compass-discovery-list">
            {themes.map(theme => <div className="dfx-compass-theme opportunity" key={theme.theme_key}>
              <div><ThunderboltOutlined /><strong>{theme.theme_name}</strong><span>{theme.stance}</span></div>
              <p>{theme.evidence[0] || theme.suggested_action}</p>
              {theme.mapped_assets.slice(0, 1).map((asset, index) => <button type="button" key={asset.symbol} onClick={() => addCandidate(theme, index)}><PlusOutlined /> 关注 {asset.name}</button>)}
            </div>)}
            {riskThemes.map(theme => <div className="dfx-compass-theme risk" key={`risk-${theme.theme_key}`}>
              <div><SafetyCertificateOutlined /><strong>{theme.theme_name}</strong><span>风险留意</span></div>
              <p>{theme.evidence[0] || theme.suggested_action}</p>
            </div>)}
          </div> : <div className="dfx-compass-loading">{loading ? '正在汇总行情、期权、宏观和站内信号…' : '暂时没有足够的市场信号，稍后再试。'}</div>}
        </div>
      </div>
      <div className="dfx-compass-foot">综合行情、宏观、期权、风险雷达和站内证据；机会先加入观察，不构成投资建议。{updatedAt ? ` 更新于 ${new Date(updatedAt).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })}` : ''}</div>
    </section>
  );
};

export default InvestorCompass;
