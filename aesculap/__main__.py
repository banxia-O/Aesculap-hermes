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


def _cmd_config(args: argparse.Namespace) -> int:
    """Load and validate a config file; report the result."""
    try:
        cfg = load_config(args.path)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    print(f"config OK: tier={cfg.scope.tier} mode={cfg.mode} "
          f"probes={len(cfg.probes)} enabled={cfg.enabled}")
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
    pc.add_argument("path", help="path to config.yaml")
    pc.set_defaults(func=_cmd_config)

    for name, help_text in (
        ("install", "run the install wizard"),
        ("start", "start the daemon"),
        ("stop", "stop the daemon"),
        ("status", "show daemon + open-issue status"),
        ("probe", "run the Tier 0 probe suite once and print results"),
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
