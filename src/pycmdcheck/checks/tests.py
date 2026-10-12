"""Test runner check.

This module provides the TestsCheck class which runs package tests
using pytest or unittest and reports the results. Whether the project has
tests at all is reported by the test_suite check, which only reads files.
"""

import os
import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.checks.test_suite import find_tests
from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.fileio import is_regular_file, plain_text
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.subprocess_runner import (
    PYTEST_LOCK,
    SubprocessResult,
    ToolArgsError,
    could_not_run,
    describe_tool,
    find_python,
    missing_tool_hint,
    resolve_timeout,
    run_tool,
    sanitize_args,
    tool_available,
)

# pytest's closing line, with or without its "=" padding:
# "3 passed, 1 skipped in 0.05s" or "no tests ran in 0.01s"
_PYTEST_SUMMARY_RE = re.compile(r"(\b\d+ \w+|no tests ran).* in [\d.]+s\b")

# A line of pytest's short summary: "FAILED tests/test_a.py::test_x - assert 0".
# A captured log record ("ERROR    root:mod.py:7 ...") has padding after the level.
_PYTEST_FAILURE_RE = re.compile(r"^(FAILED|ERROR) \S")

# unittest's closing line: "Ran 5 tests in 0.010s"
_UNITTEST_RAN_RE = re.compile(r"^Ran \d+ tests? in ")


def _cut(lines: list[str]) -> list[str]:
    """Return the first MAX_DETAIL_LINES lines and say how many were left out."""
    if len(lines) <= MAX_DETAIL_LINES:
        return lines
    return [*lines[:MAX_DETAIL_LINES], f"... and {len(lines) - MAX_DETAIL_LINES} more"]


def _failed(count: int) -> Finding:
    """Return the one finding for failed tests, with their number when known."""
    message = f"{count} test failure(s) or error(s)" if count else "Tests failed"
    return Finding("tests.failed", message, CheckStatus.ERROR)


