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

import re
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
    project = data.get("project", {})
    return project if isinstance(project, dict) else {}


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
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        return {}
    table = tool.get(tool_name, {})
    return table if isinstance(table, dict) else {}


_AUTHOR_RE = re.compile(r"^\s*(?P<name>.*?)\s*(?:<(?P<email>[^>]+)>)?\s*$")


def _parse_poetry_authors(authors: Any) -> list[dict[str, str]]:
    """Convert Poetry ``authors`` (``"Name <email>"`` strings) to PEP 621 form."""
    if not isinstance(authors, list):
        return []
    result: list[dict[str, str]] = []
    for entry in authors:
        if not isinstance(entry, str):
            continue
        m = _AUTHOR_RE.match(entry)
        if not m:
            continue
        person: dict[str, str] = {}
        name = (m.group("name") or "").strip()
        email = (m.group("email") or "").strip()
        if name:
            person["name"] = name
        if email:
            person["email"] = email
        if person:
            result.append(person)
    return result


def poetry_python_to_pep440(constraint: str) -> str | None:
    """Translate a Poetry ``python`` constraint to a PEP 440 specifier.

    Poetry uses caret (``^3.9``) and tilde (``~3.9``) operators that PEP 440
    does not. This produces a best-effort PEP 440 specifier preserving the
    lower bound (which is what the EOL check cares about). PEP 440 operators
    (``>=``, ``<``, ``==``, ``!=``, comma-separated) and bare versions are
    handled too. Returns ``None`` for an unusable value.
    """
    if not isinstance(constraint, str):
        return None
    text = constraint.strip()
    if not text:
        return None
    # Caret/tilde: keep the lower bound (e.g. ^3.9 / ~3.9 -> >=3.9).
    m = re.match(r"^[\^~]\s*(\d+(?:\.\d+)*)$", text)
    if m:
        return f">={m.group(1)}"
    # Already a PEP 440 operator (or comma-separated set): pass through.
    if re.search(r"[<>=!~]", text):
        return text
    # Bare version (e.g. "3.9"): treat as a lower bound.
    if re.match(r"^\d+(?:\.\d+)*$", text):
        return f">={text}"
    return None


def _synthesize_from_poetry(poetry: dict[str, Any]) -> dict[str, Any]:
    """Build a PEP 621-shaped ``[project]`` dict from a ``[tool.poetry]`` table."""
    project: dict[str, Any] = {}

    for key in ("name", "version", "description", "readme", "license", "keywords"):
        if key in poetry:
            project[key] = poetry[key]

    if isinstance(poetry.get("classifiers"), list):
        project["classifiers"] = poetry["classifiers"]

    authors = _parse_poetry_authors(poetry.get("authors"))
    if authors:
        project["authors"] = authors
    maintainers = _parse_poetry_authors(poetry.get("maintainers"))
    if maintainers:
        project["maintainers"] = maintainers

    # requires-python lives under [tool.poetry.dependencies].python in Poetry.
    deps = poetry.get("dependencies", {})
    if isinstance(deps, dict):
        py = deps.get("python")
        if isinstance(py, str):
            translated = poetry_python_to_pep440(py)
            if translated:
                project["requires-python"] = translated
        dep_names = [name for name in deps if name != "python"]
        if dep_names:
            project["dependencies"] = dep_names

    # urls: dedicated keys plus an optional [tool.poetry.urls] table.
    urls: dict[str, str] = {}
    for label, key in (
        ("Homepage", "homepage"),
        ("Repository", "repository"),
        ("Documentation", "documentation"),
    ):
        value = poetry.get(key)
        if isinstance(value, str):
            urls[label] = value
    extra_urls = poetry.get("urls")
    if isinstance(extra_urls, dict):
        for label, value in extra_urls.items():
            if isinstance(value, str):
                urls[label] = value
    if urls:
        project["urls"] = urls

    return project


def get_effective_project_table(package_path: Path) -> dict[str, Any]:
    """Return a PEP 621-shaped ``[project]`` table, accounting for legacy Poetry.

    If a PEP 621 ``[project]`` table is present (even minimally), it is returned
    unchanged. Otherwise, if a legacy ``[tool.poetry]`` table is present (the
    metadata format used by Poetry projects before Poetry 2.0), its fields are
    normalized into a PEP 621-shaped dict so the checks can treat both layouts
    uniformly. Returns an empty dict if neither is present.
    """
    data = read_pyproject(package_path)
    if data is None:
        return {}
    project = data.get("project")
    if isinstance(project, dict) and project:
        return project
    tool = data.get("tool", {})
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    if isinstance(poetry, dict) and poetry:
        return _synthesize_from_poetry(poetry)
    return {}


def clear_cache() -> None:
    """Clear the :func:`read_pyproject` LRU cache.

    Call this between test runs or when the underlying ``pyproject.toml``
    may have changed on disk.
    """
    read_pyproject.cache_clear()
