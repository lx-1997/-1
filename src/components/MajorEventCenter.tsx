import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Select, Space, Spin, Tag, Typography } from 'antd';
import {
  AlertOutlined,
  AuditOutlined,
  CalendarOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import {
  MajorEventImpact,
  MajorEventRecord,
  MajorEventRiskLevel,
  MajorEventStatus,
  MajorEventType,
  scanMajorEvents
} from '../services/majorEventService';

const { Text } = Typography;

interface MajorEventCenterProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

type RiskFilter = 'all' | MajorEventRiskLevel;
type ImpactFilter = 'all' | MajorEventImpact;
type SortMode = 'time_desc' | 'risk_desc' | 'impact_desc';

const eventTypeMeta: Record<MajorEventType, { label: string; color: string }> = {
  control_change: { label: '控制权', color: 'purple' },
  restructuring: { label: '并购重组', color: 'geekblue' },
  buyback: { label: '股份回购', color: 'green' },
  equity_incentive: { label: '股权激励', color: 'cyan' },
  pledge_freeze: { label: '质押冻结', color: 'volcano' },
  litigation_arbitration: { label: '诉讼仲裁', color: 'orange' },
  regulatory_penalty: { label: '监管处罚', color: 'red' },
  st_delisting: { label: 'ST/退市', color: 'red' },
  abnormal_trading: { label: '交易异动', color: 'gold' },
  dividend: { label: '分红派息', color: 'lime' },
  financing: { label: '再融资', color: 'blue' },
  related_transaction: { label: '关联/担保', color: 'magenta' },
  other: { label: '其他事项', color: 'default' }
};

const statusMeta: Record<MajorEventStatus, { label: string; color: string }> = {
  new: { label: '新披露', color: 'blue' },
  progress: { label: '进展', color: 'purple' },
  completed: { label: '完成/终止', color: 'default' },
  risk: { label: '风险', color: 'red' },
  other: { label: '其他', color: 'cyan' }
};

const impactMeta: Record<MajorEventImpact, { label: string; color: string }> = {
  positive: { label: '偏正面', color: 'green' },
  neutral: { label: '中性', color: 'default' },
  negative: { label: '偏负面', color: 'red' },
  mixed: { label: '待验证', color: 'gold' },
  unknown: { label: '未识别', color: 'default' }
};

const riskMeta: Record<MajorEventRiskLevel, { label: string; color: string }> = {
  high: { label: '高风险', color: 'red' },
  medium: { label: '中风险', color: 'gold' },
  low: { label: '低风险', color: 'green' }
};

const detailQualityLabel: Record<string, string> = {
  full: '明细完整',
  partial: '部分明细',
  title_only: '标题识别'
};

const eventTypeOptions = Object.entries(eventTypeMeta).map(([value, meta]) => ({
  label: meta.label,
  value
}));

const getRecordKey = (record: MajorEventRecord): string => {
  return record.announcement_id || `${record.symbol}-${record.announcement_date}-${record.title}`;
};

const displayValue = (value?: string): string => {
  const trimmed = (value || '').trim();
  return trimmed || '--';
};

const compactDisplayValue = (value?: string, limit = 42): string => {
  const displayed = displayValue(value);
  if (displayed === '--' || displayed.length <= limit) {
    return displayed;
  }
  return `${displayed.slice(0, limit)}...`;
};

const normalizeSearchText = (value: string): string => {
  return value
    .toLowerCase()
    .replace(/[\s._\-—~～,，.。:：;；/\\()[\]【】《》"'“”‘’]+/g, '');
};

const fuzzyMatch = (value: string, keyword: string): boolean => {
  const target = normalizeSearchText(value);
  const query = normalizeSearchText(keyword);
  if (!query || target.includes(query)) {
    return true;
  }

  let queryIndex = 0;
  for (const char of target) {
    if (char === query[queryIndex]) {
      queryIndex += 1;
      if (queryIndex === query.length) {
        return true;
      }
    }
  }
  return false;
};

const announcementTimeScore = (record: MajorEventRecord): number => {
  const parsed = new Date(`${record.announcement_date}T00:00:00`).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
};

const riskScore = (level: MajorEventRiskLevel): number => {
  return { high: 3, medium: 2, low: 1 }[level] || 0;
};

const impactScore = (impact: MajorEventImpact): number => {
  return { negative: 4, mixed: 3, positive: 2, neutral: 1, unknown: 0 }[impact] || 0;
};

const formatDate = (value: string): string => {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleDateString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    weekday: 'short'
  });
};

const formatFullDate = (value: string): string => {
  const parsed = new Date(`${value}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleDateString('zh-CN', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    weekday: 'long'
  });
};

const formatTimestamp = (value?: string): string => {
  if (!value) {
    return '--';
  }

  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }

  return parsed.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  });
};

const MajorEventCenter: React.FC<MajorEventCenterProps> = ({ appState, onStockSelect }) => {
  const [days, setDays] = useState(30);
  const [limit, setLimit] = useState(120);
  const [detailLimit, setDetailLimit] = useState(12);
  const [eventTypes, setEventTypes] = useState<MajorEventType[]>([]);
  const [riskFilter, setRiskFilter] = useState<RiskFilter>('all');
  const [impactFilter, setImpactFilter] = useState<ImpactFilter>('all');
  const [sortMode, setSortMode] = useState<SortMode>('time_desc');
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<Awaited<ReturnType<typeof scanMajorEvents>> | null>(null);
  const [selectedRecordKey, setSelectedRecordKey] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const lastAutoScanSignatureRef = useRef('');

  const stockBySymbol = useMemo(() => {
    return new Map(appState.stocks.map(stock => [stock.symbol.toUpperCase(), stock]));
  }, [appState.stocks]);

  const loadEvents = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await scanMajorEvents({
        market: 'A',
        days,
        event_types: eventTypes,
        limit,
        detail_limit: detailLimit
      });
      setResult(response);
      setSelectedRecordKey(prev => {
        const stillExists = prev && response.records.some(record => getRecordKey(record) === prev);
        return stillExists ? prev : response.records[0] ? getRecordKey(response.records[0]) : null;
      });
    } catch (scanError) {
      console.warn('Major event scan failed:', scanError);
      setError('A 股重大事项扫描服务暂不可用，请稍后重试或检查后端连接。');
    } finally {
      setLoading(false);
    }
  }, [days, detailLimit, eventTypes, limit]);

  useEffect(() => {
    const signature = `${days}:${limit}:${detailLimit}:${eventTypes.slice().sort().join(',')}`;
    if (lastAutoScanSignatureRef.current === signature) {
      return;
    }
    lastAutoScanSignatureRef.current = signature;
    void loadEvents();
  }, [days, detailLimit, eventTypes, limit, loadEvents]);

  const records = result?.records || [];

  const filteredRecords = useMemo(() => {
    const keywords = query.trim().split(/\s+/).filter(Boolean);

    return records.filter(record => {
      const searchFields = [
        record.symbol,
        record.name,
        record.title,
        record.subject,
        record.amount,
        record.share_ratio,
        record.progress,
        record.deadline,
        eventTypeMeta[record.event_type].label,
        statusMeta[record.status].label,
        impactMeta[record.impact].label,
        ...record.tags,
        ...record.key_points,
        ...record.risk_flags,
        ...record.action_items
      ];
      const matchesQuery = keywords.length === 0
        || keywords.every(keyword => searchFields.some(field => fuzzyMatch(field, keyword)));
      const matchesRisk = riskFilter === 'all' || record.risk_level === riskFilter;
      const matchesImpact = impactFilter === 'all' || record.impact === impactFilter;
      return matchesQuery && matchesRisk && matchesImpact;
    });
  }, [impactFilter, query, records, riskFilter]);

  const rankedRecords = useMemo(() => {
    return [...filteredRecords].sort((left, right) => {
      const timeDelta = announcementTimeScore(right) - announcementTimeScore(left);
      if (sortMode === 'risk_desc') {
        const riskDelta = riskScore(right.risk_level) - riskScore(left.risk_level);
        return riskDelta || timeDelta || left.symbol.localeCompare(right.symbol);
      }
      if (sortMode === 'impact_desc') {
        const impactDelta = impactScore(right.impact) - impactScore(left.impact);
        return impactDelta || timeDelta || left.symbol.localeCompare(right.symbol);
      }
      return timeDelta || riskScore(right.risk_level) - riskScore(left.risk_level) || left.symbol.localeCompare(right.symbol);
    });
  }, [filteredRecords, sortMode]);

  useEffect(() => {
    if (rankedRecords.length === 0) {
      setSelectedRecordKey(null);
      return;
    }

    if (!selectedRecordKey || !rankedRecords.some(record => getRecordKey(record) === selectedRecordKey)) {
      setSelectedRecordKey(getRecordKey(rankedRecords[0]));
    }
  }, [rankedRecords, selectedRecordKey]);

  const selectedRecord = rankedRecords.find(record => getRecordKey(record) === selectedRecordKey) || rankedRecords[0];
  const selectedStock = selectedRecord ? stockBySymbol.get(selectedRecord.symbol.toUpperCase()) : undefined;
  const highRiskCount = records.filter(record => record.risk_level === 'high').length;
  const negativeCount = records.filter(record => record.impact === 'negative').length;
  const highPriorityCount = records.filter(record => ['regulatory_penalty', 'st_delisting', 'pledge_freeze', 'litigation_arbitration'].includes(record.event_type)).length;
  const detailRate = result
    ? `${result.detail_success_count}/${result.detail_attempted_count || result.detail_success_count || 0}`
    : '--';

  const openAnnouncement = (url: string) => {
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  };

  return (
    <div className="shareholder-change-shell major-event-shell">
      <div className="shareholder-change-header">
        <div>
          <div className="dashboard-eyebrow">A SHARE EVENT CENTER</div>
          <h2 className="dashboard-title">重大事项预警</h2>
          <div className="dashboard-subtitle">扫描 A 股控制权、重组、回购、处罚、诉讼、ST、质押冻结和交易异动公告。</div>
        </div>
        <Space size={8} wrap>
          <Tag color="red">A 股</Tag>
          {result && <Tag color="cyan">{result.provider}</Tag>}
          <Select
            value={days}
            onChange={setDays}
            style={{ width: 104 }}
            options={[
              { label: '近 7 天', value: 7 },
              { label: '近 30 天', value: 30 },
              { label: '近 90 天', value: 90 },
              { label: '近 180 天', value: 180 }
            ]}
          />
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadEvents}>
            刷新
          </Button>
        </Space>
      </div>

      <div className="shareholder-change-scanbar">
        <Select
          mode="multiple"
          allowClear
          maxTagCount="responsive"
          placeholder="全部重大事项"
          value={eventTypes}
          onChange={value => setEventTypes(value as MajorEventType[])}
          style={{ minWidth: 260, flex: 1 }}
          options={eventTypeOptions}
        />
        <Space size={8} wrap>
          <Select
            value={limit}
            onChange={setLimit}
            style={{ width: 112 }}
            options={[
              { label: '前 40 条', value: 40 },
              { label: '前 80 条', value: 80 },
              { label: '前 120 条', value: 120 },
              { label: '前 200 条', value: 200 }
            ]}
          />
          <Select
            value={detailLimit}
            onChange={setDetailLimit}
            style={{ width: 132 }}
            options={[
              { label: '不解析 PDF', value: 0 },
              { label: '解析 8 条', value: 8 },
              { label: '解析 12 条', value: 12 },
              { label: '解析 24 条', value: 24 },
              { label: '解析 50 条', value: 50 }
            ]}
          />
        </Space>
      </div>

      {error && (
        <Alert className="shareholder-change-alert" type="warning" showIcon message={error} />
      )}

      {result?.warnings.length ? (
        <Alert
          className="shareholder-change-alert"
          type="info"
          showIcon
          message={result.warnings[0]}
        />
      ) : null}

      <div className="shareholder-change-summary-grid">
        <div className="metric-tile">
          <div className="metric-label"><FileSearchOutlined /><span>命中公告</span></div>
          <div className="metric-value">{result?.returned_count ?? '--'}</div>
          <div className="metric-note">总命中 {result?.total_found ?? '--'} 条</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><SafetyCertificateOutlined /><span>高风险</span></div>
          <div className="metric-value">{highRiskCount}</div>
          <div className="metric-note">处罚/ST/诉讼/冻结等</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><ThunderboltOutlined /><span>负面/重点</span></div>
          <div className="metric-value">{negativeCount}/{highPriorityCount}</div>
          <div className="metric-note">影响识别 / 重点类型</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><AuditOutlined /><span>PDF 明细</span></div>
          <div className="metric-value">{detailRate}</div>
          <div className="metric-note">成功/尝试解析</div>
        </div>
      </div>

      {result && (
        <Alert
          className="shareholder-change-alert"
          type="info"
          showIcon
          message={result.summary}
          description={result.coverage_note}
        />
      )}

      <div className="shareholder-change-toolbar">
        <Space size={8} wrap>
          <Select
            value={riskFilter}
            onChange={value => setRiskFilter(value as RiskFilter)}
            style={{ width: 120 }}
            options={[
              { label: '全部风险', value: 'all' },
              { label: '高风险', value: 'high' },
              { label: '中风险', value: 'medium' },
              { label: '低风险', value: 'low' }
            ]}
          />
          <Select
            value={impactFilter}
            onChange={value => setImpactFilter(value as ImpactFilter)}
            style={{ width: 128 }}
            options={[
              { label: '全部影响', value: 'all' },
              { label: '偏负面', value: 'negative' },
              { label: '待验证', value: 'mixed' },
              { label: '偏正面', value: 'positive' },
              { label: '未识别', value: 'unknown' }
            ]}
          />
          <Select
            value={sortMode}
            onChange={value => setSortMode(value as SortMode)}
            style={{ width: 132 }}
            options={[
              { label: '公告时间', value: 'time_desc' },
              { label: '风险优先', value: 'risk_desc' },
              { label: '影响优先', value: 'impact_desc' }
            ]}
          />
        </Space>
        <Input.Search
          allowClear
          placeholder="模糊搜索代码、公司、事项或风险"
          value={query}
          onChange={event => setQuery(event.target.value)}
          style={{ width: 280 }}
        />
      </div>

      <Spin spinning={loading}>
        <div className="shareholder-change-layout">
          <section className="shareholder-change-list-panel">
            {rankedRecords.length === 0 ? (
              <Empty description="没有匹配的重大事项公告" />
            ) : (
              <>
                <div className="shareholder-change-list-head">
                  <span>标的</span>
                  <span>事件/公告</span>
                  <span>核心变量</span>
                  <span>风险/日期</span>
                </div>
                {rankedRecords.map((record, index) => {
                  const key = getRecordKey(record);
                  const isSelected = selectedRecordKey === key;
                  return (
                    <button
                      type="button"
                      className={`shareholder-change-row ${isSelected ? 'selected' : ''}`}
                      key={key}
                      onClick={() => setSelectedRecordKey(key)}
                    >
                      <div className="shareholder-change-issuer">
                        <span className="shareholder-change-rank">#{index + 1}</span>
                        <div>
                          <div className="shareholder-change-symbol">{record.symbol}</div>
                          <div className="shareholder-change-name">{record.name}</div>
                        </div>
                      </div>
                      <div>
                        <div className="shareholder-change-row-title">{record.title}</div>
                        <div className="shareholder-change-event-meta">
                          <Tag color={eventTypeMeta[record.event_type].color}>{eventTypeMeta[record.event_type].label}</Tag>
                          <Tag color={statusMeta[record.status].color}>{statusMeta[record.status].label}</Tag>
                          <Tag color={impactMeta[record.impact].color}>{impactMeta[record.impact].label}</Tag>
                        </div>
                      </div>
                      <div>
                        <div className="shareholder-change-main-value">
                          {compactDisplayValue(record.amount || record.share_ratio || record.subject, 36)}
                        </div>
                        <div className="shareholder-change-meta">{compactDisplayValue(record.progress || record.deadline, 44)}</div>
                      </div>
                      <div className="shareholder-change-risk-cell">
                        <Tag color={riskMeta[record.risk_level].color}>{riskMeta[record.risk_level].label}</Tag>
                        <span>{formatDate(record.announcement_date)}</span>
                      </div>
                    </button>
                  );
                })}
              </>
            )}
          </section>

          <aside className="shareholder-change-detail-panel">
            {selectedRecord ? (
              <>
                <div className="shareholder-change-detail-head">
                  <div>
                    <div className="shareholder-change-symbol">{selectedRecord.symbol}</div>
                    <h3>{selectedRecord.name}</h3>
                    <Text type="secondary">{selectedRecord.source_name} · {formatTimestamp(result?.generated_at)}</Text>
                  </div>
                  <Space size={6} wrap>
                    <Tag color={eventTypeMeta[selectedRecord.event_type].color}>{eventTypeMeta[selectedRecord.event_type].label}</Tag>
                    <Tag color={riskMeta[selectedRecord.risk_level].color}>{riskMeta[selectedRecord.risk_level].label}</Tag>
                  </Space>
                </div>

                <div className="shareholder-change-detail-date">
                  <CalendarOutlined />
                  <span>{formatFullDate(selectedRecord.announcement_date)}</span>
                  <Tag color={statusMeta[selectedRecord.status].color}>{statusMeta[selectedRecord.status].label}</Tag>
                  <Tag color={impactMeta[selectedRecord.impact].color}>{impactMeta[selectedRecord.impact].label}</Tag>
                  <Tag color={selectedRecord.detail_quality === 'full' ? 'green' : selectedRecord.detail_quality === 'partial' ? 'blue' : 'default'}>
                    {detailQualityLabel[selectedRecord.detail_quality] || selectedRecord.detail_source}
                  </Tag>
                </div>

                <div className="shareholder-change-metric-grid">
                  <div className="shareholder-change-metric-box">
                    <span>主体</span>
                    <strong>{displayValue(selectedRecord.subject)}</strong>
                  </div>
                  <div className="shareholder-change-metric-box">
                    <span>金额 / 比例</span>
                    <strong>{displayValue([selectedRecord.amount, selectedRecord.share_ratio].filter(Boolean).join(' / '))}</strong>
                  </div>
                  <div className="shareholder-change-metric-box">
                    <span>进展</span>
                    <strong>{displayValue(selectedRecord.progress)}</strong>
                  </div>
                  <div className="shareholder-change-metric-box">
                    <span>期限</span>
                    <strong>{displayValue(selectedRecord.deadline)}</strong>
                  </div>
                </div>

                {selectedRecord.key_points.length > 0 && (
                  <div className="shareholder-change-detail-section">
                    <div className="shareholder-change-box-title"><ThunderboltOutlined />关键看点</div>
                    <div className="shareholder-change-chip-grid">
                      {selectedRecord.key_points.map(item => <span key={item}>{item}</span>)}
                    </div>
                  </div>
                )}

                {selectedRecord.risk_flags.length > 0 && (
                  <div className="shareholder-change-detail-section">
                    <div className="shareholder-change-box-title"><AlertOutlined />风险提示</div>
                    <div className="shareholder-change-chip-grid">
                      {selectedRecord.risk_flags.map(item => <span key={item}>{item}</span>)}
                    </div>
                  </div>
                )}

                {selectedRecord.action_items.length > 0 && (
                  <div className="shareholder-change-detail-section">
                    <div className="shareholder-change-box-title"><SafetyCertificateOutlined />后续动作</div>
                    <div className="shareholder-change-chip-grid">
                      {selectedRecord.action_items.map(item => <span key={item}>{item}</span>)}
                    </div>
                  </div>
                )}

                <div className="shareholder-change-detail-section">
                  <div className="shareholder-change-box-title"><FileSearchOutlined />公告证据</div>
                  <div className="shareholder-change-title">{selectedRecord.title}</div>
                  {selectedRecord.detail_summary && (
                    <div className="shareholder-change-excerpt">{selectedRecord.detail_summary}</div>
                  )}
                  {selectedRecord.evidence_excerpt && (
                    <div className="shareholder-change-excerpt">{selectedRecord.evidence_excerpt}</div>
                  )}
                </div>

                <div className="shareholder-change-actions">
                  <Button icon={<LinkOutlined />} onClick={() => openAnnouncement(selectedRecord.url)}>
                    原文公告
                  </Button>
                  {selectedStock && (
                    <Button type="primary" onClick={() => onStockSelect(selectedStock)}>
                      个股专区
                    </Button>
                  )}
                </div>
              </>
            ) : (
              <Empty description="选择一条重大事项公告查看明细" />
            )}
          </aside>
        </div>
      </Spin>
    </div>
  );
};

export default MajorEventCenter;
