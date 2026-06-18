"""增长分析引擎：常驻的"数据分析师 Agent"。

四大北极星指标围绕运营目标——用户数量 / 留存 / 日活 / 付费转化：
- compute_kpis()  从 auth_users + activity_log 确定性计算 KPI（ground truth，不经 LLM）
- generate_report() KPI → LLM 产出改进建议（叙述层；失败回退规则模板，诚实标注 provider）
- 报告落库 growth_reports（metrics 同库），运营看板按日查看历史

由 main.run_growth_analyst() 每个自然日（北京时间 16:20）自动生成一份。
"""
from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from .metrics_store import _connect as _metrics_connect

logger = logging.getLogger("deepfocus.growth")

BJ_TZ = timezone(timedelta(hours=8))


# ---------------------------------------------------------------- 落库

def init_growth_db() -> None:
    with _metrics_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS growth_reports (
                day TEXT PRIMARY KEY,
                generated_at TEXT NOT NULL,
                provider TEXT NOT NULL DEFAULT 'template',
                kpis_json TEXT NOT NULL,
                report_json TEXT NOT NULL
            )
            """
        )
        conn.commit()


def save_report(day: str, provider: str, kpis: dict, report: dict) -> None:
    try:
        with _metrics_connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO growth_reports (day, generated_at, provider, kpis_json, report_json)"
                " VALUES (?,?,?,?,?)",
                (
                    day,
                    datetime.now(BJ_TZ).isoformat(),
                    provider,
                    json.dumps(kpis, ensure_ascii=False, default=str),
                    json.dumps(report, ensure_ascii=False, default=str),
                ),
            )
            # 保留最近 120 天报告
            conn.execute(
                "DELETE FROM growth_reports WHERE day NOT IN "
                "(SELECT day FROM growth_reports ORDER BY day DESC LIMIT 120)"
            )
            conn.commit()
    except sqlite3.Error as exc:
        logger.warning("growth 报告落库失败：%s", exc)


def _row_to_report(row: sqlite3.Row) -> dict:
    return {
        "day": row["day"],
        "generated_at": row["generated_at"],
        "provider": row["provider"],
        "kpis": json.loads(row["kpis_json"]),
        "report": json.loads(row["report_json"]),
    }


def latest_report() -> Optional[dict]:
    try:
        with _metrics_connect() as conn:
            row = conn.execute(
                "SELECT * FROM growth_reports ORDER BY day DESC LIMIT 1"
            ).fetchone()
        return _row_to_report(row) if row else None
    except sqlite3.Error:
        return None


def has_report_today() -> bool:
    day = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    try:
        with _metrics_connect() as conn:
            row = conn.execute("SELECT day FROM growth_reports WHERE day=?", (day,)).fetchone()
        return row is not None
    except sqlite3.Error:
        return False


def report_history(limit: int = 14) -> list[dict]:
    try:
        with _metrics_connect() as conn:
            rows = conn.execute(
                "SELECT * FROM growth_reports ORDER BY day DESC LIMIT ?", (max(1, limit),)
            ).fetchall()
        return [_row_to_report(r) for r in rows]
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------- KPI 计算（确定性 ground truth）

def _bj_day(ts: str) -> str:
    """activity_log.ts / auth created_at（ISO，UTC 或带时区）→ 北京日期 YYYY-MM-DD。"""
    try:
        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(BJ_TZ).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return ""


def _activity_days_by_actor(days: int) -> dict[str, set[str]]:
    """近 N 天：actor_id → 活跃过的北京日期集合（DAU/留存共用一次扫描）。"""
    since = (datetime.now(BJ_TZ) - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    out: dict[str, set[str]] = {}
    with _metrics_connect() as conn:
        rows = conn.execute(
            "SELECT actor_id, ts FROM activity_log WHERE ts >= ?", (since,)
        ).fetchall()
    for r in rows:
        day = _bj_day(r["ts"])
        if day:
            out.setdefault(r["actor_id"], set()).add(day)
    return out


def _funnel_counts(days: int) -> dict[str, int]:
    """转化漏斗：近 N 天各关键动作的去重人数（pageview→领体验→打开购买页→点已付款）。"""
    since = (datetime.now(BJ_TZ) - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    out: dict[str, int] = {}
    with _metrics_connect() as conn:
        rows = conn.execute(
            "SELECT action, COUNT(DISTINCT actor_id) AS n FROM activity_log"
            " WHERE ts >= ? AND action IN ('pageview','claim_trial','open_buy','buy_contact','invite_click')"
            " GROUP BY action",
            (since,),
        ).fetchall()
    for r in rows:
        out[r["action"]] = int(r["n"])
    return out


def _user_rows() -> list[dict]:
    """auth_users 关键字段（直读 SQLite，避免 ORM 依赖循环）。"""
    from . import storage
    from sqlalchemy import text

    engine = storage.get_engine()
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT id, created_at, membership_source, membership_expires_at FROM auth_users WHERE is_active=1")
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "created_day": _bj_day(str(r[1])) if r[1] is not None else "",
            "membership_source": (r[2] or ""),
            "membership_expires_at": str(r[3]) if r[3] is not None else "",
        }
        for r in rows
    ]


def compute_kpis(days: int = 14) -> dict[str, Any]:
    """北极星 KPI：用户数量 / 日活 / 留存 / 付费转化 + 漏斗，全部确定性计算。"""
    now_bj = datetime.now(BJ_TZ)
    day_list = [(now_bj - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days - 1, -1, -1)]
    today = day_list[-1]

    # —— 用户数量 ——
    users = _user_rows()
    total_users = len(users)
    new_by_day = {d: 0 for d in day_list}
    for u in users:
        if u["created_day"] in new_by_day:
            new_by_day[u["created_day"]] += 1
    new_7d = sum(v for d, v in new_by_day.items() if d >= day_list[max(0, days - 7)])

    # —— 日活（登录用户 + 匿名访客分开看）——
    actor_days = _activity_days_by_actor(days)
    dau_by_day = {d: {"users": 0, "anon": 0} for d in day_list}
    for actor, dset in actor_days.items():
        kind = "users" if actor.startswith("u:") else "anon"
        for d in dset:
            if d in dau_by_day:
                dau_by_day[d][kind] += 1
    dau_series = [
        {"day": d, "users": dau_by_day[d]["users"], "anon": dau_by_day[d]["anon"],
         "total": dau_by_day[d]["users"] + dau_by_day[d]["anon"]}
        for d in day_list
    ]
    dau_today = dau_series[-1]["total"]
    wau = len({a for a, ds in actor_days.items() if any(d >= day_list[max(0, days - 7)] for d in ds)})

    # —— 留存（注册用户 cohort：注册日后第 1 / 7 天是否回访）——
    def _retention(offset: int) -> dict[str, Any]:
        cohort = ret = 0
        for u in users:
            cd = u["created_day"]
            if not cd or cd not in new_by_day:
                continue
            target = (datetime.strptime(cd, "%Y-%m-%d") + timedelta(days=offset)).strftime("%Y-%m-%d")
            if target > today:  # 还没到回访观察日，不计入分母
                continue
            cohort += 1
            if target in actor_days.get(f"u:{u['id']}", set()):
                ret += 1
        return {"cohort": cohort, "retained": ret,
                "rate": round(ret / cohort * 100, 1) if cohort else None}

    retention_d1 = _retention(1)
    retention_d7 = _retention(7)

    # —— 付费转化 ——
    paid = sum(1 for u in users if u["membership_source"] == "paid")
    funnel = _funnel_counts(days)
    paid_rate = round(paid / total_users * 100, 2) if total_users else None

    return {
        "generated_at": now_bj.isoformat(),
        "window_days": days,
        "users": {"total": total_users, "new_today": new_by_day.get(today, 0), "new_7d": new_7d,
                  "new_by_day": [{"day": d, "n": new_by_day[d]} for d in day_list]},
        "dau": {"today": dau_today, "wau": wau, "series": dau_series},
        "retention": {"d1": retention_d1, "d7": retention_d7},
        "monetization": {"paid_members": paid, "paid_rate_pct": paid_rate,
                         "funnel": {
                             "visitors": funnel.get("pageview", 0),
                             "invite_click": funnel.get("invite_click", 0),
                             "claim_trial": funnel.get("claim_trial", 0),
                             "open_buy": funnel.get("open_buy", 0),
                             "buy_contact": funnel.get("buy_contact", 0),
                         }},
    }


# ---------------------------------------------------------------- AI 分析报告

_REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "highlights": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {"type": "string"}},
    },
}


def _template_report(kpis: dict) -> dict:
    """LLM 不可用时的规则回退：基于阈值给出朴素诊断，诚实标注。"""
    u, dau, ret, mon = kpis["users"], kpis["dau"], kpis["retention"], kpis["monetization"]
    highlights, risks, actions = [], [], []
    if u["new_7d"] > 0:
        highlights.append(f"近7日新增注册 {u['new_7d']} 人")
    else:
        risks.append("近7日零新增注册，拉新通道需要检查（分享/邀请/SEO）")
        actions.append("检查邀请活动入口曝光与分享卡片是否正常")
    d1 = ret["d1"]["rate"]
    if d1 is not None and d1 < 20:
        risks.append(f"次日留存仅 {d1}%，新用户首日没有形成使用习惯")
        actions.append("强化新用户首日价值：注册后引导直达速判卡/复盘等核心功能")
    fun = mon["funnel"]
    if fun["open_buy"] > 0 and fun["buy_contact"] == 0:
        risks.append("有用户打开购买页但无人点「我已付款」，价格或支付流程可能有阻力")
        actions.append("复查套餐定价与购买页文案，考虑限时优惠")
    if not actions:
        actions.append("保持现有节奏，关注 DAU 与留存趋势变化")
    return {
        "summary": (
            f"总用户 {u['total']}，今日 DAU {dau['today']}（周活 {dau['wau']}），"
            f"次日留存 {d1 if d1 is not None else '—'}%，付费会员 {mon['paid_members']} 人"
            f"（转化率 {mon['paid_rate_pct'] if mon['paid_rate_pct'] is not None else '—'}%）。"
        ),
        "highlights": highlights,
        "risks": risks,
        "actions": actions,
    }


async def generate_report(llm: Any = None) -> dict:
    """生成当日增长分析报告并落库。llm 为 CloudResearchLLM 实例；失败回退模板。"""
    kpis = compute_kpis()
    provider, report = "template", None
    if llm is not None:
        prompt = (
            "你是 SaaS 增长分析师。下面是金融终端产品 DEEPFOCUS 最近 14 天的真实运营数据(JSON)。"
            "目标是提升：用户数量、留存、日活、付费转化率。请基于数据给出分析：\n"
            f"{json.dumps(kpis, ensure_ascii=False)}\n\n"
            "输出严格 JSON：{\"summary\": \"3句话以内的总体诊断\", "
            "\"highlights\": [\"亮点，最多3条\"], \"risks\": [\"风险/异常，最多3条\"], "
            "\"actions\": [\"具体可执行的改进动作，按优先级最多5条\"]}。"
            "只引用数据里出现的数字，不要编造；样本量小时明确说明结论置信度有限。"
        )
        try:
            # 本地/生产 llm.complete_json 签名有漂移（schema 参数有无），按签名自适应
            import inspect
            kwargs: dict[str, Any] = {"max_tokens": 1200, "timeout_seconds": 45}
            if "schema" in inspect.signature(llm.complete_json).parameters:
                kwargs["schema"] = _REPORT_SCHEMA
            out = await llm.complete_json(prompt, **kwargs)
            if isinstance(out, dict) and out.get("summary"):
                report = {
                    "summary": str(out.get("summary") or ""),
                    "highlights": [str(x) for x in (out.get("highlights") or [])][:3],
                    "risks": [str(x) for x in (out.get("risks") or [])][:3],
                    "actions": [str(x) for x in (out.get("actions") or [])][:5],
                }
                provider = "llm"
        except Exception as exc:  # noqa: BLE001
            logger.warning("growth LLM 分析失败，回退模板：%s", exc)
    if report is None:
        report = _template_report(kpis)
    day = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    save_report(day, provider, kpis, report)
    return {"day": day, "provider": provider, "kpis": kpis, "report": report,
            "generated_at": datetime.now(BJ_TZ).isoformat()}
