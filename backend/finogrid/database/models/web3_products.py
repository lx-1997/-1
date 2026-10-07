"""Database models for the non-custodial RWA and GameFi product layer."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, JSON
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, TimestampMixin, UUIDMixin


class RWAAsset(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "rwa_assets"

    asset_key: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    asset_class: Mapped[str] = mapped_column(String(64), nullable=False)
    issuer_client_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("clients.id", ondelete="SET NULL"))
    jurisdiction: Mapped[Optional[str]] = mapped_column(String(2))
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)


class RwaEvidenceEvent(Base, UUIDMixin):
    __tablename__ = "rwa_evidence_events"

    asset_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("rwa_assets.id", ondelete="RESTRICT"), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(64), nullable=False)
    source_uri: Mapped[str] = mapped_column(String(2048), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    signer: Mapped[str] = mapped_column(String(255), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    previous_event_hash: Mapped[Optional[str]] = mapped_column(String(64))
    event_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GamefiSeason(Base, UUIDMixin):
    __tablename__ = "gamefi_seasons"

    season_key: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(32), default="draft", nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scoring_config: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GamefiStrategyRun(Base, UUIDMixin):
    __tablename__ = "gamefi_strategy_runs"

    season_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("gamefi_seasons.id", ondelete="CASCADE"), nullable=False)
    strategy_key: Mapped[str] = mapped_column(String(128), nullable=False)
    snapshot_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    return_pct: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    max_drawdown_pct: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    win_rate_pct: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    score: Mapped[float] = mapped_column(Numeric(18, 8), nullable=False)
    rank: Mapped[Optional[int]] = mapped_column(Integer)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
