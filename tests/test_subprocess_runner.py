"""Tests for the shared subprocess runner utility and subprocess-based checks.

This module contains two groups of tests:

1. Tests for ``pycmdcheck.subprocess_runner`` -- the shared utility module that
   provides ``SubprocessResult``, ``tool_available``, and ``run_tool``.

2. Tests for the individual check modules (tests, linting, typing) that invoke
   external tools via the subprocess runner.  These mock ``tool_available`` and
   ``run_tool`` so the tests do not depend on whether ruff, mypy, or pytest are
   actually installed.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pycmdcheck.results import CheckStatus
from pycmdcheck.subprocess_runner import (
    SubprocessResult,
    run_tool,
    sanitize_args,
    tool_available,
)

# ===========================================================================
# SubprocessResult dataclass tests
# ===========================================================================


class TestSubprocessResult:
    """Test the SubprocessResult dataclass properties and behaviour."""

    def test_default_values(self) -> None:
        """A default-constructed result should have sensible defaults."""
        result = SubprocessResult()
        assert result.returncode == -1
        assert result.stdout == ""
        assert result.stderr == ""
        assert result.timed_out is False
        assert result.error is None

    def test_success_when_returncode_zero(self) -> None:
        """success should be True when returncode is 0 and no timeout/error."""
        result = SubprocessResult(returncode=0)
        assert result.success is True

    def test_not_success_when_nonzero_returncode(self) -> None:
        """success should be False when returncode is non-zero."""
        result = SubprocessResult(returncode=1)
        assert result.success is False

    def test_not_success_when_timed_out(self) -> None:
        """success should be False when timed_out is True, even if returncode is 0."""
        result = SubprocessResult(returncode=0, timed_out=True)
        assert result.success is False

    def test_not_success_when_error(self) -> None:
        """success should be False when error is set, even if returncode is 0."""
        result = SubprocessResult(returncode=0, error="something went wrong")
        assert result.success is False

    def test_not_success_default(self) -> None:
        """Default result (returncode=-1) should not be successful."""
        result = SubprocessResult()
        assert result.success is False

    def test_output_combines_stdout_and_stderr(self) -> None:
        """output should concatenate stdout and stderr."""
        result = SubprocessResult(returncode=0, stdout="hello\n", stderr="world\n")
        assert result.output == "hello\nworld\n"

    def test_output_stdout_only(self) -> None:
        """output should return just stdout when stderr is empty."""
        result = SubprocessResult(returncode=0, stdout="hello\n", stderr="")
        assert result.output == "hello\n"

    def test_output_stderr_only(self) -> None:
        """output should return just stderr when stdout is empty."""
        result = SubprocessResult(returncode=0, stdout="", stderr="error\n")
        assert result.output == "error\n"

    def test_output_both_empty(self) -> None:
        """output should return empty string when both are empty."""
        result = SubprocessResult(returncode=0, stdout="", stderr="")
        assert result.output == ""

    def test_output_lines_basic(self) -> None:
        """output_lines should split stdout into lines."""
        result = SubprocessResult(returncode=0, stdout="line1\nline2\nline3\n")
        assert result.output_lines() == ["line1", "line2", "line3"]

    def test_output_lines_strip_blank_true(self) -> None:
        """output_lines should strip blank lines by default."""
        result = SubprocessResult(returncode=0, stdout="line1\n\nline2\n\n\nline3\n")
        assert result.output_lines(strip_blank=True) == [
            "line1",
            "line2",
            "line3",
        ]

    def test_output_lines_strip_blank_false(self) -> None:
        """output_lines with strip_blank=False should keep blank lines."""
        result = SubprocessResult(returncode=0, stdout="line1\n\nline2\n")
        assert result.output_lines(strip_blank=False) == [
            "line1",
            "",
            "line2",
        ]

    def test_output_lines_empty_stdout(self) -> None:
        """output_lines should return empty list for empty stdout."""
        result = SubprocessResult(returncode=0, stdout="")
        assert result.output_lines() == []

    def test_output_lines_only_blanks(self) -> None:
        """strip_blank=True returns empty list for all-blank stdout."""
        result = SubprocessResult(returncode=0, stdout="\n\n\n")
        assert result.output_lines(strip_blank=True) == []

    def test_frozen(self) -> None:
        """SubprocessResult should be frozen (immutable)."""
        result = SubprocessResult(returncode=0)
        with pytest.raises(AttributeError):
            result.returncode = 1  # type: ignore[misc]


# ===========================================================================
# tool_available tests
# ===========================================================================


class TestToolAvailable:
    """Test the tool_available helper."""

    def test_tool_exists(self) -> None:
        """tool_available returns True when shutil.which finds the tool."""
        with patch(
            "pycmdcheck.subprocess_runner.shutil.which",
            return_value="/usr/bin/ruff",
        ):
            assert tool_available("ruff") is True

    def test_tool_missing(self) -> None:
        """tool_available returns False when shutil.which returns None."""
        with patch(
            "pycmdcheck.subprocess_runner.shutil.which",
            return_value=None,
        ):
            assert tool_available("nonexistent-tool") is False


# ===========================================================================
# run_tool tests
# ===========================================================================


class TestRunTool:
    """Test the run_tool subprocess wrapper."""

    def test_successful_command(self) -> None:
        """run_tool returns success result for a zero-exit command."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "all good\n"
        mock_proc.stderr = ""

        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            return_value=mock_proc,
        ):
            result = run_tool(["echo", "hello"], cwd="/tmp")

        assert result.success is True
        assert result.returncode == 0
        assert result.stdout == "all good\n"
        assert result.stderr == ""
        assert result.timed_out is False
        assert result.error is None

    def test_failing_command(self) -> None:
        """run_tool returns non-success result for a non-zero exit command."""
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stdout = ""
        mock_proc.stderr = "error occurred\n"

        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            return_value=mock_proc,
        ):
            result = run_tool(["false"], cwd="/tmp")

        assert result.success is False
        assert result.returncode == 1
        assert result.stderr == "error occurred\n"
        assert result.timed_out is False
        assert result.error is None

    def test_timeout(self) -> None:
        """run_tool returns timed_out=True when subprocess times out."""
        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="sleep", timeout=5),
        ):
            result = run_tool(["sleep", "100"], cwd="/tmp", timeout=5)

        assert result.success is False
        assert result.timed_out is True

    def test_nonexistent_command(self) -> None:
        """run_tool returns error string when the command cannot be found."""
        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            side_effect=FileNotFoundError("No such file or directory: 'nope'"),
        ):
            result = run_tool(["nope"], cwd="/tmp")

        assert result.success is False
        assert result.error is not None
        assert "nope" in result.error

    def test_generic_exception(self) -> None:
        """run_tool catches generic exceptions and returns error string."""
        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            side_effect=OSError("Permission denied"),
        ):
            result = run_tool(["restricted"], cwd="/tmp")

        assert result.success is False
        assert result.error is not None
        assert "Permission denied" in result.error

    def test_stderr_capture(self) -> None:
        """run_tool captures stderr from the subprocess."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = "out\n"
        mock_proc.stderr = "warn: something\n"

        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            return_value=mock_proc,
        ):
            result = run_tool(["some-tool"], cwd="/tmp")

        assert result.stdout == "out\n"
        assert result.stderr == "warn: something\n"
        assert result.output == "out\nwarn: something\n"

    def test_passes_cwd_and_timeout(self) -> None:
        """run_tool forwards cwd and timeout to subprocess.run."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            return_value=mock_proc,
        ) as mock_run:
            run_tool(["echo"], cwd="/some/dir", timeout=60)

        mock_run.assert_called_once_with(
            ["echo"],
            cwd="/some/dir",
            capture_output=True,
            text=True,
            timeout=60,
        )

    def test_default_timeout(self) -> None:
        """run_tool uses 300 seconds as the default timeout."""
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = ""
        mock_proc.stderr = ""

        with patch(
            "pycmdcheck.subprocess_runner.subprocess.run",
            return_value=mock_proc,
        ) as mock_run:
            run_tool(["echo"], cwd="/tmp")

        _, kwargs = mock_run.call_args
        assert kwargs["timeout"] == 300


