"""Tests for build check."""

import os
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pycmdcheck.checks.build import BuildCheck
from pycmdcheck.config import get_check_config, load_config
from pycmdcheck.constants import MAX_DETAIL_LINES
from pycmdcheck.results import CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult
from tests.helpers import only_code

MODULE = "pycmdcheck.checks.build"


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> Callable[..., MagicMock]:
    """Pretend the build module is installed and return the ``run_tool`` mock."""

    def install(
        result: SubprocessResult | None = None, *, artifacts: tuple[str, ...] = ()
    ) -> MagicMock:
        def fake_run_tool(cmd: list[str], **kwargs: object) -> SubprocessResult:
            for name in artifacts:
                Path(cmd[cmd.index("--outdir") + 1], name).write_text("")
            return result or SubprocessResult(returncode=0)

        run = MagicMock(side_effect=fake_run_tool)
        monkeypatch.setattr(f"{MODULE}.tool_version", lambda name, **kw: "9.9")
        monkeypatch.setattr(f"{MODULE}.run_tool", run)
        return run

    return install


def fake_build(cmd: list[str], *, cwd: str, timeout: float) -> SubprocessResult:
    """Stand in for a build that takes 100 seconds."""
    if timeout < 100:
        return SubprocessResult(timed_out=True)
    # A second --outdir replaces the one the check gave, as in the real build.
    if cmd.count("--outdir") == 1:
        Path(cmd[cmd.index("--outdir") + 1], "mypackage-0.1.0.tar.gz").write_text("")
    return SubprocessResult(returncode=0)


