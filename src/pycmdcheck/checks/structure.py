"""Package structure validation check.

This module provides the StructureCheck class which validates that a
Python package follows standard directory layouts (src layout or flat layout).
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.package_layout import NON_PACKAGE_DIRS
from pycmdcheck.results import CheckResult, CheckStatus


class StructureCheck(BaseCheck):
    """Check that package has a valid directory structure.

    Validates that the package follows one of the standard Python
    package layouts:

    - **src layout**: Package code in ``src/<package_name>/``
    - **flat layout**: Package code directly in project root

    The check verifies that package directories contain ``__init__.py``
    files and are properly structured.

    Attributes:
        name: The check identifier ("structure").
        description: Human-readable description of this check.

    Examples:
        Run the structure check:

        >>> from pathlib import Path
        >>> check = StructureCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'structure'

        Check a package with src layout:

        >>> # For a package with src/mypackage/__init__.py
        >>> # result.status == CheckStatus.OK
        >>> # result.details contains "Using src layout"
    """

    name = "structure"
    description = "Validate package directory structure"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check package directory structure.

        Determines the layout type (src or flat) and validates that
        the package directories are properly formed with ``__init__.py``
        files.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if valid src or flat layout found
            - WARNING status if packages missing ``__init__.py``
            - ERROR status if no package or module found
        """
        details: list[str] = []

        # Check for src layout
        src_path = package_path / "src"
        if src_path.is_dir():
            return self._check_src_layout(package_path, src_path, details)

        # Check for flat layout
        return self._check_flat_layout(package_path, details)

    def _check_src_layout(
        self,
        package_path: Path,
        src_path: Path,
        details: list[str],
    ) -> CheckResult:
        """Validate src layout structure."""
        details.append("Using src layout")

        # Find package directories in src/
        package_dirs = [
            d
            for d in src_path.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
        ]

        if not package_dirs:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="No package directory found in src/",
                details=details,
            )

        # Check for __init__.py in package directories
        missing_init: list[str] = []
        for pkg_dir in package_dirs:
            init_path = pkg_dir / "__init__.py"
            if not init_path.exists():
                missing_init.append(pkg_dir.name)
            else:
                details.append(f"Found package: {pkg_dir.name}")

        if missing_init:
            details.append(f"Missing __init__.py in: {', '.join(missing_init)}")
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="Some packages missing __init__.py",
                details=details,
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Valid src layout structure",
            details=details,
        )

    def _check_flat_layout(
        self,
        package_path: Path,
        details: list[str],
    ) -> CheckResult:
        """Validate flat layout structure."""
        details.append("Using flat layout")

        # Look for package directories (not src/)
        package_dirs = [
            d
            for d in package_path.iterdir()
            if d.is_dir()
            and not d.name.startswith((".", "_"))
            and d.name not in NON_PACKAGE_DIRS
            and (d / "__init__.py").exists()
        ]

        if not package_dirs:
            # Check if there are any .py files at root (single-file module)
            py_files = list(package_path.glob("*.py"))
            py_files = [
                f for f in py_files if f.name not in ("setup.py", "conftest.py")
            ]

            if py_files:
                details.append(
                    f"Found single-file modules: {', '.join(f.name for f in py_files)}"
                )
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.OK,
                    message="Valid single-file module structure",
                    details=details,
                )

            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="No package or module found",
                details=details + ["Consider using src/ layout or adding __init__.py"],
            )

        for pkg_dir in package_dirs:
            details.append(f"Found package: {pkg_dir.name}")

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Valid flat layout structure",
            details=details,
        )
