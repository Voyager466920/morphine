"""`morphine run -- <cli>`: a transparent PTY wrapper for any agent CLI.

It passes your keystrokes and the CLI's output through untouched while
watching the output for a usage-limit message. When it sees one it works out
when the limit resets, sits idle until then (no tokens), and types the resume
text (default ``continue``) back into the CLI. Use it for CLIs that expose no
usage data (Grok Build, Gemini CLI) or as a safety net for any of them.
"""

from __future__ import annotations

import datetime as dt
import errno
import os
import re
import select
import signal
import struct
import sys
from typing import Any, Dict, List, Optional, Tuple

from morphine import state
from morphine.guard import keep_awake, notify, recheck_sec
from morphine.util import fmt_clock, fmt_duration, now, parse_ts

ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]")

_UNIT = {"d": 86400, "h": 3600, "m": 60, "s": 1}
REL_RE = re.compile(
    r"\b(?:in|after|for)\s+((?:\d+(?:\.\d+)?\s*(?:days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b[\s,]*(?:and\s+)?)+)"
)
REL_PART_RE = re.compile(r"(\d+(?:\.\d+)?)\s*(days?|d|hours?|hrs?|h|minutes?|mins?|m|seconds?|secs?|s)\b")
ISO_RE = re.compile(r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:z|[+-]\d{2}:?\d{2})?")
CLOCK_AT_RE = re.compile(
    r"(?:reset|resets|try again|available|back|retry)[^\n]{0,24}?\bat\s+(\d{1,2})(?::(\d{2}))?\s*([ap])?\.?m?\b"
)
CLOCK_BARE_RE = re.compile(
    r"(?:resets?|try again|available again|back)\s+(\d{1,2})(?::(\d{2}))?\s*(?:([ap])\.?m\.?)?\b"
)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _next_clock(hour: int, minute: int, ampm: Optional[str], at: float) -> Optional[float]:
    if ampm:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if ampm == "p" else 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    base = dt.datetime.fromtimestamp(at)
    cand = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if cand.timestamp() <= at:
        cand += dt.timedelta(days=1)
    return cand.timestamp()


def parse_reset(text: str, at: Optional[float] = None) -> Optional[float]:
    """Best-effort: find when a limit resets in a CLI's (lower-cased, ANSI-free) output."""
    at = now() if at is None else at
    t = text.lower()
    m = REL_RE.search(t)
    if m:
        secs = 0.0
        for num, unit in REL_PART_RE.findall(m.group(1)):
            secs += float(num) * _UNIT[unit[0]]
        if secs > 0:
            return at + secs
    m = ISO_RE.search(t)
    if m:
        ts = parse_ts(m.group(0).replace(" ", "T").upper())
        if ts and ts > at:
            return ts
    m = CLOCK_AT_RE.search(t)
    if m:
        return _next_clock(int(m.group(1)), int(m.group(2) or 0), m.group(3), at)
    m = CLOCK_BARE_RE.search(t)
    if m and (m.group(2) or m.group(3)):
        return _next_clock(int(m.group(1)), int(m.group(2) or 0), m.group(3), at)
    return None


class LimitDetector:
    def __init__(self, patterns: List[str], window: int = 4000):
        self.patterns = [re.compile(p, re.I) for p in patterns]
        self.window = window
        self.buf = ""

    def reset(self) -> None:
        self.buf = ""

    def feed(self, data: bytes) -> Optional[str]:
        """Feed raw output; return the matched text if a limit message is present."""
        self.buf = (self.buf + strip_ansi(data.decode("utf-8", "replace")))[-self.window:]
        for p in self.patterns:
            m = p.search(self.buf)
            if m:
                return m.group(0)
        return None


def _copy_winsize(src_fd: int, dst_fd: int) -> None:
    try:
        import fcntl
        import termios

        ws = fcntl.ioctl(src_fd, termios.TIOCGWINSZ, struct.pack("HHHH", 0, 0, 0, 0))
        fcntl.ioctl(dst_fd, termios.TIOCSWINSZ, ws)
    except (OSError, ImportError):
        pass


