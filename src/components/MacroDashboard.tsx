import React, { useCallback, useMemo, useState, useEffect } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Tag,
  Tabs,
  Tooltip,
  Typography,
  App as AntdApp,
} from 'antd';
import {
  ArrowUpOutlined,
  ArrowDownOutlined,
  InfoCircleOutlined,
  ThunderboltOutlined,
  DollarOutlined,
  LineChartOutlined,
  GlobalOutlined,
  FundOutlined,
  BulbOutlined,
  BankOutlined,
} from '@ant-design/icons';
import {
  fetchMarketDashboard,
  fetchAshareDashboard,
  analyzeGlobalDashboard,
  analyzeAshareDashboard,
  MarketDashboardResponse,
  DashboardCategory,
  MarketIndicator,
  DashboardAnalysisResponse,
} from '../services/marketDashboardService';
import { useRegisterModuleContext } from '../contexts/ModuleContext';
import CenterShell from './common/CenterShell';
import ShareButton from './common/ShareButton';
import './MacroDashboard.css';

const { Title, Text, Paragraph } = Typography;

const CATEGORY_ICONS: Record<string, React.ReactNode> = {
  volatility: <ThunderboltOutlined />,
  interest_rate: <BankOutlined />,
  currency_commodity: <DollarOutlined />,
  sentiment_risk: <LineChartOutlined />,
  macro: <FundOutlined />,
  breadth: <GlobalOutlined />,
  a_share: <GlobalOutlined />,
};

const SIGNAL_CONFIG: Record<string, { color: string; bg: string; border: string; label: string }> = {
  strong_bullish: { color: '#237804', bg: 'rgba(16,185,129,0.08)', border: 'rgba(16,185,129,0.35)', label: '强烈看多' },
  bullish: { color: '#389e0d', bg: 'rgba(16,185,129,0.08)', border: 'rgba(16,185,129,0.18)', label: '看多' },
  neutral: { color: '#8c8c8c', bg: 'var(--surface-muted)', border: 'var(--border)', label: '中性' },
  bearish: { color: '#cf1322', bg: 'rgba(249,115,22,0.08)', border: 'rgba(239,68,68,0.2)', label: '看空' },
  strong_bearish: { color: '#a8071a', bg: 'rgba(239,68,68,0.08)', border: 'rgba(239,68,68,0.35)', label: '强烈看空' },
};

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const fmtNum = (v: number): string => numberFormat.format(v);

const _isUnavailable = (indicator: MarketIndicator): boolean => indicator.status.startsWith('数据');

const _fmtValue = (indicator: MarketIndicator): string => {
  if (_isUnavailable(indicator)) return '暂无';
  const v = indicator.value;
  const u = indicator.unit;
  if (u === '$') return `$${fmtNum(v)}`;
  if (u === '%') return `${v.toFixed(2)}%`;
  if (u === 'bp') return `${v.toFixed(0)}bp`;
  return fmtNum(v);
};

const _fmtChange = (indicator: MarketIndicator): string | null => {
  if (indicator.change === null || indicator.change === undefined) return null;
  const prefix = indicator.change >= 0 ? '+' : '';
  if (indicator.change_pct !== null && indicator.change_pct !== undefined) {
    return `${prefix}${fmtNum(indicator.change_pct)}%`;
  }
  return `${prefix}${fmtNum(indicator.change)}`;
};

const _isPositive = (indicator: MarketIndicator): boolean => {
  if (indicator.change_pct !== null && indicator.change_pct !== undefined) {
    return indicator.change_pct >= 0;
  }
  return (indicator.change ?? 0) >= 0;
};

const SCORE_COLOR = (score: number): string => {
  if (score >= 70) return '#52c41a';
  if (score >= 55) return '#bae637';
  if (score >= 45) return '#faad14';
  if (score >= 30) return '#ff7a45';
  return '#ff4d4f';
};

