"""Aesculap CLI entry point.

Subcommands:
  install          run the interactive install wizard (PRD §9.1, §5.1)
  install-systemd  render + (optionally) write the systemd unit (PRD §2)
  config           validate a config file
  probe            run the Tier 0 probe suite once (PRD §3)
  start            run the daemon in the foreground (systemd runs it this way)
  status           show daemon mode + recent audit + open issues
  enable/disable   flip the master switch (PRD §10.1)
  mode             switch between fix and observe (PRD §10.2)
"""

from __future__ import annotations

import argparse
import sys

import yaml

from aesculap import __version__
from aesculap.config import ConfigError, load_config


def _load(args: argparse.Namespace):
    return load_config(args.config)


def _cmd_config(args: argparse.Namespace) -> int:
    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    print(f"config OK: tier={cfg.scope.tier} mode={cfg.mode} "
          f"probes={len(cfg.probes)} enabled={cfg.enabled}")
    return 0


def _cmd_probe(args: argparse.Namespace) -> int:
    from aesculap.probes.base import ProbeStatus
    from aesculap.probes.registry import ProbeSuite

    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    suite = ProbeSuite.from_config(cfg.probes)
    worst_fail = False
    for r in suite.run_all():
        first = r.evidence.splitlines()[0] if r.evidence else ""
        print(f"[{r.status.value:>4}] {r.name}: {first}")
        worst_fail = worst_fail or r.status is ProbeStatus.FAIL
    return 1 if worst_fail else 0


def _cmd_start(args: argparse.Namespace) -> int:
    import signal

    from aesculap.audit.log import AuditLog
    from aesculap.daemon import Daemon

    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    audit = AuditLog(cfg.audit_log_path or f"{cfg.state_dir or '/tmp'}/audit.jsonl")
    daemon = Daemon(cfg, audit)
    signal.signal(signal.SIGTERM, lambda *_: daemon._stop.set())
    signal.signal(signal.SIGINT, lambda *_: daemon._stop.set())
    daemon.run_forever()
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    from aesculap.install.wizard import run_wizard

    return run_wizard(args.config)


def _cmd_install_systemd(args: argparse.Namespace) -> int:
    from aesculap.install.systemd_unit import plan_install, write_unit

    plan = plan_install(args.config, args.scope)
    print(f"# systemd unit ({args.scope} scope) -> {plan.unit_path}\n")
    print(plan.content)
    if args.write:
        write_unit(plan)
        print(f"\n✓ wrote {plan.unit_path}")
    else:
        print("# (dry-run; pass --write to install) then run:")
    print(plan.enable_hint)
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    from aesculap.audit.log import AuditLog
    from aesculap.notify.dedup import NotificationDeduper

    try:
        cfg = _load(args)
    except ConfigError as e:
        print(f"config invalid: {e}", file=sys.stderr)
        return 1
    print(f"mode={cfg.mode} enabled={cfg.enabled} tier={cfg.scope.tier}")
    audit_path = cfg.audit_log_path or f"{cfg.state_dir or '/tmp'}/audit.jsonl"
    records = AuditLog(audit_path).read_all()
    print(f"audit: {len(records)} records at {audit_path}")
    for r in records[-args.tail:]:
        print(f"  {r.get('iso','')} {r.get('event','')} "
              f"{r.get('fingerprint','')}")
    state_dir = cfg.state_dir or "/tmp"
    issues = NotificationDeduper(f"{state_dir.rstrip('/')}/open_issues.json").open_issues()
    print(f"open issues ({len(issues)}): {', '.join(issues) or 'none'}")
    return 0


def _set_config_key(path: str, **changes) -> None:
    with open(path) as fh:
        data = yaml.safe_load(fh) or {}
    data.update(changes)
    with open(path, "w") as fh:
        yaml.safe_dump(data, fh, sort_keys=False, allow_unicode=True)


def _cmd_enable(args: argparse.Namespace) -> int:
    _load(args)  # validate first
    _set_config_key(args.config, enabled=True)
    print("enabled=true (PRD §10.1)")
    return 0


def _cmd_disable(args: argparse.Namespace) -> int:
    _load(args)
    _set_config_key(args.config, enabled=False)
    print("enabled=false (master switch off, PRD §10.1)")
    return 0


def _cmd_mode(args: argparse.Namespace) -> int:
    if args.mode not in ("fix", "observe"):
        print("mode must be fix or observe", file=sys.stderr)
        return 1
    _load(args)
    _set_config_key(args.config, mode=args.mode)
    print(f"mode={args.mode} (PRD §10.2)")
    return 0


def _cmd_stop(args: argparse.Namespace) -> int:
    # The daemon is managed by systemd; stopping is a systemctl action. We print
    # the right command rather than guessing the unit scope.
    print("Stop the daemon via systemd:")
    print("  systemctl --user stop aesculap.service   # user scope")
    print("  sudo systemctl stop aesculap.service      # system scope")
    print("(A foreground `aesculap start` stops on SIGINT/SIGTERM.)")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="aesculap", description=__doc__)
    p.add_argument("--version", action="version", version=f"aesculap {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def with_config(name, help_text, func):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("config", help="path to config.yaml")
        sp.set_defaults(func=func)
        return sp

    with_config("config", "validate a config file", _cmd_config)
    with_config("probe", "run the Tier 0 probe suite once", _cmd_probe)
    with_config("start", "start the daemon (foreground)", _cmd_start)
    with_config("enable", "turn the master switch on", _cmd_enable)
    with_config("disable", "turn the master switch off", _cmd_disable)

    pi = sub.add_parser("install", help="run the install wizard")
    pi.add_argument("config", help="output path for config.yaml")
    pi.set_defaults(func=_cmd_install)

    pis = sub.add_parser("install-systemd", help="render/write the systemd unit")
    pis.add_argument("config", help="path to config.yaml")
    pis.add_argument("--scope", choices=("user", "system"), default="user")
    pis.add_argument("--write", action="store_true", help="write the unit file")
    pis.set_defaults(func=_cmd_install_systemd)

    pst = with_config("status", "show daemon + open-issue status", _cmd_status)
    pst.add_argument("--tail", type=int, default=10, help="recent audit lines")

    pm = sub.add_parser("mode", help="switch fix/observe")
    pm.add_argument("mode", choices=("fix", "observe"))
    pm.add_argument("config", help="path to config.yaml")
    pm.set_defaults(func=_cmd_mode)

    sub.add_parser("stop", help="how to stop the daemon").set_defaults(func=_cmd_stop)
    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
