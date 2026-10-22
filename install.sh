#!/usr/bin/env bash
# One-shot installer.
#   ./install.sh                 # CLI + hooks for every agent CLI found, + menu bar app on macOS
#   ./install.sh --cli-only      # skip building the menu bar app
#   ./install.sh claude codex    # only wire these agents
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
CLI_ONLY=0
TARGETS=()
for a in "$@"; do
  case "$a" in
    --cli-only) CLI_ONLY=1 ;;
    *) TARGETS+=("$a") ;;
  esac
done

command -v python3 >/dev/null || { echo "python3 is required (macOS: xcode-select --install)"; exit 1; }

if [[ "$(uname)" == "Darwin" && $CLI_ONLY -eq 0 ]] && command -v swift >/dev/null; then
  "$ROOT/app/scripts/bundle.sh" --install
  MORPHINE="$HOME/.local/bin/morphine"
else
  mkdir -p "$HOME/.local/bin"
  ln -sf "$ROOT/bin/morphine" "$HOME/.local/bin/morphine"
  MORPHINE="$HOME/.local/bin/morphine"
  [[ "$(uname)" == "Darwin" && $CLI_ONLY -eq 0 ]] && echo "! swift not found — skipped the menu bar app (xcode-select --install)"
fi

if [[ ${#TARGETS[@]} -eq 0 ]]; then
  for t in claude codex gemini grok; do
    case $t in
      claude) [[ -d "$HOME/.claude" ]] || command -v claude >/dev/null && TARGETS+=(claude) ;;
      codex) [[ -d "$HOME/.codex" ]] || command -v codex >/dev/null && TARGETS+=(codex) ;;
      gemini) command -v gemini >/dev/null && TARGETS+=(gemini) ;;
      grok) command -v grok >/dev/null && TARGETS+=(grok) ;;
    esac
  done
fi

if [[ ${#TARGETS[@]} -gt 0 ]]; then
  "$MORPHINE" install "${TARGETS[@]}"
else
  echo "No agent CLIs detected. Later: morphine install claude codex gemini grok"
fi

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo; echo "Add to ~/.zshrc:  export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
echo; "$MORPHINE" status || true
if ! command -v rtk >/dev/null; then
  echo; echo "Tip: rtk compresses shell output for fewer tokens:  brew install rtk  then  morphine rtk setup"
fi
