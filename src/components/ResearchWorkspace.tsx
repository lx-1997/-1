import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { apiGet, apiPost } from '../services/apiClient';
import { getAuthSnapshot, isAuthRevisionCurrent, subscribeAuth } from '../state/authSession';
import './ResearchWorkspace.css';

interface Fact { status: string; value: string | number | null }
interface Evidence { status: string; fields?: Record<string, Fact>; currency?: string; source?: string; report_date?: string; published_date?: string; period_basis?: string; issues?: string[] }
interface Revision { id: string; created: number; config_version: number; payload: { evidence: Record<string, Evidence>; changed_modules?: string[]; changes?: {tool: string; field: string; before?: Fact; after?: Fact}[]; report: { analysis_status?: string; evidence_audit?: {checks?: {tool: string; market_time?: string; fetched_at?: string}[]}; followup_question?: string; summary?: string; observations?: { tool: string; field: string; interpretation: string }[]; hypothesis_updates?: string[] }; project_context?: { notes: string; hypotheses: string[] } } }
interface Project { id: string; title: string; symbol: string; market: string; objective: string; notes: string; hypotheses: string[]; interval_seconds: number; version: number; next_due: number; revision_cursor?: string | null; revisions?: Revision[]; runs?: { id: string; status: string; progress: number }[]; last_observation?: { created: number; changed: number } }
const labels: Record<string, string> = { get_market_quote: '行情', get_valuation: '估值', get_financials: '财报', get_financial_statements: '财务三表', get_analyst_consensus: '卖方观点', price: '价格', change_percent: '涨跌幅', pe_ratio: 'PE（以来源口径为准）', pe_dynamic: '动态 PE', forward_pe: '前瞻 PE', pb_ratio: 'PB', market_cap: '总市值', revenue: '营业收入', net_income: '净利润', eps: '每股收益', revenue_yoy: '营收同比', profit_yoy: '利润同比', roe: 'ROE', capex: '资本开支', free_cash_flow: '自由现金流', rating: '评级', consensus_rating: '聚合评级', analyst_count: '分析师人数', institution_count: '机构覆盖', target_price: '目标价', avg_target_price: '平均目标价', forecast_eps_this_year: '来源本年预测 EPS' };
const statuses: Record<string, string> = { usable: '结构检查通过', limited: '存在口径限制', rejected: '已拒绝引用', no_data: '暂无数据', unavailable: '暂不可用', pending: '排队中', running: '核验与分析中', completed: '本轮检查完成', failed: '检查失败，可重试', cancelled: '已取消', generated: 'AI 研究解读', insufficient_data: '事实不足', unchanged: '事实未变化' };
const issueLabels: Record<string, string> = { market_time_timezone_unspecified: '行情时间未说明时区，不能据此认定实时性', issuer_not_returned: '来源未返回主体代码', currency_unspecified: '来源未说明币种', source_unspecified: '来源标识缺失', as_of_missing: '来源时点未返回', period_basis_unspecified: '累计或单季口径未说明', publication_date_missing_not_point_in_time: '披露时间缺失，不可用于历史时点验证', report_period_missing: '报告期间缺失', historical_report_only: '仅有历史财报，不能称为最新', historical_snapshot_only: '仅有历史快照', pe_basis_unspecified: 'PE 的 TTM 或前瞻口径未说明' };
const date = (stamp?: number) => stamp ? new Date(stamp * 1000).toLocaleString('zh-CN', { hour12: false }) : '尚未检查';
const errorText = (error: any) => typeof error?.detail === 'string' ? error.detail : '请求未完成，请稍后重试';

