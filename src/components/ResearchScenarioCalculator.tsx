import React, { useRef, useState } from 'react';

export interface SavedScenario {
  id: string; revision_id: string; created: number;
  payload: { annual_eps: string; pe: string; fiscal_year: number; currency: string; conditional_price: string;
    relative_to_snapshot_pct: string | null; source_note?: string; notice: string };
}
export interface ScenarioInput {
  request_key: string; revision_id: string; annual_eps: string; pe: string; fiscal_year: number;
  currency: string; share_basis_confirmed: boolean; source_note: string;
}
const format = (value: number | string) => {
  const number = Number(value);
  if (!Number.isFinite(number)) return String(value);
  return number !== 0 && Math.abs(number) < 0.000001 ? number.toExponential(6) : number.toLocaleString('zh-CN', { maximumFractionDigits: 6 });
};

export default function ResearchScenarioCalculator({ revisionId, quote, scenarios, busy, blocked, onSave }: {
  revisionId: string;
  quote?: { currency?: string; fields?: Record<string, { status: string; value: string | number | null }> };
  scenarios: SavedScenario[]; busy: boolean; blocked: boolean; onSave: (input: ScenarioInput) => Promise<void>;
}) {
  const [eps, setEps] = useState(''); const [pe, setPe] = useState('');
  const [year, setYear] = useState(String(new Date().getUTCFullYear() + 1));
  const [currency, setCurrency] = useState(''); const [confirmed, setConfirmed] = useState(false);
  const [note, setNote] = useState('');
  const request = useRef<{ fingerprint: string; key: string } | null>(null);
  const e = Number(eps), p = Number(pe), y = Number(year);
  const valid = !!eps && !!pe && e > 0 && e <= 1e9 && p > 0 && p <= 1e6 && Number.isFinite(e * p)
    && Number.isInteger(y) && y >= 1900 && y <= 2200 && !!currency;
  const field = quote?.fields?.price;
  const reference = field && ['usable', 'limited'].includes(field.status) ? Number(field.value) : NaN;
  const relative = valid && reference > 0 && Number.isFinite(reference) && currency === quote?.currency ? (e * p / reference - 1) * 100 : null;
  return <details className="rw-settings rw-scenario"><summary>可计算情景 · 年度 EPS × PE</summary>
    <p className="rw-caption">手工假设，不是预测。累计或单季 EPS 不会自动填入或年化；仅适用于正年度 EPS 的 PE 法。</p>
    <fieldset disabled={busy} style={{ border: 0, padding: 0, margin: 0 }}>
      <div className="rw-scenario-inputs"><label>盈利年度<input aria-label="情景盈利年度" type="number" min="1900" max="2200" step="1" value={year} onChange={event => setYear(event.target.value)} /></label>
        <label>标的年度 EPS 假设<input aria-label="年度 EPS 假设" type="number" min="0" step="any" value={eps} onChange={event => setEps(event.target.value)} placeholder="请自行填写年度假设" /></label>
        <label>PE 假设<input aria-label="PE 假设" type="number" min="0" step="any" value={pe} onChange={event => setPe(event.target.value)} placeholder="请自行填写估值倍数" /></label>
        <label>假设币种<select value={currency} onChange={event => setCurrency(event.target.value)}><option value="">请选择</option><option value="CNY">CNY</option><option value="HKD">HKD</option><option value="USD">USD</option></select></label></div>
      <div className="rw-scenario-result" aria-live="polite">{valid ? <><b>假设情景价 {format(e * p)} {currency}</b><span>{relative !== null && Number.isFinite(relative) ? `相对所选版本行情 ${relative > 0 ? '+' : ''}${format(relative)}%` : '参考行情缺失或币种不同，不计算相对变化'}</span></> : '填写明确的年度、正 EPS、正 PE 和币种后立即重算'}</div>
      <label className="rw-scenario-confirm"><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我确认这里是年度假设，且每股单位、币种与交易标的一致</label>
      <label>假设说明（个人观点，不作为已核验数据）<textarea maxLength={500} value={note} onChange={event => setNote(event.target.value)} /></label>
      {blocked && <p className="rw-caption">请先完成当前研究或保存笔记设置，再保存这组情景。</p>}
      <button disabled={!valid || !confirmed || busy || blocked} onClick={() => {
        const input = { revision_id: revisionId, annual_eps: eps, pe, fiscal_year: y, currency, share_basis_confirmed: confirmed, source_note: note };
        const fingerprint = JSON.stringify(input);
        const key = request.current?.fingerprint === fingerprint ? request.current.key : `scenario:${Date.now()}:${Math.random().toString(36).slice(2)}`;
        request.current = { fingerprint, key };
        void onSave({ ...input, request_key: key }).then(() => { if (request.current?.key === key) request.current = null; }).catch(() => { /* parent displays the failure; retain retry identity */ });
      }}>保存这组情景</button>
    </fieldset>
    {!!scenarios.length && <><h4>最近保存的情景（最多显示二十组）</h4>{scenarios.map(item => <article key={item.id} className="rw-saved-scenario"><b>{item.payload.fiscal_year} · {format(item.payload.conditional_price)} {item.payload.currency}</b><p>年度 EPS {item.payload.annual_eps} × PE {item.payload.pe} · 用户假设</p><small>{item.payload.notice}</small><button onClick={() => { setEps(item.payload.annual_eps); setPe(item.payload.pe); setYear(String(item.payload.fiscal_year)); setCurrency(item.payload.currency); setNote(item.payload.source_note || ''); setConfirmed(false); }}>使用这组参数</button></article>)}</>}
  </details>;
}