const IndicatorCard: React.FC<{ indicator: MarketIndicator }> = ({ indicator }) => {
  const sig = SIGNAL_CONFIG[indicator.signal] || SIGNAL_CONFIG.neutral;
  const changeText = _fmtChange(indicator);
  const isUp = _isPositive(indicator);
  const unavailable = _isUnavailable(indicator);
  const readingText = unavailable ? indicator.status : indicator.current_reading;

  return (
    <Tooltip
      title={
        <div style={{ maxWidth: 300 }}>
          <Paragraph style={{ marginBottom: 4, color: '#fff' }}>{indicator.description}</Paragraph>
          <Paragraph style={{ marginBottom: 4, color: 'rgba(255,255,255,0.75)', fontSize: 12 }}>
            {indicator.historical_context}
          </Paragraph>
          <Paragraph style={{ marginBottom: 0, color: sig.color, fontSize: 12, fontWeight: 600 }}>
            当前状态：{readingText}
          </Paragraph>
          <Paragraph style={{ marginBottom: 0, color: 'rgba(255,255,255,0.45)', fontSize: 11 }}>
            数据来源: {indicator.source}
          </Paragraph>
        </div>
      }
    >
      <Card
        size="small"
        hoverable
        className={`macro-dashboard-indicator macro-dashboard-indicator--${indicator.signal}`}
        style={{
          borderLeft: `3px solid ${sig.border}`,
          background: sig.bg,
        }}
      >
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 8 }}>
          <Text style={{ fontSize: 13, fontWeight: 500 }}>{indicator.name}</Text>
          <Tag
            color={
              unavailable
                ? 'default'
                : indicator.signal.includes('bullish')
                ? 'green'
                : indicator.signal.includes('bearish')
                ? 'red'
                : 'default'
            }
            style={{ fontSize: 11, margin: 0 }}
          >
            {unavailable ? '待更新' : sig.label}
          </Tag>
        </div>
        <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
          <Text style={{ fontSize: 24, fontWeight: 700, color: 'var(--text)' }}>
            {_fmtValue(indicator)}
          </Text>
          {changeText && (
            <Text
              style={{
                fontSize: 13,
                color: isUp ? '#52c41a' : '#ff4d4f',
              }}
            >
              {isUp ? <ArrowUpOutlined /> : <ArrowDownOutlined />} {changeText}
            </Text>
          )}
        </div>
        <div style={{ marginTop: 6 }}>
          <Text style={{ fontSize: 12, color: sig.color, lineHeight: 1.4 }}>
            {readingText}
          </Text>
        </div>
        <div style={{ marginTop: 4 }}>
          <Text style={{ fontSize: 11, color: 'var(--text-muted)' }}>{indicator.source}</Text>
          <Tooltip title={indicator.description}>
            <InfoCircleOutlined style={{ marginLeft: 4, color: 'var(--text-muted)', fontSize: 11 }} />
          </Tooltip>
        </div>
      </Card>
    </Tooltip>
  );
};

const CategorySection: React.FC<{ category: DashboardCategory }> = ({ category }) => {
  return (
    <div className="macro-dashboard-category">
      <div className="macro-dashboard-category-head" style={{ display: 'flex', alignItems: 'center', marginBottom: 12, gap: 8 }}>
        <span style={{ fontSize: 16, color: '#1677ff' }}>{CATEGORY_ICONS[category.key]}</span>
        <Title level={5} style={{ margin: 0, fontWeight: 600 }}>{category.name}</Title>
      </div>
      <Row gutter={[12, 12]}>
        {category.indicators.map((ind) => (
          <Col xs={24} sm={12} md={8} lg={6} key={ind.key}>
            <IndicatorCard indicator={ind} />
          </Col>
        ))}
      </Row>
    </div>
  );
};

