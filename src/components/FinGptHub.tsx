import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  List,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Tabs,
  Tag,
  Typography,
  Upload,
  message
} from 'antd';
import {
  ApiOutlined,
  AuditOutlined,
  BulbOutlined,
  ExperimentOutlined,
  FileSearchOutlined,
  LineChartOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  SettingOutlined,
  ThunderboltOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import {
  AiResearchReport,
  CapabilityListResponse,
  FinGptTaskResponse,
  ModelConfig,
  SentimentResponse,
  StockCheckResponse,
  analyzeReport,
  analyzeStock,
  assessCorridorRisk,
  checkAiApiHealth,
  createAgentBrief,
  extractFileText,
  forecastStock,
  getFinGptCapabilities,
  getModelConfig,
  ragQuery,
  runStockCheck,
  scoreSentiment,
  summarizeNews,
  updateModelConfig
} from '../services/aiResearchService';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface FinGptHubProps {
  appState: AppState;
}

const sentimentMeta = {
  positive: { text: '偏积极', color: 'green' },
  neutral: { text: '中性', color: 'blue' },
  negative: { text: '偏谨慎', color: 'red' }
};

const riskMeta = {
  low: { text: '低', color: '#52c41a' },
  medium: { text: '中', color: '#faad14' },
  high: { text: '高', color: '#ff4d4f' }
};

const agentOptions = [
  { value: 'ops_oversight', label: '运营监督 Agent' },
  { value: 'audit_governance', label: '审计治理 Agent' },
  { value: 'process_improvement', label: '流程改进 Agent' },
  { value: 'internal_support', label: '内部支持 Agent' },
  { value: 'treasury_strategy', label: '资金策略 Agent' }
];

const sampleReport = `营收同比增长 18%，毛利率提升 2.1 个百分点，经营现金流改善。
管理层上调全年指引，但提示供应链和汇率仍有不确定性。`;

const sampleRagQuestion = 'Finogrid 的 Agent Ledger 和运维控制台分别负责什么？';

const FinGptHub: React.FC<FinGptHubProps> = ({ appState }) => {
  const [selectedSymbol, setSelectedSymbol] = useState(appState.stocks[0]?.symbol);
  const [question, setQuestion] = useState('');
  const [stockCheck, setStockCheck] = useState<StockCheckResponse | null>(null);
  const [stockReport, setStockReport] = useState<AiResearchReport | null>(null);
  const [sentimentText, setSentimentText] = useState('公司业绩超预期，但管理层提示未来需求仍有波动风险。');
  const [sentimentResult, setSentimentResult] = useState<SentimentResponse | null>(null);
  const [genericResult, setGenericResult] = useState<FinGptTaskResponse | null>(null);
  const [reportText, setReportText] = useState(sampleReport);
  const [ragQuestion, setRagQuestion] = useState(sampleRagQuestion);
  const [ragContext, setRagContext] = useState('');
  const [corridorCode, setCorridorCode] = useState('BR');
  const [corridorContext, setCorridorContext] = useState('巴西 PIX 通道今日成功率稳定，USDC 链上确认时间正常。');
  const [agentRole, setAgentRole] = useState('ops_oversight');
  const [agentContext, setAgentContext] = useState('今日支付失败率略有上升，两个 KYA 请求待人工复核，USDC 通道无异常。');
  const [loadingKey, setLoadingKey] = useState<string | null>(null);
  const [apiStatus, setApiStatus] = useState<{ status: string; provider: string; model: string } | null>(null);
  const [capabilities, setCapabilities] = useState<CapabilityListResponse | null>(null);
  const [modelConfig, setModelConfig] = useState<ModelConfig | null>(null);
  const [configDraft, setConfigDraft] = useState({
    provider: 'mock' as ModelConfig['provider'],
    model: 'mock-research-analyst',
    base_url: '',
    api_key: '',
    temperature: 0.2
  });

  const selectedStock = useMemo(
    () => appState.stocks.find(stock => stock.symbol === selectedSymbol) || appState.stocks[0],
    [appState.stocks, selectedSymbol]
  );

  const relatedPosts = useMemo(
    () => selectedStock
      ? appState.posts.filter(post => post.stockSymbol === selectedStock.symbol).slice(0, 8)
      : [],
    [appState.posts, selectedStock]
  );

  useEffect(() => {
    checkAiApiHealth()
      .then(setApiStatus)
      .catch(() => setApiStatus(null));
    getFinGptCapabilities()
      .then(setCapabilities)
      .catch(() => setCapabilities(null));
    getModelConfig()
      .then(config => {
        setModelConfig(config);
        setConfigDraft({
          provider: config.provider,
          model: config.model,
          base_url: config.base_url || '',
          api_key: '',
          temperature: config.temperature
        });
      })
      .catch(() => setModelConfig(null));
  }, []);

  const runTask = async (key: string, task: () => Promise<void>) => {
    setLoadingKey(key);
    try {
      await task();
      message.success('FinGPT 能力调用完成');
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '请确认后端 API 已启动并配置模型';
      message.error(`调用失败：${detail}`);
    } finally {
      setLoadingKey(null);
    }
  };

  const handleStockAnalysis = () => runTask('stock', async () => {
    if (!selectedStock) return;
    const data = await analyzeStock({
      stock: selectedStock,
      posts: relatedPosts.map(post => ({
        title: post.title,
        summary: post.summary,
        content: post.content,
        category: post.category,
        tags: post.tags,
        qualityScore: post.qualityScore,
        publishTime: post.publishTime
      })),
      question,
      locale: 'zh-CN'
    });
    setStockReport(data);
  });

  const handleStockCheck = () => runTask('stock-check', async () => {
    if (!selectedStock) return;
    setStockCheck(await runStockCheck({
      stock: selectedStock,
      posts: relatedPosts.map(post => ({
        title: post.title,
        summary: post.summary,
        content: post.content,
        category: post.category,
        tags: post.tags,
        qualityScore: post.qualityScore,
        publishTime: post.publishTime
      })),
      question,
      horizon: '1周',
      locale: 'zh-CN'
    }));
  });

  const handleSentiment = () => runTask('sentiment', async () => {
    setSentimentResult(await scoreSentiment(sentimentText));
  });

  const handleNewsSummary = () => runTask('news', async () => {
    setGenericResult(await summarizeNews({
      stock: selectedStock,
      items: relatedPosts.map(post => ({
        title: post.title,
        summary: post.summary,
        source: post.author.username,
        published_at: post.publishTime
      })),
      focus: question || '提炼影响股价的关键信息'
    }));
  });

  const handleReport = () => runTask('report', async () => {
    setGenericResult(await analyzeReport({
      title: selectedStock ? `${selectedStock.name} 报告解读` : '报告解读',
      report_text: reportText,
      stock: selectedStock
    }));
  });

  const handleRag = () => runTask('rag', async () => {
    setGenericResult(await ragQuery({
      question: ragQuestion,
      documents: ragContext.trim()
        ? [{ source: '手动输入/上传资料', text: ragContext }]
        : []
    }));
  });

  const handleForecast = () => runTask('forecast', async () => {
    if (!selectedStock) return;
    setGenericResult(await forecastStock({
      stock: selectedStock,
      horizon: '1周',
      context: question,
      posts: relatedPosts.map(post => ({
        title: post.title,
        summary: post.summary,
        content: post.content,
        category: post.category,
        tags: post.tags,
        qualityScore: post.qualityScore,
        publishTime: post.publishTime
      }))
    }));
  });

  const handleCorridor = () => runTask('corridor', async () => {
    setGenericResult(await assessCorridorRisk({
      corridor_code: corridorCode,
      asset: 'USDC',
      news_items: [
        ...relatedPosts.map(post => ({
          title: post.title,
          summary: post.summary,
          source: post.author.username,
          published_at: post.publishTime
        })),
        ...(corridorContext.trim()
          ? [{ title: '手动输入/上传资料', summary: corridorContext, source: 'local-input' }]
          : [])
      ]
    }));
  });

  const handleAgent = () => runTask('agent', async () => {
    setGenericResult(await createAgentBrief({
      role: agentRole,
      context: agentContext
    }));
  });

  const handleSaveModelConfig = () => runTask('model-config', async () => {
    const payload: any = {
      provider: configDraft.provider,
      model: configDraft.model,
      base_url: configDraft.base_url,
      temperature: configDraft.temperature,
      persist: true
    };
    if (configDraft.api_key.trim()) {
      payload.api_key = configDraft.api_key.trim();
    }

    const saved = await updateModelConfig(payload);
    setModelConfig(saved);
    setConfigDraft(prev => ({ ...prev, api_key: '' }));
    setApiStatus({ status: 'ok', provider: saved.provider, model: saved.model });
    setCapabilities(await getFinGptCapabilities());
  });

  const stockResult = stockReport && (
    <Card title={<Space><LineChartOutlined />{selectedStock?.name} 投研结果</Space>} extra={<Tag color="blue">{stockReport.model}</Tag>}>
      <Paragraph style={{ fontSize: 16 }}>{stockReport.executive_summary}</Paragraph>
      <Row gutter={[16, 16]}>
        <Col xs={24} md={8}>
          <Statistic title="情绪" value={sentimentMeta[stockReport.sentiment_label].text} valueStyle={{ color: sentimentMeta[stockReport.sentiment_label].color }} />
        </Col>
        <Col xs={24} md={8}>
          <Statistic title="情绪分" value={stockReport.sentiment_score} precision={2} />
          <Progress percent={Math.round((stockReport.sentiment_score + 1) * 50)} showInfo={false} strokeColor={sentimentMeta[stockReport.sentiment_label].color} />
        </Col>
        <Col xs={24} md={8}>
          <Statistic title="风险" value={riskMeta[stockReport.risk_level].text} valueStyle={{ color: riskMeta[stockReport.risk_level].color }} />
        </Col>
      </Row>
      <Row gutter={[16, 16]} style={{ marginTop: 16 }}>
        <Col xs={24} md={12}><ResultList title="催化因素" items={stockReport.catalysts} /></Col>
        <Col xs={24} md={12}><ResultList title="主要风险" items={stockReport.risks} /></Col>
      </Row>
      <Alert style={{ marginTop: 16 }} type="info" showIcon message={stockReport.disclaimer} />
    </Card>
  );

  const sentimentPanel = sentimentResult && (
    <Card title="情绪分析结果" extra={<Tag color={sentimentMeta[sentimentResult.label].color}>{sentimentMeta[sentimentResult.label].text}</Tag>}>
      <Statistic title="情绪分" value={sentimentResult.score} precision={2} />
      <Progress percent={Math.round((sentimentResult.score + 1) * 50)} showInfo={false} strokeColor={sentimentMeta[sentimentResult.label].color} />
      <Paragraph style={{ marginTop: 12 }}>{sentimentResult.rationale}</Paragraph>
    </Card>
  );

  const stockSelector = (
    <Space direction="vertical" size={12} style={{ width: '100%' }}>
      <Select
        value={selectedStock?.symbol}
        style={{ width: '100%' }}
        onChange={setSelectedSymbol}
        options={appState.stocks.map(stock => ({
          value: stock.symbol,
          label: `${stock.name} (${stock.symbol})`
        }))}
      />
      {selectedStock && (
        <Row gutter={12}>
          <Col span={12}><Statistic title="价格" prefix="$" value={selectedStock.currentPrice} precision={2} /></Col>
          <Col span={12}><Statistic title="涨跌幅" suffix="%" value={selectedStock.changePercent} precision={2} valueStyle={{ color: selectedStock.changePercent >= 0 ? '#cf1322' : '#389e0d' }} /></Col>
        </Row>
      )}
      <FileTextArea
        value={question}
        rows={3}
        onChange={setQuestion}
        placeholder="可选：输入关注点，或上传公告/研报/新闻材料"
      />
    </Space>
  );

  return (
    <div style={{ padding: 16 }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} md={14}>
              <Space direction="vertical" size={4}>
                <Title level={3} style={{ margin: 0 }}>FinGPT 能力中心</Title>
                <Text type="secondary">统一承载金融情绪、新闻蒸馏、RAG、预测、通道风险和 Agent 工作流。</Text>
              </Space>
            </Col>
            <Col xs={24} md={10}>
              <Space wrap style={{ justifyContent: 'flex-end', width: '100%' }}>
                <Tag color={apiStatus ? 'green' : 'orange'}>{apiStatus ? apiStatus.provider : 'API未连接'}</Tag>
                <Tag color="blue">{apiStatus?.model || 'unknown-model'}</Tag>
                <Tag>{capabilities ? `${capabilities.capabilities.length} 项能力` : '加载中'}</Tag>
              </Space>
            </Col>
          </Row>
        </Card>

        <Tabs
          type="card"
          defaultActiveKey="stock-check"
          items={[
            {
              key: 'overview',
              label: <span><ThunderboltOutlined />能力总览</span>,
              children: (
                <Row gutter={[16, 16]}>
                  {(capabilities?.capabilities || []).map(item => (
                    <Col xs={24} md={12} xl={6} key={item.key}>
                      <Card title={item.name} extra={<Tag>{item.mode}</Tag>} style={{ height: '100%' }}>
                        <Paragraph type="secondary">{item.description}</Paragraph>
                        <Text code>{item.endpoint}</Text>
                      </Card>
                    </Col>
                  ))}
                </Row>
              )
            },
            {
              key: 'model-config',
              label: <span><SettingOutlined />模型配置</span>,
              children: (
                <ModelConfigPanel
                  config={modelConfig}
                  draft={configDraft}
                  loading={loadingKey === 'model-config'}
                  onDraftChange={setConfigDraft}
                  onSave={handleSaveModelConfig}
                />
              )
            },
            {
              key: 'stock-check',
              label: <span><ThunderboltOutlined />一键检测</span>,
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={8}>
                    <Card title="选择个股">
                      {stockSelector}
                      <Button
                        type="primary"
                        block
                        icon={loadingKey === 'stock-check' ? <ReloadOutlined spin /> : <ThunderboltOutlined />}
                        loading={loadingKey === 'stock-check'}
                        onClick={handleStockCheck}
                        style={{ marginTop: 16 }}
                      >
                        一键检测个股
                      </Button>
                    </Card>
                  </Col>
                  <Col xs={24} lg={16}>
                    {stockCheck ? <StockCheckPanel result={stockCheck} /> : <EmptyState />}
                  </Col>
                </Row>
              )
            },
            {
              key: 'stock',
              label: <span><LineChartOutlined />个股投研</span>,
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={8}>
                    <Card title="输入">
                      {stockSelector}
                      <Button type="primary" block icon={<BulbOutlined />} loading={loadingKey === 'stock'} onClick={handleStockAnalysis} style={{ marginTop: 16 }}>
                        生成投研报告
                      </Button>
                    </Card>
                  </Col>
                  <Col xs={24} lg={16}>{stockReport ? stockResult : <EmptyState />}</Col>
                </Row>
              )
            },
            {
              key: 'sentiment',
              label: <span><ExperimentOutlined />情绪分析</span>,
              children: (
                <Row gutter={[16, 16]}>
                  <Col xs={24} lg={10}>
                    <Card title="文本">
                      <FileTextArea
                        rows={8}
                        value={sentimentText}
                        onChange={setSentimentText}
                        placeholder="输入或上传新闻、公告、研报段落"
                      />
                      <Button type="primary" block icon={<ExperimentOutlined />} loading={loadingKey === 'sentiment'} onClick={handleSentiment} style={{ marginTop: 16 }}>
                        分析情绪
                      </Button>
                    </Card>
                  </Col>
                  <Col xs={24} lg={14}>{sentimentResult ? sentimentPanel : <EmptyState />}</Col>
                </Row>
              )
            },
            {
              key: 'news',
              label: <span><BulbOutlined />新闻蒸馏</span>,
              children: <TwoColumnRun input={stockSelector} buttonText="蒸馏新闻" loading={loadingKey === 'news'} onRun={handleNewsSummary} result={genericResult?.capability === 'news_summary' ? genericResult : null} />
            },
            {
              key: 'report',
              label: <span><FileSearchOutlined />财报解读</span>,
              children: (
                <TwoColumnRun
                  input={
                    <FileTextArea
                      rows={10}
                      value={reportText}
                      onChange={setReportText}
                      placeholder="输入或上传财报、研报、公告文件"
                    />
                  }
                  buttonText="解读报告"
                  loading={loadingKey === 'report'}
                  onRun={handleReport}
                  result={genericResult?.capability === 'report_analysis' ? genericResult : null}
                />
              )
            },
            {
              key: 'rag',
              label: <span><ApiOutlined />RAG问答</span>,
              children: (
                <TwoColumnRun
                  input={
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Input value={ragQuestion} onChange={event => setRagQuestion(event.target.value)} />
                      <FileTextArea
                        rows={8}
                        value={ragContext}
                        onChange={setRagContext}
                        placeholder="可选：上传或粘贴知识库资料；留空则使用 Finogrid 默认文档"
                      />
                    </Space>
                  }
                  buttonText="检索问答"
                  loading={loadingKey === 'rag'}
                  onRun={handleRag}
                  result={genericResult?.capability === 'rag_query' ? genericResult : null}
                />
              )
            },
            {
              key: 'forecast',
              label: <span><LineChartOutlined />预测推演</span>,
              children: <TwoColumnRun input={stockSelector} buttonText="生成情景推演" loading={loadingKey === 'forecast'} onRun={handleForecast} result={genericResult?.capability === 'forecast' ? genericResult : null} />
            },
            {
              key: 'corridor',
              label: <span><SafetyCertificateOutlined />通道风险</span>,
              children: (
                <TwoColumnRun
                  input={
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Select
                        value={corridorCode}
                        onChange={setCorridorCode}
                        options={['BR', 'NG', 'IN', 'AR', 'VN', 'AE', 'ID', 'PH', 'US'].map(value => ({ value, label: value }))}
                      />
                      <Alert type="info" showIcon message="面向 Finogrid 稳定币支付通道，后续可接入 FX、KYT、Bridge 和链上数据。" />
                      <FileTextArea
                        rows={6}
                        value={corridorContext}
                        onChange={setCorridorContext}
                        placeholder="输入或上传通道新闻、运营日报、伙伴 SLA 记录"
                      />
                    </Space>
                  }
                  buttonText="评估风险"
                  loading={loadingKey === 'corridor'}
                  onRun={handleCorridor}
                  result={genericResult?.capability === 'corridor_risk' ? genericResult : null}
                />
              )
            },
            {
              key: 'agent',
              label: <span><RobotOutlined />Agent工作台</span>,
              children: (
                <TwoColumnRun
                  input={
                    <Space direction="vertical" style={{ width: '100%' }}>
                      <Select value={agentRole} onChange={setAgentRole} options={agentOptions} />
                      <FileTextArea
                        rows={8}
                        value={agentContext}
                        onChange={setAgentContext}
                        placeholder="输入或上传运营日志、审计记录、支持工单"
                      />
                    </Space>
                  }
                  buttonText="生成 Agent 摘要"
                  loading={loadingKey === 'agent'}
                  onRun={handleAgent}
                  result={genericResult?.capability === 'agent_brief' ? genericResult : null}
                  icon={<AuditOutlined />}
                />
              )
            }
          ]}
        />
      </Space>
    </div>
  );
};

