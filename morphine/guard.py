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
    resume_at: float      # best estimate of when the window refills
    reason: str
    window: str
    known_reset: bool = True   # False when the provider gave no reset time


def recheck_sec(cfg: Dict[str, Any]) -> float:
    """How often a sedated agent's usage is re-read (default 31 minutes)."""
    return max(1.0, 60.0 * float(cfg.get("recheck_min", 31)))


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
    known = len(resets) == len(hits)
    if resets:
        resume_at = max(resets) + float(cfg.get("resume_grace_sec", 45))
    else:
        resume_at = at + recheck_sec(cfg)
    reason = ", ".join(f"{w.name} {w.effective_used(at):.1f}% used" for w, _ in hits)
    worst = max(hits, key=lambda h: h[1] or 0)[0].name
    return Decision(resume_at=resume_at, reason=reason, window=worst, known_reset=known)


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


def _next_check(t: float, expected: float, known: bool, cfg: Dict[str, Any]) -> float:
    """Every ``recheck_min`` minutes; also right at the reported reset time if that
    comes sooner (``recheck_at_reset``), so a refill is never missed by ~30 min."""
    nxt = t + recheck_sec(cfg)
    if known and cfg.get("recheck_at_reset", True) and expected > t:
        nxt = min(nxt, expected)
    return nxt


def _name(provider: str) -> str:
    from morphine.adapters import ADAPTERS

    return ADAPTERS[provider].name if provider in ADAPTERS else provider


def _left_from(usage: Usage, window: str) -> Optional[float]:
    for w in usage.windows:
        if w.name == window:
            return w.remaining()
    return None


