"""故事（事件簇）API：今日看板、个人化推荐、故事详情、标的下钻、埋点、显式画像。

路由分两类：
- **读**（今日/详情/标的/大众简报）：``optional_current_user``——匿名也能看公共故事，
  登录则叠加个人化排序。对应端点需在 nginx 加 ``df_web_ok`` 豁免（见部署备注）。
- **写**（埋点事件、显式画像）：埋点走 ``write_guard.check_rate`` 限流；画像读写要求登录态。

隐私：``/api/stories/me`` 的个人化结果只回给当前账号；``/api/stories/today`` 与大众简报
一律用 ``story_engine`` 的公共 mass 排序，不含任何账号数据。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from . import story_engine, story_events, user_profile, write_guard
from .auth import optional_current_user, require_current_user

logger = logging.getLogger("deepfocus.stories_api")

router = APIRouter()

_EVENT_RATE_KIND = "story_event"   # 未在 write_guard._LIMITS 里 → 走默认 20/60；够用且防脚本刷


def _uid(claims: Optional[dict]) -> str:
    return str((claims or {}).get("sub") or "").strip()


def _actor(claims: Optional[dict], request: Request) -> str:
    return _uid(claims) or (request.client.host if request.client else "anon")


# ---------------------------------------------------------------------------
# 读：今日看板 / 个人化 / 详情 / 标的
# ---------------------------------------------------------------------------

@router.get("/api/stories/today")
async def stories_today(
    request: Request,
    limit: int = 20,
    window_hours: int = story_engine.WINDOW_HOURS_DEFAULT,
    kind: str = "",
) -> dict[str, Any]:
    """今日看板：大众重要度排序的故事 + 大众简报 + 引擎健康度。匿名可访问。

    登录用户此处仍返回**公共**排序（今日看板是所有人一致的「大众 faces」），
    个人化推荐走 ``/api/stories/me``，两者职责分离，避免公共看板被个人兴趣污染。
    """
    limit = max(1, min(int(limit or 20), 100))
    cards = story_engine.list_stories(limit=limit, window_hours=window_hours, kind=kind, with_items=3)
    # 简报取更宽的候选池（不受看板 limit 限制），保证主线/公司/风险三区都有料。
    brief_cards = story_engine.list_stories(limit=60, window_hours=window_hours, with_items=3)
    brief = story_engine.build_mass_brief(brief_cards, window_hours=window_hours)
    return {
        "stories": cards,
        "brief": brief,
        "health": story_engine.story_health(),
    }


@router.get("/api/stories/me")
async def stories_me(
    request: Request,
    limit: int = 20,
    window_hours: int = story_engine.WINDOW_HOURS_DEFAULT,
    claims: Optional[dict] = Depends(optional_current_user),
) -> dict[str, Any]:
    """千人千面：按账号画像 + 隐式兴趣重排故事，每张卡带 ``why_personal`` 可解释标签。

    匿名（未登录）→ ``personalized=false``，退回大众 mass 排序，前端据此引导登录。
    """
    limit = max(1, min(int(limit or 20), 100))
    uid = _uid(claims)
    # 取候选池比展示数大，给个人化重排与探索位留出腾挪空间。
    pool = story_engine.list_stories(limit=min(100, limit * 4), window_hours=window_hours, with_items=3)
    if not uid:
        return {"personalized": False, "stories": pool[:limit]}
    ranked = user_profile.rank_stories(uid, pool, limit=limit)
    return {"personalized": True, "stories": ranked}


@router.get("/api/stories/brief")
async def stories_brief(window_hours: int = story_engine.WINDOW_HOURS_DEFAULT, fmt: str = "json") -> Any:
    """大众简报（公共资产，无账号数据）。``fmt=text`` 返回可直接推送/贴晨报的纯文本。"""
    cards = story_engine.list_stories(limit=60, window_hours=window_hours, with_items=3)
    brief = story_engine.build_mass_brief(cards, window_hours=window_hours)
    if fmt == "text":
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(story_engine.render_brief_text(brief))
    return brief


@router.get("/api/stories/symbol/{symbol}")
async def stories_by_symbol(symbol: str, days: int = 30, limit: int = 30) -> dict[str, Any]:
    """标的下钻：某只票近 N 天的故事时间线（工作区/个股页用）。匿名可访问。"""
    symbol = (symbol or "").strip().upper()[:20]
    if not symbol:
        raise HTTPException(status_code=400, detail="缺少标的代码")
    stories = story_engine.stories_for_symbol(symbol, days=max(1, min(int(days or 30), 90)),
                                              limit=max(1, min(int(limit or 30), 100)))
    return {"symbol": symbol, "stories": stories}


@router.get("/api/stories/{story_id}")
async def story_detail(story_id: str, claims: Optional[dict] = Depends(optional_current_user)) -> dict[str, Any]:
    """故事详情：卡片 + 分区成员内容（事实/观点/风险/背景）。匿名可访问。"""
    story_id = (story_id or "").strip()[:80]
    card = story_engine.story_detail(story_id)
    if card is None:
        raise HTTPException(status_code=404, detail="故事不存在或已过期")
    return card


# ---------------------------------------------------------------------------
# 写：埋点事件 / 显式画像
# ---------------------------------------------------------------------------

@router.post("/api/stories/event")
async def log_story_event(request: Request, claims: Optional[dict] = Depends(optional_current_user)) -> dict[str, Any]:
    """记录一次故事交互（曝光/点击/停留/收藏/关注/反馈）。登录可选，写侧限流防脚本刷。"""
    if not write_guard.check_rate(_EVENT_RATE_KIND, _actor(claims, request)):
        raise HTTPException(status_code=429, detail="操作太频繁，请稍后再试")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    action = str(body.get("action") or "").strip()
    story_id = str(body.get("story_id") or "").strip()
    if not action or not story_id:
        raise HTTPException(status_code=400, detail="缺少 action 或 story_id")
    themes = body.get("themes") or []
    if not isinstance(themes, list):
        themes = []
    ok = story_events.log_event(
        user_id=_uid(claims),
        story_id=story_id,
        action=action,
        content_id=str(body.get("content_id") or ""),
        surface=str(body.get("surface") or ""),
        position=int(body.get("position") if body.get("position") is not None else -1),
        dwell_ms=int(body.get("dwell_ms") or 0),
        score=float(body.get("score") or 0.0),
        symbol=str(body.get("symbol") or ""),
        kind=str(body.get("kind") or ""),
        themes=[str(t) for t in themes],
    )
    if not ok:
        raise HTTPException(status_code=400, detail="非法的 action 或匿名不可写此类事件")
    return {"ok": True}


@router.get("/api/profile")
async def get_my_profile(claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    """我的显式画像（登录态）。"""
    return user_profile.get_profile(_uid(claims))


@router.put("/api/profile")
async def put_my_profile(request: Request, claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    """保存我的显式画像（登录态，整表覆盖）。返回归一后的结果。"""
    uid = _uid(claims)
    if not uid:
        raise HTTPException(status_code=401, detail="请重新登录")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="请求体不是合法 JSON")
    try:
        saved = user_profile.set_profile(
            uid,
            markets=body.get("markets"),
            horizon=body.get("horizon"),
            risk=body.get("risk"),
            theme_follows=body.get("theme_follows"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return saved


# ---------------------------------------------------------------------------
# 管理：埋点质量评估（位置 CTR）
# ---------------------------------------------------------------------------

@router.get("/api/stories/admin/ctr")
async def admin_ctr(request: Request, surface: str = "", since_days: int = 14,
                    claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    """按展示位置统计曝光→点击转化率，评估个人化排序质量（登录态；生产可再收紧到管理员）。"""
    rows = story_events.ctr_by_position(surface=surface, since_days=max(1, min(int(since_days or 14), 90)))
    return {"surface": surface, "since_days": since_days, "positions": rows}
