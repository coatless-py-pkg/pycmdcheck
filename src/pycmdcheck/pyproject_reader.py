"""Tolerant reader for ``pyproject.toml``, ``setup.cfg`` and ``setup.py``.

Every check reads package metadata here, so each file is parsed once and one
set of rules decides what a missing, broken or oddly typed value means.

Only :func:`read_pyproject` raises. Every other function gives an empty table,
a default or ``None`` for a file or value it cannot use. What is read stays
cached until the file changes on disk (see :class:`pycmdcheck.fileio.FileCache`).
"""

import ast
import codecs
import configparser
import copy
import logging
import math
import re
import tomllib
from collections import Counter
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal, NamedTuple, overload

from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.constants import MAX_PROSE_BYTES, MAX_PYPROJECT_BYTES, MAX_TOML_DEPTH
from pycmdcheck.fileio import FileCache, FileRead, plain_text, read_ini, read_whole_file

logger = logging.getLogger(__name__)

PyprojectStatus = Literal["ok", "missing", "unparseable"]
MetadataSource = Literal["project", "poetry", "none"]


# ── Loading ───────────────────────────────────────────────────────────


class _Document(NamedTuple):
    """What one ``pyproject.toml`` holds, as kept in the cache."""

    status: PyprojectStatus
    data: dict[str, Any]  # the parsed file, empty unless status is "ok"
    error: str | None  # why the file is unparseable
    effective: dict[str, Any]  # [project] completed from [tool.poetry]
    dev_dependencies: list[str]


def _too_deep(value: Any, room: int = MAX_TOML_DEPTH) -> bool:
    """Return whether tables and arrays in *value* nest more than *room* deep."""
    if isinstance(value, dict):
        value = value.values()
    elif not isinstance(value, list):
        return False
    return room == 0 or any(_too_deep(child, room - 1) for child in value)


def _parse(read: FileRead) -> dict[str, Any]:
    """Parse the content of a ``pyproject.toml``.

    Raises:
        OSError: If the file could not be read or is too large.
        ValueError: If it is not UTF-8, is not valid TOML, or nests too deeply.
    """
    if read.problem is not None:
        verb = "is" if read.problem.startswith("larger than") else "could not be read:"
        raise OSError(f"pyproject.toml {verb} {read.problem}")
    if read.data.startswith(codecs.BOM_UTF8):
        raise ValueError(
            "pyproject.toml starts with a UTF-8 byte order mark, "
            "which TOML does not allow"
        )
    # The limit keeps every later copy of a table within the recursion limit.
    too_deep = f"pyproject.toml nests tables or arrays more than {MAX_TOML_DEPTH} deep"
    try:
        data = tomllib.loads(read.data.decode("utf-8"))
    except RecursionError:
        raise ValueError(too_deep) from None
    if _too_deep(data):
        raise ValueError(too_deep)
    return data


def _load(path: Path, read: FileRead) -> _Document:
    """Build the cache value for one ``pyproject.toml``. Never raises."""
    if read.missing:
        return _Document("missing", {}, None, {}, [])
    try:
        data = _parse(read)
    except UnicodeDecodeError as exc:
        error = f"pyproject.toml is not valid UTF-8 ({exc.reason} at byte {exc.start})"
    except (ValueError, OSError) as exc:
        error = str(exc)
    else:
        try:
            derived = _effective_project(data), _dev_dependencies(data)
        except Exception as exc:
            # Last resort. The Poetry translation is written not to raise,
            # but its input is whatever the repository holds.
            logger.debug("Could not translate [tool.poetry] in %s: %r", path, exc)
            derived = get_dict(data, "project"), []
        return _Document("ok", data, None, *derived)
    # One short line. The parser can quote a key of any length, and the end
    # of its message says where the problem is, so the middle is what goes.
    error = plain_text(error, tail=40)
    logger.debug("Could not parse %s: %s", path, error)
    return _Document("unparseable", {}, error, {}, [])


_cache: FileCache[_Document] = FileCache(_load, MAX_PYPROJECT_BYTES)


def _document(package_path: Path) -> _Document:
    return _cache.get(package_path / "pyproject.toml")


