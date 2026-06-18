from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from fastapi import HTTPException

from deepfocus_api import main
from deepfocus_api import research_wire as rw


def _seed_downloads(tmp_path: Path) -> Path:
    """构造一个抓取舱目录：两篇研报 + manifest + 应被忽略的辅助文件。"""
    out = tmp_path / "downloads" / "海外投行报告"
    out.mkdir(parents=True)
    (out / "特斯拉：Robotaxi 安全数据.pdf").write_bytes(b"%PDF-1.4 fake tesla")
    (out / "宁德时代：储能放量.pdf").write_bytes(b"%PDF-1.4 fake catl")
    (out / "manifest.json").write_text(json.dumps({"files": {
        "a": {"name": "特斯拉：Robotaxi 安全数据.pdf", "createTime": "2026-05-29T09:48:48.113+0800",
              "topicId": 111, "hashtag": "#海外投行报告#", "downloadCount": 21},
        "b": {"name": "宁德时代：储能放量.pdf", "createTime": "2026-05-20T10:00:00.000+0800",
              "topicId": 222, "hashtag": "#海外投行报告#", "downloadCount": 9},
    }}), encoding="utf-8")
    (out / "files.csv").write_text("ignored", encoding="utf-8")
    (out / "draft.part").write_bytes(b"partial")
    return out


def test_list_research_wire_sorts_newest_first_and_parses(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "WORKBENCH_DIR", tmp_path)
    _seed_downloads(tmp_path)

    result = rw.list_research_wire(limit=10)
    assert result["exists"] is True
    assert result["total"] == 2  # manifest.json / files.csv / .part 均被忽略
    titles = [it["title"] for it in result["items"]]
    assert titles == ["特斯拉：Robotaxi 安全数据", "宁德时代：储能放量"]  # 新→旧
    first = result["items"][0]
    assert first["date"] == "2026-05-29"
    assert first["org"] == "海外投行"
    assert first["download_count"] == 21
    assert first["filename"].endswith(".pdf")


def test_list_research_wire_query_filters_by_title(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "WORKBENCH_DIR", tmp_path)
    _seed_downloads(tmp_path)

    result = rw.list_research_wire(query="宁德")
    assert result["total"] == 1
    assert result["items"][0]["title"].startswith("宁德时代")


def test_list_research_wire_missing_dir_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "WORKBENCH_DIR", tmp_path)  # 无 downloads 子目录
    result = rw.list_research_wire()
    assert result["exists"] is False
    assert result["items"] == []


def _online_down(monkeypatch):
    """模拟同机 Node 工作台不可用（在线检索失败）→ 端点回退本地抓取舱。"""
    async def _boom(*args, **kwargs):
        raise RuntimeError("workbench offline")
    monkeypatch.setattr(main, "fetch_research_wire_online", _boom)


def _fake_request(headers: dict | None = None):
    """构造最小 Starlette Request（端点为 ETag/304 需要读 If-None-Match 头）。"""
    from starlette.requests import Request
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "method": "GET", "headers": raw, "query_string": b""})


def _wire_call(monkeypatch, **kwargs):
    """调端点并把 JSONResponse 解回 dict（端点改为带 ETag 的 JSONResponse）。"""
    main._WIRE_RESP_CACHE.clear()  # 测试间隔离响应缓存
    resp = asyncio.run(main.api_research_wire(_fake_request(), **kwargs))
    return json.loads(resp.body), resp


def test_wire_endpoint_falls_back_to_local_when_online_down(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "WORKBENCH_DIR", tmp_path)
    _seed_downloads(tmp_path)
    _online_down(monkeypatch)

    data, _ = _wire_call(monkeypatch, limit=10)
    assert data["total"] == 2
    assert data["data_quality"]["level"] == "live"
    assert data["source"] == "海外投行研报"  # 本地回退源
    # 本地回退：preview_url 指向内联 PDF 代理并带 filename/out 查询参数
    assert data["items"][0]["preview_url"].startswith("/api/research/workbench-pdf?filename=")
    assert "out=" in data["items"][0]["preview_url"]


def test_wire_endpoint_prefers_online_when_available(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "WORKBENCH_DIR", tmp_path)
    _seed_downloads(tmp_path)  # 本地也有，但在线可用时应优先在线

    async def _online(*, limit=60, query="", **kwargs):
        return {"items": [{
            "id": "fid123", "title": "英伟达：数据中心需求", "org": "海外投行",
            "date": "2026-06-01", "created_at": "2026-06-01T08:00:00+0800",
            "filename": "英伟达：数据中心需求.pdf", "out": "", "size": 100,
            "hashtag": "#海外投行报告#", "download_count": 3, "file_id": "fid123",
        }], "total": 1, "exists": True, "online": True}
    monkeypatch.setattr(main, "fetch_research_wire_online", _online)

    data, resp = _wire_call(monkeypatch, limit=10)
    assert data["total"] == 1
    assert data["data_quality"]["level"] == "live"
    assert "在线" in data["source"]
    # 在线条目走 wire-file 在线预览（本地未下载也能读）
    assert data["items"][0]["preview_url"].startswith("/api/research/wire-file?file_id=")
    # ETag/304 协商：第二次带 If-None-Match（含 nginx gzip 的弱 W/ 前缀）应 304
    etag = resp.headers["etag"]
    resp304 = asyncio.run(main.api_research_wire(_fake_request({"if-none-match": f"W/{etag}"}), limit=10))
    assert resp304.status_code == 304


def test_wire_endpoint_degraded_when_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(rw, "WORKBENCH_DIR", tmp_path)
    _online_down(monkeypatch)  # 在线不可用 + 本地空目录 → 降级
    data, _ = _wire_call(monkeypatch)
    assert data["total"] == 0
    assert data["data_quality"]["level"] == "degraded"
    assert data["data_quality"]["label"] == "研报库未同步"


def test_research_file_download_gated_by_default(monkeypatch):
    """默认未配 DEEPFOCUS_RESEARCH_FILE_DOWNLOAD → 原文文件下载端点一律 403（不开放原始文件，只给 AI 解读）。"""
    monkeypatch.delenv("DEEPFOCUS_RESEARCH_FILE_DOWNLOAD", raising=False)
    for call in (
        lambda: main.api_research_workbench_pdf(filename="x.pdf"),
        lambda: main.api_research_wire_file(file_id="abc"),
    ):
        with pytest.raises(HTTPException) as exc:
            asyncio.run(call())
        assert exc.value.status_code == 403


def test_workbench_pdf_blocks_path_traversal(monkeypatch):
    # 即便开启文件下载，路径穿越仍被 _safe_workbench_file_path 拦截。
    monkeypatch.setenv("DEEPFOCUS_RESEARCH_FILE_DOWNLOAD", "1")
    with pytest.raises(HTTPException) as exc:
        asyncio.run(main.api_research_workbench_pdf(filename="../../../etc/passwd"))
    assert exc.value.status_code in (400, 404)
