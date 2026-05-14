import React, { useMemo, useState } from 'react';
import {
  App as AntdApp,
  Button,
  Descriptions,
  Empty,
  Input,
  Progress,
  Segmented,
  Space,
  Switch,
  Tag,
  Timeline,
  Typography
} from 'antd';
import {
  ApiOutlined,
  AuditOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FilterOutlined,
  LineChartOutlined,
  RobotOutlined,
  SafetyCertificateOutlined,
  ToolOutlined
} from '@ant-design/icons';
import { AppState } from '../types';

const { Paragraph, Text } = Typography;
const { Search } = Input;

type SkillCategory = 'all' | 'research' | 'data' | 'risk' | 'workflow' | 'monitor';
type SkillRisk = 'low' | 'medium' | 'high';
type SkillMaturity = 'production' | 'beta' | 'draft';

interface SkillDefinition {
  id: string;
  name: string;
  category: Exclude<SkillCategory, 'all'>;
  description: string;
  agents: string[];
  inputs: string[];
  outputs: string[];
  dependencies: string[];
  permissions: string[];
  maturity: SkillMaturity;
  risk: SkillRisk;
  latency: string;
  successRate: number;
  usage: number;
  invocation: string;
}

interface SkillTemplate {
  name: string;
  objective: string;
  skills: string[];
}

interface SkillCenterProps {
  appState: AppState;
}

const categoryMeta: Record<SkillCategory, { label: string; icon?: React.ReactNode }> = {
  all: { label: '全部技能', icon: <ToolOutlined /> },
  research: { label: '投研分析', icon: <LineChartOutlined /> },
  data: { label: '数据处理', icon: <DatabaseOutlined /> },
  risk: { label: '风控合规', icon: <SafetyCertificateOutlined /> },
  workflow: { label: '任务编排', icon: <BranchesOutlined /> },
  monitor: { label: '监控预警', icon: <ClockCircleOutlined /> }
};

const maturityMeta: Record<SkillMaturity, { label: string; color: string }> = {
  production: { label: '生产可用', color: 'green' },
  beta: { label: 'Beta', color: 'blue' },
  draft: { label: '草稿', color: 'default' }
};

const riskMeta: Record<SkillRisk, { label: string; color: string }> = {
  low: { label: '低风险', color: 'green' },
  medium: { label: '中风险', color: 'gold' },
  high: { label: '高风险', color: 'red' }
};

