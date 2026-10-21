"""Google Antigravity CLI (``agy``). EXPERIMENTAL.

The running CLI serves a local Connect-RPC endpoint; ``GetUserStatus`` returns a
``remainingFraction`` and ``resetTime`` per model pool for the five-hour window.
The weekly window is not exposed. This is undocumented and may break.
"""

from __future__ import annotations

import json
import platform
import re
import ssl
import subprocess
import urllib.request
from typing import Any, Dict, List

from morphine.adapters.base import Adapter, Usage, Window
from morphine.util import find_dicts_with, parse_ts

PATH = "/exa.language_server_pb.LanguageServerService/GetUserStatus"
BODY = {"metadata": {"ideName": "antigravity", "extensionName": "antigravity", "locale": "en"}}
PROC_RE = re.compile(r"agy|antigravity|language_server", re.I)


def _listening_ports() -> List[int]:
    ports: List[int] = []
    try:
        if platform.system() == "Darwin":
            out = subprocess.run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"],
                                 capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines()[1:]:
                cols = line.split()
                if len(cols) < 9 or not PROC_RE.search(cols[0]):
                    continue
                m = re.search(r"127\.0\.0\.1:(\d+)|\[::1\]:(\d+)|localhost:(\d+)", cols[8])
                if m:
                    ports.append(int(next(g for g in m.groups() if g)))
        else:
            out = subprocess.run(["ss", "-tlnp"], capture_output=True, text=True, timeout=5).stdout
            for line in out.splitlines():
                if not PROC_RE.search(line):
                    continue
                m = re.search(r"127\.0\.0\.1:(\d+)", line)
                if m:
                    ports.append(int(m.group(1)))
    except (OSError, subprocess.SubprocessError):
        pass
    return sorted(set(ports))


def _probe(port: int, timeout: float = 2.0) -> Any:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE  # loopback-only, self-signed by the CLI
    for scheme in ("https", "http"):
        req = urllib.request.Request(
            f"{scheme}://127.0.0.1:{port}{PATH}", data=json.dumps(BODY).encode(),
            headers={"Content-Type": "application/json", "Connect-Protocol-Version": "1"},
            method="POST",
        )
        try:
            kw = {"context": ctx} if scheme == "https" else {}
            with urllib.request.urlopen(req, timeout=timeout, **kw) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            continue
    return None


def usage_from_status(data: Any) -> Usage:
    pools = list(find_dicts_with(data, "remainingFraction"))
    if not pools:
        return Usage(provider="antigravity", source="local-rpc", error="no remainingFraction in GetUserStatus")
    worst = min(pools, key=lambda p: float(p.get("remainingFraction") or 0))
    frac = float(worst.get("remainingFraction") or 0)
    win = Window(name="5h", used_pct=round((1.0 - frac) * 100.0, 2),
                 resets_at=parse_ts(worst.get("resetTime")))
    return Usage(provider="antigravity", windows=[win], source="local-rpc",
                 note="weekly limit not exposed by Antigravity")


class AntigravityAdapter(Adapter):
    id = "antigravity"
    name = "Antigravity"

    def fetch(self, cfg: Dict[str, Any]) -> Usage:
        ports = _listening_ports()
        if not ports:
            return Usage(provider="antigravity", source="local-rpc", error="agy is not running")
        for port in ports:
            data = _probe(port)
            if data is not None and "remainingFraction" in json.dumps(data):
                return usage_from_status(data)
        return Usage(provider="antigravity", source="local-rpc", error="no agy port answered GetUserStatus")
