import React, { useMemo, useState } from 'react';
import {
  Button,
  Col,
  Input,
  Modal,
  Progress,
  Row,
  Select,
  Space,
  Tag,
  Typography,
  message
} from 'antd';
import {
  BellOutlined,
  BranchesOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  ExperimentOutlined,
  FileProtectOutlined,
  PlusOutlined,
  SafetyCertificateOutlined,
  TeamOutlined,
  ThunderboltOutlined,
  TrophyOutlined,
  WarningOutlined
} from '@ant-design/icons';
import { Comment, Post, Stock, ViewType } from '../types';

const { Paragraph, Text } = Typography;
const { TextArea } = Input;

type ThesisStatus = 'tracking' | 'validated' | 'challenged';
type ThesisStance = 'bull' | 'bear' | 'neutral';

interface ThesisCard {
  id: string;
  title: string;
  stance: ThesisStance;
  status: ThesisStatus;
  confidence: number;
  evidenceCount: number;
  validationMetric: string;
  invalidationRule: string;
  nextReview: string;
  sources: string[];
}

interface NetworkProfile {
  attentionLift: number;
  institutionalWatchers: number;
  evidenceCoverage: Array<{ label: string; value: number }>;
  knowledgeNodes: Array<{ label: string; group: string; strength: number }>;
  signals: Array<{ title: string; detail: string; tone: 'positive' | 'warning' | 'neutral' }>;
  authors: Array<{ name: string; specialty: string; score: number; adoption: number }>;
  theses: ThesisCard[];
}

const statusMeta: Record<ThesisStatus, { label: string; color: string; icon: React.ReactNode }> = {
  tracking: { label: '跟踪中', color: 'blue', icon: <BellOutlined /> },
  validated: { label: '已验证', color: 'green', icon: <CheckCircleOutlined /> },
  challenged: { label: '待反证', color: 'orange', icon: <WarningOutlined /> }
};

const stanceMeta: Record<ThesisStance, { label: string; color: string }> = {
  bull: { label: '多头假设', color: 'green' },
  bear: { label: '空头假设', color: 'red' },
  neutral: { label: '中性假设', color: 'blue' }
};

