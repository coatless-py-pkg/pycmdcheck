"""Find where the Python code of a project lives.

:class:`PackageLayout` answers four questions about a project directory.
Every check asks it, so the checks agree with each other.

1. Which directories do imports resolve from? Build backend settings are read
   first, then ``src/``, the project root, ``lib/`` and ``python/``.
2. What is importable there? Regular packages, namespace packages, single
   modules and stub-only packages, each with its kind.
3. Which of those is the project's own? The one the normalised project name
   leads to, then one the backend names, then the only one, then a guess.
4. Which files are runtime source? Cache, hidden and environment directories
   are pruned while walking.

Nothing here raises on a missing, unreadable or malformed tree.
"""

from __future__ import annotations

import difflib
import fnmatch
import logging
import os
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, NamedTuple

from pycmdcheck.fileio import is_regular_file, is_safe_relative
from pycmdcheck.pyproject_reader import (
    get_dict,
    get_effective_project_table,
    get_list,
    get_setup_cfg_value,
    get_setup_py_keywords,
    get_str,
    get_str_list,
    get_table,
)

logger = logging.getLogger(__name__)

# Cache and environment directories that a walk stays out of, unless one has
# its own __init__.py. Hidden directories, *.egg-info, *.dist-info and any
# directory holding pyvenv.cfg or conda-meta are pruned as well.
PRUNED_DIRS: frozenset[str] = frozenset(
    "__pycache__ __pypackages__ node_modules site-packages venv virtualenv".split()
)
# Top-level directories that are never a package in a flat layout, unless the
# project name points at one.
NON_PACKAGE_DIRS: frozenset[str] = frozenset(
    "tests test docs doc build dist venv .venv".split()
)
# Directories of supporting code. They rank last and are never left out.
HELPER_DIRS: frozenset[str] = frozenset(
    "asv_bench bench benchmark benchmarks ci demo demos doc docs example examples "
    "script scripts test testing tests tool tools".split()
)
# Test and bundled third-party directories that source_files() skips.
TEST_DIRS: frozenset[str] = frozenset({"tests", "test", "testing"})
VENDORED_DIRS: frozenset[str] = frozenset(
    {"_vendor", "_vendored", "vendored", "extern"}
)
# Top-level .py files that are tooling, unless the project name matches one.
NON_MODULE_FILES: frozenset[str] = frozenset(
    "setup.py conftest.py noxfile.py tasks.py manage.py versioneer.py __init__.py "
    "__main__.py fabfile.py hatch_build.py dodo.py pavement.py ez_setup.py "
    "build.py".split()
)
# File suffixes that mark a directory as holding non-Python sources.
NATIVE_SOURCE_SUFFIXES: tuple[str, ...] = tuple(
    ".rs .c .cc .cpp .cxx .h .hh .hpp .hxx .pyx .pxd .f .f90 .go .zig .swift "
    ".ts .tsx .js .jsx".split()
)
# Build requirements and backends that mean compiled code, as they are shown.
# The last three are backends imported under another name.
NATIVE_BUILD_TOOLS: dict[str, str] = {
    tool.lower().replace("-", "_"): tool
    for tool in (
        "maturin setuptools-rust scikit-build scikit-build-core meson-python meson "
        "pybind11 nanobind Cython cffi cmake py-build-cmake sip swig pythran mypyc "
        "hatch-mypyc hatch-cython"
    ).split()
} | {"skbuild": "scikit-build", "mesonpy": "meson-python", "sipbuild": "sip"}
_NATIVE_SETUP_KEYWORDS = ("ext_modules", "rust_extensions", "cffi_modules")
_NATIVE_PROJECT_FILES = ("Cargo.toml", "CMakeLists.txt", "meson.build")
_EXTENSION_SUFFIXES = (".py", ".pyi", ".pyx", ".so", ".pyd")
_NAME_PREFIXES = ("python_", "py_", "python", "py")
_NAME_SUFFIXES = ("_python", "_py")
# Files that make a directory a project of its own.
_PROJECT_FILES = ("pyproject.toml", "setup.py")
# A project name longer than a file name can be matches nothing.
_MAX_NAME_LENGTH = 255
# Single sub-directories followed to find the package of a namespace package.
_MAX_NAMESPACE_DEPTH = 8
# How alike a directory name and the project name must be (a difflib ratio)
# for the directory to come first among equal guesses.
_CLOSE_NAME_RATIO = 0.6
_KIND_ORDER = {"package": 0, "namespace": 1, "stub-only": 2, "module": 3}

PackageKind = Literal["package", "namespace", "module", "stub-only"]
PrimaryMatch = Literal["name", "declared", "single", "guess", "none"]


