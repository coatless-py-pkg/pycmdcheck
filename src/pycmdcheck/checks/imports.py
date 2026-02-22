"""Import validation check.

This module provides the ImportsCheck class which validates that all
imports in the package can be resolved, helping catch missing dependencies.
"""

import ast
import importlib.util
import sys
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.results import CheckResult, CheckStatus


class ImportsCheck(BaseCheck):
    """Check that all imports in the package can be resolved.

    Scans all Python files in the package and attempts to resolve
    each import statement. Reports any imports that cannot be found,
    which may indicate missing dependencies.

    Attributes:
        name: The check identifier ("imports").
        description: Human-readable description of this check.

    Note:
        This check excludes standard library modules and local package
        imports from validation. It only flags third-party imports that
        cannot be resolved in the current environment. Only top-level
        module names (before the first dot) are validated; for example,
        ``from package.sub import item`` only checks that ``package``
        is resolvable. Relative imports are ignored entirely.

    Examples:
        Run the imports check:

        >>> from pathlib import Path
        >>> check = ImportsCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'imports'

        The check reports unresolvable imports:

        >>> # If a file imports 'nonexistent_package'
        >>> # result.status == CheckStatus.WARNING
        >>> # "Cannot import 'nonexistent_package'" in result.details
    """

    name = "imports"
    description = "Validate package imports"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check imports in all Python files.

        Parses each Python file to extract import statements, then
        attempts to import each module to verify it exists.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if all imports can be resolved
            - NOTE status if no Python files found
            - WARNING status if some imports cannot be resolved
        """
        details: list[str] = []

        # Find all Python files
        layout = PackageLayout(package_path)
        py_files = layout.python_files()

        if not py_files:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No Python files found",
                details=details,
            )

        details.append(f"Checking {len(py_files)} Python files")

        # Collect all imports
        all_imports: list[tuple[Path, str]] = []

        for py_file in py_files:
            imports = self._extract_imports(py_file)
            for imp in imports:
                all_imports.append((py_file, imp))

        # Check for import issues
        issues = self._check_imports(all_imports, package_path, layout)

        if not issues:
            details.append(f"All {len(all_imports)} imports validated")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="All imports are valid",
                details=details,
            )

        for issue in issues[:10]:
            details.append(issue)

        if len(issues) > 10:
            details.append(f"... and {len(issues) - 10} more issues")

        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=f"Found {len(issues)} import issue(s)",
            details=details,
        )

    def _extract_imports(self, py_file: Path) -> list[str]:
        """Extract import names from a Python file."""
        tree = parse_file(py_file)
        if tree is None:
            return []
        imports: list[str] = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name.split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    imports.append(node.module.split(".")[0])

        return imports

    def _check_imports(
        self,
        imports: list[tuple[Path, str]],
        package_path: Path,
        layout: PackageLayout,
    ) -> list[str]:
        """Check if imports can be resolved."""
        issues: list[str] = []

        # Get stdlib modules
        stdlib_modules = self._get_stdlib_modules()

        # Get package name(s) from src/ or root
        local_packages = layout.local_package_names()

        # Track which modules we've already checked
        checked: set[str] = set()
        unresolvable: set[str] = set()

        for py_file, module_name in imports:
            if module_name in checked:
                continue
            checked.add(module_name)

            # Skip stdlib
            if module_name in stdlib_modules:
                continue

            # Skip local packages
            if module_name in local_packages:
                continue

            # Probe without executing the module
            try:
                spec = importlib.util.find_spec(module_name)
            except (ModuleNotFoundError, ValueError):
                spec = None

            if spec is None:
                if module_name not in unresolvable:
                    unresolvable.add(module_name)
                    issues.append(
                        f"Cannot import '{module_name}' (used in {py_file.name})"
                    )

        return issues

    def _get_stdlib_modules(self) -> frozenset[str]:
        """Get set of standard library module names."""
        return sys.stdlib_module_names
