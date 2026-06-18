from __future__ import annotations

import os
import re
import threading
import time
from datetime import datetime
from typing import Optional

import httpx

"""
同花顺 iFinD（HTTP 超级命令）数据客户端。

- 鉴权：长效 refresh_token（env DEEPFOCUS_IFIND_REFRESH_TOKEN，约 30 天）换短效 access_token（约 7 天）。
  access_token 进程内缓存，到期前自动用 refresh_token 轮换；首个 access 可由 env 预置（DEEPFOCUS_IFIND_ACCESS_TOKEN）。
- 能力（按当前账号实测）：A 股实时行情 + 基本面（latest/changeRatio/pe_ttm/pb/totalCapital/turnoverRatio/amount/high/low）。
  ⚠️ 美股无实时权限、港股本账号不通——故只服务 A 股；批量里混入无权限标的会整批失败，调用前已按 A 股过滤。
- 失败绝不抛到主链路：统一返回 {ok:false, error}。tokens 只读 env、不落代码/日志。
"""

BASE = os.getenv("DEEPFOCUS_IFIND_BASE", "https://quantapi.51ifind.com").rstrip("/")
# A 股实时默认指标集（实测有效）
DEFAULT_INDICATORS = "latest,changeRatio,open,high,low,amount,volume,pe_ttm,pb,totalCapital,turnoverRatio"

_lock = threading.Lock()
_access: dict = {"token": "", "expired_time": ""}  # 进程内缓存


def _refresh_token() -> str:
    return (os.getenv("DEEPFOCUS_IFIND_REFRESH_TOKEN") or "").strip()


def enabled() -> bool:
    return bool(_refresh_token())


def allowed_usernames() -> set:
    """iFinD 增强白名单（env DEEPFOCUS_IFIND_ALLOWED_USERS，逗号分隔，小写）。默认 lx199710。"""
    return {u.strip().lower() for u in (os.getenv("DEEPFOCUS_IFIND_ALLOWED_USERS", "lx199710") or "").split(",") if u.strip()}


def user_allowed(username: Optional[str]) -> bool:
    """是否对该用户启用 iFinD 增强——**全平台灰度判定的唯一真源**。
    收口三件事：①已配置(enabled，缺 token 恒 False=故障安全) ②用户名非空 ③在白名单内。"""
    return enabled() and bool(username) and username.strip().lower() in allowed_usernames()


def _now() -> datetime:
    return datetime.now()