const skillDefinitions: SkillDefinition[] = [
  {
    id: 'market-sentiment-scan',
    name: '市场情绪扫描',
    category: 'research',
    description: '汇总新闻、社区讨论和研报摘要，给出标的短期情绪、分歧点和需要核验的催化因素。',
    agents: ['市场情绪 Agent', '投研分析 Agent'],
    inputs: ['stock.symbol', 'news_items[]', 'community_posts[]', 'time_window'],
    outputs: ['sentiment_label', 'sentiment_score', 'key_drivers[]', 'watch_items[]'],
    dependencies: ['FinGPT 情绪能力', '数据源中心', '社区内容'],
    permissions: ['读取公开资料', '读取社区内容'],
    maturity: 'production',
    risk: 'low',
    latency: '10-30 秒',
    successRate: 96,
    usage: 128,
    invocation: 'sentiment.scan({ symbol, sources, window: "24h" })'
  },
  {
    id: 'filing-report-parser',
    name: '财报与公告拆解',
    category: 'research',
    description: '解析财报、公告和上传文件，抽取营收、利润、现金流、指引变化和管理层风险表述。',
    agents: ['财报解读 Agent', '证据审查 Agent'],
    inputs: ['document_text', 'stock_profile', 'period'],
    outputs: ['financial_highlights[]', 'guidance_changes[]', 'risk_terms[]', 'investor_takeaway'],
    dependencies: ['文件解析', 'RAG 检索', 'FinGPT 报告分析'],
    permissions: ['读取上传文件', '写入资料标签'],
    maturity: 'production',
    risk: 'medium',
    latency: '30-90 秒',
    successRate: 91,
    usage: 74,
    invocation: 'filing.parse({ file_id, symbol, period })'
  },
  {
    id: 'rag-evidence-retrieval',
    name: '证据检索 RAG',
    category: 'data',
    description: '从数据源中心检索与任务相关的资料，返回证据来源、可信度、标签和可引用摘要。',
    agents: ['资料检索 Agent', '审计治理 Agent'],
    inputs: ['question', 'tags[]', 'source_scope[]'],
    outputs: ['evidence[]', 'source_scores[]', 'missing_evidence[]'],
    dependencies: ['数据源中心', '本地资料库', '网页抓取结果'],
    permissions: ['读取资料库', '读取来源元数据'],
    maturity: 'production',
    risk: 'low',
    latency: '5-20 秒',
    successRate: 94,
    usage: 203,
    invocation: 'rag.retrieve({ question, tags, top_k: 8 })'
  },
  {
    id: 'risk-discipline-check',
    name: '风险纪律审查',
    category: 'risk',
    description: '对 Agent 输出做二次审查，检查过度自信、缺少反证、仓位建议越界和高风险措辞。',
    agents: ['风控 Agent', '审计治理 Agent'],
    inputs: ['agent_report', 'investor_profile', 'risk_limits'],
    outputs: ['risk_flags[]', 'required_disclaimers[]', 'approval_required'],
    dependencies: ['风险规则库', '投资者画像'],
    permissions: ['读取用户风险画像', '读取任务报告'],
    maturity: 'production',
    risk: 'high',
    latency: '5-15 秒',
    successRate: 98,
    usage: 86,
    invocation: 'risk.review({ report, profile, limits })'
  },
  {
    id: 'scenario-matrix',
    name: '情景矩阵生成',
    category: 'research',
    description: '把核心假设拆成乐观、中性、悲观情景，生成触发条件、概率和跟踪指标。',
    agents: ['策略 Agent', '投研分析 Agent'],
    inputs: ['thesis', 'evidence[]', 'horizon'],
    outputs: ['scenarios[]', 'triggers[]', 'disconfirming_evidence[]'],
    dependencies: ['证据检索 RAG', 'FinGPT 预测能力'],
    permissions: ['读取任务上下文'],
    maturity: 'beta',
    risk: 'medium',
    latency: '15-45 秒',
    successRate: 88,
    usage: 51,
    invocation: 'scenario.matrix({ thesis, evidence, horizon })'
  },
  {
    id: 'watchlist-monitor',
    name: '观察名单监控',
    category: 'monitor',
    description: '定时扫描关注池标的，发现价格、情绪、新闻和资料更新异常时生成预警任务。',
    agents: ['监控 Agent', '市场情绪 Agent'],
    inputs: ['watchlist[]', 'rules[]', 'schedule'],
    outputs: ['alerts[]', 'triggered_tasks[]'],
    dependencies: ['关注池', '数据源中心', '市场行情'],
    permissions: ['读取关注池', '创建 Agent 任务'],
    maturity: 'beta',
    risk: 'medium',
    latency: '定时任务',
    successRate: 84,
    usage: 39,
    invocation: 'watchlist.monitor({ symbols, rules, cadence: "daily" })'
  },
  {
    id: 'portfolio-review',
    name: '组合复盘',
    category: 'workflow',
    description: '把持仓、关注池和近期事件组合成复盘任务，输出风险暴露、拥挤交易和下一步核验清单。',
    agents: ['组合复盘 Agent', '风控 Agent', '策略 Agent'],
    inputs: ['portfolio[]', 'watchlist[]', 'events[]'],
    outputs: ['exposures[]', 'risk_controls[]', 'action_plan[]'],
    dependencies: ['风险纪律审查', '情景矩阵生成'],
    permissions: ['读取组合草稿', '读取关注池'],
    maturity: 'draft',
    risk: 'high',
    latency: '1-3 分钟',
    successRate: 72,
    usage: 12,
    invocation: 'portfolio.review({ holdings, watchlist, horizon })'
  },
  {
    id: 'agent-brief-router',
    name: 'Agent Brief 路由',
    category: 'workflow',
    description: '根据用户问题自动选择 Agent、技能序列和数据源范围，生成可执行的任务 Brief。',
    agents: ['任务路由 Agent', '协调 Agent'],
    inputs: ['user_objective', 'asset_context', 'available_skills[]'],
    outputs: ['assigned_agents[]', 'skill_plan[]', 'task_brief'],
    dependencies: ['技能注册表', '数据源中心', 'Agent 任务中心'],
    permissions: ['读取技能配置', '创建任务草稿'],
    maturity: 'beta',
    risk: 'medium',
    latency: '5-10 秒',
    successRate: 89,
    usage: 67,
    invocation: 'brief.route({ objective, context, constraints })'
  }
];

const orchestrationTemplates: SkillTemplate[] = [
  {
    name: '个股深度研究',
    objective: '从资料、情绪、财报和风险纪律四层生成投资者报告。',
    skills: ['证据检索 RAG', '市场情绪扫描', '财报与公告拆解', '情景矩阵生成', '风险纪律审查']
  },
  {
    name: '盘前风险扫描',
    objective: '开盘前检查关注池异常，生成需要人工复核的预警。',
    skills: ['观察名单监控', '市场情绪扫描', '风险纪律审查']
  },
  {
    name: '组合复盘',
    objective: '复盘持仓暴露和事件冲击，给出下一步验证计划。',
    skills: ['组合复盘', '证据检索 RAG', '情景矩阵生成', '风险纪律审查']
  }
];

