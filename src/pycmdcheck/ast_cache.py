"""Thread-safe AST parse cache for pycmdcheck.

Several checks parse the same Python files. This module keeps one syntax tree
per file so the work is shared. A tree stays valid until its file changes on
disk (see :class:`pycmdcheck.fileio.FileCache`). Trees are large and the cache
has no size limit, so the runner calls
:func:`pycmdcheck.fileio.clear_all_caches` at the end of each run.

Files are read as bytes, so a byte order mark or a PEP 263 encoding
declaration is honoured the way the interpreter honours it. A file that is not
a regular file, is larger than ``MAX_SOURCE_BYTES``, or does not parse on the
running interpreter gives ``None``. Nothing a file contains makes
:func:`parse_file` raise. The size cap is the only limit on what parsing one
file can cost in time or memory.

A tree can be nested very deeply, because ``a + b + c + ...`` nests once per
term. Walk trees with :func:`ast.walk` or an explicit stack, not with a
recursive visitor.
"""

from __future__ import annotations

import ast
import logging
import warnings
from collections.abc import Iterable
from pathlib import Path
from typing import NamedTuple

from pycmdcheck.constants import MAX_SOURCE_BYTES
from pycmdcheck.fileio import FileCache, FileRead, display_path, plain_text

logger = logging.getLogger(__name__)


class _Parsed(NamedTuple):
    """A syntax tree, or the reason there is none."""

    tree: ast.Module | None
    reason: str | None


def _parse(path: Path, read: FileRead) -> _Parsed:
    """Build the cache value for one file. Never raises."""
    if read.problem is not None:
        logger.debug("Skipping %s: %s", path, read.problem)
        return _Parsed(None, plain_text(read.problem))
    try:
        # The parser warns about the code it reads (an invalid escape
        # sequence, for example). Those warnings are about the package being
        # checked, and under ``-W error`` they would fail a valid file. The
        # cache builds one value at a time, so the filters are not changed
        # from two threads at once.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return _Parsed(ast.parse(read.data, filename=str(path)), None)
    except SyntaxError as exc:
        where = f" on line {exc.lineno}" if exc.lineno else ""
        reason = f"syntax error{where}: {exc.msg}"
    except ValueError as exc:
        # Python 3.11 reports a null byte this way.
        reason = str(exc)
    except RecursionError:
        reason = "nested too deeply to parse"
    except MemoryError:
        reason = "too large to parse"
    # The reason can quote the file, so it is cleaned and shortened.
    reason = plain_text(reason)
    logger.debug("Failed to parse %s: %s", path, reason)
    return _Parsed(None, reason)


_cache: FileCache[_Parsed] = FileCache(_parse, MAX_SOURCE_BYTES)


def parse_file(path: Path, *, cache: bool = True) -> ast.Module | None:
    """Parse a Python file, returning the cached tree if the file is unchanged.

    Args:
        path: The ``.py`` file to parse.
        cache: When ``False`` a tree parsed by this call is not kept, which
            suits a check that scans many files once. A tree that is already
            cached is still returned.

    Returns:
        The module's syntax tree, or ``None`` if the file is missing, is not
        a regular file, is larger than ``MAX_SOURCE_BYTES``, or cannot be
        parsed by the running interpreter.
    """
    return _cache.get(path, store=cache).tree


def unparsed_files_detail(
    paths: Iterable[Path], root: Path, limit: int = 5
) -> str | None:
    """Build one detail line naming files that could not be parsed.

    Checks that scan source add this line so a result drawn from part of the
    code says so.

    Args:
        paths: Files for which :func:`parse_file` returned ``None``.
        root: Project directory, used to shorten the paths.
        limit: Largest number of files to name.

    Returns:
        A line such as ``"Could not parse 2 file(s), skipped: src/a.py
        (syntax error on line 1: invalid syntax), src/b.py (larger than
        2 MiB)"``, or ``None`` when *paths* is empty. Names and reasons are
        cleaned and shortened, so the line has no line break.
    """
    skipped = sorted(set(paths))
    if not skipped:
        return None
    shown: list[str] = []
    for path in skipped[:limit]:
        reason = _cache.get(path).reason
        name = display_path(path, root)
        shown.append(f"{name} ({reason})" if reason else name)
    line = f"Could not parse {len(skipped)} file(s), skipped: {', '.join(shown)}"
    if len(skipped) > limit:
        line += f" (+{len(skipped) - limit} more)"
    return line


def clear_cache() -> None:
    """Clear the AST cache and release the parsed trees."""
    _cache.clear()
