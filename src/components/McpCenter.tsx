import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Button,
  Checkbox,
  Col,
  Empty,
  Input,
  List,
  Popconfirm,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tag,
  Typography,
  message
} from 'antd';
import {
  ApiOutlined,
  ApartmentOutlined,
  CheckCircleOutlined,
  CloudServerOutlined,
  CodeOutlined,
  DeleteOutlined,
  ExperimentOutlined,
  FunctionOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ToolOutlined
} from '@ant-design/icons';
import dayjs from 'dayjs';
import { AppState } from '../types';
import {
  McpCapabilityRecord,
  McpCapabilityType,
  McpRiskLevel,
  McpServerCreate,
  McpServerRecord,
  McpTransport,
  TrustLevel,
  callMcpTool,
  createMcpServer,
  deleteMcpServer,
  discoverMcpServer,
  listMcpCapabilities,
  listMcpServers
} from '../services/mcpService';

const { Text, Title, Paragraph } = Typography;
const { TextArea } = Input;

interface McpCenterProps {
  appState: AppState;
}

interface McpTemplate {
  key: string;
  name: string;
  transport: McpTransport;
  description: string;
  url?: string;
  command?: string;
  args?: string[];
  trust_level: TrustLevel;
  risk_level: McpRiskLevel;
  approval_required: boolean;
  notes: string;
}

const defaultForm: McpServerCreate = {
  name: '',
  transport: 'streamable_http',
  description: '',
  url: '',
  command: '',
  args: [],
  env: {},
  headers: {},
  trust_level: 'unknown',
  risk_level: 'medium',
  approval_required: true,
  enabled: true,
  allowed_tools: [],
  blocked_tools: [],
  notes: ''
};

const serverTemplates: McpTemplate[] = [
  {
    key: 'deepfocus-http',
    name: '自建投研 MCP',
    transport: 'streamable_http',
    description: '远程 Streamable HTTP MCP server，用于投研工具、资料读取和工作流模板。',
    url: 'http://localhost:9000/mcp',
    trust_level: 'internal',
    risk_level: 'medium',
    approval_required: true,
    notes: '推荐把行情、资料库、组合只读查询先放到工具层；交易写操作单独加审批。'
  },
  {
    key: 'alpaca-stdio',
    name: 'Alpaca Trading MCP',
    transport: 'stdio',
    description: '股票、ETF、Crypto、期权的行情、账户和订单能力。',
    command: 'npx',
    args: ['-y', '@alpacahq/alpaca-mcp-server'],
    trust_level: 'official',
    risk_level: 'high',
    approval_required: true,
    notes: '涉及账户和订单操作，建议 allow-list 先开放只读市场数据工具。'
  },
  {
    key: 'coinbase-cdp',
    name: 'Coinbase CDP MCP',
    transport: 'stdio',
    description: '把 CDP API 暴露为 typed tools，覆盖账户、签名、转账和链上工作流。',
    command: 'npx',
    args: ['-y', '@coinbase/cdp-cli', 'mcp'],
    trust_level: 'official',
    risk_level: 'high',
    approval_required: true,
    notes: '交易、签名、转账类工具必须保留人工审批和审计。'
  }
];

const statusMeta: Record<string, { label: string; color: string }> = {
  connected: { label: '已连接', color: 'green' },
  error: { label: '异常', color: 'red' },
  disabled: { label: '停用', color: 'default' },
  unknown: { label: '未探测', color: 'gold' }
};

const transportMeta: Record<McpTransport, { label: string; icon: React.ReactNode }> = {
  streamable_http: { label: 'Streamable HTTP', icon: <CloudServerOutlined /> },
  stdio: { label: 'stdio', icon: <CodeOutlined /> },
  hosted: { label: 'Hosted', icon: <ApartmentOutlined /> }
};

const riskMeta: Record<McpRiskLevel, { label: string; color: string }> = {
  low: { label: '低风险', color: 'green' },
  medium: { label: '中风险', color: 'orange' },
  high: { label: '高风险', color: 'red' }
};

const capabilityLabel: Record<McpCapabilityType, string> = {
  tool: '工具',
  resource: '资源',
  prompt: '提示词'
};

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

const parseCsv = (value: string) => value
  .split(/[,，\n]/)
  .map(item => item.trim())
  .filter(Boolean);