const defaultProfiles: Record<string, NetworkProfile> = {
  TSLA: {
    attentionLift: 28,
    institutionalWatchers: 46,
    evidenceCoverage: [
      { label: '财报/电话会', value: 82 },
      { label: '产品与交付', value: 76 },
      { label: '监管风险', value: 58 },
      { label: '供应链', value: 64 }
    ],
    knowledgeNodes: [
      { label: 'FSD', group: '产品', strength: 88 },
      { label: 'Robotaxi', group: '催化', strength: 73 },
      { label: '中国需求', group: '市场', strength: 79 },
      { label: '电池成本', group: '指标', strength: 62 },
      { label: '监管审批', group: '风险', strength: 67 },
      { label: '比亚迪', group: '竞品', strength: 71 }
    ],
    signals: [
      {
        title: '高声誉用户关注上升',
        detail: '近 72 小时有 9 位高分用户把 FSD 相关资料加入观察。',
        tone: 'positive'
      },
      {
        title: '核心争议集中',
        detail: '估值扩张能否由交付和软件收入共同验证。',
        tone: 'neutral'
      },
      {
        title: '反证条件清晰',
        detail: '若毛利率与交付增速同时走弱，乐观假设需降权。',
        tone: 'warning'
      }
    ],
    authors: [
      { name: 'AutoAlpha', specialty: '智能驾驶', score: 94, adoption: 41 },
      { name: 'MarginLab', specialty: '汽车毛利率', score: 89, adoption: 34 },
      { name: 'SupplyTrace', specialty: '供应链', score: 86, adoption: 29 }
    ],
    theses: [
      {
        id: 'tsla-fsd',
        title: 'FSD 商业化会重估软件收入弹性',
        stance: 'bull',
        status: 'tracking',
        confidence: 68,
        evidenceCount: 12,
        validationMetric: '订阅转化率、监管批准、单位经济模型',
        invalidationRule: '连续两个季度软件收入未改善',
        nextReview: '财报后 24 小时',
        sources: ['技术专利', '电话会', '路测数据']
      },
      {
        id: 'tsla-margin',
        title: '降价压力可能继续压制整车毛利率',
        stance: 'bear',
        status: 'challenged',
        confidence: 61,
        evidenceCount: 8,
        validationMetric: 'ASP、汽车毛利率、库存天数',
        invalidationRule: '毛利率回升且库存同步下降',
        nextReview: '月度交付数据后',
        sources: ['财报', '交付数据', '竞品价格']
      }
    ]
  },
  NVDA: {
    attentionLift: 34,
    institutionalWatchers: 63,
    evidenceCoverage: [
      { label: '数据中心收入', value: 88 },
      { label: '供应链/产能', value: 74 },
      { label: '同业估值', value: 69 },
      { label: '出口限制', value: 55 }
    ],
    knowledgeNodes: [
      { label: 'H200/B200', group: '产品', strength: 91 },
      { label: '云厂商 Capex', group: '需求', strength: 86 },
      { label: 'HBM 供应', group: '供应链', strength: 78 },
      { label: 'AMD', group: '竞品', strength: 64 },
      { label: '毛利率', group: '指标', strength: 82 },
      { label: '出口管制', group: '风险', strength: 59 }
    ],
    signals: [
      {
        title: '产业链资料密度提升',
        detail: 'HBM、先进封装和云厂商资本开支被反复引用。',
        tone: 'positive'
      },
      {
        title: '估值分歧扩大',
        detail: '多头关注订单可见度，空头关注周期峰值风险。',
        tone: 'neutral'
      },
      {
        title: '政策变量待监控',
        detail: '出口限制相关资料覆盖不足，需要补充官方来源。',
        tone: 'warning'
      }
    ],
    authors: [
      { name: 'ChipCycle', specialty: '半导体周期', score: 96, adoption: 52 },
      { name: 'CloudSpend', specialty: '云资本开支', score: 91, adoption: 43 },
      { name: 'HBMNotes', specialty: '存储供应链', score: 87, adoption: 31 }
    ],
    theses: [
      {
        id: 'nvda-capex',
        title: '云厂商 AI Capex 支撑数据中心收入韧性',
        stance: 'bull',
        status: 'validated',
        confidence: 74,
        evidenceCount: 15,
        validationMetric: '云厂商资本开支、订单能见度、交付周期',
        invalidationRule: '主要客户削减 AI 资本开支',
        nextReview: '大型云厂商财报周',
        sources: ['客户财报', '产业链纪要', '订单跟踪']
      },
      {
        id: 'nvda-gm',
        title: '高毛利率需要先进封装和 HBM 供给配合',
        stance: 'neutral',
        status: 'tracking',
        confidence: 66,
        evidenceCount: 9,
        validationMetric: '毛利率、交付周期、供应商扩产',
        invalidationRule: '供给瓶颈导致交付和毛利同步恶化',
        nextReview: '供应商月度更新',
        sources: ['供应链', '财报', '电话会']
      }
    ]
  }
};

