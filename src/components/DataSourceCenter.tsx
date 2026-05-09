import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Input,
  Modal,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  Upload,
  message
} from 'antd';
import {
  ApiOutlined,
  BarChartOutlined,
  CloudUploadOutlined,
  DatabaseOutlined,
  DeleteOutlined,
  EditOutlined,
  ExperimentOutlined,
  EyeOutlined,
  FileSearchOutlined,
  LinkOutlined,
  ReloadOutlined,
  SaveOutlined,
  SearchOutlined,
  SyncOutlined,
  TagsOutlined
} from '@ant-design/icons';
import {
  DataSourceCreate,
  DataSourceItemRecord,
  DataSourceKeywordCrawlResult,
  DataSourceRecord,
  DataSourceSort,
  DataSourceTagRecord,
  DataSourceType,
  KeywordCrawlFreshness,
  KeywordCrawlProvider,
  TrustLevel,
  agentCrawl,
  createDataSource,
  deleteDataSource,
  deleteDataItem,
  listDataItems,
  listDataSources,
  listDataTags,
  interpretDataItem,
  keywordCrawl,
  syncDataSource,
  updateDataItem,
  uploadDataFile
} from '../services/dataSourceService';
import { AppState } from '../types';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface DataSourceCenterProps {
  appState: AppState;
}

const typeMeta: Record<DataSourceType, { text: string; color: string }> = {
  server_api: { text: '服务器接口', color: 'blue' },
  market_api: { text: '行情 API', color: 'purple' },
  upload: { text: '本地上传', color: 'green' },
  web_page: { text: '网页源', color: 'cyan' },
  agent_crawl: { text: 'Agent 抓取', color: 'orange' },
  manual: { text: '手工资料', color: 'default' }
};

const trustOptions: Array<{ value: TrustLevel; label: string }> = [
  { value: 'internal', label: '内部' },
  { value: 'official', label: '官方' },
  { value: 'media', label: '媒体' },
  { value: 'community', label: '社区' },
  { value: 'unknown', label: '未知' }
];

const isMockInterpretation = (value?: unknown) =>
  typeof value === 'string' && /模型：mock|mock-research-analyst/.test(value);

const savedInterpretationOf = (item?: DataSourceItemRecord | null) => {
  const saved = String(item?.metadata?.ai_interpretation || '');
  return saved && !isMockInterpretation(saved) ? saved : '';
};

const numericMetadata = (value: unknown) => {
  if (typeof value === 'number' && Number.isFinite(value)) return value;
  if (typeof value === 'string' && value.trim() && Number.isFinite(Number(value))) return Number(value);
  return null;
};

const bodyLengthOf = (item?: DataSourceItemRecord | null) => numericMetadata(item?.metadata?.body_length);

const xueqiuInteractionOf = (item?: DataSourceItemRecord | null) => {
  const metrics = [
    ['点赞', numericMetadata(item?.metadata?.like_count)],
    ['评论', numericMetadata(item?.metadata?.reply_count)],
    ['转发', numericMetadata(item?.metadata?.retweet_count)],
    ['浏览', numericMetadata(item?.metadata?.view_count)]
  ].filter(([, value]) => value !== null);
  return metrics.map(([label, value]) => `${label} ${value}`).join(' / ');
};

const firstActionableWarning = (warnings: string[] = []) =>
  warnings.find(item => !/^已使用配置的雪球登录态/.test(item)) || warnings[0];

const keywordProviderLabel: Record<KeywordCrawlProvider, string> = {
  wechat_public: '公众号',
  xueqiu: '雪球'
};

const riskLabel: Record<string, { text: string; color: string }> = {
  low: { text: '低风险', color: 'green' },
  medium: { text: '中风险', color: 'orange' },
  high: { text: '高风险', color: 'red' }
};

const defaultForm: DataSourceCreate = {
  name: '',
  source_type: 'server_api',
  description: '',
  url: '',
  method: 'GET',
  headers: {},
  params: {},
  body: null,
  symbol_param: 'symbol',
  query_param: 'q',
  trust_level: 'internal',
  enabled: true,
  notes: ''
};

const emptyEditForm = {
  title: '',
  symbol: '',
  tags: [] as string[],
  credibility_score: 0.5
};

