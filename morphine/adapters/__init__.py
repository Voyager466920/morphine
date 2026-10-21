"""Provider registry and a small read-through cache."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from morphine import config
from morphine.adapters.base import Adapter, Usage, Window
from morphine.adapters.claude import ClaudeAdapter
from morphine.adapters.codex import CodexAdapter
from morphine.adapters.gemini import GeminiAdapter
from morphine.adapters.grok import GrokAdapter
from morphine.util import now, read_json, write_json_atomic

ADAPTERS: Dict[str, Adapter] = {
    a.id: a for a in (ClaudeAdapter(), CodexAdapter(), GeminiAdapter(), GrokAdapter())
}

__all__ = ["ADAPTERS", "Adapter", "Usage", "Window", "get_usage", "cached_usage"]


def _cache_path(provider: str):
    return config.home() / "cache" / f"{provider}.json"


def cached_usage(provider: str) -> Optional[Usage]:
    d = read_json(_cache_path(provider))
    return Usage.from_dict(d) if isinstance(d, dict) else None


def get_usage(provider: str, cfg: Dict[str, Any], max_age: Optional[float] = None) -> Usage:
    """Return a usage reading no older than ``max_age`` seconds (default: config TTL)."""
    if provider not in ADAPTERS:
        return Usage(provider=provider, error=f"unknown provider '{provider}'")
    max_age = cfg.get("cache_ttl_sec", 60) if max_age is None else max_age
    path = _cache_path(provider)
    cached = read_json(path)
    if isinstance(cached, dict) and now() - float(cached.get("checked_at", 0)) < max_age:
        return Usage.from_dict(cached)
    try:
        u = ADAPTERS[provider].fetch(cfg)
    except Exception as e:  # noqa: BLE001 - an adapter bug must never break the agent
        u = Usage(provider=provider, error=f"adapter crashed: {e!r}")
    d = u.to_dict()
    d["checked_at"] = now()
    try:
        write_json_atomic(path, d)
    except OSError:
        pass
    return u


def enabled_providers(cfg: Dict[str, Any]) -> List[str]:
    return [p for p in ADAPTERS if config.provider_cfg(cfg, p).get("enabled", True)]
