import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  Popconfirm,
  Row,
  Select,
  Space,
  Spin,
  Statistic,
  Tag,
  Typography,
  message
} from 'antd';
import { SettingOutlined, DeleteOutlined } from '@ant-design/icons';
import {
  ModelConfig,
  getModelConfig,
  updateModelConfig
} from '../../services/researchService';
import { memoryCount, memoryPreview, clearMemory } from '../../utils/conversationMemory';
import {
  listDecisions,
  setOutcome,
  clearDecisions,
  computeCalibration,
  decisionActionLabel,
  DecisionRecord,
  DecisionOutcome,
} from '../../utils/decisionJournal';

const { Paragraph, Text } = Typography;

interface ModelConfigDraft {
  provider: ModelConfig['provider'];
  model: string;
  base_url: string;
  api_key: string;
  temperature: number;
}

interface ModelSettingsPanelProps {
  initialConfig?: ModelConfig | null;
  onLoaded?: (config: ModelConfig) => void;
  onSaved?: (config: ModelConfig) => void;
}

const providerOptions = [
  { value: 'mock', label: 'Mock 本地演示' },
  { value: 'openai', label: 'OpenAI' },
  { value: 'minimax', label: 'MiniMax' },
  { value: 'openai-compatible', label: 'OpenAI-compatible' },
  { value: 'cloud', label: 'Cloud compatible' }
];

const modelOptions: Record<string, string[]> = {
  mock: ['mock-research-analyst'],
  openai: ['gpt-4o-mini', 'gpt-4.1-mini', 'gpt-4.1'],
  minimax: ['MiniMax-M3'],
  'openai-compatible': ['gpt-4o-mini', 'deepseek-chat', 'qwen-plus', 'moonshot-v1-8k'],
  cloud: ['gpt-4o-mini', 'deepseek-chat', 'qwen-plus', 'moonshot-v1-8k']
};

const providerDefaults: Record<string, { model: string; base_url: string }> = {
  mock: { model: 'mock-research-analyst', base_url: '' },
  openai: { model: 'gpt-4o-mini', base_url: '' },
  minimax: { model: 'MiniMax-M3', base_url: 'https://api.minimaxi.com/v1' },
  'openai-compatible': { model: 'gpt-4o-mini', base_url: '' },
  cloud: { model: 'gpt-4o-mini', base_url: '' }
};

const emptyDraft: ModelConfigDraft = {
  provider: 'mock',
  model: 'mock-research-analyst',
  base_url: '',
  api_key: '',
  temperature: 0.2
};

const draftFromConfig = (config: ModelConfig): ModelConfigDraft => ({
  provider: config.provider,
  model: config.model,
  base_url: config.base_url || '',
  api_key: '',
  temperature: config.temperature
});

