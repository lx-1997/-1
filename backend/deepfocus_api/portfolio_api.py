"""Portfolio, backtest and quant routes with explicit per-account ownership."""
from __future__ import annotations
from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from .auth import require_admin, require_current_user
from .risk_backtest import run_risk_backtest
from .risk_management import calculate_greeks, calculate_position_risk, close_position, create_position, delete_position, get_pnl_summary, get_position, get_risk_limits, get_risk_summary, list_pnl_records, list_positions, PositionAlreadyClosedError, refresh_position_prices, update_position, update_risk_limit
from .backtest_engine import calculate_backtest_metrics, create_backtest, delete_backtest, get_backtest, list_backtests
from .backtest_executor import run_backtest, list_backtest_results
from .quant_lab import run_quant_lab
from .quant_jobs import cancel_quant_job, create_quant_job, get_quant_job
from .schemas import GreeksRequest, GreeksResponse, PnlRecord, PnlSummaryResponse, PositionCloseRequest, PositionCreateRequest, PositionListResponse, PositionRecord, PositionUpdateRequest, RiskLimitRecord, RiskLimitUpdateRequest, RiskSummaryResponse, BacktestCreateRequest, BacktestListResponse, BacktestMetricsRequest, BacktestMetricsResponse, BacktestRecord, RiskBacktestRequest, RiskBacktestResponse, QuantLabRequest, QuantLabResponse

router = APIRouter()

def _owner_args(user: dict) -> dict[str, Any]:
    owner = str(user.get("sub") or "").strip()
    if not owner:
        raise HTTPException(status_code=401, detail="请重新登录")
    return {"owner_user_id": owner, "is_admin": str(user.get("role") or "").lower() == "admin"}


@router.post("/api/risk/greeks", response_model=GreeksResponse)
async def risk_greeks(request: GreeksRequest) -> GreeksResponse:
    result = calculate_greeks(
        underlying_price=request.underlying_price,
        strike=request.strike,
        days_to_expiry=request.days_to_expiry,
        risk_free_rate=request.risk_free_rate,
        implied_vol=request.implied_vol,
        option_type=request.option_type,
    )
    return GreeksResponse(**result)


@router.get("/api/risk/positions", response_model=PositionListResponse)
async def risk_positions(status: Optional[str] = None, _user: dict = Depends(require_current_user)) -> PositionListResponse:
    positions = list_positions(status=status if status else None, **_owner_args(_user))
    enriched = []
    for pos in positions:
        risk = calculate_position_risk(pos)
        enriched.append({**pos, **risk})
    return PositionListResponse(positions=[PositionRecord(**p) for p in enriched])


@router.post("/api/risk/positions", response_model=PositionRecord)
async def risk_create_position(request: PositionCreateRequest, _user: dict = Depends(require_current_user)) -> PositionRecord:
    pos = create_position(
        symbol=request.symbol,
        name=request.name,
        market=request.market,
        asset_class=request.asset_class,
        direction=request.direction,
        entry_price=request.entry_price,
        quantity=request.quantity,
        stop_loss=request.stop_loss,
        take_profit=request.take_profit,
        position_size_pct=request.position_size_pct,
        sector=request.sector,
        strategy=request.strategy,
        notes=request.notes,
        tags=request.tags,
        greeks=request.greeks,
        owner_user_id=_owner_args(_user)["owner_user_id"],
    )
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@router.get("/api/risk/positions/{position_id}", response_model=PositionRecord)
async def risk_get_position(position_id: str, _user: dict = Depends(require_current_user)) -> PositionRecord:
    pos = get_position(position_id, **_owner_args(_user))
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@router.put("/api/risk/positions/{position_id}", response_model=PositionRecord)
async def risk_update_position(position_id: str, request: PositionUpdateRequest, _user: dict = Depends(require_current_user)) -> PositionRecord:
    updates = {k: v for k, v in request.model_dump().items() if v is not None}
    pos = update_position(position_id, **updates, **_owner_args(_user))
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@router.delete("/api/risk/positions/{position_id}")
async def risk_delete_position(position_id: str, _user: dict = Depends(require_current_user)) -> dict:
    if not delete_position(position_id, **_owner_args(_user)):
        raise HTTPException(status_code=404, detail="Position not found")
    return {"status": "deleted", "id": position_id}


@router.post("/api/risk/positions/{position_id}/close", response_model=PositionRecord)
async def risk_close_position(position_id: str, request: PositionCloseRequest, _user: dict = Depends(require_current_user)) -> PositionRecord:
    try:
        pos = close_position(position_id, request.exit_price, request.exit_reason, **_owner_args(_user))
    except PositionAlreadyClosedError:
        raise HTTPException(status_code=409, detail="该持仓已平仓，请勿重复平仓。")
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    risk = calculate_position_risk(pos)
    return PositionRecord(**{**pos, **risk})


@router.post("/api/risk/positions/refresh")
async def risk_refresh_prices(_user: dict = Depends(require_current_user)) -> dict:
    updated = refresh_position_prices(**_owner_args(_user))
    return {"status": "ok", "updated_count": len(updated), "positions": updated}


@router.get("/api/risk/summary", response_model=RiskSummaryResponse)
async def risk_summary(_user: dict = Depends(require_current_user)) -> RiskSummaryResponse:
    data = get_risk_summary(**_owner_args(_user))
    from .risk_management import calculate_position_risk
    enriched = []
    for pos in data.get("open_positions", []):
        risk = calculate_position_risk(pos)
        enriched.append({**pos, **risk})
    data["open_positions"] = enriched
    return RiskSummaryResponse(**data)


@router.get("/api/risk/limits")
async def risk_limits(_user: dict = Depends(require_current_user)) -> list[RiskLimitRecord]:
    limits = get_risk_limits()
    return [RiskLimitRecord(**lim) for lim in limits]


