import React, { useEffect, useState } from 'react';
import { Button, Space, Tag, Typography } from 'antd';
import {
  ApiOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  FileTextOutlined,
  FundProjectionScreenOutlined,
  LineChartOutlined,
  SafetyCertificateOutlined,
  WarningOutlined
} from '@ant-design/icons';
import {
  AGENT_RUN_EVENT_CONTRACT,
  AgentLogEntry,
  AgentRunEvent,
  AgentRunEventType,
  InvestmentTaskRecord
} from '../../services/agentTaskService';

const { Text } = Typography;

export type AgentBlockProjection =
  | 'run_state'
  | 'reasoning'
  | 'tool'
  | 'approval'
  | 'artifact'
  | 'control'
  | 'error';

export type AgentBlockStatus = 'working' | 'done' | 'wait' | 'error';

export interface AgentChatBlock {
  id: string;
  type: AgentRunEventType;
  projection: AgentBlockProjection;
  phase: string;
  agent: string;
  title: string;
  detail: string;
  progress?: number | null;
  createdAt: string;
  status: AgentBlockStatus;
  meta?: string[];
  payload?: Record<string, any>;
}

const phaseLabel: Record<string, string> = {
  orchestrator: '编排',
  evidence: '证据',
  research: '研判',
  risk: '风控',
  report: '报告',
  progress: '进度'
};

const statusLabel: Record<AgentBlockStatus, string> = {
  working: '运行中',
  done: '完成',
  wait: '等待',
  error: '异常'
};

const decisionLabel: Record<string, string> = {
  avoid: '暂不行动',
  watch: '观察',
  research_more: '继续研究',
  candidate: '候选机会'
};

const phaseFromAgent = (agent?: string | null): string => {
  const text = (agent || '').toLowerCase();
  if (text.includes('evidence') || text.includes('datasource')) return 'evidence';
  if (
    text.includes('analyst') ||
    text.includes('research') ||
    text.includes('sentiment') ||
    text.includes('scenario') ||
    text.includes('debate') ||
    text.includes('trader') ||
    text.includes('fsiworkflow') ||
    text.includes('modelbuilder') ||
    text.includes('earnings') ||
    text.includes('valuation') ||
    text.includes('pitch')
  ) return 'research';
  if (
    text.includes('risk') ||
    text.includes('control') ||
    text.includes('kyc') ||
    text.includes('reconciler') ||
    text.includes('reconciliation')
  ) return 'risk';
  if (text.includes('portfolio') || text.includes('report') || text.includes('resultmapper') || text.includes('modelrouter')) {
    return 'report';
  }
  return 'orchestrator';
};

const coreAgentName = (agent?: string | null): string => {
  const phase = phaseFromAgent(agent);
  return {
    orchestrator: 'OrchestratorAgent',
    evidence: 'EvidenceAgent',
    research: 'ResearchAgent',
    risk: 'RiskAgent',
    report: 'ReportAgent'
  }[phase] || 'OrchestratorAgent';
};

const projectionFromType = (type: AgentRunEventType): AgentBlockProjection => (
  AGENT_RUN_EVENT_CONTRACT[type]?.uiProjection || 'reasoning'
);

const statusFromType = (type: AgentRunEventType): AgentBlockStatus => {
  if (type === 'error') return 'error';
  if (type === 'approval_required') return 'wait';
  if (type === 'run_complete' || type === 'tool_result' || type === 'artifact_update') return 'done';
  return 'working';
};

const eventTypeFromLog = (log: AgentLogEntry, index: number, total: number): AgentRunEventType => {
  const agent = log.agent.toLowerCase();
  const message = log.message.toLowerCase();
  if (message.includes('失败') || message.includes('error') || message.includes('异常')) return 'error';
  if (index === 0 || agent.includes('taskcenter') || agent.includes('orchestrator')) return 'run_state';
  if (agent.includes('evidence') || agent.includes('datasource') || message.includes('资料') || message.includes('证据')) {
    return message.includes('命中') || index === total - 1 ? 'tool_result' : 'tool_progress';
  }
  if (agent.includes('report') || agent.includes('portfolio')) return 'artifact_update';
  return 'reasoning_delta';
};

const titleForPhase = (phase: string): string => ({
  orchestrator: '任务编排',
  evidence: '证据检索',
  research: '投资研判',
  risk: '风险复核',
  report: '报告生成'
}[phase] || 'Agent 事件');

const stateTitle = (task: InvestmentTaskRecord): string => ({
  pending: '任务排队中',
  running: 'Agent Run 执行中',
  waiting_approval: '等待投资者确认',
  completed: 'Agent Run 已完成',
  failed: 'Agent Run 失败',
  cancelled: 'Agent Run 已取消'
}[task.status] || 'Agent Run 状态');

