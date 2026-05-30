"""self_fix executor (PRD §6.1, §7).

Executes the gate-approved actions for a `self_fix` route, wrapped in the
mandatory standard flow (PRD §7.1):

    backup -> execute -> full verify -> observation window
            -> on failure: re-diagnose (count against budget) -> rollback
            -> escalate to the next ladder rung

Only two action kinds are executable here, both deliberately narrow:

- RESTART_PROCESS: an idempotent, reversible restart (PRD §6.1 typical case).
- RUN_COMMAND / WRITE_FILE: a single, fully-specified, instantly-verifiable
  change (e.g. fix a config typo). These have ALREADY passed the §8.1/§6.2 gate
  before reaching here — the executor never re-routes, it only runs and verifies.

The executor never decides whether it's *allowed* to act (the gate did that)
nor whether the mode permits acting (the pipeline checks `mode == fix`). It owns
backup/verify/rollback/budget only.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from typing import Callable

from aesculap.gate.escalation import EscalationLadder, EscalationState
from aesculap.remediate.backup import FileBackup, FileBackupManager
from aesculap.remediate.verify import VerifyResult, Verifier
from aesculap.probes.base import ProbeResult
from aesculap.types import ActionKind, ProposedAction, Route


@dataclass
class FixAttemptResult:
    success: bool
    verify: VerifyResult | None
    reason: str
    next_route: Route
    attempts: int


# A restart hook is injected so the executor stays testable and doesn't hard-
# code how a given deployment restarts Hermes (systemctl / supervisor / custom).
RestartFn = Callable[[ProposedAction], subprocess.CompletedProcess]
CommandFn = Callable[[ProposedAction], subprocess.CompletedProcess]


def _default_restart(action: ProposedAction) -> subprocess.CompletedProcess:
    cmd = action.command or action.description
    return subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=60)


def _default_command(action: ProposedAction) -> subprocess.CompletedProcess:
    return subprocess.run(
        action.command or "", shell=True, capture_output=True, text=True, timeout=120
    )


class SelfFixExecutor:
    def __init__(
        self,
        verifier: Verifier,
        backup_mgr: FileBackupManager,
        ladder: EscalationLadder,
        restart_fn: RestartFn | None = None,
        command_fn: CommandFn | None = None,
        observe_window_seconds: float = 60,
        sleep_fn: Callable[[float], None] | None = None,
    ):
        self.verifier = verifier
        self.backup_mgr = backup_mgr
        self.ladder = ladder
        self.restart_fn = restart_fn or _default_restart
        self.command_fn = command_fn or _default_command
        self.observe_window_seconds = observe_window_seconds
        # Injected so tests don't actually sleep through the observation window.
        import time as _time
        self.sleep_fn = sleep_fn or _time.sleep

    def _execute_actions(
        self, actions: list[ProposedAction]
    ) -> tuple[bool, str, list[FileBackup]]:
        """Run actions; return (ok, reason, backups_for_rollback)."""
        backups: list[FileBackup] = []
        for action in actions:
            if action.kind == ActionKind.WRITE_FILE and action.path:
                backups.append(self.backup_mgr.backup(action.path))
            try:
                if action.kind == ActionKind.RESTART_PROCESS:
                    proc = self.restart_fn(action)
                elif action.kind in (ActionKind.RUN_COMMAND, ActionKind.WRITE_FILE):
                    proc = self.command_fn(action)
                else:
                    continue
            except Exception as e:  # noqa: BLE001
                return False, f"action raised: {e}", backups
            if proc.returncode != 0:
                return False, (
                    f"action exit {proc.returncode}: "
                    f"{(proc.stderr or proc.stdout).strip()[:200]}"
                ), backups
        return True, "actions executed", backups

    def run(
        self,
        actions: list[ProposedAction],
        before: list[ProbeResult],
        state: EscalationState,
    ) -> FixAttemptResult:
        """One self_fix attempt with backup/verify/observe/rollback.

        Counts against the retry budget on failure and returns the next route to
        take (still self_fix if budget remains, else the escalation rung).
        """
        ok, reason, backups = self._execute_actions(actions)
        if not ok:
            self._rollback(backups)
            nxt = self.ladder.next_after_self_fix_failure(state)
            return FixAttemptResult(False, None, reason, nxt, state.self_fix_attempts)

        # Full verification (decision #2).
        result = self.verifier.verify(before)
        if result.passed and self.observe_window_seconds > 0:
            # Observation window: re-verify after a delay (PRD §7.1 step 4).
            self.sleep_fn(self.observe_window_seconds)
            result = self.verifier.verify(before)

        if result.passed:
            return FixAttemptResult(
                True, result, result.reason, Route.SELF_FIX,
                state.self_fix_attempts,
            )

        # Verification failed: re-diagnose is the caller's job; here we roll
        # back and report the next rung (PRD §7.1 step 5, §7.2 budget).
        self._rollback(backups)
        nxt = self.ladder.next_after_self_fix_failure(state)
        return FixAttemptResult(
            False, result, f"verify failed: {result.reason}", nxt,
            state.self_fix_attempts,
        )

    def _rollback(self, backups: list[FileBackup]) -> None:
        # Restore in reverse order so nested/overlapping edits unwind cleanly.
        for backup in reversed(backups):
            self.backup_mgr.restore(backup)
