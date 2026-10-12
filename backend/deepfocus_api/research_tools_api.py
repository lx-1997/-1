"""Authenticated investment research tools with bounded independent compute."""
from contextlib import asynccontextmanager
import asyncio
import time
from typing import Literal
from urllib.parse import quote
import uuid

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from .auth import require_current_user
from . import research_jobs
from .research_templates import research_template

Tool = Literal["performance", "portfolio", "financials", "institutions", "funds", "bonds", "factors"]


@asynccontextmanager
async def research_lifespan(_app):
    global _catalog_lock
    _catalog_lock = None
    research_jobs.manager.closing = False
    research_jobs.manager.slot = None
    try:
        yield
    finally:
        await research_jobs.manager.shutdown()


router = APIRouter(prefix="/api/research-tools", tags=["research"])


def attach_research_lifespan(app):
    """Explicitly compose lifecycle, including FastAPI versions without merging."""
    if getattr(app, "_research_lifespan_attached", False):
        return
    original = app.router.lifespan_context
    @asynccontextmanager
    async def composed(application):
        async with research_lifespan(application):
            async with original(application) as state:
                try:
                    yield state
                finally:
                    # Reap analysis before the main application's slower closers.
                    await research_jobs.manager.shutdown()
    app.router.lifespan_context = composed
    app._research_lifespan_attached = True


class ResearchRequest(BaseModel):
    tool: Tool
    input: dict
    request_id: str = Field(default_factory=lambda: uuid.uuid4().hex, min_length=8, max_length=96)


def _owner(user):
    owner = user.get("sub")
    if not isinstance(owner, str) or not owner:
        raise HTTPException(status_code=401, detail="请先登录账号")
    return owner


def _failure(error):
    return HTTPException(status_code=error.status, detail=str(error), headers={"Cache-Control": "no-store"})


_catalog_cache = None
_catalog_time = 0.0
_catalog_lock = None


@router.get("/catalog")
async def get_catalog(response: Response, user: dict = Depends(require_current_user)):
    _owner(user)
    response.headers["Cache-Control"] = "no-store"
    global _catalog_cache, _catalog_time, _catalog_lock
    if _catalog_lock is None:
        _catalog_lock = asyncio.Lock()
    async with _catalog_lock:
        if _catalog_cache is None or time.monotonic() - _catalog_time > 60:
            try:
                _catalog_cache = await research_jobs.run_worker({"operation": "catalog"}, timeout=8)
                _catalog_time = time.monotonic()
            except research_jobs.ResearchJobError as error:
                raise _failure(error)
    return _catalog_cache


@router.get("/templates/{tool}")
async def get_template(tool: Tool, user: dict = Depends(require_current_user)):
    _owner(user)
    name = quote("daocaijing-虚构演示-" + tool + ".csv")
    return Response(research_template(tool), media_type="text/csv; charset=utf-8", headers={
        "Cache-Control": "no-store", "Content-Disposition": "attachment; filename*=UTF-8''" + name,
    })


@router.post("/jobs", status_code=202)
async def create_job(payload: ResearchRequest, response: Response, user: dict = Depends(require_current_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await research_jobs.manager.create(_owner(user), payload.tool, payload.input, payload.request_id)
    except research_jobs.ResearchJobError as error:
        raise _failure(error)


@router.get("/jobs/{identifier}")
async def get_job(identifier: str, response: Response, user: dict = Depends(require_current_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return research_jobs.manager.get(identifier, _owner(user)).public()
    except research_jobs.ResearchJobError as error:
        raise _failure(error)


@router.delete("/jobs/{identifier}")
async def cancel_job(identifier: str, response: Response, user: dict = Depends(require_current_user)):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await research_jobs.manager.delete(identifier, _owner(user))
    except research_jobs.ResearchJobError as error:
        raise _failure(error)
