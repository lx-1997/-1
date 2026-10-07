"""Deterministic scoring for the research strategy arena.

Scores rank simulated strategies and do not represent a promise of investment
returns. The snapshot hash lets the UI and an auditor reproduce a leaderboard.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


@dataclass(frozen=True)
class StrategyRun:
    strategy_id: str
    return_pct: Decimal
    max_drawdown_pct: Decimal
    win_rate_pct: Decimal
    completed_at: str


@dataclass(frozen=True)
class RankedStrategy:
    strategy_id: str
    score: Decimal
    rank: int
    snapshot_hash: str


def _non_negative(value: Decimal) -> Decimal:
    return max(Decimal("0"), value)


def score_run(run: StrategyRun) -> Decimal:
    """Score a run with return, drawdown and win-rate components."""
    score = (
        run.return_pct
        - Decimal("0.50") * _non_negative(run.max_drawdown_pct)
        + Decimal("0.20") * run.win_rate_pct
    )
    return score.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)


def snapshot_hash(run: StrategyRun) -> str:
    payload = json.dumps(
        {key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(run).items()},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def rank_runs(runs: Iterable[StrategyRun]) -> list[RankedStrategy]:
    scored = [(run, score_run(run)) for run in runs]
    scored.sort(key=lambda item: (-item[1], item[0].max_drawdown_pct, item[0].strategy_id))
    return [
        RankedStrategy(
            strategy_id=run.strategy_id,
            score=score,
            rank=index,
            snapshot_hash=snapshot_hash(run),
        )
        for index, (run, score) in enumerate(scored, start=1)
    ]
