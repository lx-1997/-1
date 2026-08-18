from __future__ import annotations

import asyncio
import ast
from pathlib import Path
from types import SimpleNamespace

from deepfocus_api import pdf_brand
from deepfocus_api import research_workbench as rw


class _FakeRequest:
    method = "GET"
    headers = {"range": "bytes=0-1023", "x-test": "1"}
    url = SimpleNamespace(path="/research-workbench/api/previews/abc", query="")

    async def body(self) -> bytes:
        return b""


class _FakeUpstreamResponse:
    status_code = 200
    headers = {
        "content-type": "application/pdf",
        "content-disposition": "inline; filename=report.pdf",
        "content-length": "12",
    }

    def __init__(self) -> None:
        self.closed = False

    async def aread(self) -> bytes:
        return b"%PDF-raw"

    async def aclose(self) -> None:
        self.closed = True


class _FakeClient:
    last_headers = None

    def __init__(self, *args, **kwargs) -> None:
        self.response = _FakeUpstreamResponse()
        self.closed = False

    def build_request(self, method, url, *, headers, content):
        type(self).last_headers = headers
        return SimpleNamespace(method=method, url=url, headers=headers, content=content)

    async def send(self, request, *, stream):
        assert stream is True
        return self.response

    async def aclose(self) -> None:
        self.closed = True


def test_pdf_delivery_path_covers_preview_and_downloads():
    assert rw._is_pdf_delivery_path("api/previews/abc")
    assert rw._is_pdf_delivery_path("downloads/海外投行报告/report.PDF")
    assert not rw._is_pdf_delivery_path("api/status")
    assert not rw._is_pdf_delivery_path("assets/app.js")


def test_workbench_pdf_preview_is_branded_and_range_is_not_forwarded(monkeypatch):
    async def ready() -> None:
        return None

    async def brand(raw: bytes, *, file_id=None) -> bytes:
        assert raw == b"%PDF-raw"
        return b"%PDF-branded"

    monkeypatch.setattr(rw, "ensure_research_workbench_started", ready)
    monkeypatch.setattr(rw.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(pdf_brand, "apply_pdf_brand", brand)

    response = asyncio.run(rw.proxy_research_workbench(_FakeRequest(), "api/previews/abc"))

    assert response.body == b"%PDF-branded"
    assert response.status_code == 200
    assert response.headers["content-length"] == str(len(response.body))
    assert response.headers["accept-ranges"] == "none"
    assert response.headers["cache-control"] == "private, no-store"
    assert "range" not in {key.lower() for key in _FakeClient.last_headers}


def test_local_workbench_pdf_route_uses_brand_pipeline_and_cache_buster():
    """不导入重型 main，用 AST 锁住本地研报入口不得再直出原 PDF。"""
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    source = main_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    route = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "api_research_workbench_pdf"
    )
    route_source = ast.get_source_segment(source, route) or ""
    assert "apply_pdf_brand" in route_source
    assert '"Cache-Control": "private, no-store"' in route_source
    assert '"Accept-Ranges": "none"' in route_source
    assert "&brand=v17" in source


def test_background_wire_refresh_preprocesses_online_and_local_pdfs():
    """PDF 预处理必须由后台抓取刷新触发，不能依赖第一个用户打开列表。"""
    main_path = Path(__file__).resolve().parents[1] / "main.py"
    source = main_path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    refresher = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "run_wire_refresher"
    )
    refresher_source = ast.get_source_segment(source, refresher) or ""
    assert "_trigger_pdf_prewarm" in refresher_source
    assert "_trigger_local_pdf_prewarm" in refresher_source
    assert "today_rows" in refresher_source
    for function_name in ("_prewarm_pdf_batch", "_prewarm_local_pdf_batch"):
        function = next(
            node
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        )
        function_source = ast.get_source_segment(source, function) or ""
        assert "Semaphore(1)" in function_source
