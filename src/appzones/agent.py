"""Talking to zone-init, the agent inside a zone, over its host-made socket."""
from __future__ import annotations

import json
import socket
from pathlib import Path

import zonecfg

from .common import die
from . import common


def agent_path(name: str) -> Path:
    """Where the host side of zone NAME's agent socket lives (never in the zone)."""
    if not zonecfg.NAME_RE.fullmatch(name):
        die(f"not a zone name: {name!r}")
    return common.RT / name / "agent.sock"


# ── client ─────────────────────────────────────────────────────────────────
def request(name: str, req: dict, fds: list[int] | None = None, timeout: float | None = 5.0) -> socket.socket:
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(agent_path(name)))  # host side only, see agent_listener
    data = (json.dumps(req) + "\n").encode()
    if fds:
        socket.send_fds(s, [data], fds)
    else:
        s.sendall(data)
    return s


def readline(s: socket.socket, buf: bytes = b"") -> tuple[dict, bytes]:
    """One JSON line; returns it and any bytes that came after it (pass those
    back as BUF for the next line: two replies can arrive in one read)."""
    while b"\n" not in buf:
        chunk = s.recv(65536)
        if not chunk:
            break
        buf += chunk
    line, _, rest = buf.partition(b"\n")
    return (json.loads(line.decode()) if line.strip() else {}), rest


def ping(name: str) -> dict | None:
    try:
        with request(name, {"op": "ping"}, timeout=1.0) as s:
            return readline(s)[0]
    except (OSError, ValueError):
        return None


def send_run(name: str, argv: list[str], extra_env: dict | None = None, cwd: str | None = None,
             bus: str = "host") -> int:
    with request(name, {"op": "run", "argv": argv, "env": extra_env or {}, "cwd": cwd, "bus": bus}) as s:
        r = readline(s)[0]
    if not r.get("ok"):
        raise OSError(r.get("error", "run failed"))
    return r["pid"]


def zone_procs(name: str) -> list[list]:
    """Processes in the zone other than bwrap (pid 1) and zone-init."""
    with request(name, {"op": "ps"}) as s:
        r = readline(s)[0]
    init = r.get("init")
    return [p for p in r.get("procs", []) if p[0] not in (1, init)]


# ── runtime mounts ─────────────────────────────────────────────────────────
def zone_request(name: str, req: dict) -> dict:
    """One request to zone-init; {} if it does not answer or know the op."""
    try:
        with request(name, req, timeout=10) as s:
            r = readline(s)[0]
        return r if r.get("ok") else {}
    except (OSError, ValueError):
        return {}
