"""Package structure validation check.

This module provides the StructureCheck class which validates that a
Python project holds an importable package or module. Discovery is done by
:class:`~pycmdcheck.package_layout.PackageLayout`, so this check sees the
same layout as every other check.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_DETAIL_LINES
from pycmdcheck.fileio import (
    display_path,
    find_declared_files,
    is_regular_file,
    plain_text,
)
from pycmdcheck.package_layout import NON_PACKAGE_DIRS, PackageLayout
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# How each kind of PackageLayout candidate is named in details and messages.
KIND_LABELS = {
    "package": "package",
    "namespace": "namespace package",
    "stub-only": "stub-only package",
    "module": "single-file module",
}
SEARCHED = (
    "Looked in the project root, src/, lib/, python/ and any source directory"
    " the build configuration names"
)

# The hint for a project that holds no code of its own yet.
_CREATE_PACKAGE = (
    "Create src/<name>/__init__.py, where <name> is the name used to import the "
    "package."
)


def _listed(names: list[str]) -> str:
    """Join *names*, saying how many were left out past MAX_DETAIL_LINES."""
    shown = ", ".join(plain_text(name) for name in names[:MAX_DETAIL_LINES])
    extra = len(names) - MAX_DETAIL_LINES
    return f"{shown} (+{extra} more)" if extra > 0 else shown


def _stray_python(package_path: Path) -> list[str]:
    """Return root scripts and directories holding a package that no layout claims.

    Version 0.1.1 reported these as a valid structure (``noxfile.py`` alone,
    ``packages/core/src/core``, a virtualenv in ``env/``), so they keep the
    result below ERROR. The names it skipped are skipped here too.
    """
    stray: set[str] = set()
    patterns = ["*.py", "*/**/__init__.py"]
    for found in find_declared_files(package_path, patterns, limit=200):
        parts = found.relative_to(package_path).parts
        top = parts[0]
        if len(parts) == 1:
            skip = top in ("setup.py", "conftest.py")
        else:
            skip = top.startswith("_") or top in NON_PACKAGE_DIRS
        if not skip:
            stray.add(top)
    return sorted(stray)


class StructureCheck(BaseCheck):
    """Check that the project has an importable package or module.

    Accepted layouts:

    - **src layout**: code in ``src/<package_name>/``
    - **flat layout**: code directly in the project root
    - a source directory named by the build backend, ``lib/`` or ``python/``
    - a single-file module, a PEP 420 namespace package or a stub-only package

    Directories of Rust, C or front-end sources under ``src/`` are ignored. A
    native-extension project with no Python package is skipped.

    Attributes:
        name: The check identifier ("structure").
        description: Human-readable description of this check.
        rules: Every code the check can emit, with what the rule means.
        hints: The same codes, each with what to do about the finding.

    Examples:
        Run the structure check:

        >>> from pathlib import Path
        >>> check = StructureCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'structure'
    """

    name = "structure"
    description = "Validate package directory structure"
    rules = {
        "structure.missing-package": "No importable package or module was found",
        "structure.missing-init": "The package directory has no __init__.py",
        "structure.non-python-sources": (
            "src/ holds no Python package and nothing says the build is native"
        ),
        "structure.unrecognized-layout": (
            "Python files exist only outside the layouts the check knows"
        ),
    }
    hints = {
        "structure.missing-package": (
            "Add an __init__.py file to the directory that holds your code, or "
            "create src/<name>/__init__.py if there is no code yet."
        ),
        "structure.missing-init": (
            "Add an empty __init__.py file to the package directory."
        ),
        "structure.non-python-sources": (
            "Add a package directory with an __init__.py file to src/, or list the "
            "build tool, such as maturin or Cython, in requires under [build-system]."
        ),
        "structure.unrecognized-layout": (
            "Move the package to src/<name>/, or name the directory that holds it "
            "in the build settings, such as where in [tool.setuptools.packages.find]."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check package directory structure.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if a package, module, namespace package or stub-only
              package is found
            - NOTE status if nothing is found and ``src/`` holds sources that
              no native build explains, or Python files exist only outside a
              recognized layout
            - WARNING status if the package directory has no ``__init__.py``
            - ERROR status if no package or module is found
            - SKIPPED status ("not-applicable") if nothing is found in a
              native-extension project
        """
        layout = PackageLayout(package_path)
        primary = layout.primary_candidate
        if primary is None:
            return self._nothing_found(package_path, layout)

        base = primary.root.path
        if base == package_path / "src":
            where, message = "src layout", "Valid src layout structure"
        elif base == package_path:
            where, message = "flat layout", "Valid flat layout structure"
        else:
            where = f"source directory {display_path(base, package_path)}/"
            message = "Valid package structure"
        if primary.kind != "package":
            message = f"Valid {KIND_LABELS[primary.kind]} structure"
        details = [f"Using {where}"]

        # Helpers (benchmarks/, loose scripts beside a package) are not listed.
        shown = [c for c in layout.packages if c.rank >= 2] or layout.packages
        # A directory of modules with no __init__.py anywhere in it.
        bare = [c for c in shown if c.kind == "namespace" and not c.contains_package]
        for kind, label in KIND_LABELS.items():
            names = [c.path.name for c in shown if c.kind == kind and c not in bare]
            if names:
                plural = "s" if len(names) > 1 else ""
                details.append(f"Found {label}{plural}: {_listed(names)}")
        ignored = [d.name for d in layout.non_python_src_dirs]
        if ignored:
            details.append(
                f"Ignored non-Python directories in src/: {_listed(ignored)}"
            )

        if primary in bare:
            missing = f"Missing __init__.py in: {_listed([c.name for c in bare])}"
            details.append(missing)
            return self._report(
                "structure.missing-init",
                CheckStatus.WARNING,
                "Some packages missing __init__.py",
                details,
                found=missing,
            )
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message=message,
            details=details,
        )

    def _report(
        self,
        code: str,
        status: CheckStatus,
        message: str,
        details: list[str],
        *,
        found: str = "",
        hint: str | None = None,
    ) -> CheckResult:
        """Build the result for the one rule that fired.

        The finding says *found*, or *message* when that already names it. A
        *hint* given here is used in place of the one in ``hints``.
        """
        findings = [Finding(code, found or message, status, hint)]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=message,
            details=details,
            findings=findings,
        )

    def _nothing_found(self, package_path: Path, layout: PackageLayout) -> CheckResult:
        """Grade a project in which no importable package or module was found."""
        evidence = layout.native_build_evidence
        if evidence:
            # The build makes the module, so no Python package is expected.
            in_src = " in src/" if layout.has_src_dir else ""
            return CheckResult.skip(
                self.name,
                f"No Python package{in_src} (native-extension layout)",
                "not-applicable",
                [*evidence, SEARCHED],
            )
        if layout.src_has_non_python_sources:
            # Without a sign of a native build, src/ may hold data or Java.
            return self._report(
                "structure.non-python-sources",
                CheckStatus.NOTE,
                "No Python package in src/ (non-Python sources only)",
                [SEARCHED],
            )
        stray = _stray_python(package_path)
        if stray:
            outside = f"Python files outside a recognized layout: {_listed(stray)}"
            # A script or a virtual environment is no package that could be
            # moved, so such a project is told to create one.
            movable = any(
                (package_path / name).is_dir()
                and not is_regular_file(package_path / name / "pyvenv.cfg")
                for name in stray
            )
            return self._report(
                "structure.unrecognized-layout",
                CheckStatus.NOTE,
                "No package found in a recognized layout",
                [outside, SEARCHED],
                found=outside,
                hint=None if movable else _CREATE_PACKAGE,
            )
        return self._report(
            "structure.missing-package",
            CheckStatus.ERROR,
            (
                "No package directory found in src/"
                if layout.has_src_dir
                else "No package or module found"
            ),
            [SEARCHED],
        )
