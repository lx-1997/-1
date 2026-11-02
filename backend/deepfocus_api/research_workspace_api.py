"""Authenticated, owner-only research workspace endpoints."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import require_current_user
from . import research_workspace as store

router = APIRouter(prefix="/api/research/projects", tags=["research-workspace"])


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    symbol: str = Field(min_length=1, max_length=12)
    market: str = Field(min_length=2, max_length=2)
    objective: str = Field(min_length=1, max_length=2000)
    notes: str = Field(default="", max_length=8000)
    hypotheses: list[str] = Field(default_factory=list)
    interval_seconds: int = 0


class ProjectUpdate(BaseModel):
    expected_version: int = Field(ge=1)
    notes: str = Field(default="", max_length=8000)
    hypotheses: list[str] = Field(default_factory=list)
    interval_seconds: int = 0


class RefreshRequest(BaseModel):
    request_key: str = Field(min_length=1, max_length=100)
    followup_question: str = Field(default="", max_length=2000)


def owner(user):
    key = str(user.get("sub") or "").strip()
    if not key:
        raise HTTPException(401, "请重新登录")
    return key


def call(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except LookupError:
        raise HTTPException(404, "研究项目不存在") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@router.get("")
def list_projects(user: dict = Depends(require_current_user)):
    return {"projects": call(store.list_projects, owner(user))}


@router.post("")
def create_project(body: ProjectCreate, user: dict = Depends(require_current_user)):
    return call(store.create_project, owner(user), **body.model_dump())


@router.get("/{project_id}")
def get_project(project_id: str, user: dict = Depends(require_current_user)):
    return call(store.get_project, owner(user), project_id)


@router.post("/{project_id}/settings")
def update_project(project_id: str, body: ProjectUpdate, user: dict = Depends(require_current_user)):
    return call(store.update_project, owner(user), project_id, **body.model_dump())


@router.get("/{project_id}/revisions")
def project_revisions(project_id: str, before: str, limit: int = Query(default=30, ge=1, le=50), user: dict = Depends(require_current_user)):
    return call(store.revision_page, owner(user), project_id, before, limit)


@router.post("/{project_id}/refresh", status_code=202)
def refresh_project(project_id: str, body: RefreshRequest, user: dict = Depends(require_current_user)):
    task_id = call(store.enqueue_refresh, owner(user), project_id, body.request_key, followup_question=body.followup_question)
    return {"task_id": task_id, "status": "accepted"}
