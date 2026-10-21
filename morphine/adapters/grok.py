"""Grok Build. xAI publishes no usage numbers or reset windows, so there is
nothing to read ahead of time. Run it under ``morphine run -- grok`` and the
wrapper reacts when the CLI prints a limit message."""

from __future__ import annotations

from typing import Any, Dict

from morphine.adapters.base import Adapter, Usage


class GrokAdapter(Adapter):
    id = "grok"
    name = "Grok Build"
    supports_preemptive = False

    def fetch(self, cfg: Dict[str, Any]) -> Usage:
        return Usage(provider="grok", source="wrapper",
                     note="no usage API — guarded by `morphine run -- grok`")
