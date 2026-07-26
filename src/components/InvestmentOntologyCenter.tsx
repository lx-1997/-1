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

  return (
    <CenterShell
      eyebrow="DAO ONTOLOGY · DECISION OS"
      title="投资本体决策驾驶舱"
      subtitle="把公司、证券、事件、证据、论点、持仓与动作放在同一条可审计链路中"
      icon={<NodeIndexOutlined />}
      className="ontology-center"
      error={error}
      loading={loading}
      loadingText="正在构建投资影响图…"
      actions={snapshot && (
        <>
          <Tag color="purple">INTERACTIVE MVP</Tag>
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
          <section className="ontology-kpi-grid">
            <article className="ontology-kpi">
              <span>CANONICAL SECURITY</span>
              <strong>{snapshot.identity.security.canonical_key}</strong>
              <small>{snapshot.identity.issuer.label}</small>
            </article>
            <article className="ontology-kpi">
              <span>论点置信度</span>
              <strong>{percentage(thesisAttrs.confidence)}</strong>
              <small>{decision.thesis.label}</small>
            </article>
            <article className="ontology-kpi">
              <span>组合仓位 / 风险预算</span>
              <strong>
                {numberText(positionAttrs.weight_pct, '%')}
                <em> / {numberText(positionAttrs.risk_budget_pct, '%')}</em>
              </strong>
              <small>浮动盈亏 {numberText(positionAttrs.pnl_pct, '%')}</small>
            </article>
            <article className="ontology-kpi">
              <span>证据路径</span>
              <strong>
                <b className="positive">+{decision.supporting_paths}</b>
                <em> / </em>
                <b className="negative">−{decision.contradicting_paths}</b>
              </strong>
              <small>支持 / 反证路径实时汇总</small>
            </article>
          </section>

          <section className="ontology-main-grid">
            <article className="ontology-panel ontology-graph-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">IMPACT GRAPH</span>
                  <h3>这条信息为什么会影响我的持仓？</h3>
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

            <article className="ontology-panel ontology-decision-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">DECISION OBJECT</span>
                  <h3>需要我做什么？</h3>
                </div>
                <Tag color={toneColor[decision.tone]}>{decision.verdict}</Tag>
              </header>

              <div className={`ontology-change-callout ${decision.tone}`}>
                <span>自上次决策以来</span>
                <strong>{decision.change_summary}</strong>
              </div>

              <div className="ontology-thesis-card">
                <div className="ontology-thesis-head">
                  <BulbOutlined />
                  <span>当前投资论点</span>
                  <Tag>{percentage(thesisAttrs.confidence)}</Tag>
                </div>
                <strong>{decision.thesis.label}</strong>
                <p><b>失效条件：</b>{String(thesisAttrs.invalidation || '未设置')}</p>
              </div>

              <div className="ontology-recommendation">
                <small>ONTOLOGY FUNCTION · 建议动作</small>
                <strong>{decision.recommended_action}</strong>
                <p>{decision.recommended_reason}</p>
              </div>

              <div className="ontology-action-stack">
                <Button
                  type="primary"
                  icon={<CheckCircleOutlined />}
                  loading={actionLoading}
                  onClick={() => void recordAction(
                    decision.recommended_action_type,
                    decision.recommended_reason,
                  )}
                >
                  接受建议并写入审计
                </Button>
                <Button
                  icon={<FileSearchOutlined />}
                  disabled={actionLoading}
                  onClick={() => void recordAction(
                    'request_research',
                    `补证任务：${decision.change_summary}`,
                  )}
                >
                  发起补证任务
                </Button>
              </div>

              <div className="ontology-guardrails">
                <SafetyCertificateOutlined />
                <div>
                  {snapshot.guardrails.map(item => <span key={item}>{item}</span>)}
                </div>
              </div>
            </article>
          </section>

          <section className="ontology-bottom-grid">
            <article className="ontology-panel ontology-identity-panel">
              <header className="ontology-panel-header">
                <div>
                  <span className="ontology-panel-kicker">IDENTITY RESOLUTION</span>
                  <h3>一个对象，多套代码</h3>
                </div>
                <LinkOutlined />
              </header>
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
                  <span className="ontology-panel-kicker">ACTION AUDIT</span>
                  <h3>动作审计时间线</h3>
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
                  <strong>还没有动作记录</strong>
                  <span>接受一次建议，完整审计记录会出现在这里。</span>
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

