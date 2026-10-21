"""OpenAI Codex CLI (ChatGPT plan).

Sources, in order:
1. ``codex app-server`` JSON-RPC ``account/rateLimits/read`` — live numbers.
2. The newest ``token_count`` event in ``~/.codex/sessions/**/rollout-*.jsonl`` —
   Codex writes ``rate_limits.primary`` (5h) / ``secondary`` (weekly) there after
   every turn. Passive and free, but only as fresh as the last turn.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import select
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from morphine import __version__
from morphine.adapters.base import Adapter, Usage, Window
from morphine.util import parse_ts, window_name_from_minutes


def codex_home() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()


# ---------------------------------------------------------------- parsing

def _window_from(obj: Dict[str, Any], fallback: str, event_ts: Optional[float]) -> Optional[Window]:
    if not isinstance(obj, dict):
        return None
    used = obj.get("used_percent", obj.get("usedPercent"))
    try:
        used = float(used) if used is not None else None
    except (TypeError, ValueError):
        used = None
    minutes = obj.get("window_minutes", obj.get("windowDurationMins", obj.get("windowMinutes")))
    resets = parse_ts(obj.get("resets_at", obj.get("resetsAt")))
    if resets is None:
        rin = obj.get("resets_in_seconds", obj.get("resetsInSeconds"))
        if rin is not None and event_ts is not None:
            try:
                resets = event_ts + float(rin)
            except (TypeError, ValueError):
                resets = None
    try:
        minutes = float(minutes) if minutes is not None else None
    except (TypeError, ValueError):
        minutes = None
    return Window(name=window_name_from_minutes(minutes, fallback), used_pct=used, resets_at=resets)


def usage_from_rate_limits(rl: Dict[str, Any], source: str, event_ts: Optional[float] = None) -> Usage:
    wins: List[Window] = []
    for key, fallback in (("primary", "5h"), ("secondary", "7d")):
        w = _window_from(rl.get(key), fallback, event_ts)
        if w is not None:
            wins.append(w)
    return Usage(provider="codex", windows=wins, source=source,
                 error=None if wins else "no rate limit windows")


# ------------------------------------------------------- session log scan

def _recent_rollouts(days: int = 7, limit: int = 40) -> List[Path]:
    root = codex_home() / "sessions"
    files: List[Path] = []
    today = dt.date.today()
    for i in range(days + 1):
        d = today - dt.timedelta(days=i)
        p = root / f"{d.year:04d}" / f"{d.month:02d}" / f"{d.day:02d}"
        if p.is_dir():
            files.extend(p.glob("rollout-*.jsonl"))
    if not files and root.is_dir():  # unknown layout: fall back to a bounded walk
        files = list(root.rglob("rollout-*.jsonl"))
    files.sort(key=lambda f: f.stat().st_mtime, reverse=True)
    return files[:limit]


def _tail_lines(path: Path, max_bytes: int = 512 * 1024) -> List[str]:
    try:
        size = path.stat().st_size
        with open(path, "rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # drop partial line
            return f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []


def fetch_from_sessions() -> Usage:
    best = None  # (ts, rate_limits)
    for f in _recent_rollouts():
        for line in reversed(_tail_lines(f)):
            if "rate_limits" not in line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            payload = ev.get("payload") if isinstance(ev, dict) else None
            if not isinstance(payload, dict):
                payload = ev if isinstance(ev, dict) else {}
            rl = payload.get("rate_limits")
            if not isinstance(rl, dict):
                continue
            ts = parse_ts(ev.get("timestamp")) or f.stat().st_mtime
            if best is None or ts > best[0]:
                best = (ts, rl)
            break  # newest event in this file found
        if best is not None:
            break  # files are newest-first, so the first hit is the latest reading
    if best is None:
        return Usage(provider="codex", source="sessions", error="no token_count events in ~/.codex/sessions")
    u = usage_from_rate_limits(best[1], "sessions", event_ts=best[0])
    u.fetched_at = best[0]
    return u


# ---------------------------------------------------------- app-server RPC

def find_codex(cfg: Optional[Dict[str, Any]] = None) -> Optional[str]:
    """Locate a `codex` binary even when it isn't on PATH (menu bar apps get a bare
    PATH, and the VS Code / Cursor extensions ship their own copy)."""
    override = ((cfg or {}).get("providers", {}).get("codex", {}) or {}).get("path")
    if override and os.access(os.path.expanduser(override), os.X_OK):
        return os.path.expanduser(override)
    exe = shutil.which("codex")
    if exe:
        return exe
    home = Path.home()
    fixed = [Path("/opt/homebrew/bin/codex"), Path("/usr/local/bin/codex"), home / ".local/bin/codex",
             home / ".npm-global/bin/codex", home / ".bun/bin/codex", home / ".volta/bin/codex"]
    for p in fixed:
        if os.access(p, os.X_OK):
            return str(p)
    globs = ["~/.nvm/versions/node/*/bin/codex",
             "~/.vscode/extensions/openai.chatgpt-*/bin/*/codex",
             "~/.cursor/extensions/openai.chatgpt-*/bin/*/codex",
             "~/.windsurf/extensions/openai.chatgpt-*/bin/*/codex"]
    import glob as _glob

    found = [Path(p) for g in globs for p in _glob.glob(os.path.expanduser(g)) if os.access(p, os.X_OK)]
    if found:
        return str(max(found, key=lambda p: p.stat().st_mtime))  # newest install
    return None


def fetch_from_app_server(timeout: float = 8.0, cfg: Optional[Dict[str, Any]] = None) -> Usage:
    exe = find_codex(cfg)
    if not exe:
        return Usage(provider="codex", source="app-server", error="codex binary not found")
    try:
        proc = subprocess.Popen(
            [exe, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, bufsize=0,
        )
    except OSError as e:
        return Usage(provider="codex", source="app-server", error=str(e))

    deadline = time.time() + timeout
    buf = b""

    def send(msg: Dict[str, Any]) -> None:
        proc.stdin.write((json.dumps(msg) + "\n").encode())
        proc.stdin.flush()

    def recv(want_id: int) -> Optional[Dict[str, Any]]:
        nonlocal buf
        while time.time() < deadline:
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                try:
                    msg = json.loads(line)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
            r, _, _ = select.select([proc.stdout], [], [], max(0.0, deadline - time.time()))
            if not r:
                break
            chunk = os.read(proc.stdout.fileno(), 65536)
            if not chunk:
                break
            buf += chunk
        return None

    try:
        send({"id": 1, "method": "initialize",
              "params": {"clientInfo": {"name": "morphine", "title": "Morphine", "version": __version__}}})
        if recv(1) is None:
            return Usage(provider="codex", source="app-server", error="app-server did not answer initialize")
        send({"method": "initialized"})
        time.sleep(0.5)  # the server needs a beat before account calls answer
        send({"id": 2, "method": "account/rateLimits/read"})
        resp = recv(2)
        if resp is not None and "error" in resp:
            send({"id": 3, "method": "account/rateLimits/read", "params": {}})
            resp = recv(3)
        if not resp or "result" not in resp:
            return Usage(provider="codex", source="app-server", error="no rateLimits result")
        result = resp["result"]
        rl = result.get("rateLimits", result) if isinstance(result, dict) else {}
        return usage_from_rate_limits(rl, "app-server")
    except (OSError, ValueError) as e:
        return Usage(provider="codex", source="app-server", error=str(e))
    finally:
        try:
            proc.kill()
            proc.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass


class CodexAdapter(Adapter):
    id = "codex"
    name = "Codex"

    def fetch_local(self, cfg: Dict[str, Any]) -> Optional[Usage]:
        u = fetch_from_sessions()
        return u if u.ok else None

    def fetch(self, cfg: Dict[str, Any]) -> Usage:
        live = fetch_from_app_server(cfg=cfg)
        if live.ok:
            return live
        logged = fetch_from_sessions()
        if logged.ok:
            logged.note = f"from last turn's log (live read failed: {live.error})"
            return logged
        logged.error = f"{live.error}; {logged.error}"
        return logged
