import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ApartmentOutlined,
  AuditOutlined,
  BankOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  DatabaseOutlined,
  FileSearchOutlined,
  FundProjectionScreenOutlined,
  LinkOutlined,
  NodeIndexOutlined,
  ReloadOutlined,
  SafetyCertificateOutlined,
  ThunderboltOutlined,
} from '@ant-design/icons';
import { Button, Select, Tag, Tooltip, message } from 'antd';
import CenterShell from './common/CenterShell';
import {
  fetchOntologyDemo,
  OntologyDemoAction,
  OntologyDemoSnapshot,
  OntologyEdge,
  OntologyEntityType,
  OntologyNode,
  recordOntologyDemoAction,
} from '../services/ontologyService';
import './InvestmentOntologyCenter.css';

const NODE_META: Record<OntologyEntityType, {
  label: string;
  className: string;
  icon: React.ReactNode;
}> = {
  Evidence: { label: '证据', className: 'evidence', icon: <DatabaseOutlined /> },
  Event: { label: '事件', className: 'event', icon: <ThunderboltOutlined /> },
  Thesis: { label: '投资论点', className: 'thesis', icon: <BulbOutlined /> },
  Security: { label: '证券', className: 'security', icon: <FundProjectionScreenOutlined /> },
  Issuer: { label: '公司', className: 'issuer', icon: <BankOutlined /> },
  Position: { label: '持仓', className: 'position', icon: <AuditOutlined /> },
  Portfolio: { label: '组合', className: 'portfolio', icon: <ApartmentOutlined /> },
};

const EDGE_LABELS: Record<string, string> = {
  EVIDENCES: '证明',
  SUPPORTS: '支持',
  WEAKENS: '削弱',
  CONTRADICTS: '反驳',
  ABOUT: '关于',
  REPRESENTS: '对应',
  GOVERNS: '约束',
  HOLDS: '持有',
  POSITION_IN: '属于',
};

const toneColor: Record<string, string> = {
  positive: 'green',
  warning: 'gold',
  neutral: 'blue',
};

function percentage(value: unknown): string {
  const numberValue = Number(value);
  return Number.isFinite(numberValue) ? `${Math.round(numberValue * 100)}%` : '—';
}