def read_pyproject(package_path: Path) -> dict[str, Any] | None:
    """Return the parsed ``pyproject.toml``, or ``None`` if there is no file.

    The dict is the cached one, so treat it as read-only. Checks should use
    the accessors below, which do not raise and return private copies.

    Raises:
        ValueError: If the file is not UTF-8, not valid TOML or nested too deeply.
        OSError: If it is not a regular file, cannot be read, or is too large.
    """
    document = _document(package_path)
    if document.status == "unparseable":
        # Parse again, so that the caller gets the original exception.
        path = package_path / "pyproject.toml"
        return _parse(read_whole_file(path, MAX_PYPROJECT_BYTES))
    return document.data if document.status == "ok" else None


def pyproject_status(package_path: Path) -> PyprojectStatus:
    """Say whether ``pyproject.toml`` is usable, without raising.

    ``"unparseable"`` means something is there that cannot be used: invalid
    TOML, not UTF-8, a directory or device, too large, or nested too deeply.
    """
    return _document(package_path).status


def pyproject_error(package_path: Path) -> str | None:
    """Return why ``pyproject.toml`` is unparseable, otherwise ``None``.

    The reason is one line of at most 200 characters. It starts with
    ``"pyproject.toml"`` unless it is the TOML parser's own message.
    """
    return _document(package_path).error


def clear_cache() -> None:
    """Forget what was read from ``pyproject.toml`` and ``setup.cfg``."""
    _cache.clear()
    _setup_cfg_cache.clear()


# ── Tables ────────────────────────────────────────────────────────────


def get_table(package_path: Path, *keys: str) -> dict[str, Any]:
    """Return a private copy of the table at a key path in ``pyproject.toml``.

    ``get_table(path)`` is the whole document and ``get_table(path, "tool",
    "hatch")`` is ``[tool.hatch]``. The result is empty if the file is missing
    or unparseable, a key is absent, or the value found is not a table.
    """
    table = _document(package_path).data
    for key in keys:
        table = get_dict(table, key)
    return copy.deepcopy(table)


def get_project_table(package_path: Path) -> dict[str, Any]:
    """Return a copy of ``[project]`` as written, see :func:`get_table`."""
    return get_table(package_path, "project")


def get_tool_table(package_path: Path, tool_name: str) -> dict[str, Any]:
    """Return a copy of ``[tool.<tool_name>]``, see :func:`get_table`."""
    return get_table(package_path, "tool", tool_name)


def get_effective_project_table(package_path: Path) -> dict[str, Any]:
    """Return a PEP 621-shaped ``[project]`` table, accounting for Poetry.

    With only ``[tool.poetry]`` (Poetry before 2.0), its fields are translated:
    ``requires-python`` from ``dependencies.python``, ``dependencies`` as
    requirement strings, entries marked ``optional`` under
    ``optional-dependencies``, and ``urls`` from the homepage, repository and
    documentation keys. With both tables (Poetry 2), ``[project]`` wins and
    ``[tool.poetry]`` fills in only the fields it lacks. Such a field can
    still be listed in ``dynamic``, because Poetry may replace it at build
    time. The result is a private copy, empty when neither table is usable.
    """
    return copy.deepcopy(_document(package_path).effective)


def project_metadata_source(package_path: Path) -> MetadataSource:
    """Say which table :func:`get_effective_project_table` is built from.

    ``"project"`` is a non-empty ``[project]`` table, which Poetry may have
    completed. ``"poetry"`` is ``[tool.poetry]`` alone.
    """
    data = _document(package_path).data
    if get_dict(data, "project"):
        return "project"
    return "poetry" if get_dict(get_dict(data, "tool"), "poetry") else "none"


def get_dev_dependencies(package_path: Path) -> list[str]:
    """Return requirement strings from development-only dependency tables.

    These can explain an import but are never installed for a user of the
    package. The tables read are ``[dependency-groups]``, the Poetry groups,
    the hatch environments, and the Poetry, uv and pdm ``dev-dependencies``.
    The result is a new list in file order, without duplicates, extras or pip
    options (``-e .``).
    """
    return list(_document(package_path).dev_dependencies)


# ── Typed getters ─────────────────────────────────────────────────────
# A *table* that is not a mapping reads as an empty one in all of these.


