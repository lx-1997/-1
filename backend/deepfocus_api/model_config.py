from __future__ import annotations

from . import db

import ipaddress
import json
import os
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from .schemas import ModelConfigRequest, ModelConfigResponse


# 本平台要直连的外部数据源父域名（行情/公告/舆情/海关/宏观等）。某些环境的出网代理会
# 封锁这些域名（403），但直连其实可达——把它们加进 NO_PROXY，让数据获取绕代理直连。
DATA_SOURCE_BYPASS_DOMAINS = (
    ".eastmoney.com", "dfcfw.com", ".dfcfw.com",
    ".sina.com.cn", "sinajs.cn", "hq.sinajs.cn",
    ".qq.com", "gtimg.cn", "qt.gtimg.cn",
    "xueqiu.com", ".xueqiu.com",
    "weixin.sogou.com", ".sogou.com",
    ".cninfo.com.cn", ".customs.gov.cn", ".un.org",
    ".cctv.com", "stooq.com", ".stooq.com",
    ".yahoo.com", "finnhub.io", ".nasdaq.com",
    ".stlouisfed.org", ".tradier.com", ".cboe.com",
    ".hkex.com", ".trendforce.com",
    ".google.com", ".github.com", ".githubusercontent.com",
)


def _add_to_no_proxy(hosts) -> None:
    additions = {h for h in hosts if h}
    if not additions:
        return
    for var in ("NO_PROXY", "no_proxy"):
        existing = {p.strip() for p in os.environ.get(var, "").split(",") if p.strip()}
        merged = existing | additions
        if merged != existing:
            os.environ[var] = ",".join(sorted(merged))


def configure_data_source_egress() -> None:
    """让平台的外部数据源域名绕过出网代理（封锁云服务的环境下仍能直连取数）。
    在后端启动时调用一次即可；正常无代理的环境里 NO_PROXY 不产生副作用。"""
    _add_to_no_proxy(DATA_SOURCE_BYPASS_DOMAINS)


_PROVIDER_DEFAULT_HOSTS = {
    "minimax": {"api.minimaxi.com", ".minimaxi.com"},
    "deepseek": {"api.deepseek.com", ".deepseek.com"},
    "openai": {"api.openai.com", ".openai.com"},
    "openai-compatible": {"api.openai.com", ".openai.com"},
    "cloud": {"api.openai.com", ".openai.com"},
}

# DeepSeek exposes an OpenAI-compatible API.  Keep a canonical name in the
# persisted config while accepting spellings used by existing integrations.
_PROVIDER_ALIASES = {
    "deep-seek": "deepseek",
    "deep_seek": "deepseek",
    "deepseek-api": "deepseek",
    "deepseek-compatible": "deepseek",
}


def _canonical_provider(value: Any) -> str:
    provider = str(value or "mock").strip().lower()
    return _PROVIDER_ALIASES.get(provider, provider)


def _bypass_proxy_for_model_host(base_url: Optional[str], provider: Optional[str] = None) -> None:
    """让模型 API 域名绕过出网代理。

    某些运行环境（如沙箱）设了 HTTPS_PROXY，且该代理会封锁云模型域名（返回 403）；
    而模型 API 直连其实可达。这里把模型域名加进 NO_PROXY，使「仅模型流量直连、其它流量
    仍走代理」。在没有代理的正常环境里 NO_PROXY 不产生任何副作用。
    """
    host = None
    if base_url:
        try:
            host = urlparse(base_url).hostname
        except Exception:
            host = None
    if not host:
        # base_url 为空（如 openai 官方未显式配 base_url）→ 用 provider 的官方域名兜底，
        # 否则官方 API 在封锁环境里会被代理拦。
        if provider:
            _add_to_no_proxy(_PROVIDER_DEFAULT_HOSTS.get(provider.lower(), set()))
        return
    # IP 地址不提父域（父域对 IP 无意义）。
    try:
        ipaddress.ip_address(host)
        _add_to_no_proxy({host})
        return
    except ValueError:
        pass
    # 取「直接父域」（去掉最左 label），覆盖同注册域的子域，但不会过宽到整个二级 TLD
    # （如 api.x.co.uk → .x.co.uk 而非 .co.uk；api.minimaxi.com → .minimaxi.com）。
    parts = host.split(".")
    if len(parts) >= 3:
        parent = "." + ".".join(parts[1:])
    elif len(parts) == 2:
        parent = "." + host
    else:
        parent = host
    _add_to_no_proxy({host, parent})