interface TwoColumnRunProps {
  input: React.ReactNode;
  buttonText: string;
  loading: boolean;
  onRun: () => void;
  result: FinGptTaskResponse | null;
  icon?: React.ReactNode;
}

const TwoColumnRun: React.FC<TwoColumnRunProps> = ({ input, buttonText, loading, onRun, result, icon }) => (
  <Row gutter={[16, 16]}>
    <Col xs={24} lg={8}>
      <Card title="输入">
        {input}
        <Button type="primary" block icon={loading ? <ReloadOutlined spin /> : icon || <BulbOutlined />} loading={loading} onClick={onRun} style={{ marginTop: 16 }}>
          {buttonText}
        </Button>
      </Card>
    </Col>
    <Col xs={24} lg={16}>
      {result ? <TaskResult result={result} /> : <EmptyState />}
    </Col>
  </Row>
);

const EmptyState: React.FC = () => (
  <Card>
    <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="运行能力后查看结果" />
  </Card>
);

interface FileTextAreaProps {
  value: string;
  onChange: (value: string) => void;
  rows?: number;
  placeholder?: string;
}

const acceptedFileTypes = '.txt,.md,.markdown,.csv,.json,.pdf,.docx,.xlsx,.log';

const appendExtractedText = (current: string, result: { filename: string; text: string }) => {
  const block = `[文件: ${result.filename}]\n${result.text}`;
  return current.trim() ? `${current.trim()}\n\n${block}` : block;
};

