import React, { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Button,
  Card,
  Col,
  Empty,
  Input,
  InputNumber,
  Row,
  Select,
  Space,
  Statistic,
  Tag,
  Typography,
  message
} from 'antd';
import { SettingOutlined } from '@ant-design/icons';
import {
  ModelConfig,
  getModelConfig,
  updateModelConfig
} from '../../services/aiResearchService';

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
  minimax: ['MiniMax-M2.7'],
  'openai-compatible': ['gpt-4o-mini', 'deepseek-chat', 'qwen-plus', 'moonshot-v1-8k'],
  cloud: ['gpt-4o-mini', 'deepseek-chat', 'qwen-plus', 'moonshot-v1-8k']
};

const providerDefaults: Record<string, { model: string; base_url: string }> = {
  mock: { model: 'mock-research-analyst', base_url: '' },
  openai: { model: 'gpt-4o-mini', base_url: '' },
  minimax: { model: 'MiniMax-M2.7', base_url: 'https://api.minimax.io/v1' },
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
                placeholder="例如 gpt-4o-mini、MiniMax-M2.7、deepseek-chat"
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
    </Row>
  );
};

export default ModelSettingsPanel;