const fallbackProfile: NetworkProfile = {
  attentionLift: 16,
  institutionalWatchers: 24,
  evidenceCoverage: [
    { label: '财报/公告', value: 62 },
    { label: '同业对比', value: 54 },
    { label: '行业数据', value: 49 },
    { label: '风险事件', value: 45 }
  ],
  knowledgeNodes: [
    { label: '核心业务', group: '公司', strength: 67 },
    { label: '同业估值', group: '估值', strength: 58 },
    { label: '财务指标', group: '指标', strength: 64 },
    { label: '催化剂', group: '事件', strength: 51 },
    { label: '风险清单', group: '风险', strength: 55 }
  ],
  signals: [
    {
      title: '资料覆盖仍需补齐',
      detail: '缺少最近电话会、同业估值和核心指标更新。',
      tone: 'warning'
    },
    {
      title: '观点分歧可沉淀',
      detail: '建议把评论区观点转成可验证假设卡。',
      tone: 'neutral'
    }
  ],
  authors: [
    { name: 'DeepFocus Pro', specialty: '基本面', score: 86, adoption: 22 },
    { name: 'EventDesk', specialty: '事件跟踪', score: 82, adoption: 18 }
  ],
  theses: [
    {
      id: 'generic-quality',
      title: '基本面改善需要被经营指标连续验证',
      stance: 'neutral',
      status: 'tracking',
      confidence: 56,
      evidenceCount: 5,
      validationMetric: '收入增速、利润率、现金流',
      invalidationRule: '核心指标连续两期不达预期',
      nextReview: '下一份公告后',
      sources: ['财报', '公告', '同业数据']
    }
  ]
};

interface ResearchFlywheelProps {
  stock: Stock;
  posts: Post[];
  comments: Comment[];
  onViewChange?: (view: ViewType) => void;
}

