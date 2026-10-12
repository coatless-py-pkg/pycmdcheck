"""Changelog file check.

Verifies that a package has a changelog or news file documenting
changes across releases.
"""

import string
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MIN_CHANGELOG_LENGTH
from pycmdcheck.fileio import (
    display_path,
    find_declared_file,
    find_project_file,
    find_project_files,
    is_regular_file,
    os_error_text,
    plain_text,
    read_text_capped,
)
from pycmdcheck.pyproject_reader import (
    get_effective_project_table,
    get_str_dict,
    project_metadata_source,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# What the PyPA "well-known project URLs" rule drops from a label.
_LABEL_NOISE = str.maketrans("", "", string.punctuation + string.whitespace)
# The second way of a hint. It is only named for a project that keeps its
# metadata in pyproject.toml, because a [project.urls] table in any other
# project would start a [project] table that has no name.
_LINK = ", or link them as Changelog in {} of pyproject.toml"
_ADD = "Add a CHANGELOG.md file with your release notes to the project root{}."
_WRITE = "Write at least {} characters of release notes in {}{}."
_READ = (
    "Give yourself read permission on the changelog file, for example with "
    "chmod u+r {}."
)


def urls_table(package_path: Path) -> str | None:
    """Name the table that holds the project's links, or None if it has none."""
    tables = {"project": "[project.urls]", "poetry": "[tool.poetry.urls]"}
    return tables.get(project_metadata_source(package_path))


class ChangelogCheck(BaseCheck):
    """Check for changelog or news file.

    Searches the project root, ``docs/`` and ``doc/`` for common changelog
    names, then for a directory of news fragments, then for a changelog link
    in the project URLs. A file in the root must have meaningful content.

    Attributes:
        name: The check identifier ("changelog").
        description: Human-readable description of this check.
        rules: The codes this check can report.
        hints: What to do about each code.
    """

    name = "changelog"
    description = "Check for changelog or news file"
    rules = {
        "changelog.missing": (
            "No changelog file, directory of notes or changelog link found"
        ),
        "changelog.too-short": (
            "The changelog file in the project root has fewer than "
            f"{MIN_CHANGELOG_LENGTH} characters"
        ),
        "changelog.unreadable": "The changelog file could not be read",
    }
    hints = {
        "changelog.missing": _ADD.format(_LINK.format("[project.urls]")),
        "changelog.too-short": _WRITE.format(
            MIN_CHANGELOG_LENGTH, "the changelog file", _LINK.format("[project.urls]")
        ),
        "changelog.unreadable": _READ.format("CHANGELOG.md"),
    }

    # File names without extension, matched without regard to letter case.
    CHANGELOG_STEMS = (
        "CHANGELOG",
        "CHANGES",
        "NEWS",
        "HISTORY",
        "RELEASES",
        "RELEASE_NOTES",
        "RELEASE-NOTES",
        "RELEASENOTES",
        "WHATSNEW",
        "WHATS-NEW",
        "WHATS_NEW",
    )
    DOCS_DIRS = ("docs", "doc")
    # Root directories of news fragments (towncrier and similar tools).
    FRAGMENT_DIRS = ("changes", "newsfragments", "changelog.d")
    # Normalized labels from the PyPA list of well-known project URLs.
    URL_LABELS = frozenset(
        {"changelog", "changes", "whatsnew", "history", "releasenotes"}
    )

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for changelog file presence and content.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if a changelog with content is found
            - NOTE status if the changelog is too short or none is found
            - WARNING status if a changelog file cannot be read and no other
              changelog is found, also when a short file sits beside it
        """
        found: str | None = None
        stub: str | None = None
        unreadable: str | None = None
        unread = ""
        # Each hint names the file at hand and the table this project has.
        table = urls_table(package_path)
        link = _LINK.format(table) if table else ""

        for candidate in find_project_files(package_path, self.CHANGELOG_STEMS):
            shown = display_path(candidate, package_path)
            try:
                content = read_text_capped(candidate)
            except OSError as exc:
                unreadable = f"Could not read {shown}: {os_error_text(exc)}"
                unread = shown
                continue
            if len(content.strip()) >= MIN_CHANGELOG_LENGTH:
                found = shown
                break
            # Keep looking: a stub often points at the real file.
            stub = stub or shown

        found = found or self._find_elsewhere(package_path)
        if found:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Changelog present",
                details=[f"Found: {found}"],
            )
        # A file that could not be read may hold the notes, so it comes
        # before a stub. The stub is still named in the details.
        if unreadable:
            findings = [
                Finding(
                    "changelog.unreadable",
                    unreadable,
                    CheckStatus.WARNING,
                    _READ.format(unread),
                )
            ]
            details = [unreadable]
            if stub:
                details.append(
                    f"{stub} has fewer than {MIN_CHANGELOG_LENGTH} characters"
                )
            return CheckResult(
                name=self.name,
                status=most_severe(findings),
                message="Could not read changelog file",
                details=details,
                findings=findings,
            )
        if stub:
            findings = [
                Finding(
                    "changelog.too-short",
                    f"{stub} has fewer than {MIN_CHANGELOG_LENGTH} characters",
                    CheckStatus.NOTE,
                    _WRITE.format(MIN_CHANGELOG_LENGTH, stub, link),
                )
            ]
            return CheckResult(
                name=self.name,
                status=most_severe(findings),
                message="Changelog file is too short",
                details=[
                    f"Found: {stub}",
                    "Changelog appears empty or incomplete: fewer than "
                    f"{MIN_CHANGELOG_LENGTH} characters",
                ],
                findings=findings,
            )
        findings = [
            Finding(
                "changelog.missing",
                "No changelog found",
                CheckStatus.NOTE,
                _ADD.format(link),
            )
        ]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message="No changelog found",
            details=[
                "Looked in the project root, docs/ and doc/ for CHANGELOG, CHANGES, "
                "NEWS, HISTORY, RELEASES, RELEASE_NOTES or WHATSNEW "
                "(.md, .rst, .txt or no extension, any letter case)",
                "Also looked for a directory of notes (changes/, newsfragments/, "
                "changelog.d/, or one of the names above under docs/ or doc/)"
                # With no table of links there was no link to look for.
                + (f" and for a changelog link in {table}" if table else ""),
            ],
            findings=findings,
        )

    def _find_elsewhere(self, package_path: Path) -> str | None:
        """Describe a changelog kept outside the root, judged on presence only."""
        doc_file = find_project_file(
            package_path, self.CHANGELOG_STEMS, subdirs=self.DOCS_DIRS
        )
        if doc_file is not None:
            return display_path(doc_file, package_path)

        dirs = list(self.FRAGMENT_DIRS) + [
            f"{docs}/{stem}" for docs in self.DOCS_DIRS for stem in self.CHANGELOG_STEMS
        ]
        for name in dirs:
            note = find_declared_file(package_path, f"{name}/*")
            # A directory with __init__.py is a Python package, not notes.
            if note is not None and not is_regular_file(note.parent / "__init__.py"):
                return display_path(note.parent, package_path) + "/"

        urls = get_str_dict(get_effective_project_table(package_path), "urls")
        for label in urls:
            if label.translate(_LABEL_NOISE).lower() in self.URL_LABELS:
                return f'"{plain_text(label)}" link in the project URLs'
        return None
