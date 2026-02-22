"""Build check — verifies the package can be built."""

import importlib.util
import tempfile
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import run_tool, sanitize_args


class BuildCheck(BaseCheck):
    """Check that the package can be built into a wheel."""

    name = "build"
    description = "Verify package builds successfully"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Attempt to build the package."""
        # Check if build module is available
        if importlib.util.find_spec("build") is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="'build' module not installed",
                details=["Install it: pip install build"],
            )

        # Check pyproject.toml exists
        if not (package_path / "pyproject.toml").exists():
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="No pyproject.toml found",
                details=["Cannot build without pyproject.toml"],
            )

        timeout = config.get("timeout", DEFAULT_TIMEOUT)
        args = sanitize_args(config.get("args", []))

        with tempfile.TemporaryDirectory() as tmpdir:
            cmd = ["python", "-m", "build", "--outdir", tmpdir, *args]
            result = run_tool(cmd, cwd=package_path, timeout=timeout)

            if result.timed_out:
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.ERROR,
                    message="Build timed out",
                    details=[f"Build took longer than {timeout} seconds"],
                )

            if result.error is not None:
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.ERROR,
                    message="Failed to run build",
                    details=[result.error],
                )

            if result.success:
                # Verify wheel was created
                wheels = list(Path(tmpdir).glob("*.whl"))
                sdists = list(Path(tmpdir).glob("*.tar.gz"))
                details = []
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
                    details=["No .whl or .tar.gz files were produced"],
                )

            # Build failed (non-zero exit code)
            output_lines = result.output_lines()
            details = (
                output_lines[-MAX_DETAIL_LINES:]
                if output_lines
                else ["No output captured"]
            )
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Build failed",
                details=details,
            )
