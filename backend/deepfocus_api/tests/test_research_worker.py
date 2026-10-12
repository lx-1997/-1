from contextlib import nullcontext
import io
import json
import sys
from types import ModuleType

from deepfocus_api import research_tools_worker as worker


def test_live_financial_provenance_passes_through_domain_without_reexpansion(monkeypatch, capsys):
    snapshot = {"records": [{"symbol": "AAPL"}], "warnings": ["technical " + str(i) for i in range(17)],
                "methodology": ["filing vintage evidence"], "sources": [{"name": "SEC official"}]}
    assets, analytics = ModuleType("deepfocus_api.research_assets"), ModuleType("deepfocus_api.research_analytics")
    def fetch(symbols, max_periods):
        assert symbols == ["AAPL"] and max_periods == 8
        return snapshot
    def analyze(data):
        assert data["warnings"] == snapshot["warnings"] and data["methodology"] == snapshot["methodology"]
        assert data["sources"] == snapshot["sources"] and data["period_type"] == "quarterly"
        return {"warnings": ["summarized disclosure limitation"], "methodology": ["details retained"], "sources": []}
    assets.fetch_sec_financial_records, analytics.analyze_financials = fetch, analyze
    monkeypatch.setitem(sys.modules, assets.__name__, assets)
    monkeypatch.setitem(sys.modules, analytics.__name__, analytics)
    monkeypatch.setattr(worker, "configure_identity", lambda: None)
    monkeypatch.setattr(worker, "compute_lock", nullcontext)
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"tool": "financials", "input": {"symbols": "AAPL"}})))
    worker.main()
    result = json.loads(capsys.readouterr().out)["result"]
    assert result["warnings"] == ["summarized disclosure limitation"]
    assert result["methodology"] == ["details retained"] and result["sources"] == snapshot["sources"]
