"""邀请奖励活动：有效邀请判定（IP 去重 + 激活）+ 卡折算 + 自助兑换台账。

规则（里程碑阶梯，见 REG_MILESTONES / PAID_MILESTONES）：
- 有效注册邀请 N_reg = 我邀请的 ∧ 注册 IP 在我名下唯一 ∧ 已激活（有操作活动，终端每次加载都打 pageview）。
- 有效付费邀请 N_paid = 被邀请人 membership_source=paid ∧ 尊享/永久。
- 注册阶梯：1→3天卡、3→周卡、8→月卡、16→季卡、30→年卡（累计达阈值解锁对应卡，取每档最高已达）。
- 付费阶梯：2→季卡、4→年卡（权重更高）。⭐已取消「永久会员」档。
- 可兑换 = 赚得 − 已兑换（台账 granted/pending）。
- 兑换：防刷通过即自动顺延会员(source=reward)+台账 granted；可疑（同 IP 多号聚集）→ pending + 转人工。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import DateTime, Integer, String, func, select
from sqlalchemy.orm import Mapped, mapped_column

from . import auth, data_store, metrics_store, support_store
from .storage import Base, session_scope

CARD_DAYS = {"day3": 3, "week": 7, "month": 31, "quarter": 93, "year": 365, "lifetime": 0}
CARD_LABEL = {"day3": "3天卡", "week": "周卡", "month": "月卡", "quarter": "季卡", "year": "年卡", "lifetime": "永久会员"}
# 各卡「价值」(对标购买页原价 orig)：用于「免费领价值¥X」话术。年卡 orig=698（与 payment_config 一致）。
CARD_VALUE = {"day3": 0, "week": 28, "month": 68, "quarter": 198, "year": 698, "lifetime": 1998}

# 上瘾化里程碑：累计有效邀请达阈值即一次性解锁对应卡。首邀立得(留钩子) + 上调难度 + 取消「永久会员」档(控成本)。
# ⭐年卡门槛=邀满 30 位有效好友（要调送卡成本只改这里）；不再有 lifetime 档。
REG_MILESTONES = [(1, "day3"), (3, "week"), (8, "month"), (16, "quarter"), (30, "year")]
PAID_MILESTONES = [(2, "quarter"), (4, "year")]  # 付费邀请权重更高（拉来 4 个付费才得年卡）；取消 lifetime
# 主推大奖：年卡（¥698），整个活动以「解锁年卡」为核心目标
HERO_CARD = "year"
# 等级头衔（按得分 = 有效注册 + 付费×3）：有面子、不想掉
LEVELS = [(0, "青铜"), (2, "白银"), (5, "黄金"), (10, "铂金"), (20, "钻石"), (40, "王者")]
WEEK_TARGET = 3  # 每周目标邀请数（限时紧迫位）


class ReferralReward(Base):
    """邀请奖励兑换台账：每兑换一张卡落一行（防重复计数 + 看板审计 + 人工队列）。"""

    __tablename__ = "referral_rewards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    inviter_id: Mapped[str] = mapped_column(String(64), index=True)
    inviter_name: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    card_type: Mapped[str] = mapped_column(String(16))                 # month / quarter / year
    days: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="granted")  # granted / pending / rejected
    channel: Mapped[str] = mapped_column(String(16), default="self")    # self / admin
    note: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


_BJ_TZ = timezone(timedelta(hours=8))


def _activity_last_seen() -> dict[str, str]:
    """每个登录账号最近活跃时间(ISO)，键=去掉 u: 前缀的 user id。失败返回空 dict（绝不阻断计算）。"""
    try:
        act = metrics_store.member_activity_windows() or {}
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for key, val in act.items():
        if str(key).startswith("u:"):
            ls = (val or {}).get("last_seen") or ""
            if ls:
                out[str(key)[2:]] = ls
    return out


def _bj_date(dt_or_iso: Any) -> Optional[str]:
    try:
        if isinstance(dt_or_iso, str):
            d = datetime.fromisoformat(dt_or_iso.strip().replace("Z", "+00:00"))
        else:
            d = dt_or_iso
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.astimezone(_BJ_TZ).strftime("%Y-%m-%d")
    except Exception:  # noqa: BLE001
        return None


def _returned_later(created_at: Any, last_seen: Optional[str]) -> bool:
    """⭐真激活信号：被邀请人在注册【次日及以后】还回访过(最近活跃日 > 注册日)——
    杜绝「注册即走」的凑人头水号；只有真正回来用的才算有效邀请。"""
    if not last_seen:
        return False
    cd = _bj_date(created_at)
    ld = _bj_date(last_seen)
    return bool(cd and ld and ld > cd)


def _is_paid_member(user: "auth.User") -> bool:
    if (getattr(user, "membership_source", None) or "").lower() != "paid":
        return False
    return auth.membership_of(user).get("tier") in ("premium", "lifetime")


def _gather(user_id: str) -> Optional[dict]:
    """拉某邀请人的全部被邀请人，判定有效注册/付费 + IP 去重 + 聚集红旗。"""
    last_seen_map = _activity_last_seen()
    with session_scope() as session:
        me = session.get(auth.User, user_id)
        if me is None:
            return None
        invitees = session.scalars(
            select(auth.User).where(auth.User.invited_by == user_id).order_by(auth.User.created_at.desc())
        ).all()
        # 本周(北京时间周一 0 点起)邀请数——限时紧迫位
        cn_now = datetime.now(timezone(timedelta(hours=8)))
        week_start = (cn_now - timedelta(days=cn_now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
        seen_ip: dict[str, int] = {}
        items: list[dict] = []
        n_reg = 0
        n_paid = 0
        week_count = 0
        for u in invitees:
            ip = (u.registered_ip or "").strip()
            dup = bool(ip) and ip in seen_ip
            if ip:
                seen_ip[ip] = seen_ip.get(ip, 0) + 1
            is_act = _returned_later(u.created_at, last_seen_map.get(u.id))
            is_paid = _is_paid_member(u)
            qualified_reg = (not dup) and is_act      # 有效注册：IP 唯一 ∧ 注册后次日及以后还回访过(真激活)
            if qualified_reg:
                n_reg += 1
            if is_paid:
                n_paid += 1
            if u.created_at is not None:
                ca = u.created_at if u.created_at.tzinfo else u.created_at.replace(tzinfo=timezone.utc)
                if ca.astimezone(timezone(timedelta(hours=8))) >= week_start:
                    week_count += 1
            items.append({
                "username": u.username,
                "created_at": u.created_at.isoformat() if u.created_at else "",
                "status": "paid" if is_paid else ("activated" if is_act else "registered"),
                "qualified": qualified_reg,
                "dup_ip": dup,
            })
        clustered = sum(c for c in seen_ip.values() if c >= 2)  # 同 IP ≥2 个号的总数
        total = len(invitees)
        suspicious = clustered >= 3 or (total > 0 and clustered / total > 0.5)
        days_left_in_week = 7 - cn_now.weekday() - (1 if cn_now.hour or cn_now.minute else 0)
        return {
            "code": me.invite_code or "", "inviter_name": me.username,
            "invited_total": total, "n_reg": n_reg, "n_paid": n_paid,
            "week_count": week_count, "week_days_left": max(0, days_left_in_week),
            "items": items, "clustered": clustered, "suspicious": suspicious,
        }


def _earned(n_reg: int, n_paid: int) -> dict[str, int]:
    """里程碑制：每达到一个阈值，对应卡 +1（一次性）。注册档与付费档各自累计。"""
    out = {k: 0 for k in CARD_DAYS}
    for th, card in REG_MILESTONES:
        if n_reg >= th:
            out[card] += 1
    for th, card in PAID_MILESTONES:
        if n_paid >= th:
            out[card] += 1
    return out


def _redeemed(user_id: str) -> dict[str, int]:
    out = {k: 0 for k in CARD_DAYS}
    with session_scope() as session:
        rows = session.execute(
            select(ReferralReward.card_type, func.count())
            .where(ReferralReward.inviter_id == user_id, ReferralReward.status.in_(("granted", "pending")))
            .group_by(ReferralReward.card_type)
        ).all()
    for card_type, cnt in rows:
        if card_type in out:
            out[card_type] = int(cnt)
    return out


def _level_of(score: int) -> dict:
    """按得分定等级 + 下一级还差多少（不想掉的状态感）。"""
    cur = LEVELS[0]
    nxt = None
    for i, (th, name) in enumerate(LEVELS):
        if score >= th:
            cur = (th, name)
            nxt = LEVELS[i + 1] if i + 1 < len(LEVELS) else None
    return {
        "name": cur[1],
        "score": score,
        "next_name": nxt[1] if nxt else None,
        "to_next": (nxt[0] - score) if nxt else 0,
        "next_at": nxt[0] if nxt else None,
    }


def _next_milestone(n_reg: int) -> Optional[dict]:
    """下一个未解锁的注册里程碑（近在咫尺驱动）。"""
    for th, card in REG_MILESTONES:
        if n_reg < th:
            return {"at": th, "remaining": th - n_reg, "card": card,
                    "card_label": CARD_LABEL[card], "card_value": CARD_VALUE.get(card, 0)}
    return None


def _milestone_ladder(n_reg: int) -> list[dict]:
    return [{"at": th, "card": card, "card_label": CARD_LABEL[card],
             "card_value": CARD_VALUE.get(card, 0), "reached": n_reg >= th}
            for th, card in REG_MILESTONES]


def _hero_progress(n_reg: int) -> dict:
    """主推大奖（年卡 ¥698）的进度：解锁门槛 / 还差几人 / 完成度 / 是否已到手。
    已解锁年卡后，hero 顺延为更大的永久会员（持续给目标）。"""
    hero_at = next((th for th, card in REG_MILESTONES if card == HERO_CARD), 10)
    card = HERO_CARD
    if n_reg >= hero_at:  # 已拿到年卡 → 把目标顺延到永久会员（封顶则维持年卡达成态）
        nxt = next(((th, c) for th, c in REG_MILESTONES if th > hero_at), None)
        if nxt:
            hero_at, card = nxt[0], nxt[1]
    remaining = max(0, hero_at - n_reg)
    return {
        "card": card,
        "card_label": CARD_LABEL[card],
        "card_value": CARD_VALUE.get(card, 0),
        "at": hero_at,
        "reached": n_reg >= hero_at,
        "remaining": remaining,
        "percent": min(100, round(n_reg / hero_at * 100)) if hero_at else 100,
    }


def overview(user_id: str) -> Optional[dict]:
    g = _gather(user_id)
    if g is None:
        return None
    n_reg, n_paid = g["n_reg"], g["n_paid"]
    earned = _earned(n_reg, n_paid)
    redeemed = _redeemed(user_id)
    available = {k: max(0, earned[k] - redeemed.get(k, 0)) for k in earned}
    has_redeemable = any(v > 0 for v in available.values())
    return {
        "code": g["code"],
        "invited_total": g["invited_total"],
        "qualified_reg": n_reg,
        "qualified_paid": n_paid,
        "earned": earned,
        "redeemed": redeemed,
        "available": available,
        "has_redeemable": has_redeemable,
        "level": _level_of(n_reg + n_paid * 3),
        "next_milestone": _next_milestone(n_reg),
        "hero": _hero_progress(n_reg),  # 主推大奖（¥698年卡）进度
        "ladder": _milestone_ladder(n_reg),
        "weekly": {
            "count": g.get("week_count", 0),
            "target": WEEK_TARGET,
            "remaining": max(0, WEEK_TARGET - g.get("week_count", 0)),
            "days_left": g.get("week_days_left", 0),
        },
        # 被邀请人用户名脱敏（隐私：不向用户暴露他人完整账户名）
        "invited": [{**it, "username": _mask_name(it.get("username", ""))} for it in g["items"][:50]],
        "suspicious": g["suspicious"],
        "card_days": CARD_DAYS,
        "card_label": CARD_LABEL,
    }


def _activated_counts(*, start_q: Any = None, end_q: Any = None) -> dict[str, int]:
    """每个邀请人名下【已激活(注册次日及以后回访) + IP 去重】的有效邀请数。
    排行榜/冲榜赛排名都用它——**奖励以激活为准**，杜绝注册即走的凑人头刷榜。可选时间窗(冲榜赛用)。"""
    last_seen = _activity_last_seen()
    out: dict[str, int] = {}
    seen_ip: dict[str, set] = {}
    with session_scope() as session:
        q = select(auth.User.invited_by, auth.User.id, auth.User.created_at, auth.User.registered_ip).where(
            auth.User.invited_by.isnot(None)
        )
        if start_q is not None:
            q = q.where(auth.User.created_at >= start_q)
        if end_q is not None:
            q = q.where(auth.User.created_at <= end_q)
        rows = session.execute(q).all()
    for inviter, uid, created_at, ip in rows:
        if not _returned_later(created_at, last_seen.get(uid)):
            continue
        ipk = (ip or "").strip()
        if ipk:
            s = seen_ip.setdefault(inviter, set())
            if ipk in s:  # 同邀请人名下重复 IP 不重复计(与有效邀请同口径防刷)
                continue
            s.add(ipk)
        out[inviter] = out.get(inviter, 0) + 1
    return out


def _rank_names(session, ids: list[str]) -> dict[str, str]:
    names: dict[str, str] = {}
    if ids:
        for u in session.scalars(select(auth.User).where(auth.User.id.in_(ids))):
            names[u.id] = u.username
    return names


def leaderboard(limit: int = 20) -> dict:
    """邀请排行榜（按【激活】有效邀请数排名，奖励以激活为准）。名字脱敏。"""
    ranked = sorted(_activated_counts().items(), key=lambda kv: kv[1], reverse=True)[:limit]
    with session_scope() as session:
        names = _rank_names(session, [iid for iid, _ in ranked])
    out = [{"rank": i + 1, "name": _mask_name(names.get(iid, "用户")), "count": cnt}
           for i, (iid, cnt) in enumerate(ranked)]
    return {"items": out, "generated_at": datetime.now(timezone.utc).isoformat()}


# --------------------------------------------------------------------------- #
# 限时邀请冲榜赛：第1名永久 / 2-3名年卡 / 前10名季卡（榜单只算活动期内的邀请）
# --------------------------------------------------------------------------- #
def _mask_name(name: str) -> str:
    name = name or "用户"
    if "@" in name:
        head = name.split("@")[0]
        return (head[:2] + "***") if len(head) > 2 else (head[:1] + "**")
    if len(name) <= 2:
        return name[:1] + "*"
    return name[:2] + "*" * (len(name) - 2)


def _prize_for_rank(rank: Optional[int]) -> Optional[dict]:
    if rank == 1:
        return {"tier": "lifetime", "label": "永久会员", "emoji": "👑"}
    if rank in (2, 3):
        return {"tier": "year", "label": "年卡", "emoji": "🥇"}
    if rank and 4 <= rank <= 10:
        return {"tier": "quarter", "label": "季卡", "emoji": "🎴"}
    return None


PRIZE_TIERS = [
    {"ranks": "第 1 名", "label": "永久会员", "emoji": "👑"},
    {"ranks": "第 2-3 名", "label": "年卡会员", "emoji": "🥇"},
    {"ranks": "第 4-10 名", "label": "季卡会员", "emoji": "🎴"},
]


def _campaign_cfg() -> dict:
    """活动配置（可用 env 覆盖；默认 2026-06-11 ~ 06-25，北京时间）。"""
    title = os.getenv("DEEPFOCUS_CAMPAIGN_TITLE", "邀请冲榜赛 · 第一名赢永久会员")
    start_s = os.getenv("DEEPFOCUS_CAMPAIGN_START", "2026-06-11")
    end_s = os.getenv("DEEPFOCUS_CAMPAIGN_END", "2026-06-25")
    cn = timezone(timedelta(hours=8))

    def _parse(s: str, end: bool) -> datetime:
        s = (s or "").strip()
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                d = datetime.strptime(s, fmt).replace(tzinfo=cn)
                if fmt == "%Y-%m-%d" and end:  # 只给日期的结束时间补到当天 23:59:59
                    d = d.replace(hour=23, minute=59, second=59)
                return d
            except ValueError:
                continue
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=cn)
    start_dt = _parse(start_s, False)
    end_dt = _parse(end_s, True)
    now = datetime.now(timezone.utc)
    return {
        "title": title, "start": start_s, "end": end_s,
        "start_dt": start_dt, "end_dt": end_dt,
        # 查询用：转裸 UTC，匹配 created_at 的裸 UTC 存储（否则按字面比会差 8 小时）
        "start_q": start_dt.astimezone(timezone.utc).replace(tzinfo=None),
        "end_q": end_dt.astimezone(timezone.utc).replace(tzinfo=None),
        "active": start_dt <= now <= end_dt,
        "upcoming": now < start_dt,
        "ended": now > end_dt,
    }


def campaign(user_id: Optional[str] = None, *, limit: int = 20) -> dict:
    """限时冲榜赛榜单（仅统计活动期内的邀请）+ 我的排名/当前可得奖/反超下一名所需。"""
    cfg = _campaign_cfg()
    now = datetime.now(timezone.utc)
    # 冲榜赛同样按【激活】计数(活动期内邀请、次日回访才算)
    ranked = sorted(_activated_counts(start_q=cfg["start_q"], end_q=cfg["end_q"]).items(),
                    key=lambda kv: kv[1], reverse=True)
    with session_scope() as session:
        names = _rank_names(session, [iid for iid, _ in ranked[:limit]])

    board = []
    for i, (iid, cnt) in enumerate(ranked[:limit]):
        rank = i + 1
        board.append({"rank": rank, "name": _mask_name(names.get(iid, "用户")), "count": cnt, "prize": _prize_for_rank(rank)})

    me = None
    if user_id:
        my_count = next((c for iid, c in ranked if iid == user_id), 0)
        higher = [c for _, c in ranked if c > my_count]
        my_rank = len(higher) + 1 if my_count > 0 else None
        to_next = (min(higher) - my_count + 1) if higher else 0  # 反超上一名所需
        tenth = ranked[9][1] if len(ranked) >= 10 else 0
        to_top10 = 0 if (my_rank and my_rank <= 10) else max(1, tenth - my_count + 1)
        me = {
            "count": my_count, "rank": my_rank,
            "prize": _prize_for_rank(my_rank) if my_rank else None,
            "to_next": to_next, "to_top10": to_top10,
        }

    return {
        "active": cfg["active"], "upcoming": cfg["upcoming"], "ended": cfg["ended"],
        "title": cfg["title"], "start": cfg["start"], "end": cfg["end"],
        "ends_in": max(0, int((cfg["end_dt"] - now).total_seconds())),
        "starts_in": max(0, int((cfg["start_dt"] - now).total_seconds())),
        "prizes": PRIZE_TIERS,
        "leaderboard": board,
        "me": me,
    }


_SETTLE_KIND = "campaign_settled"


def settle_campaign(confirm: bool = False, *, limit: int = 10) -> dict:
    """结算冲榜赛：取活动期内邀请数前 10，附每人有效邀请/防刷标记（管理员确认防刷）。
    confirm=False → 仅预览；confirm=True → 实际发奖(永久/年卡/季卡) + 私信通知 + 幂等防重发。"""
    cfg = _campaign_cfg()
    # 结算同样按【激活】排名(与榜单一致：奖励只发给真带来活跃用户的人)
    rows = sorted(_activated_counts(start_q=cfg["start_q"], end_q=cfg["end_q"]).items(),
                  key=lambda kv: kv[1], reverse=True)[:limit]
    with session_scope() as session:
        ids = [iid for iid, _ in rows]
        users = {u.id: u for u in session.scalars(select(auth.User).where(auth.User.id.in_(ids)))} if ids else {}

    winners: list[dict] = []
    for i, (iid, cnt) in enumerate(rows):
        rank = i + 1
        prize = _prize_for_rank(rank)
        if not prize:
            continue
        u = users.get(iid)
        uname = u.username if u else iid
        g = _gather(iid) or {}
        winners.append({
            "rank": rank, "user_id": iid, "username": uname, "count": int(cnt),
            "qualified_reg": g.get("n_reg", 0), "suspicious": g.get("suspicious", False),
            "prize": prize,
        })

    already = data_store.latest(_SETTLE_KIND, cfg["end"]) is not None
    if not confirm:
        return {"ok": True, "preview": True, "settled": already, "end": cfg["end"],
                "title": cfg["title"], "winners": winners,
                "note": "预览：confirm=true 才真正发奖。请先核对每人『有效邀请/可疑』再确认。"}
    if already:
        return {"ok": False, "error": "本期已结算，请勿重复发奖", "winners": winners}

    granted = []
    for w in winners:
        tier = w["prize"]["tier"]
        if tier == "lifetime":
            auth.grant_membership(w["username"], 0, permanent=True, source="reward")
        elif tier == "year":
            auth.grant_membership(w["username"], 365, source="reward")
        elif tier == "quarter":
            auth.grant_membership(w["username"], 93, source="reward")
        # 私信通知中奖
        try:
            support_store.add_message(
                w["user_id"], w["username"], "admin",
                f"🎉 恭喜！你在「{cfg['title']}」中获得第 {w['rank']} 名，"
                f"已为你开通【{w['prize']['label']}】，感谢你的拉新支持！",
            )
        except Exception:  # noqa: BLE001
            pass
        # 台账
        try:
            with session_scope() as session:
                session.add(ReferralReward(
                    id=uuid.uuid4().hex, inviter_id=w["user_id"], inviter_name=w["username"],
                    card_type=w["prize"]["tier"], days=0, status="granted", channel="campaign",
                    note=f"冲榜赛第{w['rank']}名 {w['prize']['label']}", created_at=datetime.now(timezone.utc),
                ))
        except Exception:  # noqa: BLE001
            pass
        granted.append({"rank": w["rank"], "username": w["username"], "prize": w["prize"]["label"]})

    data_store.record(_SETTLE_KIND, cfg["end"], {"winners": granted, "settled_at": datetime.now(timezone.utc).isoformat()})
    return {"ok": True, "preview": False, "granted": granted, "count": len(granted)}


def redeem(user_id: str, card_type: str) -> dict:
    card_type = (card_type or "").strip().lower()
    if card_type not in CARD_DAYS:
        return {"ok": False, "error": "无效的卡类型"}
    g = _gather(user_id)
    if g is None:
        return {"ok": False, "error": "用户不存在"}
    earned = _earned(g["n_reg"], g["n_paid"])
    redeemed = _redeemed(user_id)
    if max(0, earned[card_type] - redeemed.get(card_type, 0)) <= 0:
        return {"ok": False, "error": f"暂无可兑换的{CARD_LABEL[card_type]}"}
    days = CARD_DAYS[card_type]
    now = datetime.now(timezone.utc)
    # 可疑（同 IP 多号聚集）→ 落 pending、转人工，不自动发放
    if g["suspicious"]:
        with session_scope() as session:
            session.add(ReferralReward(
                id=uuid.uuid4().hex, inviter_id=user_id, inviter_name=g["inviter_name"],
                card_type=card_type, days=days, status="pending", channel="self",
                note="疑似同IP多号聚集，待人工复核", created_at=now,
            ))
        return {"ok": True, "status": "pending",
                "message": f"{CARD_LABEL[card_type]}兑换已提交，因风控需人工复核——请点头像→💬 联系管理员。"}
    # 干净 → 自助秒兑：会员顺延 + 台账 granted
    if card_type == "lifetime":
        mem = auth.grant_membership(g["inviter_name"], 0, permanent=True, source="reward")
        msg = f"🎉 已兑换{CARD_LABEL[card_type]}，恭喜解锁永久会员！"
    else:
        mem = auth.grant_membership(g["inviter_name"], days, source="reward")
        msg = f"已兑换{CARD_LABEL[card_type]}，会员顺延 {days} 天。"
    with session_scope() as session:
        session.add(ReferralReward(
            id=uuid.uuid4().hex, inviter_id=user_id, inviter_name=g["inviter_name"],
            card_type=card_type, days=days, status="granted", channel="self",
            note="自助兑换", created_at=now,
        ))
    return {"ok": True, "status": "granted", "days": days,
            "card_label": CARD_LABEL[card_type], "membership": mem, "message": msg}


def admin_referrals(limit: int = 50) -> dict:
    """看板：邀请活动汇总 + 待人工兑换队列 + 最近兑换台账。"""
    def _row(r: ReferralReward) -> dict:
        return {"inviter": r.inviter_name or r.inviter_id, "card": CARD_LABEL.get(r.card_type, r.card_type),
                "days": r.days, "status": r.status, "channel": r.channel, "note": r.note or "",
                "created_at": r.created_at.isoformat() if r.created_at else ""}
    with session_scope() as session:
        pend = session.scalars(
            select(ReferralReward).where(ReferralReward.status == "pending").order_by(ReferralReward.created_at.desc())
        ).all()
        recent = session.scalars(
            select(ReferralReward).order_by(ReferralReward.created_at.desc())
        ).all()
        granted_days = session.scalar(
            select(func.coalesce(func.sum(ReferralReward.days), 0)).where(ReferralReward.status == "granted")
        ) or 0
        paid_members = session.scalar(
            select(func.count()).select_from(auth.User)
            .where(func.lower(func.coalesce(auth.User.membership_source, "")) == "paid")
        ) or 0
    return {
        "pending": [_row(r) for r in pend[:limit]],
        "recent": [_row(r) for r in recent[:limit]],
        "granted_days_total": int(granted_days),
        "pending_count": len(pend),
        "paid_members": int(paid_members),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
