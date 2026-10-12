"""Bounded, tolerant access to files in the repository being checked.

Repository content is untrusted. A file may be a link to a device, far larger
than expected, saved in an unexpected encoding, or named with different letter
case than the usual spelling. Checks read and find files through the helpers
here, so each of those cases gets the same answer everywhere. Symbolic links
are followed, because a package inside a larger repository links to the
license files above it.
"""

from __future__ import annotations

import codecs
import configparser
import fnmatch
import glob
import logging
import os
import re
import stat
import threading
import time
import weakref
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path
from typing import Any, Generic, NamedTuple, TypeVar

from pycmdcheck.constants import MAX_PROSE_BYTES

logger = logging.getLogger(__name__)

PROSE_EXTENSIONS: tuple[str, ...] = (".md", ".rst", ".txt", "")
"""Extensions tried, in order, for README-style files. ``""`` is a bare name."""

NON_PROSE_SUFFIXES: frozenset[str] = frozenset(
    # Source code and scripts, what editors and merge tools leave behind,
    # and pictures.
    ".py .pyc .pyo .pyi .pyx .pxd .sh .bash .bat .cmd .ps1 .js .ts .c .h .cpp "
    ".rs .go .java .rb .php "
    ".orig .bak .rej .swp .tmp .patch .diff "
    ".png .jpg .jpeg .gif .svg .webp .ico".split()
)
"""Final suffixes that are never a README, license or changelog."""

# Longest text plain_text() returns by default, longest path display_path()
# returns, and longest path or glob pattern accepted from pyproject.toml.
_MAX_TEXT_LENGTH = 200
_MAX_DISPLAY_PATH = 240
_MAX_DECLARED_LENGTH = 1024
# A file changed less than this long (in nanoseconds) before it was read may
# be rewritten without its size or modification time changing, because some
# filesystems record times in steps of milliseconds or seconds.
_YOUNG_NS = 2_000_000_000

_BOMS: tuple[tuple[bytes, str], ...] = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    # UTF-32 first, because its LE mark starts with the UTF-16 LE mark.
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)

_T = TypeVar("_T")


# ── Reading ───────────────────────────────────────────────────────────


def is_regular_file(path: Path) -> bool:
    """Return whether *path* is a regular file, following symlinks. Never raises."""
    try:
        return stat.S_ISREG(os.stat(path).st_mode)
    except (OSError, ValueError):
        return False


def os_error_text(exc: OSError) -> str:
    """Return a short reason for *exc* without the file path."""
    return exc.strerror or str(exc)


def read_bytes_capped(path: Path, max_bytes: int) -> tuple[bytes, bool]:
    """Read at most *max_bytes* from a regular file, and say if it holds more.

    Raises:
        OSError: If the path does not exist, is not a regular file (a
            directory, device, FIFO or socket), or cannot be read.
    """
    try:
        mode = os.stat(path).st_mode
    except ValueError as exc:  # a null byte in the path
        raise OSError(str(exc)) from None
    if not stat.S_ISREG(mode):
        raise OSError("not a regular file")
    with open(path, "rb") as handle:
        data = handle.read(max_bytes + 1)
    return data[:max_bytes], len(data) > max_bytes


