"""Install wizard (PRD §9.1, §5.1, §2, §7.1, decision #1).

Interactive setup that produces a validated config.yaml. The prompting (I/O) is
separated from the config assembly (`build_config`) so the logic is testable.

Critical safety behaviours (PRD §9.1):
- writable scope tier is a FORCED explicit choice — NO default is offered.
- choosing tier C triggers a second confirmation and a non-dedicated-host
  warning (PRD §9.1 strong warning).

Decision #1: scan the Hermes config dir, list identity-file candidates, let the
user tick which to blacklist; the ticked set is written into scope.identity_files.

Preconditions surfaced (PRD §16): file logging (§2) and git repo (§7.1) — if the
project isn't a git repo, the wizard offers to `git init` it.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from aesculap.config import Config, load_config
from aesculap.install.detect import EnvDetection, detect_environment
from aesculap.install.systemd_unit import VALID_SCOPES

SCOPE_TABLE = """\
Writable scope — you MUST choose one (no default, PRD §9.1):

  A  project tree only                 risk ⭐         (safest, recommended)
  B  Hermes config dir + project tree   risk ⭐⭐⭐       (dedicated Hermes host)
  C  whole host / environment           risk ⭐⭐⭐⭐⭐ ⚠   (ONLY a dedicated host)

⚠  If Aesculap is installed on a personal computer or anywhere alongside other
   important data, NEVER choose C.
