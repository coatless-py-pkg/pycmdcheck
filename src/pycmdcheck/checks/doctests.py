"""Doctest execution check.

Runs doctests embedded in module docstrings using pytest's
``--doctest-modules`` flag.
"""

import logging
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import run_tool, tool_available

logger = logging.getLogger(__name__)


class DoctestsCheck(BaseCheck):
    """Run doctests via pytest --doctest-modules.

    Executes all doctests embedded in module docstrings and reports
    failures.

    Attributes:
        name: The check identifier ("doctests").
        description: Human-readable description of this check.
    """

    name = "doctests"
    description = "Run doctests in package modules"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run doctests for the package.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.
                Supports ``args`` (list[str]) for additional pytest args
                and ``timeout`` (int) to override default.

        Returns:
            A CheckResult with:

            - OK status if all doctests pass or no doctests found
            - WARNING status if some doctests fail
            - ERROR status if doctest runner failed to execute
            - SKIPPED status if pytest is not installed
        """
        if not tool_available("pytest"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="pytest not installed",
                details=["Install pytest to run doctests"],
            )

        # Find the package source directory
        src_dir = package_path / "src"
        target = src_dir if src_dir.is_dir() else package_path

        timeout = config.get("timeout", DEFAULT_TIMEOUT)
        extra_args = config.get("args", [])

        cmd = [
            "pytest",
            "--doctest-modules",
            "--tb=short",
            "-q",
            str(target),
            *extra_args,
        ]

        details = ["Running: pytest --doctest-modules"]

        result = run_tool(cmd, cwd=package_path, timeout=timeout)

        if result.error:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run pytest for doctests",
                details=[f"Error: {result.error}"],
            )

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Doctest execution timed out",
                details=[f"Timeout after {timeout}s"],
            )

        stdout = result.stdout or ""
        lines = stdout.strip().splitlines()

        # pytest exit code 5 means "no tests were collected" — for a package
        # with no doctests this is success, not a failure.
        if result.returncode == 5 or "no tests ran" in stdout.lower():
            details.append("No doctests found in package modules")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No doctests found",
                details=details,
            )

        if result.returncode == 0:
            details.append("All doctests passed")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All doctests passed",
                details=details,
            )

        # Doctests failed — extract failure info
        failed_lines = [ln for ln in lines if "FAILED" in ln or "ERROR" in ln]
        details.extend(failed_lines[:MAX_DETAIL_LINES])
        if len(failed_lines) > MAX_DETAIL_LINES:
            details.append(f"... and {len(failed_lines) - MAX_DETAIL_LINES} more")

        fail_count = len(failed_lines) if failed_lines else "some"
        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=f"{fail_count} doctest(s) failed",
            details=details,
        )