# ===========================================================================
# Check-level subprocess tests (existing tests)
# ===========================================================================


# ---------------------------------------------------------------------------
# Tests check -- subprocess paths
# ---------------------------------------------------------------------------


class TestTestsCheckSubprocess:
    """Test the subprocess interactions in TestsCheck."""

    def test_pytest_not_installed(self, temp_package: Path) -> None:
        """Test SKIPPED status when pytest is not found on PATH."""
        from pycmdcheck.checks.tests import TestsCheck

        with patch("pycmdcheck.checks.tests.tool_available", return_value=False):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "pytest"})

        assert result.status == CheckStatus.SKIPPED
        assert result.message == "pytest not installed"
        assert any("pip install pytest" in d for d in result.details)

    def test_pytest_success(self, temp_package: Path) -> None:
        """Test OK status when pytest runs and passes."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="===== 3 passed in 0.05s =====\n",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.tests.tool_available", return_value=True),
            patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result),
        ):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "pytest"})

        assert result.status == CheckStatus.OK
        assert result.message == "All tests passed"
        assert any("passed" in d for d in result.details)

    def test_pytest_failure(self, temp_package: Path) -> None:
        """Test ERROR status when pytest runs and tests fail."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(
            returncode=1,
            stdout="FAILED tests/test_foo.py::test_bar - AssertionError\n",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.tests.tool_available", return_value=True),
            patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result),
        ):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "pytest"})

        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests failed"
        assert any("FAILED" in d for d in result.details)

    def test_pytest_timeout(self, temp_package: Path) -> None:
        """Test ERROR status when pytest times out."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(timed_out=True)

        with (
            patch("pycmdcheck.checks.tests.tool_available", return_value=True),
            patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result),
        ):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "pytest"})

        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests timed out"
        assert any("300 seconds" in d for d in result.details)

    def test_pytest_exception(self, temp_package: Path) -> None:
        """Test ERROR status when run_tool returns an error."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(error="Permission denied")

        with (
            patch("pycmdcheck.checks.tests.tool_available", return_value=True),
            patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result),
        ):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "pytest"})

        assert result.status == CheckStatus.ERROR
        assert result.message == "Failed to run tests"
        assert "Permission denied" in result.details[0]

    def test_unittest_success(self, temp_package: Path) -> None:
        """Test OK status when unittest discover runs and passes."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="Ran 5 tests in 0.01s\n\nOK\n",
        )

        with patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "unittest"})

        assert result.status == CheckStatus.OK
        assert result.message == "All tests passed"
        assert any("Ran" in d for d in result.details)

    def test_unittest_failure(self, temp_package: Path) -> None:
        """Test ERROR status when unittest reports failures."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(
            returncode=1,
            stdout="",
            stderr="FAIL: test_something (test_mod.TestCase)\n",
        )

        with patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "unittest"})

        assert result.status == CheckStatus.ERROR
        assert result.message == "Tests failed"

    def test_pytest_output_capture(self, temp_package: Path) -> None:
        """Test that pytest stdout/stderr are captured in details."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(
            returncode=1,
            stdout=(
                "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
                "FAILED tests/test_b.py::test_y - KeyError\n"
            ),
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.tests.tool_available", return_value=True),
            patch("pycmdcheck.checks.tests.run_tool", return_value=mock_result),
        ):
            check = TestsCheck()
            result = check.run(temp_package, {"runner": "pytest"})

        assert result.status == CheckStatus.ERROR
        # Both FAILED lines should appear in details
        failed_details = [d for d in result.details if "FAILED" in d]
        assert len(failed_details) == 2

    def test_pytest_with_extra_args(self, temp_package: Path) -> None:
        """Test that extra args from config are passed to pytest command."""
        from pycmdcheck.checks.tests import TestsCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="1 passed in 0.01s",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.tests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.tests.run_tool", return_value=mock_result
            ) as mock_run,
        ):
            check = TestsCheck()
            result = check.run(
                temp_package, {"runner": "pytest", "args": ["-x", "--cov"]}
            )

        # Verify the command included the extra args
        call_args = mock_run.call_args
        cmd = call_args[0][0]
        assert "-x" in cmd
        assert "--cov" in cmd
        assert result.status == CheckStatus.OK


# ---------------------------------------------------------------------------
# Linting check -- subprocess paths
# ---------------------------------------------------------------------------


class TestLintingCheckSubprocess:
    """Test the subprocess interactions in LintingCheck."""

    def test_ruff_not_installed(self, temp_package: Path) -> None:
        """Test SKIPPED status when ruff is not found on PATH."""
        from pycmdcheck.checks.linting import LintingCheck

        with patch("pycmdcheck.checks.linting.tool_available", return_value=False):
            check = LintingCheck()
            result = check.run(temp_package, {"tool": "ruff"})

        assert result.status == CheckStatus.SKIPPED
        assert result.message == "ruff not installed"

    def test_ruff_success(self, temp_package: Path) -> None:
        """Test OK status when ruff finds no issues."""
        from pycmdcheck.checks.linting import LintingCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.linting.tool_available", return_value=True),
            patch("pycmdcheck.checks.linting.run_tool", return_value=mock_result),
        ):
            check = LintingCheck()
            result = check.run(temp_package, {"tool": "ruff"})

        assert result.status == CheckStatus.OK
        assert result.message == "No linting issues found"

    def test_ruff_warnings(self, temp_package: Path) -> None:
        """Test WARNING status when ruff finds issues."""
        from pycmdcheck.checks.linting import LintingCheck

        mock_result = SubprocessResult(
            returncode=1,
            stdout=(
                "src/mypackage/foo.py:10:1: E501 Line too long\n"
                "src/mypackage/bar.py:5:1: F401 Unused import\n"
            ),
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.linting.tool_available", return_value=True),
            patch("pycmdcheck.checks.linting.run_tool", return_value=mock_result),
        ):
            check = LintingCheck()
            result = check.run(temp_package, {"tool": "ruff"})

        assert result.status == CheckStatus.WARNING
        assert "2 linting issue" in result.message
        assert any("E501" in d for d in result.details)
        assert any("F401" in d for d in result.details)

    def test_ruff_timeout(self, temp_package: Path) -> None:
        """Test ERROR status when ruff times out."""
        from pycmdcheck.checks.linting import LintingCheck

        mock_result = SubprocessResult(timed_out=True)

        with (
            patch("pycmdcheck.checks.linting.tool_available", return_value=True),
            patch("pycmdcheck.checks.linting.run_tool", return_value=mock_result),
        ):
            check = LintingCheck()
            result = check.run(temp_package, {"tool": "ruff"})

        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()

    def test_flake8_not_installed(self, temp_package: Path) -> None:
        """Test SKIPPED when flake8 is not installed."""
        from pycmdcheck.checks.linting import LintingCheck

        with patch("pycmdcheck.checks.linting.tool_available", return_value=False):
            check = LintingCheck()
            result = check.run(temp_package, {"tool": "flake8"})

        assert result.status == CheckStatus.SKIPPED
        assert "flake8" in result.message.lower()

    def test_pylint_not_installed(self, temp_package: Path) -> None:
        """Test SKIPPED when pylint is not installed."""
        from pycmdcheck.checks.linting import LintingCheck

        with patch("pycmdcheck.checks.linting.tool_available", return_value=False):
            check = LintingCheck()
            result = check.run(temp_package, {"tool": "pylint"})

        assert result.status == CheckStatus.SKIPPED
        assert "pylint" in result.message.lower()


# ---------------------------------------------------------------------------
# Typing check -- subprocess paths
# ---------------------------------------------------------------------------


class TestTypingCheckSubprocess:
    """Test the subprocess interactions in TypingCheck."""

    def test_mypy_not_installed(self, temp_package: Path) -> None:
        """Test SKIPPED status when mypy is not found on PATH."""
        from pycmdcheck.checks.typing import TypingCheck

        with patch("pycmdcheck.checks.typing.tool_available", return_value=False):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "mypy"})

        assert result.status == CheckStatus.SKIPPED
        assert result.message == "mypy not installed"

    def test_mypy_success(self, temp_package: Path) -> None:
        """Test OK status when mypy finds no type errors."""
        from pycmdcheck.checks.typing import TypingCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="Success: no issues found in 5 source files\n",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.typing.tool_available", return_value=True),
            patch("pycmdcheck.checks.typing.run_tool", return_value=mock_result),
        ):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "mypy"})

        assert result.status == CheckStatus.OK
        assert result.message == "No type errors found"

    def test_mypy_errors(self, temp_package: Path) -> None:
        """Test ERROR status when mypy finds type errors."""
        from pycmdcheck.checks.typing import TypingCheck

        mock_result = SubprocessResult(
            returncode=1,
            stdout=(
                "src/pkg/foo.py:10: error: Incompatible types\n"
                "src/pkg/bar.py:5: error: Missing return\n"
                "Found 2 errors in 2 files\n"
            ),
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.typing.tool_available", return_value=True),
            patch("pycmdcheck.checks.typing.run_tool", return_value=mock_result),
        ):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "mypy"})

        assert result.status == CheckStatus.ERROR
        assert "type error" in result.message.lower()
        errors_in_details = [d for d in result.details if "error:" in d]
        assert len(errors_in_details) == 2

    def test_mypy_timeout(self, temp_package: Path) -> None:
        """Test ERROR status when mypy times out."""
        from pycmdcheck.checks.typing import TypingCheck

        mock_result = SubprocessResult(timed_out=True)

        with (
            patch("pycmdcheck.checks.typing.tool_available", return_value=True),
            patch("pycmdcheck.checks.typing.run_tool", return_value=mock_result),
        ):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "mypy"})

        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()

    def test_mypy_strict_mode(self, temp_package: Path) -> None:
        """Test that strict=True adds --strict flag to mypy command."""
        from pycmdcheck.checks.typing import TypingCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="Success",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.typing.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.typing.run_tool", return_value=mock_result
            ) as mock_run,
        ):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "mypy", "strict": True})

        cmd = mock_run.call_args[0][0]
        assert "--strict" in cmd
        assert result.status == CheckStatus.OK

    def test_pyright_not_installed(self, temp_package: Path) -> None:
        """Test SKIPPED status when pyright is not found on PATH."""
        from pycmdcheck.checks.typing import TypingCheck

        with patch("pycmdcheck.checks.typing.tool_available", return_value=False):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "pyright"})

        assert result.status == CheckStatus.SKIPPED
        assert "pyright" in result.message.lower()

    def test_pyright_success(self, temp_package: Path) -> None:
        """Test OK status when pyright finds no errors."""
        from pycmdcheck.checks.typing import TypingCheck

        mock_result = SubprocessResult(
            returncode=0,
            stdout="0 errors, 0 warnings\n",
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.typing.tool_available", return_value=True),
            patch("pycmdcheck.checks.typing.run_tool", return_value=mock_result),
        ):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "pyright"})

        assert result.status == CheckStatus.OK
        assert result.message == "No type errors found"

    def test_pyright_errors(self, temp_package: Path) -> None:
        """Test ERROR status when pyright finds errors."""
        from pycmdcheck.checks.typing import TypingCheck

        mock_result = SubprocessResult(
            returncode=1,
            stdout=(
                "src/pkg/foo.py:10:5 - error: Type mismatch\n1 error, 0 warnings\n"
            ),
            stderr="",
        )

        with (
            patch("pycmdcheck.checks.typing.tool_available", return_value=True),
            patch("pycmdcheck.checks.typing.run_tool", return_value=mock_result),
        ):
            check = TypingCheck()
            result = check.run(temp_package, {"tool": "pyright"})

        assert result.status == CheckStatus.ERROR
        assert "type error" in result.message.lower()


class TestSanitizeArgs:
    def test_allows_flags(self) -> None:
        assert sanitize_args(["--strict", "-v", "--select=E501"]) == [
            "--strict",
            "-v",
            "--select=E501",
        ]

    def test_allows_flag_value_pairs(self) -> None:
        """Non-flag arguments (flag values) are allowed."""
        assert sanitize_args(["--select", "E,F,W"]) == ["--select", "E,F,W"]

    def test_rejects_non_strings(self) -> None:
        with pytest.raises(TypeError, match="Non-string argument"):
            sanitize_args(["--strict", 42, "-v"])  # type: ignore[list-item]

    def test_empty_list(self) -> None:
        assert sanitize_args([]) == []
