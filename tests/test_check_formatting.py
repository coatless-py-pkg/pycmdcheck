"""Tests for formatting check."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pycmdcheck.checks.formatting import FormattingCheck
from pycmdcheck.results import CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult


class TestFormattingCheck:
    """Tests for FormattingCheck."""

    def test_unsupported_tool(self, temp_package: Path) -> None:
        check = FormattingCheck()
        result = check.run(temp_package, {"tool": "unsupported"})
        assert result.status == CheckStatus.ERROR

    def test_ruff_not_installed(self, temp_package: Path) -> None:
        check = FormattingCheck()
        with patch("pycmdcheck.subprocess_runner.shutil.which", return_value=None):
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.SKIPPED

    def test_ruff_clean(self, temp_package: Path) -> None:
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/ruff",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.OK

    def test_ruff_needs_reformatting(self, temp_package: Path) -> None:
        """Files needing reformatting return WARNING."""
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/ruff",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="Would reformat: foo.py\nWould reformat: bar.py\n",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.WARNING
        assert "2" in result.message
        assert "reformat" in result.message.lower()

    def test_black_not_installed(self, temp_package: Path) -> None:
        """Black not installed returns SKIPPED."""
        check = FormattingCheck()
        with patch("pycmdcheck.subprocess_runner.shutil.which", return_value=None):
            result = check.run(temp_package, {"tool": "black"})
        assert result.status == CheckStatus.SKIPPED
        assert "black" in result.message.lower()

    def test_black_clean(self, temp_package: Path) -> None:
        """Black with clean code returns OK."""
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/black",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            result = check.run(temp_package, {"tool": "black"})
        assert result.status == CheckStatus.OK
        assert any("black" in d.lower() for d in result.details)

    def test_black_needs_reformatting(self, temp_package: Path) -> None:
        """Black finding unformatted files returns WARNING."""
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/black",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="Would reformat foo.py\n",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "black"})
        assert result.status == CheckStatus.WARNING

    def test_ruff_timeout(self, temp_package: Path) -> None:
        """Timed-out ruff returns ERROR."""
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/ruff",
            ),
            patch(
                "pycmdcheck.checks.formatting.run_tool",
                return_value=SubprocessResult(timed_out=True),
            ),
        ):
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()

    def test_ruff_non_standard_output(self, temp_package: Path) -> None:
        """Tool output without 'would reformat' uses generic message."""
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/ruff",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="error: unexpected token\ninvalid syntax\n",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.WARNING
        assert "issues found" in result.message.lower()
        # Raw output lines should be in details
        assert any("unexpected token" in d for d in result.details)

    def test_black_non_standard_output(self, temp_package: Path) -> None:
        """Black output without 'would reformat' uses generic message."""
        check = FormattingCheck()
        with (
            patch(
                "pycmdcheck.subprocess_runner.shutil.which",
                return_value="/usr/bin/black",
            ),
            patch("pycmdcheck.subprocess_runner.subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock(
                returncode=1,
                stdout="Oh no! Something went wrong\n",
                stderr="",
            )
            result = check.run(temp_package, {"tool": "black"})
        assert result.status == CheckStatus.WARNING
        assert "issues found" in result.message.lower()
