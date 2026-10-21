"""Command-line entry point."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from typing import Any, Dict, List, Optional

from morphine import __version__, config, state
from morphine.adapters import ADAPTERS, Usage, cached_usage, enabled_providers, get_usage
from morphine.adapters.base import shortest_window
from morphine.adapters.claude import statusline_cache_path, usage_from_rate_limits
from morphine.util import fmt_clock, fmt_duration, now, read_json, write_json_atomic

# What each host expects on stdout from an "allow" hook.
ALLOW_OUTPUT = {"claude": "", "codex": "", "grok": "", "gemini": "{}"}


# ------------------------------------------------------------------ status

def collect(cfg: Dict[str, Any], cached_only: bool) -> Dict[str, Any]:
    t = now()
    providers = []
    for pid in enabled_providers(cfg):
        if cached_only:
            # Fast path for the menu bar (polled every couple of seconds): the
            # newest of the last full reading and a cheap local one.
            cands = [cached_usage(pid)]
            try:
                cands.append(ADAPTERS[pid].fetch_local(cfg))
            except Exception:  # noqa: BLE001
                pass
            cands = [c for c in cands if c is not None]
            good = [c for c in cands if c.ok]
            u = max(good, key=lambda c: c.fetched_at) if good else (cands[0] if cands else None)
            u = u or Usage(provider=pid, error="no reading yet")
        else:
            u = get_usage(pid, cfg)
        d = u.to_dict()
        d["id"] = pid
        d["name"] = ADAPTERS[pid].name
        d["preemptive"] = ADAPTERS[pid].supports_preemptive
        for w in d["windows"]:
            win = next(x for x in u.windows if x.name == w["name"])
            w["used_now"] = win.effective_used(t)
            w["remaining"] = win.remaining(t)
        disp = shortest_window(u.windows, t)
        d["display_window"] = disp.name if disp else None
        providers.append(d)
    return {
        "version": __version__,
        "generated_at": t,
        "threshold_pct": cfg.get("threshold_pct"),
        "windows": cfg.get("windows"),
        "keep_awake": cfg.get("keep_awake"),
        "providers": providers,
        "waiting": state.waiting(),
    }


def _bar(pct: Optional[float], width: int = 12) -> str:
    if pct is None:
        return "·" * width
    fill = int(round(width * max(0.0, min(100.0, pct)) / 100))
    return "█" * fill + "░" * (width - fill)


def print_status(data: Dict[str, Any]) -> None:
    t = data["generated_at"]
    print(f"Morphine {data['version']} — lockout when < {data['threshold_pct']}% left\n")
    for p in data["providers"]:
        head = f"{p['name']:<12}"
        if not p["windows"]:
            msg = p.get("note") or p.get("error") or "no data"
            print(f"{head} {msg}")
            continue
        first = True
        for w in p["windows"]:
            used = w.get("used_now")
            reset = w.get("resets_at")
            when = f"resets {fmt_clock(reset)} (in {fmt_duration(reset - t)})" if reset and reset > t else ""
            est = " ~est" if w.get("estimated") else ""
            used_s = f"{used:5.1f}%" if used is not None else "   ?  "
            print(f"{head if first else ' ' * 12} {w['name']:>3} {_bar(used)} {used_s}{est}  {when}")
            first = False
        if p.get("note"):
            print(f"{' ' * 12}     {p['note']}")
    if data["waiting"]:
        print("\nIn lockout:")
        for w in data["waiting"]:
            left = w["resume_at"] - t
            print(f"  ⏸ {w['provider']:<11} {w.get('reason', '')} → resumes {fmt_clock(w['resume_at'])} "
                  f"(in {fmt_duration(left)})")


# ------------------------------------------------------------------- hook

def cmd_hook(args: argparse.Namespace) -> int:
    provider = args.provider
    raw = ""
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
    except (OSError, ValueError):
        raw = ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    event = payload.get("hook_event_name") or payload.get("hookEventName") or ""
    # Grok Build also executes hooks from ~/.claude/settings.json. Don't let the
    # Claude hook judge Grok by Claude's quota.
    if os.environ.get("GROK_HOOK_EVENT") or os.environ.get("GROK_SESSION_ID"):
        provider = "grok"
    if provider == "antigravity":
        allow = "{}" if "Invocation" in str(event) else '{"allow_tool": true}'
    else:
        allow = ALLOW_OUTPUT.get(provider, "")
    try:
        cfg = config.load()
        if provider in ADAPTERS and ADAPTERS[provider].supports_preemptive \
                and config.provider_cfg(cfg, provider).get("enabled", True):
            from morphine.guard import guard

            meta = {
                "session": payload.get("session_id") or payload.get("sessionId"),
                "cwd": payload.get("cwd") or os.getcwd(),
                "event": event or None,
            }
            guard(provider, cfg, meta)
    except Exception as e:  # noqa: BLE001 - never break the agent
        state.log(f"hook error ({provider}): {e!r}")
    if allow:
        print(allow)
    return 0


# ------------------------------------------------------------- statusline

def cmd_statusline(args: argparse.Namespace) -> int:
    raw = sys.stdin.read() if not sys.stdin.isatty() else ""
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        payload = {}
    rl = payload.get("rate_limits") if isinstance(payload, dict) else None
    ours = ""
    if isinstance(rl, dict):
        u = usage_from_rate_limits(rl, "statusline")
        if u.ok:
            d = u.to_dict()
            write_json_atomic(statusline_cache_path(), d)
            # Also refresh the main cache so hooks see this reading immediately.
            write_json_atomic(config.home() / "cache" / "claude.json", dict(d, checked_at=now()))
            parts = []
            w = shortest_window(u.windows)
            if w is not None:
                parts.append(f"{w.name} {max(0.0, 100 - w.effective_used()):.0f}% left")
            if parts:
                ours = "💉 " + " · ".join(parts)
    locked = [w for w in state.waiting() if w.get("provider") == "claude"]
    if locked:
        ours = f"⏸ Morphine {fmt_duration(locked[0]['resume_at'] - now())}"
    theirs = ""
    if args.wrap:
        try:
            theirs = subprocess.run(args.wrap, shell=True, input=raw, capture_output=True,
                                    text=True, timeout=10).stdout.rstrip("\n")
        except (OSError, subprocess.SubprocessError):
            theirs = ""
    print("  │  ".join(x for x in (theirs, ours) if x))
    return 0


# ------------------------------------------------------------------ others

def cmd_status(args: argparse.Namespace) -> int:
    cfg = config.load()
    if args.refresh:
        cfg = dict(cfg, cache_ttl_sec=0)
    data = collect(cfg, cached_only=args.cached)
    if args.json:
        print(json.dumps(data, ensure_ascii=False))
    else:
        print_status(data)
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    from morphine.wrapper import run

    cmd = list(args.cmd)
    if cmd and cmd[0] == "--":
        cmd = cmd[1:]
    if not cmd:
        print("usage: morphine run [--provider NAME] -- <command> [args...]", file=sys.stderr)
        return 2
    if cmd[0] == "command" and len(cmd) > 1:  # allow `alias grok='morphine run -- command grok'`
        cmd = cmd[1:]
    provider = args.provider or os.path.basename(cmd[0])
    return run(cmd, provider, config.load())


def cmd_install(args: argparse.Namespace) -> int:
    from morphine import install

    targets: List[str] = args.targets or ["all"]
    if "all" in targets:
        targets = list(install.INSTALLERS)
    for t in targets:
        if t not in install.INSTALLERS:
            print(f"unknown target: {t} (choose from {', '.join(install.INSTALLERS)})", file=sys.stderr)
            return 2
        if t == "claude":
            notes = install.install_claude(hooks=not args.no_hooks, statusline=not args.no_statusline,
                                           dry_run=args.dry_run)
        else:
            notes = install.INSTALLERS[t](dry_run=args.dry_run)
        print(f"[{t}]")
        for n in notes:
            print(f"  {n}")
    return 0


def cmd_uninstall(args: argparse.Namespace) -> int:
    from morphine import install

    for n in install.uninstall_all(dry_run=args.dry_run):
        print(n)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    state.request_resume_now()
    n = len(state.waiting())
    print(f"resume requested ({n} agent{'s' if n != 1 else ''} waiting)")
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    if args.action == "set":
        if not args.key or args.value is None:
            print("usage: morphine config set KEY VALUE", file=sys.stderr)
            return 2
        v = config.set_value(args.key, args.value)
        print(f"{args.key} = {json.dumps(v)}")
    elif args.action == "path":
        print(config.config_path())
    else:
        cfg = config.load()
        if args.key:
            node: Any = cfg
            for part in args.key.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            print(json.dumps(node, ensure_ascii=False, indent=2))
        else:
            print(json.dumps(cfg, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="morphine", description="Sedate AI agents through usage cooldowns.")
    p.add_argument("--version", action="version", version=f"morphine {__version__}")
    sub = p.add_subparsers(dest="command")

    s = sub.add_parser("status", help="show usage for every provider")
    s.add_argument("--json", action="store_true")
    s.add_argument("--cached", action="store_true", help="only read caches (fast, no network)")
    s.add_argument("--refresh", action="store_true", help="ignore caches")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("hook", help="hook entry point (called by the agent CLI)")
    s.add_argument("provider", choices=sorted(ADAPTERS))
    s.set_defaults(func=cmd_hook)

    s = sub.add_parser("statusline", help="Claude Code status line (feeds usage to Morphine)")
    s.add_argument("--wrap", help="your previous status line command; its output is kept")
    s.set_defaults(func=cmd_statusline)

    s = sub.add_parser("run", help="run a CLI under the PTY wrapper")
    s.add_argument("--provider", help="label for the menu bar (default: command name)")
    s.add_argument("cmd", nargs=argparse.REMAINDER)
    s.set_defaults(func=cmd_run)

    s = sub.add_parser("install", help="add Morphine hooks to agent CLIs")
    s.add_argument("targets", nargs="*", help="claude codex gemini antigravity grok | all")
    s.add_argument("--dry-run", action="store_true")
    s.add_argument("--no-hooks", action="store_true", help="claude: status line only (hooks via plugin)")
    s.add_argument("--no-statusline", action="store_true")
    s.set_defaults(func=cmd_install)

    s = sub.add_parser("uninstall", help="remove Morphine hooks everywhere")
    s.add_argument("--dry-run", action="store_true")
    s.set_defaults(func=cmd_uninstall)

    s = sub.add_parser("resume", help="end every lockout now")
    s.set_defaults(func=cmd_resume)

    s = sub.add_parser("config", help="show or change settings")
    s.add_argument("action", nargs="?", choices=["get", "set", "path"], default="get")
    s.add_argument("key", nargs="?")
    s.add_argument("value", nargs="?")
    s.set_defaults(func=cmd_config)
    return p


def main(argv: Optional[List[str]] = None) -> int:
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "func", None):
        args = p.parse_args(["status"])
    return int(args.func(args) or 0)
