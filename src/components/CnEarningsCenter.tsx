import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Alert, Button, Empty, Input, Select, Space, Spin, Tag, Typography } from 'antd';
import {
  AuditOutlined,
  BarChartOutlined,
  BulbOutlined,
  CalendarOutlined,
  CheckCircleOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined
} from '@ant-design/icons';
import { AppState, Stock } from '../types';
import {
  CnEarningsDiagnosisResponse,
  CnEarningsDiagnosisSignal,
  CnEarningsFinancialQuality,
  CnEarningsRecord,
  CnEarningsReportType,
  CnEarningsRiskLevel,
  diagnoseCnEarnings,
  enrichCnEarningsRecordDetail,
  scanCnEarnings
} from '../services/cnEarningsService';

const { Text } = Typography;

interface CnEarningsCenterProps {
  appState: AppState;
  onStockSelect: (stock: Stock) => void;
}

type ReportFilter = 'all' | CnEarningsReportType;
type RiskFilter = 'all' | CnEarningsRiskLevel;
type SortMode = 'time_desc' | 'revenue_desc' | 'profit_desc';

const reportTypeMeta: Record<CnEarningsReportType, { label: string; color: string }> = {
  annual: { label: '年报', color: 'blue' },
  semiannual: { label: '半年报', color: 'cyan' },
  q1: { label: '一季报', color: 'green' },
  q3: { label: '三季报', color: 'purple' },
  forecast: { label: '业绩预告', color: 'gold' },
  flash: { label: '业绩快报', color: 'lime' },
  correction: { label: '更正/修正', color: 'orange' },
  other: { label: '财报公告', color: 'default' }
};

const riskMeta: Record<CnEarningsRiskLevel, { label: string; color: string }> = {
  high: { label: '高风险', color: 'red' },
  medium: { label: '中风险', color: 'gold' },
  low: { label: '低风险', color: 'green' }
};

const detailQualityLabel: Record<string, string> = {
  full: '明细完整',
  partial: '部分明细',
  title_only: '标题识别'
};

const diagnosisSignalMeta: Record<CnEarningsDiagnosisSignal, { label: string; color: string }> = {
  positive: { label: '偏正面', color: 'green' },
  neutral: { label: '中性', color: 'blue' },
  negative: { label: '偏负面', color: 'red' },
  watch: { label: '观察', color: 'gold' }
};

const financialQualityMeta: Record<CnEarningsFinancialQuality, { label: string; color: string }> = {
  strong: { label: '质量强', color: 'green' },
  stable: { label: '质量稳', color: 'cyan' },
  mixed: { label: '质量分化', color: 'gold' },
  weak: { label: '质量弱', color: 'red' },
  unknown: { label: '待核验', color: 'default' }
};

const getRecordKey = (record: CnEarningsRecord): string => {
  return record.announcement_id || `${record.symbol}-${record.announcement_date}-${record.title}`;
};

const displayValue = (value?: string, fallback = '--'): string => {
  const trimmed = (value || '').trim();
  return trimmed || fallback;
};

const trimMetricNumber = (value: number, digits = 2): string => {
  return value
    .toFixed(digits)
    .replace(/\.0+$/, '')
    .replace(/(\.\d*[1-9])0+$/, '$1');
};

const parseMoneyAmount = (value?: string): number | null => {
  const raw = (value || '').trim();
  if (!raw || raw === '--' || /%|每股|EPS|ROE/i.test(raw)) {
    return null;
  }

  const compact = raw.replace(/[,，\s]/g, '');
  const match = compact.match(/-?\d+(?:\.\d+)?/);
  if (!match) {
    return null;
  }

  const number = Number(match[0]);
  if (!Number.isFinite(number)) {
    return null;
  }

  if (/亿/.test(compact)) {
    return number * 100_000_000;
  }
  if (/万/.test(compact)) {
    return number * 10_000;
  }
  return number;
};

const formatMoneyAmount = (value?: string, fallback = '--'): string => {
  const amount = parseMoneyAmount(value);
  if (amount == null) {
    return displayValue(value, fallback);
  }

  const absAmount = Math.abs(amount);
  if (absAmount >= 10_000_000) {
    return `${trimMetricNumber(amount / 100_000_000)}亿`;
  }
  if (absAmount >= 10_000) {
    return `${trimMetricNumber(amount / 10_000)}万`;
  }
  return trimMetricNumber(amount);
};

