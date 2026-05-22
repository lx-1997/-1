from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from typing import AsyncIterator, Optional, Union

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse, StreamingResponse

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKBENCH_DIR = REPO_ROOT / "modules" / "research-workbench"
WORKBENCH_SCRIPT = WORKBENCH_DIR / "tool-server.js"
WORKBENCH_PORT = int(os.getenv("RESEARCH_WORKBENCH_INTERNAL_PORT", "3927"))
WORKBENCH_HOST = "127.0.0.1"
WORKBENCH_UPSTREAM = os.getenv(
    "RESEARCH_WORKBENCH_UPSTREAM_URL",
    f"http://{WORKBENCH_HOST}:{WORKBENCH_PORT}",
).rstrip("/")
AUTOSTART = os.getenv("DEEPFOCUS_RESEARCH_WORKBENCH_AUTOSTART", "1").lower() not in {
    "0",
    "false",
    "no",
    "off",
}
START_TIMEOUT_SECONDS = float(os.getenv("RESEARCH_WORKBENCH_START_TIMEOUT", "12"))

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
}

_process: Optional[asyncio.subprocess.Process] = None
_process_lock = asyncio.Lock()
_log_tasks: set[asyncio.Task[None]] = set()
_last_error = ""


async def warm_research_workbench() -> None:
    try:
        await ensure_research_workbench_started()
    except Exception as exc:
        # Keep the main API available even if the optional workbench cannot boot.
        print(f"[research-workbench] startup deferred: {exc}")


async def stop_research_workbench() -> None:
    global _process
    process = _process
    _process = None

    for task in list(_log_tasks):
        task.cancel()
    _log_tasks.clear()

    if process is None or process.returncode is not None:
        return

    process.terminate()
    try:
        await asyncio.wait_for(process.wait(), timeout=5)
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()


async def proxy_research_workbench(request: Request, path: str) -> Union[StreamingResponse, RedirectResponse]:
    if request.url.path == "/research-workbench":
        return RedirectResponse(url="/research-workbench/", status_code=307)

    await ensure_research_workbench_started()

    upstream_path = f"/{path}" if path else "/"
    upstream_url = f"{WORKBENCH_UPSTREAM}{upstream_path}"
    if request.url.query:
        upstream_url = f"{upstream_url}?{request.url.query}"

    request_headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
    }
    body = await request.body()
    client = httpx.AsyncClient(timeout=None, follow_redirects=False)
    upstream_request = client.build_request(
        request.method,
        upstream_url,
        headers=request_headers,
        content=body,
    )

    try:
        upstream_response = await client.send(upstream_request, stream=True)
    except httpx.HTTPError as exc:
        await client.aclose()
        raise HTTPException(status_code=502, detail=f"研报工作台代理失败：{exc}") from exc

    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }

    async def body_iter() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream_response.aiter_raw():
                yield chunk
        finally:
            await upstream_response.aclose()
            await client.aclose()

    return StreamingResponse(
        body_iter(),
        status_code=upstream_response.status_code,
        headers=response_headers,
    )


async def ensure_research_workbench_started() -> None:
    global _last_error, _process
    if await _is_workbench_healthy():
        _last_error = ""
        return

    async with _process_lock:
        if await _is_workbench_healthy():
            _last_error = ""
            return

        if not AUTOSTART:
            _last_error = "研报工作台自动启动已关闭。"
            raise HTTPException(status_code=503, detail=_last_error)

        if not WORKBENCH_SCRIPT.exists():
            _last_error = f"研报工作台入口不存在：{WORKBENCH_SCRIPT}"
            raise HTTPException(status_code=503, detail=_last_error)

        if not (WORKBENCH_DIR / "node_modules").exists():
            _last_error = "研报工作台依赖未安装，请先执行 npm run research-workbench:install。"
            raise HTTPException(status_code=503, detail=_last_error)

        node_bin = shutil.which("node")
        if not node_bin:
            _last_error = "未找到 node，可先安装 Node.js 后重试。"
            raise HTTPException(status_code=503, detail=_last_error)

        if _process is not None and _process.returncode is None:
            await _wait_until_healthy()
            return

        env = {
            **os.environ,
            "PORT": str(WORKBENCH_PORT),
            "RESEARCH_WORKBENCH_CORS_ORIGIN": "*",
        }
        _process = await asyncio.create_subprocess_exec(
            node_bin,
            str(WORKBENCH_SCRIPT),
            cwd=str(WORKBENCH_DIR),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _start_log_task(_process.stdout, "stdout")
        _start_log_task(_process.stderr, "stderr")
        await _wait_until_healthy()
        _last_error = ""


async def _wait_until_healthy() -> None:
    deadline = asyncio.get_running_loop().time() + START_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _is_workbench_healthy(timeout=0.5):
            return
        if _process is not None and _process.returncode is not None:
            raise HTTPException(status_code=503, detail=f"研报工作台启动失败，退出码 {_process.returncode}。")
        await asyncio.sleep(0.25)

    detail = _last_error or "研报工作台启动超时。"
    raise HTTPException(status_code=503, detail=detail)


async def _is_workbench_healthy(timeout: float = 0.8) -> bool:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(f"{WORKBENCH_UPSTREAM}/api/status")
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _start_log_task(stream: Optional[asyncio.StreamReader], label: str) -> None:
    if stream is None:
        return
    task = asyncio.create_task(_drain_stream(stream, label))
    _log_tasks.add(task)
    task.add_done_callback(_log_tasks.discard)


async def _drain_stream(stream: asyncio.StreamReader, label: str) -> None:
    while True:
        line = await stream.readline()
        if not line:
            return
        text = line.decode("utf-8", errors="replace").rstrip()
        if text:
            print(f"[research-workbench:{label}] {text}")
