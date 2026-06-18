import React, { useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Col,
  Descriptions,
  Empty,
  Input,
  List,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Steps,
  Tag,
  Timeline,
  Typography,
  message
} from 'antd';
import {
  ApiOutlined,
  AuditOutlined,
  BarChartOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  DollarOutlined,
  FileSearchOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
  OrderedListOutlined,
  PlayCircleOutlined,
  ReloadOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
  WarningOutlined
} from '@ant-design/icons';
import { AppState } from '../types';
import CollapsibleSection from './CollapsibleSection';
import ShareButton from './common/ShareButton';
import {
  AgentEngine,
  AgentRuntimeHealth,
  InvestmentTaskCreate,
  InvestmentTaskRecord,
  InvestmentTaskResult,
  cancelAgentTask,
  createAgentTask,
  getAgentHealth,
  listAgentTasks,
  retryAgentTask
} from '../services/agentService';
import {
  coreAgentNameFromAgent,
  coreFindingTitle,
  resolveAgentPhase
} from '../utils/agentProjection';
import type { CoreAgentPhase } from '../utils/agentProjection';
import './centers/researchAgent.css';

const { Paragraph, Text, Title } = Typography;
const { TextArea } = Input;

interface InvestorAgentCenterProps {
  appState: AppState;
}

const statusMeta: Record<string, { color: string; text: string; badge: 'default' | 'processing' | 'success' | 'error' | 'warning' }> = {
  pending: { color: 'default', text: '排队中', badge: 'default' },
  running: { color: 'processing', text: '执行中', badge: 'processing' },
  waiting_approval: { color: 'warning', text: '待确认', badge: 'warning' },
  failed: { color: 'error', text: '失败', badge: 'error' },
  completed: { color: 'success', text: '完成', badge: 'success' },
  cancelled: { color: 'default', text: '已取消', badge: 'default' }
};

const decisionMeta: Record<string, { color: string; text: string }> = {
  avoid: { color: 'red', text: '暂避' },
  watch: { color: 'blue', text: '观察' },
  research_more: { color: 'gold', text: '继续研究' },
  candidate: { color: 'green', text: '候选机会' }
};

const engineMeta: Record<AgentEngine, { title: string; short: string; color: string; description: string }> = {
  deepfocus: {
    title: 'DeepFocus Native',
    short: 'Native',
    color: 'cyan',
    description: '使用 4 个核心角色调度证据、分析、风控和输出层；FinGPT 与专题能力作为技能接入。'
  },
  tradingagents: {
    title: 'TradingAgents',
    short: 'TA',
    color: 'purple',
    description: '作为分析引擎接入，由 Analyst 汇总其 analyst/debate/trader 结果，前台不再展开为额外角色。'
  },
  financial_services: {
    title: 'Financial Services Playbook',
    short: 'FSI',
    color: 'geekblue',
    description: '作为工作流模板接入，把财报复核、模型、Pitch、估值、KYC 和对账能力交给核心角色调度。'
  }
};

const engineOptions = [
  { value: 'deepfocus', label: 'DeepFocus' },
  { value: 'tradingagents', label: 'TradingAgents' },
  { value: 'financial_services', label: 'FSI Playbook' }
];

const tradingAgentsEngineConfig = {
  max_debate_rounds: 1,
  max_risk_discuss_rounds: 1,
  selected_analysts: ['market', 'news', 'fundamentals'],
  timeout_seconds: 180,
  tool_timeout_seconds: 12,
  web_search_limit: 4,
  web_search_timeout_seconds: 6
};

const financialServicesEngineConfig = {
  source_project: 'anthropics/financial-services',
  playbooks: [
    'market-researcher',
    'earnings-reviewer',
    'model-builder',
    'pitch-agent',
    'valuation-reviewer',
    'kyc-screener',
    'gl-reconciler',
    'month-end-closer'
  ]
};