export default function ResearchWorkspace() {
  const session = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot);
  const [projects, setProjects] = useState<Project[]>([]);
  const [selected, setSelected] = useState('');
  const [detail, setDetail] = useState<Project | null>(null);
  const [loadedRevision, setLoadedRevision] = useState(-1);
  const [revisionId, setRevisionId] = useState('');
  const [history, setHistory] = useState<{project: string; items: Revision[]; cursor: string | null} | null>(null);
  const [pinnedRevision, setPinnedRevision] = useState<Revision | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [creating, setCreating] = useState(false);
  const [notes, setNotes] = useState('');
  const [hypotheses, setHypotheses] = useState('');
  const [interval, setIntervalValue] = useState(0);
  const [followup, setFollowup] = useState('');
  const refreshRequest = useRef<{ project: string; question: string; key: string } | null>(null);
  const sequence = useRef(0);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;
  const visible = loadedRevision === session.revision;

  useEffect(() => {
    const controller = new AbortController();
    const revision = session.revision;
    setDetail(null); setSelected(''); setProjects([]); setError(''); setCreating(false); setBusy(false); setFollowup(''); refreshRequest.current = null;
    if (session.token) apiGet<{ projects: Project[] }>('/api/research/projects', { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) {
        setProjects(result.projects); setSelected(result.projects[0]?.id || ''); setLoadedRevision(revision);
      }
    }).catch(err => { if (!controller.signal.aborted && isAuthRevisionCurrent(revision)) setError(errorText(err)); });
    return () => controller.abort();
  }, [session.revision, session.token]);

  useEffect(() => {
    const revision = session.revision;
    const request = ++sequence.current;
    const controller = new AbortController();
    setDetail(null); setRevisionId(''); setHistory(null); setPinnedRevision(null); setFollowup(''); refreshRequest.current = null;
    if (!selected || !session.token) return () => controller.abort();
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const result = await apiGet<Project>(`/api/research/projects/${selected}`, { signal: controller.signal });
        if (controller.signal.aborted || sequence.current !== request || !isAuthRevisionCurrent(revision)) return;
        setDetail(result); setLoadedRevision(revision);
        timer = setTimeout(load, result.runs?.some(run => ['pending', 'running'].includes(run.status)) ? 3000 : 30000);
      } catch (err) {
        if (!controller.signal.aborted && sequence.current === request && isAuthRevisionCurrent(revision)) {
          setError(errorText(err)); timer = setTimeout(load, 10000);
        }
      }
    };
    void load();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [selected, session.revision, session.token]);

  useEffect(() => {
    setNotes(detail?.notes || ''); setHypotheses((detail?.hypotheses || []).join('\n'));
    setIntervalValue(detail?.interval_seconds || 0);
  }, [detail?.id, detail?.version]);

  async function action(work: () => Promise<void>) {
    const revision = session.revision;
    setBusy(true); setError('');
    try { await work(); } catch (err) { if (isAuthRevisionCurrent(revision)) setError(errorText(err)); }
    finally { if (isAuthRevisionCurrent(revision)) setBusy(false); }
  }
  const current = visible && detail?.id === selected ? detail : null;
  const latestRun = current?.runs?.[0];
  const running = !!latestRun && ['pending', 'running'].includes(latestRun.status);
  const revisions: Revision[] = Array.from(new Map<string, Revision>([...(current?.revisions || []), ...(history?.project === current?.id ? history?.items || [] : []), ...(pinnedRevision ? [pinnedRevision] : [])].map(item => [item.id, item] as [string, Revision])).values());
  const revision = revisions.find(item => item.id === revisionId) || current?.revisions?.[0];
  const report = revision?.payload.report;
  async function refreshProject(question = '') {
    if (!current) return;
    const requestRevision = session.revision;
    const previous = refreshRequest.current;
    const key = previous?.project === current.id && previous.question === question ? previous.key : `manual:${Date.now()}:${Math.random().toString(36).slice(2)}`;
    refreshRequest.current = { project: current.id, question, key };
    await apiPost(`/api/research/projects/${current.id}/refresh`, { request_key: key, followup_question: question });
    const next = await apiGet<Project>(`/api/research/projects/${current.id}`);
    if (isAuthRevisionCurrent(requestRevision) && selectedRef.current === current.id) {
      setDetail(next); setFollowup(''); refreshRequest.current = null;
    }
  }
  if (!session.token) return <section className="rw-space"><h2>研究空间</h2><p>登录后保存私有研究、笔记和跟踪设置。</p></section>;

  return <section className="rw-space" aria-label="研究空间">
    <header className="rw-header"><div><h2>研究空间</h2><p>保留依据，跟踪变化，让每次研究接着往下做。</p></div><button onClick={() => setCreating(!creating)}>{creating ? '收起' : '＋ 新建研究'}</button></header>
    {error && <p role="alert" className="rw-error">{error}</p>}
    {creating && <form className="rw-create" onSubmit={event => {
      event.preventDefault(); const data = new FormData(event.currentTarget); const requestRevision = session.revision;
      void action(async () => {
        const created = await apiPost<Project>('/api/research/projects', { title: data.get('title'), symbol: String(data.get('symbol')).trim().toUpperCase(), market: data.get('market'), objective: data.get('objective') });
        if (!isAuthRevisionCurrent(requestRevision)) return;
        setProjects(previous => [created, ...previous]); setSelected(created.id); setCreating(false); setLoadedRevision(requestRevision);
      });
    }}>
      <label>研究名称<input name="title" required maxLength={120} placeholder="例如：谷歌盈利与估值跟踪" /></label>
      <div className="rw-form-row"><label>市场<select name="market"><option value="CN">A股</option><option value="HK">港股</option><option value="US">美股</option></select></label><label>股票代码<input name="symbol" required maxLength={12} placeholder="600519 / 00700 / GOOGL" /></label></div>
      <label>研究问题<textarea name="objective" required maxLength={2000} placeholder="想验证什么？关注多长时间？什么变化会推翻判断？" /></label>
      <button disabled={busy} type="submit">{busy ? '正在保存…' : '创建私有研究'}</button>
    </form>}
    {!visible || !projects.length ? <p className="rw-empty">{!visible ? '正在读取研究项目…' : '还没有研究项目。新建后可核验数据、保存假设并开启定期跟踪。'}</p> : <div className="rw-layout">
      <nav aria-label="研究项目" className="rw-projects">{projects.map(item => <button key={item.id} aria-pressed={selected === item.id} onClick={() => { setSelected(item.id); setError(''); }}><strong>{item.title}</strong><span>{item.market} · {item.symbol}</span></button>)}</nav>
      <main className="rw-main">{!current ? <p>正在读取项目…</p> : <>
        <header className="rw-project-header"><div><span className="rw-eyebrow">{current.market} / {current.symbol} · 私有研究</span><h3>{current.title}</h3><p>{current.objective}</p></div><button disabled={busy || running} onClick={() => { void action(async () => {
          await refreshProject();
        }); }}>{running ? '检查进行中…' : '更新研究'}</button></header>
        <div className="rw-run-status" role="status"><span>{latestRun ? statuses[latestRun.status] || latestRun.status : '尚未开始核验'}</span><span>上次检查：{date(current.last_observation?.created)}</span>{current.last_observation?.changed === 0 && <span>跟踪字段未变化，已保留原版本</span>}</div>
        {running && <><progress max={100} value={latestRun.progress} aria-label="研究任务进度" /><button className="rw-secondary" disabled={busy} onClick={() => { const requestRevision = session.revision; void action(async () => {
          await apiPost(`/api/agents/tasks/${latestRun.id}/cancel`); const next = await apiGet<Project>(`/api/research/projects/${current.id}`);
          if (isAuthRevisionCurrent(requestRevision) && selectedRef.current === current.id) setDetail(next);
        }); }}>取消本轮检查</button></>}
        <form onSubmit={event => { event.preventDefault(); void action(() => refreshProject(followup.trim())); }}><label>接着研究<textarea value={followup} maxLength={2000} onChange={event => setFollowup(event.target.value)} placeholder="基于已有研究继续提问，例如：哪些变化会推翻当前盈利判断？" /></label><button disabled={busy || running || !followup.trim()} type="submit">提交追问</button></form>
        {revision && <>
          <div className="rw-version"><label>研究版本 <select aria-label="研究版本" value={revision.id} onChange={event => { setRevisionId(event.target.value); setPinnedRevision(revisions.find(item => item.id === event.target.value) || null); }}>{revisions.map(item => <option key={item.id} value={item.id}>{date(item.created)} · 配置 v{item.config_version}</option>)}</select></label></div>
          {(history?.project === current.id ? history.cursor : current.revision_cursor) && <button disabled={busy} onClick={() => { const authRevision = session.revision; const cursor = history?.project === current.id ? history.cursor : current.revision_cursor; void action(async () => { const page = await apiGet<{revisions: Revision[]; next_cursor: string | null}>(`/api/research/projects/${current.id}/revisions?before=${encodeURIComponent(cursor || '')}`); if (isAuthRevisionCurrent(authRevision) && selectedRef.current === current.id) setHistory(previous => ({project: current.id, items: [...(previous?.project === current.id ? previous.items : []), ...page.revisions], cursor: page.next_cursor})); }); }}>加载更早的研究版本</button>}
          <article className="rw-report"><span className="rw-eyebrow">{statuses[report?.analysis_status || ''] || '研究记录'}</span><h4>本轮判断</h4>{report?.followup_question && <p>本轮追问：{report.followup_question}</p>}<p>{report?.summary}</p>{report?.observations?.map((observation, index) => <p key={index}><b>{labels[observation.tool]} / {labels[observation.field] || observation.field}：</b>{observation.interpretation}</p>)}
            {!!report?.hypothesis_updates?.length && <><h4>待验证的假设</h4><ul>{report.hypothesis_updates.map((item, index) => <li key={index}>{item}</li>)}</ul></>}
          </article>
          {!!revision.payload.changes?.length && <details className="rw-changes"><summary>本轮字段变化（{revision.payload.changes.length}）</summary><dl>{revision.payload.changes.map((change, index) => <div key={index}><dt>{labels[change.tool]} / {labels[change.field] || change.field}</dt><dd>{change.before?.value == null ? '未取得' : String(change.before.value)} → {change.after?.value == null ? '未取得' : String(change.after.value)}</dd></div>)}</dl></details>}
          {revision.payload.project_context && <details className="rw-changes"><summary>该版本的笔记与假设</summary><p>{revision.payload.project_context.notes || '未填写个人笔记'}</p><ul>{revision.payload.project_context.hypotheses.map((item, index) => <li key={index}>{item}</li>)}</ul></details>}
          <h4>事实与覆盖</h4><p className="rw-caption">以下为数据源原始数值。结构检查通过不代表独立原文审计；缺失字段保持为空。</p>
          <div className="rw-evidence">{Object.entries(revision.payload.evidence).filter(([, value]) => value.fields).map(([tool, evidence]) => <article key={tool}><header><b>{labels[tool] || tool}</b><span>{statuses[evidence.status] || evidence.status}</span></header><p className="rw-caption">{evidence.report_date ? `报告期 ${evidence.report_date} · ${evidence.period_basis || '累计/单季口径未返回'}` : '请以本轮来源时点为准'}{evidence.currency ? ` · ${evidence.currency}` : ' · 币种未返回'}{evidence.published_date ? ` · 披露 ${evidence.published_date}` : ''}</p>{!!evidence.issues?.length && <p className="rw-caption">{evidence.issues.map(issue => issueLabels[issue] || '此项元数据需复核').join('；')}</p>}<p className="rw-caption">来源时间：{report?.evidence_audit?.checks?.find(check => check.tool === tool)?.market_time || report?.evidence_audit?.checks?.find(check => check.tool === tool)?.fetched_at || '未返回'}</p><dl>{Object.entries(evidence.fields || {}).map(([field, fact]) => <div key={field}><dt>{labels[field] || field}</dt><dd>{['usable', 'limited'].includes(fact.status) ? String(fact.value) : '—'}</dd></div>)}</dl>{evidence.source && (/^https:\/\//i.test(evidence.source) ? <a href={evidence.source} target="_blank" rel="noreferrer">查看数据来源 ↗</a> : <small>来源：{evidence.source}</small>)}</article>)}</div>
        </>}
        <details className="rw-settings" open={!revision}><summary>研究笔记、假设与持续跟踪</summary><label>个人笔记（观点，不作为已核实事实）<textarea value={notes} maxLength={8000} onChange={event => setNotes(event.target.value)} /></label><label>待验证假设（每行一条，最多十二条）<textarea value={hypotheses} onChange={event => setHypotheses(event.target.value)} /></label><label>自动跟踪频率<select value={interval} onChange={event => setIntervalValue(Number(event.target.value))}><option value={0}>手动检查</option><option value={86400}>每天检查</option><option value={604800}>每周检查</option></select></label><p className="rw-caption">检查行情、财报、估值、卖方观点及资料变化；仅变化时更新解读。可随时改为手动停止定期跟踪。</p><button disabled={busy} onClick={() => { const requestRevision = session.revision; void action(async () => {
          const updated = await apiPost<Project>(`/api/research/projects/${current.id}/settings`, { expected_version: current.version, notes, hypotheses: hypotheses.split('\n').map(item => item.trim()).filter(Boolean), interval_seconds: interval });
          if (isAuthRevisionCurrent(requestRevision) && selectedRef.current === current.id) setDetail(updated);
        }); }}>保存研究设置</button>{current.interval_seconds > 0 && <p className="rw-caption">下次计划检查：{date(current.next_due)}</p>}</details>
      </>}</main>
    </div>}
  </section>;
}
