"""Built-in Tier 0 probes (PRD §3).

Deterministic, stdlib-only, no LLM. Each maps to a config `type:` id. Users can
add probes via config; these cover the PRD's built-in list:

- process_alive      — Hermes process / session alive
- heartbeat_fresh    — heartbeat file timestamp freshness
- log_error_count    — error-pattern count over the last N log lines
- api_last_success   — model API connectivity via last-success timestamp
- gateway_responds   — messaging gateway responds (command probe)
- disk_free          — disk free headroom
- mem_free           — memory headroom

Process/memory checks read /proc directly (Linux) to avoid a psutil dependency,
degrading to WARN where the platform doesn't expose it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import time
from pathlib import Path

from aesculap.probes.base import Probe, ProbeResult, register_probe


@register_probe
class ProcessAliveProbe(Probe):
    """FAIL if no running process matches `pattern` (PRD §3, §2 liveness).

    Reads /proc/<pid>/cmdline so it catches OOM-killed processes that leave no
    log trace (PRD §2 rationale for a separate liveness check).
    """

    type_id = "process_alive"

    def run(self) -> ProbeResult:
        pattern = self.params.get("pattern", "")
        if not pattern:
            return self.warn("process_alive: no `pattern` configured")
        rx = re.compile(pattern)
        proc = Path("/proc")
        if not proc.is_dir():
            return self.warn("process_alive: /proc unavailable on this platform")
        matches = []
        for entry in proc.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ")
            except OSError:
                continue
            text = cmdline.decode("utf-8", "replace").strip()
            if text and rx.search(text):
                matches.append((entry.name, text))
        if matches:
            return self.ok(
                f"{len(matches)} process(es) match {pattern!r}; "
                f"e.g. pid {matches[0][0]}",
                match_count=len(matches),
                pids=[m[0] for m in matches],
            )
        return self.fail(f"no running process matches {pattern!r}", match_count=0)


@register_probe
class HeartbeatFreshProbe(Probe):
    """FAIL if a heartbeat file's mtime is older than `max_age_seconds`."""

    type_id = "heartbeat_fresh"

    def run(self) -> ProbeResult:
        path = self.params.get("path", "")
        max_age = float(self.params.get("max_age_seconds", 300))
        if not path:
            return self.warn("heartbeat_fresh: no `path` configured")
        p = Path(path)
        if not p.exists():
            return self.fail(f"heartbeat file missing: {path}")
        age = time.time() - p.stat().st_mtime
        if age > max_age:
            return self.fail(
                f"heartbeat stale: {age:.0f}s old (max {max_age:.0f}s)",
                age_seconds=age,
            )
        return self.ok(f"heartbeat fresh: {age:.0f}s old", age_seconds=age)


@register_probe
class LogErrorCountProbe(Probe):
    """Count error-pattern hits in the last N log lines (PRD §3).

    WARN/FAIL thresholds are configurable. Returns the matched lines as evidence
    so triage (later) and the audit log get concrete context.
    """

    type_id = "log_error_count"

    def run(self) -> ProbeResult:
        path = self.params.get("path", "")
        lines_n = int(self.params.get("lines", 200))
        patterns = self.params.get("patterns", ["Traceback", "CRITICAL"])
        fail_threshold = int(self.params.get("fail_threshold", 1))
        warn_threshold = int(self.params.get("warn_threshold", 0))
        if not path:
            return self.warn("log_error_count: no `path` configured")
        p = Path(path)
        if not p.is_file():
            return self.fail(f"log file missing: {path}")
        compiled = [re.compile(pat) for pat in patterns]
        tail = _tail_lines(p, lines_n)
        hits = [ln for ln in tail if any(rx.search(ln) for rx in compiled)]
        count = len(hits)
        evidence = "\n".join(hits[-5:])  # last few matching lines
        if count >= fail_threshold:
            return self.fail(
                f"{count} error-pattern hit(s) in last {lines_n} lines\n{evidence}",
                hit_count=count,
            )
        if warn_threshold and count >= warn_threshold:
            return self.warn(f"{count} error-pattern hit(s)", hit_count=count)
        return self.ok(f"{count} error-pattern hit(s)", hit_count=count)


