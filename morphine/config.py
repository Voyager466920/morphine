"""Configuration and on-disk layout.

Everything lives under ``$MORPHINE_HOME`` (default ``~/.morphine``)::

    config.json            user settings (merged over DEFAULTS)
    state.json             agents currently in lockout (read by the menu bar app)
    cache/<provider>.json  last usage reading per provider
    cache/claude.statusline.json   pushed by the Claude Code status line
    resume-now             touched by `morphine resume` / the menu bar to end every lockout early
    log.txt                one line per lockout start/end
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any, Dict

from morphine.util import read_json, write_json_atomic

DEFAULTS: Dict[str, Any] = {
    # Lock the agent out when the *remaining* share of a window drops below this.
    "threshold_pct": 5.0,
    # Which windows may trigger a lockout. "5h" = session window, "7d" = weekly,
    # "1d" = daily request quota (Gemini CLI estimate).
    "windows": ["5h", "7d", "1d"],
    # Keep the Mac from idle-sleeping while an agent waits (caffeinate -i).
    "keep_awake": True,
    # macOS notification on lockout start / resume.
    "notify": True,
    # Extra seconds to wait after the provider's reset time before resuming.
    "resume_grace_sec": 45,
    # How old a cached usage reading may be before the hook fetches a fresh one.
    "cache_ttl_sec": 60,
    # How often a waiting hook wakes up to check for "resume now" / dead parent.
    "poll_sec": 10,
    # While an agent is sedated, re-read its usage this often and resume as soon
    # as the window has refilled.
    "recheck_min": 31,
    # Also re-check exactly at the provider's reported reset time when that
    # comes before the next periodic check.
    "recheck_at_reset": True,
    "providers": {
        "claude": {"enabled": True},
        "codex": {"enabled": True},
        "gemini": {
            "enabled": True,
            # Gemini CLI exposes no quota endpoint; usage is estimated from
            # local session files against this daily request budget.
            "daily_requests": 1000,
            "reset_tz": "America/Los_Angeles",
            # Estimates are rough, so by default they are shown but do not lock out.
            "lockout_on_estimate": False,
        },
        "grok": {"enabled": True},
    },
    # PTY wrapper (`morphine run`) for CLIs that expose no usage data.
    "wrapper": {
        "resume_text": "continue",
        "limit_patterns": [
            r"usage limit",
            r"rate[ -]?limit(ed)?\b.*(reached|exceeded|hit)",
            r"(reached|exceeded|hit) (your|the) (usage |rate )?limit",
            r"quota (exceeded|exhausted|reached)",
            r"resource[_ ]?exhausted",
            r"limit reached",
            r"too many requests",
            r"out of (credits|usage)",
        ],
    },
}


def home() -> Path:
    return Path(os.environ.get("MORPHINE_HOME", "~/.morphine")).expanduser()


def config_path() -> Path:
    return home() / "config.json"


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load() -> Dict[str, Any]:
    user = read_json(config_path(), {}) or {}
    if not isinstance(user, dict):
        user = {}
    return _merge(DEFAULTS, user)


def load_user() -> Dict[str, Any]:
    user = read_json(config_path(), {}) or {}
    return user if isinstance(user, dict) else {}


def set_value(dotted_key: str, raw_value: str) -> Any:
    """Set ``a.b.c`` in the user config. Values are parsed as JSON when possible."""
    import json

    try:
        value = json.loads(raw_value)
    except ValueError:
        value = raw_value
    user = load_user()
    node = user
    parts = dotted_key.split(".")
    for p in parts[:-1]:
        if not isinstance(node.get(p), dict):
            node[p] = {}
        node = node[p]
    node[parts[-1]] = value
    write_json_atomic(config_path(), user)
    return value


def provider_cfg(cfg: Dict[str, Any], provider: str) -> Dict[str, Any]:
    return cfg.get("providers", {}).get(provider, {}) or {}