const taskTypeOptions = [
  { value: 'investment_research', label: '个股投研' },
  { value: 'portfolio_review', label: '组合复盘' },
  { value: 'risk_review', label: '风险审查' },
  { value: 'watchlist_monitor', label: '观察名单' }
];

const profileOptions = ['保守', '稳健', '进取', '专业'].map(value => ({ value, label: value }));

const taskEngine = (task?: InvestmentTaskRecord | null): AgentEngine => (
  task?.engine || (task?.input?.engine as AgentEngine) || 'deepfocus'
);

const phaseDefinitions: Array<{
  key: CoreAgentPhase;
  title: string;
  goal: string;
  color: string;
  icon: React.ReactNode;
}> = [
  {
    key: 'orchestrator',
    title: '目标拆解',
    goal: '确认标的、周期、投资者画像和赚钱目标',
    color: 'gray',
    icon: <PlayCircleOutlined />
  },
  {
    key: 'evidence',
    title: '证据装填',
    goal: '同步行情、资料、公告和本地证据',
    color: 'blue',
    icon: <DatabaseOutlined />
  },
  {
    key: 'research',
    title: '分析融合',
    goal: '融合基本面、新闻、情绪、技术面、TradingAgents 和专题技能',
    color: 'cyan',
    icon: <BarChartOutlined />
  },
  {
    key: 'risk',
    title: '亏损路径',
    goal: '先找失效条件、仓位纪律和反证',
    color: 'orange',
    icon: <WarningOutlined />
  },
  {
    key: 'report',
    title: '输出层',
    goal: '把证据、判断和风控压缩成可复核报告',
    color: 'green',
    icon: <FundProjectionScreenOutlined />
  }
];

const phaseByKey = phaseDefinitions.reduce<Record<CoreAgentPhase, typeof phaseDefinitions[number]>>((acc, phase) => {
  acc[phase.key] = phase;
  return acc;
}, {} as Record<CoreAgentPhase, typeof phaseDefinitions[number]>);

const boundedPercent = (value: number): number => Math.max(0, Math.min(100, Math.round(value)));

const decisionReadinessScore = (task: InvestmentTaskRecord): number => {
  const result = task.result;
  if (!result) {
    return boundedPercent(task.progress * 0.42);
  }

  const confidence = Math.max(0, Math.min(1, Number(result.confidence || 0)));
  const evidenceCount = result.evidence?.length || 0;
  const actionCount = result.action_plan?.length || 0;
  const riskCount = result.risk_controls?.length || 0;
  const antiThesisCount = result.disconfirming_evidence?.length || 0;
  const watchCount = result.watchlist?.length || 0;

  return boundedPercent(
    confidence * 42 +
    Math.min(evidenceCount, 6) * 5 +
    Math.min(actionCount, 5) * 4 +
    Math.min(riskCount, 5) * 4 +
    Math.min(antiThesisCount, 4) * 3 +
    Math.min(watchCount, 4) * 2
  );
};

const decisionGuidance = (result?: InvestmentTaskResult | null): string => {
  if (!result) return '等待核心链路完成证据、反证和风险纪律后再转化为投资动作。';
  if (result.decision === 'candidate') return '可进入候选机会池，但必须先按触发器和仓位纪律小步验证。';
  if (result.decision === 'watch') return '保持观察，等价格行为、公告或财报触发器确认后再考虑行动。';
  if (result.decision === 'avoid') return '暂时把资金保护放在第一位，直到关键风险被证伪或价格重新给出安全边际。';
  return '继续补证，优先解决资料缺口和反证问题，再判断是否值得投入资金。';
};

const coreFindingEntries = (
  findings: Record<string, string[]> = {}
): Array<[string, string[]]> => {
  const merged = new Map<string, string[]>();
  Object.entries(findings).forEach(([agent, items]) => {
    const title = coreFindingTitle(agent);
    const existing = merged.get(title) || [];
    merged.set(title, [...existing, ...items]);
  });
  return Array.from(merged.entries());
};

