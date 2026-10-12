"""Community files check.

Verifies that a package has community health files such as
CONTRIBUTING.md and CODE_OF_CONDUCT.md.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.fileio import display_path, find_project_file
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe


class CommunityCheck(BaseCheck):
    """Check for community health files.

    Looks for a contributing guide and a code of conduct in the project
    root, ``.github/``, ``docs/`` and ``doc/``. Default files that an
    organization keeps in its own ``.github`` repository are not part of a
    clone, so this check cannot see them.

    Attributes:
        name: The check identifier ("community").
        description: Human-readable description of this check.
        rules: The codes this check can report.
        hints: What to do about each code.
    """

    name = "community"
    description = "Check for community health files"
    rules = {
        "community.missing-contributing": (
            "No contributing guide found in the repository"
        ),
        "community.missing-code-of-conduct": (
            "No code of conduct found in the repository"
        ),
    }
    hints = {
        "community.missing-contributing": (
            "Add a CONTRIBUTING.md file to the project root or .github/ that says "
            "how to report problems and send changes."
        ),
        "community.missing-code-of-conduct": (
            "Add a CODE_OF_CONDUCT.md file to the project root or .github/, with a "
            "text such as the Contributor Covenant."
        ),
    }

    # File names without extension, matched without regard to letter case.
    # Each may end in .md, .rst, .txt or nothing.
    CONTRIBUTING_STEMS = ("CONTRIBUTING",)
    CODE_OF_CONDUCT_STEMS = ("CODE_OF_CONDUCT", "CODE-OF-CONDUCT", "CONDUCT")

    # GitHub reads community health files from .github/, the repository root
    # and docs/. A file in any of them counts here, and in doc/ as well.
    SEARCH_DIRS = ("", ".github", "docs", "doc")

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for community health files.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if both files are present
            - NOTE status if one or both files are not in the repository
        """
        details: list[str] = []
        missing: list[str] = []
        findings: list[Finding] = []

        wanted = (
            (
                "CONTRIBUTING.md",
                self.CONTRIBUTING_STEMS,
                "community.missing-contributing",
            ),
            (
                "CODE_OF_CONDUCT.md",
                self.CODE_OF_CONDUCT_STEMS,
                "community.missing-code-of-conduct",
            ),
        )
        for label, stems, code in wanted:
            found = find_project_file(package_path, stems, subdirs=self.SEARCH_DIRS)
            if found is not None:
                details.append(f"Found: {display_path(found, package_path)}")
            else:
                missing.append(label)
                findings.append(
                    Finding(code, f"{label} not found in repository", CheckStatus.NOTE)
                )

        if not missing:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Community health files present",
                details=details,
            )

        searched = ", ".join(f"{d}/" if d else "root" for d in self.SEARCH_DIRS)
        details.append(f"Not found in repository ({searched}): {', '.join(missing)}")
        details.append(
            "Organization-level default community health files cannot be "
            "detected by this check"
        )
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=f"{len(missing)} community file(s) not found in repository",
            details=details,
            findings=findings,
        )