def _parse_dt(s: str) -> Optional[datetime]:
    try:
        return datetime.strptime((s or "").strip(), "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return None


def _fetch_access_token() -> Optional[str]:
    """用 refresh_token 换取新的 access_token，写入缓存。失败返回 None。"""
    rt = _refresh_token()
    if not rt:
        return None
    try:
        r = httpx.post(f"{BASE}/api/v1/get_access_token", headers={"Content-Type": "application/json", "refresh_token": rt},
                       json={}, timeout=15, trust_env=False)
        data = (r.json() or {}).get("data") or {}
        tok = (data.get("access_token") or "").strip()
        if tok:
            _access["token"] = tok
            _access["expired_time"] = data.get("expired_time") or ""
            return tok
    except Exception:  # noqa: BLE001
        pass
    return None


def _access_token() -> Optional[str]:
    """返回可用 access_token：有「带到期时间且未临期」的缓存直接用；否则用 refresh 换新（拿到真实到期时间）。
    refresh 失败才回退到 env 种子 token。线程安全。"""
    with _lock:
        exp = _parse_dt(_access["expired_time"])
        if _access["token"] and exp and (exp - _now()).total_seconds() >= 86400:
            return _access["token"]  # 缓存有效（留 1 天余量）
        tok = _fetch_access_token()  # 首次/临期/无到期信息 → 换取（顺带拿到 expired_time，使后续续期可判定）
        if tok:
            return tok
        return _access["token"] or (os.getenv("DEEPFOCUS_IFIND_ACCESS_TOKEN") or "").strip() or None


# A 股代码归一：裸 6 位 → 补交易所后缀；已带 .SH/.SZ/.BJ 原样。
def normalize_a_code(code: str) -> Optional[str]:
    c = (code or "").strip().upper()
    if not c:
        return None
    if re.fullmatch(r"\d{6}\.(SH|SZ|BJ)", c):
        return c
    if re.fullmatch(r"\d{6}", c):
        if c[0] == "6":
            return f"{c}.SH"
        if c[0] in ("0", "3"):
            return f"{c}.SZ"
        if c[0] in ("4", "8") or c.startswith("92"):
            return f"{c}.BJ"
        return f"{c}.SH"
    return None  # 非 A 股（港美股等）——本账号无权限，不处理


def _request_quote(codes: str, indicators: str, token: str) -> dict:
    r = httpx.post(f"{BASE}/api/v1/real_time_quotation",
                   headers={"Content-Type": "application/json", "access_token": token},
                   json={"codes": codes, "indicators": indicators}, timeout=15, trust_env=False)
    return r.json() or {}


def real_time_quote(codes, indicators: str = DEFAULT_INDICATORS) -> dict:
    """A 股实时行情 + 基本面。codes 可为 list 或逗号串（裸 6 位会自动补后缀）。
    返回 {ok, rows:[{code, <indicator>:value,...}], skipped:[非A股原样], error?}。"""
    if not enabled():
        return {"ok": False, "error": "iFinD 未配置（缺 refresh token）", "rows": []}
    raw = codes if isinstance(codes, list) else [c for c in re.split(r"[,，\s]+", str(codes or "")) if c]
    norm, skipped = [], []
    for c in raw:
        n = normalize_a_code(c)
        (norm.append(n) if n else skipped.append(c))
    if not norm:
        return {"ok": False, "error": "无有效 A 股代码（iFinD 本账号仅支持 A 股实时）", "rows": [], "skipped": skipped}
    token = _access_token()
    if not token:
        return {"ok": False, "error": "iFinD 鉴权失败（token 换取失败）", "rows": []}
    try:
        data = _request_quote(",".join(norm), indicators, token)
        if data.get("errorcode") and int(data.get("errorcode")) == -1010:  # token 失效 → 强制换一次重试
            with _lock:
                _access["token"] = ""
            token = _access_token()
            if token:
                data = _request_quote(",".join(norm), indicators, token)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"请求异常：{exc}"[:160], "rows": []}
    if int(data.get("errorcode", -1)) != 0:
        return {"ok": False, "error": data.get("errmsg") or f"iFinD errorcode={data.get('errorcode')}", "rows": [], "skipped": skipped}
    rows = []
    for t in data.get("tables") or []:
        tbl = t.get("table") or {}
        row = {"code": t.get("thscode"), "time": (t.get("time") or [None])[0]}
        for k, v in tbl.items():
            row[k] = v[0] if isinstance(v, list) and v else v
        rows.append(row)
    return {"ok": True, "rows": rows, "skipped": skipped}


_QUOTE_CACHE: dict = {}        # code -> (ts, row)；单标的行情 TTL 缓存，护授权配额
_QUOTE_CACHE_TTL = 300.0       # 5 分钟：基本面补强够新，又把 iFinD 调用压到极低频


def cached_single_quote(symbol: str, ttl: float = _QUOTE_CACHE_TTL) -> Optional[dict]:
    """单 A 股标的的行情+基本面 row（带 TTL 缓存）。非 A股/未配置/失败→None。
    用于「估值补强」低频共享：无论多少请求，每标的每 ttl 秒最多打一次 iFinD。"""
    code = normalize_a_code(symbol)
    if not code:
        return None
    now = time.time()
    hit = _QUOTE_CACHE.get(code)
    if hit and (now - hit[0]) < ttl:
        return hit[1]
    res = real_time_quote(symbol)
    row = (res.get("rows") or [None])[0] if res.get("ok") else None
    _QUOTE_CACHE[code] = (now, row)
    return row


def access_token_status() -> dict:
    """运维自检：是否配置 + 当前缓存 token 到期时间（不返回 token 本身）。"""
    return {"enabled": enabled(), "has_token": bool(_access["token"]), "expired_time": _access["expired_time"]}
