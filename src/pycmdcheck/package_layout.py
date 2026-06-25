"""Shared utility for discovering Python package layout and files.

This module provides the :class:`PackageLayout` class, which consolidates
all package/directory discovery logic into a single, reusable component.
It replaces duplicated ``_find_package_dir``, ``_get_local_packages``, and
hardcoded exclusion sets that were previously scattered across check modules.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# ── Shared exclusion sets ─────────────────────────────────────────────

EXCLUDED_DIRS: frozenset[str] = frozenset(
    {
        "venv",
        ".venv",
        "env",
        ".env",
        "build",
        "dist",
        ".git",
        "__pycache__",
        ".tox",
        ".nox",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "node_modules",
        ".eggs",
    }
)
"""Directories that should always be skipped when walking a project tree."""

NON_PACKAGE_DIRS: frozenset[str] = frozenset(
    {
        "tests",
        "test",
        "docs",
        "doc",
        "build",
        "dist",
        "venv",
        ".venv",
    }
)
"""Top-level directories that are never real packages in a flat layout."""

NON_MODULE_FILES: frozenset[str] = frozenset(
    {
        "setup.py",
        "conftest.py",
        "noxfile.py",
        "tasks.py",
        "manage.py",
        "versioneer.py",
        "__init__.py",
        "__main__.py",
    }
)
"""Top-level ``.py`` files that are tooling/scripts, not the package module."""


class PackageLayout:
    """Discover and expose the layout of a Python project.

    Supports both *src/* layout and flat layout.  Filesystem scanning is
    deferred until the first property access (lazy discovery).

    Args:
        project_root: Path to the project root (the directory containing
            ``pyproject.toml`` or equivalent).
        package_name: Optional hint for the primary package name.  When
            given, it is used to select :attr:`primary_package` from the
            discovered packages.
    """

    def __init__(
        self,
        project_root: Path,
        *,
        package_name: str | None = None,
    ) -> None:
        self._root = project_root
        self._package_name = package_name

        # Lazily populated by ``_discover()``
        self._is_src_layout: bool | None = None
        self._package_dirs: list[Path] | None = None

    # ── Public properties ─────────────────────────────────────────────

    @property
    def is_src_layout(self) -> bool:
        """Return ``True`` if the project uses a ``src/`` layout."""
        if self._is_src_layout is None:
            self._discover()
        assert self._is_src_layout is not None
        return self._is_src_layout

    @property
    def package_dirs(self) -> list[Path]:
        """Return all package directories that contain ``__init__.py``."""
        if self._package_dirs is None:
            self._discover()
        assert self._package_dirs is not None
        return list(self._package_dirs)

    @property
    def primary_package(self) -> Path | None:
        """Return the main package directory.

        If *package_name* was given at construction time and a matching
        package exists, that package is returned.  Otherwise the first
        discovered package is returned (or ``None`` if no packages exist).
        """
        dirs = self.package_dirs
        if not dirs:
            return None

        if self._package_name:
            for d in dirs:
                if d.name == self._package_name:
                    return d

        return dirs[0]

    # ── Public methods ────────────────────────────────────────────────

    def python_files(self, *, include_tests: bool = False) -> list[Path]:
        """Return all ``.py`` files inside discovered packages.

        Args:
            include_tests: When ``False`` (the default), files under
                directories named ``tests`` or ``test`` are excluded.

        Returns:
            Sorted list of ``.py`` file paths.
        """
        result: list[Path] = []
        for pkg_dir in self.package_dirs:
            for py_file in pkg_dir.rglob("*.py"):
                parts = set(py_file.relative_to(pkg_dir).parts)
                # Always skip globally excluded dirs
                if parts & EXCLUDED_DIRS:
                    continue
                if not include_tests and parts & {"tests", "test"}:
                    continue
                result.append(py_file)

        # Also filter based on the full path parts (catches excluded dirs
        # that sit *above* the package dir, e.g. a .venv ancestor).
        result = [
            f for f in result if not any(part in EXCLUDED_DIRS for part in f.parts)
        ]
        return sorted(result)

    def local_package_names(self) -> set[str]:
        """Return the names of all discovered local packages."""
        return {d.name for d in self.package_dirs}

    @property
    def import_root(self) -> Path:
        """Return the directory imports resolve from (``src/`` or the root)."""
        if self._is_src_layout is None:
            self._discover()
        src_dir = self._root / "src"
        return src_dir if (self._is_src_layout and src_dir.is_dir()) else self._root

    def top_level_modules(self) -> list[Path]:
        """Return single-file top-level modules at the import root.

        Finds ``.py`` files directly under the import root (``src/`` for a
        src-layout, otherwise the project root) that represent the package
        itself — e.g. a single-module package ``foo.py`` — excluding common
        tooling scripts (``setup.py``, ``conftest.py``, …). Used to support
        single-module packages that have no package directory / ``__init__.py``.
        """
        base = self.import_root
        if not base.is_dir():
            return []
        modules: list[Path] = []
        for item in sorted(base.iterdir()):
            if (
                item.is_file()
                and item.suffix == ".py"
                and item.name not in NON_MODULE_FILES
                and not item.name.startswith(".")
            ):
                modules.append(item)
        return modules

    # ── Internal discovery ────────────────────────────────────────────

    def _discover(self) -> None:
        """Scan the filesystem and populate internal caches."""
        logger.debug("Discovering package layout under %s", self._root)

        dirs: list[Path] = []
        src_dir = self._root / "src"

        if src_dir.is_dir():
            self._is_src_layout = True
            logger.debug("Detected src/ layout")
            for item in sorted(src_dir.iterdir()):
                if (
                    item.is_dir()
                    and not item.name.startswith((".", "_"))
                    and (item / "__init__.py").exists()
                ):
                    logger.debug("  Found src-layout package: %s", item.name)
                    dirs.append(item)
        else:
            self._is_src_layout = False
            logger.debug("No src/ directory; checking flat layout")
            for item in sorted(self._root.iterdir()):
                if (
                    item.is_dir()
                    and item.name not in NON_PACKAGE_DIRS
                    and not item.name.startswith(".")
                    and (item / "__init__.py").exists()
                ):
                    logger.debug("  Found flat-layout package: %s", item.name)
                    dirs.append(item)

        self._package_dirs = dirs
        logger.debug(
            "Discovery complete: %d package(s) found",
            len(self._package_dirs),
        )
