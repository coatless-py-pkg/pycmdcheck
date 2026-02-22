"""Type checking with configurable backends.

This module provides the TypingCheck class which runs static type
checking tools (mypy or pyright) on the package and reports any errors.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import run_tool, sanitize_args, tool_available


class TypingCheck(BaseCheck):
    """Run type checking tools on the package.

    Executes a configurable static type checker to verify type
    annotations. Supports mypy (default) and pyright.

    Attributes:
        name: The check identifier ("typing").
        description: Human-readable description of this check.
        SUPPORTED_TOOLS: List of supported type checking tools.

    Configuration Options:
        tool (str): Type checker to use ("mypy" or "pyright").
            Defaults to "mypy".
        strict (bool): Whether to enable strict mode. Defaults to False.
        args (list[str]): Additional arguments to pass to the type checker.

    Examples:
        Run type checking with default settings (mypy):

        >>> from pathlib import Path
        >>> check = TypingCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'typing'

        Configure to use pyright:

        >>> config = {"enabled": True, "tool": "pyright"}
        >>> result = check.run(Path("."), config)

        Enable strict mode:

        >>> config = {"enabled": True, "strict": True}
        >>> result = check.run(Path("."), config)
    """

    name = "typing"
    description = "Run type checking (mypy/pyright)"

    SUPPORTED_TOOLS = ["mypy", "pyright"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run type checking on the package.

        Executes the configured type checker and collects any errors found.
        Errors are reported in the result details (limited to first 10).

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary with options:

                - tool (str): "mypy" or "pyright" (default: "mypy")
                - strict (bool): Enable strict mode (default: False)
                - args (list[str]): Additional arguments for the checker

        Returns:
            A CheckResult with:

            - OK status if no type errors found
            - ERROR status if type errors found or checker fails
            - SKIPPED status if the type checker is not installed
        """
        tool = config.get("tool", "mypy")

        if tool not in self.SUPPORTED_TOOLS:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=f"Unsupported type checker: {tool}",
                details=[f"Supported tools: {', '.join(self.SUPPORTED_TOOLS)}"],
            )

        preamble = [f"Using type checker: {tool}"]

        if tool == "mypy":
            return self._run_mypy(package_path, config, preamble)
        else:  # pyright
            return self._run_pyright(package_path, config, preamble)

    def _run_mypy(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run mypy type checker."""
        if not tool_available("mypy"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="mypy not installed",
                details=["Install mypy: pip install mypy"],
            )

        # Determine target directory
        src_dir = package_path / "src"
        if src_dir.is_dir():
            target = str(src_dir)
        else:
            target = "."

        args = sanitize_args(config.get("args", []))
        strict = config.get("strict", False)

        cmd = ["mypy", target]
        if strict:
            cmd.append("--strict")
        cmd.extend(args)

        result = run_tool(cmd, cwd=package_path, timeout=LONG_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Type checking timed out",
                details=[f"Type checking took longer than {LONG_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run mypy",
                details=[result.error],
            )

        stdout = result.stdout.strip()

        if result.success:
            # Check for "Success" message
            if "Success" in stdout or not stdout:
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.OK,
                    message="No type errors found",
                    details=list(preamble),
                )

        # Parse mypy output for errors
        errors = []
        for line in stdout.split("\n"):
            if ": error:" in line:
                errors.append(line.strip())

        if not errors and "error:" in stdout:
            errors = [line.strip() for line in stdout.split("\n") if line.strip()]

        details = [*preamble, *errors[:MAX_DETAIL_LINES]]

        if len(errors) > MAX_DETAIL_LINES:
            details.append(f"... and {len(errors) - MAX_DETAIL_LINES} more errors")

        error_count = len(errors) if errors else "some"
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message=f"Found {error_count} type error(s)",
            details=details,
        )

    def _run_pyright(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run pyright type checker."""
        if not tool_available("pyright"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="pyright not installed",
                details=["Install pyright: pip install pyright"],
            )

        args = sanitize_args(config.get("args", []))
        strict = config.get("strict", False)

        cmd = ["pyright"]
        if strict:
            cmd.append("--strict")
        cmd.extend(args)

        result = run_tool(cmd, cwd=package_path, timeout=LONG_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Type checking timed out",
                details=[f"Type checking took longer than {LONG_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run pyright",
                details=[result.error],
            )

        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No type errors found",
                details=list(preamble),
            )

        # Parse pyright output
        stdout = result.stdout.strip()
        errors = []
        for line in stdout.split("\n"):
            if " - error:" in line or " - warning:" in line:
                errors.append(line.strip())

        details = [*preamble, *errors[:MAX_DETAIL_LINES]]

        if len(errors) > MAX_DETAIL_LINES:
            details.append(f"... and {len(errors) - MAX_DETAIL_LINES} more errors")

        error_count = len(errors) if errors else "some"
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message=f"Found {error_count} type error(s)",
            details=details,
        )
