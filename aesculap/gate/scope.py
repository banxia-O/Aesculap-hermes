"""Writable scope tiers + blacklist (PRD §9).

Two independent checks compose here:

1. **Scope tier** (§9.1) — the *outer boundary* of where writes may land at
   all. Chosen explicitly at install time (A/B/C); there is no default.
2. **Blacklist** (§9.2) — a hard *floor* that applies inside any tier,
   default-allow: everything within the boundary is writable EXCEPT blacklisted
   paths. The floor is hard-wired here and cannot be widened by config; the user
   may only ADD to it (identity files, extra globs).

A path is writable iff it is inside the tier boundary AND not blacklisted.
This module is pure, deterministic, and has no LLM dependency.
"""

from __future__ import annotations

import fnmatch
import os
import stat
from dataclasses import dataclass
from pathlib import Path

from aesculap.config import ScopeConfig

# Credential / secret filename patterns — always blacklisted regardless of tier
# (PRD §9.2). Matched against the file name and against full-path globs below.
_CREDENTIAL_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "id_rsa",
    "id_ed25519",
    "*.p12",
    "*.pfx",
    "credentials",
    "credentials.*",
    ".netrc",
    ".npmrc",
    ".pypirc",
    ".git-credentials",
)

# Substrings that, if present in the (lowercased) name, flag a likely secret.
_CREDENTIAL_NAME_SUBSTRINGS = ("secret", "token", "apikey", "api_key", "password")

# System-sensitive prefixes — excluded even under tier C (PRD §9.2 last bullet).
_SYSTEM_SENSITIVE_PREFIXES = (
    "/etc",
    "/boot",
    "/usr",
    "/bin",
    "/sbin",
    "/lib",
    "/lib64",
    "/sys",
    "/proc",
    "/dev",
    "/var/lib",
    "/root/.ssh",
)


@dataclass
class ScopeVerdict:
    """Result of a writability check, with an audit reason."""

    allowed: bool
    reason: str


def _norm(path: str | os.PathLike[str]) -> Path:
    """Absolute, symlink-resolved-as-far-as-possible path for comparison.

    We resolve to defeat ``../`` traversal and symlink escapes. ``strict=False``
    lets us reason about paths that don't exist yet (a fix may create a file).
    """
    return Path(path).expanduser().resolve(strict=False)


def _is_within(child: Path, parent: Path) -> bool:
    """True if `child` is `parent` or nested under it (after normalization)."""
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def _is_readonly_file(p: Path) -> bool:
    """True if the file exists and has no owner write bit (≈ chmod 444, §9.2)."""
    try:
        mode = p.stat().st_mode
    except OSError:
        return False
    return not bool(mode & stat.S_IWUSR)


class ScopeGate:
    """Adjudicates whether a given filesystem path may be written."""

    def __init__(self, config: ScopeConfig, aesculap_home: str = ""):
        self.config = config
        self.tier = config.tier
        self.project_root = _norm(config.project_root) if config.project_root else None
        self.hermes_config_dir = (
            _norm(config.hermes_config_dir) if config.hermes_config_dir else None
        )
        self.aesculap_home = _norm(aesculap_home) if aesculap_home else None
        # Identity files are stored as resolved absolute paths for exact match,
        # plus kept as raw entries so glob-style entries still work.
        self._identity_paths = {_norm(f) for f in config.identity_files}
        self._identity_globs = list(config.identity_files)
        self._extra_globs = list(config.extra_blacklist)

    # --- tier boundary (§9.1) --------------------------------------------
    def _within_tier(self, p: Path) -> bool:
        if self.tier == "C":
            # Whole host writable EXCEPT system-sensitive prefixes (still floored).
            return True
        boundaries: list[Path] = []
        if self.project_root:
            boundaries.append(self.project_root)
        if self.tier == "B" and self.hermes_config_dir:
            boundaries.append(self.hermes_config_dir)
        return any(_is_within(p, b) for b in boundaries)

    # --- blacklist floor (§9.2) ------------------------------------------
    def is_identity_file(self, p: Path) -> bool:
        if p in self._identity_paths:
            return True
        sp = str(p)
        name = p.name
        return any(
            fnmatch.fnmatch(sp, g) or fnmatch.fnmatch(name, g)
            for g in self._identity_globs
        )

    def is_credential_file(self, p: Path) -> bool:
        # Match case-insensitively: fnmatch is case-sensitive on Linux, so we
        # lowercase the name (patterns are already lowercase) — otherwise `.ENV`,
        # `ID_RSA`, `Credentials`, `key.PEM` would slip past the §9.2 floor.
        lname = p.name.lower()
        if any(fnmatch.fnmatch(lname, pat) for pat in _CREDENTIAL_NAME_PATTERNS):
            return True
        return any(s in lname for s in _CREDENTIAL_NAME_SUBSTRINGS)

    def is_system_sensitive(self, p: Path) -> bool:
        sp = str(p)
        return any(
            sp == prefix or sp.startswith(prefix + os.sep)
            for prefix in _SYSTEM_SENSITIVE_PREFIXES
        )

    def is_aesculap_self(self, p: Path) -> bool:
        """PRD §9.3: Aesculap must never modify its own directory."""
        return self.aesculap_home is not None and _is_within(p, self.aesculap_home)

    def _matches_extra(self, p: Path) -> bool:
        sp = str(p)
        return any(fnmatch.fnmatch(sp, g) for g in self._extra_globs)

    def blacklist_reason(self, p: Path) -> str | None:
        """Return the blacklist rule that blocks `p`, or None if not floored.

        Order is deliberate: self-modification and identity files first, since
        those are the most catastrophic and most likely to be probed by a
        mis-routed fix.
        """
        if self.is_aesculap_self(p):
            return "blacklist: Aesculap self-directory (§9.3)"
        if self.is_identity_file(p):
            return "blacklist: identity file (§9.2)"
        if self.is_credential_file(p):
            return "blacklist: credential/.env file (§9.2)"
        if self.is_system_sensitive(p):
            return "blacklist: system-sensitive path (§9.2)"
        if _is_readonly_file(p):
            return "blacklist: read-only file (chmod 444, §9.2)"
        if self._matches_extra(p):
            return "blacklist: user-configured pattern (§9.2)"
        return None

    # --- public API -------------------------------------------------------
    def blacklist_floor(self, path: str | os.PathLike[str]) -> str | None:
        """Return the §9.2 blacklist reason for `path`, ignoring the tier
        boundary. Used to vet command path-arguments: a command must never name
        a blacklisted path (identity/credential/system-sensitive/self-dir) even
        if its working directory would otherwise be in scope.
        """
        return self.blacklist_reason(_norm(path))

    def check_write(self, path: str | os.PathLike[str]) -> ScopeVerdict:
        """Adjudicate a write to `path`. Blacklist floor wins over tier."""
        p = _norm(path)
        reason = self.blacklist_reason(p)
        if reason is not None:
            return ScopeVerdict(False, reason)
        if not self._within_tier(p):
            return ScopeVerdict(
                False, f"outside tier {self.tier} writable boundary (§9.1)"
            )
        return ScopeVerdict(True, f"within tier {self.tier} boundary, not blacklisted")
