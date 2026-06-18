export type CoreAgentPhase = 'orchestrator' | 'evidence' | 'research' | 'risk' | 'report';

export const coreAgentPhases: CoreAgentPhase[] = [
  'orchestrator',
  'evidence',
  'research',
  'risk',
  'report'
];

export const coreAgentNameByPhase: Record<CoreAgentPhase, string> = {
  orchestrator: 'Orchestrator',
  evidence: 'Evidence',
  research: 'Analyst',
  risk: 'Risk',
  report: 'Report Builder'
};

export const coreAgentPhaseLabel: Record<CoreAgentPhase, string> = {
  orchestrator: '编排',
  evidence: '证据',
  research: '研判',
  risk: '风控',
  report: '报告'
};

export const coreAgentPhaseTitle: Record<CoreAgentPhase, string> = {
  orchestrator: '任务编排',
  evidence: '证据检索',
  research: '投资研判',
  risk: '风险复核',
  report: '报告生成'
};

const phaseTokens: Array<[CoreAgentPhase, string[]]> = [
  ['evidence', ['evidence', 'datasource', 'setup']],
  [
    'research',
    [
      'analyst',
      'research',
      'sentiment',
      'scenario',
      'debate',
      'trader',
      'fsiworkflow',
      'modelbuilder',
      'earnings',
      'valuation',
      'pitch',
      'technical',
      'fundamental',
      'market',
      'news'
    ]
  ],
  ['risk', ['risk', 'control', 'kyc', 'reconciler', 'reconciliation']],
  ['report', ['portfolio', 'report', 'resultmapper', 'modelrouter']],
  ['orchestrator', ['orchestrator', 'taskcenter', 'adapter']]
];

const findingPhaseOverrides: Record<string, CoreAgentPhase> = {
  adapter: 'orchestrator',
  setup: 'evidence',
  model: 'report'
};

const displayTextReplacements: Array<[string, string]> = [
  ['OrchestratorAgent', 'Orchestrator'],
  ['EvidenceAgent', 'Evidence'],
  ['ResearchAgent', 'Analyst'],
  ['RiskAgent', 'Risk'],
  ['ReportAgent', 'Report Builder'],
  ['Research Agent', 'Analyst'],
  ['Report Agent', 'Report Builder'],
  ['5 个核心 Agent', '4 个核心角色 + 报告输出层'],
  ['五个核心 Agent', '4 个核心角色 + 报告输出层'],
  ['多 Agent Run', '投研任务'],
  ['多 Agent 工作台', '投研工作台'],
  ['多 Agent', '核心链路'],
  ['投研任务 收束', '投研任务收束'],
  ['核心链路 证据范围', '核心链路；证据范围'],
  ['核心链路证据范围', '核心链路；证据范围']
];

const findMatchingPhase = (agent?: string | null): CoreAgentPhase | null => {
  const normalized = (agent || '').toLowerCase();
  for (const [phase, tokens] of phaseTokens) {
    if (tokens.some(token => normalized.includes(token))) {
      return phase;
    }
  }
  return null;
};

export const resolveAgentPhase = (agent?: string | null): CoreAgentPhase => {
  return findMatchingPhase(agent) || 'orchestrator';
};

export const coreAgentNameFromAgent = (agent?: string | null): string => (
  coreAgentNameByPhase[resolveAgentPhase(agent)]
);

export const coreFindingTitle = (agent: string): string => {
  const normalized = agent.toLowerCase();
  const phase = findingPhaseOverrides[normalized] || findMatchingPhase(agent);
  return phase ? coreAgentNameByPhase[phase] : agent;
};

export const sanitizeAgentDisplayText = (value?: string | null): string => {
  let text = value || '';
  displayTextReplacements.forEach(([oldText, newText]) => {
    text = text.split(oldText).join(newText);
  });
  return text
    .replace(/投研任务\s+收束/g, '投研任务收束')
    .replace(/核心链路\s+证据范围/g, '核心链路；证据范围');
};
