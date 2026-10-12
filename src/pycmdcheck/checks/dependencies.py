"""Dependency audit check.

Cross-references declared dependencies in pyproject.toml with actual
imports found in source files.
"""

import importlib.metadata
import re
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import unparsed_files_detail
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_DETAIL_LINES
from pycmdcheck.fileio import display_path, plain_text
from pycmdcheck.import_scan import scan_imports, third_party
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.pyproject_reader import (
    get_dev_dependencies,
    get_dict,
    get_effective_project_table,
    get_list,
    get_project_table,
    get_str_list,
    get_table,
    is_dynamic,
    parse_requirement,
    pyproject_status,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# Import names of distributions whose own name does not give them away.
# Used when the distribution is not installed beside pycmdcheck.
PYPI_TO_IMPORT: dict[str, tuple[str, ...]] = {
    "pillow": ("PIL",),
    "scikit-learn": ("sklearn",),
    "scikit-image": ("skimage",),
    "python-dateutil": ("dateutil",),
    "pyyaml": ("yaml",),
    "beautifulsoup4": ("bs4",),
    "opencv-python": ("cv2",),
    "opencv-python-headless": ("cv2",),
    "typing-extensions": ("typing_extensions",),
    "pyjwt": ("jwt",),
    "pyzmq": ("zmq",),
    "python-dotenv": ("dotenv",),
    "attrs": ("attr", "attrs"),
    "python-multipart": ("multipart",),
    "msgpack-python": ("msgpack",),
    "ruamel-yaml": ("ruamel",),
    "google-cloud-storage": ("google",),
    "protobuf": ("google",),
    "python-magic": ("magic",),
    "charset-normalizer": ("charset_normalizer",),
    "importlib-metadata": ("importlib_metadata",),
    "importlib-resources": ("importlib_resources",),
    "setuptools": ("setuptools", "pkg_resources"),
    "pycryptodome": ("Crypto",),
    "pymysql": ("pymysql",),
    "psycopg2-binary": ("psycopg2",),
    "psycopg2": ("psycopg2",),
    "gitpython": ("git",),
    "biopython": ("Bio",),
    "pywin32": ("win32api", "win32con", "win32com", "pywintypes"),
    "matplotlib": ("matplotlib", "mpl_toolkits"),
    "pytest": ("pytest", "_pytest"),
}

_LEADING_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")


def _normalize_pypi_name(name: str) -> str:
    """Normalize a PyPI package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _requirement_name(requirement: object) -> str | None:
    """Return the distribution a requirement string names, or ``None``.

    A string that is not a valid requirement (``numpy>=1.0.*``) still counts
    by its leading name.
    """
    parsed = parse_requirement(requirement)
    if parsed is not None:
        return parsed.name
    if not isinstance(requirement, str):
        return None
    match = _LEADING_NAME_RE.match(requirement.strip())
    return match.group(0).rstrip("._-") if match else None


def installed_import_names() -> dict[str, set[str]]:
    """Map each distribution installed beside pycmdcheck to its import names."""
    try:
        provided = importlib.metadata.packages_distributions()
    except Exception:
        # Broken metadata in the environment must not fail the check.
        return {}
    installed: dict[str, set[str]] = {}
    for import_name, distributions in provided.items():
        if import_name.endswith("__mypyc"):
            continue
        for distribution in distributions:
            names = installed.setdefault(_normalize_pypi_name(distribution), set())
            names.add(import_name)
    return installed


def import_names(distribution: str, installed: dict[str, set[str]]) -> set[str]:
    """Return the lower-cased import names a distribution may provide.

    Installed metadata is used when the environment has it. Otherwise the
    names are guesses: the static table, the normalised name and its dashed
    prefixes (``zope`` for ``zope.interface``). A stub distribution such as
    ``pandas-stubs`` gets no prefix guess: it does not provide ``pandas``.
    """
    normalized = _normalize_pypi_name(distribution)
    parts = normalized.split("-")
    names = {"_".join(parts), *PYPI_TO_IMPORT.get(normalized, ())}
    if normalized in installed:
        names.update(installed[normalized])
    elif parts[-1] != "stubs":
        names.update("_".join(parts[:end]) for end in range(1, len(parts)))
    return {name.lower() for name in names}


def declared_names(
    requirements: Iterable[object], installed: dict[str, set[str]]
) -> dict[str, set[str]]:
    """Map each required distribution (normalised) to its import names."""
    declared: dict[str, set[str]] = {}
    for requirement in requirements:
        name = _requirement_name(requirement)
        if name:
            declared[_normalize_pypi_name(name)] = import_names(name, installed)
    return declared


def optional_requirements(package_path: Path, project: dict[str, Any]) -> list[str]:
    """Return the requirements of extras and development tables.

    They can explain an import but are never audited as unused.
    """
    requirements = get_dev_dependencies(package_path)
    extras = get_dict(project, "optional-dependencies")
    for extra in extras:
        requirements += get_str_list(extras, extra)
    return requirements


def unknown_dependencies(
    package_path: Path, project: dict[str, Any]
) -> tuple[str, str, list[str]] | None:
    """Say why the declared dependencies cannot be read from pyproject.toml.

    A missing or unreadable file or table is reported by the metadata check,
    so here it is a failed prerequisite and the details point there.

    Args:
        package_path: The project directory.
        project: The effective ``[project]`` table.

    Returns:
        A ``(summary, skip reason, details)`` triple, or ``None`` when the
        declared set is known (it may be empty).
    """
    status = pyproject_status(package_path)
    outside = "Dependencies declared in setup.py or setup.cfg are not read"
    if status == "missing":
        return (
            "No pyproject.toml found",
            "prerequisite-failed",
            [outside, "The metadata check reports the missing file"],
        )
    if status == "unparseable":
        return (
            "pyproject.toml could not be parsed",
            "prerequisite-failed",
            ["The metadata check reports the reason"],
        )
    if not project:
        return (
            "No [project] table",
            "prerequisite-failed",
            [outside, "The metadata check reports the missing [project] table"],
        )
    # Dynamic extras count too: an import in an optional module may be one.
    for key in ("dependencies", "optional-dependencies"):
        if key not in project and is_dynamic(project, key):
            return (
                "Dependencies are declared dynamically",
                "not-applicable",
                [f"[project].dynamic lists {key}, which are only known at build time"],
            )
    return None


def name_list(label: str, names: Iterable[str]) -> str:
    """Format names as one sorted detail line that says how many were cut."""
    ordered = sorted(set(names))
    shown = ", ".join(plain_text(name, 60) for name in ordered[:MAX_DETAIL_LINES])
    more = len(ordered) - MAX_DETAIL_LINES
    return f"{label}: {shown}" + (f" (+{more} more)" if more > 0 else "")


def first_imported_in(names: Iterable[str], files: dict[str, Path], root: Path) -> str:
    """Format one detail line that gives the first file importing each name.

    The names are those :func:`name_list` shows, in the same order.
    """
    shown = ", ".join(
        f"{plain_text(name, 60)} ({display_path(files[name], root)})"
        for name in sorted(set(names))[:MAX_DETAIL_LINES]
    )
    return f"First imported in: {shown}"


# The hints of the two rules about the dependency list, and the two places
# a project can keep that list in.
_ADD = "Add the package that provides each undeclared module to {}."
_REMOVE = "Remove each dependency the package does not need from {}."
_PROJECT = "dependencies in [project] of pyproject.toml"
_POETRY = "[tool.poetry.dependencies] of pyproject.toml"


class DependenciesCheck(BaseCheck):
    """Check that declared dependencies match actual imports.

    Only an unconditional module-level import of a third-party module can be
    undeclared. Guarded imports (see :mod:`pycmdcheck.import_scan`) and
    imports in tests shipped with the package only count as use.
    """

    name = "dependencies"
    description = "Audit declared vs. actual dependencies"
    rules = {
        "dependencies.undeclared": (
            "A third-party module is imported unconditionally but not declared"
        ),
        "dependencies.unused": "A declared dependency is never imported",
        "dependencies.unparsed-sources": (
            "Source files were found but none of them could be parsed"
        ),
    }
    hints = {
        "dependencies.undeclared": _ADD.format(_PROJECT),
        "dependencies.unused": _REMOVE.format(_PROJECT),
        "dependencies.unparsed-sources": (
            "Correct the syntax errors in the files named in the details, or run "
            "pycmdcheck with the Python version the package is written for."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Cross-reference pyproject.toml dependencies with imports.

        Returns:
            A CheckResult with:

            - OK status if every unconditional import is declared and every
              declared dependency is imported
            - NOTE status if a declared dependency is never imported, or
              none of the source files found could be parsed
            - WARNING status if an unconditional import is not declared
            - SKIPPED status if the dependencies are dynamic or cannot be
              read from pyproject.toml, or no source was found
        """
        project = get_effective_project_table(package_path)
        unknown = unknown_dependencies(package_path, project)
        if unknown is not None:
            summary, reason, why = unknown
            return CheckResult.skip(
                self.name, f"{summary}, skipping dependency audit", reason, why
            )

        raw_deps = get_list(project, "dependencies")
        installed = installed_import_names()
        declared = declared_names(raw_deps, installed)
        optional = declared_names(
            optional_requirements(package_path, project), installed
        )

        # Tests shipped inside the package can use a dependency, but an
        # import there is never a missing runtime dependency.
        layout = PackageLayout(package_path)
        runtime_files = layout.source_files()
        test_files = set(layout.source_files(include_tests=True)) - set(runtime_files)
        scan = scan_imports(runtime_files)
        test_scan = scan_imports(sorted(test_files))
        skipped = scan.skipped + test_scan.skipped

        details: list[str] = []
        unparsed = unparsed_files_detail(skipped, package_path)
        if unparsed:
            details.append(unparsed)

        if scan.scanned == 0 and scan.skipped:
            # Source was found, so this is something the author can fix.
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="Could not parse package source; skipping dependency audit",
                details=details,
                findings=[
                    Finding(
                        "dependencies.unparsed-sources",
                        "No source file could be parsed",
                        CheckStatus.NOTE,
                    )
                ],
            )
        if scan.scanned == 0:
            details.append("Found no package or module under src/ or the project root")
            return CheckResult.skip(
                self.name,
                "Could not locate package source; skipping dependency audit",
                "not-applicable",
                details,
            )

        local = layout.local_names()
        covered: set[str] = set().union(*declared.values(), *optional.values())

        def uncovered(names: Iterable[str]) -> list[str]:
            return [n for n in third_party(names, local) if n.lower() not in covered]

        undeclared = uncovered(scan.hard)
        guarded = uncovered(scan.guarded - set(scan.hard))
        if layout.is_native_build:
            # A private extension module may only exist once the project is built.
            guarded += [name for name in undeclared if name.startswith("_")]
            undeclared = [name for name in undeclared if not name.startswith("_")]
        used = {name.lower() for name in scan.used | test_scan.used}
        unused = [dist for dist, names in declared.items() if not names & used]
        if skipped:
            # A partial scan cannot show that a dependency is unused.
            unused = []

        # One finding for each rule that fired. Its message is the detail line
        # that names what was found.
        findings: list[Finding] = []
        found: list[str] = []
        # The hints name the table that this project lists its dependencies in.
        # Poetry's table only counts while [project] lists no dependencies.
        poetry = get_table(package_path, "tool", "poetry")
        in_project = "dependencies" in get_project_table(package_path)
        place = _POETRY if poetry and not in_project else _PROJECT
        if undeclared:
            line = name_list("Undeclared imports", undeclared)
            findings.append(
                Finding(
                    "dependencies.undeclared",
                    line,
                    CheckStatus.WARNING,
                    _ADD.format(place),
                )
            )
            found += [line, first_imported_in(undeclared, scan.hard, package_path)]
        if unused:
            line = name_list("Unused dependencies", unused)
            findings.append(
                Finding(
                    "dependencies.unused",
                    line,
                    CheckStatus.NOTE,
                    _REMOVE.format(place),
                )
            )
            found += [
                line,
                "Declared but not imported directly. "
                "Each may be a plugin, driver, stub or data package",
            ]
        if guarded:
            label = "Guarded or optional imports not declared (not flagged)"
            found.append(name_list(label, guarded))
        details[:0] = found
        self._describe_requirements(raw_deps, details)

        if findings:
            return CheckResult(
                name=self.name,
                status=most_severe(findings),
                message=(
                    f"Found {len(undeclared)} undeclared import(s)"
                    if undeclared
                    else f"Found {len(unused)} unused declared dependency(ies)"
                ),
                details=details,
                findings=findings,
            )

        if not declared:
            details.append("Package has no declared dependencies")
        elif not skipped:
            details.append(f"All {len(declared)} dependencies verified")
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message=(
                "All dependencies match imports"
                if declared
                else "No dependencies declared"
            ),
            details=details,
        )

    def _describe_requirements(self, raw_deps: list[Any], details: list[str]) -> None:
        """Append informational lines about the requirement strings.

        Names the dependencies with neither a version specifier nor a URL,
        and the entries that are not valid requirements. Neither is a
        finding, so neither changes the status.
        """
        unbounded: list[str] = []
        invalid: list[str] = []
        for dep in raw_deps:
            parsed = parse_requirement(dep)
            if parsed is None:
                invalid.append(repr(dep))
            elif not parsed.specifier and not parsed.url:
                unbounded.append(parsed.name)
        if unbounded:
            label = "Unbounded dependencies (no version specifier)"
            details.append(name_list(label, unbounded))
        if invalid:
            shown = ", ".join(plain_text(d, 60) for d in invalid[:MAX_DETAIL_LINES])
            more = len(invalid) - MAX_DETAIL_LINES
            details.append(
                f"Not valid requirements, matched by name only: {shown}"
                + (f" (+{more} more)" if more > 0 else "")
            )