CONFIG_PATH = db.data_path('.model_config.json', 'DEEPFOCUS_MODEL_CONFIG_PATH')

MODEL_POOL_ENV = "DEEPFOCUS_LLM_POOL_JSON"


def _pool_items(raw: Any) -> list[dict[str, Any]]:
    """展开模型池配置，兼容内部 list 和用户提供的 modelMapping 形状。

    这里只负责把配置读成候选项；密钥永远只留在服务端配置，不会被 public_model_config 返回。
    """
    if raw is None:
        return []
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (TypeError, ValueError):
            return []
    if isinstance(raw, dict):
        if "modelMapping" in raw:
            raw = raw.get("modelMapping")
        if isinstance(raw, dict) and any(key in raw for key in ("api_key", "apiKey", "base_url", "baseUrl", "model")):
            raw = [raw]
        elif isinstance(raw, dict):
            # modelMapping 的键（例如 Moss）只是展示名，不参与路由。
            flattened: list[Any] = []
            for value in raw.values():
                flattened.extend(value if isinstance(value, list) else [value])
            raw = flattened
    if not isinstance(raw, list):
        return []
    return [dict(item) for item in raw if isinstance(item, dict)]


def _normalize_pool(raw: Any, fallback: Optional[dict[str, Any]] = None) -> list[dict[str, Any]]:
    """规范化 OpenAI-compatible 模型池，并丢弃缺少密钥的候选。"""
    fallback = fallback or {}
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(_pool_items(raw)):
        provider = _canonical_provider(item.get("provider") or fallback.get("provider") or "minimax")
        if provider not in {"openai", "minimax", "deepseek", "openai-compatible", "cloud"}:
            continue
        api_key = str(item.get("api_key") or item.get("apiKey") or "").strip()
        if not api_key:
            continue
        model = str(item.get("model") or fallback.get("model") or _default_model_for(provider)).strip()
        base_url = item.get("base_url") or item.get("baseUrl") or fallback.get("base_url") or _default_base_url_for(provider)
        label = str(item.get("label") or item.get("name") or f"{provider}-{index + 1}").strip()
        entry = {
            "provider": provider,
            "model": model,
            "base_url": str(base_url).strip() if base_url else None,
            "api_key": api_key,
            "label": label,
        }
        _bypass_proxy_for_model_host(entry.get("base_url"), provider)
        normalized.append(entry)
    return normalized


def _pool_from_env() -> list[dict[str, Any]]:
    return _normalize_pool(os.getenv(MODEL_POOL_ENV))


