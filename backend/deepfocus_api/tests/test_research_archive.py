"""研报持久归档：累积/幂等/倒序/翻页/搜索/容量。"""
import os
import tempfile

import pytest


@pytest.fixture
def arch(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_RESEARCH_ARCHIVE_DB_PATH", tempfile.mktemp(suffix=".sqlite3"))
    import importlib
    from deepfocus_api import research_archive
    importlib.reload(research_archive)  # 让 DB_PATH 重新读 env
    return research_archive


def _it(i, date, title="研报"):
    return {"id": i, "date": date, "title": title, "file_id": i, "filename": f"{i}.pdf"}


def test_upsert_query_dedup_order(arch):
    arch.upsert([_it("a", "2026-06-24", "NVDA"), _it("b", "2026-06-20", "AAPL"), _it("c", "2026-06-10", "老")])
    arch.upsert([_it("a", "2026-06-24", "NVDA 更新")])      # 幂等：同 id 更新不新增
    assert arch.count() == 3
    assert [x["id"] for x in arch.query(limit=2)] == ["a", "b"]   # date 倒序
    assert arch.query(limit=1)[0]["title"] == "NVDA 更新"          # 内容已更新


def test_before_paging_and_search(arch):
    arch.upsert([_it("a", "2026-06-24"), _it("b", "2026-06-20"), _it("c", "2026-06-10")])
    assert [x["id"] for x in arch.query(before="2026-06-20")] == ["c"]   # 只取更早(不含等于)
    arch.upsert([_it("x", "2026-06-15", "特斯拉 FSD")])
    assert [x["id"] for x in arch.query(query_text="特斯拉")] == ["x"]    # 标题搜索


def test_capacity_cap(arch, monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_RESEARCH_ARCHIVE_MAX", "3")
    import importlib
    importlib.reload(arch)
    arch.upsert([_it(str(i), f"2026-06-{10+i:02d}") for i in range(6)])   # 6 条，超上限 3
    assert arch.count() == 3
    assert [x["id"] for x in arch.query(limit=9)] == ["5", "4", "3"]      # 保留最新 3


def test_safe_on_bad_input(arch):
    assert arch.upsert([]) == 0
    assert arch.upsert([{"date": "2026-06-24"}]) == 0   # 无 id → 跳过
    assert arch.query() == []
