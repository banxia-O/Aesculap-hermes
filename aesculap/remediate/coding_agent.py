"""coding_agent executor (PRD §6.1, §6.4, §7.1).

Delegates to an external coding tool (Claude Code / Codex) for fixes that need
code comprehension across files, tests, and iteration. All work happens inside a
git repo so a failed run is rolled back with `git reset --hard` (PRD §7.1):

    git checkpoint -> invoke tool -> commit -> full verify
                   -> on failure: rollback to checkpoint -> escalate to human

If no coding tool is installed, this route should never have been reached (the
§6.2 gate degrades it to human via the capability inventory, §6.4); we
defensively re-check and escalate to human if so.

Tool invocation is provider-agnostic via a command template. Known-good
defaults (from research):
- claude: `claude -p {prompt} --output-format json`  (headless; PreToolUse
  deny hooks can add a second tripwire layer)
- codex:  `codex exec --sandbox workspace-write --ask-for-approval never {prompt}`
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from aesculap.remediate.backup import BackupError, GitBackupManager, GitCheckpoint
from aesculap.remediate.verify import VerifyResult, Verifier
from aesculap.probes.base import ProbeResult
from aesculap.types import Route

# Built-in command templates for known tools. {prompt} is the fix instruction.
DEFAULT_TEMPLATES = {
    "claude": "claude -p {prompt} --output-format json",
    "codex": "codex exec --sandbox workspace-write --ask-for-approval never {prompt}",
}


@dataclass
class CodingAgentResult:
    success: bool
    verify: VerifyResult | None
    reason: str
    next_route: Route
    commit_sha: str = ""


class CodingAgentExecutor:
    def __init__(
        self,
        tool: str,
        verifier: Verifier,
        git: GitBackupManager,
        command_template: str = "",
        timeout_seconds: float = 1800,
        runner=None,
    ):
        self.tool = tool
        self.verifier = verifier
        self.git = git
        self.command_template = command_template or DEFAULT_TEMPLATES.get(tool, "")
        self.timeout_seconds = timeout_seconds
        # Injected for testing; defaults to real subprocess.
        self._runner = runner or self._run_subprocess

    def available(self) -> bool:
        import shutil
        return bool(self.tool) and shutil.which(self.tool) is not None

    def _run_subprocess(self, command: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            command, shell=True, capture_output=True, text=True,
            cwd=str(self.git.repo), timeout=self.timeout_seconds,
        )

    def _build_command(self, prompt: str) -> str:
        import shlex
        if "{prompt}" not in self.command_template:
            return f"{self.command_template} {shlex.quote(prompt)}".strip()
        return self.command_template.replace("{prompt}", shlex.quote(prompt))

    def run(
        self, prompt: str, before: list[ProbeResult]
    ) -> CodingAgentResult:
        # §6.4 defensive re-check: tool gone -> human.
        if not self.available():
            return CodingAgentResult(
                False, None,
                f"coding agent {self.tool!r} not available; escalate to human",
                Route.HUMAN,
            )
        # git checkpoint (PRD §7.1).
        try:
            checkpoint = self.git.checkpoint()
        except BackupError as e:
            return CodingAgentResult(False, None, str(e), Route.HUMAN)

        command = self._build_command(prompt)
        try:
            proc = self._runner(command)
        except Exception as e:  # noqa: BLE001
            self.git.rollback(checkpoint)
            return CodingAgentResult(
                False, None, f"coding agent invocation failed: {e}", Route.HUMAN
            )
        if proc.returncode != 0:
            self.git.rollback(checkpoint)
            return CodingAgentResult(
                False, None,
                f"coding agent exit {proc.returncode}: "
                f"{(proc.stderr or proc.stdout).strip()[:200]}",
                Route.HUMAN,
            )

        # Commit whatever the tool changed, then verify (PRD §7.1).
        commit_sha = self.git.commit_all(f"aesculap coding_agent fix: {prompt[:60]}")
        result = self.verifier.verify(before)
        if result.passed:
            return CodingAgentResult(
                True, result, result.reason, Route.CODING_AGENT, commit_sha
            )
        # Verify failed -> roll back to checkpoint, escalate to human (§6.3).
        self.git.rollback(checkpoint)
        return CodingAgentResult(
            False, result, f"verify failed: {result.reason}", Route.HUMAN
        )
