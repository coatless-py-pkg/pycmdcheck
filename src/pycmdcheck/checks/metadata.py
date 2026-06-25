"""Package metadata validation check.

This module provides the MetadataCheck class which validates that a
Python package has proper metadata defined in pyproject.toml (preferred)
or legacy setup.py/setup.cfg files.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.pyproject_reader import get_effective_project_table, read_pyproject
from pycmdcheck.results import CheckResult, CheckStatus


class MetadataCheck(BaseCheck):
    """Check that package metadata is valid and complete.

    Validates package metadata according to PEP 621 (pyproject.toml).
    Checks for required fields (name, version) and recommends additional
    fields (description, readme, license, requires-python).

    Attributes:
        name: The check identifier ("metadata").
        description: Human-readable description of this check.
        REQUIRED_FIELDS: List of fields that must be present in [project].
        RECOMMENDED_FIELDS: List of fields that should be present.
        EXTENDED_FIELDS: List of extended fields checked for completeness.

    Examples:
        Run the metadata check on a package:

        >>> from pathlib import Path
        >>> check = MetadataCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.status in [CheckStatus.OK, CheckStatus.NOTE, CheckStatus.ERROR]
        True

        The check validates pyproject.toml structure:

        >>> check.REQUIRED_FIELDS
        ['name', 'version']
    """

    name = "metadata"
    description = "Validate package metadata (pyproject.toml)"

    REQUIRED_FIELDS = ["name", "version"]
    RECOMMENDED_FIELDS = ["description", "readme", "license", "requires-python"]
    EXTENDED_FIELDS = ["authors", "urls", "classifiers"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check package metadata for validity and completeness.

        Looks for pyproject.toml first (preferred), falling back to
        setup.py/setup.cfg (legacy). Validates required fields and
        suggests recommended fields.

        Args:
            package_path: Path to the package directory containing
                pyproject.toml or setup files.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if all required and recommended fields present
            - NOTE status if required fields present but some recommended missing
            - WARNING status if using legacy setup.py/setup.cfg
            - ERROR status if pyproject.toml is invalid or missing required fields
        """
        pyproject_path = package_path / "pyproject.toml"
        setup_py_path = package_path / "setup.py"
        setup_cfg_path = package_path / "setup.cfg"

        details: list[str] = []

        # Check for pyproject.toml (preferred)
        if pyproject_path.exists():
            return self._check_pyproject(pyproject_path, details)

        # Check for setup.py or setup.cfg
        if setup_py_path.exists() or setup_cfg_path.exists():
            details.append("Using legacy setup.py/setup.cfg instead of pyproject.toml")
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="Consider migrating to pyproject.toml",
                details=details,
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="No package metadata found",
            details=["Missing pyproject.toml, setup.py, or setup.cfg"],
        )

    def _check_pyproject(self, pyproject_path: Path, details: list[str]) -> CheckResult:
        """Validate pyproject.toml contents."""
        try:
            pyproject = read_pyproject(pyproject_path.parent)
        except (ValueError, OSError) as e:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Invalid pyproject.toml",
                details=[f"TOML parse error: {e}"],
            )

        if pyproject is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="No package metadata found",
                details=["Missing pyproject.toml"],
            )

        # Use the effective table so legacy Poetry ([tool.poetry], no [project])
        # is treated like a PEP 621 [project] table rather than reported empty.
        project = get_effective_project_table(pyproject_path.parent)
        missing_required: list[str] = []
        missing_recommended: list[str] = []

        # Check required fields
        for field in self.REQUIRED_FIELDS:
            if not self._is_declared(project, field):
                missing_required.append(field)

        # Check recommended fields
        for field in self.RECOMMENDED_FIELDS:
            if not self._is_declared(project, field):
                missing_recommended.append(field)

        # Build result
        if missing_required:
            details.append(f"Missing required fields: {', '.join(missing_required)}")
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Missing required metadata fields",
                details=details,
            )

        if missing_recommended:
            details.append(
                f"Missing recommended fields: {', '.join(missing_recommended)}"
            )
            self._check_extended_fields(project, pyproject, details)
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="Some recommended metadata fields are missing",
                details=details,
            )

        self._check_extended_fields(project, pyproject, details)

        if any("missing extended" in d.lower() for d in details):
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="Some extended metadata fields are missing",
                details=details,
            )

        details.append("All metadata fields present")
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Package metadata is valid",
            details=details,
        )

    @staticmethod
    def _is_declared(project: dict[str, Any], field: str) -> bool:
        """Return whether a ``[project]`` field is declared.

        A field counts as declared if it is statically present in the
        ``[project]`` table OR listed in PEP 621 ``dynamic`` (in which case
        the build backend supplies its value at build time, e.g. via
        setuptools_scm, hatch-vcs, or uv-dynamic-versioning). Fields named
        in ``dynamic`` must not be reported as missing.
        """
        if field in project:
            return True
        dynamic = project.get("dynamic", [])
        return isinstance(dynamic, list) and field in dynamic

    def _check_extended_fields(
        self,
        project: dict[str, Any],
        pyproject: dict[str, Any],
        details: list[str],
    ) -> None:
        """Check extended metadata fields and append notes to details.

        Validates that ``authors``, ``classifiers``, and ``[project.urls]``
        are present and non-empty.  Missing or empty fields are reported as
        a single NOTE-level detail line.  Fields declared in PEP 621
        ``dynamic`` are considered present (the build backend supplies them).

        Args:
            project: The ``[project]`` table from pyproject.toml.
            pyproject: The full parsed pyproject.toml dictionary.
            details: Mutable list to which detail strings are appended.
        """
        missing: list[str] = []

        # authors: must be a non-empty list (or declared dynamic)
        authors = project.get("authors")
        if not self._is_declared(project, "authors") and (
            not isinstance(authors, list) or len(authors) == 0
        ):
            missing.append("authors")

        # urls: the [project.urls] table must exist with >=1 entry (or dynamic)
        urls = project.get("urls")
        if not self._is_declared(project, "urls") and (
            not isinstance(urls, dict) or len(urls) == 0
        ):
            missing.append("urls")

        # classifiers: must be a non-empty list (or declared dynamic)
        classifiers = project.get("classifiers")
        if not self._is_declared(project, "classifiers") and (
            not isinstance(classifiers, list) or len(classifiers) == 0
        ):
            missing.append("classifiers")

        if missing:
            details.append(f"Missing extended metadata fields: {', '.join(missing)}")
