import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Alert,
  Badge,
  Button,
  Card,
  Col,
  Collapse,
  Divider,
  Progress,
  Row,
  Space,
  Spin,
  Statistic,
  Steps,
  Tag,
  Timeline,
  Tooltip,
  Typography,
} from 'antd';
import {
  AimOutlined,
  ArrowDownOutlined,
  ArrowUpOutlined,
  BarChartOutlined,
  BulbOutlined,
  CheckCircleOutlined,
  ClockCircleOutlined,
  CloseCircleOutlined,
  DashboardOutlined,
  DatabaseOutlined,
  ExclamationCircleOutlined,
  FundOutlined,
  GlobalOutlined,
  LineChartOutlined,
  LoadingOutlined,
  MinusOutlined,
  PieChartOutlined,
  RiseOutlined,
  SafetyCertificateOutlined,
  SearchOutlined,
  SettingOutlined,
  StockOutlined,
  SyncOutlined,
  ThunderboltOutlined,
  TrophyOutlined,
  WarningOutlined,
} from '@ant-design/icons';
import {
  subscribeResearchLoop,
  LoopResearchEvent,
  RiskMatrix,
  ResearchThesis,
} from '../services/agentService';
import Markdown from './common/Markdown';
import CitableSources from './common/CitableSources';

const { Text, Paragraph, Title } = Typography;

/** 深研结论里的 [n] 内联引用 → 点击打开对应证据来源。 */
function openCitation(sources: LoopResearchEvent['citable_sources'], n: number): void {
  const src = sources?.find(s => s.n === n);
  if (src?.url) {
    window.open(src.url, '_blank', 'noopener');
  }
}

const MODULE_LABELS: Record<string, string> = {
  quotes: '实时行情', macro: '宏观环境', risk: '持仓风险',
  evidence: '数据源证据', metrics: '财报指标',
};


const MODULE_ICONS: Record<string, React.ReactNode> = {
  quotes: <LineChartOutlined />,
  macro: <GlobalOutlined />,
  risk: <SafetyCertificateOutlined />,
  evidence: <SearchOutlined />,
  metrics: <BarChartOutlined />,
};

const REC_COLORS: Record<string, { bg: string; text: string; label: string }> = {
  strong_buy: { bg: 'rgba(0,100,0,0.3)', text: 'var(--positive)', label: '强烈看好' },
  buy: { bg: 'rgba(0,100,0,0.15)', text: 'var(--positive)', label: '看好' },
  hold: { bg: 'rgba(180,83,9,0.15)', text: 'var(--warning)', label: '中性' },
  reduce: { bg: 'rgba(180,0,0,0.15)', text: 'var(--negative)', label: '偏谨慎' },
  sell: { bg: 'rgba(180,0,0,0.3)', text: 'var(--negative)', label: '看淡' },
  strong_sell: { bg: 'rgba(180,0,0,0.3)', text: 'var(--negative)', label: '强烈看淡' },
};

const SIGNAL_STYLES: Record<string, { color: string; icon: React.ReactNode }> = {
  bullish: { color: 'var(--positive)', icon: <ArrowUpOutlined /> },
  neutral: { color: 'var(--text-muted)', icon: <MinusOutlined /> },
  bearish: { color: 'var(--negative)', icon: <ArrowDownOutlined /> },
  tailwind: { color: 'var(--positive)', icon: <RiseOutlined /> },
  headwind: { color: 'var(--negative)', icon: <ArrowDownOutlined /> },
  undervalued: { color: 'var(--positive)', icon: <RiseOutlined /> },
  fair: { color: 'var(--text-muted)', icon: <MinusOutlined /> },
  overvalued: { color: 'var(--negative)', icon: <ArrowDownOutlined /> },
};

function recBadge(rec: string): { color: string; bg: string; text: string; label: string } {
  const entry = REC_COLORS[rec] || REC_COLORS.hold;
  return { ...entry, color: entry.text };
}

function signalTag(sig: string | undefined): React.ReactNode | null {
  if (!sig || !SIGNAL_STYLES[sig]) return null;
  const s = SIGNAL_STYLES[sig];
  return <span style={{ color: s.color, fontSize: 12 }}>{s.icon} {sig}</span>;
}