class TestBuildCheck:
    """Tests for BuildCheck."""

    def test_build_not_installed(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """The probe asks the interpreter that would run the build."""
        run = tool()
        probe = MagicMock(return_value=None)
        monkeypatch.setattr(f"{MODULE}.tool_version", probe)
        result = BuildCheck().run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "tool-missing"
        assert result.findings == []
        assert "build" in result.message.lower()
        assert any(
            "'pip install \"pycmdcheck[build]\"' or 'pip install build'" in d
            and sys.executable in d
            for d in result.details
        )
        probe.assert_called_once_with(
            "build", command=[sys.executable, "-m", "build", "--version"]
        )
        run.assert_not_called()

    def test_probe_does_not_import_the_project(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """A build.py on sys.path must not be executed inside pycmdcheck."""
        marker = temp_package / "EXECUTED_BY_PROBE"
        (temp_package / "build.py").write_text(
            "from pathlib import Path\n"
            "Path(__file__).with_name('EXECUTED_BY_PROBE').write_text('')\n"
        )
        monkeypatch.delitem(sys.modules, "build", raising=False)
        monkeypatch.syspath_prepend(str(temp_package))
        tool(artifacts=("mypackage-0.1.0.tar.gz",))
        try:
            result = BuildCheck().run(temp_package, {})
        finally:
            sys.modules.pop("build", None)
        assert not marker.exists()
        assert result.status == CheckStatus.OK

    def test_no_build_files(
        self, empty_dir: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Nothing to build from: the metadata check owns that finding."""
        run = tool()
        result = BuildCheck().run(empty_dir, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "prerequisite-failed"
        assert result.findings == []
        assert "pyproject.toml" in result.message.lower()
        assert any("setup.py" in d and "setup.cfg" in d for d in result.details)
        assert any("metadata check" in d for d in result.details)
        run.assert_not_called()

    def test_unreadable_pyproject_is_not_built(
        self, package_with_invalid_toml: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The metadata check owns a broken pyproject.toml, also beside a setup.py."""
        setup_py = "from setuptools import setup\n\nsetup(name='invalidtoml')\n"
        (package_with_invalid_toml / "setup.py").write_text(setup_py)
        run = tool()
        result = BuildCheck().run(package_with_invalid_toml, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "prerequisite-failed"
        assert result.findings == []
        assert result.message == "pyproject.toml could not be read"
        assert result.details == ["The metadata check reports the reason"]
        run.assert_not_called()

    def test_setup_py_only_project_is_built(
        self, package_with_setup_py: Path, tool: Callable[..., MagicMock]
    ) -> None:
        (package_with_setup_py / "pyproject.toml").unlink(missing_ok=True)
        run = tool(artifacts=("pkg-0.1.0.tar.gz",))
        result = BuildCheck().run(package_with_setup_py, {})
        assert result.status == CheckStatus.OK
        run.assert_called_once()

    def test_runs_own_interpreter_from_a_neutral_directory(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """A build.py at the project root must not shadow ``python -m build``."""
        (temp_package / "build.py").write_text("raise SystemExit('shadowed')\n")
        run = tool(artifacts=("mypackage-0.1.0-py3-none-any.whl",))
        result = BuildCheck().run(temp_package, {"args": ["--wheel"], "timeout": 7})
        cmd = run.call_args.args[0]
        outdir = cmd[4]
        assert cmd[:4] == [sys.executable, "-m", "build", "--outdir"]
        assert cmd[5:] == [os.path.abspath(temp_package), "--wheel"]
        assert run.call_args.kwargs == {"cwd": outdir, "timeout": 7}
        assert Path(outdir).resolve() != temp_package.resolve()
        assert result.status == CheckStatus.OK
        assert result.findings == []
        assert result.skip_reason is None
        assert "built successfully" in result.message.lower()
        assert "Using build 9.9" in result.details
        assert any("wheel" in d.lower() for d in result.details)

    def test_build_failure_shows_the_end_of_stdout_and_stderr(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        noise = "".join(f"* step {i}\n" for i in range(MAX_DETAIL_LINES))
        tool(
            SubprocessResult(
                returncode=1,
                stdout=noise,
                stderr="OSError: version file does not exist\nERROR Backend failed\n",
            )
        )
        result = BuildCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert "failed" in result.message.lower()
        assert only_code(result) == "build.failed"
        assert result.details[-1] == "ERROR Backend failed"
        assert any("OSError" in d for d in result.details)
        assert any("2 earlier lines" in d for d in result.details)

    def test_build_failure_without_output(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool(SubprocessResult(returncode=1))
        result = BuildCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert only_code(result) == "build.failed"
        assert any("no output" in d for d in result.details)

    def test_build_timeout(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Timed-out build returns ERROR."""
        tool(SubprocessResult(timed_out=True))
        result = BuildCheck().run(temp_package, {"timeout": "soon"})
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()
        assert only_code(result) == "build.timed-out"
        assert "120 seconds" in result.findings[0].message
        assert any("120 seconds" in d for d in result.details)
        assert any("Ignored timeout" in d for d in result.details)

    @pytest.mark.parametrize(
        ("outcome", "reason"),
        [
            (
                SubprocessResult(error="Could not start python: denied"),
                ["Could not start python: denied"],
            ),
            (
                SubprocessResult(
                    returncode=2,
                    stderr="python -m build: error: unrecognized arguments: -Z\n",
                ),
                [
                    "build exited with code 2",
                    "python -m build: error: unrecognized arguments: -Z",
                ],
            ),
            (SubprocessResult(returncode=-9), ["build exited with code -9"]),
        ],
        ids=["not-started", "usage", "killed"],
    )
    def test_could_not_run_is_not_a_failed_build(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        outcome: SubprocessResult,
        reason: list[str],
    ) -> None:
        """Only exit code 1 says that the package does not build."""
        tool(outcome)
        result = BuildCheck().run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert result.message == "build could not run"
        assert only_code(result) == "build.could-not-run"
        assert result.details == ["Using build 9.9", *reason]

    def test_invalid_args_option(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = BuildCheck().run(temp_package, {"args": "--wheel"})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "build.invalid-args"
        assert any("list of strings" in d for d in result.details)
        run.assert_not_called()

    def test_build_success_no_artifacts(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Build succeeds (exit 0) but produces no artifacts -> WARNING."""
        tool(SubprocessResult(returncode=0, stdout="Build completed\n"))
        result = BuildCheck().run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert "no artifacts" in result.message.lower()
        assert only_code(result) == "build.missing-artifacts"

    @pytest.mark.parametrize(
        ("code", "before", "after", "said"),
        [
            (
                "build.invalid-args",
                'args = "--wheel --sdist"',
                'args = ["--wheel", "--sdist"]',
                'so "--wheel --sdist" becomes ["--wheel", "--sdist"].',
            ),
            (
                "build.timed-out",
                "timeout = 60",
                "timeout = 300",
                "Set a longer timeout in [tool.pycmdcheck.checks.build]",
            ),
            (
                "build.missing-artifacts",
                'args = ["--outdir", "dist"]',
                "args = []",
                "Remove --outdir and -o from the args option of the build check",
            ),
        ],
    )
    def test_following_the_hint_clears_the_code(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        code: str,
        before: str,
        after: str,
        said: str,
    ) -> None:
        """The options are read from the table that the hints name."""
        assert said in BuildCheck.hints[code]
        tool().side_effect = fake_build
        pyproject = temp_package / "pyproject.toml"
        project = pyproject.read_text()
        found = []
        for options in (before, after):
            pyproject.write_text(
                f"{project}\n[tool.pycmdcheck.checks.build]\n{options}\n"
            )
            config = get_check_config(load_config(temp_package), "build")
            result = BuildCheck().run(temp_package, config)
            found.append([finding.code for finding in result.findings])
        assert found == [[code], []]
