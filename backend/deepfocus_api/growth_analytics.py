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
            text("SELECT id, created_at, membership_source, membership_expires_at, invited_by FROM auth_users WHERE is_active=1")
        ).fetchall()
    return [
        {
            "id": str(r[0]),
            "created_day": _bj_day(str(r[1])) if r[1] is not None else "",
            "membership_source": (r[2] or ""),
            "membership_expires_at": str(r[3]) if r[3] is not None else "",
            "invited_by": (str(r[4]) if r[4] else ""),
        }
        for r in rows
    ]


def _spread_funnel(days: int) -> dict[str, int]:
    """传播漏斗（复制是全站最大自然分发行为）：复制人数 → utm=copy/img 落地 UV。
    pageview 的 target 以 utm_source 值开头（前端 [utm, referrer].join(' · ')），前缀匹配即归因。"""
    since = (datetime.now(BJ_TZ) - timedelta(days=days)).astimezone(timezone.utc).isoformat()
    out = {"copy_users": 0, "copy_landing_uv": 0}
    with _metrics_connect() as conn:
        row = conn.execute(
            "SELECT COUNT(DISTINCT actor_id) AS n FROM activity_log WHERE ts >= ? AND action = 'copy'",
            (since,),
        ).fetchone()
        out["copy_users"] = int(row["n"] or 0)
        row = conn.execute(
            "SELECT COUNT(DISTINCT actor_id) AS n FROM activity_log"
            " WHERE ts >= ? AND action = 'pageview' AND (target LIKE 'copy%' OR target LIKE 'img%')",
            (since,),
        ).fetchone()
        out["copy_landing_uv"] = int(row["n"] or 0)
    return out


def _calls_funnel(days: int) -> dict[str, Any]:
    """战绩闭环漏斗(P0 灰度)：表态动作(call_create 去重人数/次数 + call_cancel/call_view 次数)
    + 结算触达(metric_daily: call:settle_push_delivered / cold_skip)+ 微信兑现推送回站
    (pageview utm=wxsettle 落地 UV，前缀匹配同 _spread_funnel)。
    白名单期样本极小只作方向参考，不据此做放量决策；相位过线门见 docs/战绩闭环-数据飞轮设计.md §13。
    失败返回空不拖垮 KPI 主体。"""
    out: dict[str, Any] = {"create_users": 0, "create_n": 0, "cancel_n": 0, "view_n": 0,
                           "settle_push_delivered": 0, "settle_push_cold_skip": 0,
                           "wxsettle_landing_uv": 0}
    try:
        since = (datetime.now(BJ_TZ) - timedelta(days=days)).astimezone(timezone.utc).isoformat()
        day_floor = (datetime.now(BJ_TZ) - timedelta(days=days)).strftime("%Y-%m-%d")
        with _metrics_connect() as conn:
            rows = conn.execute(
                "SELECT action, COUNT(*) AS n, COUNT(DISTINCT actor_id) AS u FROM activity_log"
                " WHERE ts >= ? AND action IN ('call_create','call_cancel','call_view') GROUP BY action",
                (since,),
            ).fetchall()
            for r in rows:
                if r["action"] == "call_create":
                    out["create_users"], out["create_n"] = int(r["u"]), int(r["n"])
                elif r["action"] == "call_cancel":
                    out["cancel_n"] = int(r["n"])
                else:
                    out["view_n"] = int(r["n"])
            for key, field in (("call:settle_push_delivered", "settle_push_delivered"),
                               ("call:settle_push_cold_skip", "settle_push_cold_skip")):
                row = conn.execute(
                    "SELECT COALESCE(SUM(count),0) AS n FROM metric_daily WHERE key = ? AND day >= ?",
                    (key, day_floor),
                ).fetchone()
                out[field] = int(row["n"] or 0)
            row = conn.execute(
                "SELECT COUNT(DISTINCT actor_id) AS n FROM activity_log"
                " WHERE ts >= ? AND action = 'pageview' AND target LIKE 'wxsettle%'",
                (since,),
            ).fetchone()
            out["wxsettle_landing_uv"] = int(row["n"] or 0)
    except Exception:  # noqa: BLE001 - 表态埋点/计数缺失时 KPI 主体照常出
        pass
    return out


def _marketing_funnel(days: int) -> dict[str, Any]:
    """自动营销漏斗：sent → clicked → returned（回访率）。直读 marketing_engine，缺表返回零不拖垮 KPI。
    挂进 growth_loops 后，16:20 的 LLM 报告 prompt 自动看到营销效果，AI 建议里能对营销做调优（诊断→行动闭环）。"""
    try:
        from . import marketing_engine
        return marketing_engine.marketing_funnel(days)
    except Exception:  # noqa: BLE001 - 营销模块未就绪/缺表时 KPI 主体照常出
        return {"sent": 0, "clicked": 0, "returned": 0, "ctr_pct": None, "return_pct": None}


