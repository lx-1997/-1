import React, { useEffect, useRef, useState } from 'react';
import * as authService from '../services/authService';
import { formatErrorMessage } from '../services/apiClient';

declare global { interface Window { turnstile?: any; onTurnstileLoad?: () => void; } }

// 按需加载 Cloudflare Turnstile 脚本（只加载一次）
let _tsLoading: Promise<boolean> | null = null;
function loadTurnstile(): Promise<boolean> {
  if (typeof window === 'undefined') return Promise.resolve(false);
  if (window.turnstile) return Promise.resolve(true);
  if (_tsLoading) return _tsLoading;
  _tsLoading = new Promise<boolean>((resolve) => {
    const s = document.createElement('script');
    s.src = 'https://challenges.cloudflare.com/turnstile/v0/api.js';
    s.async = true; s.defer = true;
    s.onload = () => resolve(true);
    s.onerror = () => resolve(false);
    document.head.appendChild(s);
    window.setTimeout(() => resolve(!!window.turnstile), 6000);  // 6s 还没好(可能被墙)→放弃，前端放行
  });
  return _tsLoading;
}

interface TerminalAuthModalProps {
  open: boolean;
  onClose: () => void;
  onAuthed: (username: string, isNew?: boolean) => void;  // isNew=本次为注册（父组件做 T+0 激活）
  /** 触发登录的上下文文案，例如「AI 解读」需要登录。 */
  reason?: string;
}

/**
 * 终端独占页的轻量登录/注册弹窗（Bloomberg 风格）。
 *
 * 复用现成的 authService（JWT 落库），账号即主平台账号；
 * 邮箱选填——仅用户名 + 密码即可注册。登录态由父组件持有，成功后回调 onAuthed。
 */
