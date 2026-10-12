"""Tests for the tests check."""

import re
import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pycmdcheck.checks.tests import TestsCheck
from pycmdcheck.config import get_check_config, load_config
from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import PYTEST_LOCK, SubprocessResult
from tests.helpers import only_code, write_files

# The tool fixture in conftest.py reads this to know which module to patch.
MODULE = "pycmdcheck.checks.tests"

# "pytest -v --tb=short" with one failing test, one collection error and a
# captured log line (pytest 9).
PYTEST_FAILURE_OUTPUT = """\
============================= test session starts ==============================
tests/test_a.py::test_status_passed_is_reported PASSED                   [ 50%]
tests/test_a.py::test_backend FAILED                                     [100%]
==================================== ERRORS ====================================
______________________ ERROR collecting tests/test_b.py ________________________
ERROR    x:test_a.py:7 backend said ERROR 500
=========================== short test summary info ============================
FAILED tests/test_a.py::test_backend - assert 500 == 200
ERROR tests/test_b.py - ModuleNotFoundError: No module named 'missing'
==================== 1 failed, 1 passed, 1 error in 0.12s ======================
"""

# "pytest -v --tb=short" with a test module that cannot be imported, which
# ends the run with exit code 2 (pytest 9).
PYTEST_COLLECTION_OUTPUT = """\
============================= test session starts ==============================
collecting ... collected 1 item / 1 error

==================================== ERRORS ====================================
_______________________ ERROR collecting tests/test_b.py _______________________
ImportError while importing test module '/work/demo/tests/test_b.py'.
Hint: make sure your test modules/packages have valid Python names.
Traceback:
/usr/lib/python3.12/importlib/__init__.py:90: in import_module
    return _bootstrap._gcd_import(name[level:], package, level)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_b.py:1: in <module>
    import missing
E   ModuleNotFoundError: No module named 'missing'
=========================== short test summary info ============================
ERROR tests/test_b.py
!!!!!!!!!!!!!!!!!!!! Interrupted: 1 error during collection !!!!!!!!!!!!!!!!!!!!
=============================== 1 error in 0.06s ===============================
"""


@pytest.fixture
def tool(
    tool: Callable[..., MagicMock], monkeypatch: pytest.MonkeyPatch
) -> Callable[..., MagicMock]:
    """Add a stand-in for the interpreter to the tool fixture of conftest.py."""
    monkeypatch.setattr(f"{MODULE}.find_python", lambda: "/env/bin/python")
    return tool


def run_with_table(root: Path, options: str) -> CheckResult:
    """Run the check with *options* as its table in the project's pyproject.toml."""
    table = f"[tool.pycmdcheck.checks.tests]\n{options}\n"
    (root / "pyproject.toml").write_text(table)
    return TestsCheck().run(root, get_check_config(load_config(root), "tests"))


