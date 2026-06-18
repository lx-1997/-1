import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { Button, Card, Empty, Spin, Tooltip } from 'antd';
import { ReloadOutlined, SafetyCertificateOutlined } from '@ant-design/icons';
import { getCorpusStats, CorpusStats } from '../services/infrastructureService';
import { credibilityMeta } from '../utils/credibility';
import './CorpusOverview.css';

const FRESHNESS_FIELDS: Array<{ key: keyof CorpusStats['freshness']; label: string }> = [
  { key: 'last_24h', label: '近 24 小时' },
  { key: 'last_7d', label: '近 7 天' },
  { key: 'last_30d', label: '近 30 天' },
  { key: 'older', label: '更早' },
];

/** 可信度徽标（复用全站统一分级 utils/credibility）。 */
const CredBadge: React.FC<{ value: number }> = ({ value }) => {
  const meta = credibilityMeta(value);
  if (!meta) {
    return null;
  }
  return (
    <span className={`dfx-corpus-cred ${meta.tier}`} title={`平均可信度 ${Math.round(value * 100)}%`}>
      {meta.label} {Math.round(value * 100)}%
    </span>
  );
};

/**
 * 证据库「语料质量与覆盖」概览 —— 让用户看清驱动 Agent 引用的语料健康度：
 * 可信度分布（与 grounding 同档）、标的覆盖、来源质量、新鲜度。全量后端聚合。
 */
