import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Typography,
  App as AntdApp
} from 'antd';
import {
  AimOutlined,
  ApiOutlined,
  BarChartOutlined,
  FieldTimeOutlined,
  LineChartOutlined,
  PlusOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis
} from 'recharts';
import { AppState } from '../types';
import { MarketSymbolCandidate, searchMarketSymbols } from '../services/marketDataService';
import {
  analyzeOptionsTrend,
  OptionsAiAnalysisResponse,
  getOptionsSignals,
  OptionsExpirationSignal,
  OptionsKeyStrike,
  OptionsSignal,
  OptionsSignalResponse,
  OptionsSourceStatus,
  OptionsTrendLabel,
  OptionsUnusualFlow
} from '../services/optionsSignalService';

const { Paragraph, Text, Title } = Typography;
const { Search } = Input;

interface OptionsSignalCenterProps {
  appState: AppState;
}

type SymbolMode = 'watchlist' | 'custom' | 'search';

const directionColor: Record<OptionsSignal['direction'], string> = {
  偏多: 'green',
  中性: 'blue',
  偏空: 'red',
  不可判定: 'default'
};

const sourceStatusColor: Record<OptionsSourceStatus['status'], string> = {
  ready: 'green',
  fallback: 'blue',
  blocked: 'orange',
  unavailable: 'default'
};

const sourceStatusLabel: Record<OptionsSourceStatus['status'], string> = {
  ready: '就绪',
  fallback: '兜底',
  blocked: '待配置',
  unavailable: '不可用'
};

const severityColor: Record<OptionsUnusualFlow['severity'], string> = {
  高: 'red',
  中: 'orange',
  低: 'blue'
};

const trendColor: Record<OptionsTrendLabel, string> = {
  看涨: 'green',
  震荡偏强: 'cyan',
  震荡: 'blue',
  震荡偏弱: 'orange',
  看跌: 'red',
  不可判定: 'default'
};

const numberFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const priceFormat = new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 });
const moneyFormat = new Intl.NumberFormat('en-US', {
  maximumFractionDigits: 1,
  notation: 'compact',
  compactDisplay: 'short'
});

const formatNumber = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  if (Math.abs(value) >= 1_000_000) {
    return `${(value / 1_000_000).toFixed(1)}M`;
  }
  if (Math.abs(value) >= 1_000) {
    return `${(value / 1_000).toFixed(1)}K`;
  }
  return numberFormat.format(value);
};

const formatPrice = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  return priceFormat.format(value);
};

const formatMoney = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  return `$${moneyFormat.format(value)}`;
};

const formatRatio = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  return value.toFixed(2);
};

const formatPercent = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  return `${(value * 100).toFixed(1)}%`;
};

const formatConfidence = (value?: number | null): string => {
  if (typeof value !== 'number' || !Number.isFinite(value)) {
    return '--';
  }
  return `${Math.round(value * 100)}%`;
};

