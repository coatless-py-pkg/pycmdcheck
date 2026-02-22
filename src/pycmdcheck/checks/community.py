"""Community files check.

Verifies that a package has community health files such as
CONTRIBUTING.md and CODE_OF_CONDUCT.md.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.results import CheckResult, CheckStatus


class CommunityCheck(BaseCheck):
    """Check for community health files.

    Verifies that CONTRIBUTING.md and CODE_OF_CONDUCT.md exist in
    the package directory.

    Attributes:
        name: The check identifier ("community").
        description: Human-readable description of this check.
    """

    name = "community"
    description = "Check for community health files"

    CONTRIBUTING_FILENAMES = [
        "CONTRIBUTING.md",
        "CONTRIBUTING.rst",
        "CONTRIBUTING.txt",
        "CONTRIBUTING",
    ]

    CODE_OF_CONDUCT_FILENAMES = [
        "CODE_OF_CONDUCT.md",
        "CODE_OF_CONDUCT.rst",
        "CODE_OF_CONDUCT.txt",
        "CODE_OF_CONDUCT",
    ]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for community health files.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if both files are present
            - NOTE status if one or both files are missing
        """
        details: list[str] = []
        missing: list[str] = []

        # Check for CONTRIBUTING
        contributing = self._find_file(package_path, self.CONTRIBUTING_FILENAMES)
        if contributing:
            details.append(f"Found: {contributing.name}")
        else:
            missing.append("CONTRIBUTING.md")

        # Check for CODE_OF_CONDUCT
        coc = self._find_file(package_path, self.CODE_OF_CONDUCT_FILENAMES)
        if coc:
            details.append(f"Found: {coc.name}")
        else:
            missing.append("CODE_OF_CONDUCT.md")

        if not missing:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Community health files present",
                details=details,
            )

        details.append(f"Missing: {', '.join(missing)}")
        return CheckResult(
            name=self.name,
            status=CheckStatus.NOTE,
            message=f"Missing {len(missing)} community file(s)",
            details=details,
        )

    def _find_file(self, package_path: Path, filenames: list[str]) -> Path | None:
        """Find a file matching one of the given filenames."""
        for filename in filenames:
            candidate = package_path / filename
            if candidate.exists():
                return candidate
        return None