def decode_text(data: bytes) -> str:
    """Decode file content to text without ever failing.

    A UTF-8, UTF-16 or UTF-32 byte order mark selects that encoding. Anything
    else is read as UTF-8, with undecodable bytes replaced by U+FFFD. Line
    endings become ``"\\n"``.
    """
    encoding = next((name for bom, name in _BOMS if data.startswith(bom)), "utf-8")
    text = data.decode(encoding, errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_capped(path: Path, max_bytes: int = MAX_PROSE_BYTES) -> str:
    """Read a prose file, cut off at *max_bytes*, whatever its encoding.

    Raises:
        OSError: As for :func:`read_bytes_capped`. Use :func:`os_error_text`
            for the reason.
    """
    return decode_text(read_bytes_capped(path, max_bytes)[0])


def try_read_text(path: Path, max_bytes: int = MAX_PROSE_BYTES) -> str | None:
    """Read like :func:`read_text_capped`, or return ``None`` on an ``OSError``."""
    try:
        return read_text_capped(path, max_bytes)
    except OSError as exc:
        logger.debug("Could not read %s: %s", path, exc)
        return None


def read_ini(
    path: Path, max_bytes: int = MAX_PROSE_BYTES
) -> configparser.RawConfigParser | None:
    """Parse an INI-style file such as ``setup.cfg``, or return ``None``.

    Values are returned as written, without ``%`` interpolation, and duplicate
    sections or options are not errors. ``None`` means the file is missing,
    unreadable or not valid INI syntax.
    """
    try:
        data, truncated = read_bytes_capped(path, max_bytes)
    except OSError as exc:
        logger.debug("Could not read %s: %s", path, exc)
        return None
    text = decode_text(data)
    if truncated:
        # Keep the complete lines only. A section header or option cut in
        # half would be a syntax error.
        text = text[: text.rfind("\n") + 1]
    parser = configparser.RawConfigParser(strict=False)
    try:
        parser.read_string(text, source=str(path))
    except configparser.Error as exc:
        logger.debug("Could not parse %s: %s", path, exc)
        return None
    return parser


class FileRead(NamedTuple):
    """The whole content of a file, or a short reason why there is none."""

    data: bytes
    problem: str | None = None  # such as "file not found", with data empty
    missing: bool = False


def format_size(num_bytes: int) -> str:
    """Return a byte count as short text, such as ``"2 MiB"`` or ``"1.5 KiB"``."""
    for unit, size in (("MiB", 1024 * 1024), ("KiB", 1024)):
        if num_bytes >= size:
            return f"{num_bytes / size:.1f}".removesuffix(".0") + f" {unit}"
    return f"{num_bytes} bytes"


def read_whole_file(path: Path, max_bytes: int) -> FileRead:
    """Read a complete regular file, refusing one larger than *max_bytes*.

    Content such as TOML or Python source only makes sense whole, so this
    never returns part of a file. It does not raise.
    """
    try:
        data, truncated = read_bytes_capped(path, max_bytes)
    except (FileNotFoundError, NotADirectoryError):
        return FileRead(b"", "file not found", missing=True)
    except OSError as exc:
        return FileRead(b"", os_error_text(exc))
    if truncated:
        return FileRead(b"", f"larger than {format_size(max_bytes)}")
    return FileRead(data)


# ── Finding files by conventional name ────────────────────────────────


def _list_dir(directory: Path) -> list[tuple[str, bool, bool]]:
    """Return ``(name, is_file, is_dir)`` for each entry, sorted by name.

    Symlinks are followed, so a link to a device or a broken link is neither.
    An unreadable directory gives an empty list.
    """
    entries: list[tuple[str, bool, bool]] = []
    try:
        with os.scandir(directory) as it:
            for entry in it:
                try:
                    entries.append((entry.name, entry.is_file(), entry.is_dir()))
                except OSError:
                    entries.append((entry.name, False, False))
    except OSError:
        return []
    return sorted(entries)


def _rank(names: Sequence[str]) -> dict[str, int]:
    """Map each case-folded name to its position, keeping the first of a pair."""
    ranks: dict[str, int] = {}
    for position, name in enumerate(names):
        ranks.setdefault(name.casefold(), position)
    return ranks


def _resolve_subdir(root: Path, subdir: str) -> Path | None:
    """Return *subdir* under *root*, matching each part without regard to case."""
    current = root
    for part in subdir.split("/"):
        if not part:
            continue
        names = [name for name, _is_file, is_dir in _list_dir(current) if is_dir]
        if part not in names:
            folded = [name for name in names if name.casefold() == part.casefold()]
            if not folded:
                return None
            part = folded[0]
        current = current / part
    return current


def find_subdir(root: Path, name: str) -> Path | None:
    """Return the directory *name* under *root*, or ``None``.

    *name* may hold several parts separated by ``/``. Each part is matched
    without regard to letter case and comes back under its on-disk name. A
    symbolic link does not count as a directory, so the result always lies
    inside *root*, and a *name* that stands for *root* itself gives ``None``.
    """
    current = root
    for part in name.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        matches: list[str] = []
        try:
            with os.scandir(current) as entries:
                for entry in entries:
                    if entry.name.casefold() == part.casefold() and entry.is_dir(
                        follow_symlinks=False
                    ):
                        matches.append(entry.name)
        except OSError:
            return None
        if not matches:
            return None
        current = current / (part if part in matches else min(matches))
    return None if current == root else current


def find_project_files(
    root: Path,
    stems: Sequence[str],
    extensions: Sequence[str] | None = PROSE_EXTENSIONS,
    subdirs: Sequence[str] = ("",),
) -> list[Path]:
    """Find conventionally named regular files without regard to letter case.

    Args:
        root: Project directory.
        stems: File names without extension, most preferred first.
        extensions: Accepted extensions with their leading dot, most preferred
            first. ``""`` accepts the bare stem. ``None`` accepts the stem
            alone or followed by ``.``, ``-`` or ``_`` and anything else
            (``README.zh-CN.md``, ``LICENSE-MIT``), but not a name that ends
            in one of :data:`NON_PROSE_SUFFIXES`.
        subdirs: Directories to search, relative to *root*, in order. ``""``
            is *root* itself. Their names are matched without regard to case.

    Returns:
        Every match under its on-disk name, ordered by search directory, then
        stem, then extension, then name.
    """
    wanted = _rank(stems)
    ext_rank = None if extensions is None else _rank(extensions)
    found: list[Path] = []
    for subdir in subdirs:
        directory = _resolve_subdir(root, subdir)
        if directory is None:
            continue
        ranked: list[tuple[int, int, int, str]] = []
        for name, is_file, _is_dir in _list_dir(directory):
            if not is_file:
                continue
            folded = name.casefold()
            stem, ext = os.path.splitext(folded)
            if ext_rank is None:
                if ext in NON_PROSE_SUFFIXES:
                    continue
                for prefix, rank in wanted.items():
                    if folded == prefix or folded.startswith(prefix + "."):
                        ranked.append((rank, 0, len(name), name))
                        break
                    if folded.startswith((prefix + "-", prefix + "_")):
                        ranked.append((rank, 1, len(name), name))
                        break
            elif stem in wanted and ext in ext_rank:
                ranked.append((wanted[stem], ext_rank[ext], len(name), name))
            elif folded in wanted and "" in ext_rank:
                # A stem that itself contains a dot, such as "README.md".
                ranked.append((wanted[folded], ext_rank[""], len(name), name))
        found.extend(directory / name for *_key, name in sorted(ranked))
    return found


def find_project_file(
    root: Path,
    stems: Sequence[str],
    extensions: Sequence[str] | None = PROSE_EXTENSIONS,
    subdirs: Sequence[str] = ("",),
) -> Path | None:
    """Return the first result of :func:`find_project_files`, or ``None``."""
    matches = find_project_files(root, stems, extensions, subdirs)
    return matches[0] if matches else None


# ── Text that is safe to show ─────────────────────────────────────────


def plain_text(text: str, limit: int = _MAX_TEXT_LENGTH, *, tail: int = 0) -> str:
    """Make text taken from a repository safe to show in one detail line.

    Characters that are not printable (control characters, line breaks, the
    marks that reorder text, lone surrogates) become visible escapes such as
    ``\\n``, so the result is one line that can always be printed and encoded.
    Text longer than *limit* is cut and the cut is marked with ``...``.

    Args:
        text: The text to clean.
        limit: Largest number of characters to return.
        tail: How many characters of the end to keep when the text is cut.
    """
    limit = max(limit, 3)
    tail = max(0, min(tail, limit - 3))
    if not text.isprintable():
        text = "".join(
            char if char.isprintable() else char.encode("unicode_escape").decode()
            for char in text
        )
    if len(text) <= limit:
        return text
    end = text[len(text) - tail :] if tail else ""
    return text[: limit - 3 - len(end)] + "..." + end


def display_path(path: Path, root: Path) -> str:
    """Return *path* relative to *root* with forward slashes, for messages.

    The result is cleaned by :func:`plain_text`, and a very long path is
    shortened in the middle. A path outside *root* is shown by its file name.
    """
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError:
        relative = path.name
    return plain_text(relative, _MAX_DISPLAY_PATH, tail=_MAX_DISPLAY_PATH // 2)


# ── Paths and patterns declared in pyproject.toml ─────────────────────


def is_safe_relative(value: object) -> bool:
    """Return whether a path or glob taken from metadata stays inside the project.

    ``False`` for anything that is not a string, is empty, is absolute (POSIX,
    drive-letter or UNC form), contains a ``..`` segment or a null character,
    or is unreasonably long. Only the text is looked at.
    """
    if not isinstance(value, str) or not value.strip():
        return False
    if "\0" in value or len(value) > _MAX_DECLARED_LENGTH:
        return False
    normalised = value.replace("\\", "/")
    if normalised.startswith("/") or re.match(r"[A-Za-z]:", normalised):
        return False
    return ".." not in normalised.split("/")


def _glob_files(root: Path, pattern: str) -> Iterator[Path]:
    """Yield regular files under *root* that match a relative glob *pattern*.

    ``*``, ``?``, ``[...]`` and ``**`` mean what they mean in
    :func:`glob.glob`, except that names are compared without regard to case
    and results come sorted, under their on-disk names. A wildcard matches a
    name that starts with a dot only when the segment does too. ``**`` does
    not enter symlinked directories, which keeps it out of link loops.
    """
    segments: list[str] = []
    for part in pattern.split("/"):
        # "**/**" means the same as "**" and would walk the tree twice.
        if part in ("", ".") or (part == "**" and segments[-1:] == ["**"]):
            continue
        segments.append(part)
    if not segments:
        return
    last = len(segments) - 1
    # Each item is a directory, the segment to match in it and, when the
    # directory was listed already, that listing.
    stack: list[tuple[Path, int, list[tuple[str, bool, bool]] | None]] = [
        (root, 0, None)
    ]
    while stack:
        directory, index, listing = stack.pop()
        entries = _list_dir(directory) if listing is None else listing
        segment = segments[index]
        if segment == "**":
            # Push sub-directories first so that the rest of the pattern is
            # tried in this directory before any directory below it.
            for name, _is_file, is_dir in reversed(entries):
                child = directory / name
                if is_dir and not name.startswith(".") and not child.is_symlink():
                    stack.append((child, index, None))
            if index < last:
                stack.append((directory, index + 1, entries))
                continue
            matched = [entry for entry in entries if not entry[0].startswith(".")]
        else:
            folded = segment.casefold()
            hidden_ok = segment.startswith(".")
            matched = [
                entry
                for entry in entries
                if (hidden_ok or not entry[0].startswith("."))
                and fnmatch.fnmatchcase(entry[0].casefold(), folded)
            ]
        if index == last:
            yield from (directory / name for name, is_file, _d in matched if is_file)
        else:
            stack.extend(
                (directory / name, index + 1, None)
                for name, _is_file, is_dir in reversed(matched)
                if is_dir
            )


def resolve_declared_file(root: Path, value: object) -> Path | None:
    """Return the file a metadata field such as ``license.file`` points at.

    The path is taken literally (``*`` and ``[`` are not wildcards here) and
    is matched without regard to letter case, so the answer does not depend
    on the filesystem. If two files differ only by case, the one spelled
    exactly as declared wins.

    Returns:
        The regular file under its on-disk name, or ``None`` when there is
        none or *value* fails :func:`is_safe_relative`.
    """
    if not isinstance(value, str) or not is_safe_relative(value):
        return None
    parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
    matches = list(_glob_files(root, glob.escape("/".join(parts))))
    # Compared as text, because path equality ignores case on Windows.
    exact = [m for m in matches if list(m.relative_to(root).parts) == parts]
    return next(iter(exact or matches), None)


def find_declared_files(root: Path, patterns: object, limit: int = 50) -> list[Path]:
    """Expand glob patterns taken from metadata, such as ``license-files``.

    Args:
        root: Project directory.
        patterns: A list or tuple of patterns, or a single string that is one
            pattern. Entries that are not strings or fail
            :func:`is_safe_relative` are skipped, and any other value holds
            no patterns. Nothing passed here raises.
        limit: Largest number of files to return.

    Returns:
        Matching regular files, in pattern order and then sorted by path,
        without duplicates. See :func:`_glob_files` for how patterns match.
    """
    if isinstance(patterns, str):
        patterns = [patterns]
    elif not isinstance(patterns, (list, tuple)):
        return []
    found: dict[Path, None] = {}
    for pattern in patterns:
        if not isinstance(pattern, str) or not is_safe_relative(pattern):
            continue
        for match in _glob_files(root, pattern.replace("\\", "/")):
            found.setdefault(match)
            if len(found) >= limit:
                return list(found)
    return list(found)


def find_declared_file(root: Path, patterns: object) -> Path | None:
    """Return the first result of :func:`find_declared_files`, or ``None``."""
    matches = find_declared_files(root, patterns, limit=1)
    return matches[0] if matches else None


# ── Caching values derived from files ─────────────────────────────────

_caches: weakref.WeakSet[FileCache[Any]] = weakref.WeakSet()


def _signature(key: str) -> tuple[int, int] | None:
    """Return a file's modification time and size, or ``None`` if absent."""
    try:
        st = os.stat(key)
    except (OSError, ValueError):
        return None
    return st.st_mtime_ns, st.st_size


class FileCache(Generic[_T]):
    """Thread-safe cache of one value per file, rebuilt when the file changes.

    A value is valid while the file's size and modification time stay the
    same. A file that was changed in the two seconds before it was read is
    compared by content as well, since its time may be too coarse to show a
    second write. So a caller that edits a file and asks again gets a fresh
    value, with no need to clear anything.

    Args:
        build: Called as ``build(path, read)`` with the :class:`FileRead` for
            the file. It should not raise. Whatever it returns is cached,
            also for a file that is missing or unreadable.
        max_bytes: Size cap handed to :func:`read_whole_file`.
    """

    def __init__(self, build: Callable[[Path, FileRead], _T], max_bytes: int) -> None:
        self._build = build
        self._max_bytes = max_bytes
        # Absolute path to (signature, content hash if young, value).
        self._entries: dict[str, tuple[tuple[int, int] | None, int | None, _T]] = {}
        # Re-entrant, so a build function may look up another file here.
        self._lock = threading.RLock()
        _caches.add(self)

    def get(self, path: Path, *, store: bool = True) -> _T:
        """Return the value for *path*, building it if needed.

        When *store* is ``False`` a newly built value is returned without
        being kept. An existing entry is still used.
        """
        key = os.path.abspath(path)
        # One caller at a time, so checks that start together on the same
        # file share a single read and build.
        with self._lock:
            now = time.time_ns()
            signature = _signature(key)
            entry = self._entries.get(key)
            if entry is not None and entry[0] == signature:
                if entry[1] is None or entry[1] == self._content_hash(key):
                    return entry[2]
            read = read_whole_file(Path(key), self._max_bytes)
            value = self._build(Path(key), read)
            if store:
                young = signature is not None and now - signature[0] < _YOUNG_NS
                digest = hash(read.data) if young and read.problem is None else None
                self._entries[key] = (signature, digest, value)
            return value

    def _content_hash(self, key: str) -> int | None:
        """Return a hash of the file's content, or ``None`` if it cannot be read."""
        read = read_whole_file(Path(key), self._max_bytes)
        return hash(read.data) if read.problem is None else None

    def clear(self) -> None:
        """Forget every entry."""
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)


def clear_all_caches() -> None:
    """Clear every :class:`FileCache`, including the pyproject and AST caches.

    Entries go stale on their own when a file changes, so this is only needed
    to release memory, above all the parsed syntax trees.
    """
    for cache in list(_caches):
        cache.clear()
