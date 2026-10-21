"""Shared lockout state (who is waiting, until when), guarded by a file lock."""

from __future__ import annotations

import contextlib
import fcntl
import os
import uuid
from typing import Any, Dict, Iterator, List, Optional

from morphine import config
from morphine.util import fmt_clock, now, read_json, write_json_atomic


def state_path():
    return config.home() / "state.json"


def resume_flag_path():
    return config.home() / "resume-now"


@contextlib.contextmanager
def _locked() -> Iterator[None]:
    home = config.home()
    home.mkdir(parents=True, exist_ok=True)
    with open(home / ".state.lock", "a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def _pid_alive(pid: Optional[int]) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _load() -> Dict[str, Any]:
    data = read_json(state_path(), {}) or {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("waiting", [])
    return data


def waiting(prune: bool = True) -> List[Dict[str, Any]]:
    """Agents currently in lockout. Entries whose waiter process died are dropped."""
    data = _load()
    items = data["waiting"]
    if not prune:
        return items
    alive = [w for w in items if _pid_alive(w.get("waiter_pid"))]
    if len(alive) != len(items):
        with _locked():
            data = _load()
            data["waiting"] = [w for w in data["waiting"] if _pid_alive(w.get("waiter_pid"))]
            write_json_atomic(state_path(), data)
        return data["waiting"]
    return items


def add_waiter(provider: str, resume_at: float, reason: str, **extra: Any) -> str:
    entry = {
        "id": uuid.uuid4().hex[:12],
        "provider": provider,
        "started_at": now(),
        "resume_at": resume_at,
        "reason": reason,
        "waiter_pid": os.getpid(),
    }
    entry.update({k: v for k, v in extra.items() if v is not None})
    with _locked():
        data = _load()
        data["waiting"].append(entry)
        write_json_atomic(state_path(), data)
    log(f"LOCKOUT {provider}: {reason}; resume at {fmt_clock(resume_at)}")
    return entry["id"]


def update_waiter(waiter_id: str, **fields: Any) -> None:
    with _locked():
        data = _load()
        for w in data["waiting"]:
            if w.get("id") == waiter_id:
                w.update(fields)
        write_json_atomic(state_path(), data)


def remove_waiter(waiter_id: str, outcome: str = "resumed") -> None:
    with _locked():
        data = _load()
        gone = [w for w in data["waiting"] if w.get("id") == waiter_id]
        data["waiting"] = [w for w in data["waiting"] if w.get("id") != waiter_id]
        write_json_atomic(state_path(), data)
    for w in gone:
        log(f"{outcome.upper()} {w.get('provider')}")


def request_resume_now() -> None:
    p = resume_flag_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(str(now()))
    # Make sure mtime moves even on coarse-resolution filesystems.
    t = now()
    os.utime(p, (t, t))


def resume_requested_since(ts: float) -> bool:
    try:
        return resume_flag_path().stat().st_mtime >= ts
    except OSError:
        return False


def log(line: str) -> None:
    import datetime as dt

    try:
        home = config.home()
        home.mkdir(parents=True, exist_ok=True)
        with open(home / "log.txt", "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now().isoformat(timespec='seconds')} {line}\n")
    except OSError:
        pass
