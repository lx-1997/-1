import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  App as AntdApp,
  Badge,
  Button,
  Checkbox,
  Col,
  Empty,
  Input,
  List,
  Progress,
  Row,
  Segmented,
  Select,
  Space,
  Statistic,
  Tag,
  Timeline,
  Typography
} from 'antd';
import {
  ApiOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  CloudServerOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ToolOutlined,
  WarningOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import CollapsibleSection from './CollapsibleSection';
import ShareButton from './common/ShareButton';
import {
  DulusAgentTurn,
  DulusDecision,
  DulusMemoryRecord,
  DulusRoundtableMode,
  DulusRoundtableResponse,
  DulusRuntimeStatus,
  DulusWebBridgeInspectResponse,
  getDulusStatus,
  inspectDulusWebBridge,
  listDulusMemory,
  runDulusRoundtable
} from '../services/agentService';
import './DulusAgentCenter.css';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface DulusAgentCenterProps {
  appState: AppState;
}

const participantOptions = [
  { label: 'Evidence Scout', value: 'evidence' },
  { label: 'Research Analyst', value: 'research' },
  { label: 'Risk Sentinel', value: 'risk' },
  { label: 'Tool Operator', value: 'operator' }
];

const decisionMeta: Record<DulusDecision, { label: string; color: string; icon: React.ReactNode }> = {
  candidate: { label: '候选机会', color: 'green', icon: <CheckCircleOutlined /> },
  watch: { label: '观察', color: 'blue', icon: <ClockCircleOutlined /> },
  research_more: { label: '继续补证', color: 'gold', icon: <FileSearchOutlined /> },
  blocked: { label: '已阻断', color: 'red', icon: <CloseCircleOutlined /> }
};

const providerStatusMeta = {
  ready: { label: '就绪', color: 'green' },
  needs_config: { label: '待配置', color: 'gold' },
  disabled: { label: '禁用', color: 'red' }
};

const permissionMeta = {
  read_only: { label: '只读', color: 'green' },
  approval_required: { label: '需确认', color: 'gold' },
  disabled: { label: '禁用', color: 'red' }
};

const stanceMeta = {
  evidence: { color: 'blue', icon: <DatabaseOutlined /> },
  research: { color: 'cyan', icon: <BranchesOutlined /> },
  risk: { color: 'orange', icon: <SafetyCertificateOutlined /> },
  operator: { color: 'purple', icon: <ToolOutlined /> },
  synthesis: { color: 'green', icon: <RobotOutlined /> }
};

const defaultObjective = '用圆桌模式研究当前标的的一到四周风险收益，并给出证据缺口、反证和下一步验证动作。';
const defaultContext = '请把结论限制在投研工作流层面，不输出确定性买卖建议；优先关注证据、风险和可执行核验。';

const roundtableModeOptions: Array<{ label: string; value: DulusRoundtableMode }> = [
  { label: 'Fast', value: 'fast' },
  { label: 'Debate', value: 'debate' },
  { label: 'Deep Research', value: 'deep_research' }
];

const DulusAgentCenter: React.FC<DulusAgentCenterProps> = ({ appState }) => {
  const { message } = AntdApp.useApp();
  const [status, setStatus] = useState<DulusRuntimeStatus | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [objective, setObjective] = useState(defaultObjective);
  const [context, setContext] = useState(defaultContext);
  const [mode, setMode] = useState<DulusRoundtableMode>('debate');
  const [participants, setParticipants] = useState<string[]>(['evidence', 'research', 'risk']);
  const [enabledTools, setEnabledTools] = useState<string[]>(['market_snapshot', 'evidence_lookup', 'risk_review', 'report_outline']);
  const [stockSymbol, setStockSymbol] = useState<string | undefined>(
    appState.selectedStock?.symbol || appState.stocks[0]?.symbol
  );
  const [response, setResponse] = useState<DulusRoundtableResponse | null>(null);
  const [memories, setMemories] = useState<DulusMemoryRecord[]>([]);
  const [webbridgeUrl, setWebbridgeUrl] = useState('http://127.0.0.1:3000/');
  const [webbridgeLoading, setWebbridgeLoading] = useState(false);
  const [webbridgeResult, setWebbridgeResult] = useState<DulusWebBridgeInspectResponse | null>(null);

  const selectedStock = useMemo(
    () => stockSymbol ? appState.stocks.find(stock => stock.symbol === stockSymbol) || null : null,
    [appState.stocks, stockSymbol]
  );

  const tools = status?.tools || [];
  const providers = status?.providers || [];
  const blockedToolCount = tools.filter(tool => tool.permission === 'disabled').length;
  const readyProviderCount = providers.filter(provider => provider.status === 'ready').length;

  const loadStatus = async () => {
    setLoading(true);
    try {
      const [nextStatus, nextMemories] = await Promise.all([
        getDulusStatus(),
        listDulusMemory(12)
      ]);
      setStatus(nextStatus);
      setMemories(nextMemories);
      const safeTools = nextStatus.tools
        .filter(tool => tool.enabled && tool.permission === 'read_only')
        .map(tool => tool.id);
      if (safeTools.length > 0 && enabledTools.length === 0) {
        setEnabledTools(safeTools.slice(0, 4));
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || 'Dulus Runtime 连接失败');
    } finally {
      setLoading(false);
    }
  };

  const inspectWebbridge = async () => {
    if (!webbridgeUrl.trim()) {
      message.warning('请填写授权 URL');
      return;
    }
    setWebbridgeLoading(true);
    try {
      const result = await inspectDulusWebBridge({ url: webbridgeUrl.trim(), mode: 'dom' });
      setWebbridgeResult(result);
      if (result.allowed) {
        message.success('授权 WebBridge 检查完成');
      } else {
        message.warning('该 URL 未通过授权策略');
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '授权 WebBridge 检查失败');
    } finally {
      setWebbridgeLoading(false);
    }
  };

  useEffect(() => {
    void loadStatus();
  }, []);

  const runRoundtable = async () => {
    if (!objective.trim()) {
      message.warning('请填写目标');
      return;
    }
    setRunning(true);
    try {
      const result = await runDulusRoundtable({
        objective,
        context,
        stock: selectedStock,
        participants,
        enabled_tools: enabledTools,
        authorized_webbridge_url: webbridgeUrl.trim() || undefined,
        mode,
        locale: 'zh-CN'
      });
      setResponse(result);
      setMemories(await listDulusMemory(12));
      message.success('Dulus 圆桌已完成');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '圆桌运行失败');
    } finally {
      setRunning(false);
    }
  };

  const toolCheckboxOptions = tools.map(tool => ({
    label: (
      <Space size={6} wrap>
        <span>{tool.name}</span>
        <Tag color={permissionMeta[tool.permission].color}>{permissionMeta[tool.permission].label}</Tag>
      </Space>
    ),
    value: tool.id,
    disabled: tool.permission === 'disabled'
  }));

  return (
    <div className="dulus-agent-shell">
      <div className="dulus-command-bar">
        <Space align="start" size={12}>
          <span className="dulus-heading-icon"><RobotOutlined /></span>
          <div>
            <Title level={3} style={{ margin: 0 }}>圆桌模式</Title>
            <Text type="secondary">Dulus Runtime · Debate · Tools · MemPalace</Text>
          </div>
        </Space>
        <Space wrap>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadStatus}>
            刷新
          </Button>
          <Button type="primary" icon={<PlayCircleOutlined />} loading={running} onClick={runRoundtable}>
            运行圆桌
          </Button>
        </Space>
      </div>

      {status?.warnings.map(warning => (
        <Alert
          key={warning}
          className="dulus-alert"
          type="warning"
          showIcon
          message={warning}
        />
      ))}

      <Row gutter={[12, 12]} className="dulus-kpi-grid">
        <Col xs={12} lg={6}>
          <div className="metric-tile">
            <Statistic title="Runtime" value={status?.compliant_mode ? 'Compliant' : 'Review'} prefix={<SafetyCertificateOutlined />} />
          </div>
        </Col>
        <Col xs={12} lg={6}>
          <div className="metric-tile">
            <Statistic title="Providers" value={`${readyProviderCount}/${providers.length || 0}`} prefix={<CloudServerOutlined />} />
          </div>
        </Col>
        <Col xs={12} lg={6}>
          <div className="metric-tile">
            <Statistic title="Tools" value={tools.length} prefix={<ToolOutlined />} />
          </div>
        </Col>
        <Col xs={12} lg={6}>
          <div className="metric-tile">
            <Statistic title="Blocked" value={blockedToolCount} prefix={<WarningOutlined />} />
          </div>
        </Col>
      </Row>

      <Row gutter={[14, 14]} className="dulus-layout">
        <Col xs={24} xl={9}>
          <div className="dulus-panel">
            <div className="dulus-panel-head">
              <Space><BranchesOutlined /><Text strong>任务编排</Text></Space>
              <Tag color="cyan">{status?.provider || 'mock'} / {status?.model || 'loading'}</Tag>
            </div>
            <Space direction="vertical" size={12} style={{ width: '100%' }}>
              <div>
                <Text className="field-label">标的</Text>
                <Select
                  allowClear
                  placeholder="不指定标的"
                  value={stockSymbol}
                  onChange={setStockSymbol}
                  style={{ width: '100%' }}
                  options={appState.stocks.map(stock => ({
                    value: stock.symbol,
                    label: `${stock.symbol} · ${stock.name}`
                  }))}
                />
              </div>
              <div>
                <Text className="field-label">模式</Text>
                <Segmented
                  block
                  value={mode}
                  options={roundtableModeOptions}
                  onChange={value => setMode(value as DulusRoundtableMode)}
                />
              </div>
              <div>
                <Text className="field-label">目标</Text>
                <TextArea
                  value={objective}
                  onChange={event => setObjective(event.target.value)}
                  autoSize={{ minRows: 3, maxRows: 6 }}
                />
              </div>
              <div>
                <Text className="field-label">上下文</Text>
                <TextArea
                  value={context}
                  onChange={event => setContext(event.target.value)}
                  autoSize={{ minRows: 5, maxRows: 9 }}
                />
              </div>
              <div>
                <Text className="field-label">授权 WebBridge URL</Text>
                <Input
                  value={webbridgeUrl}
                  onChange={event => setWebbridgeUrl(event.target.value)}
                  placeholder="仅 localhost / 127.0.0.1 / 白名单自有域名"
                />
              </div>
              <div>
                <Text className="field-label">圆桌席位</Text>
                <Checkbox.Group
                  className="dulus-checkbox-grid"
                  value={participants}
                  options={participantOptions}
                  onChange={values => setParticipants(values as string[])}
                />
              </div>
              <div>
                <Text className="field-label">工具</Text>
                <Checkbox.Group
                  className="dulus-checkbox-grid"
                  value={enabledTools}
                  options={toolCheckboxOptions}
                  onChange={values => setEnabledTools(values as string[])}
                />
              </div>
            </Space>
          </div>
        </Col>

        <Col xs={24} xl={15}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <div className="dulus-panel">
              <div className="dulus-panel-head">
                <Space><ApiOutlined /><Text strong>模型通道</Text></Space>
                <Text type="secondary">{status?.memory_scope || '本地会话记忆'}</Text>
              </div>
              <Row gutter={[10, 10]}>
                {providers.map(provider => (
                  <Col xs={24} md={12} key={provider.id}>
                    <div className="dulus-provider-tile">
                      <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
                        <div>
                          <Text strong>{provider.name}</Text>
                          <div><Text type="secondary">{provider.model}</Text></div>
                        </div>
                        <Badge color={providerStatusMeta[provider.status].color} text={providerStatusMeta[provider.status].label} />
                      </Space>
                      <Paragraph type="secondary" ellipsis={{ rows: 2 }} style={{ margin: '8px 0 0' }}>
                        {provider.notes}
                      </Paragraph>
                    </div>
                  </Col>
                ))}
              </Row>
            </div>

            <Row gutter={[14, 14]}>
              <Col xs={24} lg={12}>
                <div className="dulus-panel dulus-mini-panel">
                  <div className="dulus-panel-head">
                    <Space><FileSearchOutlined /><Text strong>授权 WebBridge</Text></Space>
                    <Button size="small" loading={webbridgeLoading} onClick={inspectWebbridge}>检查</Button>
                  </div>
                  {!webbridgeResult ? (
                    <Text type="secondary">只读取本机或白名单网页，不读取 Cookie 和浏览器 profile。</Text>
                  ) : (
                    <Space direction="vertical" size={8} style={{ width: '100%' }}>
                      <Tag color={webbridgeResult.allowed ? 'green' : 'red'}>
                        {webbridgeResult.allowed ? '已授权' : '已拒绝'}
                      </Tag>
                      <Text strong>{webbridgeResult.title || webbridgeResult.url}</Text>
                      <Paragraph ellipsis={{ rows: 3 }} style={{ margin: 0 }}>
                        {webbridgeResult.text_preview || webbridgeResult.policy}
                      </Paragraph>
                      {webbridgeResult.links.length > 0 && (
                        <Space wrap>
                          {webbridgeResult.links.slice(0, 4).map(link => <Tag key={link}>{link}</Tag>)}
                        </Space>
                      )}
                    </Space>
                  )}
                </div>
              </Col>
              <Col xs={24} lg={12}>
                <div className="dulus-panel dulus-mini-panel">
                  <div className="dulus-panel-head">
                    <Space><DatabaseOutlined /><Text strong>MemPalace</Text></Space>
                    <Tag>{memories.length} 条</Tag>
                  </div>
                  {memories.length === 0 ? (
                    <Text type="secondary">暂无记忆。运行圆桌后会自动写入。</Text>
                  ) : (
                    <List
                      size="small"
                      dataSource={memories.slice(0, 5)}
                      renderItem={memory => (
                        <List.Item>
                          <List.Item.Meta
                            title={<Space><span>{memory.title}</span><Tag>{memory.hall}</Tag></Space>}
                            description={<Text type="secondary" ellipsis>{memory.content}</Text>}
                          />
                        </List.Item>
                      )}
                    />
                  )}
                </div>
              </Col>
            </Row>

            <div className="dulus-panel dulus-output-panel">
              <div className="dulus-panel-head">
                <Space><RobotOutlined /><Text strong>圆桌输出</Text></Space>
                {response && (
                  <Space>
                    <Tag color={decisionMeta[response.decision as DulusDecision].color} icon={decisionMeta[response.decision as DulusDecision].icon}>
                      {decisionMeta[response.decision as DulusDecision].label}
                    </Tag>
                    <Progress type="circle" size={34} percent={Math.round(response.confidence * 100)} />
                  </Space>
                )}
              </div>

              {!response ? (
                <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="等待圆桌运行" />
              ) : (
                <Space direction="vertical" size={14} style={{ width: '100%' }}>
                  <div className="dulus-synthesis">
                    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 8 }}>
                      <Text strong>综合结论</Text>
                      <ShareButton
                        modalTitle="分享圆桌结论"
                        target={() => {
                          const obj = objective.trim();
                          return {
                            title: obj ? (obj.length > 38 ? `${obj.slice(0, 38)}…` : obj) : '圆桌综合结论',
                            summary: `${response.synthesis}${response.sources.length ? `\n\n来源：${response.sources.slice(0, 3).join('；')}` : ''}`,
                            byline: '由 DeepFocus 投研工作台 · 圆桌生成',
                          };
                        }}
                      />
                    </div>
                    <Paragraph style={{ margin: '8px 0 0' }}>{response.synthesis}</Paragraph>
                    <Space wrap>
                      {response.sources.map((source: string) => <Tag key={source}>{source}</Tag>)}
                    </Space>
                  </div>

                  <Row gutter={[10, 10]}>
                    {response.turns.map((turn: DulusAgentTurn) => (
                      <Col xs={24} lg={8} key={turn.participant_id}>
                        <div className="dulus-turn">
                          <Space align="start" style={{ width: '100%', justifyContent: 'space-between' }}>
                            <Space>
                              <span className={`dulus-stance ${turn.stance}`}>{stanceMeta[turn.stance].icon}</span>
                              <div>
                                <Text strong>{turn.participant_name}</Text>
                                <div><Text type="secondary">{turn.model}</Text></div>
                              </div>
                            </Space>
                            <Tag color={stanceMeta[turn.stance].color}>{Math.round(turn.confidence * 100)}%</Tag>
                          </Space>
                          <Paragraph className="dulus-turn-content">{turn.content}</Paragraph>
                          <List
                            size="small"
                            dataSource={turn.key_points.slice(0, 3)}
                            renderItem={item => <List.Item>{item}</List.Item>}
                          />
                        </div>
                      </Col>
                    ))}
                  </Row>

                  <Row gutter={[10, 10]}>
                    <Col xs={24} lg={12}>
                      <div className="dulus-trace-panel">
                        <Text strong>工具轨迹</Text>
                        <Timeline
                          className="dulus-timeline"
                          items={response.tool_traces.map(trace => ({
                            color: trace.status === 'blocked' ? 'red' : trace.status === 'skipped' ? 'gold' : 'green',
                            children: (
                              <div>
                                <Text strong>{trace.title}</Text>
                                <div><Text type="secondary">{trace.output}</Text></div>
                              </div>
                            )
                          }))}
                        />
                      </div>
                    </Col>
                    <Col xs={24} lg={12}>
                      <div className="dulus-trace-panel">
                        <Text strong>记忆摘要</Text>
                        <List
                          size="small"
                          dataSource={response.memory_notes}
                          renderItem={(item: string) => <List.Item>{item}</List.Item>}
                        />
                        {response.warnings.length > 0 && (
                          <Space wrap style={{ marginTop: 8 }}>
                            {response.warnings.map((warning: string) => <Tag color="gold" key={warning}>{warning}</Tag>)}
                          </Space>
                        )}
                      </div>
                    </Col>
                  </Row>
                  <Text type="secondary">{response.disclaimer}</Text>
                </Space>
              )}
            </div>
          </Space>
        </Col>
      </Row>
    </div>
  );
};

export default DulusAgentCenter;