class TestsCheck(BaseCheck):
    """Run package tests and report results.

    Executes the package's test suite using the configured test runner
    (pytest by default) and reports pass/fail status. Supports pytest
    and unittest runners.

    Attributes:
        name: The check identifier ("tests").
        description: Human-readable description of this check.
        rules: The codes of the findings this check can report.
        hints: What to do about each code.

    Configuration Options:
        runner (str): Test runner to use ("pytest" or "unittest").
            Defaults to "pytest".
        args (list[str]): Additional arguments to pass to the test runner.
        timeout (float): Seconds the test run may take. Defaults to 300.

    Examples:
        Run tests with default settings (pytest):

        >>> from pathlib import Path
        >>> check = TestsCheck()
        >>> result = check.run(Path("."), {"enabled": True})  # doctest: +SKIP
        >>> result.name  # doctest: +SKIP
        'tests'

        Configure to use unittest:

        >>> config = {"enabled": True, "runner": "unittest"}
        >>> result = check.run(Path("."), config)  # doctest: +SKIP

        Pass additional pytest arguments:

        >>> config = {"enabled": True, "args": ["-x", "--cov"]}
        >>> result = check.run(Path("."), config)  # doctest: +SKIP
    """

    name = "tests"
    description = "Run package tests (pytest/unittest)"
    rules = {
        "tests.unsupported-runner": "The runner option names an unsupported runner",
        "tests.invalid-args": "The args option is not a list of strings",
        "tests.timed-out": "The tests ran longer than the timeout",
        "tests.could-not-run": "The test runner is installed but could not run",
        "tests.none-collected": "The test runner collected no tests",
        "tests.collection-failed": "pytest could not collect the tests",
        "tests.failed": "Tests failed",
    }
    hints = {
        "tests.unsupported-runner": (
            'Set runner in [tool.pycmdcheck.checks.tests] to "pytest" or "unittest".'
        ),
        "tests.invalid-args": (
            'Write args as a list with one string for each argument, so "-k smoke" '
            'becomes ["-k", "smoke"].'
        ),
        "tests.timed-out": (
            "Set a longer timeout in [tool.pycmdcheck.checks.tests], or in the "
            "--config file when the run uses one."
        ),
        "tests.could-not-run": (
            "Run pytest, or python -m unittest discover -s tests, with the same "
            "args in the project directory and fix the error it prints."
        ),
        "tests.none-collected": (
            "Name test files test_*.py and test functions test_*, and for the "
            "unittest runner put them in a unittest.TestCase class."
        ),
        "tests.collection-failed": (
            "Run pytest --collect-only in the project directory and fix the import "
            "or syntax error it prints, or install the package first if pytest "
            "cannot import it."
        ),
        "tests.failed": (
            "Run pytest, or python -m unittest discover -s tests, in the project "
            "directory and fix each test that fails."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run tests and return results.

        Executes the test suite when the project has one, in any of the
        places the test_suite check looks. The test runner is determined by
        the ``runner`` config option.

        Args:
            package_path: Path to the package directory containing tests.
            config: Configuration dictionary with options:

                - runner (str): "pytest" or "unittest" (default: "pytest")
                - args (list[str]): Additional arguments for the runner
                - timeout (float): Time limit in seconds (default: 300)

        Returns:
            A CheckResult with:

            - OK status if all tests pass
            - WARNING status if no tests are collected, or the runner is
              installed but could not run
            - ERROR status if tests fail, cannot be collected or time out,
              or an unknown runner or an invalid option is configured
            - SKIPPED status if the test runner is not installed
              ("tool-missing"), or the project has no tests
              ("prerequisite-failed"), which the test_suite check reports
        """
        runner = config.get("runner", "pytest")

        tests_at = find_tests(package_path)
        if tests_at is None:
            return CheckResult.skip(
                self.name,
                "No tests to run",
                "prerequisite-failed",
                ["The test_suite check reports the missing tests"],
            )

        if runner not in ("pytest", "unittest"):
            message = f"Unknown test runner: {plain_text(str(runner))}"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=message,
                findings=[
                    Finding("tests.unsupported-runner", message, CheckStatus.ERROR)
                ],
            )

        if runner == "pytest" and not tool_available("pytest"):
            return CheckResult.skip(
                self.name,
                "pytest not installed",
                "tool-missing",
                [missing_tool_hint("pytest")],
            )

        try:
            args = sanitize_args(config.get("args"), check=self.name)
        except ToolArgsError as exc:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Invalid args option",
                details=[str(exc)],
                findings=[Finding("tests.invalid-args", str(exc), CheckStatus.ERROR)],
            )
        timeout, note = resolve_timeout(config, LONG_TIMEOUT, check=self.name)

        if runner == "pytest":
            preamble = [f"Using test runner: {describe_tool('pytest')}"]
            # The doctests check runs pytest in the same directory. Two runs
            # at once corrupt each other's coverage and cache files.
            with PYTEST_LOCK:
                result = run_tool(
                    ["pytest", "-v", "--tb=short", *args],
                    cwd=package_path,
                    timeout=timeout,
                )
        else:
            preamble = ["Using test runner: unittest"]
            cmd = [find_python(), "-m", "unittest", "discover", "-v"]
            # A test directory without __init__.py cannot be imported from the
            # project root, so discovery has to start inside it.
            if (
                os.path.isdir(tests_at)
                and not is_regular_file(tests_at / "__init__.py")
                and not any(a.startswith(("-s", "--start-directory")) for a in args)
            ):
                cmd += ["-s", os.path.relpath(tests_at, package_path)]
            result = run_tool([*cmd, *args], cwd=package_path, timeout=timeout)
        if note:
            preamble.append(note)

        if result.timed_out:
            detail = f"Tests took longer than {timeout} seconds"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Tests timed out",
                details=[detail],
                findings=[Finding("tests.timed-out", detail, CheckStatus.ERROR)],
            )

        if result.error is not None:
            return could_not_run(self.name, runner, result, preamble)

        if runner == "pytest":
            return self._pytest_result(result, preamble)
        return self._unittest_result(result, preamble)

    def _pytest_result(
        self, result: SubprocessResult, preamble: list[str]
    ) -> CheckResult:
        """Turn a finished pytest run into a result."""
        # The summary is the last line, so it is searched for from the end:
        # with -v a test id that holds "passed" would match first.
        lines = result.output_lines()
        summary = next(
            (ln.strip("= ") for ln in reversed(lines) if _PYTEST_SUMMARY_RE.search(ln)),
            None,
        )
        tail = [summary] if summary else []
        code = result.returncode

        if code == 0:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All tests passed",
                details=[*preamble, *tail],
            )

        if code == 5:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No tests collected",
                details=[*preamble, "pytest ran but found no tests", *tail],
                findings=[
                    Finding(
                        "tests.none-collected",
                        "pytest ran but found no tests",
                        CheckStatus.WARNING,
                    )
                ],
            )

        # Exit code 1 means failed tests. 2 means that pytest stopped the
        # run, which it does when collection fails, for example because a
        # test module cannot be imported. 3 is an internal error and 4 a
        # usage error: no verdict on the tests themselves. Nor is there one
        # when pytest printed nothing on stdout and wrote to stderr, which
        # is a crash or a pytest.exit() before the session started.
        if code not in (1, 2) or (not lines and result.stderr.strip()):
            return could_not_run(self.name, "pytest", result, preamble)

        if code == 2:
            # pytest reports on stdout, so the end of that is what is shown.
            report = [ln.rstrip() for ln in lines if ln.strip()]
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Tests could not be collected",
                details=[
                    *preamble,
                    "pytest exited with code 2",
                    *report[-MAX_DETAIL_LINES:],
                ],
                findings=[
                    Finding(
                        "tests.collection-failed",
                        "pytest could not collect the tests",
                        CheckStatus.ERROR,
                    )
                ],
            )

        # The lines of pytest's short summary, without separators and logs.
        failures = [ln.strip() for ln in lines if _PYTEST_FAILURE_RE.match(ln)]
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Tests failed",
            details=[*preamble, *_cut(failures), *tail],
            findings=[_failed(len(failures))],
        )

    def _unittest_result(
        self, result: SubprocessResult, preamble: list[str]
    ) -> CheckResult:
        """Turn a finished unittest run into a result (it reports on stderr)."""
        lines = result.stderr_lines()
        ran = [ln.strip() for ln in lines if _UNITTEST_RAN_RE.match(ln)][-1:]

        # Python 3.12 and later exit with 5 when nothing ran, earlier
        # versions print "Ran 0 tests" and exit with 0.
        if result.returncode == 5 or any(s.startswith("Ran 0 tests") for s in ran):
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No tests collected",
                details=[*preamble, "unittest discovery found no tests", *ran],
                findings=[
                    Finding(
                        "tests.none-collected",
                        "unittest discovery found no tests",
                        CheckStatus.WARNING,
                    )
                ],
            )

        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All tests passed",
                details=[*preamble, *ran],
            )

        # Only exit code 1 after a "Ran N tests" line is a verdict on the
        # tests. 2 is a usage error, and a run that ended before any test
        # (a start directory that cannot be imported) prints no such line.
        if result.returncode != 1 or not ran:
            return could_not_run(self.name, "unittest", result, preamble)

        failures = [ln.strip() for ln in lines if ln.startswith(("FAIL:", "ERROR:"))]
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Tests failed",
            details=[*preamble, *_cut(failures or result.tail())],
            findings=[_failed(len(failures))],
        )
