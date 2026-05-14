from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

from .schemas import ModelConfigRequest, ModelConfigResponse


CONFIG_PATH = Path(
    os.getenv(
        "DEEPFOCUS_MODEL_CONFIG_PATH",
        str(Path(__file__).resolve().parents[1] / ".model_config.json"),
    )
)


def _default_config() -> dict[str, Any]:
    provider = (
        os.getenv("DEEPFOCUS_LLM_PROVIDER")
        or os.getenv("FINGPT_LLM_PROVIDER")
        or "mock"
    ).lower()

    if provider == "minimax":
        model = os.getenv("MINIMAX_MODEL", "MiniMax-M2.7")
        base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1")
        api_key = os.getenv("MINIMAX_API_KEY")
    elif provider in {"openai", "openai-compatible", "cloud"}:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.getenv("OPENAI_BASE_URL") or None
        api_key = os.getenv("OPENAI_API_KEY")
    else:
        provider = "mock"
        model = "mock-research-analyst"
        base_url = None
        api_key = None

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "temperature": float(os.getenv("DEEPFOCUS_LLM_TEMPERATURE", "0.2")),
        "config_source": "env",
    }


def load_model_config() -> dict[str, Any]:
    config = _default_config()
    if CONFIG_PATH.exists():
        try:
            saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            config.update({key: value for key, value in saved.items() if value is not None})
            config["config_source"] = str(CONFIG_PATH)
        except (OSError, json.JSONDecodeError):
            config["config_source"] = "env-invalid-local-config"
    return _normalize_config(config)


def save_model_config(request: ModelConfigRequest) -> ModelConfigResponse:
    current = load_model_config()
    incoming = request.model_dump()

    provider = incoming["provider"].lower()
    model = incoming.get("model") or _default_model_for(provider)
    base_url = incoming.get("base_url") or _default_base_url_for(provider)
    api_key = incoming.get("api_key")

    next_config = {
        **current,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "temperature": incoming.get("temperature", current.get("temperature", 0.2)),
    }

    if api_key is not None:
        next_config["api_key"] = api_key.strip() or None

    next_config = _normalize_config(next_config)
    if incoming.get("persist", True):
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(
            json.dumps(
                {
                    "provider": next_config["provider"],
                    "model": next_config["model"],
                    "base_url": next_config.get("base_url"),
                    "api_key": next_config.get("api_key"),
                    "temperature": next_config["temperature"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        next_config["config_source"] = str(CONFIG_PATH)
    else:
        next_config["config_source"] = "request-not-persisted"

    return public_model_config(next_config)


def public_model_config(config: Optional[dict[str, Any]] = None) -> ModelConfigResponse:
    config = _normalize_config(config or load_model_config())
    api_key = config.get("api_key")
    return ModelConfigResponse(
        provider=config["provider"],
        model=config["model"],
        base_url=config.get("base_url"),
        temperature=config["temperature"],
        api_key_configured=bool(api_key),
        api_key_preview=_mask_key(api_key),
        config_source=config.get("config_source", "env"),
    )


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    provider = str(config.get("provider") or "mock").lower()
    if provider not in {"mock", "openai", "minimax", "openai-compatible", "cloud"}:
        provider = "mock"

    model = config.get("model") or _default_model_for(provider)
    base_url = config.get("base_url") or _default_base_url_for(provider)
    try:
        temperature = float(config.get("temperature", 0.2))
    except (TypeError, ValueError):
        temperature = 0.2

    return {
        **config,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "temperature": max(0.0, min(1.0, temperature)),
    }


def _default_model_for(provider: str) -> str:
    if provider == "minimax":
        return "MiniMax-M2.7"
    if provider in {"openai", "openai-compatible", "cloud"}:
        return "gpt-4o-mini"
    return "mock-research-analyst"


def _default_base_url_for(provider: str) -> Optional[str]:
    if provider == "minimax":
        return "https://api.minimax.io/v1"
    return None


def _mask_key(api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"