function MacroDashboard() {
  const { message } = AntdApp.useApp();
  const [activeTab, setActiveTab] = useState<string>('global');
  const [globalData, setGlobalData] = useState<MarketDashboardResponse | null>(null);
  const [ashareData, setAshareData] = useState<MarketDashboardResponse | null>(null);
  const [loadingGlobal, setLoadingGlobal] = useState(false);
  const [loadingAshare, setLoadingAshare] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [globalAnalysis, setGlobalAnalysis] = useState<DashboardAnalysisResponse | null>(null);
  const [ashareAnalysis, setAshareAnalysis] = useState<DashboardAnalysisResponse | null>(null);
  const [analyzingGlobal, setAnalyzingGlobal] = useState(false);
  const [analyzingAshare, setAnalyzingAshare] = useState(false);

  const loadGlobal = useCallback(async () => {
    setLoadingGlobal(true);
    setError(null);
    try {
      const data = await fetchMarketDashboard();
      setGlobalData(data);
    } catch (err: any) {
      setError(err.message || '加载失败');
    } finally {
      setLoadingGlobal(false);
    }
  }, []);

  const loadAshare = useCallback(async () => {
    setLoadingAshare(true);
    setError(null);
    try {
      const data = await fetchAshareDashboard();
      setAshareData(data);
    } catch (err: any) {
      setError(err.message || '加载失败');
    } finally {
      setLoadingAshare(false);
    }
  }, []);

  const runGlobalAnalysis = useCallback(async () => {
    setAnalyzingGlobal(true);
    try {
      const result = await analyzeGlobalDashboard();
      setGlobalAnalysis(result);
    } catch (err: any) {
      message.error(err.message || 'AI分析失败');
    } finally {
      setAnalyzingGlobal(false);
    }
  }, [message]);

  const runAshareAnalysis = useCallback(async () => {
    setAnalyzingAshare(true);
    try {
      const result = await analyzeAshareDashboard();
      setAshareAnalysis(result);
    } catch (err: any) {
      message.error(err.message || 'AI分析失败');
    } finally {
      setAnalyzingAshare(false);
    }
  }, [message]);

  useEffect(() => {
    loadGlobal();
    loadAshare();
  }, [loadGlobal, loadAshare]);

  const activeData = activeTab === 'global' ? globalData : ashareData;
  const activeAnalysis = activeTab === 'global' ? globalAnalysis : ashareAnalysis;

  const moduleContext = useMemo(() => {
    if (!activeData) return null;
    const indicatorsSummary: Record<string, unknown> = {};
    for (const cat of activeData.categories) {
      for (const ind of cat.indicators) {
        if (ind.status && !ind.status.startsWith('数据')) {
          indicatorsSummary[ind.name] = {
            value: ind.value,
            unit: ind.unit,
            signal: ind.signal,
            status: ind.status,
          };
        }
      }
    }
    return {
      module: activeTab === 'global' ? 'market-dashboard' : 'market-dashboard-ashare',
      title: activeTab === 'global' ? '全球宏观指标' : 'A股大盘指标',
      summary: `综合信号：${activeData.overall_signal}（评分${activeData.overall_score}）。${activeData.overall_summary}。共${activeData.total_indicators}项指标，${activeData.available_indicators}项可用。`,
      data: indicatorsSummary,
      lastUpdated: Date.now(),
    };
  }, [activeData, activeTab]);

  useRegisterModuleContext(moduleContext);

  const renderAnalysisPanel = useCallback((
    analysis: DashboardAnalysisResponse | null,
    analyzing: boolean,
    onAnalyze: () => void,
    label: string,
  ) => {
    if (!analysis) {
      return (
        <Card size="small" className="macro-dashboard-analysis-card" style={{ marginBottom: 24 }}>
          <div style={{ textAlign: 'center', padding: '24px 0' }}>
            <BulbOutlined style={{ fontSize: 32, color: '#1677ff', marginBottom: 12 }} />
            <br />
            <Text type="secondary">AI 综合分析尚未生成</Text>
            <br />
            <Button
              type="primary"
              loading={analyzing}
              onClick={onAnalyze}
              style={{ marginTop: 12 }}
            >
              {analyzing ? 'Agent 分析中...' : `启动 ${label} AI 分析`}
            </Button>
          </div>
        </Card>
      );
    }

    const confidencePct = Math.round(analysis.confidence * 100);
    const confidenceColor = confidencePct >= 70 ? '#52c41a' : confidencePct >= 40 ? '#faad14' : '#ff4d4f';

    return (
      <Card
        size="small"
        className="macro-dashboard-analysis-card"
        style={{ marginBottom: 24 }}
        title={
          <div className="macro-dashboard-analysis-title" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span>
              <BulbOutlined style={{ color: '#1677ff', marginRight: 8 }} />
              {analysis.title}
            </span>
            <Space>
              <Tag color={confidenceColor === '#52c41a' ? 'green' : confidenceColor === '#faad14' ? 'orange' : 'red'}>
                置信度 {confidencePct}%
              </Tag>
              <Button size="small" onClick={onAnalyze} loading={analyzing}>
                重新分析
              </Button>
            </Space>
          </div>
        }
        extra={null}
      >
        <Paragraph style={{ fontSize: 15, fontWeight: 500, color: 'var(--text)', marginBottom: 16, lineHeight: 1.6 }}>
          {analysis.summary}
        </Paragraph>
        <div style={{ marginBottom: 16 }}>
          <ShareButton
            modalTitle="分享大盘研判"
            target={() => ({
              title: activeTab === 'global' ? '全球宏观分析' : 'A股大盘分析',
              summary: analysis.summary,
              byline: '由 DeepFocus 投研工作台生成',
            })}
          />
        </div>

        {analysis.key_points.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text strong style={{ fontSize: 13 }}>关键发现</Text>
            <ul style={{ margin: '8px 0', paddingLeft: 20 }}>
              {analysis.key_points.map((kp, i) => (
                <li key={i} style={{ marginBottom: 4, fontSize: 13, lineHeight: 1.5 }}>{kp}</li>
              ))}
            </ul>
          </div>
        )}

        {analysis.signals.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text strong style={{ fontSize: 13 }}>市场信号</Text>
            <div style={{ marginTop: 6 }}>
              {analysis.signals.map((s, i) => (
                <Tag key={i} color="blue" style={{ marginBottom: 4 }}>{s}</Tag>
              ))}
            </div>
          </div>
        )}

        {analysis.risks.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text strong style={{ fontSize: 13 }}>风险提示</Text>
            <div style={{ marginTop: 6 }}>
              {analysis.risks.map((r, i) => (
                <Tag key={i} color="red" style={{ marginBottom: 4 }}>{r}</Tag>
              ))}
            </div>
          </div>
        )}

        {analysis.actions.length > 0 && (
          <div style={{ marginBottom: 12 }}>
            <Text strong style={{ fontSize: 13 }}>投资行动建议</Text>
            <div style={{ marginTop: 6 }}>
              {analysis.actions.map((a, i) => (
                <Tag key={i} color="green" style={{ marginBottom: 4 }}>{a}</Tag>
              ))}
            </div>
          </div>
        )}

        {analysis.sources.length > 0 && (
          <Text type="secondary" style={{ fontSize: 12 }}>
            数据依据：{analysis.sources.join('、')}
          </Text>
        )}
      </Card>
    );
  }, []);

  const renderDashboard = useCallback((data: MarketDashboardResponse, subtitle: string) => {
    const scoreColor = SCORE_COLOR(data.overall_score);

    return (
      <div className="macro-dashboard-content">
        <Card
          className="macro-dashboard-score-card"
          style={{
            marginBottom: 24,
            textAlign: 'center',
            border: `2px solid ${scoreColor}`,
            borderRadius: 12,
            background: `linear-gradient(135deg, rgba(${scoreColor === '#52c41a' ? '82,196,26' : scoreColor === '#faad14' ? '250,173,20' : '255,77,79'}, 0.06) 0%, var(--surface) 100%)`,
          }}
        >
          <Row align="middle" justify="center" gutter={24}>
            <Col>
              <div style={{ position: 'relative', width: 120, height: 120, margin: '0 auto' }}>
                <Progress
                  type="circle"
                  percent={data.overall_score}
                  format={(pct) => (
                    <div>
                      <div style={{ fontSize: 28, fontWeight: 800, color: scoreColor }}>{pct}</div>
                      <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>综合评分</div>
                    </div>
                  )}
                  strokeColor={scoreColor}
                  trailColor="#f0f0f0"
                  size={120}
                />
              </div>
            </Col>
            <Col>
              <div style={{ textAlign: 'left' }}>
                <Tag
                  color={
                    // 按客观评分着色（label 已去方向化，不再含"看多/看空"字样）
                    data.overall_score > 55
                      ? 'green'
                      : data.overall_score < 45
                      ? 'red'
                      : 'orange'
                  }
                  style={{ fontSize: 16, padding: '4px 16px', marginBottom: 12 }}
                >
                  {data.overall_signal}
                </Tag>
                <Paragraph style={{ maxWidth: 500, color: 'var(--text-muted)', fontSize: 14, lineHeight: 1.6 }}>
                  {data.overall_summary}
                </Paragraph>
                <div style={{ marginBottom: 12 }}>
                  <ShareButton
                    modalTitle="分享大盘研判"
                    target={() => ({
                      title: '大盘综合研判',
                      summary: data.overall_summary,
                      byline: '由 DeepFocus 投研工作台生成',
                    })}
                  />
                </div>
                <Text type="secondary" style={{ fontSize: 12 }}>
                  {data.available_indicators}/{data.total_indicators} 项指标可用 · 每2-5分钟自动刷新
                </Text>
                <div className="macro-dashboard-scope-note">{subtitle}</div>
              </div>
            </Col>
          </Row>
        </Card>

        {data.categories.map((category) => (
          <CategorySection key={category.key} category={category} />
        ))}
      </div>
    );
  }, []);

  const tabItems = [
    {
      key: 'global',
      label: '全球宏观',
      children: loadingGlobal ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" tip="加载全球指标..." /></div>
      ) : globalData ? (
        <>
          {renderAnalysisPanel(globalAnalysis, analyzingGlobal, runGlobalAnalysis, '全球宏观')}
          {renderDashboard(globalData, '涵盖波动率、利率、汇率、商品、宏观经济、市场广度等华尔街经典指标体系')}
        </>
      ) : (
        <Alert type="error" message="加载失败" description={error} showIcon style={{ margin: 40 }} />
      ),
    },
    {
      key: 'ashare',
      label: 'A股大盘',
      children: loadingAshare ? (
        <div style={{ textAlign: 'center', padding: 60 }}><Spin size="large" tip="加载A股指标..." /></div>
      ) : ashareData ? (
        <>
          {renderAnalysisPanel(ashareAnalysis, analyzingAshare, runAshareAnalysis, 'A股大盘')}
          {renderDashboard(ashareData, '上证/深证/创业板、成交额、北向资金、融资融券、涨停跌停等A股经典指标')}
        </>
      ) : (
        <Alert type="error" message="加载失败" description={error} showIcon style={{ margin: 40 }} />
      ),
    },
  ];

  return (
    <CenterShell
      className="macro-dashboard-shell"
      title="宏观雷达"
      subtitle="涵盖华尔街经典指标体系与A股本土核心指标，多维度评估市场状态"
    >
      <Tabs
        activeKey={activeTab}
        onChange={setActiveTab}
        items={tabItems}
        size="large"
        tabBarExtraContent={
          <Typography.Link onClick={() => { loadGlobal(); loadAshare(); }}>
            刷新全部
          </Typography.Link>
        }
      />
    </CenterShell>
  );
}

export default MacroDashboard;
