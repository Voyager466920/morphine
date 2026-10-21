"""The decision ("is this agent about to run dry?") and the zero-token wait."""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from morphine import state
from morphine.adapters import Usage, get_usage
from morphine.config import provider_cfg
from morphine.util import fmt_clock, fmt_duration, now


@dataclass
class Decision:
    resume_at: float
    reason: str
    window: str


def check(provider: str, usage: Usage, cfg: Dict[str, Any], at: Optional[float] = None) -> Optional[Decision]:
    """Return a Decision if any watched window has less than ``threshold_pct`` left."""
    at = now() if at is None else at
    thr = float(cfg.get("threshold_pct", 5.0))
    watched = set(cfg.get("windows", ["5h", "7d", "1d"]))
    allow_estimates = bool(provider_cfg(cfg, provider).get("lockout_on_estimate", False))
    hits = []
    for w in usage.windows:
        if w.name not in watched:
            continue
        if w.estimated and not allow_estimates:
            continue
        rem = w.remaining(at)
        if rem is None or rem >= thr:
            continue
        reset = w.resets_at if (w.resets_at and w.resets_at > at) else None
        hits.append((w, reset))
    if not hits:
        return None
    resets = [r for _, r in hits if r]
    if resets:
        resume_at = max(resets) + float(cfg.get("resume_grace_sec", 45))
    else:
        resume_at = at + 60.0 * float(cfg.get("unknown_reset_wait_min", 15))
    reason = ", ".join(f"{w.name} {w.effective_used(at):.1f}% used" for w, _ in hits)
    worst = max(hits, key=lambda h: h[1] or 0)[0].name
    return Decision(resume_at=resume_at, reason=reason, window=worst)


# ------------------------------------------------------------ side effects

def notify(title: str, body: str, cfg: Dict[str, Any]) -> None:
    if not cfg.get("notify", True) or platform.system() != "Darwin":
        return
    esc = lambda s: s.replace("\\", "\\\\").replace('"', '\\"')  # noqa: E731
    try:
        subprocess.Popen(
            ["osascript", "-e", f'display notification "{esc(body)}" with title "{esc(title)}"'],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def keep_awake(cfg: Dict[str, Any]) -> Optional[subprocess.Popen]:
    """Prevent idle sleep for as long as this process lives (macOS)."""
    if not cfg.get("keep_awake", True) or platform.system() != "Darwin":
        return None
    try:
        return subprocess.Popen(["caffeinate", "-i", "-w", str(os.getpid())],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None


class _Interrupted(Exception):
    pass


def _raise_interrupt(signum, frame):  # noqa: ARG001
    raise _Interrupted()


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def wait_out(provider: str, decision: Decision, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None,
             sleep=time.sleep) -> str:
    """Block (spending no tokens) until the window refills. Returns the outcome."""
    meta = meta or {}
    agent_pid = os.getppid()
    started = now()
    resume_at = decision.resume_at
    wid = state.add_waiter(provider, resume_at, decision.reason, window=decision.window,
                           agent_pid=agent_pid, **meta)
    caff = keep_awake(cfg)
    notify("Morphine 💉", f"{provider}: {decision.reason}. Resuming at {fmt_clock(resume_at)} "
                          f"(in {fmt_duration(resume_at - started)}).", cfg)
    old = {}
    for sig in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT):
        try:
            old[sig] = signal.signal(sig, _raise_interrupt)
        except (ValueError, OSError):
            pass
    outcome = "resumed"
    poll = max(1.0, float(cfg.get("poll_sec", 10)))
    try:
        while True:
            t = now()
            if state.resume_requested_since(started):
                outcome = "resumed early"
                break
            if os.getppid() == 1 or not _pid_alive(agent_pid):
                outcome = "abandoned"
                break
            if t >= resume_at:
                fresh = get_usage(provider, cfg, max_age=0)
                again = check(provider, fresh, cfg, at=t) if fresh.ok else None
                if again is None or again.resume_at <= t + 1:
                    break
                resume_at = again.resume_at  # provider moved the goalposts; keep waiting
                state.update_waiter(wid, resume_at=resume_at, reason=again.reason)
            sleep(min(poll, max(1.0, resume_at - t)))
    except _Interrupted:
        outcome = "interrupted"
    finally:
        for sig, h in old.items():
            try:
                signal.signal(sig, h)
            except (ValueError, OSError):
                pass
        if caff is not None:
            try:
                caff.terminate()
            except OSError:
                pass
        state.remove_waiter(wid, outcome)
        if outcome in ("resumed", "resumed early"):
            notify("Morphine ☀️", f"{provider} is back — usage window refilled.", cfg)
    return outcome


# ------------------------------------------------------------------- hook

def guard(provider: str, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> str:
    """Check once; if drained, wait it out. Returns 'ok' or the wait outcome."""
    usage = get_usage(provider, cfg)
    if not usage.ok:
        return "ok"  # unknown usage never blocks the agent
    d = check(provider, usage, cfg)
    if d is None:
        return "ok"
    return wait_out(provider, d, cfg, meta)