const CorpusOverview: React.FC<{
  watchlistSymbols?: string[];
  /** 点击标的覆盖里的某标的 → 把下方资料表筛选到该标的（让概览可交互）。 */
  onSelectSymbol?: (symbol: string) => void;
}> = ({ watchlistSymbols = [], onSelectSymbol }) => {
  const [stats, setStats] = useState<CorpusStats | null>(null);
  const [loading, setLoading] = useState(false);
  const watchSet = useMemo(
    () => new Set(watchlistSymbols.map(s => s.toUpperCase())),
    [watchlistSymbols]
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      setStats(await getCorpusStats());
    } catch {
      setStats(null);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const bands = stats?.credibility_bands;
  const bandTotal = bands ? bands.high + bands.mid + bands.low : 0;
  const maxSymbol = stats?.symbol_coverage.reduce((m, s) => Math.max(m, s.count), 0) || 1;
  const maxSource = stats?.source_quality.reduce((m, s) => Math.max(m, s.count), 0) || 1;
  const freshTotal = stats
    ? FRESHNESS_FIELDS.reduce((sum, f) => sum + (stats.freshness[f.key] || 0), 0)
    : 0;

  return (
    <Card
      className="dfx-corpus"
      title={<span><SafetyCertificateOutlined style={{ marginRight: 8, color: 'var(--accent)' }} />语料质量与覆盖</span>}
      extra={<Button size="small" icon={<ReloadOutlined />} loading={loading} onClick={load}>刷新</Button>}
    >
      {!stats ? (
        loading ? (
          <div style={{ textAlign: 'center', padding: 24 }}><Spin tip="统计语料中…" /></div>
        ) : (
          <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无语料统计" />
        )
      ) : stats.total === 0 ? (
        <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="证据库还没有资料——接入数据源或上传研报后这里会展示语料健康度" />
      ) : (
        <div className="dfx-corpus-body">
          {/* 头部：总量 + 平均可信度 */}
          <div className="dfx-corpus-headline">
            <div className="dfx-corpus-stat">
              <div className="dfx-corpus-stat-num">{stats.total}</div>
              <div className="dfx-corpus-stat-label">证据条目</div>
            </div>
            <div className="dfx-corpus-stat">
              <div className="dfx-corpus-stat-num">{Math.round(stats.avg_credibility * 100)}%</div>
              <div className="dfx-corpus-stat-label">平均可信度</div>
            </div>
            <div className="dfx-corpus-hint">
              这些证据驱动分析师 / 深研 / 圆桌的可溯源引用——按可信度排序优先被采用。
            </div>
          </div>

          {/* 可信度分布（与 grounding 一致的 高可信/中/存疑 分档） */}
          {bands && bandTotal > 0 && (
            <div className="dfx-corpus-section">
              <div className="dfx-corpus-section-title">可信度分布</div>
              <div className="dfx-corpus-bandbar">
                {bands.high > 0 && (
                  <Tooltip title={`高可信（≥75%）：${bands.high} 条`}>
                    <div className="dfx-corpus-band high" style={{ flexGrow: bands.high }}>{bands.high}</div>
                  </Tooltip>
                )}
                {bands.mid > 0 && (
                  <Tooltip title={`中（50–75%）：${bands.mid} 条`}>
                    <div className="dfx-corpus-band mid" style={{ flexGrow: bands.mid }}>{bands.mid}</div>
                  </Tooltip>
                )}
                {bands.low > 0 && (
                  <Tooltip title={`存疑（<50%）：${bands.low} 条`}>
                    <div className="dfx-corpus-band low" style={{ flexGrow: bands.low }}>{bands.low}</div>
                  </Tooltip>
                )}
              </div>
              <div className="dfx-corpus-legend">
                <span><i className="dot high" />高可信 {bands.high}</span>
                <span><i className="dot mid" />中 {bands.mid}</span>
                <span><i className="dot low" />存疑 {bands.low}</span>
              </div>
            </div>
          )}

          {/* 标的覆盖（高亮自选股） */}
          {stats.symbol_coverage.length > 0 && (
            <div className="dfx-corpus-section">
              <div className="dfx-corpus-section-title">标的覆盖 Top {stats.symbol_coverage.length}</div>
              <div className="dfx-corpus-rows">
                {stats.symbol_coverage.map(s => (
                  <div
                    key={s.symbol}
                    className={`dfx-corpus-row${onSelectSymbol ? ' clickable' : ''}`}
                    onClick={onSelectSymbol ? () => onSelectSymbol(s.symbol) : undefined}
                    role={onSelectSymbol ? 'button' : undefined}
                    title={onSelectSymbol ? `查看 ${s.symbol} 的 ${s.count} 条证据` : undefined}
                  >
                    <span className={`dfx-corpus-row-key${watchSet.has(s.symbol.toUpperCase()) ? ' watch' : ''}`}>
                      {s.symbol}{watchSet.has(s.symbol.toUpperCase()) ? ' ★' : ''}
                    </span>
                    <div className="dfx-corpus-row-bar">
                      <div className="dfx-corpus-row-fill" style={{ width: `${Math.round((s.count / maxSymbol) * 100)}%` }} />
                    </div>
                    <span className="dfx-corpus-row-count">{s.count}</span>
                    <CredBadge value={s.avg_credibility} />
                  </div>
                ))}
              </div>
              {watchSet.size > 0 && (
                <div className="dfx-corpus-foot">★ = 你的自选股；覆盖少的标的，Agent 引用时证据更薄。</div>
              )}
            </div>
          )}

          {/* 来源质量 */}
          {stats.source_quality.length > 0 && (
            <div className="dfx-corpus-section">
              <div className="dfx-corpus-section-title">来源质量</div>
              <div className="dfx-corpus-rows">
                {stats.source_quality.map(s => (
                  <div key={s.source_name} className="dfx-corpus-row">
                    <span className="dfx-corpus-row-key wide" title={s.source_name}>{s.source_name}</span>
                    <div className="dfx-corpus-row-bar">
                      <div className="dfx-corpus-row-fill" style={{ width: `${Math.round((s.count / maxSource) * 100)}%` }} />
                    </div>
                    <span className="dfx-corpus-row-count">{s.count}</span>
                    <CredBadge value={s.avg_credibility} />
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* 新鲜度 */}
          {freshTotal > 0 && (
            <div className="dfx-corpus-section">
              <div className="dfx-corpus-section-title">新鲜度</div>
              <div className="dfx-corpus-fresh">
                {FRESHNESS_FIELDS.map(f => (
                  <div key={f.key} className="dfx-corpus-fresh-cell">
                    <div className="dfx-corpus-fresh-num">{stats.freshness[f.key]}</div>
                    <div className="dfx-corpus-fresh-label">{f.label}</div>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      )}
    </Card>
  );
};

export default CorpusOverview;