const UploadAppendButton: React.FC<{
  value: string;
  onChange: (value: string) => void;
  label?: string;
}> = ({ value, onChange, label = '上传文件' }) => {
  const [uploading, setUploading] = useState(false);

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const result = await extractFileText(file);
      onChange(appendExtractedText(value, result));
      message.success(`${result.filename} 已解析，提取 ${result.char_count} 字`);
    } catch (error: any) {
      const detail = error?.response?.data?.detail || '文件解析失败';
      message.error(detail);
    } finally {
      setUploading(false);
    }
  };

  return (
    <Upload
      accept={acceptedFileTypes}
      showUploadList={false}
      beforeUpload={file => {
        handleUpload(file as File);
        return false;
      }}
    >
      <Button block loading={uploading} icon={<FileSearchOutlined />}>
        {label}
      </Button>
    </Upload>
  );
};

const FileTextArea: React.FC<FileTextAreaProps> = ({
  value,
  onChange,
  rows = 6,
  placeholder
}) => (
  <Space direction="vertical" size={8} style={{ width: '100%' }}>
    <TextArea
      rows={rows}
      value={value}
      onChange={event => onChange(event.target.value)}
      placeholder={placeholder}
    />
    <UploadAppendButton value={value} onChange={onChange} />
  </Space>
);

