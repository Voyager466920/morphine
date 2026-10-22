"""Economy mode: once an agent's usage window drops below ``economy.below_pct``
remaining (default 50%), Morphine's UserPromptSubmit hook adds a short note to
the conversation asking for token-lean replies — terse prose, no recaps, no
re-dumping files or long logs — while code, commands, accuracy, reasoning and
verification stay exactly as thorough as usual. When the window refills, a
second note switches it off.

The note is injected only on the switch (and as a reminder every
``economy.reminder_every`` prompts), not on every prompt, so it costs a few
dozen input tokens per switch rather than per turn.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from morphine import config
from morphine.adapters import ADAPTERS, Usage
from morphine.adapters.base import shortest_window
from morphine.util import now, read_json, write_json_atomic

# Agents whose UserPromptSubmit hook can add context (hookSpecificOutput.additionalContext).
SUPPORTED = {"claude", "codex"}

ON_TEXT = (
    "Morphine economy mode is on: the {name} {window} usage window has {left}% left "
    "(economy mode starts below {below}%). While it is on, the user wants token-lean work "
    "with no drop in quality: replies in short, plain sentences with no preamble, recap or "
    "filler; only the changed code or the relevant lines are shown, never whole files again; "
    "files are read in targeted ranges instead of re-reading them, long command output is "
    "filtered rather than dumped, and related tool calls are batched. Code, commands, error "
    "messages, numbers and technical details stay complete and exact, and reasoning, testing "
    "and verification are as thorough as usual."
)
OFF_TEXT = (
    "Morphine economy mode is off: the {name} {window} usage window is back to {left}% left. "
    "The user's normal response style applies again."
)


def settings(cfg: Dict[str, Any]) -> Dict[str, Any]:
    e = cfg.get("economy", {}) or {}
    return {
        "enabled": bool(e.get("enabled", True)),
        "below_pct": float(e.get("below_pct", 50)),
        "reminder_every": max(1, int(e.get("reminder_every", 15))),
    }


def remaining_for_display(usage: Usage) -> Optional[tuple]:
    """(window_name, remaining %) for the window the menu bar shows (5h, else shortest)."""
    w = shortest_window(usage.windows)
    if w is None:
        return None
    rem = w.remaining()
    return None if rem is None else (w.name, rem)


def is_on(usage: Usage, cfg: Dict[str, Any]) -> bool:
    s = settings(cfg)
    r = remaining_for_display(usage) if usage.ok else None
    return bool(s["enabled"] and r is not None and r[1] < s["below_pct"])


def _state_path():
    return config.home() / "economy.json"


def note_for_prompt(provider: str, session: Optional[str], usage: Usage, cfg: Dict[str, Any]) -> Optional[str]:
    """Context to inject on this prompt, or None. Tracks per-session on/off state."""
    if provider not in SUPPORTED:
        return None
    s = settings(cfg)
    r = remaining_for_display(usage) if usage.ok else None
    if r is None:
        return None  # unknown usage: leave the conversation alone
    window, left = r
    want = s["enabled"] and left < s["below_pct"]
    key = f"{provider}:{session or 'default'}"
    t = now()
    st = read_json(_state_path(), {}) or {}
    if not isinstance(st, dict):
        st = {}
    # forget sessions idle for a week
    st = {k: v for k, v in st.items() if isinstance(v, dict) and t - float(v.get("ts", 0)) < 7 * 86400}
    cur = st.get(key, {"on": False, "since_note": 0})
    fmt = dict(name=ADAPTERS[provider].name, window=window, left=int(round(left)), below=int(round(s["below_pct"])))
    note = None
    if want and (not cur.get("on") or int(cur.get("since_note", 0)) + 1 >= s["reminder_every"]):
        note = ON_TEXT.format(**fmt)
        cur = {"on": True, "since_note": 0}
    elif want:
        cur = {"on": True, "since_note": int(cur.get("since_note", 0)) + 1}
    elif cur.get("on"):
        note = OFF_TEXT.format(**fmt)
        cur = {"on": False, "since_note": 0}
    cur["ts"] = t
    st[key] = cur
    try:
        write_json_atomic(_state_path(), st)
    except OSError:
        pass
    return note


def hook_output(event: str, note: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": event or "UserPromptSubmit",
                                              "additionalContext": note}}, ensure_ascii=False)
