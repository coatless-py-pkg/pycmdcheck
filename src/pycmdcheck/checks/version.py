"""Version consistency check."""

import ast
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.pyproject_reader import get_effective_project_table, read_pyproject
from pycmdcheck.results import CheckResult, CheckStatus


class VersionCheck(BaseCheck):
    """Check that __version__ in code matches pyproject.toml version."""

    name = "version"
    description = "Verify version consistency"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Compare __version__ in code with pyproject.toml version."""
        # Distinguish "no pyproject.toml" from "pyproject present but no
        # [project] table" (e.g. legacy Poetry / setup.py metadata).
        if read_pyproject(package_path) is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No pyproject.toml found",
            )

        project = get_effective_project_table(package_path)
        if not project:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No [project] table (legacy/setup.py metadata)",
                details=["Version consistency not checked"],
            )

        # Check for dynamic version
        dynamic = project.get("dynamic", [])
        if "version" in dynamic:
            return CheckResult(
                name=self.name,
                status=CheckStatus.SKIPPED,
                message="Version is dynamic (set at build time)",
            )

        pyproject_version = project.get("version")
        if not pyproject_version:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No version in pyproject.toml [project] table",
            )

        # Find __version__ in code (package __init__.py or single-module file)
        raw_name = project.get("name")
        fs_name = raw_name.replace("-", "_") if raw_name else None
        layout = PackageLayout(package_path, package_name=fs_name)
        version_source = self._find_version_source(layout, fs_name)
        if version_source is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No __init__.py or module found to check __version__",
            )

        code_version = self._extract_version(version_source)
        if code_version is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No __version__ defined in code",
                details=[
                    f"pyproject.toml version: {pyproject_version}",
                    f'Consider adding __version__ = "{pyproject_version}"'
                    f" to {version_source.name}",
                ],
            )

        if code_version == self._DYNAMIC_VERSION:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Version set dynamically (e.g. importlib.metadata)",
                details=[
                    f"pyproject.toml version: {pyproject_version}",
                    "__version__ is set via function call at runtime",
                ],
            )

        if code_version == pyproject_version:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message=f"Version {pyproject_version} is consistent",
                details=[
                    f"pyproject.toml: {pyproject_version}",
                    f"__version__: {code_version}",
                ],
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Version mismatch",
            details=[
                f"pyproject.toml: {pyproject_version}",
                f"__version__: {code_version}",
            ],
        )

    # Sentinel returned when __version__ is set via a function call
    _DYNAMIC_VERSION = "<dynamic>"

    def _find_version_source(
        self, layout: PackageLayout, fs_name: str | None
    ) -> Path | None:
        """Return the file that should declare ``__version__``.

        Prefers the primary package's ``__init__.py``; falls back to a
        single-module package (``foo.py`` at the import root) when there is no
        package directory.
        """
        pkg_dir = layout.primary_package
        if pkg_dir is not None:
            init_file = pkg_dir / "__init__.py"
            if init_file.exists():
                return init_file

        modules = layout.top_level_modules()
        if fs_name:
            for module in modules:
                if module.stem == fs_name:
                    return module
        if len(modules) == 1:
            return modules[0]
        return None

    def _extract_version(self, init_file: Path) -> str | None:
        """Extract __version__ from a Python file using AST.

        Returns the literal version string, the sentinel ``_DYNAMIC_VERSION``
        if the version is set via a function call (e.g.
        ``importlib.metadata.version()``), or ``None`` if no ``__version__``
        assignment is found.
        """
        tree = parse_file(init_file)
        if tree is None:
            return None

        for node in ast.walk(tree):
            # ``from ._version import __version__`` re-exports the version from
            # another module — treat it like a runtime/dynamic source rather
            # than "missing", since it cannot be compared statically here.
            if isinstance(node, ast.ImportFrom):
                for alias in node.names:
                    if "__version__" in (alias.name, alias.asname):
                        return self._DYNAMIC_VERSION
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__version__":
                        if isinstance(node.value, ast.Constant) and isinstance(
                            node.value.value, str
                        ):
                            return node.value.value
                        if isinstance(node.value, ast.Call):
                            return self._DYNAMIC_VERSION
            elif isinstance(node, ast.AnnAssign):
                if (
                    isinstance(node.target, ast.Name)
                    and node.target.id == "__version__"
                ):
                    if (
                        node.value
                        and isinstance(node.value, ast.Constant)
                        and isinstance(node.value.value, str)
                    ):
                        return node.value.value
                    if node.value and isinstance(node.value, ast.Call):
                        return self._DYNAMIC_VERSION

        return None