def start_lockout(provider: str, decision: Decision, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None,
                  left: Optional[float] = None) -> Dict[str, Any]:
    """Begin (or join) the provider's lockout. Notifies only when it is new."""
    t = now()
    nxt = _next_check(t, decision.resume_at, decision.known_reset, cfg)
    rec = {
        "provider": provider, "started_at": t,
        "resume_at": decision.resume_at if decision.known_reset else nxt,
        "expected": decision.resume_at, "known": decision.known_reset,
        "next_check_at": nxt, "reason": decision.reason, "window": decision.window,
        "left": None if left is None else round(left, 1),
    }
    rec.update({k: v for k, v in (meta or {}).items() if v is not None})
    if state.start_lockout(provider, rec):
        mins = int(recheck_sec(cfg) // 60)
        notify("Morphine 💉", f"{_name(provider)} paused: {decision.reason}. "
                              + (f"Refill ~{fmt_clock(decision.resume_at)}; " if decision.known_reset else "")
                              + f"checking every {mins} min.", cfg)
    return state.get_lockout(provider) or rec


def tick(provider: str, cfg: Dict[str, Any], rec: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """If the lockout's next check is due, re-read usage; clear it once refilled.
    Returns the (possibly updated) lockout, or None when the provider is free."""
    rec = rec or state.get_lockout(provider)
    if not rec:
        return None
    t = now()
    if t < float(rec.get("next_check_at", 0)):
        return rec
    fresh = get_usage(provider, cfg, max_age=0)
    expected, known, reason = float(rec.get("expected", t)), bool(rec.get("known")), rec.get("reason", "")
    left = rec.get("left")
    if fresh.ok:
        again = check(provider, fresh, cfg, at=t)
        if again is None:
            return _finish(provider, cfg)
        expected, known, reason = again.resume_at, again.known_reset, again.reason
        left = _left_from(fresh, again.window)
    elif known and t >= expected:
        return _finish(provider, cfg)  # can't read, but the known reset has passed
    nxt = _next_check(t, expected, known, cfg)
    state.update_lockout(provider, expected=expected, known=known, reason=reason, next_check_at=nxt,
                         resume_at=expected if known else nxt,
                         left=None if left is None else round(left, 1))
    return state.get_lockout(provider)


def _finish(provider: str, cfg: Dict[str, Any], outcome: str = "resumed") -> None:
    if state.clear_lockout(provider, outcome) is not None and outcome == "resumed":
        notify("Morphine ☀️", f"{_name(provider)} is back — usage window refilled.", cfg)
    return None


def tick_all(cfg: Dict[str, Any]) -> None:
    for provider in list(state.all_lockouts()):
        try:
            tick(provider, cfg)
        except Exception as e:  # noqa: BLE001
            state.log(f"tick error ({provider}): {e!r}")


def wake_all() -> int:
    """'Wake now': end every lockout and don't re-lock before each window's refill."""
    n = 0
    for provider, rec in state.all_lockouts().items():
        state.snooze(provider, float(rec.get("expected") or rec.get("resume_at") or now()))
        state.clear_lockout(provider, "resumed early")
        n += 1
    state.request_resume_now()
    return n


def acquire(provider: str, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """Return the provider's active lockout (starting one if usage is drained), or None."""
    rec = state.get_lockout(provider)
    if rec:
        return tick(provider, cfg, rec)
    if state.snoozed(provider):
        return None
    usage = get_usage(provider, cfg)
    if not usage.ok:
        return None  # unknown usage never blocks the agent
    d = check(provider, usage, cfg)
    if d is None:
        return None
    return start_lockout(provider, d, cfg, meta, left=_left_from(usage, d.window))


def wait_for_clear(provider: str, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None,
                   sleep=time.sleep) -> str:
    """Block this hook (no tokens spent) until the provider's lockout ends."""
    meta = meta or {}
    agent_pid = os.getppid()
    started = now()
    rec = state.get_lockout(provider) or {}
    wid = state.add_waiter(provider, float(rec.get("resume_at", started)), rec.get("reason", ""),
                           window=rec.get("window"), agent_pid=agent_pid, **meta)
    caff = keep_awake(cfg)
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
            if state.resume_requested_since(started):
                outcome = "resumed early"
                break
            if os.getppid() == 1 or not _pid_alive(agent_pid):
                outcome = "abandoned"
                break
            rec = tick(provider, cfg)
            if rec is None:
                break
            sleep(min(poll, max(1.0, float(rec.get("next_check_at", now())) - now())))
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
    return outcome


def wait_out(provider: str, decision: Decision, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None,
             sleep=time.sleep) -> str:
    """Start a lockout from `decision` and block until it ends."""
    start_lockout(provider, decision, cfg, meta)
    return wait_for_clear(provider, cfg, meta, sleep=sleep)


# ------------------------------------------------------------------- hook

def pause_mode(provider: str, cfg: Dict[str, Any]) -> str:
    """'block' = the hook sleeps until refill (Claude Code honours long hooks);
    'deny' = refuse tool calls/prompts so the agent stops by itself (Codex kills
    long-running hooks after ~30 s, so sleeping can't hold it)."""
    return str(provider_cfg(cfg, provider).get("pause_mode", "block"))


def deny_reason(provider: str, rec: Dict[str, Any], cfg: Dict[str, Any], for_model: bool) -> str:
    left = rec.get("left")
    left_s = f" ({left:.0f}% left)" if isinstance(left, (int, float)) else ""
    when = f"It refills around {fmt_clock(float(rec['resume_at']))}; " if rec.get("known") else ""
    mins = int(recheck_sec(cfg) // 60)
    base = (f"Morphine paused {_name(provider)}: the {rec.get('window', 'usage')} usage window is nearly "
            f"used up{left_s}. {when}Morphine re-checks every {mins} min.")
    if for_model:
        return base + (" Make no further tool calls; end this turn now with a one-line status so no more "
                       "usage is spent. The user will continue after the window refills.")
    return base + " Use 'Wake now' in the Morphine menu to continue anyway."


def guard(provider: str, cfg: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> str:
    """Back-compat helper: check once; if drained, block until refilled."""
    rec = acquire(provider, cfg, meta)
    if rec is None:
        return "ok"
    return wait_for_clear(provider, cfg, meta)
