"""Linting check with configurable backends.

This module provides the LintingCheck class which runs code linting
tools (ruff, flake8, or pylint) on the package and reports any issues.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import run_tool, sanitize_args, tool_available


class LintingCheck(BaseCheck):
    """Run linting tools on the package.

    Executes a configurable linting tool to check code quality and
    style. Supports ruff (default), flake8, and pylint.

    Attributes:
        name: The check identifier ("linting").
        description: Human-readable description of this check.
        SUPPORTED_TOOLS: List of supported linting tools.

    Configuration Options:
        tool (str): Linter to use ("ruff", "flake8", or "pylint").
            Defaults to "ruff".
        args (list[str]): Additional arguments to pass to the linter.

    Examples:
        Run linting with default settings (ruff):

        >>> from pathlib import Path
        >>> check = LintingCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'linting'

        Configure to use flake8:

        >>> config = {"enabled": True, "tool": "flake8"}
        >>> result = check.run(Path("."), config)

        Pass additional ruff arguments:

        >>> config = {"enabled": True, "args": ["--select", "E,W"]}
        >>> result = check.run(Path("."), config)
    """

    name = "linting"
    description = "Run code linting (ruff/flake8/pylint)"

    SUPPORTED_TOOLS = ["ruff", "flake8", "pylint"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run linting check on the package.

        Executes the configured linting tool and collects any issues found.
        Issues are reported in the result details (limited to first 10).

        Args:
            package_path: Path to the package directory to lint.
            config: Configuration dictionary with options:

                - tool (str): "ruff", "flake8", or "pylint" (default: "ruff")
                - args (list[str]): Additional arguments for the linter

        Returns:
            A CheckResult with:

            - OK status if no linting issues found
            - WARNING status if linting issues found
            - ERROR status if linter fails or unsupported tool specified
            - SKIPPED status if the linting tool is not installed
        """
        tool = config.get("tool", "ruff")

        if tool not in self.SUPPORTED_TOOLS:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=f"Unsupported linting tool: {tool}",
                details=[f"Supported tools: {', '.join(self.SUPPORTED_TOOLS)}"],
            )

        preamble = [f"Using linter: {tool}"]

        if tool == "ruff":
            return self._run_ruff(package_path, config, preamble)
        elif tool == "flake8":
            return self._run_flake8(package_path, config, preamble)
        else:  # pylint
            return self._run_pylint(package_path, config, preamble)

    def _run_ruff(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run ruff linter."""
        if not tool_available("ruff"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="ruff not installed",
                details=["Install ruff: pip install ruff"],
            )

        args = sanitize_args(config.get("args", []))
        cmd = ["ruff", "check", ".", *args]

        result = run_tool(cmd, cwd=package_path, timeout=DEFAULT_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Linting timed out",
                details=[f"Linting took longer than {DEFAULT_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run ruff",
                details=[result.error],
            )

        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No linting issues found",
                details=list(preamble),
            )

        issues = result.output_lines()
        issue_count = len(issues)

        details = [*preamble, *issues[:MAX_DETAIL_LINES]]

        if issue_count > MAX_DETAIL_LINES:
            details.append(f"... and {issue_count - MAX_DETAIL_LINES} more issues")

        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=f"Found {issue_count} linting issue(s)",
            details=details,
        )

    def _run_flake8(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run flake8 linter."""
        if not tool_available("flake8"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="flake8 not installed",
                details=["Install flake8: pip install flake8"],
            )

        args = sanitize_args(config.get("args", []))
        cmd = ["flake8", ".", *args]

        result = run_tool(cmd, cwd=package_path, timeout=DEFAULT_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Linting timed out",
                details=[f"Linting took longer than {DEFAULT_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run flake8",
                details=[result.error],
            )

        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No linting issues found",
                details=list(preamble),
            )

        issues = result.output_lines()
        issue_count = len(issues)

        details = [*preamble, *issues[:MAX_DETAIL_LINES]]

        if issue_count > MAX_DETAIL_LINES:
            details.append(f"... and {issue_count - MAX_DETAIL_LINES} more issues")

        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=f"Found {issue_count} linting issue(s)",
            details=details,
        )

    def _run_pylint(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run pylint linter."""
        if not tool_available("pylint"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="pylint not installed",
                details=["Install pylint: pip install pylint"],
            )

        # Find Python files to lint
        src_dir = package_path / "src"
        if src_dir.is_dir():
            target = str(src_dir)
        else:
            target = "."

        args = sanitize_args(config.get("args", []))
        cmd = ["pylint", target, "--output-format=text", *args]

        result = run_tool(cmd, cwd=package_path, timeout=LONG_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Linting timed out",
                details=[f"Linting took longer than {LONG_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run pylint",
                details=[result.error],
            )

        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No linting issues found",
                details=list(preamble),
            )

        # Parse output
        issues = result.output_lines()
        issue_lines = [i for i in issues if ": " in i and i[0] != "*"]

        details = [*preamble, *issue_lines[:MAX_DETAIL_LINES]]

        if len(issue_lines) > MAX_DETAIL_LINES:
            details.append(f"... and {len(issue_lines) - MAX_DETAIL_LINES} more issues")

        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=f"Found {len(issue_lines)} linting issue(s)",
            details=details,
        )
