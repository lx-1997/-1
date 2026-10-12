import base64

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from deepfocus_api.auth import require_current_user
from deepfocus_api import trade_statement_api as api


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(api.router)
    return TestClient(app)


def payload(content=b"date,symbol,side,quantity,price,currency\n2026-10-01,000001,buy,100,10,CNY\n"):
    return {"file_name": "statement.csv", "content_base64": base64.b64encode(content).decode()}


def login(client):
    client.app.dependency_overrides[require_current_user] = lambda: {"sub": "user-a"}


def test_anonymous_cannot_analyze_or_download(client):
    assert client.post("/api/portfolio/statement/analyze", json=payload()).status_code == 401
    assert client.get("/api/portfolio/statement/template").status_code == 401


def test_authenticated_analysis_is_stateless_and_receives_currency(client, monkeypatch):
    login(client)
    seen = []

    def analyze(content, name, currency):
        seen.append((content, name, currency))
        return {"trade_count": 1, "currencies": [{"currency": "CNY", "realized_pnl": 0}]}

    monkeypatch.setattr(api, "analyze_statement", analyze)
    body = {**payload(), "settlement_currency": "CNY"}
    response = client.post("/api/portfolio/statement/analyze", json=body)
    assert response.status_code == 200
    assert response.json()["trade_count"] == 1
    assert seen[0][1:] == ("statement.csv", "CNY")
    assert seen[0][0].startswith(b"date,")


@pytest.mark.parametrize("body", [
    {"file_name": "a.csv", "content_base64": "%%%"},
    payload(b""),
    {**payload(), "settlement_currency": "EUR"},
])
def test_invalid_requests_are_rejected(client, body):
    login(client)
    assert client.post("/api/portfolio/statement/analyze", json=body).status_code == 422


def test_size_limit_applies_to_decoded_bytes(client):
    login(client)
    response = client.post("/api/portfolio/statement/analyze", json=payload(b"x" * (api.MAX_FILE_BYTES + 1)))
    assert response.status_code == 413


def test_parser_error_has_actionable_detail(client, monkeypatch):
    login(client)

    def reject(*args):
        raise api.StatementError("未找到成交日期列")

    monkeypatch.setattr(api, "analyze_statement", reject)
    response = client.post("/api/portfolio/statement/analyze", json=payload())
    assert response.status_code == 422
    assert response.json()["detail"] == "未找到成交日期列"


def test_template_is_authenticated_download_and_roundtrips(client):
    login(client)
    response = client.get("/api/portfolio/statement/template")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert "attachment" in response.headers["content-disposition"]
    assert response.headers["cache-control"] == "no-store"
    result = client.post("/api/portfolio/statement/analyze", json=payload(response.content))
    assert result.status_code == 200
    assert result.json()["trade_count"] > 0