def run(cmd: List[str], provider: str, cfg: Dict[str, Any]) -> int:
    import pty
    import termios
    import tty

    wcfg = cfg.get("wrapper", {})
    resume_text = str(wcfg.get("resume_text", "continue"))
    recheck = recheck_sec(cfg)
    grace = float(cfg.get("resume_grace_sec", 45))
    detector = LimitDetector(wcfg.get("limit_patterns", []))

    pid, fd = pty.fork()
    if pid == 0:  # child
        try:
            os.execvp(cmd[0], cmd)
        except OSError as e:
            sys.stderr.write(f"morphine: cannot run {cmd[0]}: {e}\n")
        os._exit(127)

    stdin_fd = sys.stdin.fileno()
    is_tty = os.isatty(stdin_fd)
    saved = termios.tcgetattr(stdin_fd) if is_tty else None
    if is_tty:
        _copy_winsize(stdin_fd, fd)
        tty.setraw(stdin_fd)
        signal.signal(signal.SIGWINCH, lambda *_: _copy_winsize(stdin_fd, fd))

    pending: Optional[Tuple[float, str, float, Any]] = None  # (resume_at, waiter_id, started, caffeinate)
    quiet_until = 0.0
    stdin_open = True
    try:
        while True:
            fds = [fd] + ([stdin_fd] if stdin_open else [])
            try:
                r, _, _ = select.select(fds, [], [], 1.0)
            except InterruptedError:
                continue
            if fd in r:
                try:
                    data = os.read(fd, 65536)
                except OSError as e:
                    if e.errno == errno.EIO:  # child closed the pty
                        break
                    raise
                if not data:
                    break
                os.write(sys.stdout.fileno(), data)
                if pending is None and now() >= quiet_until:
                    hit = detector.feed(data)
                    if hit:
                        t = now()
                        reset = parse_reset(detector.buf, t)
                        # Probe again every `recheck_min` (31 min) or at the printed reset
                        # time, whichever is sooner. A probe while still limited just
                        # re-prints the limit message and re-arms this wait.
                        resume_at = min(reset + grace, t + recheck) if reset else (t + recheck)
                        reason = f"limit message: “{hit.strip()[:60]}”"
                        wid = state.add_waiter(provider, resume_at, reason, window="wrapper",
                                               agent_pid=pid, cwd=os.getcwd())
                        notify("Morphine 💉", f"{provider}: limit hit. Resuming at {fmt_clock(resume_at)} "
                                              f"(in {fmt_duration(resume_at - t)}).", cfg)
                        pending = (resume_at, wid, t, keep_awake(cfg))
            if stdin_open and stdin_fd in r:
                data = os.read(stdin_fd, 65536)
                if not data:
                    stdin_open = False
                else:
                    os.write(fd, data)
            if pending is not None:
                resume_at, wid, started, caff = pending
                early = state.resume_requested_since(started)
                if now() >= resume_at or early:
                    if resume_text:
                        os.write(fd, resume_text.encode() + b"\r")
                    state.remove_waiter(wid, "resumed early" if early else "resumed")
                    notify("Morphine ☀️", f"{provider} is back — sent “{resume_text}”.", cfg)
                    if caff is not None:
                        try:
                            caff.terminate()
                        except OSError:
                            pass
                    detector.reset()
                    quiet_until = now() + 90  # ignore the old message while the screen redraws
                    pending = None
    finally:
        if pending is not None:
            state.remove_waiter(pending[1], "abandoned")
            if pending[3] is not None:
                try:
                    pending[3].terminate()
                except OSError:
                    pass
        if saved is not None:
            termios.tcsetattr(stdin_fd, termios.TCSADRAIN, saved)
    _, status = os.waitpid(pid, 0)
    if os.WIFEXITED(status):
        return os.WEXITSTATUS(status)
    return 128 + os.WTERMSIG(status) if os.WIFSIGNALED(status) else 1