interface ModelConfigDraft {
  provider: ModelConfig['provider'];
  model: string;
  base_url: string;
  api_key: string;
  temperature: number;
}

interface ModelConfigPanelProps {
  config: ModelConfig | null;
  draft: ModelConfigDraft;
  loading: boolean;
  onDraftChange: React.Dispatch<React.SetStateAction<ModelConfigDraft>>;
  onSave: () => void;
}

const providerOptions = [
  { value: 'mock', label: 'Mock 本地演示' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'minimax', label: 'MiniMax' },
  { value: 'openai-compatible', label: 'OpenAI-compatible' }
];

const modelOptions: Record<string, string[]> = {
  mock: ['mock-research-analyst'],
  openai: ['gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4.1'],
  minimax: ['MiniMax-M2.7'],
  'openai-compatible': ['gpt-4o-mini', 'deepseek-chat', 'qwen-plus', 'moonshot-v1-8k']
};

const providerDefaults: Record<string, { model: string; base_url: string }> = {
  mock: { model: 'mock-research-analyst', base_url: '' },
  openai: { model: 'gpt-4o-mini', base_url: '' },
  minimax: { model: 'MiniMax-M2.7', base_url: 'https://api.minimax.io/v1' },
  'openai-compatible': { model: 'gpt-4o-mini', base_url: '' },
  cloud: { model: 'gpt-4o-mini', base_url: '' }
};

