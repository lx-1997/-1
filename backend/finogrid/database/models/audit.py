"""Audit log model — append-only compliance and governance trail."""
from __future__ import annotations

import uuid
from typing import Optional

from sqlalchemy import String, ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin, UUIDMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    """Append-only audit log for compliance, governance, and regulatory reporting."""

    __tablename__ = "audit_logs"

    # Actor
    actor_type: Mapped[str] = mapped_column(String(64), nullable=False)  # client|agent|system|operator
    actor_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Action
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    resource_type: Mapped[Optional[str]] = mapped_column(String(64))
    resource_id: Mapped[Optional[str]] = mapped_column(String(255))

    # Context
    corridor_code: Mapped[Optional[str]] = mapped_column(String(2))
    client_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    batch_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True))

    # Data
    before_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    after_state: Mapped[Optional[dict]] = mapped_column(JSONB)
    detail: Mapped[Optional[str]] = mapped_column(Text)
    ip_address: Mapped[Optional[str]] = mapped_column(String(45))
    request_id: Mapped[Optional[str]] = mapped_column(String(255))
