from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from .shared_utils import (
    dedupe,
    profit_factor,
    safe_float,
    safe_int,
    utc_now_iso,
)

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_BACKTEST_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".backtest_engine.sqlite3"),
    )
)

_BACKTEST_FIELDS = [
    "id", "name", "market", "strategy_type", "symbols_json",
    "start_date", "end_date", "initial_capital", "benchmark",
    "parameters_json", "status", "progress",
    "result_json", "error",
    "created_at", "updated_at", "completed_at",
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_backtest_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS backtests (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'US',
                strategy_type TEXT NOT NULL DEFAULT 'momentum',
                symbols_json TEXT NOT NULL DEFAULT '[]',
                start_date TEXT NOT NULL DEFAULT '',
                end_date TEXT NOT NULL DEFAULT '',
                initial_capital REAL NOT NULL DEFAULT 100000,
                benchmark TEXT DEFAULT 'SPY',
                parameters_json TEXT NOT NULL DEFAULT '{}',
                status TEXT NOT NULL DEFAULT 'pending',
                progress INTEGER NOT NULL DEFAULT 0,
                result_json TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_backtests_status ON backtests(status)"
        )
        conn.commit()


def create_backtest(
    name: str,
    market: str = "US",
    strategy_type: str = "momentum",
    symbols: Optional[list[str]] = None,
    start_date: str = "",
    end_date: str = "",
    initial_capital: float = 100000,
    benchmark: str = "SPY",
    parameters: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    init_backtest_db()
    now = utc_now_iso()
    bt_id = str(uuid.uuid4())

    if not start_date:
        start_date = (datetime.utcnow() - timedelta(days=365)).strftime("%Y-%m-%d")
    if not end_date:
        end_date = datetime.utcnow().strftime("%Y-%m-%d")

    record = {
        "id": bt_id,
        "name": name,
        "market": market,
        "strategy_type": strategy_type,
        "symbols_json": json.dumps(symbols or [], ensure_ascii=False),
        "start_date": start_date,
        "end_date": end_date,
        "initial_capital": initial_capital,
        "benchmark": benchmark,
        "parameters_json": json.dumps(parameters or {}, ensure_ascii=False),
        "status": "pending",
        "progress": 0,
        "result_json": None,
        "error": None,
        "created_at": now,
        "updated_at": now,
        "completed_at": None,
    }
    with _connect() as conn:
        placeholders = ", ".join([f":{f}" for f in _BACKTEST_FIELDS])
        conn.execute(
            f"INSERT INTO backtests ({', '.join(_BACKTEST_FIELDS)}) VALUES ({placeholders})",
            record,
        )
        conn.commit()
    return get_backtest(bt_id) or record


def get_backtest(backtest_id: str) -> Optional[dict[str, Any]]:
    init_backtest_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM backtests WHERE id = ?", (backtest_id,)
        ).fetchone()
    if not row:
        return None
    result = dict(row)
    try:
        result["symbols"] = json.loads(result.pop("symbols_json", "[]"))
    except (json.JSONDecodeError, TypeError):
        result["symbols"] = []
    try:
        result["parameters"] = json.loads(result.pop("parameters_json", "{}"))
    except (json.JSONDecodeError, TypeError):
        result["parameters"] = {}
    try:
        result["result"] = json.loads(result.pop("result_json", "null"))
    except (json.JSONDecodeError, TypeError):
        result["result"] = None
    return result


def list_backtests(limit: int = 50) -> list[dict[str, Any]]:
    init_backtest_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM backtests ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    results = []
    for row in rows:
        r = dict(row)
        try:
            r["symbols"] = json.loads(r.pop("symbols_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            r["symbols"] = []
        try:
            r["parameters"] = json.loads(r.pop("parameters_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            r["parameters"] = {}
        try:
            r["result"] = json.loads(r.pop("result_json", "null"))
        except (json.JSONDecodeError, TypeError):
            r["result"] = None
        results.append(r)
    return results


def update_backtest(backtest_id: str, **kwargs: Any) -> Optional[dict[str, Any]]:
    init_backtest_db()
    now = utc_now_iso()
    allowed = {"name", "market", "strategy_type", "start_date", "end_date",
               "initial_capital", "benchmark", "status", "progress", "error"}
    updates = {k: v for k, v in kwargs.items() if k in allowed}

    if "symbols" in kwargs:
        updates["symbols_json"] = json.dumps(kwargs["symbols"], ensure_ascii=False)
    if "parameters" in kwargs:
        updates["parameters_json"] = json.dumps(kwargs["parameters"], ensure_ascii=False)
    if "result" in kwargs:
        updates["result_json"] = json.dumps(kwargs["result"], ensure_ascii=False)

    if updates.get("status") == "completed":
        updates["completed_at"] = now
    updates["updated_at"] = now
    updates["id"] = backtest_id

    set_clause = ", ".join([f"{k} = :{k}" for k in updates if k != "id"])

    with _connect() as conn:
        conn.execute(
            f"UPDATE backtests SET {set_clause} WHERE id = :id",
            updates,
        )
        conn.commit()
    return get_backtest(backtest_id)


def delete_backtest(backtest_id: str) -> bool:
    init_backtest_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM backtests WHERE id = ?", (backtest_id,))
        conn.commit()
        return cursor.rowcount > 0


def _clamp_metric(value: float, lo: float, hi: float) -> float:
    """钳制单个指标，防止极短样本/极端数据产生失真值或 NaN/inf。"""
    if value != value or value in (float("inf"), float("-inf")):
        return 0.0
    return max(lo, min(value, hi))


def calculate_backtest_metrics(
    equity_curve: list[float],
    benchmark_curve: Optional[list[float]] = None,
    initial_capital: float = 100000,
    risk_free_rate: float = 0.02,
) -> dict[str, Any]:
    if not equity_curve or len(equity_curve) < 2:
        return {
            "total_return": 0,
            "total_return_pct": 0,
            "annualized_return": 0,
            "annualized_volatility": 0,
            "sharpe_ratio": 0,
            "sortino_ratio": 0,
            "max_drawdown": 0,
            "max_drawdown_pct": 0,
            "calmar_ratio": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_trades": 0,
            "avg_trade_return": 0,
            "benchmark_return": 0,
            "alpha": 0,
            "beta": 0,
            "information_ratio": 0,
        }

    final_value = equity_curve[-1]
    total_return = final_value - initial_capital
    total_return_pct = (total_return / initial_capital) * 100

    n_periods = len(equity_curve) - 1
    annualized_return = ((final_value / initial_capital) ** (252.0 / max(n_periods, 1)) - 1) * 100

    returns = []
    for i in range(1, len(equity_curve)):
        if equity_curve[i - 1] > 0:
            returns.append((equity_curve[i] - equity_curve[i - 1]) / equity_curve[i - 1])

    if not returns:
        return {
            "total_return": round(total_return, 2),
            "total_return_pct": round(total_return_pct, 2),
            "annualized_return": 0,
            "annualized_volatility": 0,
            "sharpe_ratio": 0,
            "sortino_ratio": 0,
            "max_drawdown": 0,
            "max_drawdown_pct": 0,
            "calmar_ratio": 0,
            "win_rate": 0,
            "profit_factor": 0,
            "total_trades": 0,
            "avg_trade_return": 0,
            "benchmark_return": 0,
            "alpha": 0,
            "beta": 0,
            "information_ratio": 0,
        }

    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0
    std_dev = math.sqrt(variance)
    annualized_vol = std_dev * math.sqrt(252) * 100

    downside_returns = [r for r in returns if r < 0]
    downside_std = math.sqrt(sum(r ** 2 for r in downside_returns) / len(downside_returns)) if downside_returns else std_dev
    sortino = ((mean_return * 252 - risk_free_rate) / (downside_std * math.sqrt(252))) if downside_std > 0 else 0

    peak = equity_curve[0]
    max_dd = 0.0
    max_dd_pct = 0.0
    for value in equity_curve:
        if value > peak:
            peak = value
        dd = peak - value
        dd_pct = (dd / peak) * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
        if dd_pct > max_dd_pct:
            max_dd_pct = dd_pct

    calmar = (annualized_return / max_dd_pct) if max_dd_pct > 0 else 0

    sharpe = ((mean_return * 252 - risk_free_rate) / (std_dev * math.sqrt(252))) if std_dev > 0 else 0

    positive_returns = [r for r in returns if r > 0]
    negative_returns = [r for r in returns if r < 0]
    win_rate = (len(positive_returns) / len(returns)) * 100 if returns else 0

    total_profit = sum(positive_returns) * initial_capital if positive_returns else 0
    total_loss = abs(sum(negative_returns) * initial_capital) if negative_returns else 0

    benchmark_return = 0.0
    alpha = 0.0
    beta = 0.0
    information_ratio = 0.0

    if benchmark_curve and len(benchmark_curve) == len(equity_curve):
        benchmark_returns = []
        for i in range(1, len(benchmark_curve)):
            if benchmark_curve[i - 1] > 0:
                benchmark_returns.append(
                    (benchmark_curve[i] - benchmark_curve[i - 1]) / benchmark_curve[i - 1]
                )
        if benchmark_returns:
            benchmark_return = ((benchmark_curve[-1] / benchmark_curve[0]) - 1) * 100
            bench_mean = sum(benchmark_returns) / len(benchmark_returns)

            covar = sum((r - mean_return) * (b - bench_mean) for r, b in zip(returns, benchmark_returns)) / (len(returns) - 1) if len(returns) > 1 else 0
            bench_var = sum((b - bench_mean) ** 2 for b in benchmark_returns) / (len(benchmark_returns) - 1) if len(benchmark_returns) > 1 else 0
            beta = covar / bench_var if bench_var > 0 else 0

            alpha = (mean_return * 252 - risk_free_rate - beta * (bench_mean * 252 - risk_free_rate)) * 100

            excess = [r - b for r, b in zip(returns, benchmark_returns)]
            tracking_error = math.sqrt(sum((e - sum(excess) / len(excess)) ** 2 for e in excess) / (len(excess) - 1)) if len(excess) > 1 else 0
            information_ratio = ((mean_return - bench_mean) * math.sqrt(252) / tracking_error) if tracking_error > 0 else 0

    # 防止极短样本/极端数据产生失真指标（如 3 点曲线年化 3.3e11%、beta -1129）
    annualized_return = _clamp_metric(annualized_return, -100.0, 1.0e4)
    sharpe = _clamp_metric(sharpe, -100.0, 100.0)
    sortino = _clamp_metric(sortino, -100.0, 100.0)
    calmar = _clamp_metric(calmar, -100.0, 100.0)
    beta = _clamp_metric(beta, -10.0, 10.0)
    alpha = _clamp_metric(alpha, -1.0e4, 1.0e4)
    information_ratio = _clamp_metric(information_ratio, -100.0, 100.0)

    return {
        "total_return": round(total_return, 2),
        "total_return_pct": round(total_return_pct, 2),
        "annualized_return": round(annualized_return, 2),
        "annualized_volatility": round(annualized_vol, 2),
        "sharpe_ratio": round(sharpe, 4),
        "sortino_ratio": round(sortino, 4),
        "max_drawdown": round(max_dd, 2),
        "max_drawdown_pct": round(max_dd_pct, 2),
        "calmar_ratio": round(calmar, 4),
        "win_rate": round(win_rate, 1),
        "profit_factor": profit_factor(total_profit, total_loss),
        "total_trades": len(returns),
        "avg_trade_return": round(mean_return * 100, 4),
        "benchmark_return": round(benchmark_return, 2),
        "alpha": round(alpha, 4),
        "beta": round(beta, 4),
        "information_ratio": round(information_ratio, 4),
    }


def compute_equity_curve_from_trades(
    trades: list[dict[str, Any]],
    initial_capital: float = 100000,
) -> list[float]:
    if not trades:
        return [initial_capital]

    equity = initial_capital
    curve = [equity]
    for trade in trades:
        pnl = safe_float(trade.get("pnl") or trade.get("realized_pnl"), 0)
        equity += pnl
        curve.append(equity)
    return curve