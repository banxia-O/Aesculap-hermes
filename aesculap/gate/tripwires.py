"""Hard tripwires (PRD §8.1).

Pure deterministic code, independent of the LLM. Any proposed action that hits
one of these is intercepted and forced to `human` — the LLM cannot route around
it, even if it confidently emitted `self_fix`.

Tripwires (PRD §8.1):
- writing a chmod-444 (read-only) file
- touching .env / key / credential files
- executing `rm`, `git push --force`
- hitting a path outside the §9 writable scope
- hitting a paid / billing interface
- modifying Aesculap's own directory (§9.3)
- modifying an identity file (§9.2)

The scope/blacklist concerns (read-only, credentials, self-dir, identity files,
out-of-scope paths) are delegated to ScopeGate so there is a single source of
truth. This module adds the *command-shaped* tripwires (rm, force-push, billing)
that scope alone can't see.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

from aesculap.gate.scope import ScopeGate
from aesculap.types import ActionKind, ProposedAction

# Command tokens that are categorically forbidden (PRD §8.1).
# Matched on the parsed argv, not a substring, to avoid false positives like a
# path containing "rm".
_FORBIDDEN_COMMANDS = {"rm", "rmdir", "shred", "mkfs", "dd"}

# Shell metacharacters that enable command substitution, variable expansion,
# command chaining, sub-shells, or redirection. argv-token matching cannot reason
# about a command that uses any of these (e.g. `$(echo rm)`, `` `rm` ``, `$RM`,
# `a; rm -rf /`, `x | sh`, `echo > /etc/passwd`), so such a command is forced to
# `human` outright (§8.1). The executor additionally runs commands with
# shell=False so these would not expand even if they slipped through — this gate
# check is the first, declarative line of defense.
_SHELL_METACHARS = ("$", "`", ";", "|", "&", ">", "<", "(", ")", "{", "}", "\n")

# Regexes for dangerous command *shapes* that argv-token matching misses.
_FORCE_PUSH_RE = re.compile(r"\bgit\b.*\bpush\b.*(--force\b|--force-with-lease\b|-f\b)")
_DANGEROUS_SHAPES = (
    (_FORCE_PUSH_RE, "git push --force"),
    (re.compile(r"\brm\b\s+(-\w*\s+)*-?\w*[rf]"), "rm -rf"),
    (re.compile(r">\s*/dev/sd"), "raw disk write"),
    (re.compile(r"\bchmod\b\s+-R\b"), "recursive chmod"),
)

# Substrings that suggest a paid / billing interface (PRD §8.1). Conservative:
# the cost of a false positive is "ask a human", which is the safe direction.
_BILLING_SUBSTRINGS = (
    "billing",
    "payment",
    "purchase",
    "checkout",
    "/charge",
    "topup",
    "top-up",
    "recharge",
    "subscribe",
    "invoice",
)


@dataclass
class TripwireHit:
    """A fired tripwire, with the action index and a human-readable reason."""

    action_index: int
    reason: str


def _command_argv(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        # Unparseable command -> treat the whole string as one token so the
        # shape regexes still run; safer than silently passing.
        return [command]


def _path_like_tokens(argv: list[str]) -> list[str]:
    """Absolute path-shaped tokens in an argv (for §9.2 floor vetting, #2).

    We only vet *absolute* paths (``/...`` or ``~...``), optionally the value
    after ``--opt=``: relative paths resolve against an ambiguous working dir, so
    judging them invites false negatives/positives. Absolute paths are the clear
    danger (e.g. ``truncate -s0 /etc/hostname``). A token naming a system binary
    (``/usr/bin/python``) will also match — an accepted conservative trade-off:
    a hit routes to a human, never to silent execution.
    """
    out: list[str] = []
    for tok in argv:
        cand = tok.split("=", 1)[1] if tok.startswith("--") and "=" in tok else tok
        if cand.startswith("/") or cand.startswith("~"):
            out.append(cand)
    return out


def _check_command(command: str) -> str | None:
    """Return a tripwire reason if a shell command is forbidden, else None."""
    if not command:
        return None
    # Shell-expansion / chaining metacharacters: argv parsing can't see through
    # them, so refuse the command outright rather than risk a bypass (§8.1).
    for ch in _SHELL_METACHARS:
        if ch in command:
            return f"tripwire: shell metacharacter {ch!r} in command (§8.1)"
    argv = _command_argv(command)
    # Bare executable name (strip any path) against the forbidden set.
    for tok in argv:
        exe = tok.rsplit("/", 1)[-1]
        if exe in _FORBIDDEN_COMMANDS:
            return f"tripwire: forbidden command `{exe}` (§8.1)"
    lowered = command.lower()
    for pattern, label in _DANGEROUS_SHAPES:
        if pattern.search(command):
            return f"tripwire: dangerous command shape `{label}` (§8.1)"
    if any(s in lowered for s in _BILLING_SUBSTRINGS):
        return "tripwire: paid/billing interface (§8.1)"
    return None


class TripwireGate:
    """Scans a list of proposed actions for hard tripwires."""

    def __init__(self, scope_gate: ScopeGate):
        self.scope = scope_gate

    def scan_action(self, action: ProposedAction) -> str | None:
        """Return the first tripwire reason for one action, or None."""
        # File-shaped actions: delegate the path verdict to the scope gate,
        # which already encodes read-only / credential / identity / self-dir /
        # out-of-scope as blacklist+boundary rules.
        if action.kind in (ActionKind.WRITE_FILE, ActionKind.DELETE_FILE):
            if not action.path:
                return "tripwire: file action with no path (§8.1, fail-closed)"
            verdict = self.scope.check_write(action.path)
            if not verdict.allowed:
                return f"tripwire: {verdict.reason}"
        # delete_file is a destructive op; even inside scope it warrants a human
        # unless it's an explicit, reversible cleanup. Conservatively flag it.
        if action.kind == ActionKind.DELETE_FILE:
            return "tripwire: file deletion is destructive (§8.1)"
        # Command-shaped actions: forbidden tokens, dangerous shapes, billing.
        if action.kind == ActionKind.RUN_COMMAND:
            reason = _check_command(action.command or "")
            if reason is not None:
                return reason
            # #2: vet absolute path arguments against the §9.2 blacklist floor,
            # so a non-forbidden binary can't reach a blacklisted path (e.g.
            # `truncate -s0 /etc/hostname`, `cat /root/.ssh/id_rsa`).
            for tok in _path_like_tokens(_command_argv(action.command or "")):
                floor = self.scope.blacklist_floor(tok)
                if floor is not None:
                    return f"tripwire: command path argument {tok!r} -> {floor}"
        return None

    def scan(self, actions: list[ProposedAction]) -> list[TripwireHit]:
        """Return every tripwire hit across all actions (empty == clean)."""
        hits: list[TripwireHit] = []
        for i, action in enumerate(actions):
            reason = self.scan_action(action)
            if reason is not None:
                hits.append(TripwireHit(action_index=i, reason=reason))
        return hits
