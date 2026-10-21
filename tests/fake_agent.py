"""A pretend agent CLI for wrapper tests: hits a limit, then waits for 'continue'."""

import sys

print("\x1b[1mfake agent ready\x1b[0m", flush=True)
print("Working... \x1b[31mYou've hit your usage limit.\x1b[0m Try again in 2 seconds.", flush=True)
line = sys.stdin.readline().strip()
if line == "continue":
    print("RESUMED-OK", flush=True)
    sys.exit(0)
print(f"UNEXPECTED: {line!r}", flush=True)
sys.exit(3)
