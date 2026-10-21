"""Common types for provider adapters.

An adapter answers one question: *how much of each usage window is used, and
when does it reset?* It never decides anything; `morphine.guard` does that.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from morphine.util import now


@dataclass
class Window:
    name: str                       # "5h", "7d", "1d"
    used_pct: Optional[float]       # 0..100, None if unknown
    resets_at: Optional[float]      # epoch seconds, None if unknown
    estimated: bool = False

    def effective_used(self, at: Optional[float] = None) -> Optional[float]:
        """Used % right now. A window whose reset time has passed counts as fresh."""
        at = now() if at is None else at
        if self.resets_at is not None and self.resets_at <= at:
            return 0.0
        return self.used_pct

    def remaining(self, at: Optional[float] = None) -> Optional[float]:
        u = self.effective_used(at)
        return None if u is None else max(0.0, 100.0 - u)


@dataclass
class Usage:
    provider: str
    windows: List[Window] = field(default_factory=list)
    source: str = ""
    fetched_at: float = field(default_factory=now)
    error: Optional[str] = None
    note: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.windows)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["ok"] = self.ok
        return d

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Usage":
        wins = [
            Window(
                name=w.get("name", "?"),
                used_pct=w.get("used_pct"),
                resets_at=w.get("resets_at"),
                estimated=bool(w.get("estimated", False)),
            )
            for w in d.get("windows", []) or []
        ]
        return Usage(
            provider=d.get("provider", "?"),
            windows=wins,
            source=d.get("source", ""),
            fetched_at=float(d.get("fetched_at") or 0),
            error=d.get("error"),
            note=d.get("note"),
        )


# Shorter windows first: the display rule is "show the 5-hour window if there is
# one, otherwise the next-shortest limit" (daily, then weekly).
WINDOW_ORDER = {"5h": 0, "1d": 1, "7d": 2}


def shortest_window(windows: List[Window], at: Optional[float] = None) -> Optional[Window]:
    known = [w for w in windows if w.effective_used(at) is not None]
    if not known:
        return None
    return min(known, key=lambda w: WINDOW_ORDER.get(w.name, 99))


class Adapter:
    id: str = ""
    name: str = ""
    # True when the CLI has hooks we can use to pause *before* the limit.
    supports_preemptive: bool = True

    def fetch(self, cfg: Dict[str, Any]) -> Usage:  # pragma: no cover - interface
        raise NotImplementedError

    def fetch_local(self, cfg: Dict[str, Any]) -> Optional[Usage]:
        """A cheap, network-free reading (local files / localhost) for frequent
        polling by the menu bar. None when the provider has no such source."""
        return None
