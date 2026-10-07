"""Protected Ops API for RWA evidence and GameFi strategy seasons."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_db, require_ops_key
from ....database.models.web3_products import (
    GamefiSeason,
    GamefiStrategyRun,
    RWAAsset,
    RwaEvidenceEvent,
)
from ...web3_products.gamefi import StrategyRun, rank_runs, score_run, snapshot_hash
from ...web3_products.rwa_evidence import EvidenceEvent, event_hash

router = APIRouter(dependencies=[Depends(require_ops_key)])


class RWAAssetCreate(BaseModel):
    asset_key: str = Field(..., min_length=1, max_length=255)
    asset_class: str = Field(..., min_length=1, max_length=64)
    jurisdiction: Optional[str] = Field(None, min_length=2, max_length=2)
    metadata: dict = Field(default_factory=dict)


class EvidenceCreate(BaseModel):
    evidence_type: str = Field(..., min_length=1, max_length=64)
    source_uri: str = Field(..., min_length=10, max_length=2048)
    content_hash: str = Field(..., min_length=64, max_length=64)
    observed_at: datetime
    signer: str = Field(..., min_length=1, max_length=255)
    payload: dict = Field(default_factory=dict)


class SeasonCreate(BaseModel):
    season_key: str = Field(..., min_length=1, max_length=128)
    title: str = Field(..., min_length=1, max_length=255)
    starts_at: datetime
    ends_at: datetime
    status: str = Field(default="draft", pattern="^(draft|open)$")
    scoring_config: dict = Field(default_factory=dict)


class StrategyRunCreate(BaseModel):
    strategy_key: str = Field(..., min_length=1, max_length=128)
    return_pct: Decimal
    max_drawdown_pct: Decimal
    win_rate_pct: Decimal
    completed_at: datetime


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@router.post("/rwa/assets", status_code=201)
async def create_rwa_asset(request: RWAAssetCreate, db: AsyncSession = Depends(get_db)):
    asset = RWAAsset(
        asset_key=request.asset_key,
        asset_class=request.asset_class,
        jurisdiction=request.jurisdiction.upper() if request.jurisdiction else None,
        status="draft",
        metadata_=request.metadata,
    )
    db.add(asset)
    try:
        await db.commit()
        await db.refresh(asset)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Asset could not be created: {exc}")
    return {"asset_id": str(asset.id), "asset_key": asset.asset_key, "status": asset.status}


@router.post("/rwa/assets/{asset_id}/evidence", status_code=201)
async def append_rwa_evidence(asset_id: str, request: EvidenceCreate, db: AsyncSession = Depends(get_db)):
    try:
        asset_uuid = uuid.UUID(asset_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid asset_id")
    asset = (await db.execute(select(RWAAsset).where(RWAAsset.id == asset_uuid))).scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=404, detail="RWA asset not found")
    previous = (await db.execute(
        select(RwaEvidenceEvent).where(RwaEvidenceEvent.asset_id == asset_uuid).order_by(desc(RwaEvidenceEvent.created_at)).limit(1)
    )).scalar_one_or_none()
    event = EvidenceEvent(
        asset_id=asset_id,
        evidence_type=request.evidence_type,
        source_uri=request.source_uri,
        content_hash=request.content_hash.lower(),
        observed_at=request.observed_at.isoformat(),
        signer=request.signer,
        payload=request.payload,
        previous_event_hash=previous.event_hash if previous else None,
    )
    try:
        digest = event_hash(event)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    stored = RwaEvidenceEvent(
        asset_id=asset_uuid,
        evidence_type=event.evidence_type,
        source_uri=event.source_uri,
        content_hash=event.content_hash,
        observed_at=request.observed_at,
        signer=event.signer,
        payload=event.payload,
        previous_event_hash=event.previous_event_hash,
        event_hash=digest,
        created_at=datetime.now(timezone.utc),
    )
    db.add(stored)
    await db.commit()
    return {"event_id": str(stored.id), "event_hash": digest, "previous_event_hash": event.previous_event_hash}


@router.get("/rwa/assets/{asset_id}/evidence")
async def list_rwa_evidence(asset_id: str, db: AsyncSession = Depends(get_db)):
    events = (await db.execute(
        select(RwaEvidenceEvent).where(RwaEvidenceEvent.asset_id == uuid.UUID(asset_id)).order_by(RwaEvidenceEvent.created_at)
    )).scalars().all()
    return {"asset_id": asset_id, "events": [
        {"event_id": str(row.id), "evidence_type": row.evidence_type, "source_uri": row.source_uri,
         "content_hash": row.content_hash, "observed_at": _iso(row.observed_at), "signer": row.signer,
         "previous_event_hash": row.previous_event_hash, "event_hash": row.event_hash}
        for row in events
    ]}


@router.post("/gamefi/seasons", status_code=201)
async def create_gamefi_season(request: SeasonCreate, db: AsyncSession = Depends(get_db)):
    if request.ends_at <= request.starts_at:
        raise HTTPException(status_code=422, detail="ends_at must be after starts_at")
    season = GamefiSeason(
        season_key=request.season_key, title=request.title, status=request.status,
        starts_at=request.starts_at, ends_at=request.ends_at,
        scoring_config=request.scoring_config,
        created_at=datetime.now(timezone.utc),
    )
    db.add(season)
    try:
        await db.commit()
        await db.refresh(season)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=409, detail=f"Season could not be created: {exc}")
    return {"season_id": str(season.id), "season_key": season.season_key, "status": season.status}


@router.post("/gamefi/seasons/{season_id}/runs", status_code=201)
async def record_gamefi_run(season_id: str, request: StrategyRunCreate, db: AsyncSession = Depends(get_db)):
    try:
        season_uuid = uuid.UUID(season_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid season_id")
    season = (await db.execute(select(GamefiSeason).where(GamefiSeason.id == season_uuid))).scalar_one_or_none()
    if season is None:
        raise HTTPException(status_code=404, detail="GameFi season not found")
    if season.status != "open":
        raise HTTPException(status_code=409, detail="GameFi season is not open")
    run = StrategyRun(
        strategy_id=request.strategy_key, return_pct=request.return_pct,
        max_drawdown_pct=request.max_drawdown_pct, win_rate_pct=request.win_rate_pct,
        completed_at=request.completed_at.isoformat(),
    )
    row = GamefiStrategyRun(
        season_id=season_uuid, strategy_key=run.strategy_id, snapshot_hash=snapshot_hash(run),
        return_pct=run.return_pct, max_drawdown_pct=run.max_drawdown_pct,
        win_rate_pct=run.win_rate_pct, score=score_run(run),
        completed_at=request.completed_at, created_at=datetime.now(timezone.utc),
    )
    db.add(row)
    await db.commit()
    return {"run_id": str(row.id), "strategy_key": row.strategy_key, "score": str(row.score), "snapshot_hash": row.snapshot_hash}


@router.get("/gamefi/seasons/{season_id}/leaderboard")
async def gamefi_leaderboard(season_id: str, db: AsyncSession = Depends(get_db)):
    rows = (await db.execute(
        select(GamefiStrategyRun).where(GamefiStrategyRun.season_id == uuid.UUID(season_id))
    )).scalars().all()
    runs = [StrategyRun(r.strategy_key, Decimal(str(r.return_pct)), Decimal(str(r.max_drawdown_pct)), Decimal(str(r.win_rate_pct)), r.completed_at.isoformat()) for r in rows]
    ranked = rank_runs(runs)
    return {"season_id": season_id, "leaderboard": [
        {"strategy_key": row.strategy_id, "score": str(row.score), "rank": row.rank, "snapshot_hash": row.snapshot_hash}
        for row in ranked
    ]}