function confidenceColor(v: number): string {
  if (v >= 0.7) return 'var(--positive)';
  if (v >= 0.4) return 'var(--warning)';
  return 'var(--negative)';
}

function riskScoreColor(v: number): string {
  if (v <= 3) return 'var(--positive)';
  if (v <= 6) return 'var(--warning)';
  return 'var(--negative)';
}

interface ResearchState {
  symbol: string;
  question: string;
  framework: string;
  scopingRationale: string;
  keyQuestions: string[];
  acquiredModules: string[];
  analyses: LoopResearchEvent[];
  synthesis: LoopResearchEvent | null;
  traceEvents: LoopResearchEvent[];
  finished: boolean;
  error: string | null;
}

interface AgentLoopStreamProps {
  symbol: string;
  question: string;
  onComplete?: (final: LoopResearchEvent) => void;
  onCancel?: () => void;
}

const AgentLoopStream: React.FC<AgentLoopStreamProps> = ({
  symbol, question, onComplete, onCancel,
}) => {
  const [state, setState] = useState<ResearchState>({
    symbol, question,
    framework: '', scopingRationale: '', keyQuestions: [],
    acquiredModules: [], analyses: [], synthesis: null,
    traceEvents: [], finished: false, error: null,
  });
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    setState({
      symbol, question,
      framework: '', scopingRationale: '', keyQuestions: [],
      acquiredModules: [], analyses: [], synthesis: null,
      traceEvents: [], finished: false, error: null,
    });

    const unsub = subscribeResearchLoop(symbol, question, {
      onEvent: (evt) => {
        setState(prev => {
          const next = { ...prev, traceEvents: [...prev.traceEvents, evt] };
          if (evt.research_framework) {
            next.framework = evt.research_framework;
            next.scopingRationale = evt.scoping_rationale || '';
            next.keyQuestions = evt.key_questions || [];
          }
          if (evt.module && evt.status === 'done') {
            const mods = new Set(prev.acquiredModules);
            mods.add(evt.module);
            next.acquiredModules = Array.from(mods);
          }
          if (evt.phase === 'analyze' && evt.status === 'done') {
            next.analyses = [...prev.analyses, evt];
          }
          if (evt.phase === 'synthesize' && evt.status === 'done') {
            next.synthesis = evt;
          }
          return next;
        });
      },
      onDone: (final) => {
        setState(prev => ({ ...prev, finished: true, synthesis: prev.synthesis || final }));
      },
      onError: (err) => setState(prev => ({ ...prev, error: err, finished: true })),
    });
    return () => unsub();
  }, [symbol, question]);

  useEffect(() => {
    if (state.finished && state.synthesis) {
      onComplete?.(state.synthesis);
    }
  }, [state.finished, state.synthesis, onComplete]);

  const synth = state.synthesis;
  const rec = recBadge(synth?.recommendation || '');
  const confVal = synth?.confidence || 0;
  const elapsed = synth?.elapsed_seconds || 0;
  const glancableProgress = state.acquiredModules.length / 5;

  const renderTraceHandle = useCallback(() => {
    const steps = state.traceEvents.filter(e =>
      e.phase === 'scope' || e.phase === 'acquire' || e.phase === 'analyze' || e.phase === 'synthesize'
    );
    if (steps.length === 0) return null;

    return (
      <Collapse
        ghost size="small"
        defaultActiveKey={['trace']}
        style={{ marginTop: 12, background: 'var(--surface-muted)', borderRadius: 8 }}
        items={[{
          key: 'trace',
          label: (
            <Text type="secondary" style={{ fontSize: 11 }}>
              <SettingOutlined /> 执行轨迹（{steps.length} 步）
            </Text>
          ),
          children: (
            <Timeline
              style={{ paddingLeft: 4 }}
              items={steps.map((s, i) => {
                const done = s.status === 'done';
                const err = s.status === 'error';
                const work = s.status === 'working';
                return {
                  color: err ? 'red' : done ? 'green' : 'blue',
                  dot: err ? <CloseCircleOutlined /> : done ? <CheckCircleOutlined /> : <LoadingOutlined />,
                  children: (
                    <div>
                      <Tag color={err ? 'red' : done ? 'green' : 'processing'} style={{ fontSize: 10 }}>
                        {s.phase_label || s.phase}
                      </Tag>
                      {' '}
                      <Text style={{ fontSize: 11, color: err ? 'var(--negative)' : 'var(--text-muted)' }}>
                        {s.module_name ? MODULE_LABELS[s.module_name || ''] || s.module_name : s.detail?.slice(0, 50)}
                      </Text>
                    </div>
                  ),
                };
              })}
            />
          ),
        }]}
      />
    );
  }, [state.traceEvents]);

  return (
    <div ref={scrollRef}>
      {state.error && (
        <Alert type="error" message={state.error} showIcon style={{ marginBottom: 12 }} closable />
      )}

      {!synth && !state.error && (
        <Card size="small" style={{ marginBottom: 12 }}>
          <div style={{ textAlign: 'center', padding: '16px 0' }}>
            <SyncOutlined spin style={{ fontSize: 24, color: 'var(--info)', marginBottom: 8 }} />
            <br />
            <Text style={{ fontSize: 13, fontWeight: 500 }}>
              {state.framework ? state.framework : '启动机构级研究流程...'}
            </Text>
            <div style={{ marginTop: 8 }}>
              <Progress
                percent={Math.round(glancableProgress * 100)}
                size="small"
                strokeColor="var(--info)"
                format={() => `${state.acquiredModules.length}/5 模块`}
                style={{ maxWidth: 200, margin: '0 auto' }}
              />
            </div>
            {state.scopingRationale && (
              <Text type="secondary" style={{ fontSize: 11, marginTop: 4, display: 'block' }}>
                {state.scopingRationale}
              </Text>
            )}
          </div>
          {renderTraceHandle()}
        </Card>
      )}

      {synth && (
        <div style={{ marginBottom: 16 }}>
          {/* Recommendation Header Card */}
          <Card
            size="small"
            style={{
              marginBottom: 12,
              background: `linear-gradient(135deg, ${rec.bg} 0%, rgba(0,0,0,0.8) 100%)`,
              border: `1px solid ${rec.text}33`,
            }}
            bodyStyle={{ padding: '14px 16px' }}
          >
            <Row align="middle" justify="space-between">
              <Col>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <div style={{
                    background: rec.text, color: '#000',
                    fontWeight: 800, fontSize: 14, padding: '4px 14px',
                    borderRadius: 4, letterSpacing: 0.5,
                  }}>
                    {rec.label}
                  </div>
                  <div>
                    <Text style={{ color: rec.text, fontWeight: 600, fontSize: 15 }}>
                      {symbol}
                    </Text>
                    {synth.conviction && (
                      <Tag color={synth.conviction === 'high' ? 'green' : synth.conviction === 'low' ? 'red' : 'orange'}
                           style={{ marginLeft: 8, fontSize: 10 }}>
                        {synth.conviction === 'high' ? '高确信度' : synth.conviction === 'low' ? '低确信度' : '中等确信'}
                      </Tag>
                    )}
                  </div>
                </div>
              </Col>
              <Col>
                <div style={{
                  position: 'relative', width: 60, height: 60,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                }}>
                  <Progress
                    type="circle" size={60} percent={Math.round(confVal * 100)}
                    strokeColor={confidenceColor(confVal)}
                    trailColor="rgba(255,255,255,0.1)"
                    format={() => (
                      <div>
                        <div style={{ fontSize: 14, fontWeight: 700, color: confidenceColor(confVal) }}>
                          {Math.round(confVal * 100)}
                        </div>
                      </div>
                    )}
                  />
                </div>
              </Col>
            </Row>
          </Card>

          {/* Executive Summary */}
          <Card size="small" style={{ marginBottom: 12 }}>
            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-start' }}>
              <BulbOutlined style={{ color: 'var(--warning)', fontSize: 14, marginTop: 2 }} />
              <div>
                <Text strong style={{ fontSize: 12, color: 'var(--text-muted)' }}>执行摘要</Text>
                <div style={{ fontSize: 13, lineHeight: 1.7, marginBottom: 8, marginTop: 4, color: 'var(--text)' }}>
                  <Markdown
                    content={synth.executive_summary || ''}
                    citations={synth.citable_sources?.map(s => s.n)}
                    onCitationClick={(n) => openCitation(synth.citable_sources, n)}
                  />
                </div>
                {synth.target_rationale && (
                  <div style={{ fontSize: 12, color: 'var(--text-muted)' }}>
                    建议依据：
                    <Markdown
                      content={synth.target_rationale}
                      className="dfx-inline-md"
                      citations={synth.citable_sources?.map(s => s.n)}
                      onCitationClick={(n) => openCitation(synth.citable_sources, n)}
                    />
                  </div>
                )}
              </div>
            </div>
          </Card>

          <Row gutter={12} style={{ marginBottom: 12 }}>
            {/* Scenario Analysis */}
            <Col span={12}>
              <Card
                size="small"
                title={
                  <Text strong style={{ fontSize: 12 }}>
                    <FundOutlined style={{ marginRight: 4, color: 'var(--info)' }} />
                    情景分析
                  </Text>
                }
              >
                {synth.thesis ? (
                  <div>
                    {(['bull_case', 'base_case', 'bear_case'] as (keyof ResearchThesis)[]).map((key, i) => {
                      const labels = ['乐观', '基准', '悲观'];
                      const icons = [<RiseOutlined />, <MinusOutlined />, <ArrowDownOutlined />];
                      const colors = ['var(--positive)', 'var(--text-muted)', 'var(--negative)'];
                      const pct = Math.round((synth.thesis as ResearchThesis)[`${key.split('_')[0]}_probability` as keyof ResearchThesis] as number * 100);
                      const text = (synth.thesis as ResearchThesis)[key] as string;
                      if (!text) return null;
                      return (
                        <div key={key} style={{ marginBottom: i < 2 ? 8 : 0 }}>
                          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 2 }}>
                            <span style={{ fontSize: 11, color: colors[i] }}>
                              {icons[i]} {labels[i]} ({pct}%)
                            </span>
                          </div>
                          <Text style={{ fontSize: 11, color: 'var(--text-muted)', lineHeight: 1.4 }}>
                            {text}
                          </Text>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <Text type="secondary" style={{ fontSize: 11 }}>暂无情景分析</Text>
                )}
              </Card>
            </Col>

            {/* Risk Matrix */}
            <Col span={12}>
              <Card
                size="small"
                title={
                  <Text strong style={{ fontSize: 12 }}>
                    <SafetyCertificateOutlined style={{ marginRight: 4, color: 'var(--warning)' }} />
                    风险矩阵
                  </Text>
                }
              >
                {synth.risk_matrix ? (
                  <div>
                    {(Object.entries(synth.risk_matrix) as [string, { score: number; label: string; detail: string }][]).map(([key, dim]) => (
                      <div key={key} style={{ marginBottom: 6 }}>
                        <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 2 }}>
                          <Text style={{ fontSize: 10, color: 'var(--text-muted)' }}>{dim.label || key}</Text>
                          <Text style={{ fontSize: 10, fontWeight: 600, color: riskScoreColor(dim.score) }}>
                            {dim.score}/10
                          </Text>
                        </div>
                        <Progress
                          percent={dim.score * 10}
                          size="small"
                          showInfo={false}
                          strokeColor={riskScoreColor(dim.score)}
                          trailColor="var(--border)"
                          style={{ margin: 0 }}
                        />
                        {dim.detail && (
                          <Text type="secondary" style={{ fontSize: 9, display: 'block', marginTop: 1 }}>
                            {dim.detail}
                          </Text>
                        )}
                      </div>
                    ))}
                  </div>
                ) : (
                  <Text type="secondary" style={{ fontSize: 11 }}>暂无风险评估</Text>
                )}
              </Card>
            </Col>
          </Row>

          {/* Analysis Cross-Validation */}
          {state.analyses.length > 0 && (
            <Card
              size="small"
              style={{ marginBottom: 12 }}
              title={
                <Text strong style={{ fontSize: 12 }}>
                  <DashboardOutlined style={{ marginRight: 4, color: '#a78bfa' }} />
                  多维度交叉验证
                </Text>
              }
            >
              <Row gutter={[8, 8]}>
                {state.analyses.map((a, i) => (
                  <Col span={12} key={i}>
                    <div style={{
                      background: 'var(--surface-muted)', borderRadius: 6, padding: '8px 10px',
                      borderLeft: `3px solid ${a.momentum_signal === 'bullish' ? 'var(--positive)' : a.momentum_signal === 'bearish' ? 'var(--negative)' : 'var(--border)'}`,
                    }}>
                      <Text strong style={{ fontSize: 11 }}>第{a.round}轮</Text>
                      <Text style={{ fontSize: 11, color: 'var(--text)', display: 'block', marginTop: 4, lineHeight: 1.5 }}>
                        {a.detail}
                      </Text>
                      <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                        {a.momentum_signal && (
                          <span style={{ fontSize: 10, color: SIGNAL_STYLES[a.momentum_signal]?.color || 'var(--text-muted)' }}>
                            {SIGNAL_STYLES[a.momentum_signal]?.icon} 动量:{a.momentum_signal}
                          </span>
                        )}
                        {a.valuation_view && (
                          <span style={{ fontSize: 10, color: SIGNAL_STYLES[a.valuation_view]?.color || 'var(--text-muted)' }}>
                            {SIGNAL_STYLES[a.valuation_view]?.icon} 估值:{a.valuation_view}
                          </span>
                        )}
                        {a.macro_tailwind && (
                          <span style={{ fontSize: 10, color: SIGNAL_STYLES[a.macro_tailwind]?.color || 'var(--text-muted)' }}>
                            {SIGNAL_STYLES[a.macro_tailwind]?.icon} 宏观:{a.macro_tailwind}
                          </span>
                        )}
                        {a.fundamental_quality !== undefined && (
                          <span style={{ fontSize: 10 }}>
                            <TrophyOutlined /> 基本面:{a.fundamental_quality}
                          </span>
                        )}
                      </div>
                    </div>
                  </Col>
                ))}
              </Row>
            </Card>
          )}

          {/* Catalysts & Next Steps */}
          <Row gutter={12} style={{ marginBottom: 12 }}>
            {synth.key_catalysts && synth.key_catalysts.length > 0 && (
              <Col span={12}>
                <Card
                  size="small"
                  title={<Text strong style={{ fontSize: 12 }}><ThunderboltOutlined style={{ color: 'var(--warning)', marginRight: 4 }} />关键催化剂</Text>}
                >
                  {synth.key_catalysts.map((c, i) => (
                    <div key={i} style={{ fontSize: 11, marginBottom: 4, padding: '3px 8px', background: 'rgba(245,158,11,0.12)', borderRadius: 4, color: 'var(--warning)' }}>
                      <ClockCircleOutlined style={{ marginRight: 4 }} />{c}
                    </div>
                  ))}
                </Card>
              </Col>
            )}
            {synth.next_steps && synth.next_steps.length > 0 && (
              <Col span={12}>
                <Card
                  size="small"
                  title={<Text strong style={{ fontSize: 12 }}><AimOutlined style={{ color: 'var(--info)', marginRight: 4 }} />后续研究建议</Text>}
                >
                  {synth.next_steps.map((s, i) => (
                    <div key={i} style={{ fontSize: 11, marginBottom: 4, padding: '3px 8px', background: 'rgba(99,102,241,0.12)', borderRadius: 4, color: '#818cf8' }}>
                      {i + 1}. {s}
                    </div>
                  ))}
                </Card>
              </Col>
            )}
          </Row>

          {/* Data issues */}
          {synth.data_issues && synth.data_issues.length > 0 && (
            <Alert
              type="warning"
              message="数据完整性提示"
              description={synth.data_issues.join('；')}
              showIcon
              style={{ marginBottom: 12 }}
            />
          )}

          {/* 可溯源来源：编号证据条目（带链接 + 可信度），让深研结论像分析师快答一样可核验 */}
          <CitableSources
            sources={synth.citable_sources}
            label={`可溯源来源（${synth.citable_sources?.length ?? 0}）`}
          />

          {/* Sources Footer */}
          <div style={{
            display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            fontSize: 10, color: 'var(--text-muted)', paddingTop: 8,
            borderTop: '1px solid var(--border)',
          }}>
            <span>
              数据模块：{(synth.sources || synth.modules_gathered || []).map(m => MODULE_LABELS[m] || m).join(' · ')}
            </span>
            <span>
              {synth.total_rounds || state.analyses.length} 轮分析 · {elapsed}s · {new Date().toLocaleTimeString()}
            </span>
          </div>

          {/* Execution Trace */}
          {renderTraceHandle()}
        </div>
      )}
    </div>
  );
};

export default AgentLoopStream;