const DataSourceCenter: React.FC<DataSourceCenterProps> = ({ appState }) => {
  const defaultStock = appState.stocks[0];
  const [sources, setSources] = useState<DataSourceRecord[]>([]);
  const [items, setItems] = useState<DataSourceItemRecord[]>([]);
  const [tags, setTags] = useState<DataSourceTagRecord[]>([]);
  const [selectedItem, setSelectedItem] = useState<DataSourceItemRecord | null>(null);
  const [editingItem, setEditingItem] = useState<DataSourceItemRecord | null>(null);
  const [loading, setLoading] = useState(false);
  const [syncingId, setSyncingId] = useState<string | null>(null);
  const [savingItem, setSavingItem] = useState(false);
  const [interpretingItem, setInterpretingItem] = useState<DataSourceItemRecord | null>(null);
  const [interpretationText, setInterpretationText] = useState('');
  const [interpretationLoading, setInterpretationLoading] = useState(false);
  const [savingInterpretation, setSavingInterpretation] = useState(false);
  const [keywordCrawling, setKeywordCrawling] = useState(false);
  const [lastKeywordResult, setLastKeywordResult] = useState<DataSourceKeywordCrawlResult | null>(null);
  const [form, setForm] = useState<DataSourceCreate>(defaultForm);
  const [headersJson, setHeadersJson] = useState('{}');
  const [paramsJson, setParamsJson] = useState('{}');
  const [bodyJson, setBodyJson] = useState('');
  const [symbolFilter, setSymbolFilter] = useState(appState.stocks[0]?.symbol || '');
  const [queryFilter, setQueryFilter] = useState('');
  const [sourceTypeFilter, setSourceTypeFilter] = useState<DataSourceType | undefined>();
  const [sourceIdFilter, setSourceIdFilter] = useState<string | undefined>();
  const [tagFilter, setTagFilter] = useState<string | undefined>();
  const [itemSort, setItemSort] = useState<DataSourceSort>('time_desc');
  const [uploadMeta, setUploadMeta] = useState({ symbol: appState.stocks[0]?.symbol || '', title: '', tags: '' });
  const [crawlUrl, setCrawlUrl] = useState('');
  const [crawlLimit, setCrawlLimit] = useState(10);
  const [currentResultNotice, setCurrentResultNotice] = useState('');
  const [keywordCrawlForm, setKeywordCrawlForm] = useState({
    provider: 'wechat_public' as KeywordCrawlProvider,
    keyword: defaultStock?.name || defaultStock?.symbol || '',
    symbol: defaultStock?.symbol || '',
    limit: 10,
    sort: 'time_desc' as DataSourceSort,
    freshness: 'week' as KeywordCrawlFreshness
  });
  const [editForm, setEditForm] = useState(emptyEditForm);

  const stats = useMemo(() => {
    const active = sources.filter(source => source.status === 'active').length;
    const errors = sources.filter(source => source.status === 'error').length;
    const stored = sources.reduce((sum, source) => sum + source.items_count, 0);
    const local = items.filter(item => item.source_type === 'upload').length;
    const remote = items.filter(item => item.source_type !== 'upload').length;
    return { active, errors, stored, local, remote, tags: tags.length };
  }, [items, sources, tags]);

  const typeDistribution = useMemo(() => {
    return Object.entries(typeMeta).map(([type, meta]) => ({
      type: type as DataSourceType,
      ...meta,
      count: items.filter(item => item.source_type === type).length
    })).filter(item => item.count > 0);
  }, [items]);

  const loadData = async (overrides?: Partial<{
    symbol: string;
    query: string;
    sourceType: DataSourceType | undefined;
    sourceId: string | undefined;
    tag: string | undefined;
    sort: DataSourceSort;
  }>) => {
    setCurrentResultNotice('');
    const nextSymbol = overrides && 'symbol' in overrides ? overrides.symbol : symbolFilter;
    const nextQuery = overrides && 'query' in overrides ? overrides.query : queryFilter;
    const nextSourceType = overrides && 'sourceType' in overrides ? overrides.sourceType : sourceTypeFilter;
    const nextSourceId = overrides && 'sourceId' in overrides ? overrides.sourceId : sourceIdFilter;
    const nextTag = overrides && 'tag' in overrides ? overrides.tag : tagFilter;
    const nextSort = overrides && 'sort' in overrides ? overrides.sort : itemSort;
    setLoading(true);
    try {
      const [nextSources, nextItems, nextTags] = await Promise.all([
        listDataSources(),
        listDataItems({
          symbol: nextSymbol || undefined,
          query: nextQuery || undefined,
          source_type: nextSourceType,
          source_id: nextSourceId,
          tag: nextTag,
          limit: 80,
          sort: nextSort
        }),
        listDataTags()
      ]);
      setSources(nextSources);
      setItems(nextItems);
      setTags(nextTags);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '数据源中心连接失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
  }, []);

  const parseJsonObject = (value: string, label: string): Record<string, any> => {
    if (!value.trim()) {
      return {};
    }
    const parsed = JSON.parse(value);
    if (!parsed || Array.isArray(parsed) || typeof parsed !== 'object') {
      throw new Error(`${label} 必须是 JSON 对象`);
    }
    return parsed;
  };

  const handleCreate = async () => {
    try {
      const payload: DataSourceCreate = {
        ...form,
        headers: parseJsonObject(headersJson, 'Headers'),
        params: parseJsonObject(paramsJson, 'Params'),
        body: bodyJson.trim() ? parseJsonObject(bodyJson, 'Body') : null
      };
      await createDataSource(payload);
      setForm(defaultForm);
      setHeadersJson('{}');
      setParamsJson('{}');
      setBodyJson('');
      await loadData();
      message.success('数据源已保存');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error.message || '保存失败');
    }
  };

  const handleSync = async (source: DataSourceRecord) => {
    setSyncingId(source.id);
    try {
      const result = await syncDataSource(source.id, {
        symbol: symbolFilter || undefined,
        query: queryFilter || undefined,
        limit: 20
      });
      await loadData();
      message.success(`已导入 ${result.imported_count} 条资料`);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '同步失败');
      await loadData();
    } finally {
      setSyncingId(null);
    }
  };

  const handleDelete = async (source: DataSourceRecord) => {
    await deleteDataSource(source.id);
    await loadData();
    message.success('数据源已删除');
  };

  const handleUpload = async (file: File) => {
    try {
      await uploadDataFile(file, uploadMeta);
      setUploadMeta(prev => ({ ...prev, title: '' }));
      await loadData();
      message.success('文件已进入资料库');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '上传失败');
    }
  };

  const handleCrawl = async () => {
    try {
      const result = await agentCrawl({
        url: crawlUrl,
        symbol: symbolFilter || undefined,
        query: queryFilter || undefined,
        limit: crawlLimit
      });
      const [nextSources, nextTags] = await Promise.all([
        listDataSources(),
        listDataTags()
      ]);
      setCrawlUrl('');
      setSources(nextSources);
      setTags(nextTags);
      setItems(result.items);
      setCurrentResultNotice(`当前列表显示本次网页抓取结果：${result.imported_count} 条。点击“检索”可查看历史匹配资料。`);
      message.success(`已导入/更新 ${result.imported_count} 条网页资料`);
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '抓取失败');
    }
  };

  const handleKeywordCrawl = async () => {
    if (!keywordCrawlForm.keyword.trim()) {
      message.warning('请输入关键词');
      return;
    }
    setKeywordCrawling(true);
    try {
      const result = await keywordCrawl({
        provider: keywordCrawlForm.provider,
        keyword: keywordCrawlForm.keyword.trim(),
        symbol: keywordCrawlForm.symbol || undefined,
        limit: keywordCrawlForm.limit,
        sort: keywordCrawlForm.sort,
        freshness: keywordCrawlForm.freshness
      });
      setLastKeywordResult(result);

      setSymbolFilter(keywordCrawlForm.symbol || '');
      setQueryFilter(keywordCrawlForm.keyword.trim());
      setSourceTypeFilter('agent_crawl');
      setSourceIdFilter(undefined);
      setTagFilter(undefined);
      setItemSort(keywordCrawlForm.sort);
      const [nextSources, nextTags] = await Promise.all([
        listDataSources(),
        listDataTags()
      ]);
      setSources(nextSources);
      setTags(nextTags);
      setItems(result.items);
      setCurrentResultNotice(`当前列表显示本次关键词抓取结果：${result.imported_count} 条。点击“检索”可查看历史匹配资料。`);
      if (result.imported_count > 0) {
        message.success(result.fallback_used
          ? `已按源策略降级到${keywordProviderLabel[result.effective_provider]}，导入/更新 ${result.imported_count} 条资料`
          : `已抓取/更新 ${result.imported_count} 条资料`);
      } else {
        message.warning(firstActionableWarning(result.warnings) || '未抓取到可用资料');
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '关键词抓取失败');
    } finally {
      setKeywordCrawling(false);
    }
  };

  const startEditItem = (item: DataSourceItemRecord) => {
    setEditingItem(item);
    setEditForm({
      title: item.title,
      symbol: item.symbol || '',
      tags: item.tags || [],
      credibility_score: item.credibility_score
    });
  };

  const handleSaveItem = async () => {
    if (!editingItem) {
      return;
    }
    setSavingItem(true);
    try {
      const updated = await updateDataItem(editingItem.id, {
        title: editForm.title,
        symbol: editForm.symbol || null,
        tags: editForm.tags,
        credibility_score: editForm.credibility_score
      });
      setItems(prev => prev.map(item => item.id === updated.id ? updated : item));
      if (selectedItem?.id === updated.id) {
        setSelectedItem(updated);
      }
      setEditingItem(null);
      await loadData();
      message.success('资料标签已更新');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存失败');
    } finally {
      setSavingItem(false);
    }
  };

  const handleDeleteItem = async (item: DataSourceItemRecord) => {
    await deleteDataItem(item.id);
    if (selectedItem?.id === item.id) {
      setSelectedItem(null);
    }
    await loadData();
    message.success('资料已删除');
  };

  const generateInterpretation = async (item?: DataSourceItemRecord) => {
    const target = item || interpretingItem;
    if (!target) {
      return;
    }
    setInterpretationLoading(true);
    try {
      const result = await interpretDataItem(target.id, true);
      setInterpretationText(result.interpretation);
      setItems(prev => prev.map(current => current.id === result.item.id ? result.item : current));
      if (selectedItem?.id === result.item.id) {
        setSelectedItem(result.item);
      }
      setInterpretingItem(result.item);
    } catch (error: any) {
      if (error?.response?.status === 409) {
        setInterpretingItem(null);
        setInterpretationText('');
        Modal.warning({
          title: '请先配置云端模型',
          content: error?.response?.data?.detail || '当前仍是 mock 模式，不能执行真实 AI 解读。请到 FinGPT → 模型配置 填写云模型 API Key 后再解读。'
        });
      } else {
        message.error(error?.response?.data?.detail || 'AI 解读失败');
      }
    } finally {
      setInterpretationLoading(false);
    }
  };

  const openInterpretation = async (item: DataSourceItemRecord) => {
    const saved = savedInterpretationOf(item);
    setInterpretingItem(item);
    setInterpretationText(saved);
    if (!saved) {
      await generateInterpretation(item);
    }
  };

  const handleSaveInterpretation = async () => {
    if (!interpretingItem) {
      return;
    }
    setSavingInterpretation(true);
    try {
      const updated = await updateDataItem(interpretingItem.id, {
        ai_interpretation: interpretationText
      });
      setItems(prev => prev.map(item => item.id === updated.id ? updated : item));
      if (selectedItem?.id === updated.id) {
        setSelectedItem(updated);
      }
      setInterpretingItem(updated);
      await loadData();
      message.success('AI 解读已保存');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '保存解读失败');
    } finally {
      setSavingInterpretation(false);
    }
  };

  const resetFilters = async () => {
    setSymbolFilter('');
    setQueryFilter('');
    setSourceTypeFilter(undefined);
    setSourceIdFilter(undefined);
    setTagFilter(undefined);
    setItemSort('time_desc');
    await loadData({ symbol: '', query: '', sourceType: undefined, sourceId: undefined, tag: undefined, sort: 'time_desc' });
  };

  const stockOptions = appState.stocks.map(stock => ({
    value: stock.symbol,
    label: `${stock.name} (${stock.symbol})`
  }));

  const handleKeywordSymbolChange = (value?: string) => {
    const stock = appState.stocks.find(item => item.symbol === value);
    const previousStock = appState.stocks.find(item => item.symbol === keywordCrawlForm.symbol);
    setKeywordCrawlForm(prev => {
      const currentKeyword = prev.keyword.trim();
      const shouldUseName =
        prev.provider === 'wechat_public' &&
        stock &&
        (!currentKeyword || currentKeyword === prev.symbol || currentKeyword === previousStock?.name);
      return {
        ...prev,
        symbol: value || '',
        keyword: shouldUseName ? stock.name : prev.keyword
      };
    });
  };

  const sourceOptions = sources.map(source => ({
    value: source.id,
    label: source.name
  }));

  const tagOptions = tags.map(item => ({
    value: item.tag,
    label: `${item.tag} (${item.count})`
  }));

  return (
    <div style={{ padding: 16 }}>
      <Space direction="vertical" size={16} style={{ width: '100%' }}>
        <Card>
          <Row gutter={[16, 16]} align="middle">
            <Col xs={24} lg={12}>
              <Title level={3} style={{ margin: 0 }}>数据源中心</Title>
              <Text type="secondary">统一管理本地文件、远端资料、网页抓取和 Agent 证据标签</Text>
            </Col>
            <Col xs={24} lg={12}>
              <Row gutter={12}>
                <Col span={6}><Statistic title="活跃源" value={stats.active} /></Col>
                <Col span={6}><Statistic title="资料条目" value={stats.stored} /></Col>
                <Col span={6}><Statistic title="标签" value={stats.tags} /></Col>
                <Col span={6}><Statistic title="异常源" value={stats.errors} /></Col>
              </Row>
            </Col>
          </Row>
        </Card>

        <Row gutter={[16, 16]}>
          <Col xs={24} xl={8}>
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Card title={<Space><ApiOutlined />注册数据源</Space>}>
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Input
                    value={form.name}
                    onChange={event => setForm(prev => ({ ...prev, name: event.target.value }))}
                    placeholder="名称，例如：自有投研接口"
                  />
                  <Select
                    value={form.source_type}
                    onChange={value => setForm(prev => ({ ...prev, source_type: value }))}
                    options={[
                      { value: 'server_api', label: '服务器接口' },
                      { value: 'market_api', label: '行情 API' },
                      { value: 'web_page', label: '网页源' },
                      { value: 'manual', label: '手工资料' }
                    ]}
                  />
                  <Input
                    value={form.url}
                    onChange={event => setForm(prev => ({ ...prev, url: event.target.value }))}
                    placeholder="URL，可用 {symbol} / {query}"
                  />
                  <Row gutter={8}>
                    <Col span={12}>
                      <Select
                        value={form.method}
                        onChange={value => setForm(prev => ({ ...prev, method: value }))}
                        options={[
                          { value: 'GET', label: 'GET' },
                          { value: 'POST', label: 'POST' }
                        ]}
                      />
                    </Col>
                    <Col span={12}>
                      <Select
                        value={form.trust_level}
                        onChange={value => setForm(prev => ({ ...prev, trust_level: value }))}
                        options={trustOptions}
                      />
                    </Col>
                  </Row>
                  <Row gutter={8}>
                    <Col span={12}>
                      <Input
                        value={form.symbol_param}
                        onChange={event => setForm(prev => ({ ...prev, symbol_param: event.target.value }))}
                        placeholder="symbol 参数名"
                      />
                    </Col>
                    <Col span={12}>
                      <Input
                        value={form.query_param}
                        onChange={event => setForm(prev => ({ ...prev, query_param: event.target.value }))}
                        placeholder="query 参数名"
                      />
                    </Col>
                  </Row>
                  <TextArea
                    rows={2}
                    value={form.description}
                    onChange={event => setForm(prev => ({ ...prev, description: event.target.value }))}
                    placeholder="说明"
                  />
                  <TextArea rows={3} value={headersJson} onChange={event => setHeadersJson(event.target.value)} placeholder='Headers JSON，例如 {"Authorization":"Bearer ..."}' />
                  <TextArea rows={3} value={paramsJson} onChange={event => setParamsJson(event.target.value)} placeholder='Params JSON，例如 {"apikey":"..."}' />
                  <TextArea rows={3} value={bodyJson} onChange={event => setBodyJson(event.target.value)} placeholder="POST Body JSON" />
                  <Button type="primary" block icon={<DatabaseOutlined />} onClick={handleCreate}>
                    保存数据源
                  </Button>
                </Space>
              </Card>

              <Card title={<Space><CloudUploadOutlined />本地上传</Space>}>
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Select
                    allowClear
                    value={uploadMeta.symbol || undefined}
                    onChange={value => setUploadMeta(prev => ({ ...prev, symbol: value || '' }))}
                    options={stockOptions}
                    placeholder="关联标的"
                  />
                  <Input value={uploadMeta.title} onChange={event => setUploadMeta(prev => ({ ...prev, title: event.target.value }))} placeholder="资料标题" />
                  <Input value={uploadMeta.tags} onChange={event => setUploadMeta(prev => ({ ...prev, tags: event.target.value }))} placeholder="标签，逗号分隔" />
                  <Upload
                    showUploadList={false}
                    beforeUpload={file => {
                      handleUpload(file as File);
                      return false;
                    }}
                  >
                    <Button block icon={<CloudUploadOutlined />}>选择文件入库</Button>
                  </Upload>
                </Space>
              </Card>

              <Card title={<Space><LinkOutlined />Agent 抓取网页</Space>}>
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Input value={crawlUrl} onChange={event => setCrawlUrl(event.target.value)} placeholder="网页 URL" />
                  <Input
                    type="number"
                    min={1}
                    max={100}
                    value={crawlLimit}
                    onChange={event => setCrawlLimit(Math.max(1, Math.min(100, Number(event.target.value || 10))))}
                    placeholder="抓取数量"
                  />
                  <Alert
                    type="info"
                    showIcon
                    message="单篇文章 URL 会入库 1 条；如果粘贴雪球/公众号搜索结果页，会按抓取数量拆成多条帖子。批量抓取更推荐使用下方关键词抓取。"
                  />
                  <Button block icon={<LinkOutlined />} onClick={handleCrawl} disabled={!crawlUrl}>
                    抓取并入库
                  </Button>
                </Space>
              </Card>

              <Card title={<Space><SearchOutlined />关键词抓取</Space>}>
                <Space direction="vertical" size={10} style={{ width: '100%' }}>
                  <Select
                    value={keywordCrawlForm.provider}
                    onChange={value => setKeywordCrawlForm(prev => {
                      const stock = appState.stocks.find(item => item.symbol === prev.symbol);
                      const currentKeyword = prev.keyword.trim();
                      const shouldUseName = value === 'wechat_public' && stock && (!currentKeyword || currentKeyword === stock.symbol);
                      return { ...prev, provider: value, keyword: shouldUseName ? stock.name : prev.keyword };
                    })}
                    options={[
                      { value: 'wechat_public', label: '公众号' },
                      { value: 'xueqiu', label: '雪球' }
                    ]}
                  />
                  <Select
                    value={keywordCrawlForm.sort}
                    onChange={value => setKeywordCrawlForm(prev => ({ ...prev, sort: value }))}
                    options={[
                      { value: 'time_desc', label: '时间从新到旧' },
                      { value: 'relevance', label: '综合排序' }
                    ]}
                  />
                  <Select
                    value={keywordCrawlForm.freshness}
                    onChange={value => setKeywordCrawlForm(prev => ({ ...prev, freshness: value }))}
                    options={[
                      { value: 'day', label: '最近 1 天' },
                      { value: 'week', label: '最近 7 天' },
                      { value: 'month', label: '最近 30 天' },
                      { value: 'year', label: '最近 1 年' },
                      { value: 'any', label: '全部时间' }
                    ]}
                  />
                  <Select
                    allowClear
                    value={keywordCrawlForm.symbol || undefined}
                    onChange={handleKeywordSymbolChange}
                    options={stockOptions}
                    placeholder="关联标的"
                  />
                  <Input
                    value={keywordCrawlForm.keyword}
                    onChange={event => setKeywordCrawlForm(prev => ({ ...prev, keyword: event.target.value }))}
                    placeholder="关键词，例如 TSLA / 特斯拉 / FSD"
                  />
                  <Input
                    type="number"
                    min={1}
                    max={30}
                    value={keywordCrawlForm.limit}
                    onChange={event => setKeywordCrawlForm(prev => ({
                      ...prev,
                      limit: Math.max(1, Math.min(30, Number(event.target.value || 10)))
                    }))}
                    placeholder="抓取数量"
                  />
                  <Alert
                    type={keywordCrawlForm.provider === 'xueqiu' ? 'warning' : 'info'}
                    showIcon
                    message={keywordCrawlForm.provider === 'xueqiu'
                      ? '可在后端 .env 配置 DEEPFOCUS_XUEQIU_COOKIE 或 XUEQIU_TOKEN 使用自有雪球登录态；遇到 WAF/验证码时不会绕过访问控制，系统会记录阻断原因并自动改用公众号公开搜索补资料。'
                      : '公众号默认抓最近 7 天，建议用中文公司名或事件词；英文代码会偏历史结果。'}
                  />
                  {lastKeywordResult && (
                    <Alert
                      type={lastKeywordResult.fallback_used ? 'warning' : lastKeywordResult.imported_count > 0 ? 'success' : 'info'}
                      showIcon
                      message={`上次策略：${keywordProviderLabel[lastKeywordResult.provider]} → ${keywordProviderLabel[lastKeywordResult.effective_provider]} · 导入 ${lastKeywordResult.imported_count} 条`}
                      description={
                        <Space direction="vertical" size={6}>
                          <Space wrap>
                            <Tag color={riskLabel[lastKeywordResult.provider_policy.risk_level]?.color || 'default'}>
                              {riskLabel[lastKeywordResult.provider_policy.risk_level]?.text || lastKeywordResult.provider_policy.risk_level}
                            </Tag>
                            <Tag color="blue">健康 {lastKeywordResult.provider_policy.health_score}</Tag>
                            <Tag>{lastKeywordResult.provider_policy.auth_mode}</Tag>
                            {lastKeywordResult.provider_policy.configured === false && <Tag color="gold">未配置登录态</Tag>}
                            {lastKeywordResult.fallback_used && <Tag color="orange">已降级</Tag>}
                          </Space>
                          {firstActionableWarning(lastKeywordResult.warnings) && (
                            <Text type="secondary">{firstActionableWarning(lastKeywordResult.warnings)}</Text>
                          )}
                        </Space>
                      }
                    />
                  )}
                  <Button
                    block
                    type="primary"
                    icon={<SearchOutlined />}
                    loading={keywordCrawling}
                    onClick={handleKeywordCrawl}
                  >
                    按关键词抓取
                  </Button>
                </Space>
              </Card>
            </Space>
          </Col>

          <Col xs={24} xl={16}>
            <Space direction="vertical" size={16} style={{ width: '100%' }}>
              <Card
                title={<Space><DatabaseOutlined />数据源</Space>}
                extra={<Button icon={<ReloadOutlined />} loading={loading} onClick={() => loadData()}>刷新</Button>}
              >
                <Table
                  size="small"
                  rowKey="id"
                  dataSource={sources}
                  pagination={{ pageSize: 6 }}
                  columns={[
                    {
                      title: '名称',
                      dataIndex: 'name',
                      render: (value, record) => (
                        <Space direction="vertical" size={0}>
                          <Text strong>{value}</Text>
                          <Text type="secondary">{record.description || record.config?.url || '-'}</Text>
                        </Space>
                      )
                    },
                    {
                      title: '类型',
                      dataIndex: 'source_type',
                      width: 110,
                      render: value => <Tag color={typeMeta[value as DataSourceType]?.color}>{typeMeta[value as DataSourceType]?.text || value}</Tag>
                    },
                    {
                      title: '状态',
                      dataIndex: 'status',
                      width: 90,
                      render: value => <Tag color={value === 'active' ? 'green' : value === 'error' ? 'red' : 'default'}>{value}</Tag>
                    },
                    {
                      title: '条目',
                      dataIndex: 'items_count',
                      width: 80
                    },
                    {
                      title: '同步',
                      width: 150,
                      render: (_, record) => (
                        <Space>
                          <Button size="small" icon={<SyncOutlined />} loading={syncingId === record.id} onClick={() => handleSync(record)}>
                            同步
                          </Button>
                          <Button size="small" danger icon={<DeleteOutlined />} onClick={() => handleDelete(record)} />
                        </Space>
                      )
                    }
                  ]}
                  expandable={{
                    expandedRowRender: record => (
                      <Descriptions size="small" column={1}>
                        <Descriptions.Item label="URL">{record.config?.url || '-'}</Descriptions.Item>
                        <Descriptions.Item label="最近同步">{record.last_sync_at ? new Date(record.last_sync_at).toLocaleString() : '-'}</Descriptions.Item>
                        <Descriptions.Item label="错误">{record.last_error || '-'}</Descriptions.Item>
                      </Descriptions>
                    )
                  }}
                />
              </Card>

              <Card title={<Space><FileSearchOutlined />资料资产管理</Space>}>
                <Space direction="vertical" size={12} style={{ width: '100%' }}>
                  <Row gutter={[12, 12]}>
                    <Col xs={24} md={8}>
                      <Alert type="success" showIcon message={`本地文件 ${stats.local} 份`} />
                    </Col>
                    <Col xs={24} md={8}>
                      <Alert type="info" showIcon message={`远端/抓取 ${stats.remote} 份`} />
                    </Col>
                    <Col xs={24} md={8}>
                      <Alert type="warning" showIcon message={`可用标签 ${stats.tags} 个`} />
                    </Col>
                  </Row>

                  {typeDistribution.length > 0 && (
                    <Space wrap>
                      <Text type="secondary"><BarChartOutlined /> 来源分布</Text>
                      {typeDistribution.map(item => (
                        <Tag key={item.type} color={item.color}>{item.text} {item.count}</Tag>
                      ))}
                    </Space>
                  )}

                  {tags.length > 0 && (
                    <Space wrap>
                      <Text type="secondary"><TagsOutlined /> 标签</Text>
                      {tags.slice(0, 20).map(item => (
                        <Tag
                          key={item.tag}
                          color={tagFilter === item.tag ? 'blue' : 'default'}
                          style={{ cursor: 'pointer' }}
                          onClick={() => {
                            setTagFilter(item.tag);
                            loadData({ tag: item.tag });
                          }}
                        >
                          {item.tag} {item.count}
                        </Tag>
                      ))}
                    </Space>
                  )}

                  <Row gutter={8}>
                    <Col xs={24} md={6}>
                      <Select
                        allowClear
                        value={symbolFilter || undefined}
                        onChange={value => setSymbolFilter(value || '')}
                        options={stockOptions}
                        placeholder="标的"
                      />
                    </Col>
                    <Col xs={24} md={6}>
                      <Select
                        allowClear
                        value={sourceTypeFilter}
                        onChange={value => setSourceTypeFilter(value)}
                        options={Object.entries(typeMeta).map(([value, meta]) => ({ value, label: meta.text }))}
                        placeholder="资料类型"
                      />
                    </Col>
                    <Col xs={24} md={6}>
                      <Select
                        allowClear
                        value={sourceIdFilter}
                        onChange={value => setSourceIdFilter(value)}
                        options={sourceOptions}
                        placeholder="来源"
                      />
                    </Col>
                    <Col xs={24} md={6}>
                      <Select
                        allowClear
                        showSearch
                        value={tagFilter}
                        onChange={value => setTagFilter(value)}
                        options={tagOptions}
                        placeholder="Tag"
                      />
                    </Col>
                    <Col xs={24} md={6}>
                      <Select
                        value={itemSort}
                        onChange={value => setItemSort(value)}
                        options={[
                          { value: 'time_desc', label: '时间从新到旧' },
                          { value: 'relevance', label: '综合排序' }
                        ]}
                      />
                    </Col>
                    <Col xs={24} md={12}>
                      <Input value={queryFilter} onChange={event => setQueryFilter(event.target.value)} placeholder="关键词" />
                    </Col>
                    <Col xs={24} md={3}>
                      <Button block icon={<ReloadOutlined />} loading={loading} onClick={() => loadData()}>检索</Button>
                    </Col>
                    <Col xs={24} md={3}>
                      <Button block onClick={resetFilters}>清空</Button>
                    </Col>
                  </Row>
                  {currentResultNotice && (
                    <Alert
                      type="success"
                      showIcon
                      message={currentResultNotice}
                      action={<Button size="small" onClick={() => loadData()}>查看历史匹配</Button>}
                    />
                  )}
                  {items.length === 0 ? (
                    <Alert type="info" showIcon message="暂无匹配资料" />
                  ) : (
                    <Table
                      size="small"
                      rowKey="id"
                      dataSource={items}
                      pagination={{ pageSize: 8 }}
                      columns={[
                        {
                          title: '资料',
                          dataIndex: 'title',
                          render: (value, record) => (
                            <Space direction="vertical" size={2} style={{ maxWidth: 520 }}>
                              {record.url ? (
                                <a href={record.url} target="_blank" rel="noreferrer" style={{ fontWeight: 600 }}>
                                  {value}
                                </a>
                              ) : (
                                <Text strong>{value}</Text>
                              )}
                              <Paragraph ellipsis={{ rows: 2 }} style={{ margin: 0 }}>{record.text_preview}</Paragraph>
                              <Space wrap>
                                <Tag color={typeMeta[record.source_type]?.color}>{typeMeta[record.source_type]?.text}</Tag>
                                {record.symbol && <Tag>{record.symbol}</Tag>}
                                {record.metadata?.provider === 'xueqiu' && bodyLengthOf(record) !== null && (
                                  <Tag color="volcano">正文 {bodyLengthOf(record)} 字</Tag>
                                )}
                                {record.metadata?.author_name && <Tag color="blue">{record.metadata.author_name}</Tag>}
                                {savedInterpretationOf(record) && <Tag color="geekblue">已解读</Tag>}
                                {record.tags.map(tag => <Tag key={tag}>{tag}</Tag>)}
                              </Space>
                            </Space>
                          )
                        },
                        {
                          title: '来源',
                          dataIndex: 'source_name',
                          width: 150,
                          render: (value, record) => (
                            <Space direction="vertical" size={0}>
                              <Text>{value}</Text>
                              <Text type="secondary">{new Date(record.collected_at).toLocaleString()}</Text>
                            </Space>
                          )
                        },
                        {
                          title: '可信度',
                          dataIndex: 'credibility_score',
                          width: 90,
                          render: value => <Tag color="gold">{Math.round(value * 100)}%</Tag>
                        },
                        {
                          title: '管理',
                          width: 270,
                          render: (_, record) => (
                            <Space wrap size={4}>
                              <Button size="small" icon={<EyeOutlined />} onClick={() => setSelectedItem(record)}>查看</Button>
                              <Button
                                size="small"
                                icon={<ExperimentOutlined />}
                                loading={interpretationLoading && interpretingItem?.id === record.id}
                                onClick={() => openInterpretation(record)}
                              >
                                AI 解读
                              </Button>
                              <Button size="small" icon={<EditOutlined />} onClick={() => startEditItem(record)}>Tag</Button>
                              <Popconfirm title="删除这份资料？" okText="删除" cancelText="取消" onConfirm={() => handleDeleteItem(record)}>
                                <Button size="small" danger icon={<DeleteOutlined />} />
                              </Popconfirm>
                            </Space>
                          )
                        }
                      ]}
                    />
                  )}
                </Space>
              </Card>
            </Space>
          </Col>
        </Row>
      </Space>

      <Modal
        title={selectedItem?.title}
        open={Boolean(selectedItem)}
        onCancel={() => setSelectedItem(null)}
        footer={null}
        width={820}
      >
        {selectedItem && (
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Descriptions size="small" column={1}>
              <Descriptions.Item label="来源">{selectedItem.source_name}</Descriptions.Item>
              <Descriptions.Item label="类型">{typeMeta[selectedItem.source_type]?.text}</Descriptions.Item>
              <Descriptions.Item label="标的">{selectedItem.symbol || '-'}</Descriptions.Item>
              {selectedItem.metadata?.provider === 'xueqiu' && (
                <>
                  <Descriptions.Item label="作者">{selectedItem.metadata.author_name || '-'}</Descriptions.Item>
                  <Descriptions.Item label="发布时间">{selectedItem.metadata.published_at ? new Date(selectedItem.metadata.published_at).toLocaleString() : '-'}</Descriptions.Item>
                  <Descriptions.Item label="正文长度">{bodyLengthOf(selectedItem) !== null ? `${bodyLengthOf(selectedItem)} 字` : '-'}</Descriptions.Item>
                  <Descriptions.Item label="互动">{xueqiuInteractionOf(selectedItem) || '-'}</Descriptions.Item>
                </>
              )}
              <Descriptions.Item label="Tags">
                <Space wrap>
                  {selectedItem.tags.length ? selectedItem.tags.map(tag => <Tag key={tag}>{tag}</Tag>) : '-'}
                </Space>
              </Descriptions.Item>
              <Descriptions.Item label="URL">
                {selectedItem.url ? <a href={selectedItem.url} target="_blank" rel="noreferrer">{selectedItem.url}</a> : '-'}
              </Descriptions.Item>
            </Descriptions>
            <Space>
              <Button icon={<ExperimentOutlined />} onClick={() => openInterpretation(selectedItem)}>AI 解读</Button>
              <Button icon={<EditOutlined />} onClick={() => startEditItem(selectedItem)}>编辑标签</Button>
              <Popconfirm title="删除这份资料？" okText="删除" cancelText="取消" onConfirm={() => handleDeleteItem(selectedItem)}>
                <Button danger icon={<DeleteOutlined />}>删除资料</Button>
              </Popconfirm>
            </Space>
            {savedInterpretationOf(selectedItem) && (
              <Card size="small" title="AI 解读">
                <Paragraph style={{ whiteSpace: 'pre-wrap', marginBottom: 0 }}>
                  {savedInterpretationOf(selectedItem)}
                </Paragraph>
              </Card>
            )}
            <pre style={{ whiteSpace: 'pre-wrap', maxHeight: 480, overflow: 'auto', background: '#fafafa', padding: 12 }}>
              {selectedItem.text}
            </pre>
          </Space>
        )}
      </Modal>

      <Modal
        title={interpretingItem ? `AI 解读：${interpretingItem.title}` : 'AI 解读'}
        open={Boolean(interpretingItem)}
        onCancel={() => setInterpretingItem(null)}
        width={860}
        footer={[
          <Button key="cancel" onClick={() => setInterpretingItem(null)}>关闭</Button>,
          <Button
            key="regen"
            icon={<ExperimentOutlined />}
            loading={interpretationLoading}
            onClick={() => generateInterpretation()}
          >
            重新解读
          </Button>,
          <Button
            key="save"
            type="primary"
            icon={<SaveOutlined />}
            loading={savingInterpretation}
            onClick={handleSaveInterpretation}
          >
            保存解读
          </Button>
        ]}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            type="info"
            showIcon
            message="这里复用 FinGPT 的文章/研报解读能力；生成结果可编辑，保存后会写回这条资料。"
          />
          <TextArea
            rows={16}
            value={interpretationText}
            onChange={event => setInterpretationText(event.target.value)}
            placeholder={interpretationLoading ? 'AI 正在解读...' : '点击重新解读生成内容，或直接输入你的解读。'}
          />
        </Space>
      </Modal>

      <Modal
        title="编辑资料标签"
        open={Boolean(editingItem)}
        onCancel={() => setEditingItem(null)}
        onOk={handleSaveItem}
        okText="保存"
        cancelText="取消"
        confirmLoading={savingItem}
        width={620}
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Input
            value={editForm.title}
            onChange={event => setEditForm(prev => ({ ...prev, title: event.target.value }))}
            placeholder="资料标题"
          />
          <Select
            allowClear
            showSearch
            value={editForm.symbol || undefined}
            onChange={value => setEditForm(prev => ({ ...prev, symbol: value || '' }))}
            options={stockOptions}
            placeholder="关联标的"
          />
          <Select
            mode="tags"
            value={editForm.tags}
            onChange={value => setEditForm(prev => ({ ...prev, tags: value }))}
            options={tags.map(item => ({ value: item.tag, label: item.tag }))}
            placeholder="输入或选择 tag"
          />
          <Input
            type="number"
            min={0}
            max={100}
            value={Math.round(editForm.credibility_score * 100)}
            onChange={event => setEditForm(prev => ({
              ...prev,
              credibility_score: Math.max(0, Math.min(1, Number(event.target.value || 0) / 100))
            }))}
            placeholder="可信度 0-100"
          />
          {editingItem && (
            <Alert
              type="info"
              showIcon
              message={`${editingItem.source_name} · ${typeMeta[editingItem.source_type]?.text} · ${new Date(editingItem.collected_at).toLocaleString()}`}
            />
          )}
        </Space>
      </Modal>
    </div>
  );
};

export default DataSourceCenter;