const SkillCenter: React.FC<SkillCenterProps> = ({ appState }) => {
  const { message } = AntdApp.useApp();
  const [activeCategory, setActiveCategory] = useState<SkillCategory>('all');
  const [keyword, setKeyword] = useState('');
  const [enabledIds, setEnabledIds] = useState<string[]>(
    skillDefinitions
      .filter(skill => skill.maturity !== 'draft')
      .map(skill => skill.id)
  );
  const [selectedSkillId, setSelectedSkillId] = useState(skillDefinitions[0].id);

  const filteredSkills = useMemo(() => {
    const normalizedKeyword = keyword.trim().toLowerCase();
    return skillDefinitions.filter(skill => {
      const matchCategory = activeCategory === 'all' || skill.category === activeCategory;
      const matchKeyword = !normalizedKeyword ||
        skill.name.toLowerCase().includes(normalizedKeyword) ||
        skill.description.toLowerCase().includes(normalizedKeyword) ||
        skill.agents.some(agent => agent.toLowerCase().includes(normalizedKeyword));
      return matchCategory && matchKeyword;
    });
  }, [activeCategory, keyword]);

  const selectedSkill = skillDefinitions.find(skill => skill.id === selectedSkillId) || filteredSkills[0] || skillDefinitions[0];
  const enabledCount = enabledIds.length;
  const productionCount = skillDefinitions.filter(skill => skill.maturity === 'production').length;
  const highRiskCount = skillDefinitions.filter(skill => skill.risk === 'high').length;

  const toggleSkill = (skillId: string, enabled: boolean) => {
    setEnabledIds(prev => enabled
      ? Array.from(new Set([...prev, skillId]))
      : prev.filter(id => id !== skillId)
    );
  };

  const handleCopyInvocation = async () => {
    try {
      await navigator.clipboard.writeText(selectedSkill.invocation);
      message.success('调用模板已复制');
    } catch {
      message.error('复制失败，请手动复制调用模板');
    }
  };

  return (
    <div className="skill-center-shell">
      <div className="skill-hero">
        <div>
          <div className="dashboard-eyebrow">SKILL REGISTRY</div>
          <h2 className="dashboard-title">Agent 技能库</h2>
          <div className="dashboard-subtitle">
            把 Agent 能力拆成可治理、可编排、可复用的技能单元，明确输入输出、权限边界和风险等级。
          </div>
        </div>
        <Space wrap>
          <Tag color="blue"><RobotOutlined /> {appState.stocks.length} 个标的上下文</Tag>
          <Tag color="green"><CheckCircleOutlined /> {enabledCount} 个已启用</Tag>
        </Space>
      </div>

      <div className="skill-overview-grid">
        <div className="metric-tile">
          <div className="metric-label"><ToolOutlined /> 技能总数</div>
          <div className="metric-value">{skillDefinitions.length}</div>
          <div className="metric-note">覆盖投研、资料、风控和监控</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><CheckCircleOutlined /> 生产可用</div>
          <div className="metric-value">{productionCount}</div>
          <div className="metric-note">可直接进入多 Agent 编排</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><SafetyCertificateOutlined /> 高风险技能</div>
          <div className="metric-value">{highRiskCount}</div>
          <div className="metric-note">需要审查和人工确认</div>
        </div>
        <div className="metric-tile">
          <div className="metric-label"><ApiOutlined /> 本周调用</div>
          <div className="metric-value">
            {skillDefinitions.reduce((sum, skill) => sum + skill.usage, 0)}
          </div>
          <div className="metric-note">基于模拟注册表统计</div>
        </div>
      </div>

      <div className="skill-toolbar">
        <Segmented
          value={activeCategory}
          onChange={value => setActiveCategory(value as SkillCategory)}
          options={(Object.keys(categoryMeta) as SkillCategory[]).map(key => ({
            value: key,
            label: (
              <Space size={6}>
                {categoryMeta[key].icon}
                <span>{categoryMeta[key].label}</span>
              </Space>
            )
          }))}
        />
        <Search
          allowClear
          placeholder="搜索技能、Agent 或能力描述"
          value={keyword}
          onChange={event => setKeyword(event.target.value)}
          style={{ width: 320 }}
        />
      </div>

      <div className="skill-layout">
        <section className="skill-catalog">
          {filteredSkills.length === 0 ? (
            <Empty description="没有匹配的技能" />
          ) : filteredSkills.map(skill => {
            const enabled = enabledIds.includes(skill.id);
            const selected = selectedSkill.id === skill.id;
            return (
              <article
                key={skill.id}
                className={`skill-card ${selected ? 'selected' : ''} ${enabled ? '' : 'disabled'}`}
                onClick={() => setSelectedSkillId(skill.id)}
              >
                <div className="skill-card-head">
                  <div>
                    <div className="skill-card-title">{skill.name}</div>
                    <div className="skill-card-subtitle">{categoryMeta[skill.category].label} · {skill.latency}</div>
                  </div>
                  <Switch
                    size="small"
                    checked={enabled}
                    onClick={(checked, event) => {
                      event.stopPropagation();
                      toggleSkill(skill.id, checked);
                    }}
                  />
                </div>
                <Paragraph ellipsis={{ rows: 2 }} className="skill-card-desc">
                  {skill.description}
                </Paragraph>
                <Space size={6} wrap>
                  <Tag color={maturityMeta[skill.maturity].color}>{maturityMeta[skill.maturity].label}</Tag>
                  <Tag color={riskMeta[skill.risk].color}>{riskMeta[skill.risk].label}</Tag>
                  <Tag>{skill.agents.length} Agents</Tag>
                </Space>
              </article>
            );
          })}
        </section>

        <section className="skill-detail-panel">
          <div className="terminal-panel-header">
            <div className="terminal-panel-title">
              <FileSearchOutlined />
              <span>{selectedSkill.name}</span>
            </div>
            <Space>
              <Tag color={maturityMeta[selectedSkill.maturity].color}>{maturityMeta[selectedSkill.maturity].label}</Tag>
              <Tag color={riskMeta[selectedSkill.risk].color}>{riskMeta[selectedSkill.risk].label}</Tag>
            </Space>
          </div>

          <div className="skill-detail-body">
            <Paragraph style={{ fontSize: 15 }}>{selectedSkill.description}</Paragraph>

            <div className="skill-health-row">
              <div>
                <Text type="secondary">成功率</Text>
                <Progress percent={selectedSkill.successRate} size="small" />
              </div>
              <div>
                <Text type="secondary">调用次数</Text>
                <div className="skill-large-number">{selectedSkill.usage}</div>
              </div>
              <div>
                <Text type="secondary">延迟</Text>
                <div className="skill-large-number">{selectedSkill.latency}</div>
              </div>
            </div>

            <Descriptions size="small" bordered column={1} className="skill-descriptions">
              <Descriptions.Item label="适用 Agent">
                <Space wrap>{selectedSkill.agents.map(agent => <Tag key={agent} color="blue">{agent}</Tag>)}</Space>
              </Descriptions.Item>
              <Descriptions.Item label="依赖能力">
                <Space wrap>{selectedSkill.dependencies.map(item => <Tag key={item}>{item}</Tag>)}</Space>
              </Descriptions.Item>
              <Descriptions.Item label="权限边界">
                <Space wrap>{selectedSkill.permissions.map(item => <Tag key={item} color="gold">{item}</Tag>)}</Space>
              </Descriptions.Item>
            </Descriptions>

            <div className="skill-io-grid">
              <div className="skill-io-box">
                <div className="skill-box-title"><FilterOutlined /> 输入</div>
                {selectedSkill.inputs.map(item => <div className="skill-code-line" key={item}>{item}</div>)}
              </div>
              <div className="skill-io-box">
                <div className="skill-box-title"><AuditOutlined /> 输出</div>
                {selectedSkill.outputs.map(item => <div className="skill-code-line" key={item}>{item}</div>)}
              </div>
            </div>

            <div className="skill-invocation">
              <div>
                <Text type="secondary">调用模板</Text>
                <code>{selectedSkill.invocation}</code>
              </div>
              <Button onClick={handleCopyInvocation}>复制模板</Button>
            </div>
          </div>
        </section>
      </div>

      <section className="terminal-panel skill-template-panel">
        <div className="terminal-panel-header">
          <div className="terminal-panel-title">
            <BranchesOutlined />
            <span>技能编排模板</span>
          </div>
          <Text type="secondary">用于多 Agent 任务自动拆解</Text>
        </div>
        <div className="skill-template-grid">
          {orchestrationTemplates.map(template => (
            <article className="skill-template-card" key={template.name}>
              <div className="skill-card-title">{template.name}</div>
              <Paragraph className="skill-card-desc">{template.objective}</Paragraph>
              <Timeline
                items={template.skills.map((skill, index) => ({
                  color: index === template.skills.length - 1 ? 'green' : 'blue',
                  children: skill
                }))}
              />
            </article>
          ))}
        </div>
      </section>
    </div>
  );
};

export default SkillCenter;
