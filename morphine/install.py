"""Wire Morphine into each CLI's hook system (idempotent, with backups)."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from morphine.util import read_json, write_json_atomic

# Hook timeouts: long enough to sit out a weekly reset (8 days).
WAIT_SEC = 8 * 24 * 3600
MARK = "morphine hook"


def morphine_cmd() -> str:
    """Absolute command that runs this Morphine install."""
    exe = shutil.which("morphine")
    if exe:
        return shlex.quote(os.path.realpath(exe))
    here = Path(__file__).resolve().parent.parent
    launcher = here / "bin" / "morphine"
    if launcher.exists():
        return shlex.quote(str(launcher))
    return f"{shlex.quote(sys.executable)} -m morphine"


def _backup(path: Path) -> Optional[Path]:
    if not path.exists():
        return None
    b = path.with_name(f"{path.name}.morphine-backup-{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, b)
    return b


def _has_mark(obj: Any) -> bool:
    return MARK in json.dumps(obj)


def _strip_ours(groups: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for g in groups or []:
        hooks = [h for h in g.get("hooks", []) if MARK not in str(h.get("command", ""))]
        if hooks:
            g = dict(g, hooks=hooks)
            out.append(g)
    return out


# ------------------------------------------------------------------ Claude

def claude_settings_path() -> Path:
    return Path(os.environ.get("CLAUDE_CONFIG_DIR", "~/.claude")).expanduser() / "settings.json"


def install_claude(hooks: bool = True, statusline: bool = True, dry_run: bool = False) -> List[str]:
    path = claude_settings_path()
    s = read_json(path, {}) or {}
    cmd = morphine_cmd()
    notes = []
    if hooks:
        h = s.setdefault("hooks", {})
        hook = {"type": "command", "command": f"{cmd} hook claude", "timeout": WAIT_SEC}
        h["PreToolUse"] = _strip_ours(h.get("PreToolUse", [])) + [{"matcher": "*", "hooks": [hook]}]
        h["UserPromptSubmit"] = _strip_ours(h.get("UserPromptSubmit", [])) + [{"hooks": [dict(hook)]}]
        notes.append("Claude hooks: PreToolUse + UserPromptSubmit")
    if statusline:
        cur = s.get("statusLine") or {}
        cur_cmd = cur.get("command", "") if isinstance(cur, dict) else ""
        if "morphine statusline" in cur_cmd or "morphine' statusline" in cur_cmd:
            notes.append("Claude status line already feeds Morphine")
        else:
            line = f"{cmd} statusline"
            if cur_cmd:
                line += f" --wrap {shlex.quote(cur_cmd)}"
                notes.append("Claude status line: wrapped your existing one")
            else:
                notes.append("Claude status line: installed")
            s["statusLine"] = {"type": "command", "command": line, "padding": 0}
    if not dry_run:
        b = _backup(path)
        write_json_atomic(path, s)
        if b:
            notes.append(f"backup: {b}")
    notes.append(f"edited {path}")
    return notes


# ------------------------------------------------------------------- Codex

def codex_config_path() -> Path:
    return Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"


CODEX_BLOCK = """
# >>> morphine >>>
[[hooks.PreToolUse]]
matcher = ".*"
[[hooks.PreToolUse.hooks]]
type = "command"
command = "{cmd} hook codex"
timeout = {timeout}
statusMessage = "Morphine: checking usage window"

[[hooks.UserPromptSubmit]]
[[hooks.UserPromptSubmit.hooks]]
type = "command"
command = "{cmd} hook codex"
timeout = {timeout}
# <<< morphine <<<
"""


def install_codex(dry_run: bool = False) -> List[str]:
    path = codex_config_path()
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    if "# >>> morphine >>>" in text:
        start = text.index("# >>> morphine >>>")
        end = text.index("# <<< morphine <<<", start) + len("# <<< morphine <<<")
        text = (text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")).strip("\n") + "\n"
    cmd = morphine_cmd().replace("\\", "\\\\").replace('"', '\\"')
    text = text.rstrip("\n") + "\n" + CODEX_BLOCK.format(cmd=cmd, timeout=WAIT_SEC)
    notes = []
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        b = _backup(path)
        path.write_text(text, encoding="utf-8")
        if b:
            notes.append(f"backup: {b}")
    notes += [f"edited {path}", "Codex: open `codex`, run /hooks and trust the two Morphine hooks once."]
    return notes


# ------------------------------------------------------------------ Gemini

def gemini_settings_path() -> Path:
    return Path(os.environ.get("GEMINI_HOME", "~/.gemini")).expanduser() / "settings.json"


def install_gemini(dry_run: bool = False) -> List[str]:
    path = gemini_settings_path()
    s = read_json(path, {}) or {}
    h = s.setdefault("hooks", {})
    hook = {"name": "morphine", "type": "command", "command": f"{morphine_cmd()} hook gemini",
            "timeout": WAIT_SEC * 1000}  # Gemini CLI timeouts are milliseconds
    h["BeforeTool"] = _strip_ours(h.get("BeforeTool", [])) + [{"matcher": ".*", "hooks": [hook]}]
    h["BeforeAgent"] = _strip_ours(h.get("BeforeAgent", [])) + [{"hooks": [dict(hook)]}]
    notes = []
    if not dry_run:
        b = _backup(path)
        write_json_atomic(path, s)
        if b:
            notes.append(f"backup: {b}")
    notes += [f"edited {path}",
              "Gemini usage is an estimate; for a hard stop use `morphine run --provider gemini -- gemini`."]
    return notes


# -------------------------------------------------------------------- Grok

def install_grok(dry_run: bool = False) -> List[str]:
    return [
        "Grok Build publishes no usage numbers, so Morphine guards it with the PTY wrapper.",
        "Add this to ~/.zshrc:",
        f"  alias grok='{morphine_cmd()} run --provider grok -- command grok'",
    ]


INSTALLERS = {
    "claude": install_claude,
    "codex": install_codex,
    "gemini": install_gemini,
    "grok": install_grok,
}


def uninstall_all(dry_run: bool = False) -> List[str]:
    notes = []
    for path, events in (
        (claude_settings_path(), ("PreToolUse", "UserPromptSubmit")),
        (gemini_settings_path(), ("BeforeTool", "BeforeAgent")),
    ):
        s = read_json(path)
        if not isinstance(s, dict) or not _has_mark(s):
            continue
        for ev in events:
            if ev in s.get("hooks", {}):
                s["hooks"][ev] = _strip_ours(s["hooks"][ev])
                if not s["hooks"][ev]:
                    del s["hooks"][ev]
        sl = s.get("statusLine")
        if isinstance(sl, dict) and "statusline" in sl.get("command", "") and "morphine" in sl.get("command", ""):
            import re

            m = re.search(r"--wrap (.+)$", sl["command"])
            if m:
                s["statusLine"]["command"] = shlex.split(m.group(1))[0]
            else:
                del s["statusLine"]
        if not dry_run:
            _backup(path)
            write_json_atomic(path, s)
        notes.append(f"cleaned {path}")
    cp = codex_config_path()
    if cp.exists() and "# >>> morphine >>>" in cp.read_text(encoding="utf-8"):
        text = cp.read_text(encoding="utf-8")
        start = text.index("# >>> morphine >>>")
        end = text.index("# <<< morphine <<<", start) + len("# <<< morphine <<<")
        if not dry_run:
            _backup(cp)
            cp.write_text((text[:start].rstrip("\n") + "\n" + text[end:].lstrip("\n")), encoding="utf-8")
        notes.append(f"cleaned {cp}")
    return notes or ["nothing to remove"]
