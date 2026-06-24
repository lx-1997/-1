"""知识星球「海外投行报告」研报流：把研报工作台已下载到本地的研报，归一化成终端可消费的列表。

数据来自研报工作台（modules/research-workbench）按主题下载到 downloads/海外投行报告/ 的 PDF，
配套 manifest.json 记录标题/创建时间/话题等元数据。纯本地磁盘读取，无外网请求——
因此即便沙箱出网受限也始终可用，列表内容即「已抓取入库」的真实研报。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

# 在线列表缓存（按关键词缓存「整池」，与请求条数无关；冷抓 ~12-30s，命中 ~0.1s）。
# TTL 默认 600s，远大于后台保活刷新周期 → 用户请求永远命中热缓存、不会撞到冷抓；
# 新鲜度由后台 wire-refresher 周期性回源保证，而非靠对用户请求过期。
_WIRE_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_WIRE_TTL = float(os.getenv("DEEPFOCUS_WIRE_CACHE_TTL", "600"))
# 一次回源抓取的「池」上限（去重前，交给工作台 resultLimit；搜索非下载、不受 ZSXQ 下载限额影响）
_WIRE_POOL = int(os.getenv("DEEPFOCUS_WIRE_POOL", "500"))

from .research_workbench import WORKBENCH_DIR

# 知识星球「海外投行报告」星球 ID（与研报工作台默认一致）；同机 Node 工作台内部地址
ZSXQ_GROUP = os.getenv("ZSXQ_GROUP", "88888142214212").strip()
_WORKBENCH_PORT = os.getenv("RESEARCH_WORKBENCH_INTERNAL_PORT", "3927").strip()
_WORKBENCH_BASE = f"http://127.0.0.1:{_WORKBENCH_PORT}"

# 研报登录态（ZSXQ cookie）热更新：存到本地文件，运行时可经网页一键替换、即时生效、免重启/免SSH。
# 若设置了 override，则每次检索把它作为 payload.cookie 传给工作台（优先级高于工作台 env ZSXQ_COOKIE）。
_COOKIE_FILE = Path(os.getenv("DEEPFOCUS_ZSXQ_COOKIE_FILE", str(Path(__file__).resolve().parent / ".zsxq_cookie.json")))


def load_zsxq_override() -> dict[str, Any]:
    try:
        d = json.loads(_COOKIE_FILE.read_text("utf-8"))
        if isinstance(d, dict) and str(d.get("cookie") or "").strip():
            return d
    except Exception:  # noqa: BLE001 - 无文件/损坏都按「无 override」处理
        pass
    return {}


def save_zsxq_override(cookie: str, aduid: str = "") -> None:
    payload = {"cookie": (cookie or "").strip(), "aduid": (aduid or "").strip(),
               "updated_at": datetime.now(timezone.utc).isoformat()}
    _COOKIE_FILE.write_text(json.dumps(payload, ensure_ascii=False), "utf-8")


def clear_zsxq_override() -> None:
    try:
        _COOKIE_FILE.unlink()
    except FileNotFoundError:
        pass


def parse_curl_cookie(text: str) -> tuple[str, str]:
    """从粘贴内容里抽出 (cookie, aduid)：
    - 整段 curl：取 `-b/--cookie` 的值作 cookie、`-H 'x-aduid: ...'` 作 aduid（开发者工具 Copy as cURL 直接粘即可）。
    - 纯 cookie 字符串：原样返回，aduid 为空。"""
    t = (text or "").strip()
    looks_curl = ("-b " in t) or ("--cookie" in t) or ("-H " in t) or t.lower().startswith("curl")
    if not looks_curl:
        return t, ""
    cookie = ""
    m = re.search(r"(?:-b|--cookie)\s+(['\"])(.*?)\1", t, re.S)
    if m:
        cookie = m.group(2)
    else:  # 退而求其次：从 -H 'cookie: ...' 取
        m = re.search(r"-H\s+(['\"])\s*cookie:\s*(.*?)\1", t, re.S | re.I)
        if m:
            cookie = m.group(2)
    am = re.search(r"-H\s+(['\"])\s*x-aduid:\s*(.*?)\1", t, re.S | re.I)
    aduid = am.group(2).strip() if am else ""
    return cookie.strip(), aduid


def _auth_payload(cookie: str = "", aduid: str = "") -> dict[str, Any]:
    """返回要并入工作台请求的鉴权字段：显式传入 > 本地 override > 空(用工作台 env)。"""
    if cookie.strip():
        return {"cookie": cookie.strip(), **({"aduid": aduid.strip()} if aduid.strip() else {})}
    ov = load_zsxq_override()
    if ov.get("cookie"):
        return {"cookie": str(ov["cookie"]).strip(), **({"aduid": str(ov.get("aduid") or "").strip()} if ov.get("aduid") else {})}
    return {}


def auth_payload(cookie: str = "", aduid: str = "") -> dict[str, Any]:
    """公开版 _auth_payload：供 main.py 在调工作台其它端点(如 /api/preview)时带上热更新 cookie。"""
    return _auth_payload(cookie, aduid)


async def probe_zsxq(cookie: str = "", aduid: str = "") -> tuple[bool, str]:
    """探活：用当前(或指定)登录态向工作台拉 1 条，验证 cookie 是否仍有效。返回 (ok, 详情)。"""
    payload: dict[str, Any] = {"group": ZSXQ_GROUP, "resultLimit": 1, "searchPages": 1}
    payload.update(_auth_payload(cookie, aduid))
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(f"{_WORKBENCH_BASE}/api/search", json=payload, timeout=30)
        if resp.status_code != 200:
            body = (resp.text or "")[:160]
            return False, f"HTTP {resp.status_code}: {body}"
        data = resp.json()
        if isinstance(data, dict) and data.get("error"):
            return False, str(data.get("error"))[:160]
        return True, "ok"
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {str(exc)[:140]}"


def _extract_doc_date(name: str) -> str:
    """从研报文件名/标题里提取真实发布日期（星球同步时间不可靠）。返回 YYYY-MM-DD / YYYY-MM / ''。"""
    s = name or ""
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", s)  # YYYYMMDD
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
    m = re.search(r"(20\d{2})[-./](\d{1,2})[-./](\d{1,2})", s)  # YYYY-MM-DD
    if m and 1 <= int(m.group(2)) <= 12 and 1 <= int(m.group(3)) <= 31:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    m = re.search(r"(20\d{2})\s*年\s*(\d{1,2})\s*月(?:\s*(\d{1,2})\s*日)?", s)  # 中文年月（日可选）
    if m and 1 <= int(m.group(2)) <= 12:
        if m.group(3) and 1 <= int(m.group(3)) <= 31:
            return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        return f"{m.group(1)}-{int(m.group(2)):02d}"
    return ""


async def fetch_research_wire_online(
    *, limit: int = 60, query: str = "", use_cache: bool = True,
) -> dict[str, Any]:
    """在线从同机 Node 工作台拉海外投行研报：空关键词=最新，带关键词=搜索。

    带 TTL 缓存（默认 150s）：知识星球检索每次约 8s，缓存后重复加载秒回。
    返回与 list_research_wire 同构的 dict（online=True）；调用方失败时回退本地磁盘。
    """
    want = max(1, min(int(limit or 60), 500))
    kw = (query or "").strip()
    cache_key = kw  # 只按关键词缓存「整池」，请求条数仅决定返回多少（切片），不影响命中
    now = time.monotonic()

    def _slice(full: dict[str, Any]) -> dict[str, Any]:
        items = full.get("items") or []
        return {**full, "items": items[:want], "total": len(items)}

    hit = _WIRE_CACHE.get(cache_key)
    if use_cache and hit and (now - hit[0]) < _WIRE_TTL:
        return _slice(hit[1])
    # 回源抓「整池」：resultLimit 拉满、searchPages=0 不限页（工作台内部 20/页翻到 resultLimit）
    payload: dict[str, Any] = {"group": ZSXQ_GROUP, "resultLimit": _WIRE_POOL, "searchPages": 0}
    payload.update(_auth_payload())  # 有热更新 cookie 则用它（优先级高于工作台 env）
    if kw:
        payload["keyword"] = kw
    try:
        async with httpx.AsyncClient(trust_env=False) as client:
            resp = await client.post(f"{_WORKBENCH_BASE}/api/search", json=payload, timeout=60)
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        if hit:  # 抓取失败 → 回退上次成功结果（stale-while-revalidate），绝不让用户看空/报错
            return _slice(hit[1])
        raise
    items: list[dict[str, Any]] = []
    seen_titles: set[str] = set()  # 同一篇研报常被重复发布（file_id 不同、标题同），按标题去重保留最新
    for it in (data.get("items") or []):
        name = str(it.get("name") or "").strip()
        if not name or not _is_doc_file(name):  # 过滤 mp3 等非文档/图片文件
            continue
        title = _clean_title(name)
        dedup_key = title.casefold()
        if dedup_key in seen_titles:
            continue
        seen_titles.add(dedup_key)
        topic_time = str(it.get("topicCreateTime") or it.get("createTime") or "").strip()
        created = str(it.get("createTime") or "").strip()
        _doc = _extract_doc_date(name)
        # 只接受完整 YYYY-MM-DD；部分日期(如「2026年6月」→2026-06)会在前端日期分组里多出脏分组，回退到帖子时间
        disp_date = (_doc if len(_doc) == 10 else "") or topic_time[:10] or created[:10]
        fid = str(it.get("fileId") or "")
        items.append({
            "id": fid or hashlib.sha1(name.encode("utf-8")).hexdigest()[:12],
            "title": title,
            "org": "海外投行",
            "date": disp_date,
            "created_at": topic_time or created,
            "filename": name,
            "out": "",
            "size": int(it.get("size") or 0),
            "hashtag": str(it.get("hashtag") or "#海外投行报告#"),
            "download_count": int(it.get("downloadCount") or 0),
            "file_id": fid,
        })
    full = {"items": items, "total": len(items), "exists": True, "online": True}
    if items:  # 仅缓存成功的非空结果，避免缓存住偶发空响应
        _WIRE_CACHE[cache_key] = (now, full)
        if len(_WIRE_CACHE) > 60:  # 防无界增长
            oldest = min(_WIRE_CACHE.items(), key=lambda kv: kv[1][0])[0]
            _WIRE_CACHE.pop(oldest, None)
    elif hit:  # 偶发空响应 → 也回退上次成功结果
        return _slice(hit[1])
    return _slice(full)

DEFAULT_OUT = "downloads/海外投行报告"
_SKIP_NAMES = {"manifest.json", "files.csv"}
# 只保留「文档 / 图片」类研报；排除音频(mp3/m4a)、视频(mp4/mov)、压缩包等
_DOC_SUFFIXES = {
    ".pdf", ".doc", ".docx", ".ppt", ".pptx", ".txt", ".md", ".markdown",
    ".png", ".jpg", ".jpeg", ".webp", ".gif",
}
_TITLE_SUFFIX_RE = re.compile(
    r"\.(pdf|docx?|pptx?|txt|md|markdown|png|jpe?g|webp|gif)$", re.IGNORECASE
)


def _is_doc_file(name: str) -> bool:
    """是否文档/图片类研报（按扩展名）；无扩展名按非文档处理，过滤掉音视频等。"""
    dot = name.rfind(".")
    return dot != -1 and name[dot:].lower() in _DOC_SUFFIXES


def _clean_title(name: str) -> str:
    return re.sub(r"\s+", " ", _TITLE_SUFFIX_RE.sub("", name)).strip()


def _resolve_dir(out: str) -> Path:
    """把 out 解析到抓取舱内的目录，越界则回退默认目录（防路径穿越）。"""
    root = WORKBENCH_DIR.resolve()
    base = (root / (out or DEFAULT_OUT)).resolve()
    try:
        base.relative_to(root)
    except ValueError:
        return root / DEFAULT_OUT
    return base


def _load_manifest(directory: Path) -> dict[str, dict[str, Any]]:
    manifest_path = directory / "manifest.json"
    if not manifest_path.exists():
        return {}
    try:
        data = json.loads(manifest_path.read_text("utf-8"))
    except (json.JSONDecodeError, OSError, ValueError):
        return {}
    files = data.get("files") if isinstance(data, dict) else None
    if not isinstance(files, dict):
        return {}
    by_name: dict[str, dict[str, Any]] = {}
    for record in files.values():
        if isinstance(record, dict) and record.get("name"):
            by_name[str(record["name"])] = record
    return by_name


def list_research_wire(*, out: str = DEFAULT_OUT, limit: int = 60, query: str = "") -> dict[str, Any]:
    """列出本地已下载的海外投行研报（新→旧）。返回 dict 供上层包成响应模型。"""
    directory = _resolve_dir(out)
    target_out = out or DEFAULT_OUT
    exists = directory.exists() and directory.is_dir()
    items: list[dict[str, Any]] = []

    if exists:
        manifest = _load_manifest(directory)
        for entry in directory.iterdir():
            if not entry.is_file():
                continue
            name = entry.name
            if name in _SKIP_NAMES or name.endswith(".part"):
                continue
            if entry.suffix.lower() not in _DOC_SUFFIXES:
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            meta = manifest.get(name, {})
            created = str(meta.get("createTime") or meta.get("topicCreateTime") or "").strip()
            if not created:
                created = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat()
            topic_id = meta.get("topicId")
            items.append({
                "id": str(topic_id) if topic_id else hashlib.sha1(name.encode("utf-8")).hexdigest()[:12],
                "title": _clean_title(name),
                "org": "海外投行",
                "date": created[:10],
                "created_at": created,
                "filename": name,
                "out": target_out,
                "size": int(stat.st_size),
                "hashtag": str(meta.get("hashtag") or "#海外投行报告#"),
                "download_count": int(meta.get("downloadCount") or 0),
            })

        keyword = (query or "").strip().lower()
        if keyword:
            items = [it for it in items if keyword in it["title"].lower()]
        items.sort(key=lambda it: it["created_at"], reverse=True)
        # 按标题去重（保留最新一条），避免同篇研报重复出现
        deduped: list[dict[str, Any]] = []
        seen_titles: set[str] = set()
        for it in items:
            key = str(it["title"]).strip().casefold()
            if key in seen_titles:
                continue
            seen_titles.add(key)
            deduped.append(it)
        items = deduped

    total = len(items)
    if limit and limit > 0:
        items = items[:limit]
    return {"items": items, "total": total, "exists": exists, "dir": str(directory)}
