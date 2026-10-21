---
name: morphine
description: Set up, check or troubleshoot Morphine, the usage-cooldown guard that pauses Claude Code, Codex, Gemini CLI or Grok Build at zero token cost when the 5-hour/weekly usage window is nearly drained and resumes them after the reset. Use when the user asks about usage limits, waiting for a limit reset, auto-resuming after a limit, or Morphine's status or settings.
---

# Morphine

Morphine does its waiting **outside the model**: a hook (or PTY wrapper) holds the
agent, re-reads usage every `recheck_min` minutes (default 31, plus once at the
reported reset time) and lets it continue once the window has refilled, so no
tokens are spent while it waits. Never emulate this by polling usage from model turns — every check would cost
a turn. Use the `morphine` CLI instead.

## Find the CLI

Use `morphine` if it is on PATH. Otherwise try, in order:
`~/.local/bin/morphine`, `/Applications/Morphine.app/Contents/Resources/morphine/bin/morphine`,
or the plugin copy: `ls ~/.claude/plugins/cache/*/morphine/*/bin/morphine`.

## Common tasks

| User wants | Run |
|---|---|
| See usage for every agent | `morphine status` (`--json` for machine output) |
| Wire up agents | `morphine install claude codex gemini grok` (or `all`) |
| Installed as a Claude Code plugin | `morphine install claude --no-hooks` (status line only; the plugin already ships the hooks) |
| Change the trigger | `morphine config set threshold_pct 3` (remaining %, default 5) |
| Only watch the 5-hour window | `morphine config set windows '["5h"]'` |
| Change how often a sedated agent is re-checked | `morphine config set recheck_min 31` (minutes, default 31) |
| End a lockout now | `morphine resume` |
| Guard a CLI with no usage API | `morphine run --provider grok -- grok` |
| Remove everything | `morphine uninstall` |

Settings live in `~/.morphine/config.json`; the lockout log is `~/.morphine/log.txt`.

## What each agent supports

- **Claude Code** — pauses before the limit. Usage comes from the status line (`morphine install claude` adds it) or, with only a Claude Code login, from the Anthropic usage endpoint via the Keychain item "Claude Code-credentials".
- **Codex** — pauses before the limit. After installing, the user must open the Codex CLI (`brew install --cask codex`; the VS Code extension's bundled binary can't do this), run `/hooks`, and trust the two Morphine hooks once.
- **Gemini CLI** — usage is only an estimate, so it does not pause by default; use the wrapper, or `morphine config set providers.gemini.lockout_on_estimate true`.
- **Grok Build** — no usage data; use the wrapper (`alias grok='morphine run --provider grok -- command grok'`).

## Troubleshooting

- `morphine status` shows an error for a provider → that provider is unknown, and Morphine never blocks on unknown usage.
- Claude missing → install Claude Code, run `claude` once to log in, then `morphine status --refresh` in Terminal and click Always Allow on the Keychain prompt.
- Codex says "codex binary not found" → `morphine config set providers.codex.path "$(which codex)"`.
- Commands fail in zsh with `#` errors → don't paste trailing `# comments`.
- A lockout seems stuck → check `~/.morphine/state.json`, then `morphine resume`.
- While waiting, the agent's session is idle on purpose; the first call after resuming rebuilds the prompt cache, which costs a little more than usual.
