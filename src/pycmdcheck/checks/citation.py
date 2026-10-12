"""Citation file check.

Verifies that a package has a citation file such as CITATION.cff
or CITATION.bib for proper academic attribution, or other citation
metadata such as .zenodo.json or a DOI link in the README.
"""

import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.fileio import (
    display_path,
    find_project_file,
    os_error_text,
    read_text_capped,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# An unindented YAML key, with or without quotes and spaces before the colon.
_CFF_KEY_RE = re.compile(r"""^["']?([\w-]+)["']?[ \t]*:(?=\s|$)""", re.MULTILINE)
# A DOI in a README: a doi.org link, or a link to a Zenodo badge.
_DOI_LINK_RE = re.compile(r"doi\.org/10\.\d|zenodo\.org/badge/", re.IGNORECASE)


class CitationCheck(BaseCheck):
    """Check for citation file.

    Searches the project root for CITATION.cff, CITATION.bib, or similar
    files that enable proper academic citation of the package. Without one,
    it accepts .zenodo.json, codemeta.json, a paper.md with a paper.bib (in
    the root, paper/ or joss/), or a README that links a DOI.

    Attributes:
        name: The check identifier ("citation").
        description: Human-readable description of this check.
        rules: The codes this check can report.
        hints: What to do about each code.
    """

    name = "citation"
    description = "Check for citation file"
    rules = {"citation.missing": "No citation file or other citation metadata found"}
    hints = {
        "citation.missing": (
            "Add a CITATION.cff file to the project root, in the format that "
            "https://citation-file-format.github.io/ describes."
        )
    }

    # Extensions of a file named CITATION, most preferred first. The name is
    # matched without regard to letter case.
    CITATION_EXTENSIONS = (".cff", ".bib", "", ".md", ".txt", ".rst")

    # Keys that the CFF 1.2 schema requires in CITATION.cff
    CFF_REQUIRED_KEYS = ["cff-version", "message", "title", "authors"]

    # Directories that may hold a Journal of Open Source Software paper, in
    # search order. "" is the project root.
    PAPER_DIRS = ("", "paper", "joss")

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for citation file presence.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if a citation file or other citation metadata is found
            - NOTE status if neither is found
        """
        found = find_project_file(package_path, ["CITATION"], self.CITATION_EXTENSIONS)
        if found is not None:
            shown = display_path(found, package_path)
            details = [f"Found: {shown}"]
            if found.suffix.lower() == ".cff":
                self._scan_cff_keys(found, shown, details)
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Citation file present",
                details=details,
            )

        other = self._other_metadata(package_path)
        if other:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Citation metadata found",
                details=other,
            )

        names = ", ".join(f"CITATION{ext}" for ext in self.CITATION_EXTENSIONS)
        findings = [
            Finding(
                "citation.missing",
                "No citation file or other citation metadata found",
                CheckStatus.NOTE,
            )
        ]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message="No citation file found",
            details=[
                f"Looked in the project root for {names} (any letter case)",
                "Also accepted: .zenodo.json, codemeta.json, paper.md with "
                "paper.bib, or a DOI link in the README",
            ],
            findings=findings,
        )

    def _other_metadata(self, package_path: Path) -> list[str]:
        """Name the other citation metadata found in the project.

        Returns:
            One detail line for each of .zenodo.json, codemeta.json, a paper.md
            with a paper.bib beside it (the layout of a Journal of Open Source
            Software paper, in the root, paper/ or joss/) and a README that
            links a DOI. Empty when there is none.
        """

        def find(name: str, subdir: str = "") -> str | None:
            path = find_project_file(package_path, [name], ("",), (subdir,))
            return None if path is None else display_path(path, package_path)

        found = [find(".zenodo.json"), find("codemeta.json")]
        for subdir in self.PAPER_DIRS:
            paper, bib = find("paper.md", subdir), find("paper.bib", subdir)
            if paper and bib:
                joss = "a Journal of Open Source Software paper"
                found.append(f"{paper} with {bib} ({joss})")
                break
        readme = find_project_file(package_path, ["README"])
        if readme is not None:
            try:
                text = read_text_capped(readme)
            except OSError:
                text = ""
            if _DOI_LINK_RE.search(text):
                found.append(f"a DOI link in {display_path(readme, package_path)}")
        return [f"Found: {item}" for item in found if item]

    def _scan_cff_keys(self, cff_path: Path, shown: str, details: list[str]) -> None:
        """Report which required keys a line scan finds in CITATION.cff.

        There is no YAML parser to rely on, so this looks for unindented
        ``key:`` lines only. It says what it found and never claims that the
        file is valid, or that a key it did not find is missing.
        """
        try:
            content = read_text_capped(cff_path)
        except OSError as exc:
            details.append(f"Could not read {shown}: {os_error_text(exc)}")
            return

        keys = set(_CFF_KEY_RE.findall(content))
        present = [key for key in self.CFF_REQUIRED_KEYS if key in keys]
        absent = [key for key in self.CFF_REQUIRED_KEYS if key not in keys]
        if present:
            details.append(f"Found keys in {shown}: {', '.join(present)}")
        if absent:
            details.append(
                f"Not found by a line scan: {', '.join(absent)} "
                "(CFF 1.2 requires all four keys)"
            )