def _default_config() -> dict[str, Any]:
    provider = (
        os.getenv("DEEPFOCUS_LLM_PROVIDER")
        or os.getenv("FINGPT_LLM_PROVIDER")
        or "mock"
    )
    provider = _canonical_provider(provider)

    if provider == "minimax":
        model = os.getenv("MINIMAX_MODEL", "MiniMax-M3")
        base_url = os.getenv("MINIMAX_BASE_URL", "https://api.minimaxi.com/v1")
        api_key = os.getenv("MINIMAX_API_KEY")
    elif provider == "deepseek":
        model = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
        base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
        api_key = os.getenv("DEEPSEEK_API_KEY")
    elif provider in {"openai", "openai-compatible", "cloud"}:
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        base_url = os.getenv("OPENAI_BASE_URL") or None
        api_key = os.getenv("OPENAI_API_KEY")
    else:
        provider = "mock"
        model = "mock-research-analyst"
        base_url = None
        api_key = None

    env_pool = _pool_from_env()
    if env_pool:
        first = env_pool[0]
        provider = first["provider"]
        model = first["model"]
        base_url = first.get("base_url")
        api_key = first.get("api_key")

    return {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "model_pool": env_pool,
        "load_balance": len(env_pool) > 1,
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

    provider = _canonical_provider(incoming["provider"])
    model = incoming.get("model") or _default_model_for(provider)
    base_url = incoming.get("base_url") or _default_base_url_for(provider)
    api_key = incoming.get("api_key")

    # A saved model pool is an implementation detail of the currently active
    # provider.  If the settings UI explicitly switches provider/model/base
    # URL, keeping the old pool would make ``_normalize_config`` select its
    # first (usually MiniMax) slot again and silently ignore the user's
    # DeepSeek choice.  Reset the pool on an explicit identity change; retain
    # it for no-op saves such as a temperature edit so load balancing is not
    # unexpectedly destroyed.
    current_provider = _canonical_provider(current.get("provider"))
    current_model = str(current.get("model") or "")
    current_base_url = str(current.get("base_url") or "").rstrip("/")
    requested_base_url = str(base_url or "").rstrip("/")
    identity_changed = (
        provider != current_provider
        or (incoming.get("model") is not None and str(model) != current_model)
        or (incoming.get("base_url") is not None and requested_base_url != current_base_url)
    )

    next_config = {
        **current,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "temperature": incoming.get("temperature", current.get("temperature", 0.2)),
    }

    if identity_changed:
        # Do not feed a key for provider A to provider B when the user leaves
        # the key field blank.  The UI can then clearly show "未配置" and ask
        # for the matching credential instead of failing with a confusing
        # remote 401.  An explicitly supplied key is safe to carry over.
        next_config["model_pool"] = []
        next_config["api_key"] = None

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
                    "model_pool": next_config.get("model_pool") or [],
                    "load_balance": bool(next_config.get("load_balance")),
                    "temperature": next_config["temperature"],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        try:
            CONFIG_PATH.chmod(0o600)
        except OSError:
            pass
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
        pool_size=len(config.get("model_pool") or ([config] if api_key else [])),
        load_balancing=bool(config.get("load_balance") and len(config.get("model_pool") or []) > 1),
    )


def _normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    provider = _canonical_provider(config.get("provider"))
    if provider not in {"mock", "openai", "minimax", "deepseek", "openai-compatible", "cloud"}:
        provider = "mock"

    model = config.get("model") or _default_model_for(provider)
    base_url = config.get("base_url") or _default_base_url_for(provider)
    # 确保模型 API 域名绕过出网代理（封锁云模型的环境下仍能直连）；base_url 为空时按 provider 兜底。
    _bypass_proxy_for_model_host(base_url, provider)
    try:
        temperature = float(config.get("temperature", 0.2))
    except (TypeError, ValueError):
        temperature = 0.2

    fallback = {
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": config.get("api_key"),
    }
    pool = _normalize_pool(config.get("model_pool"), fallback=fallback)
    # 单模型配置继续按旧逻辑工作，同时统一暴露成长度为 1 的池，便于调用层只维护一套代码。
    if not pool and fallback.get("api_key") and provider != "mock":
        pool = _normalize_pool([fallback])
    if pool:
        provider = pool[0]["provider"]
        model = pool[0]["model"]
        base_url = pool[0].get("base_url")
        api_key = pool[0].get("api_key")
    else:
        api_key = config.get("api_key")

    return {
        **config,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "model_pool": pool,
        "load_balance": len(pool) > 1,
        "temperature": max(0.0, min(1.0, temperature)),
    }


def _default_model_for(provider: str) -> str:
    provider = _canonical_provider(provider)
    if provider == "minimax":
        return "MiniMax-M3"
    if provider == "deepseek":
        return "deepseek-chat"
    if provider in {"openai", "openai-compatible", "cloud"}:
        return "gpt-4o-mini"
    return "mock-research-analyst"


def _default_base_url_for(provider: str) -> Optional[str]:
    provider = _canonical_provider(provider)
    if provider == "minimax":
        return "https://api.minimaxi.com/v1"
    if provider == "deepseek":
        return "https://api.deepseek.com/v1"
    return None


def _mask_key(api_key: Optional[str]) -> Optional[str]:
    if not api_key:
        return None
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"
