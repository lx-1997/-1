import React, { useCallback, useEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { apiGet, apiPost } from '../services/apiClient';

// 微信扫码绑定到当前 DeepFocus 账号：绑定后可在微信里直接问 DeepFocus（个性化问答）。
// 后端 iLink 渠道，1:1 绑定；这是「问答(PULL)」入口，不是推送。

type StartResp = { qrcode: string; qr_content: string; qr_data_url?: string | null; base_url: string };
type StatusResp = { status?: string; base_url?: string; bound?: boolean };
type MeResp = { bound: boolean; wechat_masked?: string; bound_at?: string; channel_live?: boolean; push_scope?: string; push_symbols?: string[] };

type Phase = 'idle' | 'loading' | 'qr' | 'scaned' | 'confirmed' | 'expired' | 'error';

const sleep = (ms: number) => new Promise(r => setTimeout(r, ms));

const TerminalWeixinBind: React.FC<{ onClose: () => void; showToast?: (m: string) => void; onOpenConsole?: () => void }> = ({ onClose, showToast, onOpenConsole }) => {
  const [me, setMe] = useState<MeResp | null>(null);
  const [qr, setQr] = useState<StartResp | null>(null);
  const [phase, setPhase] = useState<Phase>('idle');
  const [err, setErr] = useState('');
  const [scope, setScope] = useState('off');       // 自助推送范围
  const [kws, setKws] = useState<string[]>([]);     // 关键词（命中任一即推）
  const [draft, setDraft] = useState('');           // 关键词输入框
  const [cfgMsg, setCfgMsg] = useState('');
  const runningRef = useRef(false);

  const loadMe = useCallback(async () => {
    try {
      const m = await apiGet<MeResp>('/api/weixin/bind/me');
      setMe(m);
      if (m.push_scope) setScope(m.push_scope);
      if (m.push_symbols) setKws(m.push_symbols);
    } catch { /* 忽略 */ }
  }, []);

  // 兜底：长轮询偶尔错过 confirmed 跳变时，用户手动刷新一次绑定状态
  const recheck = useCallback(async () => {
    try {
      const m = await apiGet<MeResp>('/api/weixin/bind/me');
      setMe(m);
      if (m.push_scope) setScope(m.push_scope);
      if (m.push_symbols) setKws(m.push_symbols);
      if (m.bound) { runningRef.current = false; setPhase('confirmed'); showToast?.('✅ 微信已绑定'); }
      else { showToast?.('还没检测到绑定——请确认已在微信里点「确认」，稍等几秒再刷新'); }
    } catch { /* 忽略 */ }
  }, [showToast]);

  const addKw = useCallback((raw: string) => {
    setKws(prev => {
      const next = [...prev];
      raw.split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean).forEach(k => {
        if (!next.some(x => x.toLowerCase() === k.toLowerCase())) next.push(k);
      });
      return next.slice(0, 20); // 上限 20 个，够用且防滥推
    });
    setDraft('');
  }, []);
  const removeKw = useCallback((k: string) => setKws(prev => prev.filter(x => x !== k)), []);

  const describe = (sc: string, syms: string[]) =>
    sc === 'all' ? '全部新快讯' : sc === 'watchlist' ? ('命中任一关键词即推：' + syms.join(' / ')) : '已关闭';

  const saveConfig = useCallback(async () => {
    // 输入框里还没回车/没点添加的，也一并算上
    const pending = draft.split(/[,，、\s]+/).map(s => s.trim()).filter(Boolean);
    const symbols = Array.from(new Set([...kws, ...pending].map(s => s.trim()).filter(Boolean))).slice(0, 20);
    if (scope === 'watchlist' && symbols.length === 0) { setCfgMsg('⚠️ 请至少加 1 个关键词'); return; }
    setKws(symbols); setDraft('');
    // 还没绑定：先记住选择，扫码绑定成功后自动生效（后端保存接口要求已绑定）
    if (!me?.bound) {
      try { localStorage.setItem('wx_pending_push', JSON.stringify({ scope, symbols })); } catch { /* */ }
      setCfgMsg('✅ 已记住，扫码绑定后自动生效：' + describe(scope, symbols));
      return;
    }
    setCfgMsg('保存中…');
    try {
      const r = await apiPost<{ ok?: boolean }>('/api/weixin/my-push-config', { scope, symbols });
      if (r && r.ok !== false) {
        setCfgMsg('✅ 已保存：' + describe(scope, symbols));
        void loadMe();
      } else setCfgMsg('⚠️ 保存失败');
    } catch (e: any) { setCfgMsg('⚠️ ' + (e?.response?.data?.detail || '保存失败')); }
  }, [scope, kws, draft, loadMe, me]);

  // 绑定成功后：若之前(未绑定时)选过推送范围，自动应用
  useEffect(() => {
    if (!me?.bound) return;
    let pend: string | null = null;
    try { pend = localStorage.getItem('wx_pending_push'); } catch { /* */ }
    if (!pend) return;
    (async () => {
      try {
        const cfg = JSON.parse(pend!);
        await apiPost('/api/weixin/my-push-config', { scope: cfg.scope, symbols: cfg.symbols || [] });
        try { localStorage.removeItem('wx_pending_push'); } catch { /* */ }
        setScope(cfg.scope); setKws(cfg.symbols || []);
        setCfgMsg('✅ 推送范围已生效：' + describe(cfg.scope, cfg.symbols || []));
        void loadMe();
      } catch { /* 忽略，用户可手动再存一次 */ }
    })();
  }, [me?.bound, loadMe]);

  useEffect(() => { void loadMe(); }, [loadMe]);
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.preventDefault(); onClose(); } };
    window.addEventListener('keydown', onKey);
    return () => { runningRef.current = false; window.removeEventListener('keydown', onKey); };
  }, [onClose]);

  const pollLoop = useCallback(async (qrcode: string, baseUrl: string) => {
    let base = baseUrl;
    while (runningRef.current) {
      let s: StatusResp;
      try {
        s = await apiGet<StatusResp>('/api/weixin/bind/status', { params: { qrcode, base_url: base } });
      } catch { await sleep(2000); continue; }
      if (!runningRef.current) return;
      const st = s.status;
      if (st === 'scaned_but_redirect') { if (s.base_url) base = s.base_url; continue; }
      if (st === 'scaned') { setPhase('scaned'); continue; }
      if (st === 'confirmed') {
        runningRef.current = false; setPhase('confirmed');
        showToast?.('✅ 微信已绑定，DeepFocus 重要快讯会推送给你');
        void loadMe(); return;
      }
      if (st === 'expired') { runningRef.current = false; setPhase('expired'); return; }
      // 'wait' 等：继续轮询
    }
  }, [loadMe, showToast]);

  const start = useCallback(async () => {
    setErr(''); setPhase('loading');
    try {
      const r = await apiPost<StartResp>('/api/weixin/bind/start');
      setQr(r); setPhase('qr'); runningRef.current = true;
      void pollLoop(r.qrcode, r.base_url);
    } catch (e: any) {
      setPhase('error');
      setErr(e?.response?.data?.detail || e?.message || '获取二维码失败，请稍后重试');
    }
  }, [pollLoop]);

  const statusLine = (() => {
    switch (phase) {
      case 'loading': return '正在获取二维码…';
      case 'qr': return '请用微信「扫一扫」扫码 → 在微信中点确认';
      case 'scaned': return '✓ 已扫码，请在微信中点「确认」';
      case 'confirmed': return '✅ 绑定成功！';
      case 'expired': return '二维码已过期，请重新生成';
      case 'error': return err || '出错了';
      default: return '';
    }
  })();

  const box: React.CSSProperties = {
    position: 'fixed', inset: 0, background: 'rgba(0,0,0,.6)', display: 'flex',
    alignItems: 'center', justifyContent: 'center', zIndex: 9999,
  };
  const card: React.CSSProperties = {
    width: 'min(420px, 92vw)', background: '#0e1320', color: '#d7dde8',
    border: '1px solid #283042', borderRadius: 12, padding: '20px 22px',
    boxShadow: '0 12px 40px rgba(0,0,0,.5)', fontSize: 13,
  };
  const btn: React.CSSProperties = {
    background: '#2b6cff', color: '#fff', border: 'none', borderRadius: 8,
    padding: '9px 16px', fontSize: 13, cursor: 'pointer',
  };

  return createPortal(
    <div style={box} onClick={onClose}>
      <div style={card} onClick={e => e.stopPropagation()}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
          <strong style={{ fontSize: 15 }}>🟢 绑定微信 · 收 DeepFocus 快讯</strong>
          <span onClick={onClose} style={{ cursor: 'pointer', color: '#8b95a7', fontSize: 18, lineHeight: 1 }}>×</span>
        </div>

        {me?.bound && phase !== 'qr' && phase !== 'scaned' ? (
          <div style={{ background: '#13351f', border: '1px solid #1f5b34', borderRadius: 8, padding: '10px 12px', marginBottom: 12 }}>
            ✅ 已绑定微信 {me.wechat_masked ? `（${me.wechat_masked}）` : ''}
            {me.bound_at ? <div style={{ color: '#8b95a7', fontSize: 12, marginTop: 4 }}>绑定于 {new Date(me.bound_at).toLocaleString()}</div> : null}
            {me.channel_live === false ? <div style={{ color: '#e0a23c', fontSize: 12, marginTop: 4 }}>⚠️ 推送渠道未开启（管理员设 DEEPFOCUS_WEIXIN_CHANNEL=1），绑定已记录，开启后即可收到。</div> : null}
          </div>
        ) : null}

        {me?.bound && phase !== 'qr' && phase !== 'scaned' ? (
          <div style={{ background: '#3a2a0d', border: '1px solid #6b4e15', borderRadius: 8, padding: '11px 13px', marginBottom: 12, color: '#ffd98a', fontSize: 13, lineHeight: 1.6 }}>
            ⚡ <b>最后一步：去微信激活推送</b>
            <div style={{ color: '#e8cf95', marginTop: 4 }}>
              绑定后请在微信里给 DeepFocus 机器人<b>发一句话</b>（如「你好」），推送才会开通。
              <br />若隔一阵子没再收到，回复任意消息即可<b>重新激活</b>。
            </div>
          </div>
        ) : null}

        {true ? (
          <div style={{ background: '#0b1322', border: '1px solid #283042', borderRadius: 8, padding: '12px 14px', marginBottom: 12 }}>
            <div style={{ fontWeight: 600, marginBottom: 8, color: '#cdd6e6' }}>📣 我的快讯推送设置{!me?.bound ? <span style={{ color: '#7c879b', fontSize: 12, fontWeight: 400 }}>　（先选好，扫码绑定后自动生效）</span> : null}</div>
            {([
              ['all', '全部新快讯', '有新快讯就推给我'],
              ['watchlist', '按关键词推送', '命中任一关键词就推给我'],
              ['off', '关闭推送', '暂不接收任何快讯'],
            ] as const).map(([val, label, hint]) => (
              <label key={val} style={{ display: 'flex', alignItems: 'flex-start', gap: 8, padding: '5px 0', cursor: 'pointer' }}>
                <input type="radio" name="wx-scope" checked={scope === val} onChange={() => setScope(val)} style={{ marginTop: 3 }} />
                <span><span style={{ color: '#e6ecf6' }}>{label}</span><span style={{ color: '#7c879b', fontSize: 12 }}>　{hint}</span></span>
              </label>
            ))}
            {scope === 'watchlist' ? (
              <div style={{ marginTop: 8 }}>
                {/* 已选关键词：标签形式，× 删除 */}
                {kws.length ? (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 6, marginBottom: 8 }}>
                    {kws.map(k => (
                      <span key={k} style={{ display: 'inline-flex', alignItems: 'center', gap: 5, background: '#13294a', border: '1px solid #25467d', color: '#bcd4ff', borderRadius: 14, padding: '3px 8px 3px 10px', fontSize: 12 }}>
                        {k}
                        <span onClick={() => removeKw(k)} title="删除" style={{ cursor: 'pointer', color: '#7fa8e6', fontWeight: 700, lineHeight: 1 }}>×</span>
                      </span>
                    ))}
                  </div>
                ) : null}
                {/* 输入框 + 添加按钮：回车 / 逗号 / 空格 / 点添加，都能加 */}
                <div style={{ display: 'flex', gap: 6 }}>
                  <input
                    value={draft}
                    onChange={e => setDraft(e.target.value)}
                    onKeyDown={e => {
                      if (e.key === 'Enter' || e.key === ',' || e.key === '，') { e.preventDefault(); if (draft.trim()) addKw(draft); }
                      else if (e.key === 'Backspace' && !draft && kws.length) { removeKw(kws[kws.length - 1]); }
                    }}
                    placeholder="输入关键词，回车或点「添加」，如：茅台 / 宁德时代 / 回购"
                    style={{ flex: 1, minWidth: 0, boxSizing: 'border-box', background: '#0e1320', color: '#d7dde8', border: '1px solid #283042', borderRadius: 6, padding: '7px 9px', fontSize: 12 }}
                  />
                  <button style={{ background: '#1c2740', color: '#bcd4ff', border: '1px solid #2d3f63', borderRadius: 6, padding: '0 12px', fontSize: 12, cursor: 'pointer', whiteSpace: 'nowrap' }} onClick={() => draft.trim() && addKw(draft)}>添加</button>
                </div>
                <div style={{ color: '#6b7689', fontSize: 11, marginTop: 8, lineHeight: 1.5 }}>
                  关键词可多选，<b>命中其中任意一个</b>的快讯就会推给你（模糊匹配，不区分大小写）。
                </div>
              </div>
            ) : null}
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginTop: 10 }}>
              <button style={{ ...btn, padding: '7px 14px' }} onClick={saveConfig}>{me?.bound ? '保存设置' : '记住选择'}</button>
              {cfgMsg ? <span style={{ fontSize: 12, color: cfgMsg.startsWith('⚠️') ? '#ff8e8e' : '#5ad17a' }}>{cfgMsg}</span> : null}
            </div>
          </div>
        ) : null}

        {(phase === 'qr' || phase === 'scaned') && qr ? (
          <div style={{ textAlign: 'center', marginBottom: 12 }}>
            {qr.qr_data_url
              ? <img src={qr.qr_data_url} alt="微信绑定二维码" style={{ width: 220, height: 220, background: '#fff', borderRadius: 8, padding: 6 }} />
              : <div style={{ color: '#e0a23c', fontSize: 12, wordBreak: 'break-all' }}>二维码渲染不可用，内容：{qr.qr_content}</div>}
            <div style={{ marginTop: 8 }}>
              <span onClick={recheck} style={{ color: '#5a9cff', cursor: 'pointer', fontSize: 12 }}>已在微信点了确认？点这里刷新状态 ↻</span>
            </div>
          </div>
        ) : null}

        <div style={{ minHeight: 20, marginBottom: 12, color: phase === 'error' ? '#ff6b6b' : phase === 'confirmed' ? '#5ad17a' : '#9aa6ba' }}>
          {statusLine}
        </div>

        {(phase === 'idle' || phase === 'error' || phase === 'expired' || (me?.bound && phase !== 'qr' && phase !== 'scaned')) ? (
          <button style={btn} onClick={start}>
            {me?.bound ? '重新绑定 / 换微信' : '生成二维码'}
          </button>
        ) : null}

        <div style={{ marginTop: 14, paddingTop: 12, borderTop: '1px solid #222a38', color: '#8b95a7', fontSize: 12, lineHeight: 1.6 }}>
          尊享 / 永久会员专享：绑定后，DeepFocus 的重要快讯会<b>主动推送</b>到你微信（带利好/利空标注），可在上方<b>自助选择</b>推送范围。
          <br />绑定后还能在微信里直接<b>向 DeepFocus AI 提问</b>（会员专享，畅聊不限次；常见问题秒回）。
          {onOpenConsole ? (
            <div style={{ marginTop: 10 }}>
              <span onClick={onOpenConsole} style={{ color: '#5a9cff', cursor: 'pointer' }}>📣 打开管理员推送台（内测）→</span>
            </div>
          ) : null}
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default TerminalWeixinBind;
