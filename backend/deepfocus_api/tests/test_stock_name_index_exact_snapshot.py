"""Exact A-name snapshot regression tests, with an isolated source module."""

import importlib.util
import json
from pathlib import Path
import sys
import types

import pytest

from deepfocus_api.research_prewarm_title_policy import (
    is_bare_company_date_title,
    normalize_company_names,
)


@pytest.fixture
def index(tmp_path, monkeypatch):
    # This isolated import does not touch production/local database files.
    package_name = "_title_policy_index_candidate"
    package = types.ModuleType(package_name)
    package.__path__ = []
    db = types.ModuleType(package_name + ".db")
    db.data_path = lambda *_args: tmp_path / "stock_names.json"
    package.db = db
    monkeypatch.setitem(sys.modules, package_name, package)
    monkeypatch.setitem(sys.modules, db.__name__, db)
    name = package_name + ".stock_name_index"
    spec = importlib.util.spec_from_file_location(name, Path(__import__("deepfocus_api.stock_name_index", fromlist=["x"]).__file__))
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_exact_snapshot_keeps_short_and_ambiguous_names_without_changing_chat(index):
    index._ingest({"短名": "600000", "机器人": "300024", "普通公司": "300182"})
    names = index.exact_a_share_names()
    assert {"短名", "机器人", "普通公司"} <= names
    assert not index.mentions_specific_stock("短名")
    assert not index.mentions_specific_stock("机器人板块")
    assert index.mentions_specific_stock("普通公司")
    chat_names = index.all_name_code()
    assert "短名" not in chat_names
    assert "机器人" not in chat_names
    assert chat_names["普通公司"] == "300182"


@pytest.mark.parametrize("code", ["000001", "002001", "300024", "688001", "600000", "430047", "830799", "920008", 300024])
def test_exact_snapshot_accepts_six_digit_source_codes(index, code):
    index._ingest({"测试公司": code})
    assert "测试公司" in index.exact_a_share_names()


@pytest.mark.parametrize("code", ["01860", "AAPL", "1234567", "12345", "", None, "300024.0", "３０００２４", "SZ300024", "300024.HK", "300024\nextra"])
def test_exact_snapshot_rejects_invalid_hk_and_us_source_codes(index, code):
    index._ingest({"测试公司": code})
    assert "测试公司" not in index.exact_a_share_names()


def test_exact_snapshot_reuses_disk_without_network(index, monkeypatch):
    index._CACHE_FILE.write_text(json.dumps({"短名": "600000", "机器人": "300024", "港股公司": "01860"}))
    def unexpected_network():
        raise AssertionError("exact snapshot must never fetch the network")
    monkeypatch.setattr(index, "_fetch_eastmoney", unexpected_network)
    assert {"短名", "机器人"} <= index.exact_a_share_names()
    assert "港股公司" not in index.exact_a_share_names()
    assert index._disk_load_attempted


def test_exact_snapshot_is_immutable_and_tracks_ingest(index):
    index._ingest({"短名": "600000"})
    old = index.exact_a_share_names()
    assert isinstance(old, frozenset)
    index._ingest({"新短": "600000"})
    current = index.exact_a_share_names()
    assert "短名" in old and "短名" not in current
    assert "新短" in current and "新短" not in old
    assert set(index._SEED_NAME_CODE) <= current


def test_exact_snapshot_nfkc_and_research_title_integration(index):
    index._ingest({"测试Ａ": "600000", "机器人": "300024", "短名": "300182", "汇量科技": "01860"})
    names = normalize_company_names(index.exact_a_share_names())
    assert is_bare_company_date_title("测试A20261013", names)
    assert is_bare_company_date_title("机器人20261013", names)
    assert is_bare_company_date_title("短名20261013", names)
    assert not is_bare_company_date_title("汇量科技20261013", names)
    assert not is_bare_company_date_title("创想三维20261013", names)
    assert not is_bare_company_date_title("机器人行业前瞻20261013", names)
