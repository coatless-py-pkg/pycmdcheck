"""Code formatting check."""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import run_tool, sanitize_args, tool_available


class FormattingCheck(BaseCheck):
    """Check code formatting without modifying files."""

    name = "formatting"
    description = "Check code formatting (ruff format/black)"

    SUPPORTED_TOOLS = ["ruff", "black"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run formatting check."""
        tool = config.get("tool", "ruff")

        if tool not in self.SUPPORTED_TOOLS:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=f"Unsupported formatting tool: {tool}",
                details=[f"Supported tools: {', '.join(self.SUPPORTED_TOOLS)}"],
            )

        if tool == "ruff":
            return self._run_ruff_format(package_path, config)
        else:
            return self._run_black(package_path, config)

    def _run_ruff_format(
        self, package_path: Path, config: dict[str, Any]
    ) -> CheckResult:
        """Run ruff format --check."""
        if not tool_available("ruff"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="ruff not installed",
                details=["Install ruff: pip install ruff"],
            )

        args = sanitize_args(config.get("args", []))
        result = run_tool(
            ["ruff", "format", "--check", ".", *args],
            cwd=package_path,
            timeout=DEFAULT_TIMEOUT,
        )

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Formatting check timed out",
            )
        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run ruff format",
                details=[result.error],
            )
        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Code is properly formatted",
                details=["Using formatter: ruff"],
            )

        # Files need reformatting
        lines = result.output_lines()
        reformat_lines = [ln for ln in lines if "would reformat" in ln.lower()]
        file_count = len(reformat_lines)
        details = ["Using formatter: ruff"]
        if reformat_lines:
            details.extend(reformat_lines[:MAX_DETAIL_LINES])
            if file_count > MAX_DETAIL_LINES:
                details.append(f"... and {file_count - MAX_DETAIL_LINES} more files")
        else:
            # Tool failed but output doesn't list specific files
            details.extend(lines[:MAX_DETAIL_LINES])

        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=(
                f"{file_count} file(s) need reformatting"
                if file_count
                else "Code formatting issues found"
            ),
            details=details,
        )

    def _run_black(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run black --check."""
        if not tool_available("black"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="black not installed",
                details=["Install black: pip install black"],
            )

        args = sanitize_args(config.get("args", []))
        result = run_tool(
            ["black", "--check", ".", *args],
            cwd=package_path,
            timeout=DEFAULT_TIMEOUT,
        )

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Formatting check timed out",
            )
        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run black",
                details=[result.error],
            )
        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Code is properly formatted",
                details=["Using formatter: black"],
            )

        lines = result.output_lines()
        reformat_lines = [ln for ln in lines if "would reformat" in ln.lower()]
        file_count = len(reformat_lines)
        details = ["Using formatter: black"]
        if reformat_lines:
            details.extend(reformat_lines[:MAX_DETAIL_LINES])
            if file_count > MAX_DETAIL_LINES:
                details.append(f"... and {file_count - MAX_DETAIL_LINES} more files")
        else:
            details.extend(lines[:MAX_DETAIL_LINES])

        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=(
                f"{file_count} file(s) need reformatting"
                if file_count
                else "Code formatting issues found"
            ),
            details=details,
        )
