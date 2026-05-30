"""Aesculap CLI entry point.

Phase 1 wires up the subcommands that exist today (`config` validation) and
stubs the rest with clear "not yet implemented" messages so the surface is
stable while later phases fill them in. The daemon loop, probes, triage, and
install wizard arrive in subsequent phases per the implementation plan.
"""

from __future__ import annotations

import argparse
import sys

from aesculap import __version__
from aesculap.config import ConfigError, load_config

_NOT_YET = "not yet implemented (arrives in a later phase)"


def _load(args: argparse.Namespace):
    return load_config(args.config)


def _cmd_config(args: argparse.Namespace) -> int:
    """Load and validate a config file; report the result."""
    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    print(f"config OK: tier={cfg.scope.tier} mode={cfg.mode} "
          f"probes={len(cfg.probes)} enabled={cfg.enabled}")
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    """Run the Tier 0 probe suite once and print results (PRD §3)."""
    from aesculap.probes.base import ProbeStatus
    from aesculap.probes.registry import ProbeSuite

    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    suite = ProbeSuite.from_config(cfg.probes)
    results = suite.run_all()
    worst_fail = False
    for r in results:
        print(f"[{r.status.value:>4}] {r.name}: {r.evidence.splitlines()[0] if r.evidence else ''}")
        worst_fail = worst_fail or r.status is ProbeStatus.FAIL
    return 1 if worst_fail else 0


def _cmd_start(args: argparse.Namespace) -> int:
    """Run the daemon in the foreground (systemd runs it this way, PRD §2)."""
    from aesculap.audit.log import AuditLog
    from aesculap.daemon import Daemon

    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    import signal

    audit = AuditLog(cfg.audit_log_path or f"{cfg.state_dir or '/tmp'}/audit.jsonl")
    daemon = Daemon(cfg, audit)
    # systemd stops the unit with SIGTERM; shut down cleanly so the stop is
    # audited and detectors join (PRD §2 lifecycle).
    signal.signal(signal.SIGTERM, lambda *_: daemon._stop.set())
    signal.signal(signal.SIGINT, lambda *_: daemon._stop.set())
    daemon.run_forever()
    return 0


def _stub(name: str):
    def _run(args: argparse.Namespace) -> int:
        print(f"`aesculap {name}` {_NOT_YET}", file=sys.stderr)
        return 2
    return _run


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aesculap", description=__doc__)
    p.add_argument("--version", action="version", version=f"aesculap {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    pc = sub.add_parser("config", help="validate a config file")
    pc.add_argument("config", help="path to config.yaml")
    pc.set_defaults(func=_cmd_config)

    pp = sub.add_parser("probe", help="run the Tier 0 probe suite once")
    pp.add_argument("config", help="path to config.yaml")
    pp.set_defaults(func=_cmd_probe)

    ps = sub.add_parser("start", help="start the daemon (foreground)")
    ps.add_argument("config", help="path to config.yaml")
    ps.set_defaults(func=_cmd_start)

    for name, help_text in (
        ("install", "run the install wizard"),
        ("stop", "stop the daemon"),
        ("status", "show daemon + open-issue status"),
    ):
        sp = sub.add_parser(name, help=help_text)
        sp.set_defaults(func=_stub(name))
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
