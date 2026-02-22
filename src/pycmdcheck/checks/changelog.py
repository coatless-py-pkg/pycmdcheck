"""Changelog file check.

Verifies that a package has a changelog or news file documenting
changes across releases.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MIN_CHANGELOG_LENGTH
from pycmdcheck.results import CheckResult, CheckStatus


class ChangelogCheck(BaseCheck):
    """Check for changelog or news file.

    Searches for common changelog file names and verifies
    the file has meaningful content.

    Attributes:
        name: The check identifier ("changelog").
        description: Human-readable description of this check.
    """

    name = "changelog"
    description = "Check for changelog or news file"

    CHANGELOG_FILENAMES = [
        "CHANGELOG.md",
        "CHANGELOG.rst",
        "CHANGELOG.txt",
        "CHANGELOG",
        "CHANGES.md",
        "CHANGES.rst",
        "CHANGES.txt",
        "CHANGES",
        "NEWS.md",
        "NEWS.rst",
        "NEWS.txt",
        "NEWS",
        "HISTORY.md",
        "HISTORY.rst",
        "HISTORY.txt",
        "HISTORY",
    ]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for changelog file presence and content.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if a changelog with content is found
            - WARNING status if changelog exists but is too short
            - NOTE status if no changelog found
        """
        details: list[str] = []

        for filename in self.CHANGELOG_FILENAMES:
            candidate = package_path / filename
            if candidate.exists():
                details.append(f"Found: {candidate.name}")
                try:
                    content = candidate.read_text(encoding="utf-8")
                    if len(content.strip()) < MIN_CHANGELOG_LENGTH:
                        details.append("Changelog appears empty or incomplete")
                        return CheckResult(
                            name=self.name,
                            status=CheckStatus.WARNING,
                            message="Changelog file is too short",
                            details=details,
                        )
                except (OSError, UnicodeDecodeError) as e:
                    details.append(f"Could not read changelog: {e}")
                    return CheckResult(
                        name=self.name,
                        status=CheckStatus.WARNING,
                        message="Could not read changelog file",
                        details=details,
                    )

                return CheckResult(
                    name=self.name,
                    status=CheckStatus.OK,
                    message="Changelog present",
                    details=details,
                )

        return CheckResult(
            name=self.name,
            status=CheckStatus.NOTE,
            message="No changelog found",
            details=[
                "Consider adding CHANGELOG.md to document changes across releases"
            ],
        )
