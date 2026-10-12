"""Build check: verifies the package can be built."""

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.fileio import is_regular_file
from pycmdcheck.pyproject_reader import pyproject_status
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.subprocess_runner import (
    ToolArgsError,
    could_not_run,
    missing_tool_hint,
    resolve_timeout,
    run_tool,
    sanitize_args,
    tool_version,
)

# "python -m build" can work with any one of these.
BUILD_FILES: tuple[str, ...] = ("pyproject.toml", "setup.py", "setup.cfg")


class BuildCheck(BaseCheck):
    """Check that the package can be built into a wheel."""

    name = "build"
    description = "Verify package builds successfully"
    rules = {
        "build.invalid-args": "The args option is not a list of strings",
        "build.timed-out": "The build ran longer than the timeout",
        "build.could-not-run": "The build module is installed but could not run",
        "build.missing-artifacts": "The build produced no wheel or source distribution",
        "build.failed": "The package does not build",
    }
    hints = {
        "build.invalid-args": (
            "Write args as a list with one string for each argument, so "
            '"--wheel --sdist" becomes ["--wheel", "--sdist"].'
        ),
        "build.timed-out": (
            "Set a longer timeout in [tool.pycmdcheck.checks.build], or in the "
            "--config file when the run uses one."
        ),
        "build.could-not-run": (
            "Run python -m build with the same args in the project directory and "
            "fix the error it prints."
        ),
        "build.missing-artifacts": (
            "Remove --outdir and -o from the args option of the build check, so "
            "the build writes where the check looks."
        ),
        "build.failed": (
            "Run python -m build in the project directory and fix the first error "
            "it prints."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Attempt to build the package.

        Supports ``args`` (list[str]) for additional build arguments and
        ``timeout`` (seconds) to override the default. A package that does
        not build is an ERROR. A build that could not run is a WARNING. The
        check is SKIPPED when the build module is not installed, and when
        pyproject.toml could not be read or the project has none of the
        files a build starts from, both of which the metadata check reports.
        """
        # The interpreter running pycmdcheck is asked for its build module and
        # later runs the build. Both happen in a scratch directory, never in
        # this process, so a build.py or build/ in the project is neither
        # imported nor able to shadow the module.
        build = [sys.executable, "-m", "build"]
        version = tool_version("build", command=[*build, "--version"])
        if version is None:
            return CheckResult.skip(
                self.name,
                "'build' module not installed",
                "tool-missing",
                [missing_tool_hint("build")],
            )

        if pyproject_status(package_path) == "unparseable":
            # The build would fail on it, also beside a setup.py. The
            # metadata check is the one that reports the broken file.
            return CheckResult.skip(
                self.name,
                "pyproject.toml could not be read",
                "prerequisite-failed",
                ["The metadata check reports the reason"],
            )

        if not any(is_regular_file(package_path / name) for name in BUILD_FILES):
            return CheckResult.skip(
                self.name,
                "No pyproject.toml found",
                "prerequisite-failed",
                [
                    f"Looked for {', '.join(BUILD_FILES)} in the project directory",
                    "Cannot build without one of them",
                    "The metadata check reports the missing files",
                ],
            )

        try:
            args = sanitize_args(config.get("args"), check=self.name)
        except ToolArgsError as exc:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Invalid args option",
                details=[str(exc)],
                findings=[Finding("build.invalid-args", str(exc), CheckStatus.ERROR)],
            )
        timeout, note = resolve_timeout(config, DEFAULT_TIMEOUT, check=self.name)
        notes = [note] if note else []
        preamble = [f"Using build {version}"]

        with tempfile.TemporaryDirectory() as tmpdir:
            source = os.path.abspath(package_path)
            cmd = [*build, "--outdir", tmpdir, source, *args]
            result = run_tool(cmd, cwd=tmpdir, timeout=timeout)

            if result.timed_out:
                detail = f"Build took longer than {timeout} seconds"
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.ERROR,
                    message="Build timed out",
                    details=[detail, *notes],
                    findings=[Finding("build.timed-out", detail, CheckStatus.ERROR)],
                )

            # Only exit code 1 means that the package does not build. A
            # build that never started, that was given an argument it does
            # not know (exit code 2) or that was killed says nothing about
            # the package.
            if result.error is not None or result.returncode not in (0, 1):
                return could_not_run(self.name, "build", result, [*preamble, *notes])

            if result.success:
                # Verify wheel was created
                wheels = list(Path(tmpdir).glob("*.whl"))
                sdists = list(Path(tmpdir).glob("*.tar.gz"))
                details = [*preamble, *notes]
                if wheels:
                    details.append(f"Built wheel: {wheels[0].name}")
                if sdists:
                    details.append(f"Built sdist: {sdists[0].name}")

                if wheels or sdists:
                    return CheckResult(
                        name=self.name,
                        status=CheckStatus.OK,
                        message="Package built successfully",
                        details=details,
                    )

                # Build command succeeded but produced no artifacts
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.WARNING,
                    message="Build succeeded but no artifacts found",
                    details=[*details, "No .whl or .tar.gz files were produced"],
                    findings=[
                        Finding(
                            "build.missing-artifacts",
                            "No .whl or .tar.gz files were produced",
                            CheckStatus.WARNING,
                        )
                    ],
                )

            # Build failed (exit code 1). The backend's traceback and the
            # reason are on stderr, so both streams are shown.
            output = f"{result.stdout}\n{result.stderr}".splitlines()
            lines = [line.rstrip() for line in output if line.strip()]
            details = [*preamble, *notes]
            if len(lines) > MAX_DETAIL_LINES:
                omitted = len(lines) - MAX_DETAIL_LINES
                details.append(f"... ({omitted} earlier lines of output not shown)")
            details.extend(lines[-MAX_DETAIL_LINES:] or ["build printed no output"])
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Build failed",
                details=details,
                findings=[
                    Finding(
                        "build.failed",
                        "python -m build exited with code 1",
                        CheckStatus.ERROR,
                    )
                ],
            )