@router.put("/api/risk/limits/{key}", response_model=RiskLimitRecord)
async def risk_update_limit(key: str, request: RiskLimitUpdateRequest, _user: dict = Depends(require_admin)) -> RiskLimitRecord:
    lim = update_risk_limit(key, request.value, request.enabled)
    if not lim:
        raise HTTPException(status_code=404, detail="Risk limit not found")
    return RiskLimitRecord(**lim)


@router.get("/api/risk/pnl", response_model=PnlSummaryResponse)
async def risk_pnl_summary(_user: dict = Depends(require_current_user)) -> PnlSummaryResponse:
    return PnlSummaryResponse(**get_pnl_summary(**_owner_args(_user)))


@router.get("/api/risk/pnl/records")
async def risk_pnl_records(position_id: Optional[str] = None, limit: int = 100, _user: dict = Depends(require_current_user)) -> list[PnlRecord]:
    records = list_pnl_records(position_id=position_id, limit=limit, **_owner_args(_user))
    return [PnlRecord(**r) for r in records]


@router.post("/api/risk/backtest", response_model=RiskBacktestResponse)
async def risk_backtest(request: RiskBacktestRequest) -> RiskBacktestResponse:
    result = await run_risk_backtest(request)
    return RiskBacktestResponse(**result)


@router.post("/api/quant/lab", response_model=QuantLabResponse)
async def quant_lab(payload: QuantLabRequest, request: Request) -> QuantLabResponse:
    require_current_user(request)
    result = await run_quant_lab(payload)
    return QuantLabResponse(**result)


@router.post("/api/quant/lab/jobs")
async def quant_lab_job_start(payload: QuantLabRequest, request: Request) -> dict[str, Any]:
    claims = require_current_user(request)
    owner = str(claims.get("sub") or claims.get("username") or "").strip()
    try:
        return await create_quant_job(owner, payload)
    except RuntimeError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc


@router.get("/api/quant/lab/jobs/{job_id}")
async def quant_lab_job_poll(job_id: str, request: Request) -> dict[str, Any]:
    claims = require_current_user(request)
    owner = str(claims.get("sub") or claims.get("username") or "").strip()
    job = get_quant_job(owner, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="量化任务不存在")
    return job


@router.delete("/api/quant/lab/jobs/{job_id}")
async def quant_lab_job_cancel(job_id: str, request: Request) -> dict[str, Any]:
    claims = require_current_user(request)
    owner = str(claims.get("sub") or claims.get("username") or "").strip()
    job = await cancel_quant_job(owner, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="量化任务不存在")
    return job


@router.post("/api/backtest/{backtest_id}/run")
async def backtest_run(backtest_id: str, request: Request, _user: dict = Depends(require_current_user)) -> StreamingResponse:
    bt = get_backtest(backtest_id, **_owner_args(_user))
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    if bt.get("status") == "running":
        raise HTTPException(status_code=409, detail="Backtest is already running")

    async def event_gen():
        events = run_backtest(backtest_id, request)
        try:
            async for event in events:
                if await request.is_disconnected():
                    break
                yield event
        finally:
            await events.aclose()

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache", "Connection": "keep-alive",
            "X-Accel-Buffering": "no", "Access-Control-Allow-Origin": "*",
        },
    )


@router.get("/api/backtest/aggregate")
async def backtest_aggregate_for_research(symbol: str = "", _user: dict = Depends(require_current_user)) -> dict:
    if not symbol:
        return {"backtests": [], "symbol": "", "total": 0}
    return await list_backtest_results(symbol, **_owner_args(_user))


@router.get("/api/backtest", response_model=BacktestListResponse)
async def backtest_list(limit: int = 50, _user: dict = Depends(require_current_user)) -> BacktestListResponse:
    backtests = list_backtests(limit=limit, **_owner_args(_user))
    return BacktestListResponse(backtests=[BacktestRecord(**bt) for bt in backtests])


@router.post("/api/backtest", response_model=BacktestRecord)
async def backtest_create(request: BacktestCreateRequest, _user: dict = Depends(require_current_user)) -> BacktestRecord:
    bt = create_backtest(
        name=request.name,
        market=request.market,
        strategy_type=request.strategy_type,
        symbols=request.symbols,
        start_date=request.start_date,
        end_date=request.end_date,
        initial_capital=request.initial_capital,
        benchmark=request.benchmark,
        parameters=request.parameters,
        owner_user_id=_owner_args(_user)["owner_user_id"],
    )
    return BacktestRecord(**bt)


@router.get("/api/backtest/{backtest_id}", response_model=BacktestRecord)
async def backtest_get(backtest_id: str, _user: dict = Depends(require_current_user)) -> BacktestRecord:
    bt = get_backtest(backtest_id, **_owner_args(_user))
    if not bt:
        raise HTTPException(status_code=404, detail="Backtest not found")
    return BacktestRecord(**bt)


@router.delete("/api/backtest/{backtest_id}")
async def backtest_delete(backtest_id: str, _user: dict = Depends(require_current_user)) -> dict:
    if not delete_backtest(backtest_id, **_owner_args(_user)):
        raise HTTPException(status_code=404, detail="Backtest not found")
    return {"status": "deleted", "id": backtest_id}


@router.post("/api/backtest/metrics", response_model=BacktestMetricsResponse)
async def backtest_metrics(request: BacktestMetricsRequest) -> BacktestMetricsResponse:
    metrics = calculate_backtest_metrics(
        equity_curve=request.equity_curve,
        benchmark_curve=request.benchmark_curve,
        initial_capital=request.initial_capital,
    )
    return BacktestMetricsResponse(**metrics)