const formatMoneyWithChange = (amount?: string, change?: string, fallback = '--'): string => {
  const parts = [formatMoneyAmount(amount, ''), displayValue(change, '')].filter(Boolean);
  return parts.length ? parts.join(' / ') : fallback;
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

const parseMetricValue = (value?: string): number | null => {
  const compact = (value || '').replace(/[,，\s]/g, '');
  if (!compact || compact === '--') {
    return null;
  }

  const matches = Array.from(compact.matchAll(/(-?\d+(?:\.\d+)?)(万|亿)?/g));
  if (matches.length === 0) {
    return null;
  }

  const values = matches
    .map(match => {
      const number = Number(match[1]);
      if (!Number.isFinite(number)) {
        return null;
      }
      const multiplier = match[2] === '亿' ? 100_000_000 : match[2] === '万' ? 10_000 : 1;
      return Math.abs(number * multiplier);
    })
    .filter((number): number is number => typeof number === 'number');

  return values.length ? Math.max(...values) : null;
};

const metricSortScore = (value?: string): number => {
  const parsed = parseMetricValue(value);
  return parsed == null ? Number.NEGATIVE_INFINITY : parsed;
};

const announcementTimeScore = (record: CnEarningsRecord): number => {
  const parsed = new Date(`${record.announcement_date}T00:00:00`).getTime();
  return Number.isFinite(parsed) ? parsed : 0;
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

const CnEarningsCenter: React.FC<CnEarningsCenterProps> = ({ appState, onStockSelect }) => {
  const [days, setDays] = useState(30);
  const [limit, setLimit] = useState(120);
  const [detailLimit, setDetailLimit] = useState(12);
  const [reportFilter, setReportFilter] = useState<ReportFilter>('all');
  const [riskFilter, setRiskFilter] = useState<RiskFilter>('all');
  const [sortMode, setSortMode] = useState<SortMode>('time_desc');
  const [query, setQuery] = useState('');
  const [result, setResult] = useState<Awaited<ReturnType<typeof scanCnEarnings>> | null>(null);
  const [selectedRecordKey, setSelectedRecordKey] = useState<string | null>(null);
  const [diagnosisByKey, setDiagnosisByKey] = useState<Record<string, CnEarningsDiagnosisResponse>>({});
  const [diagnosisLoadingKey, setDiagnosisLoadingKey] = useState<string | null>(null);
  const [diagnosisError, setDiagnosisError] = useState('');
  const [detailLoadingKey, setDetailLoadingKey] = useState<string | null>(null);
  const [detailAttemptedByKey, setDetailAttemptedByKey] = useState<Record<string, boolean>>({});
  const [detailError, setDetailError] = useState('');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState('');
  const lastAutoScanSignatureRef = useRef('');

  const stockBySymbol = useMemo(() => {
    return new Map(appState.stocks.map(stock => [stock.symbol.toUpperCase(), stock]));
  }, [appState.stocks]);

  const loadEarnings = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const response = await scanCnEarnings({
        market: 'A',
        days,
        limit,
        detail_limit: detailLimit
	      });
	      setResult(response);
	      setDetailAttemptedByKey({});
	      setDetailError('');
	      setSelectedRecordKey(prev => {
        const stillExists = prev && response.records.some(record => getRecordKey(record) === prev);
        return stillExists ? prev : response.records[0] ? getRecordKey(response.records[0]) : null;
      });
    } catch (scanError) {
      console.warn('CN earnings scan failed:', scanError);
      setError('A 股财报扫描服务暂不可用，请稍后重试或检查后端连接。');
    } finally {
      setLoading(false);
    }
  }, [days, detailLimit, limit]);

  useEffect(() => {
    const signature = `${days}:${limit}:${detailLimit}`;
    if (lastAutoScanSignatureRef.current === signature) {
      return;
    }
    lastAutoScanSignatureRef.current = signature;
    void loadEarnings();
  }, [days, detailLimit, limit, loadEarnings]);

  const records = result?.records || [];

  const filteredRecords = useMemo(() => {
    const keywords = query.trim().split(/\s+/).filter(Boolean);

    return records.filter(record => {
      const searchFields = [
        record.symbol,
        record.name,
        record.title,
        record.fiscal_year,
        record.fiscal_period,
        record.revenue,
        record.net_profit,
        record.eps,
        ...record.tags,
        ...record.key_takeaways,
        ...record.risk_flags
      ];
      const matchesQuery = keywords.length === 0
        || keywords.every(keyword => searchFields.some(field => fuzzyMatch(field, keyword)));
      const matchesReport = reportFilter === 'all' || record.report_type === reportFilter;
      const matchesRisk = riskFilter === 'all' || record.risk_level === riskFilter;
      return matchesQuery && matchesReport && matchesRisk;
    });
  }, [query, records, reportFilter, riskFilter]);

  const rankedRecords = useMemo(() => {
    return [...filteredRecords].sort((left, right) => {
      const timeDelta = announcementTimeScore(right) - announcementTimeScore(left);
      if (sortMode === 'revenue_desc') {
        const revenueDelta = metricSortScore(right.revenue) - metricSortScore(left.revenue);
        return revenueDelta || timeDelta || left.symbol.localeCompare(right.symbol);
      }
      if (sortMode === 'profit_desc') {
        const profitDelta = metricSortScore(right.net_profit) - metricSortScore(left.net_profit);
        return profitDelta || timeDelta || left.symbol.localeCompare(right.symbol);
      }
      return timeDelta || left.symbol.localeCompare(right.symbol);
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
  const selectedDiagnosis = selectedRecord ? diagnosisByKey[getRecordKey(selectedRecord)] : undefined;
  const highRiskCount = records.filter(record => record.risk_level === 'high').length;
  const reportCount = records.filter(record => ['annual', 'semiannual', 'q1', 'q3'].includes(record.report_type)).length;
  const detailRate = result
    ? `${result.detail_success_count}/${result.detail_attempted_count || result.detail_success_count || 0}`
    : '--';

  const openAnnouncement = (url: string) => {
    if (url) {
      window.open(url, '_blank', 'noopener,noreferrer');
    }
  };

  const loadRecordDetail = useCallback(async (record: CnEarningsRecord) => {
    const key = getRecordKey(record);
    setDetailAttemptedByKey(prev => ({ ...prev, [key]: true }));
    setDetailLoadingKey(key);
    setDetailError('');
    try {
      const response = await enrichCnEarningsRecordDetail(record);
      const nextKey = getRecordKey(response.record);
      setResult(prev => {
        if (!prev) {
          return prev;
        }
        return {
          ...prev,
          records: prev.records.map(item => (getRecordKey(item) === key ? response.record : item))
        };
      });
      if (nextKey !== key) {
        setSelectedRecordKey(nextKey);
      }
      setDetailError(response.warnings[0] || '');
    } catch (detailRequestError) {
      console.warn('CN earnings record detail failed:', detailRequestError);
      setDetailError('选中财报 PDF 明细解析失败，请打开原文核验。');
    } finally {
      setDetailLoadingKey(prev => (prev === key ? null : prev));
    }
  }, []);

  useEffect(() => {
    if (!selectedRecord) {
      return;
    }
    if (selectedRecord.detail_source !== 'title' || selectedRecord.detail_quality !== 'title_only') {
      return;
    }
    if (detailLoadingKey === getRecordKey(selectedRecord)) {
      return;
    }
    if (detailAttemptedByKey[getRecordKey(selectedRecord)]) {
      return;
    }
    void loadRecordDetail(selectedRecord);
  }, [detailAttemptedByKey, detailLoadingKey, loadRecordDetail, selectedRecord]);

  const loadDiagnosis = useCallback(async (record: CnEarningsRecord) => {
    const key = getRecordKey(record);
    setDiagnosisLoadingKey(key);
    setDiagnosisError('');
    try {
      const diagnosis = await diagnoseCnEarnings({
        record,
        style: 'brief',
        locale: 'zh-CN'
      });
      setDiagnosisByKey(prev => ({ ...prev, [key]: diagnosis }));
    } catch (diagnosisRequestError) {
      console.warn('CN earnings diagnosis failed:', diagnosisRequestError);
      setDiagnosisError('AI 诊断暂不可用，请检查模型配置或稍后重试。');
    } finally {
      setDiagnosisLoadingKey(prev => (prev === key ? null : prev));
    }
  }, []);

  return (
    <div className="shareholder-change-shell cn-earnings-shell">
      <div className="shareholder-change-header">
        <div>
          <div className="dashboard-eyebrow">A SHARE EARNINGS</div>
          <h2 className="dashboard-title">A股财报</h2>
          <div className="dashboard-subtitle">扫描 A 股年报、季报、业绩预告和快报，抽取核心财务指标并保留原文核验。</div>
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
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadEarnings}>
            刷新
          </Button>
        </Space>
      </div>

      <div className="shareholder-change-scanbar">
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
              { label: '解析 24 条', value: 24 }
            ]}
          />
        </Space>
        <Text type="secondary">数据源：巨潮资讯网公告检索</Text>
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
          <div className="metric-label"><BarChartOutlined /><span>正式财报</span></div>
          <div className="metric-value">{reportCount}</div>
          <div className="metric-note">年报/半年报/季报</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><SafetyCertificateOutlined /><span>高风险</span></div>
          <div className="metric-value">{highRiskCount}</div>
          <div className="metric-note">亏损/非标/ST/退市风险</div>
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
            value={reportFilter}
            onChange={value => setReportFilter(value as ReportFilter)}
            style={{ width: 128 }}
            options={[
              { label: '全部财报', value: 'all' },
              { label: '年报', value: 'annual' },
              { label: '半年报', value: 'semiannual' },
              { label: '一季报', value: 'q1' },
              { label: '三季报', value: 'q3' },
              { label: '业绩预告', value: 'forecast' },
              { label: '业绩快报', value: 'flash' },
              { label: '更正/修正', value: 'correction' }
            ]}
          />
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
            value={sortMode}
            onChange={value => setSortMode(value as SortMode)}
            style={{ width: 132 }}
            options={[
              { label: '公告时间', value: 'time_desc' },
              { label: '营收排名', value: 'revenue_desc' },
              { label: '净利排名', value: 'profit_desc' }
            ]}
          />
        </Space>
        <Input.Search
          allowClear
          placeholder="模糊搜索代码、公司、指标或公告"
          value={query}
          onChange={event => setQuery(event.target.value)}
          style={{ width: 280 }}
        />
      </div>

      <Spin spinning={loading}>
        <div className="shareholder-change-layout">
          <section className="shareholder-change-list-panel">
            {rankedRecords.length === 0 ? (
              <Empty description="没有匹配的 A 股财报公告" />
            ) : (
              <>
                <div className="shareholder-change-list-head">
                  <span>标的</span>
                  <span>财报/公告</span>
                  <span>核心指标</span>
                  <span>风险/日期</span>
                </div>
                {rankedRecords.map(record => {
                  const key = getRecordKey(record);
                  const isSelected = selectedRecordKey === key;
                  return (
                    <button
                      type="button"
	                      className={`shareholder-change-row ${isSelected ? 'selected' : ''}`}
	                      key={key}
	                      onClick={() => {
	                        setDetailError('');
	                        setDiagnosisError('');
	                        setSelectedRecordKey(key);
	                      }}
	                    >
                      <div>
                        <div className="shareholder-change-symbol">{record.symbol}</div>
                        <div className="shareholder-change-name">{record.name}</div>
                      </div>
                      <div>
                        <div className="shareholder-change-row-title">{record.title}</div>
                        <div className="shareholder-change-event-meta">
                          <Tag color={reportTypeMeta[record.report_type].color}>{reportTypeMeta[record.report_type].label}</Tag>
                          <span>{record.fiscal_year || '--'}</span>
                          {record.fiscal_period && <span>{record.fiscal_period}</span>}
                        </div>
                      </div>
                      <div>
                        <div className="shareholder-change-main-value">
                          {record.net_profit || record.revenue
                            ? formatMoneyAmount(record.net_profit || record.revenue)
                            : reportTypeMeta[record.report_type].label}
                        </div>
                        <div className="shareholder-change-meta">
                          {displayValue(record.net_profit_yoy || record.revenue_yoy || record.eps, detailQualityLabel[record.detail_quality] || '待解析')}
                        </div>
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
                    <Tag color={reportTypeMeta[selectedRecord.report_type].color}>{reportTypeMeta[selectedRecord.report_type].label}</Tag>
                    <Tag color={riskMeta[selectedRecord.risk_level].color}>{riskMeta[selectedRecord.risk_level].label}</Tag>
                    <Button
                      size="small"
                      type="primary"
                      icon={<BulbOutlined />}
                      loading={diagnosisLoadingKey === getRecordKey(selectedRecord)}
                      onClick={() => void loadDiagnosis(selectedRecord)}
                    >
                      {selectedDiagnosis ? '重新诊断' : 'AI 诊断'}
                    </Button>
                  </Space>
                </div>

                <div className="shareholder-change-detail-date">
                  <CalendarOutlined />
                  <span>{formatFullDate(selectedRecord.announcement_date)}</span>
                  {selectedRecord.fiscal_year && <Tag>{selectedRecord.fiscal_year}</Tag>}
                  <Tag color={selectedRecord.detail_quality === 'full' ? 'green' : selectedRecord.detail_quality === 'partial' ? 'blue' : 'default'}>
                    {detailQualityLabel[selectedRecord.detail_quality] || selectedRecord.detail_source}
                  </Tag>
                </div>

                {detailLoadingKey === getRecordKey(selectedRecord) && (
                  <Alert
                    className="shareholder-change-alert"
                    type="info"
                    showIcon
                    message="正在解析选中财报 PDF 明细"
                  />
                )}

                {detailError && detailLoadingKey !== getRecordKey(selectedRecord) && (
                  <Alert
                    className="shareholder-change-alert"
                    type="warning"
                    showIcon
                    message={detailError}
                  />
                )}

                <div className="shareholder-change-metric-grid">
                  <div className="shareholder-change-metric-box">
                    <span>营业收入 / 同比</span>
                    <strong>{formatMoneyWithChange(selectedRecord.revenue, selectedRecord.revenue_yoy, '待解析')}</strong>
                  </div>
                  <div className="shareholder-change-metric-box">
                    <span>归母净利润 / 同比</span>
                    <strong>{formatMoneyWithChange(selectedRecord.net_profit, selectedRecord.net_profit_yoy, '待解析')}</strong>
                  </div>
                  <div className="shareholder-change-metric-box">
                    <span>扣非净利润 / 同比</span>
                    <strong>{formatMoneyWithChange(selectedRecord.deducted_net_profit, selectedRecord.deducted_net_profit_yoy, '待解析')}</strong>
                  </div>
                  <div className="shareholder-change-metric-box">
                    <span>EPS / ROE</span>
                    <strong>{displayValue([selectedRecord.eps, selectedRecord.roe].filter(Boolean).join(' / '), '待解析')}</strong>
                  </div>
                </div>

                <div className="shareholder-change-detail-section">
                  <div className="shareholder-change-box-title"><BarChartOutlined />财务质量</div>
                  <div className="shareholder-change-holding-strip">
                    <div>
                      <span>经营现金流</span>
                      <strong>{formatMoneyAmount(selectedRecord.operating_cash_flow, '待解析')}</strong>
                    </div>
                    <div>
                      <span>毛利率 / 总资产</span>
                      <strong>{displayValue([selectedRecord.gross_margin, formatMoneyAmount(selectedRecord.total_assets, '')].filter(Boolean).join(' / '), '待解析')}</strong>
                    </div>
                  </div>
                </div>

                {selectedRecord.key_takeaways.length > 0 && (
                  <div className="shareholder-change-detail-section">
                    <div className="shareholder-change-box-title">核心看点</div>
                    <div className="shareholder-change-chip-grid">
                      {selectedRecord.key_takeaways.map(item => <span key={item}>{item}</span>)}
                    </div>
                  </div>
                )}

                {selectedRecord.risk_flags.length > 0 && (
                  <div className="shareholder-change-detail-section">
                    <div className="shareholder-change-box-title"><SafetyCertificateOutlined />风险提示</div>
                    <div className="shareholder-change-chip-grid">
                      {selectedRecord.risk_flags.map(item => <span key={item}>{item}</span>)}
                    </div>
                  </div>
                )}

                {(selectedDiagnosis || diagnosisError || diagnosisLoadingKey === getRecordKey(selectedRecord)) && (
                  <div className="shareholder-change-detail-section cn-earnings-diagnosis-section">
                    <div className="shareholder-change-box-title"><BulbOutlined />AI 诊断</div>
                    {diagnosisError && (
                      <Alert className="cn-earnings-diagnosis-alert" type="warning" showIcon message={diagnosisError} />
                    )}
                    {diagnosisLoadingKey === getRecordKey(selectedRecord) && !selectedDiagnosis ? (
                      <div className="cn-earnings-diagnosis-loading">
                        <Spin size="small" />
                        <span>正在调度 EarningsReviewer、QualityAgent、RiskForensicsAgent、ActionAgent</span>
                      </div>
                    ) : selectedDiagnosis ? (
                      <div className="cn-earnings-diagnosis">
                        <div className="cn-earnings-diagnosis-head">
                          <div className="cn-earnings-score">
                            <strong>{selectedDiagnosis.overall_score}</strong>
                            <span>诊断分</span>
                          </div>
                          <div>
                            <div className="cn-earnings-verdict">{selectedDiagnosis.verdict}</div>
                            <div className="cn-earnings-diagnosis-summary">{selectedDiagnosis.summary}</div>
                            <Space size={6} wrap>
                              <Tag color={diagnosisSignalMeta[selectedDiagnosis.diagnosis_signal].color}>
                                {diagnosisSignalMeta[selectedDiagnosis.diagnosis_signal].label}
                              </Tag>
                              <Tag color={riskMeta[selectedDiagnosis.risk_level].color}>{riskMeta[selectedDiagnosis.risk_level].label}</Tag>
                              <Tag color={financialQualityMeta[selectedDiagnosis.financial_quality].color}>
                                {financialQualityMeta[selectedDiagnosis.financial_quality].label}
                              </Tag>
                            </Space>
                          </div>
                        </div>

                        <div className="cn-earnings-agent-flow">
                          {selectedDiagnosis.agent_steps.map(step => (
                            <div className={`cn-earnings-agent-step ${step.status}`} key={`${step.agent}-${step.role}`}>
                              <CheckCircleOutlined />
                              <div>
                                <strong>{step.agent}</strong>
                                <span>{step.role}</span>
                                <p>{step.finding}</p>
                              </div>
                            </div>
                          ))}
                        </div>

                        <div className="cn-earnings-diagnosis-grid">
                          <div>
                            <span>积极线索</span>
                            {(selectedDiagnosis.positives.length ? selectedDiagnosis.positives : ['暂无明确积极线索']).map(item => <p key={item}>{item}</p>)}
                          </div>
                          <div>
                            <span>风险关注</span>
                            {(selectedDiagnosis.concerns.length ? selectedDiagnosis.concerns : ['暂无新增风险']).map(item => <p key={item}>{item}</p>)}
                          </div>
                          <div>
                            <span>待核验问题</span>
                            {selectedDiagnosis.questions.map(item => <p key={item}>{item}</p>)}
                          </div>
                          <div>
                            <span>下一步动作</span>
                            {selectedDiagnosis.actions.map(item => <p key={item}>{item}</p>)}
                          </div>
                        </div>

                        {selectedDiagnosis.evidence.length > 0 && (
                          <div className="cn-earnings-evidence">
                            <span>证据</span>
                            {selectedDiagnosis.evidence.slice(0, 4).map(item => <em key={item}>{item}</em>)}
                          </div>
                        )}
                        <div className="cn-earnings-diagnosis-foot">
                          <span>{selectedDiagnosis.provider} · {selectedDiagnosis.model}</span>
                          <span>置信度 {Math.round(selectedDiagnosis.confidence * 100)}%</span>
                        </div>
                      </div>
                    ) : null}
                  </div>
                )}

                <div className="shareholder-change-detail-section">
                  <div className="shareholder-change-box-title"><FileSearchOutlined />公告</div>
                  <div className="shareholder-change-title">{selectedRecord.title}</div>
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
              <Empty description="选择一条财报公告查看明细" />
            )}
          </aside>
        </div>
      </Spin>
    </div>
  );
};

export default CnEarningsCenter;