function numberText(value: unknown, suffix = ''): string {
  const numberValue = Number(value);
  if (!Number.isFinite(numberValue)) return '—';
  return `${numberValue.toLocaleString('zh-CN', { maximumFractionDigits: 1 })}${suffix}`;
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

function edgeClass(edge: OntologyEdge): string {
  if (edge.polarity > 0) return 'positive';
  if (edge.polarity < 0) return 'negative';
  return 'neutral';
}

const InvestmentOntologyCenter: React.FC = () => {
  const [snapshot, setSnapshot] = useState<OntologyDemoSnapshot | null>(null);
  const [selectedSecurityId, setSelectedSecurityId] = useState<string>();
  const [selectedNodeId, setSelectedNodeId] = useState<string>();
  const [loading, setLoading] = useState(true);
  const [actionLoading, setActionLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionPulseId, setActionPulseId] = useState<string>();
  const [apiMessage, contextHolder] = message.useMessage();

  const load = useCallback(async (securityId?: string, quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const result = await fetchOntologyDemo(securityId);
      setSnapshot(result);
      setSelectedSecurityId(result.selected_security_id);
      setSelectedNodeId(result.identity.security.id);
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : '本体快照加载失败');
    } finally {
      if (!quiet) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const nodeMap = useMemo(() => new Map(
    (snapshot?.graph.nodes || []).map(node => [node.id, node])
  ), [snapshot]);

  const selectedNode = selectedNodeId ? nodeMap.get(selectedNodeId) : undefined;

  const recordAction = useCallback(async (
    actionType: string,
    reason: string,
  ) => {
    if (!snapshot) return;
    setActionLoading(true);
    try {
      const created = await recordOntologyDemoAction({
        security_id: snapshot.selected_security_id,
        action_type: actionType,
        reason,
      });
      setActionPulseId(created.id);
      setSnapshot(prev => prev ? { ...prev, actions: [created, ...prev.actions] } : prev);
      apiMessage.success(`已记录：${created.action_label}（仅演示审计，不产生真实交易）`);
    } catch (actionError) {
      apiMessage.error(actionError instanceof Error ? actionError.message : '动作记录失败');
    } finally {
      setActionLoading(false);
    }
  }, [apiMessage, snapshot]);

  const renderEdge = (edge: OntologyEdge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!source || !target) return null;
    return (
      <line
        key={edge.id}
        className={`ontology-edge-line ${edgeClass(edge)}`}
        x1={source.position.x}
        y1={source.position.y}
        x2={target.position.x}
        y2={target.position.y}
        markerEnd={`url(#arrow-${edgeClass(edge)})`}
      />
    );
  };

  const renderEdgeLabel = (edge: OntologyEdge) => {
    const source = nodeMap.get(edge.source);
    const target = nodeMap.get(edge.target);
    if (!source || !target) return null;
    const x = (source.position.x + target.position.x) / 2;
    const y = (source.position.y + target.position.y) / 2;
    return (
      <Tooltip key={`label-${edge.id}`} title={`关系置信度 ${percentage(edge.confidence)}`}>
        <span
          className={`ontology-edge-label ${edgeClass(edge)}`}
          style={{ left: `${x}%`, top: `${y}%` }}
        >
          {EDGE_LABELS[edge.type] || edge.type}
        </span>
      </Tooltip>
    );
  };

  const actions = snapshot?.actions || [];
  const decision = snapshot?.decision;
  const positionAttrs = decision?.position.attributes || {};
  const thesisAttrs = decision?.thesis.attributes || {};
  const beginnerHeadline = decision?.tone === 'warning'
    ? '先控制风险，不急着加仓'
    : decision?.tone === 'positive'
      ? '信号正在变好，可以继续关注'
      : '暂时不需要调整';

  return (
    <CenterShell
      eyebrow="持仓决策助手"
      title="今天该怎么做？"
      subtitle="告诉你哪条信息变了、影响哪笔持仓、为什么要调整"
      icon={<NodeIndexOutlined />}
      className="ontology-center"
      error={error}
      loading={loading}
      loadingText="正在构建投资影响图…"
      actions={snapshot && (
        <>
          <Select
            className="ontology-asset-select"
            value={selectedSecurityId}
            options={snapshot.assets.map(asset => ({
              value: asset.security_id,
              label: `${asset.label} · ${asset.canonical_key}`,
            }))}
            onChange={value => void load(value)}
          />
          <Button icon={<ReloadOutlined />} onClick={() => void load(selectedSecurityId)}>
            刷新快照
          </Button>
        </>
      )}
    >
      {contextHolder}
      {snapshot && decision && (
        <div className="ontology-layout">
          <section className="ontology-decision-brief">
            <div className="ontology-brief-main">
              <div className="ontology-brief-status">
                <Tag color={toneColor[decision.tone]}>{decision.verdict}</Tag>
                <span>{snapshot.identity.security.label} · {snapshot.identity.security.canonical_key}</span>
              </div>
              <span className="ontology-brief-label">一句话结论</span>
              <h2>{beginnerHeadline}</h2>
              <p className="ontology-brief-action">{decision.recommended_action}</p>
              <p>{decision.recommended_reason}</p>
              <div className={`ontology-change-line ${decision.tone}`}>
                <span>为什么这样建议</span>
                <strong>{decision.change_summary}</strong>
              </div>
              <div className="ontology-action-row">
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={actionLoading}
                  onClick={() => void recordAction(
                    decision.recommended_action_type,
                    decision.recommended_reason,
                  )}
                >
                  记为待执行计划
                </Button>
                <Button
                  icon={<FileSearchOutlined />}
                  disabled={actionLoading}
                  onClick={() => void recordAction(
                    'request_research',
                    `补证任务：${decision.change_summary}`,
                  )}
                >
                  让系统继续找证据
                </Button>
              </div>
            </div>

            <aside className="ontology-brief-evidence" aria-label="决策依据">
              <div className="ontology-brief-evidence-head">
                <span>这次判断靠什么</span>
                <small>每项都可追溯</small>
              </div>
              <dl>
                <div>
                  <dt>投资逻辑可信度</dt>
                  <dd>{percentage(thesisAttrs.confidence)}</dd>
                </div>
                <div>
                  <dt>我现在持有</dt>
                  <dd>{numberText(positionAttrs.weight_pct, '%')}</dd>
                </div>
                <div className="risk">
                  <dt>建议仓位上限</dt>
                  <dd>{numberText(positionAttrs.risk_budget_pct, '%')}</dd>
                </div>
                <div>
                  <dt>有利 / 不利信号</dt>
                  <dd>
                    <b className="positive">{decision.supporting_paths}</b>
                    <em>/</em>
                    <b className="negative">{decision.contradicting_paths}</b>
                  </dd>
                </div>
              </dl>
              <div className="ontology-invalidation">
                <span>什么情况下原来的投资逻辑不成立？</span>
                <p>{String(thesisAttrs.invalidation || '未设置')}</p>
              </div>
              <div className="ontology-brief-guardrail">
                <SafetyCertificateOutlined />
                <span>仅写入决策审计，不连接券商</span>
              </div>
            </aside>
          </section>

          <section className="ontology-power-note">
            <span className="ontology-power-mark">本体在背后做的事</span>
            <strong>任何新消息进来，系统都会自动找到它影响的投资逻辑、持仓和风险规则。</strong>
            <p>你不需要自己翻新闻、研报和持仓表拼答案，也能知道结论从哪里来。</p>
          </section>

          <section className="ontology-simple-chain" aria-label="本次判断的四步推导">
            <header className="ontology-section-heading">
              <span>一眼看懂</span>
              <h3>这次判断是怎样得出的？</h3>
            </header>
            <div className="ontology-chain-grid">
              <article>
                <b>1</b>
                <span>新信息</span>
                <strong>{decision.change_summary}</strong>
              </article>
              <article>
                <b>2</b>
                <span>影响投资逻辑</span>
                <strong>{decision.thesis.label}</strong>
                <small>目前可信度 {percentage(thesisAttrs.confidence)}</small>
              </article>
              <article>
                <b>3</b>
                <span>检查我的持仓</span>
                <strong>
                  当前 {numberText(positionAttrs.weight_pct, '%')}，建议上限 {numberText(positionAttrs.risk_budget_pct, '%')}
                </strong>
                <small>系统发现仓位超过了预设边界</small>
              </article>
              <article>
                <b>4</b>
                <span>得到行动建议</span>
                <strong>{decision.recommended_action}</strong>
              </article>
            </div>
          </section>

          <details className="ontology-expert-details">
            <summary>
              <span>
                <strong>查看完整证据关系图</strong>
                <small>适合想核对每条证据和推导关系的用户</small>
              </span>
              <em>{snapshot.graph.nodes.length} 个对象 · {snapshot.graph.edges.length} 条关系</em>
            </summary>
            <section className="ontology-main-grid">
            <article className="ontology-panel ontology-graph-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">影响路径</span>
                  <h3>完整证据关系</h3>
                </div>
                <div className="ontology-legend">
                  <span><i className="positive" />支持</span>
                  <span><i className="negative" />反证</span>
                  <span><i className="neutral" />结构关系</span>
                </div>
              </header>

              <div className="ontology-graph">
                <svg className="ontology-edge-layer" viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden>
                  <defs>
                    <marker id="arrow-positive" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                      <path d="M0,0 L5,2.5 L0,5 z" className="ontology-arrow-positive" />
                    </marker>
                    <marker id="arrow-negative" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                      <path d="M0,0 L5,2.5 L0,5 z" className="ontology-arrow-negative" />
                    </marker>
                    <marker id="arrow-neutral" markerWidth="5" markerHeight="5" refX="4" refY="2.5" orient="auto">
                      <path d="M0,0 L5,2.5 L0,5 z" className="ontology-arrow-neutral" />
                    </marker>
                  </defs>
                  {snapshot.graph.edges.map(renderEdge)}
                </svg>
                {snapshot.graph.edges.map(renderEdgeLabel)}
                {snapshot.graph.nodes.map(node => {
                  const meta = NODE_META[node.type];
                  const isSelected = node.id === selectedNodeId;
                  return (
                    <button
                      key={node.id}
                      type="button"
                      className={`ontology-node ${meta.className}${isSelected ? ' selected' : ''}`}
                      style={{ left: `${node.position.x}%`, top: `${node.position.y}%` }}
                      onClick={() => setSelectedNodeId(node.id)}
                    >
                      <span className="ontology-node-icon">{meta.icon}</span>
                      <span className="ontology-node-copy">
                        <small>{meta.label}</small>
                        <strong>{node.label}</strong>
                      </span>
                    </button>
                  );
                })}
              </div>

              <div className="ontology-node-inspector">
                {selectedNode ? (
                  <>
                    <span className={`ontology-node-inspector-icon ${NODE_META[selectedNode.type].className}`}>
                      {NODE_META[selectedNode.type].icon}
                    </span>
                    <div>
                      <small>{NODE_META[selectedNode.type].label} · {selectedNode.canonical_key}</small>
                      <strong>{selectedNode.label}</strong>
                    </div>
                    <div className="ontology-node-properties">
                      {Object.entries(selectedNode.attributes).slice(0, 4).map(([key, value]) => (
                        <span key={key}><b>{key}</b>{String(value)}</span>
                      ))}
                    </div>
                  </>
                ) : (
                  <span>点击图中对象查看属性</span>
                )}
              </div>
            </article>

            </section>
          </details>

          <section className="ontology-bottom-grid">
            <article className="ontology-panel ontology-identity-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">系统为什么不会认错股票？</span>
                  <h3>不同代码，自动认成同一家公司</h3>
                </div>
                <LinkOutlined />
              </header>
              <p className="ontology-identity-explainer">
                财报里的 600519、行情里的 SH600519 和“贵州茅台”，都会自动合并到同一个对象。
              </p>
              <div className="ontology-identity-target">
                <span>{snapshot.identity.security.label}</span>
                <strong>{snapshot.identity.security.id}</strong>
              </div>
              <div className="ontology-alias-grid">
                {snapshot.identity.aliases.map(alias => (
                  <div key={`${alias.scheme}:${alias.alias}`}>
                    <span>{alias.scheme}</span>
                    <strong>{alias.alias}</strong>
                    <small>{alias.market}</small>
                  </div>
                ))}
              </div>
            </article>

            <article className="ontology-panel ontology-audit-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">以后可以回来复盘</span>
                  <h3>我的决策记录</h3>
                </div>
                <AuditOutlined />
              </header>
              {actions.length ? (
                <div className="ontology-audit-list">
                  {actions.map((action: OntologyDemoAction) => (
                    <div
                      key={action.id}
                      className={`ontology-audit-item${action.id === actionPulseId ? ' pulse' : ''}`}
                    >
                      <span className="ontology-audit-dot" />
                      <div>
                        <strong>{action.action_label}</strong>
                        <p>{action.reason}</p>
                        <small>{action.actor} · {formatTimestamp(action.created_at)} · PAPER ONLY</small>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <div className="ontology-audit-empty">
                  <AuditOutlined />
                  <strong>还没有决策记录</strong>
                  <span>记录一次决策后，时间、理由和执行人会留在这里。</span>
                </div>
              )}
            </article>
          </section>
        </div>
      )}
    </CenterShell>
  );
};

export default InvestmentOntologyCenter;
