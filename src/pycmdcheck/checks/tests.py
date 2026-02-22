"""Test runner check.

This module provides the TestsCheck class which runs package tests
using pytest or unittest and reports the results.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import run_tool, sanitize_args, tool_available


class TestsCheck(BaseCheck):
    """Run package tests and report results.

    Executes the package's test suite using the configured test runner
    (pytest by default) and reports pass/fail status. Supports pytest
    and unittest runners.

    Attributes:
        name: The check identifier ("tests").
        description: Human-readable description of this check.

    Configuration Options:
        runner (str): Test runner to use ("pytest" or "unittest").
            Defaults to "pytest".
        args (list[str]): Additional arguments to pass to the test runner.

    Examples:
        Run tests with default settings (pytest):

        >>> from pathlib import Path
        >>> check = TestsCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'tests'

        Configure to use unittest:

        >>> config = {"enabled": True, "runner": "unittest"}
        >>> result = check.run(Path("."), config)

        Pass additional pytest arguments:

        >>> config = {"enabled": True, "args": ["-x", "--cov"]}
        >>> result = check.run(Path("."), config)
    """

    name = "tests"
    description = "Run package tests (pytest/unittest)"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run tests and return results.

        Executes the test suite in the package's tests/ or test/ directory.
        The test runner is determined by the ``runner`` config option.

        Args:
            package_path: Path to the package directory containing tests.
            config: Configuration dictionary with options:

                - runner (str): "pytest" or "unittest" (default: "pytest")
                - args (list[str]): Additional arguments for the runner

        Returns:
            A CheckResult with:

            - OK status if all tests pass
            - WARNING status if no tests found
            - ERROR status if tests fail or runner not installed
            - SKIPPED status if the test runner is not installed
        """
        runner = config.get("runner", "pytest")

        # Check if test directory exists
        test_dirs = ["tests", "test"]
        test_dir_found = None
        for test_dir in test_dirs:
            if (package_path / test_dir).is_dir():
                test_dir_found = test_dir
                break

        # Also check for test files at root
        test_files = list(package_path.glob("test_*.py"))

        if not test_dir_found and not test_files:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No tests found",
                details=["Consider adding tests in a 'tests/' directory"],
            )

        preamble = [f"Using test runner: {runner}"]

        if runner == "pytest":
            return self._run_pytest(package_path, config, preamble)
        elif runner == "unittest":
            return self._run_unittest(package_path, config, preamble)
        else:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=f"Unknown test runner: {runner}",
                details=["Supported runners: pytest, unittest"],
            )

    def _run_pytest(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run pytest."""
        if not tool_available("pytest"):
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="pytest not installed",
                details=["Install pytest: pip install pytest"],
            )

        args = sanitize_args(config.get("args", []))
        cmd = ["pytest", "-v", "--tb=short", *args]

        result = run_tool(cmd, cwd=package_path, timeout=LONG_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Tests timed out",
                details=[f"Tests took longer than {LONG_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run tests",
                details=[result.error],
            )

        if result.success:
            # Parse pytest output for summary
            details = list(preamble)
            for line in result.stdout.split("\n"):
                if "passed" in line or "skipped" in line:
                    details.append(line.strip())
                    break

            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All tests passed",
                details=details,
            )

        # Tests failed
        output = result.stdout + result.stderr
        failure_lines: list[str] = []
        for line in output.split("\n"):
            if "FAILED" in line or "ERROR" in line:
                failure_lines.append(line.strip())

        details = [*preamble, *failure_lines[:MAX_DETAIL_LINES]]
        if len(failure_lines) > MAX_DETAIL_LINES:
            details.append("... (more failures omitted)")

        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Tests failed",
            details=details,
        )

    def _run_unittest(
        self, package_path: Path, config: dict[str, Any], preamble: list[str]
    ) -> CheckResult:
        """Run unittest discovery."""
        args = sanitize_args(config.get("args", []))
        cmd = ["python", "-m", "unittest", "discover", "-v", *args]

        result = run_tool(cmd, cwd=package_path, timeout=LONG_TIMEOUT)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Tests timed out",
                details=[f"Tests took longer than {LONG_TIMEOUT} seconds"],
            )

        if result.error is not None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Failed to run tests",
                details=[result.error],
            )

        if result.success:
            # Parse output for test count (unittest outputs to stderr)
            details = list(preamble)
            for line in result.stderr.split("\n"):
                if "Ran" in line:
                    details.append(line.strip())
                    break

            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All tests passed",
                details=details,
            )

        # Parse failures from stderr
        failure_lines: list[str] = []
        for line in result.stderr.split("\n"):
            if "FAIL:" in line or "ERROR:" in line:
                failure_lines.append(line.strip())

        details = [*preamble, *failure_lines[:MAX_DETAIL_LINES]]

        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Tests failed",
            details=details,
        )
