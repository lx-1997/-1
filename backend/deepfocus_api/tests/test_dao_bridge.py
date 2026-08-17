from deepfocus_api import dao_bridge


def test_bridge_poll_defaults_to_five_minutes(monkeypatch):
    monkeypatch.delenv("DEEPFOCUS_DAO_BRIDGE_POLL_SECONDS", raising=False)

    assert dao_bridge._bridge_config()["poll"] == 300.0


def test_bridge_poll_can_be_overridden(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_DAO_BRIDGE_POLL_SECONDS", "600")

    assert dao_bridge._bridge_config()["poll"] == 600.0