const TerminalAuthModal: React.FC<TerminalAuthModalProps> = ({ open, onClose, onAuthed, reason }) => {
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [phone, setPhone] = useState('');
  const [email, setEmail] = useState('');
  const [invite, setInvite] = useState('');
  const [hp, setHp] = useState('');  // 蜜罐：真人看不到、留空；机器人会填
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [capSitekey, setCapSitekey] = useState('');  // Turnstile sitekey（空=未启用）
  const tsTokenRef = useRef('');                      // 人机校验票据
  const tsBoxRef = useRef<HTMLDivElement | null>(null);
  const tsWidgetRef = useRef<any>(null);

  // 每次打开重置错误/忙碌态；关闭时清空表单避免残留。打开时若本地有邀请码(来自分享链接 ?ref=)则预填。
  useEffect(() => {
    if (open) {
      setError(''); setBusy(false);
      try {
        const r = localStorage.getItem('df_ref');
        if (r && !invite) { setInvite(r); setMode('register'); }
        // 匿名(本机从未登录过)默认开「注册」态——撞到收藏/自选/盯盘等钩子的多是新访客，直接给注册表单提转化；
        // 老用户(本机有登录记录 df_has_account)仍默认登录，免去多点一下的摩擦。
        else if (!localStorage.getItem('df_has_account')) { setMode('register'); }
        else { setMode('login'); }
      } catch { /* */ }
    } else { setUsername(''); setPassword(''); setPhone(''); setEmail(''); setInvite(''); }
  }, [open]);

  // Esc 关闭
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, onClose]);

  // 打开时拉人机校验配置
  useEffect(() => {
    if (!open) return;
    authService.fetchCaptchaConfig().then(c => setCapSitekey(c.enabled ? c.sitekey : '')).catch(() => setCapSitekey(''));
  }, [open]);

  // 注册态 + 已配 sitekey → 渲染 Turnstile 框（加载失败/被墙则静默放行，靠其余反刷防线）
  useEffect(() => {
    if (!open || mode !== 'register' || !capSitekey || !tsBoxRef.current) return;
    let cancelled = false;
    tsTokenRef.current = '';
    loadTurnstile().then(ok => {
      if (cancelled || !ok || !window.turnstile || !tsBoxRef.current) return;
      try {
        if (tsWidgetRef.current != null) { try { window.turnstile.remove(tsWidgetRef.current); } catch { /* */ } }
        tsWidgetRef.current = window.turnstile.render(tsBoxRef.current, {
          sitekey: capSitekey,
          callback: (t: string) => { tsTokenRef.current = t; },
          'expired-callback': () => { tsTokenRef.current = ''; },
          'error-callback': () => { tsTokenRef.current = ''; },
        });
      } catch { /* 渲染失败放行 */ }
    });
    return () => { cancelled = true; };
  }, [open, mode, capSitekey]);

  if (!open) return null;

  const isRegister = mode === 'register';

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (busy) return;
    const u = username.trim();
    if (!u || !password) { setError('请填写账号和密码'); return; }
    if (isRegister && u.length < 3) { setError('账号至少 3 位'); return; }
    if (password.length < 6) { setError('密码至少 6 位'); return; }
    if (isRegister && !phone.trim() && !email.trim()) { setError('手机号、邮箱至少填写一项'); return; }
    setBusy(true); setError('');
    try {
      const session = isRegister
        ? await authService.register(email.trim(), u, password, phone.trim(), invite.trim(), hp, tsTokenRef.current)
        : await authService.login(u, password);
      try { localStorage.removeItem('df_ref'); localStorage.setItem('df_has_account', '1'); } catch { /* */ }  // 消费邀请码 + 标记本机已有账号(下次弹层默认登录态)
      onAuthed(session.authUser.username, isRegister);
      onClose();
    } catch (err) {
      setError(formatErrorMessage(err));
      setBusy(false);
    }
  };

  return (
    <div className="bbt-auth-backdrop" onClick={onClose}>
      <form className="bbt-auth" onClick={e => e.stopPropagation()} onSubmit={submit}>
        <div className="bbt-auth-head">
          <span className="bbt-auth-key">DEEPFOCUS</span>
          <span className="bbt-auth-amber">{isRegister ? '注册账号' : '登录终端'}</span>
          <button type="button" className="bbt-auth-x" onClick={onClose} aria-label="关闭">✕</button>
        </div>

        <div className="bbt-auth-sub">
          {reason
            ? `${reason}需要登录账号 · 行情与资讯免费`
            : '登录解锁 AI 解读 · 行情与资讯免费'}
        </div>

        {isRegister && invite.trim() && (
          <div className="bbt-auth-gift">
            🎁 好友送你 <b>3 天尊享会员</b> · 注册即得
            <span className="bbt-auth-gift-sub">全球行情 · 关键快讯比别人早 · AI 研报解读</span>
          </div>
        )}

        <label className="bbt-auth-lbl">
          账号
          <input
            className="bbt-auth-inp" value={username} autoFocus autoComplete="username"
            onChange={e => setUsername(e.target.value)}
            placeholder={isRegister ? '设置登录账号' : '账号 / 手机号 / 邮箱'} />
        </label>

        <label className="bbt-auth-lbl">
          密码
          <input
            className="bbt-auth-inp" value={password} type="password"
            autoComplete={isRegister ? 'new-password' : 'current-password'}
            onChange={e => setPassword(e.target.value)} placeholder="至少 6 位" />
        </label>

        {isRegister && (
          <>
            <div className="bbt-auth-hint">💡 手机或邮箱<b>填一项即可</b>，仅用于忘记密码时找回账号</div>
            <label className="bbt-auth-lbl">
              手机号
              <input
                className="bbt-auth-inp" value={phone} type="tel" autoComplete="tel"
                onChange={e => setPhone(e.target.value)} placeholder="请输入手机号" />
            </label>
            <label className="bbt-auth-lbl">
              邮箱
              <input
                className="bbt-auth-inp" value={email} type="email" autoComplete="email"
                onChange={e => setEmail(e.target.value)} placeholder="请输入邮箱" />
            </label>
            <label className="bbt-auth-lbl">
              邀请码 <span style={{ color: '#6e6a4e', fontWeight: 400 }}>(选填)</span>
              <input
                className="bbt-auth-inp" value={invite}
                onChange={e => setInvite(e.target.value.toUpperCase())} placeholder="输入邀请码，注册立得 3 天会员" maxLength={16} />
            </label>
            <div className="bbt-auth-hint" style={{ color: invite.trim() ? '#2ec27e' : '#c98a12' }}>
              {invite.trim() ? '✓ 已带邀请码 · 注册立得 3 天尊享会员' : '🎁 填好友邀请码，注册立得 3 天尊享会员'}
            </div>
            {/* 蜜罐：对真人隐藏（机器人会填）→ 后端判刷号 */}
            <input type="text" name="website" tabIndex={-1} autoComplete="off" aria-hidden="true"
              value={hp} onChange={e => setHp(e.target.value)}
              style={{ position: 'absolute', left: '-9999px', width: 1, height: 1, opacity: 0 }} />
            {capSitekey && <div ref={tsBoxRef} className="bbt-auth-ts" style={{ marginTop: 10, minHeight: 66 }} />}
          </>
        )}

        {error && <div className="bbt-auth-err">{error}</div>}

        <button type="submit" className="bbt-auth-go" disabled={busy}>
          {busy ? '处理中…' : isRegister ? '注册并登录' : '登录'}
        </button>

        <div className="bbt-auth-toggle">
          {isRegister ? '已有账号？' : '还没有账号？'}
          <button
            type="button" className="bbt-auth-link"
            onClick={() => { setMode(isRegister ? 'login' : 'register'); setError(''); }}>
            {isRegister ? '去登录' : '免费注册'}
          </button>
        </div>
      </form>
    </div>
  );
};

export default TerminalAuthModal;
