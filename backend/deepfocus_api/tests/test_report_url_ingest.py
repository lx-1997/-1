from __future__ import annotations

import httpx
import pytest
from fastapi import HTTPException

from deepfocus_api.report_url_ingest import extract_report_url


@pytest.mark.asyncio
async def test_extract_report_url_html() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        html = """
        <!doctype html>
        <html>
          <head><title>NVDA initiation report</title><style>.x{}</style></head>
          <body>
            <article>
              <h1>NVDA initiation report</h1>
              <p>Revenue growth accelerated and gross margin expanded.</p>
              <script>window.secret = true;</script>
            </article>
          </body>
        </html>
        """
        return httpx.Response(200, headers={"content-type": "text/html"}, content=html.encode("utf-8"), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await extract_report_url("https://research.example.com/nvda", client=client)

    assert result.title == "NVDA initiation report"
    assert result.parser == "html"
    assert "Revenue growth accelerated" in result.text
    assert "window.secret" not in result.text


@pytest.mark.asyncio
async def test_extract_report_url_blocks_localhost() -> None:
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, request=request))) as client:
        with pytest.raises(HTTPException) as exc:
            await extract_report_url("http://127.0.0.1/report.pdf", client=client)

    assert exc.value.status_code == 422