def _recall_funnel(days: int) -> dict[str, Any]:
    """召回漏斗：delivered → clicked（CTR）。直读 recall_deliveries，失败返回空不拖垮 KPI。"""
    out: dict[str, Any] = {"delivered": 0, "clicked": 0, "ctr_pct": None}
    try:
        import sqlite3 as _sq
        from .recall_subscriptions import DB_PATH as _RECALL_DB
        since = (datetime.now(BJ_TZ) - timedelta(days=days)).astimezone(timezone.utc).isoformat()
        conn = _sq.connect(_RECALL_DB)
        try:
            conn.row_factory = _sq.Row
            row = conn.execute(
                "SELECT SUM(CASE WHEN status='sent' THEN 1 ELSE 0 END) AS d,"
                " SUM(CASE WHEN clicked_at IS NOT NULL THEN 1 ELSE 0 END) AS c"
                " FROM recall_deliveries WHERE created_at >= ?",
                (since,),
            ).fetchone()
            out["delivered"] = int(row["d"] or 0)
            out["clicked"] = int(row["c"] or 0)
            if out["delivered"]:
                out["ctr_pct"] = round(out["clicked"] / out["delivered"] * 100, 1)
        finally:
            conn.close()
    except Exception:  # noqa: BLE001 - 召回库缺表/未初始化时 KPI 主体照常出
        pass
    return out


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

    # —— 续费可见性（churn 止血的前提是 churn 可见）——
    def _exp_dt(u: dict):
        try:
            dt = datetime.fromisoformat(u["membership_expires_at"].replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except Exception:  # noqa: BLE001
            return None
    now_utc = now_bj.astimezone(timezone.utc)
    paid_expiring_7d = sum(
        1 for u in users if u["membership_source"] == "paid"
        and (_e := _exp_dt(u)) is not None and now_utc < _e <= now_utc + timedelta(days=7)
    )

    # —— 增长回路（传播 + 邀请质量 + 召回）——
    invited = [u for u in users if u["invited_by"] and u["created_day"] in new_by_day]
    invited_d1 = 0
    for u in invited:
        nxt = (datetime.strptime(u["created_day"], "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
        if nxt <= today and nxt in actor_days.get(f"u:{u['id']}", set()):
            invited_d1 += 1

    return {
        "generated_at": now_bj.isoformat(),
        "window_days": days,
        "users": {"total": total_users, "new_today": new_by_day.get(today, 0), "new_7d": new_7d,
                  "new_by_day": [{"day": d, "n": new_by_day[d]} for d in day_list]},
        "dau": {"today": dau_today, "wau": wau, "series": dau_series},
        "retention": {"d1": retention_d1, "d7": retention_d7},
        "monetization": {"paid_members": paid, "paid_rate_pct": paid_rate,
                         "paid_expiring_7d": paid_expiring_7d,
                         "funnel": {
                             "visitors": funnel.get("pageview", 0),
                             "invite_click": funnel.get("invite_click", 0),
                             "claim_trial": funnel.get("claim_trial", 0),
                             "open_buy": funnel.get("open_buy", 0),
                             "buy_contact": funnel.get("buy_contact", 0),
                         }},
        "growth_loops": {
            "spread": _spread_funnel(days),                       # 复制→落地（全站最大自然分发行为）
            "invited": {"signups": len(invited), "d1_returned": invited_d1},  # 邀请质量（激活口径的地基）
            "recall": _recall_funnel(days),                       # 召回 delivered→CTR
            "calls": _calls_funnel(days),                         # 战绩闭环:表态→兑现推送→回站(P0 灰度)
            "marketing": _marketing_funnel(days),                 # 自动营销:分群召回 sent→clicked→回访(默认关,灰度)
        },
    }


# ---------------------------------------------------------------- cohort 每日快照（数据地基）

def snapshot_cohort_today() -> None:
    """每日物化「注册日 cohort × 今日活跃」快照。

    activity_log 滚动只留 10 万条——不落表，三个月后留存/归因数据系统性失真（「留存涨了」
    可能只是旧行被裁掉的假象）。每天一行 JSON，保留 400 天，任何时候都能重建留存矩阵。"""
    day = datetime.now(BJ_TZ).strftime("%Y-%m-%d")
    try:
        users = _user_rows()
        actor_days = _activity_days_by_actor(2)  # 只需要"今天"，扫近 2 天足够
        cutoff = (datetime.now(BJ_TZ) - timedelta(days=35)).strftime("%Y-%m-%d")
        cohorts: dict[str, dict[str, int]] = {}
        for u in users:
            cd = u["created_day"]
            if not cd or cd < cutoff:
                continue
            c = cohorts.setdefault(cd, {"size": 0, "active_today": 0})
            c["size"] += 1
            if day in actor_days.get(f"u:{u['id']}", set()):
                c["active_today"] += 1
        with _metrics_connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS growth_cohort_daily (
                    day TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT OR REPLACE INTO growth_cohort_daily (day, snapshot_json, created_at) VALUES (?,?,?)",
                (day, json.dumps(cohorts, ensure_ascii=False), datetime.now(BJ_TZ).isoformat()),
            )
            conn.execute(
                "DELETE FROM growth_cohort_daily WHERE day NOT IN "
                "(SELECT day FROM growth_cohort_daily ORDER BY day DESC LIMIT 400)"
            )
            conn.commit()
    except Exception as exc:  # noqa: BLE001 - 快照失败不拖垮日报
        logger.warning("cohort 快照落库失败：%s", exc)


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
    snapshot_cohort_today()  # 数据地基：搭日报班车每日物化 cohort，防 activity_log 滚动裁剪导致留存失真
    save_report(day, provider, kpis, report)
    return {"day": day, "provider": provider, "kpis": kpis, "report": report,
            "generated_at": datetime.now(BJ_TZ).isoformat()}