@dataclass(frozen=True)
class ImportRoot:
    """A directory that imports resolve from."""

    path: Path


@dataclass(frozen=True)
class PackageCandidate:
    """Something importable found at an import root.

    Attributes:
        name: Top-level import name (``"poetry"`` for ``poetry.core``).
        path: The top-level directory, or the ``.py`` file of a module.
        kind: ``"package"`` has ``__init__.py``, ``"namespace"`` has none but
            holds Python, ``"module"`` is one file and ``"stub-only"`` holds
            ``.pyi`` files and no ``.py`` file.
        root: The import root it was found in.
        package_dir: Where to look for ``__init__.py`` or ``py.typed``. For a
            namespace package, the regular package inside it. ``None`` for a
            module.
        matches_name: The project name leads to it.
        declared: The build backend configuration names it.
        is_helper: Probably supporting code, such as ``benchmarks``.
        contains_package: An ``__init__.py`` lies in or below it. A namespace
            candidate without one may be a package that forgot it.
    """

    name: str
    path: Path
    kind: PackageKind
    root: ImportRoot
    package_dir: Path | None = None
    matches_name: bool = False
    declared: bool = False
    is_helper: bool = False
    contains_package: bool = False

    @property
    def rank(self) -> int:
        """Return 3 for a name or declared match, 2 if ordinary, 1 for a helper."""
        if self.matches_name or self.declared:
            return 3
        return 1 if self.is_helper else 2


# ── Names ─────────────────────────────────────────────────────────────


def normalize_name(name: str) -> str:
    """Return *name* in lower case with each run of ``-``, ``_`` and ``.`` as ``_``.

    A leading underscore is kept, so ``_yaml`` and ``yaml`` stay different.
    """
    return re.sub(r"[-_.]+", "_", name.strip()).lower()


class _NamePath(NamedTuple):
    """Directory names that a project or declared name may lead through."""

    parts: tuple[str, ...]
    declared: bool  # named by the build backend, not by the project name
    through_packages: bool  # may continue below a regular package, see _follow()


def _name_variants(name: str) -> list[str]:
    """Return normalised *name*, then the forms with a ``python`` affix dropped."""
    base = normalize_name(name)
    variants = [base] if base else []
    for prefix in _NAME_PREFIXES:
        if base.startswith(prefix) and len(base) > len(prefix) + 1:
            variants.append(base[len(prefix) :])
            break
    for suffix in _NAME_SUFFIXES:
        if base.endswith(suffix) and len(base) > len(suffix) + 1:
            variants.append(base[: -len(suffix)])
            break
    return variants


def _name_splits(name: str) -> list[tuple[str, ...]]:
    """Return the directory paths a normalised name may map to, shortest first.

    ``"a_b_c"`` gives ``("a_b_c",)``, ``("a", "b_c")`` and ``("a", "b", "c")``.
    """
    parts = name.split("_")
    splits = [(*parts[:cut], "_".join(parts[cut:])) for cut in range(len(parts))]
    return [split for split in splits if all(split)]


def _name_paths(name: str, declared: list[str]) -> list[_NamePath]:
    """Return the name paths to try under an import root, best first.

    The path along the dots of the project name comes first. A path cut at
    ``-`` or ``_`` is a guess and never starts in a helper directory, so
    ``pytest`` is not ``test/``.
    """
    paths: list[_NamePath] = []
    seen: set[tuple[str, ...]] = set()
    base = normalize_name(name)
    dotted = tuple(part for part in map(normalize_name, name.split(".")) if part)
    for position, variant in enumerate(_name_variants(name)):
        splits = _name_splits(variant)
        if position == 0 and len(dotted) > 1:
            splits.insert(0, dotted)
        for parts in splits:
            guessed = parts not in ((base,), dotted)
            if guessed and parts[0] in NON_PACKAGE_DIRS | HELPER_DIRS:
                continue
            if parts not in seen:
                seen.add(parts)
                paths.append(_NamePath(parts, False, parts == dotted))
    for item in declared:
        parts = tuple(part for part in map(normalize_name, item.split(".")) if part)
        if parts:
            paths.append(_NamePath(parts, True, True))
    return paths


def _is_close_name(candidate: str, project: str) -> bool:
    """Return whether two normalised names are alike, as sklearn and scikit_learn."""
    if not candidate or not project:
        return False
    matcher = difflib.SequenceMatcher(None, candidate, project, autojunk=False)
    return matcher.ratio() >= _CLOSE_NAME_RATIO