const formatTime = (value?: string | null) => value ? dayjs(value).format('MM-DD HH:mm') : '--';

const McpCenter: React.FC<McpCenterProps> = ({ appState }) => {
  const [servers, setServers] = useState<McpServerRecord[]>([]);
  const [capabilities, setCapabilities] = useState<McpCapabilityRecord[]>([]);
  const [selectedServerId, setSelectedServerId] = useState<string | undefined>();
  const [capabilityType, setCapabilityType] = useState<McpCapabilityType | undefined>();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [discoveringId, setDiscoveringId] = useState<string | null>(null);
  const [calling, setCalling] = useState(false);
  const [callResult, setCallResult] = useState('');
  const [headersJson, setHeadersJson] = useState('{}');
  const [envJson, setEnvJson] = useState('{}');
  const [argsText, setArgsText] = useState('');
  const [allowedToolsText, setAllowedToolsText] = useState('');
  const [blockedToolsText, setBlockedToolsText] = useState('');
  const [toolArgumentsJson, setToolArgumentsJson] = useState('{\n  \n}');
  const [selectedToolName, setSelectedToolName] = useState<string | undefined>();
  const [approved, setApproved] = useState(false);
  const [form, setForm] = useState<McpServerCreate>(defaultForm);

  const selectedServer = useMemo(
    () => servers.find(server => server.id === selectedServerId),
    [selectedServerId, servers]
  );

  const stats = useMemo(() => ({
    servers: servers.length,
    connected: servers.filter(server => server.status === 'connected').length,
    tools: servers.reduce((sum, server) => sum + server.tool_count, 0),
    highRisk: servers.filter(server => server.risk_level === 'high').length
  }), [servers]);

  const visibleCapabilities = useMemo(() => capabilities.filter(item => {
    if (selectedServerId && item.server_id !== selectedServerId) {
      return false;
    }
    if (capabilityType && item.capability_type !== capabilityType) {
      return false;
    }
    return true;
  }), [capabilities, capabilityType, selectedServerId]);

  const toolOptions = useMemo(() => visibleCapabilities
    .filter(item => item.capability_type === 'tool')
    .map(item => ({ value: item.name, label: item.title || item.name })), [visibleCapabilities]);

  const loadData = async (serverId?: string) => {
    setLoading(true);
    try {
      const [nextServers, nextCapabilities] = await Promise.all([
        listMcpServers(),
        listMcpCapabilities(serverId ? { server_id: serverId } : {})
      ]);
      setServers(nextServers);
      setCapabilities(nextCapabilities);
      if (!selectedServerId && nextServers[0]) {
        setSelectedServerId(nextServers[0].id);
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || 'MCP 模块连接失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void loadData();
  }, []);

  const applyTemplate = (template: McpTemplate) => {
    setForm({
      ...defaultForm,
      name: template.name,
      transport: template.transport,
      description: template.description,
      url: template.url || '',
      command: template.command || '',
      args: template.args || [],
      trust_level: template.trust_level,
      risk_level: template.risk_level,
      approval_required: template.approval_required,
      notes: template.notes
    });
    setArgsText((template.args || []).join('\n'));
    setHeadersJson('{}');
    setEnvJson('{}');
    setAllowedToolsText('');
    setBlockedToolsText('');
  };

  const handleCreate = async () => {
    setSaving(true);
    try {
      const payload: McpServerCreate = {
        ...form,
        args: parseCsv(argsText),
        headers: parseJsonObject(headersJson, 'Headers'),
        env: parseJsonObject(envJson, 'Env'),
        allowed_tools: parseCsv(allowedToolsText),
        blocked_tools: parseCsv(blockedToolsText)
      };
      const saved = await createMcpServer(payload);
      setForm(defaultForm);
      setHeadersJson('{}');
      setEnvJson('{}');
      setArgsText('');
      setAllowedToolsText('');
      setBlockedToolsText('');
      setSelectedServerId(saved.id);
      await loadData(saved.id);
      message.success('MCP server 已登记');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error.message || '保存失败');
    } finally {
      setSaving(false);
    }
  };

  const handleDiscover = async (server: McpServerRecord) => {
    setDiscoveringId(server.id);
    try {
      const result = await discoverMcpServer(server.id);
      setServers(prev => prev.map(item => item.id === result.server.id ? result.server : item));
      setCapabilities(result.capabilities);
      setSelectedServerId(server.id);
      if (result.warnings.length > 0) {
        message.warning(result.warnings[0]);
      } else {
        message.success(`已发现 ${result.capabilities.length} 个 MCP 能力`);
      }
    } catch (error: any) {
      await loadData(server.id);
      message.error(error?.response?.data?.detail || '探测失败');
    } finally {
      setDiscoveringId(null);
    }
  };

  const handleDelete = async (server: McpServerRecord) => {
    await deleteMcpServer(server.id);
    if (selectedServerId === server.id) {
      setSelectedServerId(undefined);
    }
    await loadData();
    message.success('MCP server 已删除');
  };

  const handleServerSelect = async (serverId: string) => {
    setSelectedServerId(serverId);
    setSelectedToolName(undefined);
    setCallResult('');
    await loadData(serverId);
  };

  const handleCallTool = async () => {
    if (!selectedServerId || !selectedToolName) {
      message.warning('请选择 MCP server 和工具');
      return;
    }
    setCalling(true);
    try {
      const result = await callMcpTool(selectedServerId, {
        tool_name: selectedToolName,
        arguments: parseJsonObject(toolArgumentsJson, '工具参数'),
        approved
      });
      setCallResult(JSON.stringify(result.result, null, 2));
      setServers(prev => prev.map(item => item.id === result.server.id ? result.server : item));
      message.success('MCP 工具调用完成');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || error.message || '调用失败');
    } finally {
      setCalling(false);
    }
  };

  const serverColumns = [
    {
      title: 'Server',
      dataIndex: 'name',
      key: 'name',
      render: (_: string, record: McpServerRecord) => (
        <button className="mcp-server-link" onClick={() => void handleServerSelect(record.id)}>
          <span>{record.name}</span>
          <small>{record.description || record.config.url || record.config.command}</small>
        </button>
      )
    },
    {
      title: '状态',
      dataIndex: 'status',
      key: 'status',
      width: 96,
      render: (value: string) => <Tag color={statusMeta[value]?.color || 'default'}>{statusMeta[value]?.label || value}</Tag>
    },
    {
      title: 'Transport',
      dataIndex: 'transport',
      key: 'transport',
      width: 150,
      render: (value: McpTransport) => (
        <Space size={6}>
          {transportMeta[value].icon}
          <span>{transportMeta[value].label}</span>
        </Space>
      )
    },
    {
      title: '能力',
      key: 'capabilities',
      width: 150,
      render: (_: unknown, record: McpServerRecord) => (
        <Space size={4}>
          <Tag>{record.tool_count} 工具</Tag>
          <Tag>{record.resource_count} 资源</Tag>
        </Space>
      )
    },
    {
      title: '风险',
      dataIndex: 'risk_level',
      key: 'risk_level',
      width: 90,
      render: (value: McpRiskLevel) => <Tag color={riskMeta[value].color}>{riskMeta[value].label}</Tag>
    },
    {
      title: '操作',
      key: 'actions',
      width: 150,
      render: (_: unknown, record: McpServerRecord) => (
        <Space size={6}>
          <Button size="small" icon={<ReloadOutlined />} loading={discoveringId === record.id} onClick={() => void handleDiscover(record)}>
            探测
          </Button>
          <Popconfirm title="删除这个 MCP server？" onConfirm={() => void handleDelete(record)}>
            <Button size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      )
    }
  ];

  return (
    <div className="mcp-center-shell">
      <div className="page-heading-band">
        <div>
          <Space align="center" size={10}>
            <span className="mcp-heading-icon"><ApiOutlined /></span>
            <Title level={3} style={{ margin: 0 }}>MCP 中心</Title>
          </Space>
          <Text type="secondary">Agent 工具、投研数据和交易能力的统一协议层</Text>
        </div>
        <Space wrap>
          <Tag color="blue">Streamable HTTP</Tag>
          <Tag color="gold">stdio 配置</Tag>
          <Tag color="red">人工审批</Tag>
        </Space>
      </div>

      <div className="mcp-kpi-grid">
        <div className="mcp-kpi-tile"><Statistic title="Server" value={stats.servers} prefix={<CloudServerOutlined />} /></div>
        <div className="mcp-kpi-tile"><Statistic title="已连接" value={stats.connected} prefix={<CheckCircleOutlined />} /></div>
        <div className="mcp-kpi-tile"><Statistic title="工具" value={stats.tools} prefix={<ToolOutlined />} /></div>
        <div className="mcp-kpi-tile"><Statistic title="高风险" value={stats.highRisk} prefix={<SafetyCertificateOutlined />} /></div>
      </div>

      <div className="mcp-pattern-grid">
        <div className="mcp-pattern-panel">
          <FunctionOutlined />
          <strong>能力发现</strong>
          <span>tools / resources / prompts</span>
        </div>
        <div className="mcp-pattern-panel">
          <ExperimentOutlined />
          <strong>投研工具化</strong>
          <span>行情、资料、组合、风控</span>
        </div>
        <div className="mcp-pattern-panel">
          <SafetyCertificateOutlined />
          <strong>审批边界</strong>
          <span>allow-list、block-list、HITL</span>
        </div>
        <div className="mcp-pattern-panel">
          <ApartmentOutlined />
          <strong>多 Server 编排</strong>
          <span>按任务连接最小能力集</span>
        </div>
      </div>

      <Row gutter={[14, 14]}>
        <Col xs={24} xl={7}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="mcp-panel">
              <div className="mcp-panel-title">
                <CloudServerOutlined />
                <span>登记 Server</span>
              </div>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Space wrap>
                  {serverTemplates.map(template => (
                    <Button key={template.key} size="small" onClick={() => applyTemplate(template)}>
                      {template.name}
                    </Button>
                  ))}
                </Space>
                <Input value={form.name} onChange={event => setForm(prev => ({ ...prev, name: event.target.value }))} placeholder="名称" />
                <Select
                  value={form.transport}
                  onChange={value => setForm(prev => ({ ...prev, transport: value }))}
                  options={[
                    { value: 'streamable_http', label: 'Streamable HTTP' },
                    { value: 'stdio', label: 'stdio' },
                    { value: 'hosted', label: 'Hosted' }
                  ]}
                />
                {form.transport === 'streamable_http' ? (
                  <Input value={form.url} onChange={event => setForm(prev => ({ ...prev, url: event.target.value }))} placeholder="https://example.com/mcp" />
                ) : (
                  <>
                    <Input value={form.command} onChange={event => setForm(prev => ({ ...prev, command: event.target.value }))} placeholder="command" />
                    <TextArea rows={3} value={argsText} onChange={event => setArgsText(event.target.value)} placeholder="args，每行一个" />
                  </>
                )}
                <Row gutter={8}>
                  <Col span={12}>
                    <Select
                      value={form.trust_level}
                      onChange={value => setForm(prev => ({ ...prev, trust_level: value }))}
                      options={[
                        { value: 'internal', label: '内部' },
                        { value: 'official', label: '官方' },
                        { value: 'media', label: '媒体' },
                        { value: 'community', label: '社区' },
                        { value: 'unknown', label: '未知' }
                      ]}
                    />
                  </Col>
                  <Col span={12}>
                    <Select
                      value={form.risk_level}
                      onChange={value => setForm(prev => ({ ...prev, risk_level: value }))}
                      options={[
                        { value: 'low', label: '低风险' },
                        { value: 'medium', label: '中风险' },
                        { value: 'high', label: '高风险' }
                      ]}
                    />
                  </Col>
                </Row>
                <Checkbox
                  checked={form.approval_required}
                  onChange={event => setForm(prev => ({ ...prev, approval_required: event.target.checked }))}
                >
                  工具调用需要确认
                </Checkbox>
                <TextArea rows={2} value={form.description} onChange={event => setForm(prev => ({ ...prev, description: event.target.value }))} placeholder="说明" />
                <TextArea rows={3} value={headersJson} onChange={event => setHeadersJson(event.target.value)} placeholder='Headers JSON，例如 {"Authorization":"Bearer ..."}' />
                <TextArea rows={3} value={envJson} onChange={event => setEnvJson(event.target.value)} placeholder='Env JSON，例如 {"API_KEY":"..."}' />
                <TextArea rows={2} value={allowedToolsText} onChange={event => setAllowedToolsText(event.target.value)} placeholder="allow-list 工具名，逗号或换行分隔" />
                <TextArea rows={2} value={blockedToolsText} onChange={event => setBlockedToolsText(event.target.value)} placeholder="block-list 工具名，逗号或换行分隔" />
                <Button type="primary" block icon={<CloudServerOutlined />} loading={saving} onClick={handleCreate}>
                  保存 Server
                </Button>
              </Space>
            </div>

            <div className="mcp-panel">
              <div className="mcp-panel-title">
                <PlayCircleOutlined />
                <span>调用工具</span>
              </div>
              <Space direction="vertical" size={10} style={{ width: '100%' }}>
                <Select
                  value={selectedServerId}
                  onChange={value => void handleServerSelect(value)}
                  options={servers.map(server => ({ value: server.id, label: server.name }))}
                  placeholder="选择 server"
                />
                <Select
                  value={selectedToolName}
                  onChange={setSelectedToolName}
                  options={toolOptions}
                  placeholder="选择工具"
                />
                <TextArea rows={6} value={toolArgumentsJson} onChange={event => setToolArgumentsJson(event.target.value)} />
                <Checkbox checked={approved} onChange={event => setApproved(event.target.checked)}>
                  已确认本次调用
                </Checkbox>
                <Button type="primary" block icon={<PlayCircleOutlined />} loading={calling} onClick={handleCallTool}>
                  调用 MCP 工具
                </Button>
                {callResult && (
                  <pre className="mcp-call-result">{callResult}</pre>
                )}
              </Space>
            </div>
          </Space>
        </Col>

        <Col xs={24} xl={17}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="mcp-panel">
              <div className="mcp-panel-title-row">
                <div className="mcp-panel-title">
                  <ApiOutlined />
                  <span>Server Registry</span>
                </div>
                <Button icon={<ReloadOutlined />} onClick={() => void loadData(selectedServerId)}>
                  刷新
                </Button>
              </div>
              <Table
                rowKey="id"
                loading={loading}
                columns={serverColumns}
                dataSource={servers}
                pagination={{ pageSize: 6 }}
                size="middle"
              />
            </div>

            <div className="mcp-panel">
              <div className="mcp-panel-title-row">
                <div>
                  <div className="mcp-panel-title">
                    <FunctionOutlined />
                    <span>能力目录</span>
                  </div>
                  {selectedServer && (
                    <Text type="secondary">
                      {selectedServer.name} · 最近探测 {formatTime(selectedServer.last_connected_at)}
                    </Text>
                  )}
                </div>
                <Space wrap>
                  <Select
                    allowClear
                    value={capabilityType}
                    onChange={setCapabilityType}
                    options={[
                      { value: 'tool', label: '工具' },
                      { value: 'resource', label: '资源' },
                      { value: 'prompt', label: '提示词' }
                    ]}
                    placeholder="能力类型"
                    style={{ width: 130 }}
                  />
                </Space>
              </div>
              {selectedServer?.last_error && (
                <Alert type={selectedServer.status === 'error' ? 'error' : 'warning'} showIcon message={selectedServer.last_error} style={{ marginBottom: 12 }} />
              )}
              <List
                dataSource={visibleCapabilities}
                locale={{ emptyText: <Empty description="暂无 MCP 能力" /> }}
                renderItem={item => (
                  <List.Item className="mcp-capability-item">
                    <div className="mcp-capability-main">
                      <Space wrap size={6}>
                        <Tag color={item.capability_type === 'tool' ? 'blue' : item.capability_type === 'resource' ? 'green' : 'purple'}>
                          {capabilityLabel[item.capability_type]}
                        </Tag>
                        <Text strong>{item.title || item.name}</Text>
                        {item.uri && <Tag>{item.uri}</Tag>}
                        {item.mime_type && <Tag>{item.mime_type}</Tag>}
                      </Space>
                      {item.description && (
                        <Paragraph className="mcp-capability-description" ellipsis={{ rows: 2, expandable: true, symbol: '展开' }}>
                          {item.description}
                        </Paragraph>
                      )}
                      {item.capability_type === 'tool' && (
                        <Button size="small" icon={<PlayCircleOutlined />} onClick={() => {
                          setSelectedServerId(item.server_id);
                          setSelectedToolName(item.name);
                        }}>
                          选择
                        </Button>
                      )}
                    </div>
                  </List.Item>
                )}
              />
            </div>
          </Space>
        </Col>
      </Row>
    </div>
  );
};

export default McpCenter;