@overload
def get_str(table: object, key: str) -> str | None: ...
@overload
def get_str(table: object, key: str, default: str) -> str: ...
def get_str(table: object, key: str, default: str | None = None) -> str | None:
    """Return ``table[key]`` if it is a string, otherwise *default*."""
    value = table.get(key) if isinstance(table, Mapping) else None
    return value if isinstance(value, str) else default


def get_list(table: object, key: str) -> list[Any]:
    """Return ``table[key]`` if it is a list, otherwise ``[]``."""
    value = table.get(key) if isinstance(table, Mapping) else None
    return value if isinstance(value, list) else []


def get_str_list(table: object, key: str) -> list[str]:
    """Return the string entries of the list at ``table[key]``, as a new list."""
    return [item for item in get_list(table, key) if isinstance(item, str)]


def get_dict(table: object, key: str) -> dict[str, Any]:
    """Return ``table[key]`` if it is a table, otherwise ``{}``."""
    value = table.get(key) if isinstance(table, Mapping) else None
    return value if isinstance(value, dict) else {}


def get_str_dict(table: object, key: str) -> dict[str, str]:
    """Return the string-to-string entries of the table at ``table[key]``."""
    items = get_dict(table, key).items()
    return {k: v for k, v in items if isinstance(k, str) and isinstance(v, str)}


def get_number(
    table: object,
    key: str,
    default: float,
    *,
    minimum: float = -math.inf,
    maximum: float = math.inf,
) -> float:
    """Return ``table[key]`` if it is a finite number, otherwise *default*.

    Booleans, numeric strings, NaN and infinity are not numbers here. The
    result, *default* included, is kept within *minimum* and *maximum*.
    """
    value = table.get(key) if isinstance(table, Mapping) else None
    number: float = default
    if isinstance(value, int) and not isinstance(value, bool):
        number = value
    elif isinstance(value, float) and math.isfinite(value):
        number = value
    return min(max(number, minimum), maximum)


def is_dynamic(project: object, field: str) -> bool:
    """Return whether *field* is listed in the ``dynamic`` array of *project*."""
    return field in get_str_list(project, "dynamic")


# ── Versions, specifiers and requirements ─────────────────────────────