"""


@dataclass
class WizardAnswers:
    """The decisions a wizard run collects (separated for testability)."""

    tier: str
    project_root: str
    hermes_config_dir: str = ""
    identity_files: list[str] = field(default_factory=list)
    triage_provider: str = ""
    triage_model: str = ""
    triage_key_env: str = ""
    selffix_provider: str = ""
    selffix_model: str = ""
    selffix_key_env: str = ""
    coding_agent_tool: str = ""
    notify_command_template: str = ""
    log_paths: list[str] = field(default_factory=list)
    systemd_scope: str = "user"
    mode: str = "fix"
    aesculap_home: str = ""
    state_dir: str = ""
    audit_log_path: str = ""


def build_config_dict(ans: WizardAnswers) -> dict:
    """Assemble a config dict from wizard answers (pure, testable)."""
    if ans.tier not in ("A", "B", "C"):
        raise ValueError("tier must be explicitly chosen as A, B, or C (§9.1)")
    if ans.systemd_scope not in VALID_SCOPES:
        raise ValueError(f"systemd_scope must be one of {VALID_SCOPES}")
    state_dir = ans.state_dir or "/var/lib/aesculap"
    cfg: dict = {
        "enabled": True,
        "mode": ans.mode,
        "aesculap_home": ans.aesculap_home or "/opt/aesculap",
        "state_dir": state_dir,
        "audit_log_path": ans.audit_log_path or f"{state_dir}/audit.jsonl",
        "scope": {
            "tier": ans.tier,
            "project_root": ans.project_root,
            "identity_files": list(ans.identity_files),
            "extra_blacklist": [],
        },
        "detectors": {"log_paths": list(ans.log_paths)},
    }
    if ans.tier in ("B", "C") and ans.hermes_config_dir:
        cfg["scope"]["hermes_config_dir"] = ans.hermes_config_dir
    if ans.tier == "B" and not ans.hermes_config_dir:
        raise ValueError("tier B requires hermes_config_dir (§9.1)")
    if ans.triage_provider:
        cfg["triage"] = {
            "provider": ans.triage_provider,
            "model": ans.triage_model,
            "api_key_env": ans.triage_key_env,
        }
    if ans.selffix_provider:
        cfg["selffix"] = {
            "provider": ans.selffix_provider,
            "model": ans.selffix_model,
            "api_key_env": ans.selffix_key_env,
        }
    if ans.coding_agent_tool:
        cfg["coding_agent"] = {"tool": ans.coding_agent_tool}
    if ans.notify_command_template:
        cfg["notify"] = {"command_template": ans.notify_command_template}
    return cfg


def write_config(ans: WizardAnswers, path: str) -> Config:
    """Build, write, and validate the config to `path`."""
    data = build_config_dict(ans)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return load_config(path)


def git_init(project_root: str) -> bool:
    """Initialize a git repo at project_root (PRD §7.1 precondition)."""
    proc = subprocess.run(
        ["git", "-C", project_root, "init"], capture_output=True, text=True
    )
    return proc.returncode == 0


# --- interactive shell ----------------------------------------------------

def _ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    resp = input(f"{prompt}{suffix}: ").strip()
    return resp or default


def _ask_yes_no(prompt: str, default: bool = False) -> bool:
    d = "Y/n" if default else "y/N"
    resp = input(f"{prompt} ({d}): ").strip().lower()
    if not resp:
        return default
    return resp in ("y", "yes")


def run_wizard(config_out: str) -> int:
    """Interactive entry point for `aesculap install`. Returns an exit code."""
    print("=== Aesculap install wizard ===\n")
    project_root = _ask("Hermes project tree path", str(Path.cwd()))
    hermes_cfg = _ask("Hermes config dir", "~/.hermes")
    det: EnvDetection = detect_environment(hermes_cfg, project_root)

    # --- forced scope tier (§9.1) -------------------------------------
    print("\n" + SCOPE_TABLE)
    tier = ""
    while tier not in ("A", "B", "C"):
        tier = _ask("Choose writable scope tier (A/B/C)").upper()
    if tier == "C":
        print("\n⚠  Tier C makes the WHOLE HOST writable (minus system paths).")
        if not _ask_yes_no("Is this host DEDICATED to Hermes only?", default=False):
            print("Aborting: do not choose C on a shared/personal machine (§9.1).")
            return 1
        if not _ask_yes_no("Confirm tier C again", default=False):
            print("Aborting tier C.")
            return 1

    # --- identity files (decision #1) ---------------------------------
    identity: list[str] = []
    if det.identity_candidates:
        print("\nIdentity-file candidates found (will be blacklisted, §9.2):")
        for i, f in enumerate(det.identity_candidates, 1):
            print(f"  {i}) {f}")
        picks = _ask("Tick to blacklist (comma indices, 'all', or empty)", "all")
        if picks.lower() == "all":
            identity = list(det.identity_candidates)
        elif picks:
            for tok in picks.split(","):
                tok = tok.strip()
                if tok.isdigit() and 1 <= int(tok) <= len(det.identity_candidates):
                    identity.append(det.identity_candidates[int(tok) - 1])
    else:
        print("\nNo identity-file candidates auto-found; add any manually later "
              "under scope.identity_files.")

    # --- providers (§5.1) ---------------------------------------------
    triage_provider = triage_key = ""
    if det.providers:
        print("\nConfigured model providers (by present API key):")
        for i, p in enumerate(det.providers, 1):
            tag = " (recommended for triage)" if p.suggested else ""
            print(f"  {i}) {p.name}{tag}")
        sel = _ask("Choose TRIAGE provider index (strong model advised)", "1")
        if sel.isdigit() and 1 <= int(sel) <= len(det.providers):
            chosen = det.providers[int(sel) - 1]
            triage_provider, triage_key = chosen.name, chosen.key_env
    triage_model = _ask("Triage model name", "") if triage_provider else ""

    # --- preconditions (§2, §7.1) -------------------------------------
    print(f"\nFile-logging check: {det.file_logging_hint}")
    log_path = _ask("Hermes log file to watch",
                    f"{det.hermes_config_dir}/logs/hermes.log")
    if not det.is_git_repo:
        print(f"\n⚠  {project_root} is not a git repo (PRD §7.1 precondition).")
        if _ask_yes_no("Run `git init` now?", default=True):
            git_init(project_root)

    # --- systemd scope ------------------------------------------------
    print("\nsystemd service scope: user (no root) or system (boot-start, root).")
    systemd_scope = ""
    while systemd_scope not in VALID_SCOPES:
        systemd_scope = _ask("Choose systemd scope (user/system)", "user").lower()

    notify_tmpl = _ask("Notification send command template",
                       "hermes gateway send --text {message}")

    ans = WizardAnswers(
        tier=tier, project_root=project_root,
        hermes_config_dir=det.hermes_config_dir if tier in ("B", "C") else "",
        identity_files=identity,
        triage_provider=triage_provider, triage_model=triage_model,
        triage_key_env=triage_key,
        coding_agent_tool=(det.coding_agents[0] if det.coding_agents else ""),
        notify_command_template=notify_tmpl,
        log_paths=[log_path] if log_path else [],
        systemd_scope=systemd_scope,
    )
    write_config(ans, config_out)
    print(f"\n✓ Config written to {config_out} (tier {tier}, mode fix).")
    print("Next: install the systemd unit with `aesculap install-systemd "
          f"{config_out} --scope {systemd_scope}` (prints the enable commands).")
    return 0
