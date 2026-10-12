import React, { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { apiPost } from '../services/apiClient';
import { getAuthSnapshot, isAuthRevisionCurrent, subscribeAuth } from '../state/authSession';
import { PLATFORM_RESULT_EVENT, publishPlatformResult } from '../utils/platformAssistant';
import type { PlatformAction, PlatformAssistantResult, PlatformResultDetail } from '../utils/platformAssistant';
import { platformActionFields } from '../utils/platformActionFields';
import './PlatformAssistantActions.css';

const PlatformWorkspace = React.lazy(() => import('../AppShell'));
const terminalOnly = process.env.REACT_APP_TERMINAL_ONLY === 'true';
const statusLabel = { completed: '已完成', needs_confirmation: '待确认', failed: '未完成' };

export default function PlatformAssistantActions() {
  const session = useSyncExternalStore(subscribeAuth, getAuthSnapshot, getAuthSnapshot);
  const [receipt, setReceipt] = useState<PlatformResultDetail | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [workspaceOpen, setWorkspaceOpen] = useState(false);
  const attempted = useRef(new Set<string>());
  const displayedRequestId = useRef<string | null>(null);
  const confirmingRequestId = useRef<string | null>(null);

  useEffect(() => {
    const receive = (event: Event) => {
      const detail = (event as CustomEvent<PlatformResultDetail>).detail;
      if (!detail || !isAuthRevisionCurrent(detail.revision)) return;
      // The operation may finish after the user has moved to another turn.
      // Its global receipt still refreshes data, but must not replace that turn.
      if (detail.requestId === confirmingRequestId.current && displayedRequestId.current !== detail.requestId) return;
      displayedRequestId.current = detail.requestId;
      setReceipt(detail); setError('');
      if (terminalOnly && detail.navigation) setWorkspaceOpen(true);
    };
    window.addEventListener(PLATFORM_RESULT_EVENT, receive);
    return () => window.removeEventListener(PLATFORM_RESULT_EVENT, receive);
  }, []);
  useEffect(() => {
    setReceipt(null); setBusy(null); setError(''); setWorkspaceOpen(false); attempted.current.clear();
    displayedRequestId.current = null; confirmingRequestId.current = null;
  }, [session.revision]);

  const confirm = async (action: PlatformAction) => {
    if (!receipt || busy || !action.confirmation_token || !isAuthRevisionCurrent(receipt.revision)
      || attempted.current.has(action.id)) return;
    const revision = receipt.revision;
    confirmingRequestId.current = receipt.requestId;
    attempted.current.add(action.id);
    setBusy(action.id); setError('');
    try {
      const result = await apiPost<PlatformAssistantResult>('/api/agents/platform-actions/confirm', {
        confirmation_token: action.confirmation_token,
      });
      // A confirmation returns only its own receipt; keep the other actions
      // reviewed in this turn available for their separate confirmations.
      const updates = new Map((result.platform_actions || []).map(item => [item.id, item]));
      const actions = (receipt.platform_actions || []).map(item => updates.get(item.id) || item);
      for (const item of result.platform_actions || []) {
        if (!actions.some(existing => existing.id === item.id)) actions.push(item);
      }
      publishPlatformResult({ ...result, platform_actions: actions }, revision, receipt.requestId);
    } catch (err) {
      if (isAuthRevisionCurrent(revision) && displayedRequestId.current === receipt.requestId) {
        setError(err instanceof Error ? err.message : '执行结果未收到，请向助手查询操作状态。');
      }
    } finally {
      if (confirmingRequestId.current === receipt.requestId) confirmingRequestId.current = null;
      if (isAuthRevisionCurrent(revision)) setBusy(null);
    }
  };

  const activeReceipt = receipt?.revision === session.revision ? receipt : null;
  return <>
    {workspaceOpen && <div className="platform-assistant-workspace" role="dialog" aria-modal="true" aria-label="平台工作区">
      <button className="platform-assistant-workspace-close" onClick={() => setWorkspaceOpen(false)}>返回 AI 助手</button>
      <React.Suspense fallback={<p role="status">正在打开平台工作区…</p>}><PlatformWorkspace /></React.Suspense>
    </div>}
    {activeReceipt && (activeReceipt.platform_actions || []).length > 0 && <aside className="platform-assistant-actions" aria-label="AI 助手操作结果" aria-live="polite">
      <header><h2>助手操作</h2><button onClick={() => { displayedRequestId.current = null; setReceipt(null); }} aria-label="关闭操作结果">×</button></header>
      {(activeReceipt.platform_actions || []).map(action => <section key={action.id} aria-label={action.title}>
        <div className="platform-assistant-action-title"><strong>{action.title}</strong><span data-status={action.status}>{statusLabel[action.status]}</span></div>
        {Object.keys(action.arguments || {}).length > 0 && <details open={action.status === 'needs_confirmation'}><summary>操作内容</summary><dl className="platform-assistant-fields">{platformActionFields(action.arguments).map((field, index) => <div key={index}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl></details>}
        {action.error && <p role="alert">{action.error}</p>}
        {action.status === 'completed' && action.result != null && <details><summary>查看结果</summary><pre>{typeof action.result === 'string' ? action.result : JSON.stringify(action.result, null, 2)}</pre></details>}
        {action.status === 'needs_confirmation' && action.confirmation_token && <button className="platform-assistant-confirm" disabled={!!busy || attempted.current.has(action.id)} onClick={() => void confirm(action)}>{busy === action.id ? '正在执行…' : attempted.current.has(action.id) ? '已提交，请查询结果' : '确认执行'}</button>}
      </section>)}
      {error && <p role="alert">{error}</p>}
    </aside>}
  </>;
}
