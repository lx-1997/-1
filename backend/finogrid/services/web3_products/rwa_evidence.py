"""Append-only, privacy-preserving RWA evidence events."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class EvidenceEvent:
    asset_id: str
    evidence_type: str
    source_uri: str
    content_hash: str
    observed_at: str
    signer: str
    payload: dict
    previous_event_hash: str | None = None


def validate_event(event: EvidenceEvent) -> None:
    if not event.asset_id.strip():
        raise ValueError("asset_id is required")
    if not event.evidence_type.strip():
        raise ValueError("evidence_type is required")
    if not (event.source_uri.startswith("https://") or event.source_uri.startswith("ipfs://")):
        raise ValueError("source_uri must use https:// or ipfs://")
    if not SHA256_RE.fullmatch(event.content_hash.lower()):
        raise ValueError("content_hash must be a lowercase SHA-256 hex digest")
    if not event.signer.strip():
        raise ValueError("signer is required")
    if event.previous_event_hash and not SHA256_RE.fullmatch(event.previous_event_hash.lower()):
        raise ValueError("previous_event_hash must be a SHA-256 hex digest")


def event_hash(event: EvidenceEvent) -> str:
    validate_event(event)
    canonical = json.dumps(asdict(event), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def verify_chain(events: list[tuple[EvidenceEvent, str]]) -> bool:
    previous: str | None = None
    for event, claimed_hash in events:
        if event.previous_event_hash != previous or event_hash(event) != claimed_hash:
            return False
        previous = claimed_hash
    return True
