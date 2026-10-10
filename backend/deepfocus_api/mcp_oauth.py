"""MCP OAuth 2.1 授权服务器（供外部 AI 客户端经浏览器授权接入）。

MCP 规范的授权框架：客户端连 /api/mcp 收到 401（带 resource_metadata 指引）→ 自动发现
本授权服务器 → 动态注册 → 打开浏览器到 /api/oauth/authorize → 用户登录并点「授权」→
带授权码回到客户端 → 换 access token（JWT, typ=mcp, 1h）+ refresh token（30d 轮换）。

设计：
- 公共客户端（Claude Desktop / Code / Cursor 等均为 PKCE 公共客户端）：client_secret 不落库，
  token 端点仅验 PKCE（OAuth 2.1 要求；明文 plain 拒绝，只收 S256）。
- 授权码 60s 单次有效；refresh token 只存 SHA-256 摘要、每次刷新轮换。
- 登录+授权页是后端直出 HTML（同源 localStorage['auth_token'] 检测登录态，未登录就地
  调 /api/auth/login），与 /api/mcp/setup 同一套模式，前端无需发版。
- access token 直接复用 auth JWT 体系（HS256 + DEEPFOCUS_JWT_SECRET，typ=mcp 无 sid），
  mcp_remote 校验后走与网页端同一套会员墙/配额；用户被停用立即失效（每请求回查）。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import sqlite3
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

from . import db
from .shared_utils import utc_now_iso

ISSUER = (os.getenv("DEEPFOCUS_OAUTH_ISSUER") or "https://daocaijing.com").rstrip("/")
RESOURCE = (os.getenv("DEEPFOCUS_MCP_PUBLIC_URL") or "https://daocaijing.com/api/mcp").strip()
SCOPE = "mcp"
CODE_TTL_SECONDS = 60
ACCESS_TOKEN_MINUTES = 60
REFRESH_TOKEN_DAYS = 30
_REGISTER_WINDOW = 3600.0
_REGISTER_MAX_PER_IP = 10  # 每 IP 每小时动态注册上限（防注册表刷爆）
_register_fails: dict = {}

AUTHORIZE_PATH = "/api/oauth/authorize"
TOKEN_PATH = "/api/oauth/token"
REGISTER_PATH = "/api/oauth/register"
AS_METADATA_PATH = "/.well-known/oauth-authorization-server"
RS_METADATA_PATH = "/.well-known/oauth-protected-resource"


def _db_path() -> Path:
    return db.data_path('.mcp_oauth.sqlite3', 'DEEPFOCUS_MCP_OAUTH_DB_PATH')


def _connect() -> sqlite3.Connection:
    conn = db.connect(_db_path())
    conn.row_factory = sqlite3.Row
    return conn


def init_oauth_db() -> None:
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_clients (
                client_id TEXT PRIMARY KEY,
                client_name TEXT NOT NULL DEFAULT '',
                redirect_uris TEXT NOT NULL DEFAULT '[]',
                created_at TEXT NOT NULL,
                last_used_at TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_codes (
                code TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                redirect_uri TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                code_challenge TEXT NOT NULL,
                resource TEXT,
                expires_at REAL NOT NULL,
                used INTEGER NOT NULL DEFAULT 0
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS oauth_refresh (
                token_hash TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT '',
                expires_at REAL NOT NULL,
                revoked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


# --------------------------------------------------------------------------- #
# 动态客户端注册（RFC 7591 简化版：公共客户端，无 secret）
# --------------------------------------------------------------------------- #
def register_client(client_name: str, redirect_uris: list[str], ip: str = "") -> Optional[dict]:
    now = time.time()
    dq = _register_fails.setdefault(ip or "?", [])
    cutoff = now - _REGISTER_WINDOW
    while dq and dq[0] < cutoff:
        dq.popleft()
    dq.append(now)
    if len(dq) > _REGISTER_MAX_PER_IP:
        return None
    uris = [str(u).strip() for u in (redirect_uris or []) if str(u).strip()][:10]
    if not uris:
        return None
    init_oauth_db()
    client_id = "dfc_" + secrets.token_urlsafe(16)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO oauth_clients (client_id, client_name, redirect_uris, created_at) VALUES (?,?,?,?)",
            (client_id, (client_name or "MCP 客户端").strip()[:120], json.dumps(uris), utc_now_iso()),
        )
        conn.commit()
    return {
        "client_id": client_id,
        "client_id_issued_at": int(now),
        "client_name": (client_name or "MCP 客户端").strip()[:120],
        "redirect_uris": uris,
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    }


def _client(conn: sqlite3.Connection, client_id: str) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()


def client_redirect_uris(client_id: str) -> list[str]:
    init_oauth_db()
    with _connect() as conn:
        row = _client(conn, client_id)
    if not row:
        return []
    try:
        return list(json.loads(row["redirect_uris"] or "[]"))
    except (ValueError, TypeError):
        return []


def touch_client(client_id: str) -> None:
    try:
        with _connect() as conn:
            conn.execute("UPDATE oauth_clients SET last_used_at = ? WHERE client_id = ?", (utc_now_iso(), client_id))
            conn.commit()
    except sqlite3.Error:
        pass


# --------------------------------------------------------------------------- #
# 授权码 / PKCE
# --------------------------------------------------------------------------- #
def create_code(client_id: str, user_id: str, redirect_uri: str, code_challenge: str, resource: str = "") -> str:
    init_oauth_db()
    code = "dfac_" + secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO oauth_codes (code, client_id, user_id, redirect_uri, scope, code_challenge, resource, expires_at)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (code, client_id, user_id, redirect_uri, SCOPE, code_challenge,
             resource or None, time.time() + CODE_TTL_SECONDS),
        )
        conn.commit()
    return code


def pkce_s256(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")


def _hash_refresh(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token_pair(client_id: str, user_id: str, username: str, resource: str = "") -> dict:
    """签发 access（JWT typ=mcp，短 TTL）+ refresh（摘要落库，30d）。"""
    from .auth import jwt_secret
    from jose import jwt as _jwt

    now = datetime.now(timezone.utc)
    claims = {
        "sub": user_id, "username": username, "role": "analyst",
        "typ": "mcp", "scope": SCOPE, "client_id": client_id,
        "iat": int(now.timestamp()),
        "exp": now + timedelta(minutes=ACCESS_TOKEN_MINUTES),
    }
    if resource:
        claims["aud"] = resource
    access = _jwt.encode(claims, jwt_secret(), algorithm="HS256")

    refresh = "dfrt_" + secrets.token_urlsafe(32)
    with _connect() as conn:
        conn.execute(
            "INSERT INTO oauth_refresh (token_hash, client_id, user_id, scope, expires_at, created_at)"
            " VALUES (?,?,?,?,?,?)",
            (_hash_refresh(refresh), client_id, user_id, SCOPE, time.time() + REFRESH_TOKEN_DAYS * 86400, utc_now_iso()),
        )
        conn.commit()
    return {
        "access_token": access,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_MINUTES * 60,
        "refresh_token": refresh,
        "scope": SCOPE,
    }


def redeem_code(code: str, client_id: str, redirect_uri: str, code_verifier: str) -> Optional[dict]:
    """校验并消费授权码（PKCE S256 + client/redirect 绑定 + 单次使用 + 过期）。"""
    init_oauth_db()
    with _connect() as conn:
        row = conn.execute("SELECT * FROM oauth_codes WHERE code = ?", (code or "",)).fetchone()
        if row is None or row["used"] or row["expires_at"] < time.time():
            return None
        if row["client_id"] != client_id or row["redirect_uri"] != redirect_uri:
            return None
        if pkce_s256(code_verifier or "") != row["code_challenge"]:
            return None
        conn.execute("UPDATE oauth_codes SET used = 1 WHERE code = ?", (code,))
        conn.execute("DELETE FROM oauth_codes WHERE expires_at < ?", (time.time() - 3600,))
        conn.commit()
    from .auth import get_user_out_by_id

    user = get_user_out_by_id(row["user_id"])
    if user is None or not user.is_active:
        return None
    touch_client(client_id)
    return issue_token_pair(client_id, row["user_id"], user.username, row["resource"] or "")


def rotate_refresh(refresh_token: str, client_id: str) -> Optional[dict]:
    """刷新：旧 refresh 轮换作废，发新对。客户端不匹配/过期/已吊销 → None。"""
    init_oauth_db()
    th = _hash_refresh(refresh_token or "")
    with _connect() as conn:
        row = conn.execute("SELECT * FROM oauth_refresh WHERE token_hash = ?", (th,)).fetchone()
        if row is None or row["revoked"] or row["expires_at"] < time.time():
            return None
        if row["client_id"] != client_id:
            return None
        conn.execute("UPDATE oauth_refresh SET revoked = 1 WHERE token_hash = ?", (th,))
        conn.commit()
    from .auth import get_user_out_by_id

    user = get_user_out_by_id(row["user_id"])
    if user is None or not user.is_active:
        return None
    return issue_token_pair(client_id, row["user_id"], user.username)


def verify_access_token(token: str) -> Optional[dict]:
    """校验 OAuth access token（JWT typ=mcp）。返回 {user_id, username} 或 None。
    带 aud 的 token（客户端传了 resource 参数）必须显式 audience 解码——python-jose 对
    含 aud 但未指定期望受众的 JWT 会抛 JWTClaimsError。"""
    if not token or token.startswith("dfm_"):
        return None
    from .auth import decode_token

    claims = decode_token(token)
    if claims is None:
        from jose import JWTError, jwt as _jwt

        from .auth import JWT_ALG, jwt_secret

        try:
            claims = _jwt.decode(token, jwt_secret(), algorithms=[JWT_ALG], audience=RESOURCE)
        except JWTError as e:
            import logging

            try:
                token_aud = (_jwt.get_unverified_claims(token) or {}).get("aud")
            except Exception:  # noqa: BLE001
                token_aud = "?"
            logging.getLogger("uvicorn.error").warning(
                "MCP OAuth 令牌校验失败: %s: %s (token_aud=%r resource=%s)",
                type(e).__name__, e, token_aud, RESOURCE,
            )
            return None
    if not claims or not claims.get("sub"):
        return None
    return {"user_id": str(claims["sub"]), "username": str(claims.get("username") or "")}


# --------------------------------------------------------------------------- #
# 元数据
# --------------------------------------------------------------------------- #
def protected_resource_metadata() -> dict:
    return {
        "resource": RESOURCE,
        "authorization_servers": [ISSUER],
        "scopes_supported": [SCOPE],
        "bearer_methods_supported": ["header"],
        "resource_documentation": f"{ISSUER}/api/mcp/setup",
    }


def authorization_server_metadata() -> dict:
    return {
        "issuer": ISSUER,
        "registration_endpoint": ISSUER + REGISTER_PATH,
        "authorization_endpoint": ISSUER + AUTHORIZE_PATH,
        "token_endpoint": ISSUER + TOKEN_PATH,
        "scopes_supported": [SCOPE],
        "response_types_supported": ["code"],
        "response_modes_supported": ["query"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "service_documentation": f"{ISSUER}/api/mcp/setup",
    }


# --------------------------------------------------------------------------- #
# 登录 + 授权页（后端直出 HTML；同源 localStorage 检测登录态，未登录就地登录）
# --------------------------------------------------------------------------- #
_CONSENT_PAGE_TMPL = """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>授权接入 稻草财经 MCP</title>
<style>
:root{--bg:#0b0d12;--panel:#12151c;--line:#222733;--text:#e6ebf2;--mute:#8a93a3;--amber:#ffb000;--green:#2bd96a;--blue:#6ab0ff;--red:#ff6b6b}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:15px/1.7 -apple-system,BlinkMacSystemFont,"Segoe UI","PingFang SC","Microsoft YaHei",sans-serif;display:flex;min-height:100vh;align-items:center;justify-content:center;padding:24px}
.card{width:100%;max-width:430px;background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:28px}
h1{font-size:19px;margin:0 0 6px}.sub{color:var(--mute);font-size:13.5px;margin:0 0 18px}
.app{background:rgba(106,176,255,.08);border:1px solid rgba(106,176,255,.3);border-radius:9px;padding:10px 14px;margin:14px 0;font-size:13.5px}
ul{margin:8px 0 0;padding-left:18px;color:var(--mute);font-size:13px}
input{width:100%;background:#0c1018;border:1px solid var(--line);border-radius:8px;color:var(--text);padding:10px 12px;font-size:14px;margin:6px 0}
button{width:100%;background:var(--blue);border:none;color:#08111c;font-weight:700;border-radius:9px;padding:11px;cursor:pointer;font-size:14.5px;margin-top:10px}
button.ghost{background:transparent;border:1px solid var(--line);color:var(--text);font-weight:600}
.err{color:var(--red);font-size:13px;min-height:18px;margin:4px 0}
.ok{color:var(--green)}.acct{color:var(--amber);font-weight:700}
.note{color:var(--mute);font-size:12px;margin-top:14px;border-top:1px solid var(--line);padding-top:12px}
.hide{display:none}
</style></head><body><div class="card">
<h1>稻草财经 MCP <span class="sub">授权请求</span></h1>
<p class="sub">以下应用请求通过 MCP 访问你的稻草财经账号数据。</p>
<div class="app"><b id="appName">MCP 客户端</b> 想要：<ul>
<li>读取行情 / A股复盘 / 个股速判卡 / 资讯 / 研报元数据 / 题材</li>
<li>使用 AI 投研问答（配额与会员权益同网页端）</li>
</ul></div>
<div id="loginBox" class="hide">
  <input id="user" placeholder="用户名 / 邮箱 / 手机号" autocomplete="username">
  <input id="pass" type="password" placeholder="密码" autocomplete="current-password">
  <div class="err" id="err"></div>
  <button onclick="doLogin()">登录并继续</button>
  <p class="note">没有账号？<a href="/" style="color:var(--blue)">到官网注册</a> 后回到本页。</p>
</div>
<div id="consentBox" class="hide">
  <p class="sub">当前登录：<span class="acct" id="who"></span>　<span id="chg" style="cursor:pointer;text-decoration:underline">切换账号</span></p>
  <button onclick="doConsent(true)">授权</button>
  <button class="ghost" onclick="doConsent(false)">拒绝</button>
</div>
<div class="err" id="gerr"></div>
<p class="note">授权后客户端将获得 1 小时有效的访问令牌（可自动续期）。内容仅供研究参考，不构成投资建议。© 稻草财经 · daocaijing.com</p>
</div>
<script>
const qs = new URLSearchParams(location.search);
const OAUTH = { client_id: qs.get('client_id')||'', redirect_uri: qs.get('redirect_uri')||'',
  state: qs.get('state')||'', code_challenge: qs.get('code_challenge')||'',
  code_challenge_method: qs.get('code_challenge_method')||'', resource: qs.get('resource')||'' };
let jwt=''; try{ jwt=localStorage.getItem('auth_token')||''; }catch(e){}
let memJwt='';
function bearer(){ return memJwt || jwt; }
function err(m){ document.getElementById('gerr').textContent=m||''; }
async function whoami(t){
  try{
    const c=new AbortController(); const tm=setTimeout(()=>c.abort(),3000);
    const r=await fetch('/api/oauth/me',{headers:{'Authorization':'Bearer '+t},signal:c.signal});
    clearTimeout(tm);
    if(!r.ok) return null; const d=await r.json(); return d.username||null;
  }catch(e){ return null; }
}
function show(box){ const el=document.getElementById(box); if(el) el.classList.remove('hide'); }
function fatal(m){ err(m); show('loginBox'); }
async function boot(){
  try{
    document.getElementById('appName').textContent = qs.get('client_name') || 'MCP 客户端';
    for(const k of ['client_id','redirect_uri','code_challenge']){
      if(!OAUTH[k]){ err('授权链接不完整（缺少 '+k+'），请回到客户端重新发起连接。'); return; }
    }
    if(OAUTH.code_challenge_method && OAUTH.code_challenge_method!=='S256'){ err('仅支持 S256 校验方式。'); return; }
    const t=bearer();
    if(t){ const u=await whoami(t); if(u){ document.getElementById('who').textContent=u; show('consentBox'); return; } }
    show('loginBox');
  }catch(e){ fatal('页面初始化异常：'+((e&&e.message)||e)+'。请直接在下方登录后授权。'); }
}
async function doLogin(){
  err(''); document.getElementById('err').textContent='';
  try{
    const r=await fetch('/api/oauth/login',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({username:document.getElementById('user').value.trim(),password:document.getElementById('pass').value})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok||!d.access_token){ document.getElementById('err').textContent=d.detail||('登录失败 '+r.status); return; }
    memJwt=d.access_token;
    const u=await whoami(memJwt);
    document.getElementById('who').textContent=u||'';
    document.getElementById('loginBox').classList.add('hide');
    show('consentBox');
  }catch(e){ document.getElementById('err').textContent='网络异常，请稍后再试：'+((e&&e.message)||e); }
}
document.getElementById('chg').onclick=()=>{ memJwt=''; document.getElementById('consentBox').classList.add('hide'); show('loginBox'); };
async function doConsent(approve){
  err('');
  try{
    const r=await fetch('/api/oauth/authorize/consent',{method:'POST',
      headers:{'Content-Type':'application/json','Authorization':'Bearer '+bearer()},
      body:JSON.stringify({approve, ...OAUTH, client_name: qs.get('client_name')||''})});
    const d=await r.json().catch(()=>({}));
    if(!r.ok){ err(d.detail||('授权失败 '+r.status)); return; }
    if(d.redirect){ location.href=d.redirect; }
    else { err('已拒绝。请回到客户端。'); document.getElementById('consentBox').innerHTML='<p class="sub">已拒绝，可关闭此页。</p>'; }
  }catch(e){ err('网络异常，请稍后再试：'+((e&&e.message)||e)); }
}
boot();
</script></body></html>"""


def consent_page() -> str:
    return _CONSENT_PAGE_TMPL


def build_consent_redirect(
    approve: bool, client_id: str, redirect_uri: str, state: str,
    code_challenge: str, method: str, resource: str, user_id: str,
) -> dict:
    """审批结果 → 客户端回调 URL（approve=False 也回调，携带 error，客户端自行处理）。"""
    uris = client_redirect_uris(client_id)
    if redirect_uri not in uris:
        return {"error": "redirect_uri 未在客户端注册列表中"}
    if method and method != "S256":
        return {"error": "仅支持 S256 code_challenge_method"}
    sep = "&" if "?" in redirect_uri else "?"
    if not approve:
        q = {"state": state} if state else {"state": ""}
        q["error"] = "access_denied"
        q["error_description"] = "用户拒绝了授权"
        return {"redirect": f"{redirect_uri}{sep}{urlencode({k: v for k, v in q.items() if v})}"}
    if not code_challenge:
        return {"error": "缺少 code_challenge（PKCE）"}
    code = create_code(client_id, user_id, redirect_uri, code_challenge, resource)
    q = {"code": code}
    if state:
        q["state"] = state
    return {"redirect": f"{redirect_uri}{sep}{urlencode(q)}"}
