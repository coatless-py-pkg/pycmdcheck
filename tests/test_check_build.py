"""Tests for build check."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pycmdcheck.checks.build import BuildCheck
from pycmdcheck.results import CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult


class TestBuildCheck:
    """Tests for BuildCheck."""

    def test_build_not_installed(self, temp_package: Path) -> None:
        check = BuildCheck()
        with patch(
            "pycmdcheck.checks.build.importlib.util.find_spec", return_value=None
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert "build" in result.message.lower()

    def test_no_pyproject(self, empty_dir: Path) -> None:
        check = BuildCheck()
        fake_spec = MagicMock()
        with patch(
            "pycmdcheck.checks.build.importlib.util.find_spec", return_value=fake_spec
        ):
            result = check.run(empty_dir, {})
        assert result.status == CheckStatus.ERROR
        assert "pyproject.toml" in result.message.lower()

    def test_build_success(self, temp_package: Path) -> None:
        """Successful build returns OK with wheel/sdist details."""
        check = BuildCheck()
        fake_spec = MagicMock()

        def fake_run_tool(cmd, *, cwd=None, timeout=120):
            # Create a fake wheel in the output dir
            outdir = cmd[cmd.index("--outdir") + 1]
            (Path(outdir) / "mypackage-0.1.0-py3-none-any.whl").write_text("")
            return SubprocessResult(returncode=0, stdout="Successfully built\n")

        with (
            patch(
                "pycmdcheck.checks.build.importlib.util.find_spec",
                return_value=fake_spec,
            ),
            patch("pycmdcheck.checks.build.run_tool", side_effect=fake_run_tool),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "built successfully" in result.message.lower()
        assert any("wheel" in d.lower() for d in result.details)

    def test_build_failure(self, temp_package: Path) -> None:
        """Failed build returns ERROR with output details."""
        check = BuildCheck()
        fake_spec = MagicMock()
        with (
            patch(
                "pycmdcheck.checks.build.importlib.util.find_spec",
                return_value=fake_spec,
            ),
            patch(
                "pycmdcheck.checks.build.run_tool",
                return_value=SubprocessResult(
                    returncode=1,
                    stdout="error: invalid config\n",
                ),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert "failed" in result.message.lower()

    def test_build_timeout(self, temp_package: Path) -> None:
        """Timed-out build returns ERROR."""
        check = BuildCheck()
        fake_spec = MagicMock()
        with (
            patch(
                "pycmdcheck.checks.build.importlib.util.find_spec",
                return_value=fake_spec,
            ),
            patch(
                "pycmdcheck.checks.build.run_tool",
                return_value=SubprocessResult(timed_out=True),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()

    def test_build_run_error(self, temp_package: Path) -> None:
        """run_tool returning an error string returns ERROR."""
        check = BuildCheck()
        fake_spec = MagicMock()
        with (
            patch(
                "pycmdcheck.checks.build.importlib.util.find_spec",
                return_value=fake_spec,
            ),
            patch(
                "pycmdcheck.checks.build.run_tool",
                return_value=SubprocessResult(error="FileNotFoundError: python"),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert "failed to run" in result.message.lower()

    def test_build_success_no_artifacts(self, temp_package: Path) -> None:
        """Build succeeds (exit 0) but produces no artifacts -> WARNING."""
        check = BuildCheck()
        fake_spec = MagicMock()

        with (
            patch(
                "pycmdcheck.checks.build.importlib.util.find_spec",
                return_value=fake_spec,
            ),
            patch(
                "pycmdcheck.checks.build.run_tool",
                return_value=SubprocessResult(returncode=0, stdout="Build completed\n"),
            ),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert "no artifacts" in result.message.lower()