const ResearchFlywheel: React.FC<ResearchFlywheelProps> = ({
  stock,
  posts,
  comments,
  onViewChange
}) => {
  const [userTheses, setUserTheses] = useState<ThesisCard[]>([]);
  const [modalOpen, setModalOpen] = useState(false);
  const [draft, setDraft] = useState({
    title: '',
    stance: 'bull' as ThesisStance,
    validationMetric: '',
    invalidationRule: ''
  });

  const profile = defaultProfiles[stock.symbol] || fallbackProfile;
  const allTheses = [...userTheses, ...profile.theses];

  const metrics = useMemo(() => {
    const averageQuality = posts.length
      ? posts.reduce((sum, post) => sum + post.qualityScore, 0) / posts.length
      : 0;
    const paidDemand = posts.reduce((sum, post) => sum + post.paidViewers, 0);
    const contributorCount = new Set(posts.map(post => post.author.id)).size;
    const commentCount = comments.length;
    const evidenceCoverage = Math.round(
      profile.evidenceCoverage.reduce((sum, item) => sum + item.value, 0) / profile.evidenceCoverage.length
    );
    const networkScore = Math.min(
      100,
      Math.round(
        evidenceCoverage * 0.36 +
        stock.communityScore * 0.24 +
        averageQuality * 0.22 +
        Math.min(18, posts.length * 3 + commentCount) +
        Math.min(10, paidDemand / 18)
      )
    );

    return {
      networkScore,
      evidenceCoverage,
      contributorCount: Math.max(contributorCount, profile.authors.length),
      commentCount
    };
  }, [comments.length, posts, profile.authors.length, profile.evidenceCoverage, stock.communityScore]);

  const handleCreateThesis = () => {
    if (!draft.title.trim() || !draft.validationMetric.trim() || !draft.invalidationRule.trim()) {
      message.warning('请补齐假设、验证指标和失效条件');
      return;
    }

    const thesis: ThesisCard = {
      id: `user-thesis-${Date.now()}`,
      title: draft.title.trim(),
      stance: draft.stance,
      status: 'tracking',
      confidence: 50,
      evidenceCount: 0,
      validationMetric: draft.validationMetric.trim(),
      invalidationRule: draft.invalidationRule.trim(),
      nextReview: '下一次资料更新后',
      sources: ['用户假设']
    };

    setUserTheses(prev => [thesis, ...prev]);
    setDraft({ title: '', stance: 'bull', validationMetric: '', invalidationRule: '' });
    setModalOpen(false);
    message.success('假设卡已加入当前标的飞轮');
  };

  const flywheelSteps = [
    { icon: <DatabaseOutlined />, label: '资料贡献', value: `${posts.length} 篇内容` },
    { icon: <FileProtectOutlined />, label: '证据结构化', value: `${metrics.evidenceCoverage}% 覆盖` },
    { icon: <ExperimentOutlined />, label: 'AI报告增强', value: `${allTheses.length} 条假设` },
    { icon: <SafetyCertificateOutlined />, label: '复盘校验', value: `${metrics.commentCount} 条讨论` },
    { icon: <TrophyOutlined />, label: '声誉沉淀', value: `${metrics.contributorCount} 位贡献者` }
  ];

  return (
    <div className="research-flywheel">
      <section className="research-network-hero">
        <div>
          <div className="dashboard-eyebrow">RESEARCH NETWORK</div>
          <h3 className="research-network-title">{stock.name} 投研飞轮</h3>
          <Text type="secondary">
            资料、假设、作者声誉和关注行为在这里汇总，沉淀为可复用的标的资产。
          </Text>
        </div>
        <Space wrap>
          <Button icon={<DatabaseOutlined />} onClick={() => onViewChange?.('data-sources')}>
            贡献资料
          </Button>
          <Button icon={<ThunderboltOutlined />} onClick={() => onViewChange?.('agent-center')}>
            多 Agent 复核
          </Button>
          <Button type="primary" icon={<PlusOutlined />} onClick={() => setModalOpen(true)}>
            新建假设
          </Button>
        </Space>
      </section>

      <div className="research-network-metrics">
        <div className="research-network-metric">
          <span>网络指数</span>
          <strong>{metrics.networkScore}</strong>
          <Progress percent={metrics.networkScore} showInfo={false} />
        </div>
        <div className="research-network-metric">
          <span>证据覆盖</span>
          <strong>{metrics.evidenceCoverage}%</strong>
          <Text type="secondary">财报、行业、风险、供应链</Text>
        </div>
        <div className="research-network-metric">
          <span>活跃假设</span>
          <strong>{allTheses.length}</strong>
          <Text type="secondary">{userTheses.length} 条来自当前用户</Text>
        </div>
        <div className="research-network-metric">
          <span>专业关注</span>
          <strong>+{profile.attentionLift}%</strong>
          <Text type="secondary">{profile.institutionalWatchers} 位高声誉观察者</Text>
        </div>
      </div>

      <div className="flywheel-steps">
        {flywheelSteps.map((step, index) => (
          <div className="flywheel-step" key={step.label}>
            <div className="flywheel-step-index">{index + 1}</div>
            <div className="flywheel-step-icon">{step.icon}</div>
            <div>
              <strong>{step.label}</strong>
              <span>{step.value}</span>
            </div>
          </div>
        ))}
      </div>

      <Row gutter={[14, 14]}>
        <Col xs={24} xl={14}>
          <section className="research-panel">
            <div className="research-panel-head">
              <div>
                <h4>投资假设卡</h4>
                <Text type="secondary">观点必须绑定验证指标、失效条件和复盘时间。</Text>
              </div>
              <Tag color="blue">{allTheses.length} cards</Tag>
            </div>

            <Space direction="vertical" size={10} style={{ width: '100%' }}>
              {allTheses.map(thesis => {
                const status = statusMeta[thesis.status];
                const stance = stanceMeta[thesis.stance];
                return (
                  <article className="thesis-card" key={thesis.id}>
                    <div className="thesis-card-head">
                      <Space wrap>
                        <Tag color={stance.color}>{stance.label}</Tag>
                        <Tag color={status.color} icon={status.icon}>{status.label}</Tag>
                      </Space>
                      <Text type="secondary">{thesis.nextReview}</Text>
                    </div>
                    <h5>{thesis.title}</h5>
                    <div className="thesis-confidence">
                      <span>置信度 {thesis.confidence}%</span>
                      <Progress percent={thesis.confidence} showInfo={false} />
                    </div>
                    <div className="thesis-grid">
                      <div>
                        <span>验证指标</span>
                        <strong>{thesis.validationMetric}</strong>
                      </div>
                      <div>
                        <span>失效条件</span>
                        <strong>{thesis.invalidationRule}</strong>
                      </div>
                    </div>
                    <div className="thesis-sources">
                      <Text type="secondary">证据 {thesis.evidenceCount} 条</Text>
                      {thesis.sources.map(source => <Tag key={source}>{source}</Tag>)}
                    </div>
                  </article>
                );
              })}
            </Space>
          </section>
        </Col>

        <Col xs={24} xl={10}>
          <Space direction="vertical" size={14} style={{ width: '100%' }}>
            <section className="research-panel">
              <div className="research-panel-head">
                <div>
                  <h4>标的知识图谱</h4>
                  <Text type="secondary">用户资料会继续补全节点强度。</Text>
                </div>
                <BranchesOutlined />
              </div>
              <div className="knowledge-node-grid">
                {profile.knowledgeNodes.map(node => (
                  <div className="knowledge-node" key={`${node.group}-${node.label}`}>
                    <div>
                      <strong>{node.label}</strong>
                      <span>{node.group}</span>
                    </div>
                    <Progress percent={node.strength} size="small" showInfo={false} />
                  </div>
                ))}
              </div>
            </section>

            <section className="research-panel">
              <div className="research-panel-head">
                <div>
                  <h4>作者声誉榜</h4>
                  <Text type="secondary">按证据质量、复盘和采纳度排序。</Text>
                </div>
                <TeamOutlined />
              </div>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {profile.authors.map((author, index) => (
                  <div className="author-rank-row" key={author.name}>
                    <div className="author-rank-index">{index + 1}</div>
                    <div>
                      <strong>{author.name}</strong>
                      <span>{author.specialty}</span>
                    </div>
                    <div className="author-rank-score">
                      <strong>{author.score}</strong>
                      <span>{author.adoption} 次采纳</span>
                    </div>
                  </div>
                ))}
              </Space>
            </section>

            <section className="research-panel">
              <div className="research-panel-head">
                <div>
                  <h4>网络信号</h4>
                  <Text type="secondary">由关注、资料和讨论共同生成。</Text>
                </div>
                <ThunderboltOutlined />
              </div>
              <Space direction="vertical" size={8} style={{ width: '100%' }}>
                {profile.signals.map(signal => (
                  <div className={`network-signal ${signal.tone}`} key={signal.title}>
                    <strong>{signal.title}</strong>
                    <span>{signal.detail}</span>
                  </div>
                ))}
              </Space>
            </section>
          </Space>
        </Col>
      </Row>

      <Modal
        title="新建投资假设"
        open={modalOpen}
        onCancel={() => setModalOpen(false)}
        onOk={handleCreateThesis}
        okText="加入飞轮"
        cancelText="取消"
      >
        <Space direction="vertical" size={12} style={{ width: '100%' }}>
          <Select
            value={draft.stance}
            onChange={value => setDraft(prev => ({ ...prev, stance: value }))}
            options={[
              { value: 'bull', label: '多头假设' },
              { value: 'bear', label: '空头假设' },
              { value: 'neutral', label: '中性假设' }
            ]}
          />
          <TextArea
            rows={2}
            value={draft.title}
            onChange={event => setDraft(prev => ({ ...prev, title: event.target.value }))}
            placeholder="假设，例如：软件收入会带来估值重估"
          />
          <Input
            value={draft.validationMetric}
            onChange={event => setDraft(prev => ({ ...prev, validationMetric: event.target.value }))}
            placeholder="验证指标，例如：订阅转化率、毛利率、订单"
          />
          <Input
            value={draft.invalidationRule}
            onChange={event => setDraft(prev => ({ ...prev, invalidationRule: event.target.value }))}
            placeholder="失效条件，例如：连续两期核心指标恶化"
          />
          <Paragraph type="secondary" style={{ marginBottom: 0 }}>
            后续可接入数据源中心，让每条假设自动绑定资料、预警和复盘结果。
          </Paragraph>
        </Space>
      </Modal>
    </div>
  );
};

export default ResearchFlywheel;
