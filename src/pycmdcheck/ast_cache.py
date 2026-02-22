"""Thread-safe AST parse cache for pycmdcheck.

Multiple checks parse the same Python files independently. This module
provides a shared cache so each file is parsed at most once per run.
"""

from __future__ import annotations

import ast
import logging
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_cache: dict[Path, ast.Module | None] = {}
_lock = threading.Lock()


def parse_file(path: Path) -> ast.Module | None:
    """Parse a Python file, returning cached result if available.

    Returns None if the file cannot be read or parsed.
    """
    resolved = path.resolve()
    with _lock:
        if resolved in _cache:
            return _cache[resolved]

    try:
        with open(resolved, encoding="utf-8") as f:
            tree = ast.parse(f.read(), filename=str(resolved))
    except (SyntaxError, UnicodeDecodeError, OSError) as exc:
        logger.debug("Failed to parse %s: %s", resolved, exc)
        tree = None

    with _lock:
        _cache[resolved] = tree
    return tree


def clear_cache() -> None:
    """Clear the AST cache (call between test runs)."""
    with _lock:
        _cache.clear()