@register_probe
class ApiLastSuccessProbe(Probe):
    """FAIL if the last successful model-API call is older than a threshold.

    Reads a timestamp file the agent updates on each success (PRD §3: "last
    success timestamp"), avoiding an active paid ping. If the file is absent,
    WARN rather than FAIL — absence isn't proof of failure.
    """

    type_id = "api_last_success"

    def run(self) -> ProbeResult:
        path = self.params.get("path", "")
        max_age = float(self.params.get("max_age_seconds", 3600))
        if not path:
            return self.warn("api_last_success: no `path` configured")
        p = Path(path)
        if not p.exists():
            return self.warn(f"api_last_success: timestamp file absent: {path}")
        age = time.time() - p.stat().st_mtime
        if age > max_age:
            return self.fail(
                f"last API success {age:.0f}s ago (max {max_age:.0f}s)",
                age_seconds=age,
            )
        return self.ok(f"last API success {age:.0f}s ago", age_seconds=age)


@register_probe
class GatewayRespondsProbe(Probe):
    """Run a configured health command for the messaging gateway (PRD §3).

    Generic: `command` is a shell command whose exit code 0 == OK. This keeps
    the probe provider-agnostic (the gateway could be any platform).
    """

    type_id = "gateway_responds"

    def run(self) -> ProbeResult:
        command = self.params.get("command", "")
        timeout = float(self.params.get("timeout_seconds", 10))
        if not command:
            return self.warn("gateway_responds: no `command` configured")
        try:
            proc = subprocess.run(
                command, shell=True, capture_output=True, text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return self.fail(f"gateway health command timed out after {timeout}s")
        if proc.returncode == 0:
            return self.ok("gateway health command exit 0")
        return self.fail(
            f"gateway health command exit {proc.returncode}: "
            f"{(proc.stderr or proc.stdout).strip()[:200]}",
            exit_code=proc.returncode,
        )


@register_probe
class DiskFreeProbe(Probe):
    """WARN/FAIL on low disk headroom (PRD §3 'disk slowly filling')."""

    type_id = "disk_free"

    def run(self) -> ProbeResult:
        path = self.params.get("path", "/")
        warn_pct = float(self.params.get("warn_percent", 90))
        fail_pct = float(self.params.get("fail_percent", 97))
        try:
            usage = shutil.disk_usage(path)
        except OSError as e:
            return self.warn(f"disk_free: cannot stat {path}: {e}")
        used_pct = usage.used / usage.total * 100 if usage.total else 0
        ev = f"{used_pct:.1f}% used at {path}"
        if used_pct >= fail_pct:
            return self.fail(ev, used_percent=used_pct)
        if used_pct >= warn_pct:
            return self.warn(ev, used_percent=used_pct)
        return self.ok(ev, used_percent=used_pct)


@register_probe
class MemFreeProbe(Probe):
    """WARN/FAIL on low available memory, read from /proc/meminfo (Linux)."""

    type_id = "mem_free"

    def run(self) -> ProbeResult:
        warn_pct = float(self.params.get("warn_percent", 90))
        fail_pct = float(self.params.get("fail_percent", 97))
        meminfo = Path("/proc/meminfo")
        if not meminfo.is_file():
            return self.warn("mem_free: /proc/meminfo unavailable")
        vals: dict[str, int] = {}
        for line in meminfo.read_text().splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                num = parts[1].strip().split()
                if num and num[0].isdigit():
                    vals[parts[0]] = int(num[0])  # kB
        total = vals.get("MemTotal", 0)
        available = vals.get("MemAvailable", 0)
        if not total:
            return self.warn("mem_free: MemTotal not found")
        used_pct = (total - available) / total * 100
        ev = f"{used_pct:.1f}% memory used"
        if used_pct >= fail_pct:
            return self.fail(ev, used_percent=used_pct)
        if used_pct >= warn_pct:
            return self.warn(ev, used_percent=used_pct)
        return self.ok(ev, used_percent=used_pct)


def _tail_lines(path: Path, n: int) -> list[str]:
    """Return the last `n` lines of a file efficiently (reads from the end)."""
    if n <= 0:
        return []
    avg = 200  # assumed bytes/line; grow the read window until we have enough
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        block = min(size, n * avg)
        data = b""
        while block <= size:
            fh.seek(size - block)
            data = fh.read(block)
            if data.count(b"\n") > n or block == size:
                break
            block = min(size, block * 2)
    lines = data.decode("utf-8", "replace").splitlines()
    return lines[-n:]