def _is_test_file(name: str) -> bool:
    """Return whether a file name follows a test naming convention."""
    named = name in ("conftest.py", "tests.py") or name.endswith("_test.py")
    return named or name.startswith("test_")


# ── Filesystem ────────────────────────────────────────────────────────


def _scan(directory: Path) -> tuple[list[str], list[str]]:
    """Return the sorted ``(directory names, file names)`` in *directory*.

    Symlinks are followed. An unreadable directory gives two empty lists.
    """
    dirs: list[str] = []
    files: list[str] = []
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if entry.is_dir():
                        dirs.append(entry.name)
                    elif entry.is_file():
                        files.append(entry.name)
                except OSError:
                    continue
    except (OSError, ValueError):
        return [], []
    return sorted(dirs), sorted(files)


def _is_pruned(directory: Path, name: str) -> bool:
    """Return whether a walk stays out of *directory*, whose name is *name*."""
    if name.startswith(".") or name.endswith((".egg-info", ".dist-info")):
        return True
    if is_regular_file(directory / "pyvenv.cfg"):
        return True
    if os.path.isdir(directory / "conda-meta"):
        return True
    return name in PRUNED_DIRS and not is_regular_file(directory / "__init__.py")


def _walk_dirs(
    top: Path, *, portions_only: bool = False
) -> Iterator[tuple[Path, list[str]]]:
    """Yield ``(directory, file names)`` for *top* and the directories below it.

    Pruned and symlinked directories are not entered. With *portions_only*,
    neither is a ``src`` directory or a project of its own, since no namespace
    package runs through one (``packages/a/src/a``).
    """
    stack = [top]
    while stack:
        current = stack.pop()
        dirs, files = _scan(current)
        yield current, files
        if portions_only and current != top:
            if current.name == "src" or any(name in files for name in _PROJECT_FILES):
                continue
        for name in reversed(dirs):
            child = current / name
            if not _is_pruned(child, name) and not os.path.islink(child):
                stack.append(child)


def _has_file(top: Path, suffixes: tuple[str, ...]) -> bool:
    """Return whether a file ending in one of *suffixes* lies in or below *top*."""
    return any(
        name.endswith(suffixes) for _, files in _walk_dirs(top) for name in files
    )


def _has_nested_package(top: Path, *, portions_only: bool = False) -> bool:
    """Return whether a directory below *top* has an ``__init__.py``."""
    walk = _walk_dirs(top, portions_only=portions_only)
    return any("__init__.py" in files for current, files in walk if current != top)


def _namespace_leaf(directory: Path) -> Path:
    """Return the first regular package reached through single sub-directories.

    ``src/ns`` holding only ``sub/__init__.py`` gives ``src/ns/sub``.
    """
    current = directory
    for _ in range(_MAX_NAMESPACE_DEPTH):
        dirs, files = _scan(current)
        below = [d for d in dirs if d.isidentifier() and not _is_pruned(current / d, d)]
        if len(below) != 1 or any(name.endswith(".py") for name in files):
            break
        current = current / below[0]
    return current


def _follow(base: Path, path: _NamePath) -> Path | None:
    """Return the directory or module that a name path leads to under *base*.

    Each part is a directory whose normalised name equals it. The last part
    may be a ``.py`` file. A guessed path stops at a regular package, so
    ``foo-utils`` is not ``foo/utils`` when ``foo`` is the package.
    """
    current = base
    for index, part in enumerate(path.parts):
        dirs, files = _scan(current)
        if index and not path.through_packages and "__init__.py" in files:
            return None
        matches = [name for name in dirs if normalize_name(name) == part]
        if matches:
            current = current / (part if part in matches else matches[0])
            continue
        if index == len(path.parts) - 1:
            for name in files:
                if name.endswith(".py") and normalize_name(name[:-3]) == part:
                    return current / name
        return None
    return current


# ── Build backend configuration ───────────────────────────────────────


