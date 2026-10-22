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


def _is_rate_limited(u: Usage) -> bool:
    return bool(u.error) and ("429" in u.error or "Too Many Requests" in u.error)


def get_usage(provider: str, cfg: Dict[str, Any], max_age: Optional[float] = None) -> Usage:
    """Return a usage reading no older than ``max_age`` seconds (default: config TTL).

    * Providers may set ``min_interval_sec`` (Claude's usage endpoint rate-limits,
      so it defaults to 3 minutes); the network is never hit more often than that.
    * A failed read never replaces the last good one: the old numbers stay (with a
      note) so the agent doesn't vanish from the menu bar.
    * HTTP 429 backs off exponentially (2 min, doubling, up to 30 min).
    """
    if provider not in ADAPTERS:
        return Usage(provider=provider, error=f"unknown provider '{provider}'")
    t = now()
    max_age = cfg.get("cache_ttl_sec", 60) if max_age is None else max_age
    max_age = max(max_age, float(config.provider_cfg(cfg, provider).get("min_interval_sec", 0)))
    path = _cache_path(provider)
    cached = read_json(path)
    cached = cached if isinstance(cached, dict) else None
    if cached is not None:
        if t - float(cached.get("checked_at", 0)) < max_age or t < float(cached.get("backoff_until", 0)):
            return Usage.from_dict(cached)
    try:
        u = ADAPTERS[provider].fetch(cfg)
    except Exception as e:  # noqa: BLE001 - an adapter bug must never break the agent
        u = Usage(provider=provider, error=f"adapter crashed: {e!r}")

    if u.ok:
        d = u.to_dict()
        d["checked_at"] = t
    else:
        prev_good = cached is not None and Usage.from_dict(cached).ok
        d = dict(cached) if prev_good else u.to_dict()
        d["checked_at"] = t
        d["last_error"] = u.error
        if prev_good:
            d["note"] = f"last good reading (latest read failed: {u.error})"
        if _is_rate_limited(u):
            prev = float((cached or {}).get("backoff_sec", 0))
            backoff = min(1800.0, max(120.0, prev * 2))
            d["backoff_sec"] = backoff
            d["backoff_until"] = t + backoff
    try:
        write_json_atomic(path, d)
    except OSError:
        pass
    return Usage.from_dict(d)


def enabled_providers(cfg: Dict[str, Any]) -> List[str]:
    return [p for p in ADAPTERS if config.provider_cfg(cfg, p).get("enabled", True)]
