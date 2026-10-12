"""Package metadata validation check.

This module provides the MetadataCheck class which validates that a
Python package has proper metadata defined in pyproject.toml (preferred)
or legacy setup.py/setup.cfg files.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.fileio import (
    is_regular_file,
    is_safe_relative,
    plain_text,
    read_ini,
    resolve_declared_file,
)
from pycmdcheck.pyproject_reader import (
    get_effective_project_table,
    get_project_table,
    get_str,
    get_str_list,
    get_table,
    is_dynamic,
    parse_requirement,
    parse_specifier_set,
    parse_version,
    project_metadata_source,
    pyproject_error,
    pyproject_status,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

_LEGACY_FILES = ("setup.py", "setup.cfg")
# Files a build backend reads the metadata from when pyproject.toml has no
# [project] table: maturin (Cargo.toml) and meson-python (meson.build).
_BACKEND_METADATA_FILES = ("Cargo.toml", "meson.build")
# Other fields that declare the same thing.
_ALTERNATIVE_FIELDS = {"license": ("license-files",), "authors": ("maintainers",)}
# The hint for a missing field. The table is [tool.poetry] in a legacy Poetry
# project, so a finding names the table its project uses.
_ADD_FIELDS = "Add each missing field to the {} table of pyproject.toml."
# The result message for metadata kept in setup.py or setup.cfg.
_LEGACY_MESSAGE = "Metadata is in setup.py or setup.cfg"
# Hints for a pyproject.toml that valid TOML would not cure, by how the
# reason starts.
_UNREADABLE_HINTS = {
    "pyproject.toml could not be read": (
        "Make pyproject.toml a regular file that you have permission to read, "
        "for example with chmod u+r pyproject.toml."
    ),
    "pyproject.toml is larger": (
        "Shorten pyproject.toml until it is within the size limit."
    ),
    "pyproject.toml nests": (
        "Nest the tables and arrays of pyproject.toml less deeply, until they "
        "are within the limit."
    ),
}


def legacy_metadata_files(package_path: Path) -> list[str]:
    """Return which of setup.py and setup.cfg can hold the package metadata.

    setup.cfg counts only with a ``[metadata]`` or ``[options]`` section,
    because many projects keep one just to configure flake8 or pytest.
    """
    found = ["setup.py"] if is_regular_file(package_path / "setup.py") else []
    parser = read_ini(package_path / "setup.cfg")
    sections = parser.sections() if parser is not None else []
    if any(s == "metadata" or s.startswith("options") for s in sections):
        found.append("setup.cfg")
    return found


def _is_project_name(value: Any) -> bool:
    requirement = parse_requirement(value)
    return requirement is not None and requirement.name == value


# Field, test, complaint and a good value to show in the hint, for [project]
# values that cannot be right. Only unambiguous cases are listed: readme and
# license have several valid forms.
_VALUE_RULES: tuple[tuple[str, Callable[[Any], bool], str, str], ...] = (
    ("name", _is_project_name, "is not a valid project name", '"my-package"'),
    (
        "version",
        lambda v: parse_version(v) is not None,
        "is not a valid version",
        '"1.2.0"',
    ),
    (
        "requires-python",
        lambda v: parse_specifier_set(v) is not None,
        "is not a valid version specifier",
        '">=3.12"',
    ),
    ("authors", lambda v: isinstance(v, list), "is not a list", '[{name = "Ada"}]'),
    ("maintainers", lambda v: isinstance(v, list), "is not a list", '[{name = "Ada"}]'),
    (
        "classifiers",
        lambda v: isinstance(v, list),
        "is not a list",
        '["Programming Language :: Python :: 3"]',
    ),
    (
        "urls",
        lambda v: isinstance(v, dict),
        "is not a table",
        '{Homepage = "https://example.com"}',
    ),
)


class MetadataCheck(BaseCheck):
    """Check that package metadata is valid and complete.

    Validates package metadata according to PEP 621 (pyproject.toml).
    Checks for required fields (name, version) and recommends additional
    fields (description, readme, license, requires-python).

    Attributes:
        name: The check identifier ("metadata").
        description: Human-readable description of this check.
        rules: The codes of the findings, each with what the rule means.
        hints: The same codes, each with what to do about the finding.
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
    rules = {
        "metadata.unreadable-pyproject": (
            "pyproject.toml cannot be read or is not valid TOML"
        ),
        "metadata.missing": (
            "No package metadata in pyproject.toml, setup.py or setup.cfg"
        ),
        "metadata.legacy-setup": (
            "The metadata is in setup.py or setup.cfg, not in pyproject.toml"
        ),
        "metadata.missing-project-table": (
            "pyproject.toml has no [project] table, and the build backend reads "
            "the metadata from its own table or file"
        ),
        "metadata.missing-required": "A required field is missing: name or version",
        "metadata.missing-recommended": (
            "A recommended field is missing: description, readme, license or "
            "requires-python"
        ),
        "metadata.missing-extended": (
            "An extended field is missing: authors or maintainers, urls or classifiers"
        ),
        "metadata.missing-build-system": "pyproject.toml has no [build-system] table",
        "metadata.invalid-value": (
            "A [project] value cannot be right, such as a version that is not PEP 440"
        ),
        "metadata.missing-readme-file": (
            "The readme file that [project] names was not found"
        ),
        "metadata.license-classifier-conflict": (
            "A setuptools project lists a license expression and a License "
            "classifier, which setuptools 77 and later refuses to build"
        ),
    }
    hints = {
        "metadata.unreadable-pyproject": (
            "Correct pyproject.toml so that it is valid TOML, saved as UTF-8 with "
            "no byte order mark."
        ),
        "metadata.missing": (
            "Add a [project] table with name and version to pyproject.toml in the "
            "project root."
        ),
        "metadata.legacy-setup": (
            "Move the metadata from setup.py or setup.cfg to a [project] table in "
            "pyproject.toml."
        ),
        "metadata.missing-project-table": (
            "Add a [project] table to pyproject.toml that sets name, and sets "
            "version or lists it in dynamic."
        ),
        "metadata.missing-required": _ADD_FIELDS.format("[project]"),
        "metadata.missing-recommended": _ADD_FIELDS.format("[project]"),
        "metadata.missing-extended": _ADD_FIELDS.format("[project]"),
        "metadata.missing-build-system": (
            "Add a [build-system] table to pyproject.toml that names your build "
            "backend in requires and build-backend."
        ),
        "metadata.invalid-value": (
            "Correct the value in the [project] table of pyproject.toml, for "
            'example version = "1.2.0".'
        ),
        "metadata.missing-readme-file": (
            "Add the file that readme names, or change readme in the [project] "
            "table of pyproject.toml to the file you have."
        ),
        "metadata.license-classifier-conflict": (
            "Remove every classifier that starts with License :: from classifiers "
            "in the [project] table of pyproject.toml."
        ),
    }

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

            - OK status if all required, recommended, and extended (authors,
              urls, classifiers) fields are present and well formed
            - NOTE status if recommended or extended fields are missing, a
              value is not valid, or the metadata is in another backend's
              own table or file
            - WARNING status if the metadata is in setup.py/setup.cfg, or a
              setuptools project lists a license expression and a License
              classifier
            - ERROR status if pyproject.toml cannot be read, required fields
              are missing, or no metadata is found

            The status is that of the most severe finding.
        """
        status = pyproject_status(package_path)
        if status == "unparseable":
            reason = pyproject_error(package_path) or "the file could not be read"
            if not reason.startswith("pyproject.toml"):
                reason = f"TOML parse error: {reason}"
            hint = next(
                (
                    h
                    for start, h in _UNREADABLE_HINTS.items()
                    if reason.startswith(start)
                ),
                None,
            )
            return self._graded(
                "Invalid pyproject.toml",
                [
                    Finding(
                        "metadata.unreadable-pyproject",
                        reason,
                        CheckStatus.ERROR,
                        hint,
                    )
                ],
            )
        if status == "ok":
            return self._check_pyproject(package_path)

        if self._found(package_path, _LEGACY_FILES):
            return self._graded(
                _LEGACY_MESSAGE,
                [
                    Finding(
                        "metadata.legacy-setup",
                        "Using legacy setup.py/setup.cfg instead of pyproject.toml",
                        CheckStatus.WARNING,
                    )
                ],
            )

        return self._graded(
            "No package metadata found",
            [
                Finding(
                    "metadata.missing",
                    "Missing pyproject.toml, setup.py, or setup.cfg",
                    CheckStatus.ERROR,
                )
            ],
        )

    def _graded(
        self, message: str, findings: list[Finding], details: list[str] | None = None
    ) -> CheckResult:
        """Build the result that carries *findings*.

        Its status is that of the most severe finding. Its details are one
        line for each finding, unless *details* gives other lines.
        """
        if details is None:
            details = [finding.message for finding in findings]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=message,
            details=details,
            findings=findings,
        )

    @staticmethod
    def _found(package_path: Path, names: tuple[str, ...]) -> list[str]:
        """Return the *names* that are regular files in the project root."""
        return [name for name in names if is_regular_file(package_path / name)]

    def _no_project_table(self, package_path: Path) -> CheckResult:
        """Grade a pyproject.toml with neither [project] nor [tool.poetry]."""
        missing = "pyproject.toml has no [project] table"
        legacy = legacy_metadata_files(package_path)
        if legacy:
            return self._graded(
                _LEGACY_MESSAGE,
                [
                    Finding(
                        "metadata.legacy-setup",
                        f"{missing}; metadata is in legacy {' and '.join(legacy)}",
                        CheckStatus.WARNING,
                    )
                ],
            )

        other = self._found(package_path, _BACKEND_METADATA_FILES)
        if get_table(package_path, "tool", "flit", "metadata"):
            other.insert(0, "[tool.flit.metadata]")
        if other:
            where = f"metadata from {' or '.join(other)}"
            return self._graded(
                missing,
                [
                    Finding(
                        "metadata.missing-project-table",
                        f"{missing}; the build backend can read {where}",
                        CheckStatus.NOTE,
                    )
                ],
                details=[
                    f"The build backend can read {where}",
                    "Metadata fields were not checked",
                ],
            )

        return self._graded(
            "No package metadata found",
            [
                Finding(
                    "metadata.missing",
                    f"{missing} or [tool.poetry] table, "
                    "and no metadata was found in setup.py or setup.cfg",
                    CheckStatus.ERROR,
                )
            ],
        )

    def _check_pyproject(self, package_path: Path) -> CheckResult:
        """Validate pyproject.toml contents."""
        # Use the effective table so legacy Poetry ([tool.poetry], no [project])
        # is treated like a PEP 621 [project] table rather than reported empty.
        project = get_effective_project_table(package_path)
        source = project_metadata_source(package_path)
        if source == "none" and "project" not in get_table(package_path):
            return self._no_project_table(package_path)

        add = _ADD_FIELDS.format("[tool.poetry]" if source == "poetry" else "[project]")
        missing_required = self._missing(project, self.REQUIRED_FIELDS)
        if missing_required:
            return self._graded(
                "Missing required metadata fields",
                [
                    Finding(
                        "metadata.missing-required",
                        f"Missing required fields: {', '.join(missing_required)}",
                        CheckStatus.ERROR,
                        add,
                    )
                ],
            )

        # One finding for each detail line, in the order people read them.
        findings: list[Finding] = []
        message = "Some metadata values need attention"

        build_system = get_table(package_path, "build-system")
        backend = get_str(build_system, "build-backend", "")
        # maturin fills in the authors from Cargo.toml when it builds.
        cargo = backend == "maturin" and is_regular_file(package_path / "Cargo.toml")

        missing_recommended = [
            field
            for field in self._missing(project, self.RECOMMENDED_FIELDS)
            # python_versions reads the Python constraint of a legacy Poetry
            # table and reports a missing one, so it is not listed twice.
            if not (field == "requires-python" and source == "poetry")
        ]
        missing_extended = [
            "authors or maintainers" if field == "authors" else field
            for field in self._missing(project, self.EXTENDED_FIELDS)
            # Poetry adds license and Python classifiers when it builds.
            if not (field == "classifiers" and source == "poetry")
            and not (field == "authors" and cargo)
        ]
        if missing_extended:
            findings.append(
                Finding(
                    "metadata.missing-extended",
                    f"Missing extended metadata fields: {', '.join(missing_extended)}",
                    CheckStatus.NOTE,
                    add,
                )
            )
            message = "Some extended metadata fields are missing"
        if missing_recommended:
            findings.insert(
                0,
                Finding(
                    "metadata.missing-recommended",
                    f"Missing recommended fields: {', '.join(missing_recommended)}",
                    CheckStatus.NOTE,
                    add,
                ),
            )
            message = "Some recommended metadata fields are missing"

        if not build_system:
            findings.append(
                Finding(
                    "metadata.missing-build-system",
                    "pyproject.toml has no [build-system] table",
                    CheckStatus.NOTE,
                )
            )
        # Values filled in from [tool.poetry] follow Poetry's own rules, so
        # only the real [project] table is judged from here on.
        declared = get_project_table(package_path)
        findings.extend(self._value_problems(package_path, declared))

        # Only setuptools refuses to build this pair. Other backends accept
        # it, so for them it is not reported.
        expression = get_str(declared, "license", "").strip()
        if (
            backend.startswith("setuptools")
            and expression
            and self._license_classifiers(declared)
        ):
            findings.append(
                Finding(
                    "metadata.license-classifier-conflict",
                    "license is an SPDX expression and a License classifier is "
                    "also listed, which setuptools 77 and later refuses to build",
                    CheckStatus.WARNING,
                )
            )
            message = "License classifier conflicts with the license expression"

        if findings:
            return self._graded(message, findings)

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Package metadata is valid",
            details=["All metadata fields present"],
        )

    @staticmethod
    def _license_classifiers(project: dict[str, Any]) -> list[str]:
        """Return the ``License ::`` trove classifiers of a project table."""
        classifiers = get_str_list(project, "classifiers")
        return [c for c in classifiers if c.startswith("License ::")]

    def _missing(self, project: dict[str, Any], fields: list[str]) -> list[str]:
        """Return the *fields* that are not declared."""
        return [field for field in fields if not self._is_declared(project, field)]

    @classmethod
    def _is_declared(cls, project: dict[str, Any], field: str) -> bool:
        """Return whether a ``[project]`` field is declared.

        A field counts as declared if it holds a non-empty value OR is listed
        in PEP 621 ``dynamic`` (the build backend then supplies its value,
        e.g. via setuptools_scm, hatch-vcs, or uv-dynamic-versioning).
        ``maintainers`` stands in for ``authors``, and ``license-files`` or a
        License classifier for ``license``.
        """
        for key in (field, *_ALTERNATIVE_FIELDS.get(field, ())):
            value = project.get(key)
            if isinstance(value, str):
                value = value.strip()
            if value not in (None, "", [], {}) or is_dynamic(project, key):
                return True
        return field == "license" and bool(cls._license_classifiers(project))

    @staticmethod
    def _value_problems(package_path: Path, declared: dict[str, Any]) -> list[Finding]:
        """Return one finding per ``[project]`` value that cannot be right."""
        problems: list[Finding] = []
        for field, is_valid, complaint, example in _VALUE_RULES:
            value = declared.get(field)
            if value not in (None, "", [], {}) and not is_valid(value):
                problems.append(
                    Finding(
                        "metadata.invalid-value",
                        f"{field} {complaint}: {plain_text(repr(value), 60)}",
                        CheckStatus.NOTE,
                        f"Set {field} in the [project] table of pyproject.toml to a "
                        f"value such as {example}.",
                    )
                )

        readme = declared.get("readme")
        path = readme.get("file") if isinstance(readme, dict) else readme
        # A path that leaves the project is not looked up, so it is not judged.
        if is_safe_relative(path) and resolve_declared_file(package_path, path) is None:
            problems.append(
                Finding(
                    "metadata.missing-readme-file",
                    f"readme file {plain_text(str(path), 80)} was not found",
                    CheckStatus.NOTE,
                )
            )
        return problems
