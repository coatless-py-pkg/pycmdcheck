"""Package structure validation check.

This module provides the StructureCheck class which validates that a
Python package follows standard directory layouts (src layout or flat layout).
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.package_layout import NON_PACKAGE_DIRS
from pycmdcheck.pyproject_reader import read_pyproject
from pycmdcheck.results import CheckResult, CheckStatus

# Build backends that produce native-extension packages which may have no
# Python source under src/ (or have it under a different directory).
NATIVE_BUILD_BACKENDS = (
    "maturin",
    "scikit_build",
    "setuptools_rust",
    "mesonpy",
    "meson_python",
)


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

        # Find candidate package directories in src/
        package_dirs = [
            d
            for d in src_path.iterdir()
            if d.is_dir() and not d.name.startswith((".", "_"))
        ]

        if not package_dirs:
            return self._handle_no_src_package(package_path, src_path, details)

        # Classify each candidate: regular package, PEP 420 namespace package,
        # or a directory genuinely missing __init__.py.
        missing_init: list[str] = []
        for pkg_dir in package_dirs:
            if (pkg_dir / "__init__.py").exists():
                details.append(f"Found package: {pkg_dir.name}")
            elif self._is_namespace_package(pkg_dir):
                details.append(f"Found namespace package: {pkg_dir.name}")
            else:
                missing_init.append(pkg_dir.name)

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

    def _handle_no_src_package(
        self,
        package_path: Path,
        src_path: Path,
        details: list[str],
    ) -> CheckResult:
        """Handle a ``src/`` directory with no candidate package subdirectory.

        Covers single-module src layouts (``src/foo.py``) and native-extension
        packages (maturin/scikit-build/setuptools-rust) whose ``src/`` holds no
        Python at all — neither should be a hard ERROR.
        """
        # Single-module src layout: src/foo.py
        modules = [f for f in sorted(src_path.glob("*.py")) if f.name != "__init__.py"]
        if modules:
            details.append(
                f"Found single-file modules: {', '.join(m.name for m in modules)}"
            )
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Valid single-file module structure",
                details=details,
            )

        # Native-extension package (maturin/scikit-build/setuptools-rust): the
        # Python lives elsewhere (or there is none), so a missing Python package
        # under src/ is expected, not an error.
        if self._is_native_backend(package_path):
            details.append(
                "No Python package under src/ (native-extension build backend detected)"
            )
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No Python package in src/ (native-extension layout)",
                details=details,
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="No package directory found in src/",
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

            # PEP 420 namespace package: a top-level dir without __init__.py
            # that nonetheless contains a nested package/module.
            namespace_dirs = [
                d
                for d in package_path.iterdir()
                if d.is_dir()
                and not d.name.startswith((".", "_"))
                and d.name not in NON_PACKAGE_DIRS
                and self._is_namespace_package(d)
            ]
            if namespace_dirs:
                details.append(
                    "Found namespace package(s): "
                    + ", ".join(d.name for d in namespace_dirs)
                )
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.OK,
                    message="Valid namespace package structure",
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

    @staticmethod
    def _is_namespace_package(directory: Path) -> bool:
        """Return whether *directory* is a PEP 420 implicit namespace package.

        A namespace package has no ``__init__.py`` of its own but contains a
        nested subpackage (a directory with ``__init__.py``) somewhere beneath
        it. This distinguishes it from a regular package that simply forgot its
        ``__init__.py``.
        """
        if (directory / "__init__.py").exists():
            return False
        for nested in directory.rglob("__init__.py"):
            if nested.parent != directory:
                return True
        return False

    @staticmethod
    def _is_native_backend(package_path: Path) -> bool:
        """Return whether pyproject.toml declares a native-extension backend."""
        data = read_pyproject(package_path)
        if not data:
            return False
        backend = data.get("build-system", {}).get("build-backend", "")
        if not isinstance(backend, str):
            return False
        return any(native in backend for native in NATIVE_BUILD_BACKENDS)
