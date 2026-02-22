"""Tests for tool-based checks with mocked subprocess calls."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pycmdcheck.results import CheckStatus


class TestTestsCheckMocked:
    """Mocked subprocess tests for TestsCheck."""

    def test_pytest_not_installed(self, temp_package: Path) -> None:
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        with patch("pycmdcheck.subprocess_runner.shutil.which", return_value=None):
            result = check.run(temp_package, {"runner": "pytest"})
        assert result.status == CheckStatus.SKIPPED
        assert "pytest not installed" in result.message.lower()

    def test_pytest_success(self, temp_package: Path) -> None:
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/pytest",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="2 passed in 0.5s\n",
                stderr="",
            )
            result = check.run(temp_package, {"runner": "pytest"})
        assert result.status == CheckStatus.OK

    def test_pytest_failure(self, temp_package: Path) -> None:
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/pytest",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="FAILED test_foo.py::test_bar\n1 failed\n",
                stderr="",
            )
            result = check.run(temp_package, {"runner": "pytest"})
        assert result.status == CheckStatus.ERROR


class TestLintingCheckMocked:
    """Mocked subprocess tests for LintingCheck."""

    def test_ruff_not_installed(self, temp_package: Path) -> None:
        from pycmdcheck.checks.linting import LintingCheck

        check = LintingCheck()
        with patch("pycmdcheck.subprocess_runner.shutil.which", return_value=None):
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.SKIPPED

    def test_ruff_clean(self, temp_package: Path) -> None:
        from pycmdcheck.checks.linting import LintingCheck

        check = LintingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/ruff",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.OK

    def test_ruff_warnings(self, temp_package: Path) -> None:
        from pycmdcheck.checks.linting import LintingCheck

        check = LintingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/ruff",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout=(
                    "file.py:1:1: E501 line too long\n"
                    "file.py:2:1: W291 trailing whitespace\n"
                ),
                stderr="",
            )
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.WARNING
        assert "2" in result.message


class TestTypingCheckMocked:
    """Mocked subprocess tests for TypingCheck."""

    def test_mypy_not_installed(self, temp_package: Path) -> None:
        from pycmdcheck.checks.typing import TypingCheck

        check = TypingCheck()
        with patch("pycmdcheck.subprocess_runner.shutil.which", return_value=None):
            result = check.run(temp_package, {"tool": "mypy"})
        assert result.status == CheckStatus.SKIPPED

    def test_mypy_success(self, temp_package: Path) -> None:
        from pycmdcheck.checks.typing import TypingCheck

        check = TypingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/mypy",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=0,
                stdout="Success: no issues found\n",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "mypy"})
        assert result.status == CheckStatus.OK

    def test_mypy_errors(self, temp_package: Path) -> None:
        from pycmdcheck.checks.typing import TypingCheck

        check = TypingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/mypy",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="file.py:10: error: Incompatible types\n",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "mypy"})
        assert result.status == CheckStatus.ERROR
