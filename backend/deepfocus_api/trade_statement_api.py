"""Authenticated, stateless broker-statement analysis; no account writes or orders."""
from __future__ import annotations

import base64
import binascii
from typing import Literal, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .auth import require_current_user
from .trade_statement import StatementError, analyze_statement, statement_template

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_BASE64_LENGTH = 4 * ((MAX_FILE_BYTES + 2) // 3)
router = APIRouter(prefix="/api/portfolio/statement", tags=["portfolio"])


class StatementAnalysisRequest(BaseModel):
    file_name: str = Field(min_length=1, max_length=255)
    content_base64: str = Field(min_length=1, max_length=MAX_BASE64_LENGTH)
    settlement_currency: Optional[Literal["CNY", "HKD", "USD"]] = None


@router.post("/analyze")
async def statement_analyze(
    payload: StatementAnalysisRequest,
    response: Response,
    _user: dict = Depends(require_current_user),
) -> dict:
    response.headers["Cache-Control"] = "no-store"
    try:
        content = base64.b64decode(payload.content_base64, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise HTTPException(status_code=422, detail="文件编码无效，请重新选择文件") from exc
    if not content:
        raise HTTPException(status_code=422, detail="文件为空，请选择包含成交记录的交割单")
    if len(content) > MAX_FILE_BYTES:
        raise HTTPException(status_code=413, detail="交割单不能超过 2 MiB")
    try:
        # Parsing spreadsheets and FIFO are CPU work; keep the API event loop free.
        result = await run_in_threadpool(
            analyze_statement, content, payload.file_name, payload.settlement_currency
        )
    except StatementError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return result


@router.get("/template")
async def statement_download_template(
    _user: dict = Depends(require_current_user),
) -> Response:
    filename = quote("daocaijing-交割单模板.csv")
    return Response(
        content="\ufeff" + statement_template(),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{filename}",
            "Cache-Control": "no-store",
        },
    )