const taskStatusForBlock = (task: InvestmentTaskRecord): AgentBlockStatus => {
  if (task.status === 'failed' || task.status === 'cancelled') return 'error';
  if (task.status === 'waiting_approval') return 'wait';
  if (task.status === 'completed') return 'done';
  return 'working';
};

export const blockFromAgentRunEvent = (event: AgentRunEvent): AgentChatBlock => ({
  id: event.id,
  type: event.type,
  projection: projectionFromType(event.type),
  phase: event.phase,
  agent: event.agent,
  title: event.title,
  detail: event.message,
  progress: event.progress,
  createdAt: event.created_at,
  status: statusFromType(event.type),
  meta: [
    phaseLabel[event.phase] || event.phase,
    event.type
  ].filter(Boolean),
  payload: event.payload
});

export const mergeAgentBlock = (
  blocks: AgentChatBlock[] | undefined,
  next: AgentChatBlock,
  maxBlocks = 8
): AgentChatBlock[] => {
  const filtered = (blocks || []).filter(block => block.id !== next.id);
  return [...filtered, next].slice(-maxBlocks);
};

export const blocksFromInvestmentTask = (task: InvestmentTaskRecord): AgentChatBlock[] => {
  const statePhase = phaseFromAgent(task.assigned_agent);
  const blocks: AgentChatBlock[] = [{
    id: `${task.id}:state:${task.status}:${task.progress}`,
    type: 'run_state',
    projection: 'run_state',
    phase: statePhase,
    agent: coreAgentName(task.assigned_agent),
    title: stateTitle(task),
    detail: `${task.title} · ${task.progress}%`,
    progress: task.progress,
    createdAt: task.updated_at,
    status: taskStatusForBlock(task),
    meta: [task.engine, task.symbol || task.asset_name || 'portfolio']
  }];

  const recentLogs = task.logs.slice(-5);
  recentLogs.forEach((log, index) => {
    const originalIndex = task.logs.length - recentLogs.length + index;
    const type = eventTypeFromLog(log, originalIndex, task.logs.length);
    const phase = phaseFromAgent(log.agent);
    blocks.push({
      id: `${task.id}:log:${originalIndex}:${type}`,
      type,
      projection: projectionFromType(type),
      phase,
      agent: coreAgentName(log.agent),
      title: titleForPhase(phase),
      detail: log.message,
      progress: log.progress ?? (originalIndex === task.logs.length - 1 ? task.progress : null),
      createdAt: log.timestamp,
      status: type === 'error' ? 'error' : taskStatusForBlock(task),
      meta: [phaseLabel[phase] || phase, type]
    });
  });

  if (task.result) {
    blocks.push({
      id: `${task.id}:artifact:investment-report`,
      type: 'artifact_update',
      projection: 'artifact',
      phase: 'report',
      agent: 'ReportAgent',
      title: '投资决策报告',
      detail: task.result.plain_language_takeaway || task.result.investor_summary || '报告已生成。',
      progress: 100,
      createdAt: task.completed_at || task.updated_at,
      status: 'done',
      meta: [
        decisionLabel[task.result.decision] || task.result.decision,
        `置信度 ${Math.round((task.result.confidence || 0) * 100)}%`
      ],
      payload: {
        artifact_type: 'investment_report',
        decision: task.result.decision,
        confidence: task.result.confidence,
        risk_controls: task.result.risk_controls || [],
        action_plan: task.result.action_plan || []
      }
    });
  }

  return blocks.slice(-8);
};

const blockIcon = (block: AgentChatBlock) => {
  if (block.projection === 'error') return <WarningOutlined />;
  if (block.projection === 'artifact') return <FileTextOutlined />;
  if (block.projection === 'approval') return <SafetyCertificateOutlined />;
  if (block.projection === 'tool') return <DatabaseOutlined />;
  if (block.projection === 'control') return <CheckCircleOutlined />;
  if (block.phase === 'risk') return <SafetyCertificateOutlined />;
  if (block.phase === 'research') return <LineChartOutlined />;
  if (block.phase === 'report') return <FundProjectionScreenOutlined />;
  if (block.phase === 'evidence') return <ApiOutlined />;
  return <ClockCircleOutlined />;
};

const clampProgress = (value?: number | null) => {
  if (typeof value !== 'number' || Number.isNaN(value)) return null;
  return Math.max(0, Math.min(100, value));
};