const ModelSettingsPanel: React.FC<ModelSettingsPanelProps> = ({
  initialConfig,
  onLoaded,
  onSaved
}) => {
  const [config, setConfig] = useState<ModelConfig | null>(initialConfig || null);
  const [draft, setDraft] = useState<ModelConfigDraft>(
    initialConfig ? draftFromConfig(initialConfig) : emptyDraft
  );
  const [loading, setLoading] = useState(false);
  // 对话记忆（AI 自我记忆）的可见与可控：展示已归档会话、支持一键清空。
  const [memStats, setMemStats] = useState<{ count: number; preview: Array<{ title: string; when: string }> }>(
    () => ({ count: memoryCount(), preview: memoryPreview(Date.now()) })
  );
  const refreshMemory = useCallback(() => {
    setMemStats({ count: memoryCount(), preview: memoryPreview(Date.now()) });
  }, []);
  const handleClearMemory = useCallback(() => {
    clearMemory();
    refreshMemory();
    message.success('对话记忆已清空');
  }, [refreshMemory]);

  // 决策日志（AI 自我进化）：查看历史判断、标注兑现、置信度校准、清空。
  const [decisions, setDecisions] = useState<DecisionRecord[]>(() => listDecisions());
  const refreshDecisions = useCallback(() => setDecisions(listDecisions()), []);
  const handleMarkOutcome = useCallback((id: string, outcome: DecisionOutcome) => {
    setOutcome(id, outcome, Date.now());
    refreshDecisions();
  }, [refreshDecisions]);
  const handleClearDecisions = useCallback(() => {
    clearDecisions();
    refreshDecisions();
    message.success('决策日志已清空');
  }, [refreshDecisions]);
  const calibration = computeCalibration(decisions);

  const applyConfig = useCallback((nextConfig: ModelConfig) => {
    setConfig(nextConfig);
    setDraft(draftFromConfig(nextConfig));
  }, []);

  const refreshConfig = useCallback(async () => {
    try {
      const nextConfig = await getModelConfig();
      applyConfig(nextConfig);
      onLoaded?.(nextConfig);
    } catch {
      setConfig(null);
    }
  }, [applyConfig, onLoaded]);

  useEffect(() => {
    if (initialConfig) {
      applyConfig(initialConfig);
      return;
    }
    void refreshConfig();
  }, [applyConfig, initialConfig, refreshConfig]);

  const updateProvider = (provider: ModelConfig['provider']) => {
    const defaults = providerDefaults[provider];
    setDraft(prev => ({
      ...prev,
      provider,
      model: defaults.model,
      base_url: defaults.base_url
    }));
  };

  const handleSave = async () => {
    setLoading(true);
    try {
      const payload: any = {
        provider: draft.provider,
        model: draft.model,
        base_url: draft.base_url,
        temperature: draft.temperature,
        persist: true
      };
      if (draft.api_key.trim()) {
        payload.api_key = draft.api_key.trim();
      }

      const saved = await updateModelConfig(payload);
      applyConfig(saved);
      onSaved?.(saved);
      message.success('模型配置已保存，新的 AI/Agent 请求会使用这套配置');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '模型配置保存失败');
    } finally {
      setLoading(false);
    }
  };

  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} lg={9}>
        <Card title="当前模型">
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
          ) : loading ? (
            <div style={{ textAlign: 'center', padding: 24 }}><Spin tip="加载配置中..." /></div>
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
                onChange={event => setDraft(prev => ({ ...prev, model: event.target.value }))}
                style={{ marginTop: 8 }}
                placeholder="例如 gpt-4o-mini、MiniMax-M3、deepseek-chat"
              />
              <Space wrap style={{ marginTop: 8 }}>
                {(modelOptions[draft.provider] || []).map(model => (
                  <Button
                    key={model}
                    size="small"
                    onClick={() => setDraft(prev => ({ ...prev, model }))}
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
                onChange={event => setDraft(prev => ({ ...prev, base_url: event.target.value }))}
                placeholder="OpenAI 官方可留空；兼容接口填写 https://.../v1"
                style={{ marginTop: 8 }}
              />
            </div>

            <div>
              <Text strong>API Key</Text>
              <Input.Password
                value={draft.api_key}
                onChange={event => setDraft(prev => ({ ...prev, api_key: event.target.value }))}
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
                onChange={value => setDraft(prev => ({ ...prev, temperature: Number(value ?? 0.2) }))}
                style={{ width: 180, marginTop: 8 }}
              />
            </div>

            <Alert
              type="info"
              showIcon
              message="这是全局模型配置：AI 对话、FinGPT 能力、Agent 队列、TradingAgents 和研报工作台都会读取同一份本机配置。"
            />

            <Button type="primary" icon={<SettingOutlined />} loading={loading} onClick={handleSave}>
              保存模型配置
            </Button>
          </Space>
        </Card>
      </Col>

      <Col xs={24}>
        <Card
          title="对话记忆"
          extra={
            memStats.count > 0 ? (
              <Popconfirm
                title="清空对话记忆？"
                description="将删除全部已归档的历史对话，Agent 不再据此召回。此操作不可撤销。"
                okText="清空"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={handleClearMemory}
              >
                <Button danger size="small" icon={<DeleteOutlined />}>清空</Button>
              </Popconfirm>
            ) : null
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Text type="secondary">
              开新对话时，DeepFocus 会把上一段对话归档到本机；之后你提问时，Agent 会按相关性
              召回过往讨论作为上下文（在「记忆」上下文源开启时）。数据仅存于本机浏览器。
            </Text>
            {memStats.count > 0 ? (
              <>
                <Statistic title="已归档对话" value={memStats.count} suffix="段" />
                <div>
                  <Text type="secondary">最近归档</Text>
                  <div style={{ marginTop: 6, display: 'flex', flexWrap: 'wrap', gap: 6 }}>
                    {memStats.preview.map((m, i) => (
                      <Tag key={i}>{m.title}　·　{m.when}</Tag>
                    ))}
                  </div>
                </div>
              </>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无归档对话（开始几次对话后，这里会出现可召回的记忆）" />
            )}
          </Space>
        </Card>
      </Col>

      <Col xs={24}>
        <Card
          title="决策追踪（自我进化）"
          extra={
            decisions.length > 0 ? (
              <Popconfirm
                title="清空决策日志？"
                description="将删除全部历史判断与校准数据，不可撤销。"
                okText="清空"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={handleClearDecisions}
              >
                <Button danger size="small" icon={<DeleteOutlined />}>清空</Button>
              </Popconfirm>
            ) : null
          }
        >
          <Space direction="vertical" size={12} style={{ width: '100%' }}>
            <Text type="secondary">
              每次深度研究给出分析评分时，DeepFocus 会自动记录这次判断（标的 / 动作 / 置信度 / 论点）。
              你标注兑现结果后，系统计算「置信度校准」——自评置信度 vs 实际命中率，识别系统性偏差，
              并在后续分析该标的时把校准结论喂回 Agent，促其自我纠偏。数据仅存本机浏览器。
            </Text>
            {decisions.length > 0 ? (
              <>
                <Row gutter={16}>
                  <Col><Statistic title="累计判断" value={calibration.total} suffix="次" /></Col>
                  <Col><Statistic title="已兑现" value={calibration.resolved} suffix="次" /></Col>
                  <Col>
                    <Statistic
                      title="命中率"
                      value={calibration.hitRate !== null ? Math.round(calibration.hitRate * 100) : '—'}
                      suffix={calibration.hitRate !== null ? '%' : ''}
                    />
                  </Col>
                  <Col>
                    <Statistic
                      title="平均置信"
                      value={calibration.avgConfidence !== null ? Math.round(calibration.avgConfidence * 100) : '—'}
                      suffix={calibration.avgConfidence !== null ? '%' : ''}
                    />
                  </Col>
                  <Col>
                    <div>
                      <Text type="secondary" style={{ fontSize: 12 }}>校准结论</Text>
                      <br />
                      <Tag
                        color={calibration.tendency === '校准良好' ? 'green'
                          : calibration.tendency === '样本不足' ? 'default' : 'orange'}
                        style={{ marginTop: 4 }}
                      >
                        {calibration.tendency}
                      </Tag>
                    </div>
                  </Col>
                </Row>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                  {decisions.slice(0, 12).map(d => (
                    <div
                      key={d.id}
                      style={{
                        display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap',
                        padding: '8px 10px', border: '1px solid var(--border, #eee)', borderRadius: 8,
                      }}
                    >
                      <Tag color="blue" style={{ margin: 0 }}>{d.symbol}</Tag>
                      <Text strong>{decisionActionLabel(d.action)}</Text>
                      <Text type="secondary" style={{ fontSize: 12 }}>置信 {Math.round(d.confidence * 100)}%</Text>
                      <Text type="secondary" style={{ flex: 1, minWidth: 120, fontSize: 12, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {d.thesis}
                      </Text>
                      {d.outcome === 'pending' ? (
                        <Space size={4}>
                          <Button size="small" onClick={() => handleMarkOutcome(d.id, 'correct')}>判对</Button>
                          <Button size="small" onClick={() => handleMarkOutcome(d.id, 'wrong')}>判错</Button>
                        </Space>
                      ) : (
                        <Tag
                          color={d.outcome === 'correct' ? 'green' : 'red'}
                          style={{ margin: 0, cursor: 'pointer' }}
                          onClick={() => handleMarkOutcome(d.id, 'pending')}
                          title={d.resolvedBy === 'price' ? '价格自动判定（点击撤回改手动）' : '人工标注（点击撤回）'}
                        >
                          {d.outcome === 'correct' ? '已判对' : '已判错'}
                          {d.resolvedBy === 'price' ? ' · 价格' : ''} ↺
                        </Tag>
                      )}
                    </div>
                  ))}
                </div>
              </>
            ) : (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无决策记录（做几次深度研究后，这里会出现可校准的判断轨迹）" />
            )}
          </Space>
        </Card>
      </Col>
    </Row>
  );
};

export default ModelSettingsPanel;
