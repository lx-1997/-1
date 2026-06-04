from __future__ import annotations

import json
import math
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .market_data import fetch_market_quotes
from .shared_utils import (
    clamp,
    dedupe,
    fmt_pct,
    fmt_usd,
    profit_factor,
    safe_float,
    safe_int,
    utc_now_iso,
)

DB_PATH = Path(
    os.getenv(
        "DEEPFOCUS_RISK_DB_PATH",
        str(Path(__file__).resolve().parents[1] / ".risk_management.sqlite3"),
    )
)

_POSITION_FIELDS = [
    "id", "symbol", "name", "market", "asset_class", "direction",
    "entry_price", "entry_date", "quantity", "current_price",
    "stop_loss", "take_profit", "position_size_pct", "sector",
    "strategy", "notes", "tags_json", "greeks_json",
    "created_at", "updated_at", "closed_at", "status",
]

_PNL_RECORD_FIELDS = [
    "id", "position_id", "symbol", "date", "entry_price",
    "exit_price", "quantity", "realized_pnl", "unrealized_pnl",
    "total_pnl", "return_pct", "holding_days", "exit_reason",
    "created_at",
]


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_risk_db() -> None:
    with _connect() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS positions (
                id TEXT PRIMARY KEY,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                market TEXT NOT NULL DEFAULT 'US',
                asset_class TEXT NOT NULL DEFAULT 'equity',
                direction TEXT NOT NULL DEFAULT 'long',
                entry_price REAL NOT NULL DEFAULT 0,
                entry_date TEXT NOT NULL DEFAULT '',
                quantity REAL NOT NULL DEFAULT 0,
                current_price REAL,
                stop_loss REAL,
                take_profit REAL,
                position_size_pct REAL DEFAULT 0,
                sector TEXT DEFAULT '',
                strategy TEXT DEFAULT '',
                notes TEXT DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                greeks_json TEXT DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                closed_at TEXT,
                status TEXT NOT NULL DEFAULT 'open'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pnl_records (
                id TEXT PRIMARY KEY,
                position_id TEXT NOT NULL,
                symbol TEXT NOT NULL,
                date TEXT NOT NULL,
                entry_price REAL NOT NULL DEFAULT 0,
                exit_price REAL,
                quantity REAL NOT NULL DEFAULT 0,
                realized_pnl REAL DEFAULT 0,
                unrealized_pnl REAL DEFAULT 0,
                total_pnl REAL DEFAULT 0,
                return_pct REAL DEFAULT 0,
                holding_days INTEGER DEFAULT 0,
                exit_reason TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                FOREIGN KEY (position_id) REFERENCES positions(id) ON DELETE CASCADE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS risk_limits (
                id TEXT PRIMARY KEY,
                key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL DEFAULT '',
                value REAL NOT NULL DEFAULT 0,
                unit TEXT NOT NULL DEFAULT '',
                enabled INTEGER NOT NULL DEFAULT 1,
                description TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_positions_symbol ON positions(symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_pnl_position ON pnl_records(position_id)"
        )
        _seed_default_risk_limits(conn)
        conn.commit()


def _seed_default_risk_limits(conn: sqlite3.Connection) -> None:
    existing = {
        row["key"]
        for row in conn.execute("SELECT key FROM risk_limits").fetchall()
    }
    defaults = [
        ("max_position_size_pct", "单仓位上限", 20, "%", "单只标的占组合最大比例"),
        ("max_sector_exposure_pct", "行业集中度上限", 40, "%", "单一行业占组合最大比例"),
        ("max_total_exposure_pct", "总仓位上限", 100, "%", "组合总敞口上限"),
        ("max_drawdown_pct", "最大回撤容忍", 15, "%", "触发风险审查的回撤阈值"),
        ("daily_loss_limit", "单日亏损上限", 5, "%", "单日亏损超限触发熔断"),
        ("var_confidence", "VaR置信度", 95, "%", "VaR计算置信水平"),
        ("var_horizon_days", "VaR窗口", 20, "天", "历史VaR回溯窗口"),
        ("max_leverage", "最大杠杆", 1, "x", "组合最大杠杆倍数"),
        ("min_risk_reward_ratio", "最低风险收益比", 2, "x", "开仓最低风险收益比"),
        ("correlation_warning", "相关性预警", 0.7, "", "头寸间相关性超过此值触发预警"),
    ]
    now = utc_now_iso()
    for key, label, value, unit, desc in defaults:
        if key not in existing:
            conn.execute(
                """INSERT INTO risk_limits (id, key, label, value, unit, enabled, description, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)""",
                (str(uuid.uuid4()), key, label, value, unit, desc, now, now),
            )


def _row_to_dict(row: sqlite3.Row, fields: list[str]) -> dict[str, Any]:
    result = dict(row)
    if "tags_json" in result:
        try:
            result["tags"] = json.loads(result.pop("tags_json", "[]"))
        except (json.JSONDecodeError, TypeError):
            result["tags"] = []
    if "greeks_json" in result:
        try:
            result["greeks"] = json.loads(result.pop("greeks_json", "{}"))
        except (json.JSONDecodeError, TypeError):
            result["greeks"] = {}
    return result


def calculate_greeks(
    underlying_price: float,
    strike: float,
    days_to_expiry: float,
    risk_free_rate: float = 0.05,
    implied_vol: float = 0.3,
    option_type: str = "call",
) -> dict[str, float]:
    if underlying_price <= 0 or strike <= 0 or days_to_expiry <= 0:
        return {
            "delta": 0, "gamma": 0, "theta": 0, "vega": 0, "rho": 0,
            "iv": implied_vol, "theoretical_price": 0,
        }

    t = days_to_expiry / 365.0
    d1 = (math.log(underlying_price / strike) + (risk_free_rate + implied_vol ** 2 / 2) * t) / (implied_vol * math.sqrt(t))
    d2 = d1 - implied_vol * math.sqrt(t)

    def _norm_cdf(x: float) -> float:
        return 0.5 * (1 + math.erf(x / math.sqrt(2)))

    def _norm_pdf(x: float) -> float:
        return math.exp(-0.5 * x * x) / math.sqrt(2 * math.pi)

    if option_type == "call":
        delta = _norm_cdf(d1)
        theta = (
            -underlying_price * _norm_pdf(d1) * implied_vol / (2 * math.sqrt(t))
            - risk_free_rate * strike * math.exp(-risk_free_rate * t) * _norm_cdf(d2)
        ) / 365.0
        rho = strike * t * math.exp(-risk_free_rate * t) * _norm_cdf(d2) / 100.0
        price = underlying_price * _norm_cdf(d1) - strike * math.exp(-risk_free_rate * t) * _norm_cdf(d2)
    else:
        delta = _norm_cdf(d1) - 1
        theta = (
            -underlying_price * _norm_pdf(d1) * implied_vol / (2 * math.sqrt(t))
            + risk_free_rate * strike * math.exp(-risk_free_rate * t) * _norm_cdf(-d2)
        ) / 365.0
        rho = -strike * t * math.exp(-risk_free_rate * t) * _norm_cdf(-d2) / 100.0
        price = strike * math.exp(-risk_free_rate * t) * _norm_cdf(-d2) - underlying_price * _norm_cdf(-d1)

    gamma = _norm_pdf(d1) / (underlying_price * implied_vol * math.sqrt(t))
    vega = underlying_price * _norm_pdf(d1) * math.sqrt(t) / 100.0

    return {
        "delta": round(delta, 4),
        "gamma": round(gamma, 4),
        "theta": round(theta, 4),
        "vega": round(vega, 4),
        "rho": round(rho, 4),
        "iv": round(implied_vol, 4),
        "theoretical_price": round(price, 4),
    }


def calculate_position_risk(position: dict[str, Any]) -> dict[str, Any]:
    entry_price = safe_float(position.get("entry_price"), 0)
    current_price = safe_float(position.get("current_price"), 0)
    quantity = safe_float(position.get("quantity"), 0)
    direction = position.get("direction", "long")

    if quantity <= 0:
        return {
            "unrealized_pnl": 0,
            "unrealized_pnl_pct": 0,
            "notional_value": 0,
            "risk_reward_ratio": 0,
            "distance_to_stop_pct": 0,
            "distance_to_target_pct": 0,
        }

    notional = quantity * current_price
    cost_basis = quantity * entry_price

    if direction == "long":
        unrealized_pnl = notional - cost_basis
    else:
        unrealized_pnl = cost_basis - notional

    unrealized_pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0

    stop_loss = safe_float(position.get("stop_loss"), 0)
    take_profit = safe_float(position.get("take_profit"), 0)

    if direction == "long":
        distance_to_stop = ((current_price - stop_loss) / current_price * 100) if stop_loss > 0 and current_price > 0 else 0
        distance_to_target = ((take_profit - current_price) / current_price * 100) if take_profit > 0 and current_price > 0 else 0
        risk_per_share = entry_price - stop_loss if stop_loss > 0 else 0
        reward_per_share = take_profit - entry_price if take_profit > 0 else 0
    else:
        distance_to_stop = ((stop_loss - current_price) / current_price * 100) if stop_loss > 0 and current_price > 0 else 0
        distance_to_target = ((current_price - take_profit) / current_price * 100) if take_profit > 0 and current_price > 0 else 0
        risk_per_share = stop_loss - entry_price if stop_loss > 0 else 0
        reward_per_share = entry_price - take_profit if take_profit > 0 else 0

    risk_reward = (reward_per_share / risk_per_share) if risk_per_share > 0 else 0

    return {
        "unrealized_pnl": round(unrealized_pnl, 2),
        "unrealized_pnl_pct": round(unrealized_pnl_pct, 2),
        "notional_value": round(notional, 2),
        "cost_basis": round(cost_basis, 2),
        "risk_reward_ratio": round(risk_reward, 2),
        "distance_to_stop_pct": round(distance_to_stop, 2),
        "distance_to_target_pct": round(distance_to_target, 2),
        "risk_per_share": round(risk_per_share, 2),
        "reward_per_share": round(reward_per_share, 2),
    }


def calculate_var(
    returns: list[float],
    confidence: float = 0.95,
    position_value: float = 0,
) -> dict[str, Any]:
    if not returns or len(returns) < 5:
        return {
            "historical_var": 0,
            "parametric_var": 0,
            "var_pct": 0,
            "confidence": confidence,
            "observations": len(returns),
            "method": "insufficient_data",
        }

    sorted_returns = sorted(returns)
    idx = int((1 - confidence) * len(sorted_returns))
    historical_var = abs(sorted_returns[max(0, min(idx, len(sorted_returns) - 1))])

    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / (len(returns) - 1) if len(returns) > 1 else 0
    std_dev = math.sqrt(variance)

    z_score = {
        0.90: 1.282, 0.95: 1.645, 0.975: 1.96, 0.99: 2.326,
    }.get(confidence, 1.645)

    parametric_var = abs(mean_return - z_score * std_dev)

    return {
        "historical_var": round(historical_var * 100, 2),
        "parametric_var": round(parametric_var * 100, 2),
        "var_amount": round(position_value * parametric_var, 2) if position_value > 0 else 0,
        "var_pct": round(parametric_var * 100, 2),
        "confidence": confidence,
        "observations": len(returns),
        "mean_return": round(mean_return * 100, 4),
        "std_dev": round(std_dev * 100, 4),
        "sharpe_ratio": round((mean_return / std_dev) * math.sqrt(252), 4) if std_dev > 0 else 0,
        "method": "parametric",
    }


def calculate_portfolio_metrics(positions: list[dict[str, Any]]) -> dict[str, Any]:
    if not positions:
        return {
            "total_value": 0,
            "total_cost": 0,
            "total_pnl": 0,
            "total_pnl_pct": 0,
            "position_count": 0,
            "open_count": 0,
            "win_rate": 0,
            "avg_return": 0,
            "max_drawdown": 0,
            "sector_exposure": {},
            "asset_class_exposure": {},
            "direction_exposure": {"long": 0, "short": 0, "net": 0, "gross": 0},
            "concentration_risk": "low",
            "risk_flags": [],
        }

    total_value = 0.0
    total_cost = 0.0
    total_pnl = 0.0
    open_count = 0
    sector_exposure: dict[str, float] = {}
    asset_class_exposure: dict[str, float] = {}
    long_value = 0.0
    short_value = 0.0
    winning_positions = 0
    evaluated_positions = 0

    for pos in positions:
        risk = calculate_position_risk(pos)
        notional = risk["notional_value"]
        cost = risk["cost_basis"]
        pnl = risk["unrealized_pnl"]

        total_value += notional
        total_cost += cost
        total_pnl += pnl

        if pos.get("status") == "open":
            open_count += 1
            sector = pos.get("sector", "未分类")
            asset_class = pos.get("asset_class", "equity")
            sector_exposure[sector] = sector_exposure.get(sector, 0) + notional
            asset_class_exposure[asset_class] = asset_class_exposure.get(asset_class, 0) + notional

            if pos.get("direction") == "short":
                short_value += notional
            else:
                long_value += notional

        if pnl != 0:
            evaluated_positions += 1
            if pnl > 0:
                winning_positions += 1

    total_pnl_pct = (total_pnl / total_cost * 100) if total_cost > 0 else 0
    win_rate = (winning_positions / evaluated_positions * 100) if evaluated_positions > 0 else 0

    sector_pct = {
        k: round(v / total_value * 100, 1)
        for k, v in sorted(sector_exposure.items(), key=lambda x: -x[1])
    } if total_value > 0 else {}

    max_sector = max(sector_exposure.values()) if sector_exposure else 0
    max_sector_pct = (max_sector / total_value * 100) if total_value > 0 else 0

    risk_flags = []
    if max_sector_pct > 40:
        risk_flags.append(f"行业集中度偏高：{max_sector_pct:.0f}%")
    if total_pnl_pct < -10:
        risk_flags.append(f"组合亏损超过10%：{total_pnl_pct:.1f}%")
    if short_value > 0 and total_value > 0 and short_value / total_value > 0.5:
        risk_flags.append("净空头敞口超过50%")

    concentration = "low"
    if max_sector_pct > 50:
        concentration = "high"
    elif max_sector_pct > 30:
        concentration = "medium"

    return {
        "total_value": round(total_value, 2),
        "total_cost": round(total_cost, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2),
        "position_count": len(positions),
        "open_count": open_count,
        "win_rate": round(win_rate, 1),
        "sector_exposure": sector_pct,
        "asset_class_exposure": {
            k: round(v / total_value * 100, 1) if total_value > 0 else 0
            for k, v in asset_class_exposure.items()
        },
        "direction_exposure": {
            "long": round(long_value, 2),
            "short": round(short_value, 2),
            "net": round(long_value - short_value, 2),
            "gross": round(long_value + abs(short_value), 2),
        },
        "concentration_risk": concentration,
        "risk_flags": risk_flags,
    }


def create_position(
    symbol: str,
    name: str = "",
    market: str = "US",
    asset_class: str = "equity",
    direction: str = "long",
    entry_price: float = 0,
    quantity: float = 0,
    stop_loss: Optional[float] = None,
    take_profit: Optional[float] = None,
    position_size_pct: float = 0,
    sector: str = "",
    strategy: str = "",
    notes: str = "",
    tags: Optional[list[str]] = None,
    greeks: Optional[dict[str, float]] = None,
) -> dict[str, Any]:
    init_risk_db()
    now = utc_now_iso()
    pos_id = str(uuid.uuid4())
    record = {
        "id": pos_id,
        "symbol": symbol.upper(),
        "name": name,
        "market": market,
        "asset_class": asset_class,
        "direction": direction,
        "entry_price": entry_price,
        "entry_date": now[:10],
        "quantity": quantity,
        "current_price": entry_price,
        "stop_loss": stop_loss,
        "take_profit": take_profit,
        "position_size_pct": position_size_pct,
        "sector": sector,
        "strategy": strategy,
        "notes": notes,
        "tags_json": json.dumps(tags or [], ensure_ascii=False),
        "greeks_json": json.dumps(greeks or {}, ensure_ascii=False),
        "created_at": now,
        "updated_at": now,
        "closed_at": None,
        "status": "open",
    }
    with _connect() as conn:
        placeholders = ", ".join([f":{f}" for f in _POSITION_FIELDS])
        conn.execute(
            f"INSERT INTO positions ({', '.join(_POSITION_FIELDS)}) VALUES ({placeholders})",
            record,
        )
        conn.commit()
    return get_position(pos_id) or record


def get_position(position_id: str) -> Optional[dict[str, Any]]:
    init_risk_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM positions WHERE id = ?", (position_id,)
        ).fetchone()
    if not row:
        return None
    return _row_to_dict(row, _POSITION_FIELDS)


def list_positions(status: Optional[str] = None) -> list[dict[str, Any]]:
    init_risk_db()
    with _connect() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM positions WHERE status = ? ORDER BY created_at DESC",
                (status,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM positions ORDER BY created_at DESC"
            ).fetchall()
    return [_row_to_dict(dict(row), _POSITION_FIELDS) for row in rows]


def update_position(
    position_id: str,
    **kwargs: Any,
) -> Optional[dict[str, Any]]:
    init_risk_db()
    now = utc_now_iso()
    allowed = {
        "name", "market", "asset_class", "direction", "entry_price",
        "quantity", "current_price", "stop_loss", "take_profit",
        "position_size_pct", "sector", "strategy", "notes", "status",
    }
    updates = {k: v for k, v in kwargs.items() if k in allowed}
    if not updates:
        return get_position(position_id)

    if "tags" in kwargs:
        updates["tags_json"] = json.dumps(kwargs["tags"], ensure_ascii=False)
    if "greeks" in kwargs:
        updates["greeks_json"] = json.dumps(kwargs["greeks"], ensure_ascii=False)

    updates["updated_at"] = now
    if updates.get("status") == "closed":
        updates["closed_at"] = now

    set_clause = ", ".join([f"{k} = :{k}" for k in updates])
    updates["id"] = position_id

    with _connect() as conn:
        conn.execute(
            f"UPDATE positions SET {set_clause} WHERE id = :id",
            updates,
        )
        conn.commit()
    return get_position(position_id)


def delete_position(position_id: str) -> bool:
    init_risk_db()
    with _connect() as conn:
        cursor = conn.execute("DELETE FROM positions WHERE id = ?", (position_id,))
        conn.commit()
        return cursor.rowcount > 0


class PositionAlreadyClosedError(Exception):
    """对已平仓的持仓再次平仓时抛出；路由层映射为 409，避免重复记账。"""


def close_position(
    position_id: str,
    exit_price: float,
    exit_reason: str = "",
) -> Optional[dict[str, Any]]:
    pos = get_position(position_id)
    if not pos:
        return None
    if pos.get("status") == "closed":
        raise PositionAlreadyClosedError(position_id)

    entry_price = safe_float(pos.get("entry_price"), 0)
    quantity = safe_float(pos.get("quantity"), 0)
    direction = pos.get("direction", "long")

    if direction == "long":
        realized_pnl = (exit_price - entry_price) * quantity
    else:
        realized_pnl = (entry_price - exit_price) * quantity

    return_pct = (realized_pnl / (entry_price * quantity) * 100) if entry_price > 0 and quantity > 0 else 0

    entry_date = pos.get("entry_date", "")
    holding_days = 0
    if entry_date:
        try:
            entry_dt = datetime.strptime(entry_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            holding_days = (datetime.now(timezone.utc) - entry_dt).days
        except ValueError:
            pass

    now = utc_now_iso()
    pnl_id = str(uuid.uuid4())
    with _connect() as conn:
        conn.execute(
            """INSERT INTO pnl_records (id, position_id, symbol, date, entry_price, exit_price,
               quantity, realized_pnl, unrealized_pnl, total_pnl, return_pct, holding_days,
               exit_reason, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?)""",
            (
                pnl_id, position_id, pos["symbol"], now[:10],
                entry_price, exit_price, quantity,
                round(realized_pnl, 2), round(realized_pnl, 2),
                round(return_pct, 2), holding_days,
                exit_reason, now,
            ),
        )
        conn.execute(
            """UPDATE positions SET status = 'closed', current_price = ?,
               updated_at = ?, closed_at = ? WHERE id = ?""",
            (exit_price, now, now, position_id),
        )
        conn.commit()

    return get_position(position_id)


def refresh_position_prices() -> list[dict[str, Any]]:
    open_positions = list_positions(status="open")
    if not open_positions:
        return []

    symbols = list(dedupe([p["symbol"] for p in open_positions]))
    if not symbols:
        return []

    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(asyncio.run, fetch_market_quotes(symbols))
                response = future.result(timeout=10)
        else:
            response = asyncio.run(fetch_market_quotes(symbols))
    except Exception:
        return open_positions

    price_map = {q.symbol.upper(): q.price for q in response.quotes}

    updated = []
    for pos in open_positions:
        current_price = price_map.get(pos["symbol"].upper())
        if current_price and current_price > 0:
            update_position(pos["id"], current_price=current_price)
            pos["current_price"] = current_price
            updated.append(pos)

    return updated


def get_risk_limits() -> list[dict[str, Any]]:
    init_risk_db()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM risk_limits ORDER BY key"
        ).fetchall()
    return [dict(row) for row in rows]


def update_risk_limit(key: str, value: float, enabled: Optional[bool] = None) -> Optional[dict[str, Any]]:
    init_risk_db()
    now = utc_now_iso()
    updates = {"value": value, "updated_at": now}
    if enabled is not None:
        updates["enabled"] = 1 if enabled else 0

    set_clause = ", ".join([f"{k} = :{k}" for k in updates])
    updates["key"] = key

    with _connect() as conn:
        conn.execute(
            f"UPDATE risk_limits SET {set_clause} WHERE key = :key",
            updates,
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM risk_limits WHERE key = ?", (key,)
        ).fetchone()
    return dict(row) if row else None


def get_risk_summary() -> dict[str, Any]:
    positions = list_positions()
    open_positions = [p for p in positions if p.get("status") == "open"]
    closed_positions = [p for p in positions if p.get("status") == "closed"]

    portfolio = calculate_portfolio_metrics(open_positions)

    pnl_records = list_pnl_records()
    daily_returns = [r.get("return_pct", 0) / 100 for r in pnl_records if r.get("return_pct")]
    var_result = calculate_var(
        daily_returns,
        confidence=0.95,
        position_value=portfolio["total_value"],
    )

    limits = {r["key"]: r for r in get_risk_limits()}

    alerts = []
    max_position_pct = limits.get("max_position_size_pct", {}).get("value", 20)
    max_sector_pct = limits.get("max_sector_exposure_pct", {}).get("value", 40)
    max_drawdown = limits.get("max_drawdown_pct", {}).get("value", 15)

    sector_exposure = portfolio.get("sector_exposure", {})
    for sector, pct in sector_exposure.items():
        if pct > max_sector_pct:
            alerts.append({
                "level": "warning",
                "type": "sector_concentration",
                "message": f"{sector}行业敞口{pct}%超过上限{max_sector_pct}%",
                "metric": "sector_exposure",
                "value": pct,
                "limit": max_sector_pct,
            })

    for pos in open_positions:
        risk = calculate_position_risk(pos)
        if risk["unrealized_pnl_pct"] < -max_drawdown:
            alerts.append({
                "level": "danger",
                "type": "drawdown",
                "message": f"{pos['symbol']}回撤{abs(risk['unrealized_pnl_pct']):.1f}%超过阈值{max_drawdown}%",
                "metric": "drawdown",
                "symbol": pos["symbol"],
                "value": abs(risk["unrealized_pnl_pct"]),
                "limit": max_drawdown,
            })

    if portfolio["total_pnl_pct"] < -max_drawdown:
        alerts.append({
            "level": "danger",
            "type": "portfolio_drawdown",
            "message": f"组合回撤{abs(portfolio['total_pnl_pct']):.1f}%超过阈值{max_drawdown}%",
            "metric": "portfolio_drawdown",
            "value": abs(portfolio["total_pnl_pct"]),
            "limit": max_drawdown,
        })

    return {
        "portfolio": portfolio,
        "var": var_result,
        "open_positions": open_positions,
        "closed_positions_count": len(closed_positions),
        "alerts": alerts,
        "risk_limits": limits,
        "generated_at": utc_now_iso(),
    }


def list_pnl_records(position_id: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    init_risk_db()
    with _connect() as conn:
        if position_id:
            rows = conn.execute(
                "SELECT * FROM pnl_records WHERE position_id = ? ORDER BY created_at DESC LIMIT ?",
                (position_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM pnl_records ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [dict(row) for row in rows]


def get_pnl_summary() -> dict[str, Any]:
    records = list_pnl_records(limit=500)
    if not records:
        return {
            "total_realized_pnl": 0,
            "total_trades": 0,
            "winning_trades": 0,
            "losing_trades": 0,
            "win_rate": 0,
            "avg_win": 0,
            "avg_loss": 0,
            "profit_factor": 0,
            "total_return_pct": 0,
            "best_trade": None,
            "worst_trade": None,
            "avg_holding_days": 0,
        }

    total_pnl = sum(r.get("realized_pnl", 0) for r in records)
    winning = [r for r in records if r.get("realized_pnl", 0) > 0]
    losing = [r for r in records if r.get("realized_pnl", 0) < 0]

    total_wins = sum(r["realized_pnl"] for r in winning)
    total_losses = abs(sum(r["realized_pnl"] for r in losing))

    holding_days = [r.get("holding_days", 0) for r in records if r.get("holding_days", 0) > 0]

    return {
        "total_realized_pnl": round(total_pnl, 2),
        "total_trades": len(records),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(len(winning) / len(records) * 100, 1) if records else 0,
        "avg_win": round(total_wins / len(winning), 2) if winning else 0,
        "avg_loss": round(total_losses / len(losing), 2) if losing else 0,
        "profit_factor": profit_factor(total_wins, total_losses),
        "total_return_pct": round(sum(r.get("return_pct", 0) for r in records), 2),
        "best_trade": max(records, key=lambda r: r.get("realized_pnl", 0)) if records else None,
        "worst_trade": min(records, key=lambda r: r.get("realized_pnl", 0)) if records else None,
        "avg_holding_days": round(sum(holding_days) / len(holding_days), 1) if holding_days else 0,
    }
