import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  ConfigProvider,
  Drawer,
  Empty,
  Input,
  Progress,
  Segmented,
  Select,
  Space,
  Table,
  Tag,
  Tooltip,
  Typography,
  theme as antdTheme
} from 'antd';
import type { ColumnsType } from 'antd/es/table';
import {
  DatabaseOutlined,
  ExclamationCircleOutlined,
  LinkOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  SearchOutlined
} from '@ant-design/icons';
import CenterShell from './common/CenterShell';
import { useTheme } from '../context/ThemeContext';
import {
  fetchMarketRiskRadar,
  MarketRiskCompany,
  MarketRiskRadarResponse,
  RiskDimensionKey,
  RiskLevel,
  RiskMarket
} from '../services/marketRiskRadarService';
import './MarketRiskRadar.css';

const { Paragraph, Text, Title } = Typography;

const MARKET_LABELS: Record<RiskMarket, string> = {
  CN: 'A股',
  HK: '港股',
  US: '美股'
};

const LEVEL_META: Record<RiskLevel, { label: string; color: string; hex: string; description: string }> = {
  green: { label: '绿灯', color: 'green', hex: '#16a34a', description: '暂未触发显著风险阈值' },
  yellow: { label: '黄灯', color: 'gold', hex: '#d97706', description: '需要加入观察并核验证据' },
  orange: { label: '橙灯', color: 'orange', hex: '#ea580c', description: '多维风险共振，需要重点复核' },
  red: { label: '红灯', color: 'red', hex: '#dc2626', description: '高强度风险信号，优先人工核验' }
};

const DIMENSION_META: Record<RiskDimensionKey, { label: string; description: string }> = {
  macro: { label: '宏观', description: '利率、波动率、信用与市场环境' },
  industry: { label: '行业', description: '同组公司价格压力与行业共振' },
  stock: { label: '个股', description: '当日波动、60日趋势和估值异常' },
  flow: { label: '资金', description: '量比、换手和主力资金方向' },
  information: { label: '信息', description: '站内快讯、文章、研报的风险证据' }
};

type MarketFilter = 'ALL' | RiskMarket;
type LevelFilter = 'all' | RiskLevel | 'high';

const formatNumber = (value?: number | null, digits = 2) => (
  typeof value === 'number' && Number.isFinite(value) ? value.toFixed(digits) : '—'
);

const formatMarketCap = (value?: number | null, currency?: string) => {
  if (typeof value !== 'number' || !Number.isFinite(value)) return '待更新';
  const symbol = currency === 'CNY' ? '¥' : currency === 'HKD' ? 'HK$' : '$';
  if (value >= 1e12) return `${symbol}${(value / 1e12).toFixed(2)}万亿`;
  if (value >= 1e9) return `${symbol}${(value / 1e9).toFixed(1)}十亿`;
  return `${symbol}${(value / 1e8).toFixed(1)}亿`;
};

const formatTime = (value?: string | null) => {
  if (!value) return '时间未知';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString('zh-CN', { hour12: false });
};

const RiskTag: React.FC<{ level: RiskLevel; score?: number }> = ({ level, score }) => {
  const meta = LEVEL_META[level];
  return (
    <Tag color={meta.color} className="market-risk-level-tag">
      {meta.label}{typeof score === 'number' ? ` ${score.toFixed(0)}` : ''}
    </Tag>
  );
};

