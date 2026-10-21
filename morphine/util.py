"""Small helpers shared across modules. Python 3.9+ standard library only."""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Optional


def now() -> float:
    return time.time()


def parse_ts(value: Any) -> Optional[float]:
    """Parse a reset timestamp into epoch seconds.

    Accepts epoch seconds, epoch milliseconds, numeric strings and ISO-8601
    strings (with 'Z', offsets, or long fractional seconds).
    """
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 1e12:  # milliseconds
            v /= 1000.0
        return v if v > 0 else None
    if isinstance(value, str):
        s = value.strip()
        if re.fullmatch(r"\d+(\.\d+)?", s):
            return parse_ts(float(s))
        s = s.replace("Z", "+00:00").replace("z", "+00:00")
        # Python 3.9 fromisoformat accepts at most 6 fractional digits.
        s = re.sub(r"(\.\d{6})\d+", r"\1", s)
        # '+0000' -> '+00:00'
        s = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", s)
        try:
            d = _dt.datetime.fromisoformat(s)
        except ValueError:
            return None
        if d.tzinfo is None:
            d = d.replace(tzinfo=_dt.timezone.utc)
        return d.timestamp()
    return None


def fmt_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h >= 24:
        d, h = divmod(h, 24)
        return f"{d}d {h}h {m:02d}m"
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def fmt_clock(ts: float) -> str:
    d = _dt.datetime.fromtimestamp(ts)
    if d.date() == _dt.date.today():
        return d.strftime("%H:%M")
    return d.strftime("%m-%d %H:%M")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def find_dicts_with(obj: Any, key: str):
    """Yield every dict (recursively) that contains `key`."""
    if isinstance(obj, dict):
        if key in obj:
            yield obj
        for v in obj.values():
            yield from find_dicts_with(v, key)
    elif isinstance(obj, list):
        for v in obj:
            yield from find_dicts_with(v, key)


def window_name_from_minutes(minutes: Optional[float], fallback: str) -> str:
    if not minutes:
        return fallback
    if minutes <= 6 * 60:
        return "5h"
    if minutes <= 26 * 60:
        return "1d"
    return "7d"