const getLatestLog = (task: InvestmentTaskRecord) => task.logs[task.logs.length - 1];

const AGENT_HEARTBEAT_WARN_MS = 90_000;

const taskHeartbeatLagMs = (task: InvestmentTaskRecord): number => {
  if (task.status !== 'running') return 0;
  const updatedAt = new Date(task.updated_at).getTime();
  return Number.isFinite(updatedAt) ? Math.max(0, Date.now() - updatedAt) : 0;
};

const formatLag = (lagMs: number): string => {
  const seconds = Math.max(0, Math.round(lagMs / 1000));
  if (seconds < 60) return `${seconds} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
};

const isHeartbeatDelayed = (task: InvestmentTaskRecord): boolean => (
  taskHeartbeatLagMs(task) >= AGENT_HEARTBEAT_WARN_MS
);

const InvestorAgentCenter: React.FC<InvestorAgentCenterProps> = ({ appState }) => {
  const [health, setHealth] = useState<AgentRuntimeHealth | null>(null);
  const [tasks, setTasks] = useState<InvestmentTaskRecord[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState<InvestmentTaskCreate>({
    title: '研究 TSLA 的 1-4 周正期望机会',
    symbol: appState.stocks[0]?.symbol,
    asset_name: appState.stocks[0]?.name,
    task_type: 'investment_research',
    engine: 'tradingagents',
    horizon: '1-4周',
    investor_profile: '稳健',
	    objective: '判断是否存在值得投入资金关注的正期望机会，并给出风险控制、反证清单和下一步验证动作。',
	    context: '请结合当前社区内容、财报变化、市场情绪、研报资料和风险事件，重点回答赚钱催化、亏损路径、仓位纪律和触发条件。',
	    engine_config: tradingAgentsEngineConfig,
	    priority: 3
	  });

  const selectedTask = useMemo(
    () => tasks.find(task => task.id === selectedTaskId) || tasks[0] || null,
    [tasks, selectedTaskId]
  );

  const selectedEngine = engineMeta[form.engine];

  const loadData = async () => {
    setLoading(true);
    try {
      const [nextHealth, nextTasks] = await Promise.all([
        getAgentHealth(),
        listAgentTasks()
      ]);
      setHealth(nextHealth);
      setTasks(nextTasks);
      if (!selectedTaskId && nextTasks.length > 0) {
        setSelectedTaskId(nextTasks[0].id);
      }
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '任务中心连接失败');
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    loadData();
    const timer = window.setInterval(loadData, 3000);
    return () => window.clearInterval(timer);
  }, []);

  const updateStock = (symbol: string) => {
    const stock = appState.stocks.find(item => item.symbol === symbol);
    setForm(prev => ({
      ...prev,
      symbol,
      asset_name: stock?.name || symbol,
      title: `研究 ${stock?.name || symbol} 的 ${prev.horizon} 机会与风险`
    }));
  };

  const handleCreate = async () => {
    setCreating(true);
    try {
      const created = await createAgentTask(form);
      setSelectedTaskId(created.id);
      await loadData();
      message.success('投研任务已进入队列');
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '创建任务失败');
    } finally {
      setCreating(false);
    }
  };

  const handleRetry = async (taskId: string) => {
    try {
      await retryAgentTask(taskId);
      await loadData();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '重试失败');
    }
  };

  const handleCancel = async (taskId: string) => {
    try {
      await cancelAgentTask(taskId);
      await loadData();
    } catch (error: any) {
      message.error(error?.response?.data?.detail || '取消失败');
    }
  };

  return (
    <div className="agent-desk-shell">
      <section className="agent-command-bar">
        <div className="agent-command-title">
          <RobotOutlined />
          <div>
            <Title level={3}>投研任务</Title>
            <Text>前台收敛为 Orchestrator / Evidence / Analyst / Risk，报告生成作为输出层；底层引擎和专题模块都作为技能接入。</Text>
          </div>
        </div>
        <div className="agent-runtime-pills">
          <span><ClockCircleOutlined /> 任务引擎 {health?.worker_running ? '运行中' : '未运行'}</span>
          <span>排队 {health?.pending || 0}</span>
          <span>执行 {health?.running || 0}</span>
          <span>完成 {health?.completed || 0}</span>
          <span>失败 {health?.failed || 0}</span>
          <Button icon={<ReloadOutlined />} loading={loading} onClick={loadData}>刷新</Button>
        </div>
      </section>

      <section className="agent-ide-grid">
        <aside className="agent-workspace-panel">
          <CollapsibleSection
            title={<><PlayCircleOutlined /> 新建投研任务</>}
            extra={<Tag color={selectedEngine.color}>{selectedEngine.short}</Tag>}
            defaultOpen={true}
            level={2}
          >
          <div className="agent-profit-protocol">
            <span><DollarOutlined /> 正期望</span>
            <span><FileSearchOutlined /> 可追溯</span>
            <span><WarningOutlined /> 先控亏损</span>
          </div>
          <Space direction="vertical" size={10} style={{ width: '100%' }}>
            <Select
              value={form.symbol}
              onChange={updateStock}
              options={appState.stocks.map(stock => ({
                value: stock.symbol,
                label: `${stock.name} (${stock.symbol})`
              }))}
            />
            <Input
              value={form.title}
              onChange={event => setForm(prev => ({ ...prev, title: event.target.value }))}
              placeholder="这次任务要完成什么"
            />
            <Row gutter={8}>
              <Col span={12}>
                <Select
                  value={form.engine}
                  options={engineOptions}
                  onChange={value => setForm(prev => ({
                    ...prev,
                    engine: value as AgentEngine,
                    objective: value === 'financial_services'
                      ? '选择合适的金融服务工作流，生成可复核的模型、备忘录、Pitch、KYC 或对账交付件。'
                      : prev.objective,
                    context: value === 'financial_services'
                      ? '请参考 financial-services cookbook 的工作流设计：先识别 playbook，再列输入包、交付件、审计检查和人工复核闸门。'
                      : prev.context,
                    engine_config: value === 'tradingagents'
                      ? tradingAgentsEngineConfig
                      : value === 'financial_services'
                        ? financialServicesEngineConfig
                        : {}
                  }))}
                />
              </Col>
              <Col span={12}>
                <Select
                  value={form.task_type}
                  options={taskTypeOptions}
                  onChange={value => setForm(prev => ({ ...prev, task_type: value as InvestmentTaskCreate['task_type'] }))}
                />
              </Col>
            </Row>
            <Row gutter={8}>
              <Col span={12}>
                <Select
                  value={form.investor_profile}
                  options={profileOptions}
                  onChange={value => setForm(prev => ({ ...prev, investor_profile: value as InvestmentTaskCreate['investor_profile'] }))}
                />
              </Col>
              <Col span={12}>
                <Input
                  value={form.horizon}
                  onChange={event => setForm(prev => ({ ...prev, horizon: event.target.value }))}
                  placeholder="周期"
                />
              </Col>
            </Row>
            <TextArea
              rows={3}
              value={form.objective}
              onChange={event => setForm(prev => ({ ...prev, objective: event.target.value }))}
              placeholder="目标：要回答什么投资问题"
            />
            <TextArea
              rows={5}
              value={form.context}
              onChange={event => setForm(prev => ({ ...prev, context: event.target.value }))}
              placeholder="补充约束、已知信息、风控要求；证据层会自动检索资料"
            />
            <Alert type="info" showIcon message={selectedEngine.description} />
            <Button type="primary" block icon={<ThunderboltOutlined />} loading={creating} onClick={handleCreate}>
              启动投研任务
            </Button>
          </Space>
          </CollapsibleSection>

          <div className="agent-queue-head">
            <span>任务队列</span>
            <Badge count={tasks.length} overflowCount={99} />
          </div>
          <div className="agent-run-list">
            {tasks.length === 0 ? (
              <Empty image={Empty.PRESENTED_IMAGE_SIMPLE} description="暂无任务" />
            ) : tasks.map(task => {
              const meta = statusMeta[task.status] || statusMeta.pending;
              const engine = engineMeta[taskEngine(task)];
              const heartbeatDelayed = isHeartbeatDelayed(task);
              return (
                <button
                  key={task.id}
                  className={`agent-run-item ${selectedTask?.id === task.id ? 'active' : ''}`}
                  onClick={() => setSelectedTaskId(task.id)}
                >
                  <span className="agent-run-topline">
                    <Text strong ellipsis>{task.title}</Text>
                    <Tag color={engine.color}>{engine.short}</Tag>
                  </span>
                  <span className="agent-run-meta">
                    <Badge status={heartbeatDelayed ? 'warning' : meta.badge} text={heartbeatDelayed ? '心跳延迟' : meta.text} />
                    <span>{task.symbol || '-'}</span>
                    <span>{task.progress}%</span>
                  </span>
                  <Progress percent={task.progress} showInfo={false} size="small" />
                </button>
              );
            })}
          </div>
        </aside>

        <main className="agent-thread-panel">
          {selectedTask ? (
            <AgentThread task={selectedTask} onRetry={handleRetry} onCancel={handleCancel} />
          ) : (
            <Empty description="启动或选择一个投研任务" />
          )}
        </main>

        <aside className="agent-evidence-panel">
          <EvidencePanel task={selectedTask} />
        </aside>
      </section>

      <section className="agent-artifact-panel">
        <ArtifactPanel task={selectedTask} />
      </section>
    </div>
  );
};

const AgentThread: React.FC<{
  task: InvestmentTaskRecord;
  onRetry: (taskId: string) => void;
  onCancel: (taskId: string) => void;
}> = ({ task, onRetry, onCancel }) => {
  const meta = statusMeta[task.status] || statusMeta.pending;
  const engine = engineMeta[taskEngine(task)];
  const result = task.result;
  const decision = result ? decisionMeta[result.decision] || decisionMeta.research_more : null;
  const heartbeatDelayed = isHeartbeatDelayed(task);
  const heartbeatLag = taskHeartbeatLagMs(task);

  return (
    <Space direction="vertical" size={14} style={{ width: '100%' }}>
      <div className="agent-thread-header">
        <div>
          <Space wrap>
            <Tag color={engine.color}>{engine.title}</Tag>
            <Badge status={meta.badge} text={meta.text} />
            {decision && <Tag color={decision.color}>{decision.text}</Tag>}
          </Space>
          <Title level={4}>{task.title}</Title>
          <Text type="secondary">{task.asset_name || task.symbol || '未指定标的'} · {task.task_type} · {new Date(task.created_at).toLocaleString()}</Text>
        </div>
        <Space>
          {result && (
            <ShareButton
              modalTitle="分享投研任务结论"
              target={() => ({
                title: task.title,
                summary: [
                  decision ? `结论倾向：${decision.text} ｜ 置信度 ${Math.round((result.confidence || 0) * 100)}%` : '',
                  result.investor_summary,
                  result.plain_language_takeaway && result.plain_language_takeaway !== result.investor_summary
                    ? result.plain_language_takeaway
                    : '',
                ].filter(Boolean).join('\n\n'),
                byline: '由 DeepFocus 投研工作台 · 深度任务生成',
              })}
            />
          )}
          {['failed', 'cancelled', 'completed'].includes(task.status) && (
            <Button onClick={() => onRetry(task.id)}>重跑</Button>
          )}
          {['pending', 'running'].includes(task.status) && (
            <Button danger onClick={() => onCancel(task.id)}>取消</Button>
          )}
        </Space>
      </div>

      <InvestmentDecisionRibbon task={task} />
      <Progress percent={task.progress} />
      {heartbeatDelayed && (
        <Alert
          type="warning"
          showIcon
          message={`外部引擎心跳已延迟 ${formatLag(heartbeatLag)}`}
          description="TradingAgents 可能仍在等待模型或行情接口；如果后端热重载打断了父进程，任务会自动失败并可重跑。"
        />
      )}
      {task.error && <Alert type="error" showIcon message={task.error} />}
      {result?.engine_status && result.engine_status !== 'completed' && (
        <Alert
          type="warning"
          showIcon
          message={result.engine_status === 'runtime_error' ? 'TradingAgents 运行失败，等待配置复核' : 'TradingAgents 运行环境待配置'}
          description="DeepFocus 已内置 TradingAgents 引擎，并会读取 设置 → 模型配置；如果这里出现告警，通常是模型 API key、模型名、行情数据源或运行时配置还需要补齐。任务结果中会保留可复核诊断。"
        />
      )}

      <AgentRunMap task={task} />

      <div className="agent-thread-log">
        <div className="agent-panel-head">
          <span><ApiOutlined /> 可审计执行 Trace</span>
          <Text type="secondary">{task.logs.length} 条事件</Text>
        </div>
        <Timeline
          items={task.logs.map(log => {
            const phase = phaseByKey[resolveAgentPhase(log.agent)];
            return {
              color: phase.color,
              children: (
                <Space direction="vertical" size={1}>
                  <Space wrap size={6}>
                    <Text strong>{coreAgentNameFromAgent(log.agent)}</Text>
                    <Tag color={phase.color}>{phase.title}</Tag>
                  </Space>
                  <Text>{log.message}</Text>
                  <Text type="secondary">{new Date(log.timestamp).toLocaleString()}</Text>
                </Space>
              )
            };
          })}
        />
      </div>

      {result ? (
        <div className="agent-findings-panel">
          <div className="agent-panel-head">
            <span><AuditOutlined /> 融合结论</span>
            <Tag color={engine.color}>{result.engine_label || engine.title}</Tag>
          </div>
          <Steps
            direction="vertical"
            size="small"
            items={coreFindingEntries(result.agent_findings || {}).map(([agent, findings]) => ({
              title: agent,
              description: (
                <List
                  size="small"
                  dataSource={findings}
                  renderItem={item => <List.Item>{item}</List.Item>}
                />
              )
            }))}
          />
        </div>
      ) : (
        <div className="agent-empty-state">
          <ClockCircleOutlined />
          <Text>任务正在排队或执行，完成后这里会展示核心链路。</Text>
        </div>
      )}
    </Space>
  );
};

const InvestmentDecisionRibbon: React.FC<{ task: InvestmentTaskRecord }> = ({ task }) => {
  const result = task.result;
  const decision = result ? decisionMeta[result.decision] || decisionMeta.research_more : null;
  const readiness = decisionReadinessScore(task);
  const evidenceCount = result?.evidence?.length || 0;
  const riskCount = result?.risk_controls?.length || 0;
  const latestLog = getLatestLog(task);

  return (
    <div className="agent-decision-ribbon">
      <div className="agent-decision-tile is-primary">
        <Text type="secondary">投资动作</Text>
        <strong>{decision?.text || statusMeta[task.status]?.text || '等待中'}</strong>
        <span>{decisionGuidance(result)}</span>
      </div>
      <div className="agent-decision-tile">
        <Text type="secondary">可执行度</Text>
        <div className="agent-score-line">
          <strong>{readiness}%</strong>
          <Progress percent={readiness} showInfo={false} size="small" />
        </div>
        <span>由置信度、证据、行动、风险和反证共同计算。</span>
      </div>
      <div className="agent-decision-tile">
        <Text type="secondary">证据 / 风控</Text>
        <strong>{evidenceCount} / {riskCount}</strong>
        <span>赚钱前先确认依据，亏损路径必须可见。</span>
      </div>
      <div className="agent-decision-tile">
        <Text type="secondary">当前环节</Text>
        <strong>{coreAgentNameFromAgent(task.assigned_agent || latestLog?.agent)}</strong>
        <span>{latestLog?.message || '等待调度。'}</span>
      </div>
    </div>
  );
};

const AgentRunMap: React.FC<{ task: InvestmentTaskRecord }> = ({ task }) => {
  const activePhase = resolveAgentPhase(task.assigned_agent);
  const loggedPhases = new Set(task.logs.map(log => resolveAgentPhase(log.agent)));
  const phaseItems = phaseDefinitions.map(phase => {
    const hasLog = loggedPhases.has(phase.key);
    const status = task.status === 'failed' && phase.key === activePhase
      ? 'error'
      : task.status === 'completed' || hasLog
        ? 'finish'
        : phase.key === activePhase || (task.status === 'pending' && phase.key === 'orchestrator')
          ? 'process'
          : 'wait';

    return {
      title: phase.title,
      description: phase.goal,
      icon: phase.icon,
      status: status as 'wait' | 'process' | 'finish' | 'error'
    };
  });

  return (
    <div className="agent-run-map">
      <div className="agent-panel-head">
        <span><LineChartOutlined /> 核心链路运行图</span>
        <Tag color={phaseByKey[activePhase].color}>{phaseByKey[activePhase].title}</Tag>
      </div>
      <Steps className="agent-stage-steps" size="small" items={phaseItems} />
    </div>
  );
};

const EvidencePanel: React.FC<{ task: InvestmentTaskRecord | null }> = ({ task }) => {
  const result = task?.result;
  const evidence = result?.evidence || [];
  const averageCredibility = evidence.length
    ? Math.round(evidence.reduce((sum, item) => sum + item.credibility_score, 0) / evidence.length * 100)
    : 0;
  return (
    <CollapsibleSection
      title={<><DatabaseOutlined /> 证据层</>}
      extra={<Tag>{evidence.length} 条</Tag>}
      defaultOpen={true}
      level={2}
    >
      <div className="agent-evidence-scorecard">
        <span>
          <Text type="secondary">平均可信度</Text>
          <strong>{averageCredibility || '-'}{averageCredibility ? '%' : ''}</strong>
        </span>
        <span>
          <Text type="secondary">资料状态</Text>
          <strong>{evidence.length > 0 ? '可复核' : '待补证'}</strong>
        </span>
      </div>
      <Descriptions column={1} size="small">
        <Descriptions.Item label="标的">{task?.asset_name || task?.symbol || '-'}</Descriptions.Item>
        <Descriptions.Item label="引擎">{task ? engineMeta[taskEngine(task)].title : '-'}</Descriptions.Item>
        <Descriptions.Item label="资料策略">数据源中心 + 本地资料 + 网页抓取</Descriptions.Item>
      </Descriptions>
      {evidence.length > 0 ? (
        <List
          className="agent-evidence-list"
          dataSource={evidence}
          renderItem={item => (
            <List.Item>
              <Space direction="vertical" size={4}>
                <Space wrap>
                  <Text strong>{item.title}</Text>
                  <Tag>{item.source}</Tag>
                  <Tag color="gold">{Math.round(item.credibility_score * 100)}%</Tag>
                </Space>
                <Text>{item.takeaway || '该证据已进入任务上下文。'}</Text>
                {item.url && <Text type="secondary" copyable>{item.url}</Text>}
              </Space>
            </List.Item>
          )}
        />
      ) : (
        <Alert
          type="warning"
          showIcon
          message="暂无可展示证据"
          description="任务完成后会显示被核心链路引用的研报、新闻、公告或本地资料。资料不足时，报告会明确提示缺口。"
        />
      )}
      <div className="agent-tool-strip">
        <span><FileSearchOutlined /> 研报解析</span>
        <span><DatabaseOutlined /> 数据源</span>
        <span><SafetyCertificateOutlined /> 风控</span>
      </div>
    </CollapsibleSection>
  );
};

const ArtifactPanel: React.FC<{ task: InvestmentTaskRecord | null }> = ({ task }) => {
  const result = task?.result;
  if (!task) {
    return (
      <div className="agent-artifact-empty">
        <FileSearchOutlined />
        <Text>选择一个投研任务后，报告、情景、风险和原始输出会出现在这里。</Text>
      </div>
    );
  }

  if (!result) {
    return (
      <div className="agent-artifact-empty">
        <ClockCircleOutlined />
        <Text>Artifact 等待生成中。</Text>
      </div>
    );
  }

  const decision = decisionMeta[result.decision] || decisionMeta.research_more;
  const readiness = decisionReadinessScore(task);
  const scenarios = result.scenarios || [];
  const riskControls = result.risk_controls || [];
  const actionPlan = result.action_plan || [];
  const disconfirmingEvidence = result.disconfirming_evidence || [];
  const watchlist = result.watchlist || [];

  return (
    <div className="agent-artifact-grid">
      <section className="agent-artifact-main">
        <div className="agent-panel-head">
          <span><CheckCircleOutlined /> 投资决策报告</span>
          <Tag color={decision.color}>{decision.text}</Tag>
        </div>
        <div className="agent-verdict-top">
          <div>
            <Text type="secondary">Capital Action</Text>
            <Title level={4}>{decision.text}</Title>
          </div>
          <Statistic title="可执行度" value={readiness} suffix="%" />
        </div>
        <div className={`agent-action-callout decision-${result.decision}`}>
          <strong>{decisionGuidance(result)}</strong>
          <span>目标是提高投资决策质量和收益机会筛选效率，不是自动下单或保证收益。</span>
        </div>
        <Paragraph>{result.investor_summary}</Paragraph>
        <div className="agent-plain-takeaway">{result.plain_language_takeaway}</div>
        <div className="agent-confidence-row">
          <Statistic title="置信度" value={result.confidence * 100} precision={0} suffix="%" />
          <Progress percent={Math.round(result.confidence * 100)} />
        </div>
      </section>

      <section className="agent-artifact-list">
        <div className="agent-panel-head"><span><BranchesOutlined /> 情景推演</span></div>
        <List
          size="small"
          dataSource={scenarios}
          renderItem={scenario => (
            <List.Item>
              <Space direction="vertical" size={3}>
                <Text strong>{scenario.case} · {scenario.probability}%</Text>
                <Text>{scenario.thesis}</Text>
                <Space wrap>{scenario.triggers.map(item => <Tag key={item}>{item}</Tag>)}</Space>
              </Space>
            </List.Item>
          )}
        />
      </section>

      <section className="agent-artifact-list">
        <div className="agent-panel-head"><span><WarningOutlined /> 风险纪律</span></div>
        <List size="small" dataSource={riskControls} renderItem={item => <List.Item>{item}</List.Item>} />
      </section>

      <section className="agent-artifact-list">
        <div className="agent-panel-head"><span><OrderedListOutlined /> 下一步动作</span></div>
        <List size="small" dataSource={actionPlan} renderItem={item => <List.Item>{item}</List.Item>} />
      </section>

      <section className="agent-artifact-list">
        <div className="agent-panel-head"><span><SafetyCertificateOutlined /> 反证清单</span></div>
        <List size="small" dataSource={disconfirmingEvidence} renderItem={item => <List.Item>{item}</List.Item>} />
      </section>

      <section className="agent-artifact-list">
        <div className="agent-panel-head"><span><LineChartOutlined /> 观察清单</span></div>
        <List size="small" dataSource={watchlist} renderItem={item => <List.Item>{item}</List.Item>} />
      </section>

      {result.artifacts?.map(artifact => (
        <section key={`${artifact.type}-${artifact.title}`} className="agent-artifact-raw">
          <div className="agent-panel-head"><span>{artifact.title}</span></div>
          <pre>{artifact.content}</pre>
        </section>
      ))}

      <Alert type="warning" showIcon message={result.disclaimer} />
    </div>
  );
};

export default React.memo(InvestorAgentCenter);