class TestDetection:
    """Where the check looks for a test suite before it runs anything."""

    def test_no_tests_is_left_to_the_test_suite_check(
        self, minimal_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = TestsCheck().run(minimal_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "prerequisite-failed"
        assert result.message == "No tests to run"
        assert result.details == ["The test_suite check reports the missing tests"]
        assert result.findings == []
        run.assert_not_called()

    def test_tests_outside_a_root_tests_directory_are_found(
        self, minimal_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool(SubprocessResult(returncode=0, stdout="1 passed in 0.01s\n"))
        write_files(minimal_package, {"src/minimal/test/test_one.py": ""})
        result = TestsCheck().run(minimal_package, {})
        assert result.status == CheckStatus.OK
        assert result.findings == []
        assert result.skip_reason is None
        run.assert_called_once()


class TestOptions:
    """Options shared by both runners."""

    def test_unknown_runner(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = TestsCheck().run(temp_package, {"runner": "nose"})
        assert result.status == CheckStatus.ERROR
        assert "Unknown test runner" in result.message
        assert only_code(result) == "tests.unsupported-runner"
        assert result.details == []
        run.assert_not_called()

    @pytest.mark.parametrize("runner", ["pytest", "unittest"])
    def test_invalid_args_option(
        self, temp_package: Path, tool: Callable[..., MagicMock], runner: str
    ) -> None:
        run = tool()
        result = TestsCheck().run(temp_package, {"runner": runner, "args": "-x"})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "tests.invalid-args"
        assert any("list of strings" in d for d in result.details)
        run.assert_not_called()

    @pytest.mark.parametrize("runner", ["pytest", "unittest"])
    def test_timeout_option(
        self, temp_package: Path, tool: Callable[..., MagicMock], runner: str
    ) -> None:
        run = tool(SubprocessResult(timed_out=True))
        result = TestsCheck().run(temp_package, {"runner": runner, "timeout": 900})
        assert run.call_args.kwargs == {"cwd": temp_package, "timeout": 900}
        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests timed out"
        assert only_code(result) == "tests.timed-out"
        assert "900 seconds" in result.findings[0].message
        assert any("900 seconds" in d for d in result.details)

    def test_invalid_timeout_uses_the_default(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = TestsCheck().run(temp_package, {"timeout": 0})
        assert run.call_args.kwargs["timeout"] == LONG_TIMEOUT
        assert any("Ignored timeout" in d for d in result.details)


class TestHints:
    """Doing what a hint says makes its rule stop firing.

    Only the hints about options are here, each written to pyproject.toml the
    way the hint spells it. Following the others changes what the test runner
    reports, and these tests do not start it.
    """

    def test_every_runner_the_hint_names_is_accepted(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        result = run_with_table(temp_package, 'runner = "nose"')
        assert only_code(result) == "tests.unsupported-runner"
        named = re.findall(r'"(\w+)"', TestsCheck.hints["tests.unsupported-runner"])
        assert named == ["pytest", "unittest"]
        for runner in named:
            result = run_with_table(temp_package, f'runner = "{runner}"')
            assert result.findings == []

    def test_args_written_as_the_hint_shows_are_accepted(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        hint = TestsCheck.hints["tests.invalid-args"]
        wrong, right = re.search(r'so (".+") becomes (\[.+\])\.$', hint).groups()
        result = run_with_table(temp_package, f"args = {wrong}")
        assert only_code(result) == "tests.invalid-args"
        result = run_with_table(temp_package, f"args = {right}")
        assert result.findings == []

    def test_longer_timeout_lets_a_slow_run_finish(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The runner is a stub that needs 400 seconds, more than the default."""
        run = tool()
        run.side_effect = lambda cmd, **kw: SubprocessResult(
            returncode=0, timed_out=kw["timeout"] < 400
        )
        assert only_code(run_with_table(temp_package, "")) == "tests.timed-out"
        # run_with_table writes the option to the table that the hint names.
        assert TestsCheck.hints["tests.timed-out"].startswith(
            "Set a longer timeout in [tool.pycmdcheck.checks.tests]"
        )
        assert run_with_table(temp_package, "timeout = 900").findings == []


class TestPytestRunner:
    """The pytest runner, with the tool replaced."""

    def test_not_installed(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool(available=False)
        result = TestsCheck().run(temp_package, {"runner": "pytest"})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "tool-missing"
        assert result.findings == []
        assert result.message == "pytest not installed"
        assert any("pip install pytest" in d for d in result.details)
        run.assert_not_called()

    def test_command_runs_under_the_pytest_lock(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """tests and doctests must not run pytest in one directory at once."""
        free_elsewhere: list[bool] = []

        def probe() -> None:
            free = PYTEST_LOCK.acquire(blocking=False)
            free_elsewhere.append(free)
            if free:
                PYTEST_LOCK.release()

        def fake_run_tool(cmd: list[str], **kwargs: object) -> SubprocessResult:
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            return SubprocessResult(returncode=0)

        run = tool()
        run.side_effect = fake_run_tool
        TestsCheck().run(temp_package, {"args": ["-x"]})
        assert run.call_args.args[0] == ["pytest", "-v", "--tb=short", "-x"]
        assert free_elsewhere == [False]

    def test_summary_is_read_from_the_end_of_the_output(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """A test id that holds 'passed' or 'skipped' is not the summary."""
        stdout = (
            "tests/test_a.py::test_skipped_checks_are_counted PASSED          [ 50%]\n"
            "tests/test_passed_all.py::test_last PASSED                       [100%]\n"
            "============================== 2 passed in 0.01s ===============\n"
        )
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = TestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.message == "All tests passed"
        assert result.details == ["Using test runner: pytest 9.9", "2 passed in 0.01s"]

    def test_failures_list_only_the_short_summary_lines(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool(SubprocessResult(returncode=1, stdout=PYTEST_FAILURE_OUTPUT))
        result = TestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests failed"
        assert only_code(result) == "tests.failed"
        assert result.findings[0].message == "2 test failure(s) or error(s)"
        assert result.details[1:] == [
            "FAILED tests/test_a.py::test_backend - assert 500 == 200",
            "ERROR tests/test_b.py - ModuleNotFoundError: No module named 'missing'",
            "1 failed, 1 passed, 1 error in 0.12s",
        ]

    def test_long_failure_list_says_how_many_were_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        lines = [
            f"FAILED tests/test_a.py::test_{n}" for n in range(MAX_DETAIL_LINES + 5)
        ]
        tool(SubprocessResult(returncode=1, stdout="\n".join(lines)))
        result = TestsCheck().run(temp_package, {})
        assert result.details[-1] == "... and 5 more"
        # one finding for the rule, with the count, not one for each test
        assert only_code(result) == "tests.failed"
        count = MAX_DETAIL_LINES + 5
        assert result.findings[0].message == f"{count} test failure(s) or error(s)"

    def test_failure_without_a_short_summary_has_no_count(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """With -r set by the project, pytest may list no FAILED line."""
        tool(SubprocessResult(returncode=1, stdout="1 failed in 0.01s\n"))
        result = TestsCheck().run(temp_package, {})
        assert only_code(result) == "tests.failed"
        assert result.findings[0].message == "Tests failed"

    def test_collection_failure_is_an_error(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Exit code 2 fails the suite, and pytest's own report is shown."""
        tool(
            SubprocessResult(
                returncode=2,
                stdout=PYTEST_COLLECTION_OUTPUT,
                stderr="ResourceWarning: unclosed file\n",
            )
        )
        result = TestsCheck().run(temp_package, {})
        report = [line for line in PYTEST_COLLECTION_OUTPUT.splitlines() if line]
        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests could not be collected"
        assert only_code(result) == "tests.collection-failed"
        assert result.details == [
            "Using test runner: pytest 9.9",
            "pytest exited with code 2",
            *report[-MAX_DETAIL_LINES:],
        ]
        assert "E   ModuleNotFoundError: No module named 'missing'" in result.details

    def test_no_tests_collected_is_a_warning(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool(SubprocessResult(returncode=5, stdout="no tests ran in 0.00s\n"))
        result = TestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert result.message == "No tests collected"
        assert only_code(result) == "tests.none-collected"
        assert len(result.details) > 1


class TestCouldNotRun:
    """A runner that could not do its job is a WARNING, not failed tests."""

    @pytest.mark.parametrize(
        ("runner", "outcome", "reason"),
        [
            (
                "pytest",
                SubprocessResult(error="Could not start pytest: denied"),
                ["Could not start pytest: denied"],
            ),
            # pytest.exit() in a conftest.py: exit code 2, but no report
            (
                "pytest",
                SubprocessResult(returncode=2, stderr="Exit: needs a database\n"),
                ["pytest exited with code 2", "Exit: needs a database"],
            ),
            (
                "pytest",
                SubprocessResult(returncode=3, stdout="INTERNALERROR> boom\n"),
                ["pytest exited with code 3", "INTERNALERROR> boom"],
            ),
            (
                "pytest",
                SubprocessResult(
                    returncode=4, stderr="pytest: error: unrecognized arguments: -n\n"
                ),
                [
                    "pytest exited with code 4",
                    "pytest: error: unrecognized arguments: -n",
                ],
            ),
            (
                "pytest",
                SubprocessResult(returncode=1, stderr="ImportError: bad plugin\n"),
                ["pytest exited with code 1", "ImportError: bad plugin"],
            ),
            (
                "unittest",
                SubprocessResult(error="Could not start /env/bin/python: denied"),
                ["Could not start /env/bin/python: denied"],
            ),
            (
                "unittest",
                SubprocessResult(
                    returncode=2, stderr="python -m unittest: error: no such option\n"
                ),
                [
                    "unittest exited with code 2",
                    "python -m unittest: error: no such option",
                ],
            ),
            (
                "unittest",
                SubprocessResult(
                    returncode=1,
                    stderr="ImportError: Start directory is not importable: 'x'\n",
                ),
                [
                    "unittest exited with code 1",
                    "ImportError: Start directory is not importable: 'x'",
                ],
            ),
        ],
        ids=[
            "pytest-not-started",
            "pytest-exit-without-report",
            "pytest-internal",
            "pytest-usage",
            "pytest-crash",
            "unittest-not-started",
            "unittest-usage",
            "unittest-crash",
        ],
    )
    def test_reports_the_exit_code_and_the_end_of_the_output(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        runner: str,
        outcome: SubprocessResult,
        reason: list[str],
    ) -> None:
        tool(outcome)
        result = TestsCheck().run(temp_package, {"runner": runner})
        using = "pytest 9.9" if runner == "pytest" else "unittest"
        assert result.status == CheckStatus.WARNING
        assert result.message == f"{runner} could not run"
        assert only_code(result) == "tests.could-not-run"
        assert result.findings[0].message == reason[0]
        assert result.details == [f"Using test runner: {using}", *reason]


class TestUnittestRunner:
    """The unittest runner, with the interpreter replaced."""

    @pytest.mark.parametrize(
        ("has_init", "args", "expected"),
        [
            (True, [], []),
            (False, [], ["-s", "tests"]),
            (False, ["-s", "other"], ["-s", "other"]),
            (False, ["--start-directory=other"], ["--start-directory=other"]),
        ],
    )
    def test_command(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        has_init: bool,
        args: list[str],
        expected: list[str],
    ) -> None:
        """-s is added only for a test directory that cannot be imported."""
        if not has_init:
            (temp_package / "tests" / "__init__.py").unlink()
        run = tool(
            SubprocessResult(returncode=0, stderr="Ran 1 test in 0.001s\n\nOK\n")
        )
        result = TestsCheck().run(temp_package, {"runner": "unittest", "args": args})
        base = ["/env/bin/python", "-m", "unittest", "discover", "-v"]
        assert run.call_args.args[0] == [*base, *expected]
        assert result.status == CheckStatus.OK
        assert result.details == ["Using test runner: unittest", "Ran 1 test in 0.001s"]

    @pytest.mark.parametrize(
        ("code", "stderr"),
        [
            (5, "Ran 0 tests in 0.000s\n\nNO TESTS RAN\n"),
            (0, "Ran 0 tests in 0.000s\n\nOK\n"),
        ],
        ids=["python-3.12", "python-3.11"],
    )
    def test_no_tests_ran_is_a_warning(
        self, temp_package: Path, tool: Callable[..., MagicMock], code: int, stderr: str
    ) -> None:
        tool(SubprocessResult(returncode=code, stderr=stderr))
        result = TestsCheck().run(temp_package, {"runner": "unittest"})
        assert result.status == CheckStatus.WARNING
        assert result.message == "No tests collected"
        assert only_code(result) == "tests.none-collected"
        assert "Ran 0 tests in 0.000s" in result.details

    def test_failures_are_listed_with_a_truncation_notice(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        count = MAX_DETAIL_LINES + 2
        stderr = "".join(
            f"FAIL: test_{n} (test_mod.Case.test_{n})\n" for n in range(count)
        )
        stderr += f"Ran {count} tests in 0.01s\n\nFAILED (failures={count})\n"
        tool(SubprocessResult(returncode=1, stderr=stderr))
        result = TestsCheck().run(temp_package, {"runner": "unittest"})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests failed"
        assert only_code(result) == "tests.failed"
        assert result.findings[0].message == f"{count} test failure(s) or error(s)"
        assert "FAIL: test_0 (test_mod.Case.test_0)" in result.details
        assert result.details[-1] == "... and 2 more"

    @pytest.mark.parametrize(
        ("stderr", "counted", "listed"),
        [
            (
                "ERROR: test_a (test_mod.Case.test_a)\n"
                "Ran 1 test in 0.002s\n\nFAILED (errors=1)\n",
                "1 test failure(s) or error(s)",
                ["ERROR: test_a (test_mod.Case.test_a)"],
            ),
            # an unexpected success fails the run under neither heading
            (
                "UNEXPECTED SUCCESS: test_a (test_mod.Case.test_a)\n"
                "Ran 1 test in 0.001s\n\nFAILED (unexpected successes=1)\n",
                "Tests failed",
                [
                    "UNEXPECTED SUCCESS: test_a (test_mod.Case.test_a)",
                    "Ran 1 test in 0.001s",
                    "FAILED (unexpected successes=1)",
                ],
            ),
        ],
        ids=["error", "unexpected-success"],
    )
    def test_failed_run_without_a_fail_line(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        stderr: str,
        counted: str,
        listed: list[str],
    ) -> None:
        """An ERROR line counts as a FAIL line does. A run can fail with neither."""
        tool(SubprocessResult(returncode=1, stderr=stderr))
        result = TestsCheck().run(temp_package, {"runner": "unittest"})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests failed"
        assert only_code(result) == "tests.failed"
        assert result.findings[0].message == counted
        assert result.details[1:] == listed


class TestRealPytest:
    """The one test here that starts pytest, to read what it really prints."""

    @pytest.mark.tools("pytest")
    def test_failed_test_is_listed(self, tmp_path: Path) -> None:
        test = "def test_sum():\n    assert 1 + 1 == 3\n"
        write_files(tmp_path, {"tests/test_demo.py": test})
        result = TestsCheck().run(tmp_path, {"timeout": 60})
        assert result.message == "Tests failed"
        assert result.details[1].startswith("FAILED tests/test_demo.py::test_sum")
