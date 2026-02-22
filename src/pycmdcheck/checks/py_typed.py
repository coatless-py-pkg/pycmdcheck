"""PEP 561 py.typed marker check.

This module provides the PyTypedCheck class which verifies that packages
using type annotations include a ``py.typed`` marker file as required by
PEP 561 for downstream type checkers to recognise the package as typed.
"""

import ast
import logging
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_TYPE_SCAN_FILES
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.results import CheckResult, CheckStatus

logger = logging.getLogger(__name__)


class PyTypedCheck(BaseCheck):
    """Check for PEP 561 ``py.typed`` marker file.

    Scans the package source for type annotation usage and verifies
    that a ``py.typed`` marker file exists in the package directory.
    If annotations are found but the marker is missing, downstream
    type checkers (mypy, pyright) will not recognise the package as
    typed.

    Attributes:
        name: The check identifier (``"py_typed"``).
        description: Human-readable description of this check.

    Examples:
        Run the py.typed check:

        >>> from pathlib import Path
        >>> check = PyTypedCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'py_typed'
    """

    name = "py_typed"
    description = "Check for PEP 561 py.typed marker"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for py.typed marker in typed packages.

        Locates the main package directory, determines whether type
        annotations are used, and checks for the ``py.typed`` marker.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if ``py.typed`` is present
            - NOTE status if types are used but no ``py.typed`` marker
            - OK status if no type annotations detected (marker not needed)
            - NOTE status if no package directory found
        """
        details: list[str] = []

        # Locate the package directory (the one with __init__.py)
        package_dir = PackageLayout(package_path).primary_package
        if package_dir is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No package directory found",
                details=["Could not locate a package with __init__.py"],
            )

        details.append(f"Package directory: {package_dir.relative_to(package_path)}")

        py_typed_path = package_dir / "py.typed"
        has_marker = py_typed_path.exists()

        if has_marker:
            details.append("py.typed marker file present")

        # Scan for type annotation usage
        uses_types = self._package_uses_types(package_dir)

        if has_marker and uses_types:
            details.append("Package uses type annotations")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="py.typed marker present in typed package",
                details=details,
            )

        if has_marker and not uses_types:
            details.append("No type annotations detected (marker still valid)")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="py.typed marker present",
                details=details,
            )

        if uses_types and not has_marker:
            details.append("Package uses type annotations but has no py.typed marker")
            details.append(
                f"Add an empty file: {py_typed_path.relative_to(package_path)}"
            )
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="Missing py.typed marker for typed package",
                details=details,
            )

        # No types used, no marker -- nothing to do
        details.append("No type annotations detected; py.typed not required")
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="py.typed not needed (no type annotations found)",
            details=details,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _package_uses_types(self, package_dir: Path) -> bool:
        """Determine whether the package uses type annotations.

        Scans Python files for function/method parameter annotations,
        return annotations, and variable annotations. Only the first
        20 files are checked for performance.

        Args:
            package_dir: Path to the package directory.

        Returns:
            True if any type annotation usage is detected.
        """
        py_files = list(package_dir.rglob("*.py"))
        # Exclude __pycache__
        py_files = [f for f in py_files if "__pycache__" not in f.parts]

        for py_file in py_files[:MAX_TYPE_SCAN_FILES]:
            try:
                if self._file_uses_types(py_file):
                    return True
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Could not check %s for types: %s", py_file, exc)
                continue

        return False

    def _file_uses_types(self, py_file: Path) -> bool:
        """Check if a single Python file uses type annotations.

        Looks for:
        - Function parameter annotations (``def f(x: int)``)
        - Return annotations (``def f() -> str``)
        - Variable annotations (``x: int = 1``)
        - Imports from ``typing`` or ``typing_extensions``

        Args:
            py_file: Path to a ``.py`` file.

        Returns:
            True if any annotation usage is found.
        """
        tree = parse_file(py_file)
        if tree is None:
            return False

        for node in ast.walk(tree):
            # Check for function annotations
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # Return annotation
                if node.returns is not None:
                    return True
                # Parameter annotations
                all_args = node.args.posonlyargs + node.args.args + node.args.kwonlyargs
                for arg in all_args:
                    if arg.annotation is not None:
                        return True
                if node.args.vararg and node.args.vararg.annotation is not None:
                    return True
                if node.args.kwarg and node.args.kwarg.annotation is not None:
                    return True

            # Check for variable annotations
            elif isinstance(node, ast.AnnAssign):
                return True

            # Check for typing imports
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.split(".")[0] in (
                    "typing",
                    "typing_extensions",
                ):
                    return True

        return False
