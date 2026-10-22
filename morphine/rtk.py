"""rtk (Rust Token Killer) integration.

rtk filters noisy shell output (git, test runners, builds, grep, cat…) before it
reaches the agent's context. Morphine doesn't reimplement it; it

* detects whether rtk is installed and wired into Claude Code / Codex,
* sets it up with `morphine rtk setup` (runs rtk's own `rtk init`),
* shows how many tokens rtk has saved in the menu bar panel, and
* in economy mode, tells the agent to route searches and big-file skims through
  rtk too (rtk's hook only sees the Bash tool; built-in Read/Grep/Glob bypass it).

https://github.com/rtk-ai/rtk
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from morphine import config
from morphine.util import now, read_json, write_json_atomic

INSTALL_HINT = "brew install rtk"
CACHE_TTL = 600  # `rtk gain` is cheap but there's no need to run it every 2 s


def find_rtk() -> Optional[str]:
    exe = shutil.which("rtk")
    if exe:
        return exe
    home = Path.home()
    for p in (Path("/opt/homebrew/bin/rtk"), Path("/usr/local/bin/rtk"),
              home / ".local/bin/rtk", home / ".cargo/bin/rtk"):
        if os.access(p, os.X_OK):
            return str(p)
    return None


def _claude_wired() -> bool:
    cdir = Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser()
    s = cdir / "settings.json"
    try:
        return "rtk" in s.read_text(encoding="utf-8")
    except OSError:
        return False


def _codex_wired() -> bool:
    cdir = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    try:
        return (cdir / "RTK.md").exists() and "RTK.md" in (cdir / "AGENTS.md").read_text(encoding="utf-8")
    except OSError:
        return False


def _find_saved(obj: Any) -> Optional[float]:
    """Best-effort: the largest number under a key that mentions both 'saved' and 'token'."""
    best = None
    if isinstance(obj, dict):
        for k, v in obj.items():
            key = str(k).lower()
            if isinstance(v, (int, float)) and not isinstance(v, bool) and "sav" in key and "token" in key:
                best = v if best is None else max(best, v)
            else:
                sub = _find_saved(v)
                if sub is not None:
                    best = sub if best is None else max(best, sub)
    elif isinstance(obj, list):
        for v in obj:
            sub = _find_saved(v)
            if sub is not None:
                best = sub if best is None else max(best, sub)
    return best


def _saved_tokens(exe: str) -> Optional[int]:
    try:
        out = subprocess.run([exe, "gain", "--all", "--format", "json"], capture_output=True, text=True, timeout=5)
        if out.returncode != 0 or not out.stdout.strip():
            return None
        v = _find_saved(json.loads(out.stdout))
        return int(v) if v is not None else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _cache_path() -> Path:
    return config.home() / "cache" / "rtk.json"


def status(refresh: bool = False) -> Dict[str, Any]:
    """{installed, path, claude, codex, saved_tokens, hint} — cached for 10 min."""
    cached = read_json(_cache_path())
    if not refresh and isinstance(cached, dict) and now() - float(cached.get("checked_at", 0)) < CACHE_TTL:
        return cached
    exe = find_rtk()
    st: Dict[str, Any] = {
        "installed": bool(exe), "path": exe,
        "claude": _claude_wired(), "codex": _codex_wired(),
        "saved_tokens": _saved_tokens(exe) if exe else None,
        "checked_at": now(),
    }
    if not exe:
        st["hint"] = f"{INSTALL_HINT}, then: morphine rtk setup"
    elif not (st["claude"] or st["codex"]):
        st["hint"] = "morphine rtk setup"
    try:
        write_json_atomic(_cache_path(), st)
    except OSError:
        pass
    return st


def cached_status() -> Optional[Dict[str, Any]]:
    d = read_json(_cache_path())
    return d if isinstance(d, dict) else None


def wired_for(provider: str) -> bool:
    st = cached_status() or {}
    return bool(st.get("installed") and st.get(provider))


def setup(targets: Optional[List[str]] = None) -> int:
    """Run rtk's own installers. Output and any prompts go straight to the terminal."""
    exe = find_rtk()
    if not exe:
        print(f"rtk is not installed. Install it first:\n  {INSTALL_HINT}\nthen run: morphine rtk setup")
        return 1
    targets = targets or []
    if not targets:
        if Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser().exists() or shutil.which("claude"):
            targets.append("claude")
        if Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser().exists() or shutil.which("codex"):
            targets.append("codex")
    rc = 0
    for t in targets:
        cmd = [exe, "init", "-g"] + (["--codex"] if t == "codex" else [])
        print(f"[{t}] {' '.join(cmd)}")
        try:
            rc |= subprocess.call(cmd)
        except OSError as e:
            print(f"  failed: {e}")
            rc = 1
    status(refresh=True)
    if "claude" in targets:
        print("Restart Claude Code so rtk's hook loads.")
    return rc