const ModelConfigPanel: React.FC<ModelConfigPanelProps> = ({
  config,
  draft,
  loading,
  onDraftChange,
  onSave
}) => {
  const updateProvider = (provider: ModelConfig['provider']) => {
    const defaults = providerDefaults[provider];
    onDraftChange(prev => ({
      ...prev,
      provider,
      model: defaults.model,
      base_url: defaults.base_url
    }));
  };

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={9}>
        <Card title="当前配置">
          {config ? (
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <Statistic title="Provider" value={config.provider} />
              <Statistic title="Model" value={config.model} />
              <Statistic title="Temperature" value={config.temperature} precision={2} />
              <div>
                <Text type="secondary">API Key</Text>
                <br />
                {config.api_key_configured ? (
                  <Tag color="green">{config.api_key_preview}</Tag>
                ) : (
                  <Tag color="orange">未配置</Tag>
                )}
              </div>
              <div>
                <Text type="secondary">配置来源</Text>
                <Paragraph code copyable style={{ marginTop: 4 }}>{config.config_source}</Paragraph>
              </div>
            </Space>
          ) : (
            <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="配置未加载" />
          )}
        </Card>
      </Col>

      <Col xs={24} lg={15}>
        <Card title="模型调用配置">
          <Space direction="vertical" size={16} style={{ width: '100%' }}>
            <div>
              <Text strong>Provider</Text>
              <Select
                value={draft.provider}
                options={providerOptions}
                onChange={updateProvider}
                style={{ width: '100%', marginTop: 8 }}
              />
            </div>

            <div>
              <Text strong>模型名</Text>
              <Input
                value={draft.model}
                onChange={event => onDraftChange(prev => ({ ...prev, model: event.target.value }))}
                style={{ marginTop: 8 }}
                placeholder="例如 gpt-4o-mini、MiniMax-M2.7、deepseek-chat"
              />
              <Space wrap style={{ marginTop: 8 }}>
                {(modelOptions[draft.provider] || []).map(model => (
                  <Button
                    key={model}
                    size="small"
                    onClick={() => onDraftChange(prev => ({ ...prev, model }))}
                  >
                    {model}
                  </Button>
                ))}
              </Space>
            </div>

            <div>
              <Text strong>Base URL</Text>
              <Input
                value={draft.base_url}
                onChange={event => onDraftChange(prev => ({ ...prev, base_url: event.target.value }))}
                placeholder="OpenAI 官方可留空；兼容接口填写 https://.../v1"
                style={{ marginTop: 8 }}
              />
            </div>

            <div>
              <Text strong>API Key</Text>
              <Input.Password
                value={draft.api_key}
                onChange={event => onDraftChange(prev => ({ ...prev, api_key: event.target.value }))}
                placeholder={config?.api_key_configured ? '留空则继续使用已保存的 key' : '输入云模型 API key'}
                style={{ marginTop: 8 }}
              />
            </div>

            <div>
              <Text strong>Temperature</Text>
              <br />
              <InputNumber
                min={0}
                max={1}
                step={0.05}
                value={draft.temperature}
                onChange={value => onDraftChange(prev => ({ ...prev, temperature: Number(value ?? 0.2) }))}
                style={{ width: 180, marginTop: 8 }}
              />
            </div>

            <Alert
              type="info"
              showIcon
              message="配置保存在本机 backend/.model_config.json，不会提交到 Git。保存后新请求会立即使用新模型。"
            />

            <Button type="primary" icon={<SettingOutlined />} loading={loading} onClick={onSave}>
              保存模型配置
            </Button>
          </Space>
        </Card>
      </Col>
    </Row>
  );
};

