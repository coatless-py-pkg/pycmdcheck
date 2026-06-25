"""Tests for doctests check."""

from pathlib import Path
from unittest.mock import patch

from pycmdcheck.checks.doctests import DoctestsCheck
from pycmdcheck.results import CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult


class TestDoctestsCheck:
    """Tests for DoctestsCheck."""

    def test_pytest_not_installed(self, temp_package: Path) -> None:
        """pytest not available -> SKIPPED."""
        check = DoctestsCheck()
        with patch("pycmdcheck.checks.doctests.tool_available", return_value=False):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.name == "doctests"

    def test_exit_code_5_is_no_doctests(self, temp_package: Path) -> None:
        """pytest exit code 5 (nothing collected) -> OK, not a false failure (#5)."""
        check = DoctestsCheck()
        with (
            patch("pycmdcheck.checks.doctests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.doctests.run_tool",
                return_value=SubprocessResult(returncode=5, stdout=""),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "no doctests" in result.message.lower()

    def test_all_doctests_pass(self, temp_package: Path) -> None:
        """All doctests pass -> OK."""
        check = DoctestsCheck()
        with (
            patch("pycmdcheck.checks.doctests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.doctests.run_tool",
                return_value=SubprocessResult(
                    returncode=0,
                    stdout="2 passed in 0.5s\n",
                ),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK

    def test_no_doctests_found(self, temp_package: Path) -> None:
        """No doctests in codebase -> OK."""
        check = DoctestsCheck()
        with (
            patch("pycmdcheck.checks.doctests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.doctests.run_tool",
                return_value=SubprocessResult(
                    returncode=0,
                    stdout="no tests ran in 0.1s\n",
                ),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "no doctests" in result.message.lower()

    def test_doctests_fail(self, temp_package: Path) -> None:
        """Doctest failures -> WARNING."""
        check = DoctestsCheck()
        with (
            patch("pycmdcheck.checks.doctests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.doctests.run_tool",
                return_value=SubprocessResult(
                    returncode=1,
                    stdout="FAILED src/pkg/module.py::pkg.module.func\n1 failed\n",
                ),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.WARNING

    def test_timeout(self, temp_package: Path) -> None:
        """Doctest execution timeout -> ERROR."""
        check = DoctestsCheck()
        with (
            patch("pycmdcheck.checks.doctests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.doctests.run_tool",
                return_value=SubprocessResult(timed_out=True),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()

    def test_run_error(self, temp_package: Path) -> None:
        """Run tool error -> ERROR."""
        check = DoctestsCheck()
        with (
            patch("pycmdcheck.checks.doctests.tool_available", return_value=True),
            patch(
                "pycmdcheck.checks.doctests.run_tool",
                return_value=SubprocessResult(error="FileNotFoundError"),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.ERROR
