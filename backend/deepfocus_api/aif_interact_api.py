"""AI 模拟盘互动 API：冠军竞猜 / 关注 agent / 调仓时间线。全部要求登录态
（不在 auth.py 公开白名单 → 匿名被 AuthMiddleware 拦 401；路由内再用
require_current_user 兜底）。写入走 write_guard 限流（用户写入接口加固规范）。"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from . import aif_interact, write_guard
from .auth import require_current_user

logger = logging.getLogger("deepfocus.aif_interact_api")

router = APIRouter()

_GUESS_RATE_KIND = "aif_guess"   # 默认 20/60s，防脚本刷；产品层另有每日一注硬约束


def _db():
    return aif_interact.connect()


@router.get("/api/ai-fund/guess")
async def guess_summary(claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    uid = str(claims.get("sub") or "")
    with _db() as conn:
        aif_interact.settle_guesses(conn)
        return aif_interact.guess_summary(conn, uid)


@router.post("/api/ai-fund/guess")
async def post_guess(payload: dict[str, Any],
                     claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    uid = str(claims.get("sub") or "")
    if not uid:
        raise HTTPException(status_code=401, detail="请先登录")
    if not write_guard.check_rate(_GUESS_RATE_KIND, uid):
        raise HTTPException(status_code=429, detail="操作太频繁，稍后再试")
    fund_id = str((payload or {}).get("fund_id") or "").strip()
    with _db() as conn:
        try:
            return aif_interact.set_guess(conn, uid, fund_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/ai-fund/follow")
async def follow_list(claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    uid = str(claims.get("sub") or "")
    with _db() as conn:
        follows = aif_interact.list_follows(conn, uid)
        return {"follows": follows, "max": 3}


@router.post("/api/ai-fund/follow")
async def post_follow(payload: dict[str, Any],
                      claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    uid = str(claims.get("sub") or "")
    if not uid:
        raise HTTPException(status_code=401, detail="请先登录")
    if not write_guard.check_rate(_GUESS_RATE_KIND, uid):
        raise HTTPException(status_code=429, detail="操作太频繁，稍后再试")
    data = payload or {}
    fund_id = str(data.get("fund_id") or "").strip()
    on = bool(data.get("on", True))
    with _db() as conn:
        try:
            return aif_interact.set_follow(conn, uid, fund_id, on)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/api/ai-fund/follow/feed")
async def follow_feed_route(limit: int = 20,
                      claims: dict = Depends(require_current_user)) -> dict[str, Any]:
    uid = str(claims.get("sub") or "")
    with _db() as conn:
        items = aif_interact.follow_feed(conn, uid, limit)
        return {"items": items, "has_any": bool(items)}
