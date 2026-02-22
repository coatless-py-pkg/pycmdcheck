"""Documentation check.

This module provides the DocsCheck class which verifies that a package
has proper documentation including a README file and optionally checks
for docstrings in the code.
"""

import ast
import logging
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_DOCSTRING_FILES, MIN_README_WORDS
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.results import CheckResult, CheckStatus

logger = logging.getLogger(__name__)


class DocsCheck(BaseCheck):
    """Check for documentation presence and quality.

    Verifies that the package has a README file and optionally checks
    that public modules, classes, and functions have docstrings.

    Attributes:
        name: The check identifier ("docs").
        description: Human-readable description of this check.
        README_FILENAMES: List of recognized README file names.

    Configuration Options:
        require_readme (bool): Whether README is required. Defaults to True.
        check_docstrings (bool): Whether to check for docstrings in code.
            Defaults to False.

    Examples:
        Run the docs check with defaults:

        >>> from pathlib import Path
        >>> check = DocsCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'docs'

        Enable docstring checking:

        >>> config = {"enabled": True, "check_docstrings": True}
        >>> result = check.run(Path("."), config)

        Make README optional:

        >>> config = {"enabled": True, "require_readme": False}
        >>> result = check.run(Path("."), config)
    """

    name = "docs"
    description = "Check documentation (README, docstrings)"

    README_FILENAMES = [
        "README.md",
        "README.rst",
        "README.txt",
        "README",
    ]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check documentation presence and quality.

        Looks for a README file and optionally scans code for missing
        docstrings on public items.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary with options:

                - require_readme (bool): Require README (default: True)
                - check_docstrings (bool): Check code docstrings (default: False)

        Returns:
            A CheckResult with:

            - OK status if all documentation requirements met
            - NOTE status if minor improvements suggested
            - WARNING status if README missing (when required)
        """
        details: list[str] = []
        issues: list[str] = []

        # Check for README
        require_readme = config.get("require_readme", True)
        readme_found = self._check_readme(package_path, details, issues, require_readme)

        # Check for docstrings (optional)
        check_docstrings = config.get("check_docstrings", False)
        if check_docstrings:
            self._check_docstrings(package_path, details, issues)

        # Determine status
        if issues and require_readme and not readme_found:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="Documentation incomplete",
                details=details + issues,
            )

        if issues:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="Some documentation improvements suggested",
                details=details + issues,
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Documentation present",
            details=details,
        )

    def _check_readme(
        self,
        package_path: Path,
        details: list[str],
        issues: list[str],
        require_readme: bool,
    ) -> bool:
        """Check for README file."""
        for filename in self.README_FILENAMES:
            readme_path = package_path / filename
            if readme_path.exists():
                details.append(f"Found README: {filename}")

                # Check README is not too short
                try:
                    content = readme_path.read_text(encoding="utf-8")
                    word_count = len(content.split())
                    if word_count < MIN_README_WORDS:
                        issues.append(
                            f"README is very short (< {MIN_README_WORDS} words)"
                        )
                    else:
                        details.append(f"README has {word_count} words")
                except (OSError, UnicodeDecodeError) as exc:
                    logger.debug("Could not read README %s: %s", readme_path, exc)

                return True

        if require_readme:
            issues.append("No README file found")
        return False

    def _check_docstrings(
        self,
        package_path: Path,
        details: list[str],
        issues: list[str],
    ) -> None:
        """Check for docstrings in public modules and functions."""
        # Find Python files
        layout = PackageLayout(package_path)
        py_files = layout.python_files()

        # Exclude test files
        py_files = [f for f in py_files if not f.name.startswith("test_")]

        missing_docstrings: list[str] = []

        for py_file in py_files[:MAX_DOCSTRING_FILES]:  # Limit to first N files
            try:
                missing = self._check_file_docstrings(py_file)
                missing_docstrings.extend(missing)
            except (OSError, UnicodeDecodeError) as exc:
                logger.debug("Could not parse %s: %s", py_file, exc)

        if missing_docstrings:
            issues.append(
                f"Missing docstrings in {len(missing_docstrings)} public items"
            )
            for missing_item in missing_docstrings[:5]:
                issues.append(f"  - {missing_item}")
            if len(missing_docstrings) > 5:
                issues.append(f"  ... and {len(missing_docstrings) - 5} more")
        else:
            details.append("All public items have docstrings")

    def _check_file_docstrings(self, py_file: Path) -> list[str]:
        """Check docstrings in a single Python file."""
        tree = parse_file(py_file)
        if tree is None:
            return []
        missing: list[str] = []

        # Check module docstring
        if not ast.get_docstring(tree):
            missing.append(f"{py_file.name}: module")

        for node in ast.walk(tree):
            # Check class docstrings
            if isinstance(node, ast.ClassDef):
                if not node.name.startswith("_"):
                    if not ast.get_docstring(node):
                        missing.append(f"{py_file.name}: class {node.name}")

            # Check function docstrings
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if not node.name.startswith("_"):
                    if not ast.get_docstring(node):
                        missing.append(f"{py_file.name}: function {node.name}")

        return missing
