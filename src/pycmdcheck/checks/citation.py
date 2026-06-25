"""Citation file check.

Verifies that a package has a citation file such as CITATION.cff
or CITATION.bib for proper academic attribution.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.results import CheckResult, CheckStatus


class CitationCheck(BaseCheck):
    """Check for citation file.

    Searches for CITATION.cff, CITATION.bib, or similar files
    that enable proper academic citation of the package.

    Attributes:
        name: The check identifier ("citation").
        description: Human-readable description of this check.
    """

    name = "citation"
    description = "Check for citation file"

    CITATION_FILENAMES = [
        "CITATION.cff",
        "CITATION.bib",
        "CITATION",
        "CITATION.md",
        "CITATION.txt",
    ]

    # Required keys in a valid CITATION.cff
    CFF_REQUIRED_KEYS = ["title", "authors"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for citation file presence.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if a citation file is found
            - NOTE status if no citation file found
        """
        details: list[str] = []

        for filename in self.CITATION_FILENAMES:
            candidate = package_path / filename
            if candidate.exists():
                details.append(f"Found: {candidate.name}")

                # Basic validation for CITATION.cff
                if candidate.name == "CITATION.cff":
                    self._validate_cff(candidate, details)

                return CheckResult(
                    name=self.name,
                    status=CheckStatus.OK,
                    message="Citation file present",
                    details=details,
                )

        return CheckResult(
            name=self.name,
            status=CheckStatus.NOTE,
            message="No citation file found",
            details=[
                "Consider adding CITATION.cff for academic attribution",
                "See https://citation-file-format.github.io/",
            ],
        )

    def _validate_cff(self, cff_path: Path, details: list[str]) -> None:
        """Basic validation of CITATION.cff content.

        Checks that required keys (title, authors) are present
        by scanning for key patterns in the YAML content.
        Does not require a YAML parser — uses simple line scanning.
        """
        try:
            content = cff_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            details.append("Could not read CITATION.cff")
            return

        missing = []
        for key in self.CFF_REQUIRED_KEYS:
            # Check for top-level key (not indented)
            if not any(line.startswith(f"{key}:") for line in content.splitlines()):
                missing.append(key)

        if missing:
            details.append(f"CITATION.cff missing keys: {', '.join(missing)}")
        else:
            details.append("CITATION.cff has required keys")