const InvestmentReportMini: React.FC<{ block: AgentChatBlock }> = ({ block }) => {
  const payload = block.payload || {};
  const actionPlan = Array.isArray(payload.action_plan)
    ? (payload.action_plan as string[]).slice(0, 2)
    : [];
  const riskControls = Array.isArray(payload.risk_controls)
    ? (payload.risk_controls as string[]).slice(0, 2)
    : [];
  const decision = String(payload.decision || '');
  const confidence = typeof payload.confidence === 'number'
    ? Math.round(payload.confidence * 100)
    : null;

  if (!decision && actionPlan.length === 0 && riskControls.length === 0 && confidence == null) {
    return null;
  }

  return (
    <div className="agent-run-artifact-mini">
      <div>
        <span>动作</span>
        <strong>{decisionLabel[decision] || decision || '待复核'}</strong>
      </div>
      <div>
        <span>置信度</span>
        <strong>{confidence != null ? `${confidence}%` : '--'}</strong>
      </div>
      {actionPlan.length > 0 && (
        <div>
          <span>下一步</span>
          <ul>
            {actionPlan.map((item: string) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
      {riskControls.length > 0 && (
        <div>
          <span>风控</span>
          <ul>
            {riskControls.map((item: string) => <li key={item}>{item}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
};

export const AgentRunBlocks: React.FC<{
  blocks?: AgentChatBlock[];
  compact?: boolean;
}> = ({ blocks, compact = false }) => {
  const [manualOpen, setManualOpen] = useState<boolean | null>(null);

  useEffect(() => {
    setManualOpen(null);
  }, [compact]);

  if (!blocks || blocks.length === 0) {
    return null;
  }

  const open = manualOpen ?? !compact;
  const completed = blocks.some(block => (
    block.type === 'run_complete'
    || (block.progress === 100 && (block.projection === 'artifact' || block.title.includes('已完成')))
  ));
  const latest = blocks[blocks.length - 1];
  const progress = clampProgress(latest?.progress);
  const visibleBlocks = compact
    ? blocks.filter(block => (
        block.projection !== 'reasoning'
        || block.status === 'error'
        || block.status === 'wait'
        || block.type === 'run_complete'
      )).slice(-5)
    : blocks;
  const displayBlocks = visibleBlocks.length > 0 ? visibleBlocks : blocks.slice(-3);
  const hiddenCount = Math.max(0, blocks.length - displayBlocks.length);

  return (
    <div className={`agent-run-blocks ${open ? 'open' : 'collapsed'} ${compact ? 'compact' : 'full'}`}>
      <div className="agent-run-blocks-head">
        <span>Agent 执行流</span>
        <Space size={6}>
          <Text type="secondary">{completed ? '已完成' : `${blocks.length} 个事件`}</Text>
          <Button size="small" type="text" onClick={() => setManualOpen(value => !(value ?? !compact))}>
            {open ? '收起' : '展开'}
          </Button>
        </Space>
      </div>
      {!open && (
        <div className="agent-run-block-summary">
          <Text type="secondary">
            {latest ? `${latest.agent} · ${latest.title}${progress != null ? ` · ${progress}%` : ''}` : '等待 Agent 事件'}
          </Text>
        </div>
      )}
      {open && (
      <div className="agent-run-block-list">
        {displayBlocks.map(block => {
          const progress = clampProgress(block.progress);
          return (
            <section key={block.id} className={`agent-run-block ${block.projection} ${block.status}`}>
              <div className="agent-run-block-icon">{blockIcon(block)}</div>
              <div className="agent-run-block-body">
                <div className="agent-run-block-title">
                  <strong>{block.title}</strong>
                  <Space size={[4, 4]} wrap>
                    <Tag>{block.agent}</Tag>
                    <Tag>{statusLabel[block.status]}</Tag>
                  </Space>
                </div>
                <Text type="secondary">{block.detail}</Text>
                {block.meta && block.meta.length > 0 && (
                  <div className="agent-run-block-meta">
                    {block.meta.slice(0, 4).map(item => <span key={item}>{item}</span>)}
                  </div>
                )}
                {progress != null && (
                  <div className="agent-run-progress" aria-label={`Agent progress ${progress}%`}>
                    <span style={{ width: `${progress}%` }} />
                  </div>
                )}
                {block.projection === 'artifact' && <InvestmentReportMini block={block} />}
              </div>
            </section>
          );
        })}
        {hiddenCount > 0 && (
          <Text type="secondary" className="agent-run-block-hidden">
            已折叠 {hiddenCount} 个中间事件
          </Text>
        )}
      </div>
      )}
    </div>
  );
};
