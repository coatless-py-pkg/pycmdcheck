"""Context-aware import collection.

Used by the dependencies check. Each absolute import is recorded as either
hard (it runs whenever the module is imported) or guarded (it sits under
``if TYPE_CHECKING``, a version, platform or ``__main__`` test, a ``try`` or
``with suppress(...)`` that handles a failed import, or inside a function).
Only a hard import can be an undeclared dependency. A guarded one only shows
that a dependency is used.
"""

import ast
import sys
from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from pycmdcheck.ast_cache import parse_file
from pycmdcheck.constants import STDLIB_MODULE_NAMES

# Names in an ``if`` test that make the imports below it conditional.
_GUARD_NAMES = frozenset({"TYPE_CHECKING", "version_info", "platform", "__name__"})

# Exception names whose handler catches a failed import.
_IMPORT_ERRORS = frozenset(
    {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
)

# Callables that import the module named by their first argument.
_DYNAMIC_IMPORTERS = frozenset({"import_module", "__import__"})


@dataclass
class ImportScan:
    """Top-level module names imported by a set of files.

    Attributes:
        hard: Names imported unconditionally at module level, each mapped to
            the first file that does so.
        guarded: Names imported in a guarded context in at least one file.
        skipped: Files that could not be parsed.
        scanned: Number of files that were parsed.
    """

    hard: dict[str, Path] = field(default_factory=dict)
    guarded: set[str] = field(default_factory=set)
    skipped: list[Path] = field(default_factory=list)
    scanned: int = 0

    @property
    def used(self) -> set[str]:
        """Every imported name, in any context."""
        return self.guarded | set(self.hard)


def _identifier(node: ast.AST) -> str:
    """Return the last name of ``name`` or ``module.name``, otherwise ``""``."""
    if isinstance(node, ast.Name):
        return node.id
    return node.attr if isinstance(node, ast.Attribute) else ""


def _is_guard_test(test: ast.expr) -> bool:
    """Return whether an ``if`` test names one of ``_GUARD_NAMES``."""
    return any(_identifier(node) in _GUARD_NAMES for node in ast.walk(test))


def _handles_failed_import(node: ast.AST) -> bool:
    """Return whether a ``try`` or ``with suppress(...)`` handles a failed import."""
    caught: list[ast.expr | None] = []
    if isinstance(node, ast.Try):
        caught = [handler.type for handler in node.handlers]
    elif isinstance(node, ast.With | ast.AsyncWith):
        for item in node.items:
            call = item.context_expr
            if isinstance(call, ast.Call) and _identifier(call.func) == "suppress":
                caught += call.args
    for exception in caught:
        if exception is None:
            return True
        types = exception.elts if isinstance(exception, ast.Tuple) else [exception]
        if any(_identifier(item) in _IMPORT_ERRORS for item in types):
            return True
    return False


def collect_imports(tree: ast.Module) -> tuple[set[str], set[str]]:
    """Collect the top-level names a module imports, split by context.

    Relative imports are left out. Calls of ``importlib.import_module`` and
    ``__import__`` with a literal name count as guarded imports.

    Args:
        tree: The parsed module.

    Returns:
        A ``(hard, guarded)`` pair of name sets. A name can be in both.
    """
    hard: set[str] = set()
    guarded: set[str] = set()
    optional: set[str] = set()
    # Only statements can hold an import, so expressions are not entered.
    # Each node carries its context: 0 hard, 1 guarded, 2 failed import handled.
    stack: list[tuple[ast.AST, int]] = [(tree, 0)]
    while stack:
        node, context = stack.pop()
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module] if node.module and node.level == 0 else []
        else:
            if _handles_failed_import(node):
                context = 2
            elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or (
                isinstance(node, ast.If) and _is_guard_test(node.test)
            ):
                context = max(context, 1)
            stack.extend(
                (child, context)
                for child in ast.iter_child_nodes(node)
                if isinstance(child, ast.stmt | ast.excepthandler | ast.match_case)
            )
            continue
        names = {module.partition(".")[0] for module in modules}
        (guarded if context else hard).update(names)
        if context == 2:
            optional.update(names)
    # A module imported once where its absence is handled is optional in the
    # whole file, as in ``if has_simplejson: from simplejson import ...``.
    hard -= optional

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and node.args
            and _identifier(node.func) in _DYNAMIC_IMPORTERS
        ):
            target = node.args[0]
            if isinstance(target, ast.Constant) and isinstance(target.value, str):
                name = target.value.partition(".")[0]
                if name.isidentifier():
                    guarded.add(name)
    return hard, guarded


def scan_imports(files: Iterable[Path]) -> ImportScan:
    """Collect the imports of several files.

    Args:
        files: The ``.py`` files to read.

    Returns:
        The combined scan. A file that cannot be parsed is listed in
        ``skipped`` and adds nothing else.
    """
    scan = ImportScan()
    for path in files:
        # Each file is read once per check, so nothing is gained by keeping
        # every tree of a large project in memory.
        tree = parse_file(path, cache=False)
        if tree is None:
            scan.skipped.append(path)
            continue
        scan.scanned += 1
        hard, guarded = collect_imports(tree)
        for name in sorted(hard):
            scan.hard.setdefault(name, path)
        scan.guarded |= guarded
    return scan


def third_party(names: Iterable[str], local: Collection[str]) -> set[str]:
    """Return the names that are neither standard library nor first-party.

    A module counts as standard library if any supported CPython release
    ships it, so a version-guarded ``import tomllib`` is never third-party.

    Args:
        names: Top-level import names.
        local: First-party names, from ``PackageLayout.local_names()``.
    """
    stdlib = STDLIB_MODULE_NAMES | sys.stdlib_module_names
    return {name for name in names if name not in stdlib and name not in local}
