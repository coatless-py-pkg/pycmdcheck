"""Cached pyproject.toml reader utility.

Provides a single, cached entry point for reading ``pyproject.toml`` files.
This replaces the duplicated TOML-parsing logic that previously lived in
``config.py``, ``metadata.py``, and ``structure.py``.  The
:func:`read_pyproject` function is decorated with :func:`functools.lru_cache`
so each file is read from disk at most once per ``package_path`` per run.

Examples:
    >>> from pathlib import Path
    >>> from pycmdcheck.pyproject_reader import read_pyproject, get_project_table
    >>> data = read_pyproject(Path("."))
    >>> project = get_project_table(Path("."))
    >>> project.get("name")
    'pycmdcheck'
"""

import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


@lru_cache(maxsize=8)
def read_pyproject(package_path: Path) -> dict[str, Any] | None:
    """Read and parse ``pyproject.toml`` from *package_path*.

    The result is cached (by ``package_path``) so repeated calls for the
    same path return the identical dict object without re-reading the file.

    Args:
        package_path: Directory that contains ``pyproject.toml``.

    Returns:
        Parsed dictionary, or ``None`` if the file does not exist.

    Raises:
        tomllib.TOMLDecodeError: If the file exists but contains invalid TOML.
    """
    pyproject_path = package_path / "pyproject.toml"

    if not pyproject_path.exists():
        return None

    with open(pyproject_path, "rb") as f:
        return tomllib.load(f)


def get_project_table(package_path: Path) -> dict[str, Any]:
    """Return the ``[project]`` table from ``pyproject.toml``.

    Args:
        package_path: Directory that contains ``pyproject.toml``.

    Returns:
        The ``[project]`` table as a dict, or an empty dict if the file
        is missing or the table is absent.
    """
    data = read_pyproject(package_path)
    if data is None:
        return {}
    return data.get("project", {})


def get_tool_table(package_path: Path, tool_name: str) -> dict[str, Any]:
    """Return the ``[tool.<tool_name>]`` table from ``pyproject.toml``.

    Args:
        package_path: Directory that contains ``pyproject.toml``.
        tool_name: Name of the tool whose configuration to retrieve
            (e.g. ``"pycmdcheck"``, ``"ruff"``).

    Returns:
        The ``[tool.<tool_name>]`` table as a dict, or an empty dict if
        the file is missing or the table is absent.
    """
    data = read_pyproject(package_path)
    if data is None:
        return {}
    return data.get("tool", {}).get(tool_name, {})


def clear_cache() -> None:
    """Clear the :func:`read_pyproject` LRU cache.

    Call this between test runs or when the underlying ``pyproject.toml``
    may have changed on disk.
    """
    read_pyproject.cache_clear()
