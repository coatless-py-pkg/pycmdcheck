"""Python version support check.

Verifies that a package's ``requires-python`` specifier excludes
end-of-life Python versions.
"""

import datetime
import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import PYTHON_EOL_VERSIONS
from pycmdcheck.pyproject_reader import read_pyproject
from pycmdcheck.results import CheckResult, CheckStatus


class PythonVersionsCheck(BaseCheck):
    """Check that requires-python excludes end-of-life Python versions.

    Parses the ``requires-python`` field from pyproject.toml and checks
    whether it allows any Python versions that have reached end-of-life.

    Attributes:
        name: The check identifier ("python_versions").
        description: Human-readable description of this check.
    """

    name = "python_versions"
    description = "Check Python version support (EOL versions)"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check requires-python against EOL versions.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if requires-python excludes all EOL versions
            - NOTE status if requires-python allows EOL versions
            - WARNING status if no requires-python specified
            - NOTE status if no pyproject.toml found
        """
        details: list[str] = []

        pyproject = read_pyproject(package_path)
        if pyproject is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No pyproject.toml found",
                details=["Cannot check requires-python without pyproject.toml"],
            )

        project = pyproject.get("project", {})
        requires_python = project.get("requires-python")

        if not requires_python:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No requires-python specified",
                details=[
                    "Add requires-python to [project] in pyproject.toml",
                    'Example: requires-python = ">=3.10"',
                ],
            )

        details.append(f"requires-python: {requires_python}")

        # Determine which EOL versions are currently past EOL
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        current_date = (now.year, now.month)

        eol_allowed: list[str] = []
        for version, eol_date in PYTHON_EOL_VERSIONS.items():
            if eol_date <= current_date:
                # This version is past EOL — check if requires-python allows it
                if self._specifier_allows(requires_python, version):
                    eol_allowed.append(version)
                    details.append(f"Python {version} is EOL but allowed")

        if eol_allowed:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message=(
                    f"requires-python allows EOL version(s): {', '.join(eol_allowed)}"
                ),
                details=details,
            )

        details.append("All allowed Python versions are supported")
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Python version requirement is current",
            details=details,
        )

    def _specifier_allows(self, specifier: str, version: str) -> bool:
        """Check if a PEP 440 specifier allows the given version.

        Uses simple string parsing to avoid requiring the ``packaging``
        library. Handles common patterns like ``>=3.10``, ``>=3.8,<4``.
        """
        # Try using packaging.specifiers if available
        try:
            from packaging.specifiers import SpecifierSet

            return version in SpecifierSet(specifier, prereleases=True)
        except ImportError:
            pass

        # Fallback: parse simple >=X.Y patterns
        match = re.search(r">=\s*(\d+\.\d+)", specifier)
        if match:
            min_version = match.group(1)
            return self._version_tuple(version) >= self._version_tuple(min_version)

        # If we can't parse, assume it allows the version
        return True

    def _version_tuple(self, version: str) -> tuple[int, ...]:
        """Convert a version string to a comparable tuple."""
        return tuple(int(x) for x in version.split("."))