const MarketRiskRadar: React.FC = () => {
  const { theme: colorTheme } = useTheme();
  const [data, setData] = useState<MarketRiskRadarResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [marketFilter, setMarketFilter] = useState<MarketFilter>('ALL');
  const [levelFilter, setLevelFilter] = useState<LevelFilter>('all');
  const [query, setQuery] = useState('');
  const [selected, setSelected] = useState<MarketRiskCompany | null>(null);

  const load = useCallback(async (force = false) => {
    setLoading(true);
    setError(null);
    try {
      setData(await fetchMarketRiskRadar(force));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '风险雷达加载失败');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load(false);
  }, [load]);

  const allCompanies = useMemo(
    () => data?.markets.flatMap(market => market.companies) || [],
    [data]
  );

  const visibleCompanies = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return allCompanies.filter(company => {
      if (marketFilter !== 'ALL' && company.market !== marketFilter) return false;
      if (levelFilter === 'high' && !['orange', 'red'].includes(company.risk_level)) return false;
      if (levelFilter !== 'all' && levelFilter !== 'high' && company.risk_level !== levelFilter) return false;
      if (needle && !`${company.name} ${company.symbol} ${company.sector}`.toLowerCase().includes(needle)) return false;
      return true;
    });
  }, [allCompanies, levelFilter, marketFilter, query]);

  const highestRisk = useMemo(
    () => [...allCompanies].sort((a, b) => b.risk_score - a.risk_score)[0],
    [allCompanies]
  );

  const columns: ColumnsType<MarketRiskCompany> = [
    {
      title: '市值排名',
      key: 'rank',
      width: 92,
      fixed: 'left',
      render: (_, company) => (
        <div className="market-risk-rank">
          <span>#{company.rank}</span>
          <Tag>{MARKET_LABELS[company.market]}</Tag>
        </div>
      )
    },
    {
      title: '公司',
      key: 'company',
      width: 190,
      fixed: 'left',
      render: (_, company) => (
        <div className="market-risk-company">
          <strong>{company.name}</strong>
          <span>{company.symbol} · {company.sector}</span>
        </div>
      )
    },
    {
      title: '总市值',
      key: 'market_cap',
      width: 130,
      sorter: (a, b) => (a.market_cap || 0) - (b.market_cap || 0),
      render: (_, company) => (
        <div>
          <div>{formatMarketCap(company.market_cap, company.currency)}</div>
          {company.data_status !== 'live' && (
            <Text type="warning" className="market-risk-small">
              {company.data_status === 'stale' ? '缓存排名' : '降级候选池'}
            </Text>
          )}
        </div>
      )
    },
    {
      title: '当日',
      dataIndex: 'change_pct',
      width: 90,
      sorter: (a, b) => (a.change_pct || 0) - (b.change_pct || 0),
      render: (value?: number | null) => (
        <span className={typeof value === 'number' ? (value >= 0 ? 'market-risk-up' : 'market-risk-down') : ''}>
          {typeof value === 'number' ? `${value >= 0 ? '+' : ''}${value.toFixed(2)}%` : '—'}
        </span>
      )
    },
    {
      title: '近60日',
      dataIndex: 'change_60d_pct',
      width: 90,
      sorter: (a, b) => (a.change_60d_pct || 0) - (b.change_60d_pct || 0),
      render: (value?: number | null) => (
        <span className={typeof value === 'number' ? (value >= 0 ? 'market-risk-up' : 'market-risk-down') : ''}>
          {typeof value === 'number' ? `${value >= 0 ? '+' : ''}${value.toFixed(1)}%` : '—'}
        </span>
      )
    },
    {
      title: '预警等级',
      key: 'risk',
      width: 170,
      sorter: (a, b) => a.risk_score - b.risk_score,
      defaultSortOrder: 'descend',
      render: (_, company) => (
        <div className="market-risk-score-cell">
          <div>
            <RiskTag level={company.risk_level} score={company.risk_score} />
            <Text type="secondary">置信度 {company.confidence === 'high' ? '高' : company.confidence === 'medium' ? '中' : '低'}</Text>
          </div>
          <Progress
            percent={company.risk_score}
            showInfo={false}
            size="small"
            strokeColor={LEVEL_META[company.risk_level].hex}
            trailColor="var(--border-soft)"
          />
        </div>
      )
    },
    {
      title: '触发原因',
      key: 'drivers',
      width: 310,
      render: (_, company) => (
        <div className="market-risk-drivers">
          {company.drivers.slice(0, 2).map(driver => <span key={driver}>• {driver}</span>)}
        </div>
      )
    },
    {
      title: '站内证据',
      key: 'evidence',
      width: 100,
      align: 'center',
      sorter: (a, b) => a.site_signal_count - b.site_signal_count,
      render: (_, company) => (
        <Tooltip title="公司名称、代码或别名命中的近期站内快讯/文章/研报">
          <Badge
            count={company.site_signal_count}
            showZero
            color={company.evidence.length > 0 ? '#ea580c' : '#64748b'}
          />
        </Tooltip>
      )
    }
  ];

  const toolbar = (
    <>
      <Segmented
        value={marketFilter}
        onChange={value => setMarketFilter(value as MarketFilter)}
        options={[
          { label: `全部 ${allCompanies.length}`, value: 'ALL' },
          { label: 'A股 20', value: 'CN' },
          { label: '港股 20', value: 'HK' },
          { label: '美股 20', value: 'US' }
        ]}
      />
      <Space wrap>
        <Input
          allowClear
          prefix={<SearchOutlined />}
          placeholder="搜索公司 / 代码 / 行业"
          value={query}
          onChange={event => setQuery(event.target.value)}
          className="market-risk-search"
        />
        <Select<LevelFilter>
          value={levelFilter}
          onChange={setLevelFilter}
          className="market-risk-level-filter"
          options={[
            { value: 'all', label: '全部等级' },
            { value: 'high', label: '橙灯 + 红灯' },
            { value: 'yellow', label: '仅黄灯' },
            { value: 'green', label: '仅绿灯' }
          ]}
        />
      </Space>
    </>
  );

  return (
    <ConfigProvider
      theme={{
        algorithm: colorTheme === 'dark' ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: '#d97706',
          borderRadius: 7
        }
      }}
    >
      <CenterShell
      eyebrow="MARKET RISK EARLY WARNING"
      title="跨市场风险预警雷达"
      subtitle="A股、港股、美股各市值前20家公司 · 宏观 × 行业 × 个股 × 资金 × daocaijing站内信息"
      icon={<SafetyCertificateOutlined />}
      actions={(
        <Space wrap>
          {data && <Text type="secondary">更新于 {formatTime(data.generated_at)}</Text>}
          <Button icon={<ReloadOutlined />} loading={loading} onClick={() => void load(true)}>
            刷新排名与信号
          </Button>
        </Space>
      )}
      toolbar={toolbar}
      error={error}
      dataQuality={data?.data_quality}
      loading={loading && !data}
      loadingText="正在聚合三个市场的市值排名与风险证据…"
      className="market-risk-radar"
    >
      {!data ? (
        <Empty description={error || '暂无风险雷达数据'} />
      ) : (
        <>
          {data.warnings.length > 0 && (
            <Alert
              type="warning"
              showIcon
              className="market-risk-warning"
              message="部分数据源已降级"
              description={data.warnings.join('；')}
            />
          )}

          <div className="market-risk-kpis">
            <Card>
              <Text type="secondary">覆盖范围</Text>
              <Title level={3}>{data.coverage.companies}<small> 家</small></Title>
              <Text>A / H / 美股各 {data.coverage.per_market_limit} 家</Text>
            </Card>
            <Card className="market-risk-kpi-danger">
              <Text type="secondary">高等级预警</Text>
              <Title level={3}>
                {data.summary.counts.red + data.summary.counts.orange}<small> 家</small>
              </Title>
              <Text>红灯 {data.summary.counts.red} · 橙灯 {data.summary.counts.orange}</Text>
            </Card>
            <Card>
              <Text type="secondary">市场平均风险</Text>
              <Title level={3}>{data.summary.average_risk.toFixed(1)}</Title>
              <RiskTag level={data.summary.risk_level} />
            </Card>
            <Card>
              <Text type="secondary">站内信息命中</Text>
              <Title level={3}>{data.summary.site_signal_companies}<small> 家</small></Title>
              <Text>已关联快讯 / 文章 / 研报</Text>
            </Card>
            <Card>
              <Text type="secondary">当前最高风险</Text>
              <Title level={4}>{highestRisk?.name || '—'}</Title>
              {highestRisk && <RiskTag level={highestRisk.risk_level} score={highestRisk.risk_score} />}
            </Card>
          </div>

          <div className="market-risk-market-strip">
            {data.summary.market_summaries.map(summary => (
              <button
                key={summary.market}
                type="button"
                className={marketFilter === summary.market ? 'is-active' : ''}
                onClick={() => setMarketFilter(summary.market)}
              >
                <span>{summary.label}</span>
                <strong>平均 {summary.average_risk.toFixed(1)}</strong>
                <small>
                  红 {summary.counts.red} · 橙 {summary.counts.orange} · 黄 {summary.counts.yellow}
                </small>
              </button>
            ))}
          </div>

          <Card className="market-risk-table-card" title={`公司预警清单（${visibleCompanies.length}）`}>
            <Table<MarketRiskCompany>
              rowKey={company => `${company.market}:${company.symbol}`}
              columns={columns}
              dataSource={visibleCompanies}
              pagination={{ pageSize: 20, showSizeChanger: false }}
              scroll={{ x: 1180 }}
              size="middle"
              onRow={company => ({
                onClick: () => setSelected(company),
                className: 'market-risk-clickable-row'
              })}
            />
          </Card>

          <div className="market-risk-methodology">
            <Card title="评分方法与数据边界">
              <div className="market-risk-weight-grid">
                {(Object.keys(data.methodology.weights) as RiskDimensionKey[]).map(key => (
                  <Tooltip key={key} title={DIMENSION_META[key].description}>
                    <div>
                      <span>{DIMENSION_META[key].label}</span>
                      <strong>{data.methodology.weights[key]}%</strong>
                    </div>
                  </Tooltip>
                ))}
              </div>
              <Paragraph>{data.methodology.explanation}</Paragraph>
              <Text type="secondary">{data.coverage.basis}</Text>
            </Card>
            <Card title="数据源状态">
              {data.sources.map(source => (
                <div className="market-risk-source-row" key={source.name}>
                  <DatabaseOutlined />
                  <div>
                    <strong>{source.name}</strong>
                    <span>{source.role}</span>
                  </div>
                  <Tag color={source.status === 'live' ? 'green' : source.status === 'partial' ? 'gold' : 'default'}>
                    {source.status}
                  </Tag>
                </div>
              ))}
              <Text type="secondary">{data.disclaimer}</Text>
            </Card>
          </div>
        </>
      )}

      <Drawer
        open={Boolean(selected)}
        onClose={() => setSelected(null)}
        width={560}
        title={selected ? `${selected.name} · ${selected.symbol}` : '预警详情'}
      >
        {selected && (
          <div className="market-risk-drawer">
            <div className="market-risk-drawer-summary">
              <div>
                <Text type="secondary">{MARKET_LABELS[selected.market]}市值第 {selected.rank} 名</Text>
                <Title level={3}>{formatMarketCap(selected.market_cap, selected.currency)}</Title>
                <Text>{selected.sector} · 现价 {formatNumber(selected.price)}</Text>
              </div>
              <RiskTag level={selected.risk_level} score={selected.risk_score} />
            </div>

            <Title level={5}>五维风险拆解</Title>
            <div className="market-risk-dimensions">
              {(Object.keys(selected.dimensions) as RiskDimensionKey[]).map(key => (
                <div key={key}>
                  <div>
                    <Tooltip title={DIMENSION_META[key].description}>
                      <span>{DIMENSION_META[key].label}</span>
                    </Tooltip>
                    <strong>{selected.dimensions[key].toFixed(0)}</strong>
                  </div>
                  <Progress
                    percent={selected.dimensions[key]}
                    showInfo={false}
                    strokeColor={LEVEL_META[
                      selected.dimensions[key] >= 75 ? 'red'
                        : selected.dimensions[key] >= 55 ? 'orange'
                          : selected.dimensions[key] >= 35 ? 'yellow' : 'green'
                    ].hex}
                  />
                </div>
              ))}
            </div>

            <Title level={5}>本次触发原因</Title>
            <div className="market-risk-driver-list">
              {selected.drivers.map(driver => (
                <div key={driver}>
                  <ExclamationCircleOutlined />
                  <span>{driver}</span>
                </div>
              ))}
            </div>

            <Title level={5}>站内可追溯证据</Title>
            {selected.evidence.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无命中风险阈值的站内证据" />
            ) : (
              <div className="market-risk-evidence-list">
                {selected.evidence.map((evidence, index) => (
                  <Card key={`${evidence.title}-${index}`} size="small">
                    <Space>
                      <Tag color={evidence.severity === 'critical' ? 'red' : 'gold'}>
                        {evidence.severity === 'critical' ? '高风险' : '需关注'}
                      </Tag>
                      <Text type="secondary">{evidence.source}</Text>
                    </Space>
                    <Title level={5}>{evidence.title}</Title>
                    {evidence.detail && <Paragraph ellipsis={{ rows: 3, expandable: true }}>{evidence.detail}</Paragraph>}
                    <Space>
                      <Text type="secondary">{formatTime(evidence.published_at)}</Text>
                      {evidence.url && (
                        <a href={evidence.url} target="_blank" rel="noreferrer" onClick={event => event.stopPropagation()}>
                          查看原文 <LinkOutlined />
                        </a>
                      )}
                    </Space>
                  </Card>
                ))}
              </div>
            )}

            <Alert
              type="info"
              showIcon
              message={LEVEL_META[selected.risk_level].description}
              description="预警是复核起点，不代表未来必然下跌。建议先核验原始证据、数据时点和公司公告。"
            />
          </div>
        )}
      </Drawer>
      </CenterShell>
    </ConfigProvider>
  );
};

export default MarketRiskRadar;
