"""Claude Code (Pro/Max subscription).

Sources, in order:
1. ``cache/claude.statusline.json`` — pushed by ``morphine statusline`` every time
   Claude Code renders its status line. Official, free, no network.
2. The OAuth usage endpoint the Claude apps use (undocumented; may change).
"""

from __future__ import annotations

import json
import os
import platform
import subprocess
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from morphine import config
from morphine.adapters.base import Adapter, Usage, Window
from morphine.util import now, parse_ts, read_json

STATUSLINE_STALE_SEC = 15 * 60
USAGE_URL = "https://api.anthropic.com/api/oauth/usage"


def statusline_cache_path() -> Path:
    return config.home() / "cache" / "claude.statusline.json"


def usage_from_rate_limits(rl: Dict[str, Any], source: str) -> Usage:
    """Parse either the status line ``rate_limits`` object or the OAuth response."""
    wins = []
    for key, name in (("five_hour", "5h"), ("seven_day", "7d")):
        w = rl.get(key)
        if not isinstance(w, dict):
            continue
        used = w.get("used_percentage", w.get("utilization"))
        try:
            used = float(used) if used is not None else None
        except (TypeError, ValueError):
            used = None
        wins.append(Window(name=name, used_pct=used, resets_at=parse_ts(w.get("resets_at"))))
    return Usage(provider="claude", windows=wins, source=source,
                 error=None if wins else "no rate_limits windows")


def _read_token() -> Optional[str]:
    env = os.environ.get("MORPHINE_CLAUDE_OAUTH_TOKEN")
    if env:
        return env
    blob = None
    if platform.system() == "Darwin":
        try:
            out = subprocess.run(
                ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
                capture_output=True, text=True, timeout=5,
            )
            if out.returncode == 0 and out.stdout.strip():
                blob = json.loads(out.stdout.strip())
        except (OSError, ValueError, subprocess.SubprocessError):
            blob = None
    if blob is None:
        cdir = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
        blob = read_json(cdir / ".credentials.json")
    if isinstance(blob, dict):
        tok = (blob.get("claudeAiOauth") or {}).get("accessToken")
        if tok:
            return tok
    return None


def fetch_oauth(timeout: float = 8.0) -> Usage:
    token = _read_token()
    if not token:
        return Usage(provider="claude", source="oauth", error="no Claude OAuth credentials found")
    req = urllib.request.Request(
        USAGE_URL,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": "oauth-2025-04-20",
            "Accept": "application/json",
            "User-Agent": "morphine-quota-guard",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001 - any failure just means "unknown"
        return Usage(provider="claude", source="oauth", error=f"oauth usage request failed: {e}")
    return usage_from_rate_limits(data, "oauth")


class ClaudeAdapter(Adapter):
    id = "claude"
    name = "Claude Code"

    def fetch_local(self, cfg: Dict[str, Any]) -> Optional[Usage]:
        cached = read_json(statusline_cache_path())
        if isinstance(cached, dict):
            u = Usage.from_dict(cached)
            return u if u.ok else None
        return None

    def fetch(self, cfg: Dict[str, Any]) -> Usage:
        cached = read_json(statusline_cache_path())
        if isinstance(cached, dict) and now() - float(cached.get("fetched_at", 0)) < STATUSLINE_STALE_SEC:
            u = Usage.from_dict(cached)
            if u.ok:
                return u
        u = fetch_oauth()
        if u.ok:
            return u
        if isinstance(cached, dict):
            stale = Usage.from_dict(cached)
            if stale.ok:
                stale.note = "stale status line reading"
                return stale
        if u.error and "credentials" in u.error:
            u.note = "Run `morphine install claude` so the status line feeds usage."
        return u
