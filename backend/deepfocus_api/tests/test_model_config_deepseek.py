from __future__ import annotations

import json

from deepfocus_api import model_config
from deepfocus_api import llm as llm_mod
from deepfocus_api.schemas import ModelConfigRequest


def test_deepseek_env_preset_uses_official_defaults_without_touching_minimax(monkeypatch):
    monkeypatch.setenv("DEEPFOCUS_LLM_PROVIDER", "deep-seek")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deep-secret")
    monkeypatch.delenv("DEEPSEEK_MODEL", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    config = model_config._default_config()

    assert config["provider"] == "deepseek"
    assert config["model"] == "deepseek-chat"
    assert config["base_url"] == "https://api.deepseek.com/v1"
    assert config["api_key"] == "deep-secret"
    assert model_config._default_model_for("minimax") == "MiniMax-M3"
    assert model_config._default_base_url_for("minimax") == "https://api.minimaxi.com/v1"


def test_deepseek_alias_is_canonicalized_in_pool_and_public_response(monkeypatch):
    monkeypatch.delenv("NO_PROXY", raising=False)
    monkeypatch.delenv("no_proxy", raising=False)
    config = model_config._normalize_config(
        {
            "provider": "deepseek-compatible",
            "api_key": "deep-secret",
        }
    )

    assert config["provider"] == "deepseek"
    assert config["model"] == "deepseek-chat"
    assert config["base_url"] == "https://api.deepseek.com/v1"
    assert config["model_pool"][0]["provider"] == "deepseek"
    public = model_config.public_model_config(config)
    assert public.provider == "deepseek"
    assert public.api_key_configured is True
    assert "deep-secret" not in public.model_dump_json()
    assert "api.deepseek.com" in (model_config.os.environ.get("NO_PROXY") or "")


def test_model_config_request_accepts_deepseek_alias():
    request = ModelConfigRequest(provider="deep_seek", api_key="key")
    assert request.provider == "deep_seek"


def test_deepseek_uses_openai_compatible_client(monkeypatch):
    captured = {}

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(llm_mod, "AsyncOpenAI", FakeClient)
    client = llm_mod.CloudResearchLLM._client_for_config(
        {
            "provider": "deepseek",
            "api_key": "deep-secret",
            "base_url": "https://api.deepseek.com/v1",
        }
    )

    assert isinstance(client, FakeClient)
    assert captured == {
        "api_key": "deep-secret",
        "base_url": "https://api.deepseek.com/v1",
    }


def test_switching_provider_does_not_keep_the_old_model_pool(tmp_path, monkeypatch):
    """Selecting DeepSeek in settings must not silently continue using MiniMax."""
    config_path = tmp_path / "model-config.json"
    config_path.write_text(json.dumps({
        "provider": "minimax",
        "model": "MiniMax-M3",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "mini-key",
        "model_pool": [{
            "provider": "minimax",
            "model": "MiniMax-M3",
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "mini-key",
            "label": "mini-1",
        }],
    }), encoding="utf-8")
    monkeypatch.setattr(model_config, "CONFIG_PATH", config_path)

    saved = model_config.save_model_config(ModelConfigRequest(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
        api_key="deep-key",
    ))

    assert saved.provider == "deepseek"
    assert saved.model == "deepseek-chat"
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted["model_pool"][0]["provider"] == "deepseek"
    assert persisted["model_pool"][0]["api_key"] == "deep-key"


def test_switching_provider_without_key_clears_stale_credential(tmp_path, monkeypatch):
    config_path = tmp_path / "model-config.json"
    config_path.write_text(json.dumps({
        "provider": "minimax",
        "model": "MiniMax-M3",
        "base_url": "https://api.minimaxi.com/v1",
        "api_key": "mini-key",
    }), encoding="utf-8")
    monkeypatch.setattr(model_config, "CONFIG_PATH", config_path)

    saved = model_config.save_model_config(ModelConfigRequest(
        provider="deepseek",
        model="deepseek-chat",
        base_url="https://api.deepseek.com/v1",
    ))

    assert saved.provider == "deepseek"
    assert saved.api_key_configured is False
