"""Gemini CLI. ESTIMATE ONLY.

Gemini CLI has no quota endpoint. We count model replies recorded today in
``~/.gemini/tmp/*/chats/session-*.json`` against a configurable daily request
budget that resets at midnight Pacific. Treat it as a lower bound.
"""

from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
from typing import Any, Dict, Tuple

from morphine.adapters.base import Adapter, Usage, Window
from morphine.config import provider_cfg
from morphine.util import parse_ts

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore


def gemini_home() -> Path:
    return Path(os.environ.get("GEMINI_HOME", "~/.gemini")).expanduser()


def day_bounds(tz_name: str, at: dt.datetime = None) -> Tuple[float, float]:
    tz = ZoneInfo(tz_name) if ZoneInfo else dt.timezone.utc
    at = at or dt.datetime.now(tz)
    at = at.astimezone(tz)
    start = at.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + dt.timedelta(days=1)
    return start.timestamp(), end.timestamp()


def count_requests_since(since: float) -> int:
    root = gemini_home() / "tmp"
    if not root.is_dir():
        return 0
    n = 0
    for f in root.glob("*/chats/session-*.json"):
        try:
            if f.stat().st_mtime < since:
                continue
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = data.get("messages", []) if isinstance(data, dict) else []
        for m in msgs:
            if not isinstance(m, dict) or m.get("type") not in ("gemini", "model", "assistant"):
                continue
            ts = parse_ts(m.get("timestamp"))
            if ts is None or ts >= since:
                n += 1
    return n


class GeminiAdapter(Adapter):
    id = "gemini"
    name = "Gemini CLI"

    def fetch_local(self, cfg: Dict[str, Any]) -> Usage:
        return self.fetch(cfg)  # already purely local

    def fetch(self, cfg: Dict[str, Any]) -> Usage:
        pc = provider_cfg(cfg, "gemini")
        budget = max(1, int(pc.get("daily_requests", 1000)))
        start, end = day_bounds(pc.get("reset_tz", "America/Los_Angeles"))
        if not (gemini_home() / "tmp").is_dir():
            return Usage(provider="gemini", source="sessions", error="no ~/.gemini/tmp (Gemini CLI not used yet)")
        used = count_requests_since(start)
        pct = min(100.0, used * 100.0 / budget)
        return Usage(
            provider="gemini",
            windows=[Window(name="1d", used_pct=round(pct, 2), resets_at=end, estimated=True)],
            source="sessions",
            note=f"estimate: ~{used}/{budget} requests today",
        )
