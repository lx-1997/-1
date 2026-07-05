"""DAO 财经事件桥接：轮询 DAO 服务器的事件 API（dao_api.py /v1/events），
把新事件（快讯/文章/研报）归一化后灌进 DeepFocus 的实时消息流（realtime_messages），
前端「金融终端」中心通过既有 SSE 实时消费。

启用：环境变量 DEEPFOCUS_DAO_BRIDGE_ENABLED=1
源地址：DEEPFOCUS_DAO_API_URL（默认 http://127.0.0.1:8765，本地开发可用 SSH 隧道映射）
鉴权：DEEPFOCUS_DAO_API_TOKEN（DAO 服务器 .api_server_token 的值）
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Optional

import sqlite3

import httpx
import requests

import time

from . import news_translate
from .realtime_messages import create_realtime_message, update_realtime_message_fields
from .schemas import RealtimeMessageCreateRequest

# 英文快讯入库前直译成中文（默认开；设 0 关闭回到原行为）。
_TRANSLATE_ENABLED = os.getenv("DEEPFOCUS_NEWS_TRANSLATE", "1").strip().lower() in ("1", "true", "yes", "on")

def _bridge_config() -> dict:
    """运行时读取环境变量（必须在 load_dotenv() 之后，否则 .env 不生效）。
    首次启动（无断点）时回填 backfill 条给终端初始内容；之后按断点续传不重复。"""
    return {
        "enabled": os.getenv("DEEPFOCUS_DAO_BRIDGE_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on"),
        "url": os.getenv("DEEPFOCUS_DAO_API_URL", "http://127.0.0.1:8765").rstrip("/"),
        "token": os.getenv("DEEPFOCUS_DAO_API_TOKEN", "").strip(),
        "poll": float(os.getenv("DEEPFOCUS_DAO_BRIDGE_POLL_SECONDS", "4")),
        "backfill": int(os.getenv("DEEPFOCUS_DAO_BRIDGE_BACKFILL", "40")),
        # 同机部署：直读 DAO 的 SQLite 库（绕过 dao_api 的 http.server，最稳）；
        # 留空则走 HTTP API（Mac 开发经 SSH 隧道）。
        "db_path": os.getenv("DEEPFOCUS_DAO_DB_PATH", "").strip(),
    }


_DB_COLS = ("id", "type", "source_id", "title", "content", "url", "source_time", "inserted_at")
# AI 情绪回填列（DAO 采集端 AI 分析完成后 UPDATE 写入；老库无此列时自动降级）
_AI_COLS = ("ai_sentiment", "ai_impact")


def _fetch_events_db(db_path: str, after_id: int, limit: int = 200) -> list[dict]:
    """同机直读 DAO 事件库（只读，绕过 http.server）。"""
    uri = f"file:{db_path}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=10)
    try:
        for cols_tuple in (_DB_COLS + _AI_COLS, _DB_COLS):
            try:
                cols = ",".join(cols_tuple)
                rows = conn.execute(
                    f"SELECT {cols} FROM events WHERE id > ? ORDER BY id ASC LIMIT ?",
                    (after_id, limit),
                ).fetchall()
                return [dict(zip(cols_tuple, r)) for r in rows]
            except sqlite3.OperationalError:
                continue  # 老库无 AI 列 → 降级为基础列
        return []
    finally:
        conn.close()


def _fetch_ai_fills_db(db_path: str, ids: list[int]) -> dict[int, tuple[str, str]]:
    """查询指定事件的 AI 情绪回填结果：{id: (sentiment, impact)}，只返回已回填的。"""
    if not ids:
        return {}
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            marks = ",".join("?" * len(ids))
            rows = conn.execute(
                f"SELECT id, ai_sentiment, ai_impact FROM events "
                f"WHERE id IN ({marks}) AND ai_sentiment != ''",
                ids,
            ).fetchall()
            return {int(r[0]): (str(r[1] or ""), str(r[2] or "")) for r in rows}
        finally:
            conn.close()
    except sqlite3.Error:
        return {}


def _fetch_latest_id_db(db_path: str) -> int:
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=10)
        try:
            v = conn.execute("SELECT max(id) FROM events").fetchone()[0]
            return int(v or 0)
        finally:
            conn.close()
    except Exception:
        return 0

_STATE_PATH = Path(
    os.getenv(
        "DEEPFOCUS_DAO_BRIDGE_STATE_PATH",
        str(Path(__file__).resolve().parents[1] / ".dao_bridge_state.json"),
    )
)

# DAO 事件类型 → 终端 topic / 来源类型 / 标签
_TYPE_META = {
    "realinfo": {"topic": "快讯", "source_type": "dao-news", "tag": "快讯"},
    "article": {"topic": "文章", "source_type": "dao-article", "tag": "文章"},
    "report": {"topic": "研报", "source_type": "dao-report", "tag": "研报"},
    "signal": {"topic": "信号", "source_type": "dao-signal", "tag": "选股信号"},
}

# 轻量情绪/级别启发（DAO 事件不带星级，按关键词上色，给终端视觉层次）
_CRITICAL = ("暴跌", "崩盘", "熔断", "袭击", "战争", "爆炸", "破产", "退市", "黑天鹅", "暴雷")
_BEARISH = ("利空", "大跌", "下跌", "跌破", "危机", "冲突", "调查", "制裁", "下调", "亏损", "减持", "违约")
_BULLISH = ("利好", "大涨", "飙升", "突破", "创新高", "获批", "超预期", "增长", "上调", "中标", "回购", "增持")


def _ai_severity(sentiment: str, impact: str = "") -> str:
    """AI 情绪 → 消息 severity；情绪是自由文本（如「地缘局势缓和」），识别不了时
    再看【影响】字段——但仅当影响只含单边方向（同时利好利空的复杂局面不强行定调）。
    都无法识别返回空串（落回关键词兜底）。"""
    t = (sentiment or "").strip()
    if "利好" in t or "缓和" in t or "偏多" in t:
        return "success"
    if "利空" in t or "升级" in t or "偏空" in t:
        return "warning"
    if "中性" in t:
        return "info"
    imp = (impact or "").strip()
    has_bull, has_bear = "利好" in imp, "利空" in imp
    if has_bull != has_bear:
        return "success" if has_bull else "warning"
    return ""


def _severity(text: str, etype: str = "") -> str:
    t = text or ""
    if etype == "signal":  # 选股信号：机会→success，风险→warning（高确定性风险→critical）
        if "风险" in t:
            return "critical" if "确定性高" in t else "warning"
        if "机会" in t:
            return "success"
    if any(k in t for k in _CRITICAL):
        return "critical"
    if any(k in t for k in _BEARISH):
        return "warning"
    if any(k in t for k in _BULLISH):
        return "success"
    return "info"


def _resolve_symbol(title: str, content: str) -> "str | None":
    """给消息盖股票代码戳：自选股召回（前端本地通知 shouldRecall + 后端离线召回 subscription_matches）
    都要求 message.symbol 命中，而上游事件从不带代码 → 快讯恒不触发个股级召回（Day-1 激活卡
    承诺的"自选有动静通知你"在架构上无法兑现）。用名称索引解析：标题优先，正文只看前 120 字
    （降低长文误命中）；解析不出保持 None，不硬猜。"""
    try:
        from . import stock_name_index
        sym = stock_name_index.resolve_to_code(title or "")
        if not sym and content:
            sym = stock_name_index.resolve_to_code(content[:120])
        return sym
    except Exception:  # noqa: BLE001 - 索引加载失败等一律不阻塞入库
        return None


def _to_request(event: dict) -> RealtimeMessageCreateRequest:
    etype = str(event.get("type") or "").strip()
    meta = _TYPE_META.get(etype, {"topic": "资讯", "source_type": "dao", "tag": etype or "资讯"})
    title = str(event.get("title") or "").strip() or "(无标题)"
    content = str(event.get("content") or "").strip()
    # 情绪标签：AI 判定优先（DAO 采集端 MiniMax 结构化分析），关键词只做兜底
    ai_sent = str(event.get("ai_sentiment") or "").strip()
    ai_impact = str(event.get("ai_impact") or "").strip()
    severity = _ai_severity(ai_sent, ai_impact) or _severity(f"{title} {content}", etype)
    metadata = {
        "dao_event_id": event.get("id"),
        "dao_type": etype,
        "source_time": event.get("source_time") or "",
        "inserted_at": event.get("inserted_at"),
    }
    if ai_sent:
        metadata["ai_sentiment"] = ai_sent
    if ai_impact:
        metadata["ai_impact"] = ai_impact
    return RealtimeMessageCreateRequest(
        title=title,
        content=content,
        source_id=str(event.get("source_id") or event.get("id") or ""),
        source_name="DAO财经",
        source_type=meta["source_type"],
        symbol=_resolve_symbol(title, content),  # 个股级召回的地基：命中自选股才有"盯盘=回访"
        topic=meta["topic"],
        severity=severity,  # type: ignore[arg-type]
        url=(str(event.get("url") or "").strip() or None),
        tags=[meta["tag"]],
        metadata=metadata,
    )


async def _maybe_translate(req: RealtimeMessageCreateRequest) -> RealtimeMessageCreateRequest:
    """英文快讯 → 中文：译文进 title/content（全链路中文），英文原文留 metadata 备查。
    不需译/超时/失败 → 原样返回，绝不丢消息、绝不阻塞主链路超过 LLM 超时。"""
    if not _TRANSLATE_ENABLED:
        return req
    try:
        zh = await news_translate.translate_news(req.title or "", req.content or "")
    except Exception:
        zh = None
    if not zh:
        return req
    meta = dict(req.metadata or {})
    meta["original_title"] = req.title
    if req.content:
        meta["original_content"] = req.content
    meta["translated"] = True
    return req.model_copy(update={"title": zh["title"], "content": zh["content"], "metadata": meta})


def _headers(token: str) -> dict:
    h = {"Accept": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    return h


def _load_after_id() -> Optional[int]:
    try:
        if _STATE_PATH.exists():
            data = json.loads(_STATE_PATH.read_text(encoding="utf-8") or "{}")
            v = data.get("after_id")
            return int(v) if v is not None else None
    except Exception:
        pass
    return None


def _save_after_id(after_id: int) -> None:
    try:
        _STATE_PATH.write_text(json.dumps({"after_id": int(after_id)}, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass


def _new_session():
    """requests Session（trust_env=False 绕过出网代理；禁用连接复用避免对 http.server 的连接被关后复用）。"""
    s = requests.Session()
    s.trust_env = False
    s.headers.update({"Connection": "close"})
    return s


def _fetch_latest_id_sync(url: str) -> int:
    """同步取最新 id（在线程池里跑）。"""
    try:
        with _new_session() as s:
            r = s.get(f"{url}/health", timeout=10)
            r.raise_for_status()
            return int((r.json().get("stats") or {}).get("latest_id") or 0)
    except Exception:
        return 0


def _fetch_events_sync(url: str, token: str, after_id: int, limit: int = 100) -> list[dict]:
    """同步拉事件（经 run_in_executor 在线程里跑）。

    用 requests（urllib3）而非 httpx：DAO 的 dao_api.py 是 http.server，httpx/h11 在 uvicorn 进程内
    会偶发/频发 RemoteProtocolError（"Server disconnected"）；urllib3 对连接断开有内置重试、表现稳定。
    再叠加单轮 3 次重试兜底；失败时上层不前进游标，事件不丢只延迟。
    """
    last_err: Exception | None = None
    for _ in range(3):
        try:
            with _new_session() as s:
                r = s.get(
                    f"{url}/v1/events",
                    params={"after_id": after_id, "limit": limit},
                    headers=_headers(token),
                    timeout=15,
                )
                r.raise_for_status()
                return r.json().get("events") or []
        except Exception as e:
            last_err = e
            continue
    raise last_err if last_err else RuntimeError("fetch failed")


async def run_dao_bridge() -> None:
    """后台任务：断点续传地把 DAO 事件灌进 DeepFocus 实时消息流。"""
    cfg = _bridge_config()
    if not cfg["enabled"]:
        print("[dao-bridge] 未启用（DEEPFOCUS_DAO_BRIDGE_ENABLED=0），跳过")
        return
    url, token, poll, backfill = cfg["url"], cfg["token"], cfg["poll"], cfg["backfill"]
    db_path = cfg["db_path"]
    use_db = bool(db_path) and os.path.exists(db_path)
    src = f"sqlite:{db_path}" if use_db else url
    print(f"[dao-bridge] 启动：源={src} 轮询={poll}s 模式={'直读DB' if use_db else 'HTTP'}")
    loop = asyncio.get_event_loop()
    after_id = _load_after_id()
    pending_ai: dict[int, tuple[str, float]] = {}  # dao_event_id -> (message_id, 放弃时限)
    while True:
        try:
            if after_id is None:
                if use_db:
                    latest = await loop.run_in_executor(None, _fetch_latest_id_db, db_path)
                else:
                    latest = await loop.run_in_executor(None, _fetch_latest_id_sync, url)
                after_id = max(0, latest - backfill)
                print(f"[dao-bridge] 首次启动，从 after_id={after_id}（latest={latest} 回填{backfill}）")
            if use_db:
                events = await loop.run_in_executor(None, _fetch_events_db, db_path, after_id)
            else:
                events = await loop.run_in_executor(None, _fetch_events_sync, url, token, after_id)
            for ev in events:
                try:
                    req = await _maybe_translate(_to_request(ev))
                    msg = create_realtime_message(req)
                    # 快讯秒发时 AI 情绪常未就绪 → 记入待回填表，AI 结果落库后更新同条消息
                    # msg 为 None 表示命中内容过滤(斧头/futou)被拦下，跳过回填登记
                    if (
                        msg is not None
                        and use_db
                        and str(ev.get("type")) == "realinfo"
                        and not str(ev.get("ai_sentiment") or "").strip()
                    ):
                        pending_ai[int(ev.get("id") or 0)] = (msg.id, time.time() + 600)
                except Exception as e:  # 单条失败不影响后续
                    print(f"[dao-bridge] 灌入失败 id={ev.get('id')}: {e}")
                eid = int(ev.get("id") or after_id)
                if eid > after_id:
                    after_id = eid
            if events:
                _save_after_id(after_id)
            # —— AI 情绪回填：已秒发的快讯等到 AI 结果后，原地升级标签并重广播 ——
            if pending_ai:
                now_ts = time.time()
                for eid in [k for k, (_, exp) in pending_ai.items() if exp < now_ts]:
                    pending_ai.pop(eid, None)  # 超时放弃（AI 失败/无结果），保留关键词标签
                fills = await loop.run_in_executor(
                    None, _fetch_ai_fills_db, db_path, list(pending_ai.keys())
                )
                for eid, (sent, impact) in fills.items():
                    mid, _exp = pending_ai.pop(eid)
                    sev = _ai_severity(sent, impact)
                    patch = {"ai_sentiment": sent}
                    if impact:
                        patch["ai_impact"] = impact
                    try:
                        update_realtime_message_fields(mid, severity=sev or None, metadata_patch=patch)
                    except Exception as e:
                        print(f"[dao-bridge] AI情绪回填更新失败 id={eid}: {e}")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            cause = e.__cause__ or e.__context__
            print(f"[dao-bridge] 拉取失败（{poll}s 后重试）: {type(e).__name__}: {e} | cause={type(cause).__name__ if cause else None}: {cause}")
        await asyncio.sleep(poll)