const verdictColor: Record<StockCheckResponse['verdict'], string> = {
  重点跟踪: 'green',
  谨慎观察: 'gold',
  暂不行动: 'red'
};

const checkStatusColor: Record<string, string> = {
  completed: 'green',
  failed: 'red',
  skipped: 'default'
};

const StockCheckPanel: React.FC<{ result: StockCheckResponse }> = ({ result }) => {
  const taskResults = [
    { key: 'news', title: '新闻蒸馏', value: result.news_summary },
    { key: 'report', title: '资料解读', value: result.report_analysis },
    { key: 'rag', title: 'RAG 问答', value: result.rag_answer },
    { key: 'forecast', title: '预测推演', value: result.forecast },
    { key: 'agent', title: 'Agent 复核', value: result.agent_brief }
  ].filter(item => item.value);

  return (
    <Space direction="vertical" size={16} style={{ width: '100%' }}>
      <Card
        title={`${result.stock.name} 一键检测`}
        extra={<Space><Tag color={verdictColor[result.verdict]}>{result.verdict}</Tag><Tag color="blue">{result.model}</Tag></Space>}
      >
        <Paragraph style={{ fontSize: 16 }}>{result.summary}</Paragraph>
        <Row gutter={[16, 16]}>
          <Col xs={24} md={8}>
            <Statistic title="综合分" value={result.score} suffix="/100" />
            <Progress percent={result.score} showInfo={false} strokeColor={verdictColor[result.verdict]} />
          </Col>
          <Col xs={24} md={8}>
            <Statistic title="置信度" value={result.confidence * 100} precision={0} suffix="%" />
            <Progress percent={Math.round(result.confidence * 100)} showInfo={false} />
          </Col>
          <Col xs={24} md={8}>
            <Statistic title="能力完成" value={result.checks.filter(item => item.status === 'completed').length} suffix={`/${result.checks.length}`} />
          </Col>
        </Row>
      </Card>

      <Row gutter={[16, 16]}>
        <Col xs={24} md={12}>
          <Card>
            <ResultList title="下一步动作" items={result.action_items} />
          </Card>
        </Col>
        <Col xs={24} md={12}>
          <Card>
            <ResultList title="风险旗标" items={result.risk_flags} />
          </Card>
        </Col>
      </Row>

      {result.stock_analysis && (
        <Card title="个股投研摘要" extra={<Tag color={sentimentMeta[result.stock_analysis.sentiment_label].color}>{sentimentMeta[result.stock_analysis.sentiment_label].text}</Tag>}>
          <Paragraph>{result.stock_analysis.executive_summary}</Paragraph>
          <Row gutter={[16, 16]}>
            <Col xs={24} md={8}><Statistic title="情绪分" value={result.stock_analysis.sentiment_score} precision={2} /></Col>
            <Col xs={24} md={8}><Statistic title="风险" value={riskMeta[result.stock_analysis.risk_level].text} valueStyle={{ color: riskMeta[result.stock_analysis.risk_level].color }} /></Col>
            <Col xs={24} md={8}><Statistic title="催化数量" value={result.stock_analysis.catalysts.length} /></Col>
          </Row>
        </Card>
      )}

      <Card title="能力执行状态">
        <Space wrap>
          {result.checks.map(item => (
            <Tag key={item.key} color={checkStatusColor[item.status]}>
              {item.name} · {item.status === 'completed' ? '完成' : item.status === 'skipped' ? '不适用' : '失败'}
            </Tag>
          ))}
        </Space>
        {result.warnings.length > 0 && (
          <Alert style={{ marginTop: 12 }} type="warning" showIcon message="部分能力未完成" description={result.warnings.join('；')} />
        )}
      </Card>

      {taskResults.length > 0 && (
        <Row gutter={[16, 16]}>
          {taskResults.map(item => (
            <Col xs={24} md={12} key={item.key}>
              <Card title={item.title} extra={<Tag>{item.value!.confidence ? `${Math.round(item.value!.confidence * 100)}%` : '能力结果'}</Tag>} style={{ height: '100%' }}>
                <Paragraph>{item.value!.summary}</Paragraph>
                <ResultList title="信号" items={item.value!.signals.slice(0, 3)} />
                <ResultList title="动作" items={item.value!.actions.slice(0, 3)} />
              </Card>
            </Col>
          ))}
        </Row>
      )}

      <Alert type="info" showIcon message={result.disclaimer} />
    </Space>
  );
};

