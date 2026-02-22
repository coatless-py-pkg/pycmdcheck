"""Dependency audit check.

Cross-references declared dependencies in pyproject.toml with actual
imports found in source files.
"""

import ast
import importlib.metadata
import re
import sys
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.pyproject_reader import get_project_table
from pycmdcheck.results import CheckResult, CheckStatus

# Common PyPI package name -> import name mappings
PYPI_TO_IMPORT: dict[str, str] = {
    "pillow": "PIL",
    "scikit-learn": "sklearn",
    "scikit-image": "skimage",
    "python-dateutil": "dateutil",
    "pyyaml": "yaml",
    "beautifulsoup4": "bs4",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "typing-extensions": "typing_extensions",
    "pyjwt": "jwt",
    "pyzmq": "zmq",
    "python-dotenv": "dotenv",
    "attrs": "attr",
    "python-multipart": "multipart",
    "msgpack-python": "msgpack",
    "ruamel-yaml": "ruamel",
    "google-cloud-storage": "google",
    "protobuf": "google",
    "python-magic": "magic",
    "charset-normalizer": "charset_normalizer",
    "importlib-metadata": "importlib_metadata",
    "importlib-resources": "importlib_resources",
    "setuptools": "setuptools",
    "pycryptodome": "Crypto",
    "pymysql": "pymysql",
    "psycopg2-binary": "psycopg2",
    "psycopg2": "psycopg2",
}


def _resolve_import_name(pypi_name: str) -> str:
    """Resolve a PyPI package name to its Python import name.

    Uses importlib.metadata to check installed package metadata first,
    falls back to the static PYPI_TO_IMPORT mapping, then to simple
    name normalization.
    """
    normalized = _normalize_pypi_name(pypi_name)

    # Try importlib.metadata first (most accurate for installed packages)
    try:
        dist = importlib.metadata.distribution(pypi_name)
        # Check top_level.txt
        try:
            top_level = dist.read_text("top_level.txt")
        except (FileNotFoundError, OSError):
            top_level = None
        if top_level:
            names = [n.strip() for n in top_level.strip().splitlines() if n.strip()]
            if names:
                # Prefer public names (not starting with _)
                public = [n for n in names if not n.startswith("_")]
                return public[0] if public else names[0]
        # Check packages from RECORD
        if dist.files:
            for f in dist.files:
                parts = f.parts
                if len(parts) > 1 and parts[-1] == "__init__.py":
                    return str(parts[0])
                # Single-file module (e.g. "foo.py" at top level)
                if len(parts) == 1 and str(parts[0]).endswith(".py"):
                    name = str(parts[0]).removesuffix(".py")
                    if not name.startswith("_"):
                        return name
    except importlib.metadata.PackageNotFoundError:
        pass

    # Fallback to static mapping
    if normalized in PYPI_TO_IMPORT:
        return PYPI_TO_IMPORT[normalized]

    # Default normalization
    return normalized.replace("-", "_")


class DependenciesCheck(BaseCheck):
    """Check that declared dependencies match actual imports."""

    name = "dependencies"
    description = "Audit declared vs. actual dependencies"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Cross-reference pyproject.toml dependencies with imports."""
        project = get_project_table(package_path)

        if not project:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No pyproject.toml found, skipping dependency audit",
            )

        raw_deps = project.get("dependencies", [])
        if not raw_deps:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No dependencies declared",
                details=["Package has no declared dependencies"],
            )

        # Parse declared dependency names
        declared = {}  # normalized_pypi_name -> import_name
        for dep in raw_deps:
            pypi_name = _strip_version_specifier(dep)
            normalized = _normalize_pypi_name(pypi_name)
            import_name = _resolve_import_name(pypi_name)
            declared[normalized] = import_name

        # Extract all imports from source
        layout = PackageLayout(package_path)
        source_imports = self._collect_imports(layout)

        details: list[str] = []

        # Check for undeclared imports
        declared_import_names = set(declared.values())
        stdlib = sys.stdlib_module_names
        local_packages = layout.local_package_names()

        undeclared = []
        for imp in source_imports:
            if imp in stdlib or imp in local_packages or imp in declared_import_names:
                continue
            undeclared.append(imp)

        # Check for unused declared dependencies
        unused = []
        for pypi_name, import_name in declared.items():
            if import_name not in source_imports:
                unused.append(pypi_name)

        if undeclared:
            details.append(f"Undeclared imports: {', '.join(sorted(undeclared)[:10])}")
        if unused:
            details.append(f"Unused dependencies: {', '.join(sorted(unused)[:10])}")

        if undeclared:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message=f"Found {len(undeclared)} undeclared import(s)",
                details=details,
            )

        if unused:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message=f"Found {len(unused)} unused declared dependency(ies)",
                details=details,
            )

        details.append(f"All {len(declared)} dependencies verified")
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="All dependencies match imports",
            details=details,
        )

    def _collect_imports(self, layout: PackageLayout) -> set[str]:
        """Extract all top-level import names from source files."""
        imports: set[str] = set()

        py_files = layout.python_files()

        for py_file in py_files:
            tree = parse_file(py_file)
            if tree is None:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.add(alias.name.split(".")[0])
                elif isinstance(node, ast.ImportFrom):
                    if node.module and node.level == 0:
                        imports.add(node.module.split(".")[0])

        return imports


def _strip_version_specifier(dep: str) -> str:
    """Strip version specifiers and extras from a PEP 508 dependency string."""
    dep = dep.split(";")[0].strip()  # Remove environment markers
    dep = re.split(r"[><=!~\[]", dep)[0].strip()  # Remove version/extras
    return dep


def _normalize_pypi_name(name: str) -> str:
    """Normalize a PyPI package name per PEP 503."""
    return re.sub(r"[-_.]+", "-", name).lower()