def parse_version(value: object) -> Version | None:
    """Parse a PEP 440 version, or return ``None`` if *value* is not one."""
    try:
        return Version(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _comparable(specifiers: SpecifierSet) -> bool:
    """Return whether every clause holds a version that can be compared.

    ``packaging`` reads the version of some clauses only when it compares
    them, and fails then on a number too long for ``int()``.
    """
    return all(
        clause.operator == "==="
        or parse_version(clause.version.removesuffix(".*")) is not None
        for clause in specifiers
    )


def parse_specifier_set(value: object) -> SpecifierSet | None:
    """Parse a PEP 440 specifier set such as ``requires-python``, or return ``None``.

    An empty string gives the set that allows every version. A set that is
    returned can be compared with any version without raising.
    """
    if not isinstance(value, str):
        return None
    try:
        specifiers = SpecifierSet(value)
    except ValueError:
        return None
    return specifiers if _comparable(specifiers) else None


def parse_requirement(value: object) -> Requirement | None:
    """Parse a PEP 508 requirement, or return ``None`` (as for ``-e .``)."""
    if not isinstance(value, str):
        return None
    try:
        requirement = Requirement(value)
    except (ValueError, RecursionError):  # deeply nested marker parentheses
        return None
    return requirement if _comparable(requirement.specifier) else None


# ── Poetry ────────────────────────────────────────────────────────────

_NAME_RE = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")
_AUTHOR_RE = re.compile(r"([^<>]*)<([^<>]+)>")
# One clause of a Poetry constraint: an optional operator, then a version.
_CLAUSE_RE = re.compile(
    r"\s*(\^|~=|~|>=|<=|==|!=|>|<|=)?\s*([0-9A-Za-z*][0-9A-Za-z.*+!_-]*)\s*,?"
)

# Extra that collects Poetry dependencies marked ``optional = true`` which no
# entry of [tool.poetry.extras] names.
POETRY_OPTIONAL_EXTRA = "poetry-optional"


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _parse_poetry_authors(authors: object) -> list[dict[str, str]]:
    """Convert Poetry ``authors`` (``"Name <email>"`` strings) to PEP 621 form."""
    people: list[dict[str, str]] = []
    for entry in authors if isinstance(authors, list) else []:
        if isinstance(entry, str):
            match = _AUTHOR_RE.fullmatch(entry.strip())
            name, email = match.groups() if match else (entry, "")
            person = {"name": name.strip(), "email": email.strip()}
            people.append({key: text for key, text in person.items() if text})
    return [person for person in people if person]


def _translate_clause(op: str | None, version: str, python: bool) -> str | None:
    """Translate one clause. ``""`` allows everything, ``None`` is unreadable."""
    if op == "=":
        op = "=="
    if version.endswith((".x", ".X")):
        # Poetry reads "1.x" as the wildcard "1.*".
        version = f"{version[:-2]}.*"
    if version == "*":
        return "" if op is None else None
    if version.endswith(".*"):
        return f"{op or '=='}{version}" if op in (None, "==", "!=") else None
    parsed = parse_version(version)
    if parsed is None:
        return None
    release = parsed.release
    if op == "^":
        # Up to the next change of the leftmost non-zero part.
        index = next((i for i, part in enumerate(release) if part), len(release) - 1)
        upper = release[:index] + (release[index] + 1,)
    elif op == "~" or (op is None and python and len(release) == 1):
        # Up to the next minor version, or the next major one for "~1".
        head = release[:2]
        upper = head[:-1] + (head[-1] + 1,)
    elif op is None and python and len(release) == 2:
        # Poetry reads a bare "3.9" for Python as the whole 3.9 series.
        return f"=={version}.*"
    else:
        return f"{op or '=='}{version}"
    return f">={version},<{'.'.join(map(str, upper))}"


def _translate_range(text: str, python: bool) -> str | None:
    """Translate a Poetry constraint without ``||`` to a PEP 440 specifier set."""
    clauses: list[str] = []
    position = 0
    while position < len(text):
        match = _CLAUSE_RE.match(text, position)
        clause = _translate_clause(match[1], match[2], python) if match else None
        if match is None or clause is None:
            return None
        if clause:
            clauses.append(clause)
        position = match.end()
    result = ",".join(clauses)
    return result if parse_specifier_set(result) is not None else None


def _lower_bound(specifier: str) -> Version | None:
    """Return the highest lower bound a translated specifier set states, if any."""
    bounds = [
        Version(clause.version.removesuffix(".*"))
        for clause in SpecifierSet(specifier)
        if clause.operator in (">=", ">", "==", "~=")
    ]
    return max(bounds, default=None)


def poetry_constraint_to_pep440(
    constraint: object, *, python: bool = False
) -> str | None:
    """Translate a Poetry version constraint to a PEP 440 specifier set.

    Handles caret (``^1.2``), tilde (``~1.2``), wildcard (``1.2.*``) and bare
    versions, PEP 440 operators, and clauses separated by commas or spaces.
    PEP 440 has no "or", so ``a || b`` gives the lowest lower bound alone
    (``"^3.2 || ^4.0"`` gives ``">=3.2"``), which allows at least what the
    original allows. With *python*, a bare ``"3.9"`` is the whole 3.9 series,
    as Poetry reads its ``python`` constraint. Elsewhere it is exact.

    Returns:
        A specifier set, ``""`` when every version is allowed (``"*"``), or
        ``None`` when the constraint cannot be translated.
    """
    if not isinstance(constraint, str):
        return None
    ranges: list[str] = []
    for part in re.split(r"\|\|?", constraint):
        translated = _translate_range(part.strip(), python) if part.strip() else None
        if translated is None:
            return None
        ranges.append(translated)
    if len(ranges) == 1:
        return ranges[0]
    bounds = [_lower_bound(item) for item in ranges]
    # An alternative without a lower bound excludes nothing.
    stated = [bound for bound in bounds if bound is not None]
    return f">={min(stated)}" if len(stated) == len(bounds) else ""


def _entry_requirement(name: str, entry: dict[str, Any]) -> str:
    """Turn one Poetry dependency table into a PEP 508 requirement string."""
    extras = [e for e in get_str_list(entry, "extras") if _NAME_RE.fullmatch(e)]
    head = f"{name}[{','.join(extras)}]" if extras else name
    git, url, path = (get_str(entry, key) for key in ("git", "url", "path"))
    candidates: list[str] = []
    if git:
        candidates.append(f"{head} @ {git if git.startswith('git+') else 'git+' + git}")
    elif url:
        candidates.append(f"{head} @ {url}")
    elif path:
        # A stand-in URL. It only marks the entry as a direct reference.
        posix = path.replace("\\", "/").lstrip("/")
        candidates.append(f"{head} @ file:///{posix}")
    else:
        specifier = poetry_constraint_to_pep440(entry.get("version"))
        markers = get_str(entry, "markers")
        if specifier and markers:
            candidates.append(f"{head}{specifier} ; {markers}")
        if specifier:
            candidates.append(f"{head}{specifier}")
    candidates.append(head)
    # The fullest form that is a valid requirement. The name alone always is.
    return next((c for c in candidates if parse_requirement(c) is not None), name)


def poetry_dependency_to_requirements(name: object, spec: object) -> list[str]:
    """Convert one Poetry dependency entry to PEP 508 requirement strings.

    *spec* is a constraint string, a table, or a list of tables, which gives
    one requirement for each. A ``git``, ``url`` or ``path`` entry becomes a
    direct reference (``name @ ...``). A value that cannot be translated
    falls back to the bare name, so the dependency still counts as declared.
    The result is empty when *name* is not a valid distribution name.
    """
    if not isinstance(name, str) or not _NAME_RE.fullmatch(name.strip()):
        return []
    name = name.strip()
    requirements = [
        _entry_requirement(name, {"version": item} if isinstance(item, str) else item)
        for item in (spec if isinstance(spec, list) else [spec])
        if isinstance(item, (str, dict))
    ]
    return list(dict.fromkeys(requirements)) or [name]


def _table_requirements(table: object) -> list[str]:
    """Return the requirements of a Poetry dependency table, less ``python``."""
    if not isinstance(table, dict):
        return []
    return [
        requirement
        for name, spec in table.items()
        if name != "python"
        for requirement in poetry_dependency_to_requirements(name, spec)
    ]


def _is_optional(spec: object) -> bool:
    items = spec if isinstance(spec, list) else [spec]
    return any(isinstance(i, dict) and i.get("optional") is True for i in items)


def _synthesize_from_poetry(poetry: dict[str, Any]) -> dict[str, Any]:
    """Build a PEP 621-shaped ``[project]`` dict from a ``[tool.poetry]`` table."""
    project: dict[str, Any] = {
        key: poetry[key]
        for key in ("name", "version", "description", "readme", "license", "keywords")
        if key in poetry
    }
    if isinstance(poetry.get("classifiers"), list):
        project["classifiers"] = poetry["classifiers"]
    for key in ("authors", "maintainers"):
        people = _parse_poetry_authors(poetry.get(key))
        if people:
            project[key] = people

    # Poetry keeps requires-python under [tool.poetry.dependencies].python.
    deps = get_dict(poetry, "dependencies")
    python = deps.get("python")
    if isinstance(python, dict):
        python = python.get("version")
    if isinstance(python, str) and python.strip():
        translated = poetry_constraint_to_pep440(python, python=True)
        if translated is None:
            # Keep the text as written, so a check can say it is unreadable.
            project["requires-python"] = python.strip()
        elif translated:
            project["requires-python"] = translated

    required: list[str] = []
    optional: dict[str, list[str]] = {}
    for name, spec in deps.items():
        if name == "python":
            continue
        requirements = poetry_dependency_to_requirements(name, spec)
        if requirements and _is_optional(spec):
            optional[_normalize_name(name)] = requirements
        else:
            required += requirements
    if required:
        project["dependencies"] = required

    # Optional dependencies are installed through [tool.poetry.extras].
    extras: dict[str, list[str]] = {}
    used: set[str] = set()
    for extra, members in get_dict(poetry, "extras").items():
        if not isinstance(members, list):
            continue
        extras[extra] = []
        for member in (m.strip() for m in members if isinstance(m, str)):
            key = _normalize_name(member)
            used.add(key)
            if key in optional:
                extras[extra] += optional[key]
            elif _NAME_RE.fullmatch(member):
                extras[extra].append(member)
    leftover = [r for key, reqs in optional.items() if key not in used for r in reqs]
    if leftover:
        extras.setdefault(POETRY_OPTIONAL_EXTRA, []).extend(leftover)
    if extras:
        project["optional-dependencies"] = extras

    urls = {
        label: poetry[label.lower()]
        for label in ("Homepage", "Repository", "Documentation")
        if isinstance(poetry.get(label.lower()), str)
    }
    urls.update(get_str_dict(poetry, "urls"))
    if urls:
        project["urls"] = urls
    return project


def _effective_project(data: dict[str, Any]) -> dict[str, Any]:
    """Build the table :func:`get_effective_project_table` returns."""
    poetry = get_dict(get_dict(data, "tool"), "poetry")
    return {**_synthesize_from_poetry(poetry), **get_dict(data, "project")}


def _dev_dependencies(data: dict[str, Any]) -> list[str]:
    """Build the list :func:`get_dev_dependencies` returns."""
    tool = get_dict(data, "tool")
    poetry = get_dict(tool, "poetry")
    groups: list[object] = list(get_dict(data, "dependency-groups").values())
    groups.append(_table_requirements(poetry.get("dev-dependencies")))
    for group in get_dict(poetry, "group").values():
        groups.append(_table_requirements(get_dict(group, "dependencies")))
    for env in get_dict(get_dict(tool, "hatch"), "envs").values():
        groups += [get_list(env, "dependencies"), get_list(env, "extra-dependencies")]
    groups.append(get_dict(tool, "uv").get("dev-dependencies"))
    groups += get_dict(get_dict(tool, "pdm"), "dev-dependencies").values()

    found: dict[str, None] = {}
    for group in groups:
        for item in group if isinstance(group, list) else []:
            # A dash starts a pip option such as "-e .".
            if isinstance(item, str) and item.strip()[:1] not in ("", "-"):
                found.setdefault(item.strip())
    return list(found)


# ── setup.cfg and setup.py ────────────────────────────────────────────

# read_ini() reads the file itself, because it cuts a long file off at the cap
# where the cache's own read would refuse it.
_setup_cfg_cache: FileCache[configparser.RawConfigParser | None] = FileCache(
    lambda path, read: None if read.missing else read_ini(path), MAX_PROSE_BYTES
)


def get_setup_cfg_value(package_path: Path, section: str, option: str) -> str | None:
    """Read one option from ``setup.cfg``, whatever the encoding of the file.

    Returns the value without surrounding whitespace, or ``None`` when the
    file, section or option is missing, the value is empty, or the file is
    not valid INI syntax.
    """
    parser = _setup_cfg_cache.get(package_path / "setup.cfg")
    value = parser.get(section, option, fallback=None) if parser is not None else None
    return value.strip() or None if value else None


class _SetupPyDynamic:
    """Marks a ``setup()`` keyword whose value is computed when setup.py runs."""

    def __repr__(self) -> str:
        return "<computed in setup.py>"


SETUP_PY_DYNAMIC: Any = _SetupPyDynamic()
"""Value of a ``setup()`` keyword that is not a literal. Test for it with ``is``."""


def get_setup_py_keywords(package_path: Path) -> dict[str, Any]:
    """Read the keyword arguments of the ``setup()`` call in ``setup.py``.

    The file is parsed, never run. A keyword whose value is a literal, or a
    module-level name assigned one, maps to that value, the last one when
    the name is assigned twice. Any other keyword maps to
    :data:`SETUP_PY_DYNAMIC`. So does a name that the file changes after
    binding it, as with ``packages.append()`` or ``packages += [...]``, and
    a name that it binds anywhere else, such as in an ``if`` block, a loop,
    a function or a class. Scopes are not followed, so a local variable of
    the same name counts. ``setup(**options)`` is followed when ``options``
    is a dict display or a ``dict(...)`` call. When keywords may be set in
    a way that cannot be followed, the key ``"**"`` is present and maps to
    :data:`SETUP_PY_DYNAMIC`. The result is a new dict, empty when no
    ``setup()`` call can be read.
    """
    tree = parse_file(package_path / "setup.py")
    return _setup_keywords(tree) if tree is not None else {}


def _mapping_items(node: ast.expr) -> tuple[list[tuple[str, ast.expr]], bool]:
    """Return the string-keyed items of a dict display or ``dict(...)`` call.

    The second value is ``False`` when some entries could not be listed.
    """
    if isinstance(node, ast.Dict):
        pairs: list[tuple[object, ast.expr]] = [
            (key.value if isinstance(key, ast.Constant) else None, value)
            for key, value in zip(node.keys, node.values, strict=True)
        ]
    elif (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "dict"
        and not node.args
    ):
        pairs = [(keyword.arg, keyword.value) for keyword in node.keywords]
    else:
        return [], False
    items = [(key, value) for key, value in pairs if isinstance(key, str)]
    return items, len(items) == len(pairs)


def _setup_keywords(tree: ast.Module) -> dict[str, Any]:
    """Collect the keywords of every ``setup()`` call. The first one wins."""
    names: dict[str, ast.expr] = {}  # module-level assignments
    elsewhere: Counter[str] = Counter()  # bindings of a name that are not those
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1:
            target, value = statement.targets[0], statement.value
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            target, value = statement.target, statement.value
        else:
            continue
        if isinstance(target, ast.Name):
            names[target.id] = value
            elsewhere[target.id] -= 1

    calls: list[ast.Call] = []
    uses: Counter[str] = Counter()  # how often each name appears in the file
    changed: set[str] = set()  # names changed after they were bound
    for node in ast.walk(tree):
        receiver: object = None
        if isinstance(node, ast.Name):
            uses[node.id] += 1
            # ``if ...: packages = [...]`` binds the name again in a block.
            elsewhere[node.id] += isinstance(node.ctx, ast.Store)
        elif isinstance(node, ast.Call):
            # ``setup(...)`` or ``setuptools.setup(...)``.
            callee = getattr(node.func, "id", None) or getattr(node.func, "attr", None)
            if callee == "setup":
                calls.append(node)
            elif callee in ("pop", "popitem", "setdefault"):
                # ``last = packages.pop()`` changes the list where it stands.
                receiver = getattr(node.func, "value", None)
        elif isinstance(node, ast.AugAssign):
            # ``packages += [...]``
            receiver = node.target
        elif isinstance(node, ast.Subscript) and not isinstance(node.ctx, ast.Load):
            # ``extras["all"] = [...]``. Reading an item changes nothing.
            receiver = node.value
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            # ``packages.append(...)`` on a line of its own.
            receiver = getattr(node.value.func, "value", None)
        if isinstance(receiver, ast.Name):
            changed.add(receiver.id)

    def literal(node: ast.expr) -> Any:
        if isinstance(node, ast.Name):
            # A name changed or bound again is not what setup() gets.
            if node.id in changed or elsewhere[node.id]:
                return SETUP_PY_DYNAMIC
            node = names.get(node.id, node)
        try:
            return ast.literal_eval(node)
        except (ValueError, TypeError, SyntaxError, MemoryError, RecursionError):
            return SETUP_PY_DYNAMIC

    keywords: dict[str, Any] = {}
    for call in sorted(calls, key=lambda call: (call.lineno, call.col_offset)):
        if call.args:
            keywords.setdefault("**", SETUP_PY_DYNAMIC)
        for keyword in call.keywords:
            if keyword.arg is not None:
                keywords.setdefault(keyword.arg, literal(keyword.value))
                continue
            source = keyword.value
            followed = True
            if isinstance(source, ast.Name):
                # Its assignment and this use make two. A mapping named
                # anywhere else may have been changed after it was built.
                followed = uses[source.id] == 2
                source = names.get(source.id, source)
            items, complete = _mapping_items(source)
            for key, value in items:
                keywords.setdefault(key, literal(value))
            if not (followed and complete):
                keywords.setdefault("**", SETUP_PY_DYNAMIC)
    return keywords
