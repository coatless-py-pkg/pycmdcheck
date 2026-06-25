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
            details.append(f"Found: {self._rel(package_path, contributing)}")
        else:
            missing.append("CONTRIBUTING.md")

        # Check for CODE_OF_CONDUCT
        coc = self._find_file(package_path, self.CODE_OF_CONDUCT_FILENAMES)
        if coc:
            details.append(f"Found: {self._rel(package_path, coc)}")
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

    # GitHub resolves community-health files from the repo root, .github/, and
    # docs/ (in that order). Mirror that so files in those locations count.
    SEARCH_DIRS = ("", ".github", "docs")

    def _find_file(self, package_path: Path, filenames: list[str]) -> Path | None:
        """Find a community file in the root, ``.github/``, or ``docs/``."""
        for subdir in self.SEARCH_DIRS:
            base = package_path / subdir if subdir else package_path
            for filename in filenames:
                candidate = base / filename
                if candidate.exists():
                    return candidate
        return None

    @staticmethod
    def _rel(package_path: Path, path: Path) -> str:
        """Return *path* relative to *package_path* for display."""
        try:
            return str(path.relative_to(package_path))
        except ValueError:
            return path.name