def _str_items(value: object) -> list[str]:
    """Return the strings in *value*, which may be one string or a list."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, (list, tuple)):
        return [item for item in value if isinstance(item, str)]
    return []


@dataclass
class _Declared:
    """What build backend configuration says about where the code is."""

    # Relative source directories. "" is the project root.
    roots: list[str] = field(default_factory=list)
    # Import names the backend is told to ship, dotted for a namespace.
    names: list[str] = field(default_factory=list)
    # Whether the names are everything that ships, as with an explicit
    # setuptools packages list. Anything else is then a helper.
    exhaustive: bool = False
    # setuptools packages.find include and exclude patterns.
    include: list[str] = field(default_factory=list)
    exclude: list[str] = field(default_factory=list)
    # Whether packages.find looks for namespace packages too.
    namespaces: bool = False

    def add_root(self, value: object) -> None:
        """Record a source directory if it is a safe relative path."""
        if not isinstance(value, str):
            return
        text = value.strip().replace("\\", "/")
        if text in ("", ".", "./") or is_safe_relative(text):
            parts = [part for part in text.split("/") if part not in ("", ".")]
            self.roots.append("/".join(parts))

    def add_name(self, value: object, *, exhaustive: bool = False) -> None:
        """Record an import name such as ``"pkg"`` or ``"ns.pkg"``."""
        if isinstance(value, str) and value.strip():
            self.names.append(value.strip())
            self.exhaustive = self.exhaustive or exhaustive

    def add_package_path(self, value: object, *, exhaustive: bool = False) -> None:
        """Record a package directory such as ``"src/pkg"``."""
        if not isinstance(value, str) or not is_safe_relative(value):
            return
        parts = [p for p in value.replace("\\", "/").split("/") if p not in ("", ".")]
        if parts and not any(char in parts[-1] for char in "*?["):
            self.add_root("/".join(parts[:-1]))
            self.add_name(parts[-1].removesuffix(".py"), exhaustive=exhaustive)

    def add_package_dir(self, mapping: object) -> None:
        """Record a setuptools ``package_dir`` mapping."""
        if not isinstance(mapping, dict):
            return
        for key, value in mapping.items():
            if not isinstance(key, str) or not isinstance(value, str):
                continue
            if key.strip() == "":
                self.add_root(value)
            else:
                self.add_package_path(value)
                self.add_name(key)


def _read_declared(root: Path) -> _Declared:
    """Collect source directories and package names from backend settings."""
    declared = _Declared()

    maturin = get_table(root, "tool", "maturin")
    declared.add_root(maturin.get("python-source"))
    # "pkg._native" is an extension module inside the package "pkg".
    module_name = get_str(maturin, "module-name") or ""
    package, dot, _module = module_name.rpartition(".")
    declared.add_name(package if dot else module_name)
    for name in get_str_list(maturin, "python-packages"):
        declared.add_name(name)

    setuptools = get_table(root, "tool", "setuptools")
    declared.add_package_dir(get_dict(setuptools, "package-dir"))
    find = get_dict(get_dict(setuptools, "packages"), "find")
    for where in _str_items(find.get("where")):
        declared.add_root(where)
    declared.include += _str_items(find.get("include"))
    declared.exclude += _str_items(find.get("exclude"))
    declared.namespaces = bool(find) and find.get("namespaces") is not False
    for key in ("packages", "py-modules"):
        for name in get_str_list(setuptools, key):
            declared.add_name(name, exhaustive=True)

    hatch = get_table(root, "tool", "hatch", "build")
    for table in (get_dict(get_dict(hatch, "targets"), "wheel"), hatch):
        for package in get_str_list(table, "packages"):
            declared.add_package_path(package, exhaustive=True)
        sources = table.get("sources")
        if isinstance(sources, dict):
            sources = [key for key, value in sources.items() if value == ""]
        for source in _str_items(sources):
            declared.add_root(source)

    for entry in get_list(get_table(root, "tool", "poetry"), "packages"):
        declared.add_root(get_str(entry, "from") or None)
        first = (get_str(entry, "include") or "").replace("\\", "/").split("/")[0]
        if not any(char in first for char in "*?["):
            declared.add_name(first.removesuffix(".py"), exhaustive=True)

    flit = get_table(root, "tool", "flit", "module")
    declared.add_name(get_str(flit, "name"), exhaustive=True)

    pdm = get_table(root, "tool", "pdm")
    declared.add_root(get_str(get_dict(pdm, "build"), "package-dir"))
    declared.add_root(get_str(pdm, "package-dir"))

    scikit = get_table(root, "tool", "scikit-build", "wheel")
    for package in get_str_list(scikit, "packages"):
        declared.add_package_path(package, exhaustive=True)

    def cfg(section: str, option: str) -> list[str]:
        value = get_setup_cfg_value(root, section, option) or ""
        return [item.strip() for item in re.split(r"[\n,]", value) if item.strip()]

    pairs = (item.partition("=") for item in cfg("options", "package_dir"))
    declared.add_package_dir({k.strip(): v.strip() for k, equals, v in pairs if equals})
    for where in cfg("options.packages.find", "where"):
        declared.add_root(where)
    declared.include += cfg("options.packages.find", "include")
    declared.exclude += cfg("options.packages.find", "exclude")
    packages = cfg("options", "packages")
    declared.namespaces |= "find_namespace:" in packages
    if any(item.startswith("find") for item in packages):
        packages = []
    for name in packages + cfg("options", "py_modules"):
        declared.add_name(name, exhaustive=True)

    keywords = get_setup_py_keywords(root)
    declared.add_package_dir(keywords.get("package_dir"))
    for key in ("packages", "py_modules"):
        # A computed list such as find_packages() names nothing here.
        if isinstance(keywords.get(key), (list, tuple)):
            for name in _str_items(keywords[key]):
                declared.add_name(name, exhaustive=True)

    declared.roots = list(dict.fromkeys(declared.roots))
    declared.names = list(dict.fromkeys(declared.names))
    return declared


def _declared_project_name(root: Path) -> str:
    """Return the project name from pyproject.toml, setup.cfg or setup.py, or ''."""
    name: object = get_str(get_effective_project_table(root), "name")
    if not isinstance(name, str) or not name.strip():
        name = get_setup_cfg_value(root, "metadata", "name")
    if not name:
        name = get_setup_py_keywords(root).get("name")
    if isinstance(name, str) and len(name) <= _MAX_NAME_LENGTH:
        return name
    return ""


def _native_build_evidence(root: Path) -> list[str]:
    """Return one readable line per sign that the build compiles native code."""
    evidence: list[str] = []
    build_system = get_table(root, "build-system")
    backend = get_str(build_system, "build-backend") or ""
    if normalize_name(backend.split(":")[0].split(".")[0]) in NATIVE_BUILD_TOOLS:
        evidence.append(f"build-backend is {backend}")
    for requirement in get_str_list(build_system, "requires"):
        match = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement)
        tool = NATIVE_BUILD_TOOLS.get(normalize_name(match.group(1))) if match else ""
        if tool:
            evidence.append(f"build-system.requires lists {tool}")
    keywords = get_setup_py_keywords(root)
    for keyword in _NATIVE_SETUP_KEYWORDS:
        if keyword in keywords:
            evidence.append(f"setup.py passes {keyword}")
    if "ext-modules" in get_table(root, "tool", "setuptools"):
        evidence.append("[tool.setuptools] ext-modules is set")
    for name in _NATIVE_PROJECT_FILES:
        if is_regular_file(root / name):
            evidence.append(f"{name} is present")
    return list(dict.fromkeys(evidence))


# ── The layout ────────────────────────────────────────────────────────

# Entries of an import root, with what a name reaches through each of them.
_Hits = dict[str, tuple[Path, _NamePath]]


class PackageLayout:
    """Discover the layout of a Python project.

    The tree is read on first use, once per instance.

    Args:
        project_root: The directory that holds ``pyproject.toml`` or the like.
    """

    def __init__(self, project_root: Path) -> None:
        self._root = project_root
        self._discovered = False
        self._project = ""
        self._declared = _Declared()
        self._name_paths: list[_NamePath] = []
        # Position of the name path that led to a candidate, by its path.
        self._name_order: dict[Path, int] = {}
        self._root_dir_names = {"src", "lib", "python"}
        self._roots: list[ImportRoot] = []
        self._candidates: list[PackageCandidate] = []
        # Directories on the way to what a name points at. No walk skips them.
        self._protected: set[Path] = set()

    @property
    def packages(self) -> list[PackageCandidate]:
        """Return everything importable at the import roots, best first.

        Order: a match with the project name, names the build backend
        declares, ordinary packages, helpers. Within a group, public names
        come before ``_private`` ones, packages before modules, and a name
        like the project's before the rest (``sklearn`` for scikit-learn).
        """
        self._discover()
        return list(self._candidates)

    @property
    def package_dirs(self) -> list[Path]:
        """Return the top-level directories that have ``__init__.py``, sorted."""
        return sorted(c.path for c in self.packages if c.kind == "package")

    @property
    def primary_candidate(self) -> PackageCandidate | None:
        """Return the candidate that is most likely the project's own."""
        return next(iter(self.packages), None)

    @property
    def primary_match(self) -> PrimaryMatch:
        """Return how :attr:`primary_candidate` was chosen.

        ``"name"`` (the project name leads to it), ``"declared"`` (the build
        backend names it), ``"single"`` (nothing else could be the package),
        ``"guess"`` (the first of several equal candidates, or a lone helper)
        or ``"none"``. Do not name a guessed directory in advice.
        """
        primary = self.primary_candidate
        if primary is None:
            return "none"
        if primary.matches_name:
            return "name"
        if primary.declared:
            return "declared"
        rivals = [c for c in self._candidates if c.rank >= primary.rank]
        return "guess" if primary.is_helper or len(rivals) > 1 else "single"

    def source_files(self, *, include_tests: bool = False) -> list[Path]:
        """Return the ``.py`` files that make up the project's runtime code.

        Left out are helper candidates whenever a real package exists, test
        directories and test files, vendored code, directories whose name
        cannot be imported, caches, hidden directories and virtual
        environments. A directory the project name points at is never
        skipped, so a project called ``testing`` is still scanned.

        Args:
            include_tests: Keep the test directories and test files.
        """
        result: set[Path] = set()
        for candidate in self._runtime():
            if candidate.kind == "module":
                result.add(candidate.path)
            elif candidate.kind != "stub-only":
                result.update(self._files(candidate.path, ".py", include_tests))
        return sorted(result)

    def stub_files(self) -> list[Path]:
        """Return the ``.pyi`` files, chosen as :meth:`source_files` chooses."""
        result: set[Path] = set()
        for candidate in self._runtime():
            if candidate.kind != "module":
                result.update(self._files(candidate.path, ".pyi", False))
            elif is_regular_file(candidate.path.with_suffix(".pyi")):
                result.add(candidate.path.with_suffix(".pyi"))
        return sorted(result)

    def local_names(self) -> set[str]:
        """Return every top-level name an import may use for first-party code.

        Holds the candidates, the names the build backend declares, the
        normalised project name, and every Python or extension module file at
        the import roots, with the directories there that hold one. This is
        wider than :meth:`source_files`. Use it only to decide that an import
        is not third-party.
        """
        names = {c.name for c in self.packages if c.name.isidentifier()}
        names.update(name.split(".")[0] for name in self._declared.names)
        if self._project:
            names.add(self._project)
        for root in self._roots or [ImportRoot(self._root)]:
            dirs, files = _scan(root.path)
            for name in files:
                stem = name.split(".")[0]
                if name.endswith(_EXTENSION_SUFFIXES) and stem.isidentifier():
                    names.add(stem)
            for name in dirs:
                if name.isidentifier() and name not in PRUNED_DIRS:
                    inside = _scan(root.path / name)[1]
                    if any(item.endswith(_EXTENSION_SUFFIXES) for item in inside):
                        names.add(name)
        return names

    def top_level_modules(self) -> list[Path]:
        """Return the single-file modules at the first import root, sorted."""
        modules = [c for c in self.packages if c.kind == "module"]
        return sorted(c.path for c in modules if c.root == self._roots[0])

    @property
    def has_src_dir(self) -> bool:
        """Return whether ``src/`` exists and does not link out of the project."""
        src_dir = self._root / "src"
        return os.path.isdir(src_dir) and self._inside_project(src_dir) is not None

    @property
    def non_python_src_dirs(self) -> list[Path]:
        """Return the directories under ``src/`` that hold files but no Python.

        These are Rust, C or front-end sources. A stub-only package is not
        listed, a directory of C sources that ships a stub is. A link out of
        the project is ignored.
        """
        if not self.has_src_dir:
            return []
        found: list[Path] = []
        for name in _scan(self._root / "src")[0]:
            path = self._root / "src" / name
            if _is_pruned(path, name):
                continue
            if os.path.islink(path) and self._inside_project(path) is None:
                continue
            if not _has_file(path, ("",)) or _has_file(path, (".py",)):
                continue
            # Stubs with nothing to compile make a stub-only package.
            stubs = _has_file(path, (".pyi",))
            if not stubs or _has_file(path, NATIVE_SOURCE_SUFFIXES):
                found.append(path)
        return found

    @property
    def src_has_non_python_sources(self) -> bool:
        """Return whether ``src/`` holds sources in a language other than Python."""
        if not self.has_src_dir:
            return False
        loose = any(
            os.path.splitext(name)[1].lower() in NATIVE_SOURCE_SUFFIXES
            for name in _scan(self._root / "src")[1]
        )
        return loose or bool(self.non_python_src_dirs)

    @property
    def native_build_evidence(self) -> list[str]:
        """Return one readable line per sign that the build compiles code.

        Read from ``[build-system]``, ``setup.py`` keywords such as
        ``ext_modules``, and build files such as ``Cargo.toml``.
        """
        return _native_build_evidence(self._root)

    @property
    def is_native_build(self) -> bool:
        """Return whether anything says the project builds native code."""
        return bool(self.native_build_evidence)

    # ── Internal discovery ────────────────────────────────────────────

    def _discover(self) -> None:
        """Read the tree once and fill in the import roots and candidates."""
        if self._discovered:
            return
        self._discovered = True
        root = self._root
        self._declared = declared = _read_declared(root)
        name = _declared_project_name(root)
        self._project = normalize_name(name)
        self._name_paths = _name_paths(name, declared.names)
        self._root_dir_names |= {r.split("/")[0] for r in declared.roots if r}

        candidates: list[PackageCandidate] = []
        seen: set[Path] = set()
        for relative in declared.roots:
            path = root / relative
            # A directory is scanned once, whatever link or spelling names
            # it, and not at all if it leaves the project.
            resolved = self._inside_project(path)
            if not os.path.isdir(path) or resolved is None or resolved in seen:
                continue
            seen.add(resolved)
            found = self._scan_root(ImportRoot(path))
            if found:
                self._roots.append(ImportRoot(path))
                candidates += found

        # Conventions, in order. A later one wins only with a better match.
        conventions = () if self._roots else ("src", "", "lib", "python")
        for directory in conventions:
            path = root / directory
            if not os.path.isdir(path) or self._inside_project(path) is None:
                continue
            # src/__init__.py makes src a package of the project root.
            if directory and is_regular_file(path / "__init__.py"):
                continue
            found = self._scan_root(ImportRoot(path))
            if self._is_better_root(found, candidates):
                self._roots, candidates = [ImportRoot(path)], found

        close = {
            c.name
            for c in candidates
            if c.rank == 2 and _is_close_name(normalize_name(c.name), self._project)
        }
        candidates.sort(
            key=lambda c: (
                -c.rank,
                not c.matches_name,
                # Among several name matches, the form that was tried first.
                self._name_order.get(c.path, 0) if c.matches_name else 0,
                not c.matches_name and c.name.startswith("_"),
                _KIND_ORDER[c.kind],
                self._roots.index(c.root),
                c.name not in close,
                c.name,
            )
        )
        self._candidates = candidates
        logger.debug("Layout of %s: %d candidate(s)", root, len(candidates))

    @staticmethod
    def _is_better_root(
        found: list[PackageCandidate], best: list[PackageCandidate]
    ) -> bool:
        """Return whether the candidates of a later root replace the best so far.

        They need a better match. A module or a directory without
        ``__init__.py`` does not replace a root that holds a real package, or
        a script named like the project would hide the package in ``src/``.
        """
        rank = max((c.rank for c in found), default=0)
        best_rank = max((c.rank for c in best), default=0)
        has_package = any(c.rank == rank and c.kind == "package" for c in found)
        if rank == best_rank >= 2:
            # A tie goes to a real package when the root chosen so far holds
            # only directories without __init__.py, such as C sources.
            return has_package and all(
                c.kind == "namespace" and not c.contains_package for c in best
            )
        if rank <= best_rank:
            return False
        if not any(c.rank >= 2 and c.kind in ("package", "namespace") for c in best):
            return True
        return has_package

    def _inside_project(self, path: Path) -> Path | None:
        """Return *path* with links resolved, or ``None`` if it leaves the project."""
        try:
            resolved = path.resolve()
            resolved.relative_to(self._root.resolve())
        except (OSError, ValueError, RuntimeError):
            return None
        return resolved

    def _runtime(self) -> list[PackageCandidate]:
        """Return the candidates that are not helpers, or all if all are."""
        return [c for c in self.packages if c.rank >= 2] or self.packages

    def _match_names(self, base: Path, entries: list[str]) -> _Hits:
        """Map *entries* of *base* to what the project or declared names reach."""
        hits: _Hits = {}
        # Only a name whose first part is an entry can lead anywhere, and the
        # first path to reach an entry wins.
        open_parts = {normalize_name(name.removesuffix(".py")) for name in entries}
        for name_path in self._name_paths:
            if name_path.parts[0] not in open_parts:
                continue
            target = _follow(base, name_path)
            if target is None:
                continue
            open_parts.discard(name_path.parts[0])
            relative = target.relative_to(base).parts
            self._name_order[base / relative[0]] = len(hits)
            hits[relative[0]] = (target, name_path)
            for depth in range(len(relative)):
                self._protected.add(base.joinpath(*relative[: depth + 1]))
        return hits

    def _left_out_by_backend(self, name: str) -> bool:
        """Return whether an explicit package list or packages.find omits *name*."""
        include, exclude = self._declared.include, self._declared.exclude
        if self._declared.exhaustive or any(
            fnmatch.fnmatchcase(name, pattern) for pattern in exclude
        ):
            return True
        return bool(include) and not any(
            fnmatch.fnmatchcase(name, pattern) or pattern.split(".")[0] == name
            for pattern in include
        )

    def _classify(
        self, path: Path, name: str, flat: bool, target: Path | None
    ) -> tuple[PackageKind, Path, bool] | None:
        """Return ``(kind, package_dir, weak)`` for a directory, or ``None``.

        *target* is what a project or declared name reaches through it, if
        any. *weak* marks a flat-layout namespace package found without such
        a name, which counts only beside no regular package. A name that
        cannot be imported (``data-files``) needs a *target*, or ``-stubs``.
        """
        files = _scan(path)[1]
        if target is not None:
            package_dir = target if os.path.isdir(target) else target.parent
            if "__init__.py" in files:
                return "package", package_dir, False
            if _has_file(path, (".py",)):
                return "namespace", package_dir, False
            return ("stub-only", path, False) if _has_file(path, (".pyi",)) else None
        if name.isidentifier():
            if "__init__.py" in files:
                return "package", path, False
            if flat:
                # A leading underscore marks build output or bundled code,
                # unless a packages.find pattern asks for the name with one
                # and no exclude pattern takes it out again. The search has
                # to cover namespace packages and this directory.
                find = self._declared
                asked = any(
                    pattern.startswith("_")
                    and fnmatch.fnmatchcase(name, pattern.split(".")[0])
                    for pattern in find.include
                ) and not any(fnmatch.fnmatchcase(name, p) for p in find.exclude)
                asked = asked and find.namespaces and "" in (find.roots or [""])
                skipped = name in self._root_dir_names or (
                    name.startswith("_") and not asked
                )
                if not skipped and _has_nested_package(path, portions_only=True):
                    return "namespace", _namespace_leaf(path), True
            elif _has_file(path, (".py",)):
                return "namespace", _namespace_leaf(path), False
        if name.endswith("-stubs") or (name.isidentifier() and "__init__.pyi" in files):
            if not _has_file(path, (".py",)) and _has_file(path, (".pyi",)):
                return "stub-only", path, False
        return None

    def _scan_root(self, import_root: ImportRoot) -> list[PackageCandidate]:
        """Return the package candidates directly under one import root."""
        base = import_root.path
        flat = base == self._root
        dirs, files = _scan(base)
        hits = self._match_names(base, dirs + files)
        found: list[PackageCandidate] = []
        weak: list[PackageCandidate] = []

        # A linked directory counts wherever it points. Only paths taken from
        # the build configuration are confined to the project.
        for name in dirs:
            path = base / name
            hit = hits.get(name)
            if hit is None and (
                _is_pruned(path, name) or (flat and name in NON_PACKAGE_DIRS)
            ):
                continue
            classified = self._classify(path, name, flat, hit[0] if hit else None)
            if classified is None:
                continue
            kind, package_dir, is_weak = classified
            candidate = PackageCandidate(
                name=name,
                path=path,
                kind=kind,
                root=import_root,
                package_dir=package_dir,
                matches_name=hit is not None and not hit[1].declared,
                declared=hit is not None and hit[1].declared,
                is_helper=hit is None
                and (name in HELPER_DIRS or self._left_out_by_backend(name)),
                contains_package=kind == "package"
                or (kind == "namespace" and _has_nested_package(path)),
            )
            (weak if is_weak else found).append(candidate)

        beside_package = flat and any(c.rank >= 2 for c in found)
        if not beside_package:
            found.extend(weak)

        for name in files:
            hit = hits.get(name)
            tooling = name in NON_MODULE_FILES or _is_test_file(name)
            if not name.endswith(".py") or (
                hit is None and (tooling or not name[:-3].isidentifier())
            ):
                continue
            found.append(
                PackageCandidate(
                    name=name[:-3],
                    path=base / name,
                    kind="module",
                    root=import_root,
                    matches_name=hit is not None and not hit[1].declared,
                    declared=hit is not None and hit[1].declared,
                    is_helper=hit is None
                    and (beside_package or self._declared.exhaustive),
                )
            )
        return found

    def _files(self, top: Path, suffix: str, include_tests: bool) -> Iterator[Path]:
        """Yield the runtime files ending in *suffix* in and below *top*.

        Pruned, symlinked, vendored and unimportable directories are skipped,
        and tests unless *include_tests*. A directory that a project or
        declared name points at is always entered.
        """
        stack = [top]
        while stack:
            current = stack.pop()
            dirs, files = _scan(current)
            for name in files:
                if name.endswith(suffix) and (include_tests or not _is_test_file(name)):
                    yield current / name
            for name in dirs:
                child = current / name
                if child not in self._protected and (
                    _is_pruned(child, name)
                    or os.path.islink(child)
                    or (name in TEST_DIRS and not include_tests)
                    or name in VENDORED_DIRS
                    or not name.isidentifier()
                ):
                    continue
                stack.append(child)