const ResultList: React.FC<{ title: string; items: string[] }> = ({ title, items }) => (
  <div>
    <Text strong>{title}</Text>
    {items.length ? (
      <List size="small" dataSource={items} renderItem={item => <List.Item>{item}</List.Item>} />
    ) : (
      <Paragraph type="secondary" style={{ marginTop: 8, marginBottom: 0 }}>暂无</Paragraph>
    )}
  </div>
);

const TaskResult: React.FC<{ result: FinGptTaskResponse }> = ({ result }) => (
  <Space direction="vertical" size={16} style={{ width: '100%' }}>
    <Card title={result.title} extra={<Tag color="blue">{result.model}</Tag>}>
      <Paragraph style={{ fontSize: 16 }}>{result.summary}</Paragraph>
      <Statistic title="置信度" value={result.confidence * 100} precision={0} suffix="%" />
      <Progress percent={Math.round(result.confidence * 100)} showInfo={false} />
    </Card>
    <Row gutter={[16, 16]}>
      <Col xs={24} md={12}><Card><ResultList title="关键点" items={result.key_points} /></Card></Col>
      <Col xs={24} md={12}><Card><ResultList title="信号" items={result.signals} /></Card></Col>
      <Col xs={24} md={12}><Card><ResultList title="风险" items={result.risks} /></Card></Col>
      <Col xs={24} md={12}><Card><ResultList title="动作" items={result.actions} /></Card></Col>
    </Row>
    <Alert type="info" showIcon message={result.disclaimer} description={result.sources.length ? `来源：${result.sources.join('、')}` : undefined} />
  </Space>
);

export default FinGptHub;