const formatDate = (date: string): string => {
  const parsed = new Date(`${date}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) {
    return date;
  }
  return parsed.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' });
};

const uniqueSymbols = (symbols: string[]): string[] => (
  Array.from(new Set(symbols.map(symbol => symbol.trim().toUpperCase()).filter(Boolean))).slice(0, 12)
);

const OptionsSignalCenter: React.FC<OptionsSignalCenterProps> = ({ appState }) => {
  const { message } = AntdApp.useApp();
  const usWatchlist = useMemo(() => {
    return appState.stocks
      .filter(stock => (stock.market || 'US') === 'US')
      .map(stock => stock.symbol)
      .slice(0, 8);
  }, [appState.stocks]);
  const defaultSymbols = useMemo(() => uniqueSymbols(usWatchlist.length ? usWatchlist : ['TSLA', 'NVDA', 'AAPL']), [usWatchlist]);
  const [symbolMode, setSymbolMode] = useState<SymbolMode>('watchlist');
  const [customSymbols, setCustomSymbols] = useState(defaultSymbols.join(', '));
  const [searchText, setSearchText] = useState('');
  const [searching, setSearching] = useState(false);
  const [searchCandidates, setSearchCandidates] = useState<MarketSymbolCandidate[]>([]);
  const [searchSymbols, setSearchSymbols] = useState<string[]>([]);
  const [horizonDays, setHorizonDays] = useState(45);
  const [maxExpirations, setMaxExpirations] = useState(3);
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(defaultSymbols[0] || null);
  const [result, setResult] = useState<OptionsSignalResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [aiAnalysis, setAiAnalysis] = useState<OptionsAiAnalysisResponse | null>(null);
  const [aiLoading, setAiLoading] = useState(false);

  useEffect(() => {
    setCustomSymbols(defaultSymbols.join(', '));
    setSelectedSymbol(prev => prev || defaultSymbols[0] || null);
  }, [defaultSymbols]);

  const symbols = useMemo(() => {
    if (symbolMode === 'watchlist') {
      return defaultSymbols;
    }
    if (symbolMode === 'search') {
      return searchSymbols;
    }
    return uniqueSymbols(customSymbols.split(/[,\s，、]+/));
  }, [customSymbols, defaultSymbols, searchSymbols, symbolMode]);

  const loadSignals = useCallback(async (overrideSymbols?: string[]) => {
    const targetSymbols = overrideSymbols || symbols;
    if (targetSymbols.length === 0) {
      message.warning('请输入至少一个美股或 ETF 代码');
      return;
    }
    setLoading(true);
    try {
      const data = await getOptionsSignals(targetSymbols, horizonDays, maxExpirations);
      setResult(data);
      setAiAnalysis(null);
      setSelectedSymbol(prev => (
        prev && data.signals.some(signal => signal.symbol === prev)
          ? prev
          : data.signals[0]?.symbol || null
      ));
      message.success(`期权快照已更新：${data.signals.length} 个标的`);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认后端 API 已启动';
      message.error(`期权模块加载失败：${detail}`);
    } finally {
      setLoading(false);
    }
  }, [horizonDays, maxExpirations, message, symbols]);

  useEffect(() => {
    void loadSignals();
  }, []);

  const handleSymbolSearch = async (value = searchText) => {
    const keyword = value.trim();
    if (!keyword) {
      message.warning('请输入美股或 ETF 代码/名称');
      return;
    }

    setSymbolMode('search');
    setSearching(true);
    try {
      const response = await searchMarketSymbols(keyword, 'US');
      const candidates = response.candidates.filter(candidate => candidate.market === 'US');
      setSearchCandidates(candidates);
      if (candidates.length === 0) {
        const directSymbol = keyword.toUpperCase();
        const nextSymbols = uniqueSymbols([directSymbol, ...searchSymbols]);
        setSearchSymbols(nextSymbols);
        setSelectedSymbol(directSymbol);
        void loadSignals(nextSymbols);
        message.info(`未命中搜索结果，已按代码 ${directSymbol} 尝试显示`);
      }
      if (response.warnings.length > 0) {
        console.warn('Options symbol search warnings:', response.warnings);
      }
    } catch (error: any) {
      message.error(error?.message || '标的搜索失败');
    } finally {
      setSearching(false);
    }
  };

  const handleShowCandidate = (candidate: MarketSymbolCandidate) => {
    const nextSymbols = uniqueSymbols([candidate.symbol, ...searchSymbols]);
    setSymbolMode('search');
    setSearchSymbols(nextSymbols);
    setSelectedSymbol(candidate.symbol);
    setSearchText(candidate.symbol);
    void loadSignals(nextSymbols);
  };

  const handleRemoveSearchSymbol = (symbol: string) => {
    const nextSymbols = searchSymbols.filter(item => item !== symbol);
    setSearchSymbols(nextSymbols);
    if (selectedSymbol === symbol) {
      setSelectedSymbol(nextSymbols[0] || null);
    }
    if (symbolMode === 'search') {
      if (nextSymbols.length > 0) {
        void loadSignals(nextSymbols);
      } else {
        setResult(null);
        setAiAnalysis(null);
      }
    }
  };

  const activeSignal = useMemo(() => {
    if (!result?.signals.length) {
      return null;
    }
    return result.signals.find(signal => signal.symbol === selectedSymbol) || result.signals[0];
  }, [result, selectedSymbol]);

  const visibleAiAnalysis = useMemo(() => {
    if (!aiAnalysis || !activeSignal || aiAnalysis.symbol !== activeSignal.symbol) {
      return null;
    }
    return aiAnalysis;
  }, [activeSignal, aiAnalysis]);

  const handleAiAnalysis = useCallback(async () => {
    if (!activeSignal) {
      message.warning('请先生成期权快照');
      return;
    }
    setAiLoading(true);
    try {
      const data = await analyzeOptionsTrend(activeSignal, horizonDays);
      setAiAnalysis(data);
      message.success(`${activeSignal.symbol} AI走势研判已生成`);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认后端 API 已启动，且模型配置可用';
      message.error(`AI分析失败：${detail}`);
    } finally {
      setAiLoading(false);
    }
  }, [activeSignal, horizonDays, message]);

  const summaryStats = useMemo(() => {
    const signals = result?.signals || [];
    return {
      total: signals.length,
      bullish: signals.filter(signal => signal.direction === '偏多').length,
      bearish: signals.filter(signal => signal.direction === '偏空').length,
      unusualCount: signals.reduce((sum, signal) => sum + (signal.unusual_flow_count || 0), 0),
      unusualPremium: signals.reduce((sum, signal) => sum + (signal.unusual_premium_notional || 0), 0),
      averageQuality: signals.length
        ? Math.round(signals.reduce((sum, signal) => sum + signal.data_quality, 0) / signals.length)
        : 0
    };
  }, [result]);

  const strikeChartData = useMemo(() => {
    if (!activeSignal) {
      return [];
    }
    return activeSignal.key_strikes.slice(0, 8).map(item => ({
      name: `${item.side === 'call' ? 'C' : 'P'} ${formatPrice(item.strike)}`,
      value: Math.round(item.value),
      metric: item.metric
    }));
  }, [activeSignal]);

  const signalColumns = [
    {
      title: '标的',
      key: 'symbol',
      render: (_: unknown, item: OptionsSignal) => (
        <button
          type="button"
          className="options-symbol-button"
          onClick={() => setSelectedSymbol(item.symbol)}
        >
          <Text strong>{item.symbol}</Text>
          <Text type="secondary">{item.provider_name}</Text>
        </button>
      )
    },
    {
      title: '投票',
      key: 'direction',
      render: (_: unknown, item: OptionsSignal) => (
        <Space direction="vertical" size={2}>
          <Tag color={directionColor[item.direction]}>{item.direction}</Tag>
          <Text type="secondary">{item.conviction}置信</Text>
        </Space>
      )
    },
    {
      title: '方向分',
      dataIndex: 'score',
      key: 'score',
      sorter: (a: OptionsSignal, b: OptionsSignal) => a.score - b.score,
      render: (score: number) => <Progress percent={score} size="small" />
    },
    {
      title: '异常大单',
      key: 'unusual',
      sorter: (a: OptionsSignal, b: OptionsSignal) => (a.unusual_flow_count || 0) - (b.unusual_flow_count || 0),
      render: (_: unknown, item: OptionsSignal) => {
        const topFlow = item.unusual_flows?.[0];
        const visibleFlowCount = item.unusual_flows?.length || 0;
        return (
          <Space direction="vertical" size={0}>
            <Space size={6}>
              <Tag color={item.unusual_flow_count ? 'red' : 'default'}>{item.unusual_flow_count || 0} 条</Tag>
              {topFlow && <Tag color={topFlow.side === 'call' ? 'green' : 'red'}>{topFlow.side === 'call' ? 'Call' : 'Put'}</Tag>}
            </Space>
            <Text type="secondary">{formatMoney(item.unusual_premium_notional)}</Text>
            {item.unusual_flow_count > visibleFlowCount && (
              <Text type="secondary">展示 Top {visibleFlowCount}</Text>
            )}
          </Space>
        );
      }
    },
    {
      title: 'PCR',
      key: 'pcr',
      render: (_: unknown, item: OptionsSignal) => (
        <Space direction="vertical" size={0}>
          <Text>成交 {formatRatio(item.put_call_volume_ratio)}</Text>
          <Text type="secondary">OI {formatRatio(item.put_call_open_interest_ratio)}</Text>
        </Space>
      )
    },
    {
      title: '关键价位',
      key: 'walls',
      render: (_: unknown, item: OptionsSignal) => (
        <Space direction="vertical" size={0}>
          <Text>Call {formatPrice(item.call_wall)}</Text>
          <Text>Put {formatPrice(item.put_wall)}</Text>
          <Text type="secondary">Pain {formatPrice(item.max_pain)}</Text>
        </Space>
      )
    },
    {
      title: '预期波动',
      key: 'move',
      render: (_: unknown, item: OptionsSignal) => (
        <Space direction="vertical" size={0}>
          <Text>{formatPercent(item.expected_move_pct)}</Text>
          <Text type="secondary">IV {formatPercent(item.avg_iv)}</Text>
        </Space>
      )
    },
    {
      title: '质量',
      dataIndex: 'data_quality',
      key: 'quality',
      sorter: (a: OptionsSignal, b: OptionsSignal) => a.data_quality - b.data_quality,
      render: (quality: number) => <Progress percent={quality} size="small" status={quality < 45 ? 'exception' : 'normal'} />
    }
  ];

  const expirationColumns = [
    {
      title: '到期',
      key: 'expiration',
      render: (_: unknown, item: OptionsExpirationSignal) => (
        <Space direction="vertical" size={0}>
          <Text strong>{formatDate(item.expiration)}</Text>
          <Text type="secondary">{item.dte ?? '--'} DTE</Text>
        </Space>
      )
    },
    {
      title: '合约数',
      dataIndex: 'contract_count',
      key: 'contract_count'
    },
    {
      title: '成交 PCR',
      key: 'pcr_volume',
      render: (_: unknown, item: OptionsExpirationSignal) => <Text>{formatRatio(item.pcr_volume)}</Text>
    },
    {
      title: 'OI PCR',
      key: 'pcr_oi',
      render: (_: unknown, item: OptionsExpirationSignal) => <Text>{formatRatio(item.pcr_open_interest)}</Text>
    },
    {
      title: 'ATM 跨式',
      key: 'straddle',
      render: (_: unknown, item: OptionsExpirationSignal) => (
        <Space direction="vertical" size={0}>
          <Text>{formatPrice(item.atm_straddle_mid)}</Text>
          <Text type="secondary">{formatPercent(item.expected_move_pct)}</Text>
        </Space>
      )
    },
    {
      title: 'ATM IV',
      key: 'iv',
      render: (_: unknown, item: OptionsExpirationSignal) => <Text>{formatPercent(item.atm_iv)}</Text>
    }
  ];

  const strikeColumns = [
    {
      title: '类型',
      key: 'metric',
      render: (_: unknown, item: OptionsKeyStrike) => (
        <Space>
          <Tag color={item.side === 'call' ? 'green' : 'red'}>{item.side === 'call' ? 'Call' : 'Put'}</Tag>
          <Text>{item.metric}</Text>
        </Space>
      )
    },
    {
      title: '行权价',
      dataIndex: 'strike',
      key: 'strike',
      render: (strike: number) => <Text strong>{formatPrice(strike)}</Text>
    },
    {
      title: '距离现价',
      key: 'distance',
      render: (_: unknown, item: OptionsKeyStrike) => <Text>{formatPercent(item.distance_pct)}</Text>
    },
    {
      title: '数值',
      dataIndex: 'value',
      key: 'value',
      sorter: (a: OptionsKeyStrike, b: OptionsKeyStrike) => a.value - b.value,
      render: (value: number) => <Text>{formatNumber(value)}</Text>
    },
    {
      title: '解读',
      dataIndex: 'interpretation',
      key: 'interpretation'
    }
  ];

  const unusualFlowColumns = [
    {
      title: '合约',
      key: 'contract',
      render: (_: unknown, item: OptionsUnusualFlow) => (
        <Space direction="vertical" size={0}>
          <Space wrap>
            <Tag color={item.side === 'call' ? 'green' : 'red'}>{item.side === 'call' ? 'Call' : 'Put'}</Tag>
            <Text strong>{formatPrice(item.strike)}</Text>
            <Tag color={severityColor[item.severity]}>{item.severity}异常</Tag>
          </Space>
          <Text type="secondary">{formatDate(item.expiration)} · {item.dte ?? '--'} DTE</Text>
        </Space>
      )
    },
    {
      title: '成交 / OI',
      key: 'volume',
      sorter: (a: OptionsUnusualFlow, b: OptionsUnusualFlow) => a.volume - b.volume,
      render: (_: unknown, item: OptionsUnusualFlow) => (
        <Space direction="vertical" size={0}>
          <Text>{formatNumber(item.volume)}</Text>
          <Text type="secondary">OI {formatNumber(item.open_interest)} · {formatRatio(item.volume_open_interest_ratio)}x</Text>
        </Space>
      )
    },
    {
      title: '权利金',
      key: 'premium',
      sorter: (a: OptionsUnusualFlow, b: OptionsUnusualFlow) => (a.premium_notional || 0) - (b.premium_notional || 0),
      render: (_: unknown, item: OptionsUnusualFlow) => (
        <Space direction="vertical" size={0}>
          <Text strong>{formatMoney(item.premium_notional)}</Text>
          <Text type="secondary">mark {formatPrice(item.mark_price)}</Text>
        </Space>
      )
    },
    {
      title: '距离现价',
      key: 'distance',
      render: (_: unknown, item: OptionsUnusualFlow) => <Text>{formatPercent(item.distance_pct)}</Text>
    },
    {
      title: '异常分',
      dataIndex: 'score',
      key: 'score',
      sorter: (a: OptionsUnusualFlow, b: OptionsUnusualFlow) => a.score - b.score,
      render: (score: number) => <Progress percent={score} size="small" status={score >= 75 ? 'exception' : 'normal'} />
    },
    {
      title: '捕捉理由',
      key: 'reason',
      render: (_: unknown, item: OptionsUnusualFlow) => (
        <Space direction="vertical" size={2}>
          <Text>{item.reason}</Text>
          <Text type="secondary">{item.interpretation}</Text>
        </Space>
      )
    }
  ];

  return (
    <div className="options-signal-page">
      <div className="options-signal-toolbar">
        <div>
          <Title level={3}>期权雷达</Title>
          <Paragraph type="secondary">
            用期权链快照跟踪异常大单候选、Put/Call、关键 OI 墙、Max Pain、跨式预期波动和 IV 偏斜。
          </Paragraph>
        </div>
        <Button
          type="primary"
          icon={<ReloadOutlined />}
          loading={loading}
          onClick={() => void loadSignals()}
        >
          刷新快照
        </Button>
      </div>

      <Row gutter={[14, 14]} className="options-control-row">
        <Col xs={24} lg={9}>
          <Card>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Text strong>标的池</Text>
              <Segmented
                value={symbolMode}
                onChange={value => setSymbolMode(value as SymbolMode)}
                options={[
                  { label: '自选美股', value: 'watchlist' },
                  { label: '手动输入', value: 'custom' },
                  { label: '搜索显示', value: 'search' }
                ]}
              />
              {symbolMode === 'search' ? (
                <Search
                  allowClear
                  value={searchText}
                  onChange={event => setSearchText(event.target.value)}
                  onSearch={handleSymbolSearch}
                  enterButton={<SearchOutlined />}
                  loading={searching}
                  placeholder="搜索代码/名称，如 META、AMD"
                />
              ) : (
                <Input
                  value={customSymbols}
                  disabled={symbolMode === 'watchlist'}
                  onChange={event => setCustomSymbols(event.target.value)}
                  placeholder="AAPL, TSLA, NVDA"
                />
              )}
              <Space wrap>
                {symbols.map(symbol => (
                  <Tag
                    key={symbol}
                    closable={symbolMode === 'search'}
                    onClose={() => handleRemoveSearchSymbol(symbol)}
                  >
                    {symbol}
                  </Tag>
                ))}
              </Space>
              {symbolMode === 'search' && searchCandidates.length > 0 && (
                <div className="options-search-results">
                  {searchCandidates.map(candidate => (
                    <div className="options-search-row" key={`${candidate.market}:${candidate.symbol}`}>
                      <Space direction="vertical" size={0}>
                        <Space wrap>
                          <Text strong>{candidate.symbol}</Text>
                          <Text>{candidate.name}</Text>
                          <Tag>{candidate.exchange || candidate.security_type || 'US'}</Tag>
                        </Space>
                        <Text type="secondary">{candidate.provider_name} · {candidate.quote_id || candidate.security_type || '期权标的'}</Text>
                      </Space>
                      <Button
                        type="primary"
                        size="small"
                        icon={<PlusOutlined />}
                        onClick={() => handleShowCandidate(candidate)}
                      >
                        显示期权
                      </Button>
                    </div>
                  ))}
                </div>
              )}
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={7}>
          <Card>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Text strong>期限窗口</Text>
              <Space wrap>
                <Select
                  value={horizonDays}
                  style={{ width: 132 }}
                  onChange={setHorizonDays}
                  options={[
                    { value: 21, label: '21 日' },
                    { value: 45, label: '45 日' },
                    { value: 90, label: '90 日' },
                    { value: 180, label: '180 日' }
                  ]}
                />
                <Select
                  value={maxExpirations}
                  style={{ width: 132 }}
                  onChange={setMaxExpirations}
                  options={[
                    { value: 1, label: '1 个到期' },
                    { value: 3, label: '3 个到期' },
                    { value: 6, label: '6 个到期' }
                  ]}
                />
              </Space>
              <Text type="secondary">当前请求：{symbols.length} 个标的 · {horizonDays} 日</Text>
            </Space>
          </Card>
        </Col>
        <Col xs={24} lg={8}>
          <Card>
            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              <Text strong>数据源</Text>
              <Space wrap>
                {(result?.sources || []).map(source => (
                  <Tag key={source.provider} color={sourceStatusColor[source.status]}>
                    {source.name} · {sourceStatusLabel[source.status]}
                  </Tag>
                ))}
              </Space>
              <Text type="secondary">{activeSignal?.delay_note || result?.disclaimer || '等待期权链响应'}</Text>
            </Space>
          </Card>
        </Col>
      </Row>

      {result?.warnings.length ? (
        <Alert
          className="options-warning"
          type="warning"
          showIcon
          message={result.warnings.slice(0, 3).join('；')}
        />
      ) : null}

      {result ? (
        <>
          <Row gutter={[14, 14]} className="options-stat-row">
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="标的数" value={summaryStats.total} prefix={<ApiOutlined />} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="偏多" value={summaryStats.bullish} prefix={<ThunderboltOutlined />} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="偏空" value={summaryStats.bearish} prefix={<SafetyCertificateOutlined />} />
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="异常大单" value={summaryStats.unusualCount} suffix="条" prefix={<ThunderboltOutlined />} />
                <Text type="secondary">权利金 {formatMoney(summaryStats.unusualPremium)}</Text>
              </Card>
            </Col>
            <Col xs={12} md={6}>
              <Card>
                <Statistic title="平均质量" value={summaryStats.averageQuality} suffix="/100" prefix={<AimOutlined />} />
              </Card>
            </Col>
          </Row>

          <Row gutter={[14, 14]}>
            <Col xs={24} xl={15}>
              <Card title={<Space><LineChartOutlined />期权投票</Space>}>
                <Table
                  rowKey="symbol"
                  columns={signalColumns}
                  dataSource={result.signals}
                  pagination={false}
                  scroll={{ x: 980 }}
                  rowClassName={record => record.symbol === activeSignal?.symbol ? 'options-active-row' : ''}
                />
              </Card>
            </Col>
            <Col xs={24} xl={9}>
              <Card title={<Space><BarChartOutlined />关键价位分布</Space>}>
                {activeSignal && strikeChartData.length > 0 ? (
                  <div className="options-chart-shell">
                    <ResponsiveContainer width="100%" height={260}>
                      <BarChart data={strikeChartData} margin={{ top: 8, right: 4, bottom: 18, left: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} />
                        <XAxis dataKey="name" tick={{ fontSize: 11 }} angle={-24} textAnchor="end" height={58} />
                        <YAxis tickFormatter={formatNumber} width={54} />
                        <Tooltip formatter={(value: number, _name, item) => [formatNumber(value), item.payload.metric]} />
                        <Bar dataKey="value" fill="#167c80" radius={[4, 4, 0, 0]} />
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                ) : (
                  <Empty description="暂无关键价位" />
                )}
              </Card>
            </Col>
          </Row>

          {activeSignal ? (
            <Card className="options-detail-card">
              <div className="options-detail-head">
                <Space wrap>
                  <Title level={4}>{activeSignal.symbol}</Title>
                  <Tag color={directionColor[activeSignal.direction]}>{activeSignal.direction}</Tag>
                  <Tag>{activeSignal.provider_name}</Tag>
                  <Tag color={activeSignal.source_status === 'partial' ? 'orange' : 'blue'}>{activeSignal.source_status}</Tag>
                </Space>
                <Space wrap>
                  <Text>现价 {formatPrice(activeSignal.underlying_price)}</Text>
                  <Text>Max Pain {formatPrice(activeSignal.max_pain)}</Text>
                  <Text>Pin {activeSignal.pin_risk_score}/100</Text>
                  <Button
                    type="primary"
                    icon={<RobotOutlined />}
                    loading={aiLoading}
                    onClick={handleAiAnalysis}
                  >
                    AI分析走势
                  </Button>
                </Space>
              </div>
              <Paragraph>{activeSignal.summary}</Paragraph>
              <Space wrap className="options-signal-tags">
                {activeSignal.signals.slice(0, 8).map(line => <Tag key={line}>{line}</Tag>)}
              </Space>
              {visibleAiAnalysis && (
                <div className="options-ai-panel">
                  <div className="options-ai-head">
                    <Space wrap>
                      <RobotOutlined />
                      <Text strong>AI走势研判</Text>
                      <Tag color={trendColor[visibleAiAnalysis.trend_label]}>{visibleAiAnalysis.trend_label}</Tag>
                      <Tag>方向分 {visibleAiAnalysis.trend_score}/100</Tag>
                      <Tag>置信 {formatConfidence(visibleAiAnalysis.confidence)}</Tag>
                    </Space>
                    <Text type="secondary">
                      {visibleAiAnalysis.provider} · {visibleAiAnalysis.model} · {visibleAiAnalysis.time_horizon}
                    </Text>
                  </div>
                  <Paragraph className="options-ai-thesis">{visibleAiAnalysis.thesis}</Paragraph>
                  <div className="options-ai-grid">
                    <div>
                      <Text strong>核心驱动</Text>
                      <Space wrap>
                        {visibleAiAnalysis.key_drivers.map(item => <Tag key={item}>{item}</Tag>)}
                      </Space>
                    </div>
                    <div>
                      <Text strong>观察价位</Text>
                      <Space wrap>
                        {visibleAiAnalysis.watch_levels.map(item => <Tag key={item}>{item}</Tag>)}
                      </Space>
                    </div>
                    <div>
                      <Text strong>上行触发</Text>
                      <ul className="options-ai-list">
                        {visibleAiAnalysis.upside_triggers.map(item => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                    <div>
                      <Text strong>下行反证</Text>
                      <ul className="options-ai-list">
                        {visibleAiAnalysis.downside_triggers.map(item => <li key={item}>{item}</li>)}
                      </ul>
                    </div>
                  </div>
                  <Alert
                    className="options-ai-action"
                    type="info"
                    showIcon
                    message={visibleAiAnalysis.suggested_action}
                    description={visibleAiAnalysis.risk_notes.slice(0, 3).join('；')}
                  />
                </div>
              )}
              {activeSignal.unusual_flow_count > 0 && (
                <Alert
                  className="options-flow-callout"
                  type="warning"
                  showIcon
                  message={`捕捉到 ${activeSignal.unusual_flow_count} 条异常大单候选，合计权利金约 ${formatMoney(activeSignal.unusual_premium_notional)}`}
                  description={`当前基于成交量、OI、权利金和行权价距离做链级异常识别；下表展示评分最高的 ${(activeSignal.unusual_flows || []).length} 条，主动买卖方向、扫单速度和成交席位需要接入实时订单流进一步确认。`}
                />
              )}

              <Tabs
                className="options-detail-tabs"
                items={[
                  {
                    key: 'unusual',
                    label: `异常大单 (${activeSignal.unusual_flow_count || 0})`,
                    children: (activeSignal.unusual_flows || []).length > 0 ? (
                      <Table
                        rowKey={item => `${item.option_symbol}-${item.score}`}
                        columns={unusualFlowColumns}
                        dataSource={activeSignal.unusual_flows || []}
                        pagination={false}
                        scroll={{ x: 980 }}
                      />
                    ) : (
                      <Empty description="暂无异常大单候选" />
                    )
                  },
                  {
                    key: 'expirations',
                    label: '到期结构',
                    children: (
                      <Table
                        rowKey="expiration"
                        columns={expirationColumns}
                        dataSource={activeSignal.expirations}
                        pagination={false}
                        scroll={{ x: 760 }}
                      />
                    )
                  },
                  {
                    key: 'strikes',
                    label: '关键价位',
                    children: (
                      <Table
                        rowKey={item => `${item.metric}-${item.side}-${item.strike}`}
                        columns={strikeColumns}
                        dataSource={activeSignal.key_strikes}
                        pagination={false}
                        scroll={{ x: 760 }}
                      />
                    )
                  },
                  {
                    key: 'risk',
                    label: '风险与限制',
                    children: (
                      <Space direction="vertical" size={8}>
                        {activeSignal.risk_flags.map(flag => (
                          <Alert key={flag} type="info" showIcon message={flag} />
                        ))}
                      </Space>
                    )
                  },
                  {
                    key: 'sources',
                    label: '数据源画像',
                    children: (
                      <Row gutter={[12, 12]}>
                        {result.sources.map(source => (
                          <Col xs={24} md={12} key={source.provider}>
                            <Card size="small" title={<Space><FieldTimeOutlined />{source.name}</Space>} extra={<Tag color={sourceStatusColor[source.status]}>{sourceStatusLabel[source.status]}</Tag>}>
                              <Space direction="vertical" size={6}>
                                <Text>{source.coverage}</Text>
                                <Text type="secondary">{source.delay}</Text>
                                <Text type="secondary">{source.notes}</Text>
                              </Space>
                            </Card>
                          </Col>
                        ))}
                      </Row>
                    )
                  }
                ]}
              />
            </Card>
          ) : null}
        </>
      ) : (
        <Card>
          <Empty description="等待生成期权信号" />
        </Card>
      )}
    </div>
  );
};

export default OptionsSignalCenter;
