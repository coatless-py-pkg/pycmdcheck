"""Version consistency check."""

import ast
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file, unparsed_files_detail
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.fileio import display_path, is_regular_file, plain_text
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.pyproject_reader import (
    get_effective_project_table,
    get_str,
    is_dynamic,
    parse_version,
    pyproject_status,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# Statements whose bodies still run at module level. ``TryStar`` is Python 3.11+.
_MODULE_LEVEL_BLOCKS = (
    ast.If,
    ast.Try,
    ast.With,
    ast.ExceptHandler,
    getattr(ast, "TryStar", ast.Try),
)


class VersionCheck(BaseCheck):
    """Check that __version__ in code matches pyproject.toml version."""

    name = "version"
    description = "Verify version consistency"
    rules = {
        "version.mismatch": (
            "__version__ in the code differs from the version in pyproject.toml"
        ),
    }
    hints = {
        "version.mismatch": (
            "Make __version__ in the code and version in pyproject.toml the same, "
            "or read __version__ with importlib.metadata.version()."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Compare __version__ in code with pyproject.toml version.

        ERROR only for two literal versions that differ. A project with
        nothing to compare is SKIPPED. The reason is "prerequisite-failed"
        when the metadata check reports what is missing (pyproject.toml, its
        ``[project]`` table or a static version), "parse-failed" when the
        file that should hold ``__version__`` could not be parsed, and
        "not-applicable" for a dynamic version, no importable module, no
        ``__version__`` or a ``__version__`` that is set at run time or is
        not a string.
        """
        # Distinguish "no pyproject.toml" from "pyproject present but no
        # [project] table" (e.g. legacy Poetry / setup.py metadata).
        status = pyproject_status(package_path)
        if status == "missing":
            return self._skipped(
                "No pyproject.toml found",
                "prerequisite-failed",
                "Version consistency not checked",
                "The metadata check reports the missing file",
            )
        if status == "unparseable":
            return self._skipped(
                "pyproject.toml could not be read",
                "prerequisite-failed",
                "The metadata check reports the reason",
            )

        project = get_effective_project_table(package_path)
        if not project:
            return self._skipped(
                "No [project] table",
                "prerequisite-failed",
                "Version consistency not checked",
                "The metadata check reports the missing [project] table",
            )

        if is_dynamic(project, "version"):
            return self._skipped(
                "Version is dynamic (set at build time)",
                "not-applicable",
                "version is listed in dynamic, so there is no static value to compare",
            )

        pyproject_version = get_str(project, "version", "").strip()
        if not pyproject_version:
            return self._skipped(
                "No static version in pyproject.toml",
                "prerequisite-failed",
                "The metadata check reports a missing or invalid version",
            )
        declared = plain_text(pyproject_version)

        # Find __version__ in code (package __init__.py or single-module file)
        layout = PackageLayout(package_path)
        version_source = self._find_version_source(layout)
        if version_source is None:
            if layout.primary_match == "guess":
                return self._skipped(
                    "Could not tell which package holds __version__",
                    "not-applicable",
                    "No package found here matches the project name",
                )
            return self._skipped(
                "No __init__.py or module found to check __version__",
                "not-applicable",
                "Looked for the project's package or single module under src/, "
                "lib/, python/ and the project root",
            )

        shown = display_path(version_source, package_path)
        code_version = self._extract_version(version_source)
        if code_version is None:
            if parse_file(version_source) is None:
                # The file was not read, so it may hold a version that differs.
                unparsed = unparsed_files_detail([version_source], package_path)
                return self._skipped(
                    "Could not read __version__ from code",
                    "parse-failed",
                    f"pyproject.toml version: {declared}",
                    unparsed or f"Could not parse {shown}",
                )
            return self._skipped(
                "No __version__ in code, nothing to compare",
                "not-applicable",
                f"pyproject.toml version: {declared}",
                f"{shown} assigns no __version__ at module level",
            )

        if code_version == self._DYNAMIC_VERSION:
            return self._skipped(
                "Version set dynamically (e.g. importlib.metadata)",
                "not-applicable",
                f"pyproject.toml version: {declared}",
                f"__version__ in {shown} is set at runtime",
            )
        if code_version == self._NOT_A_STRING:
            return self._skipped(
                "__version__ is not a string",
                "not-applicable",
                f"pyproject.toml version: {declared}",
                f"__version__ in {shown} has to be written as a string, such "
                'as "1.0", to be compared',
            )

        # Compare as PEP 440 versions ("1.0" equals "1.0.0"), or as text when
        # either side is not one.
        left, right = parse_version(pyproject_version), parse_version(code_version)
        if left is None or right is None:
            consistent = pyproject_version == code_version
        else:
            consistent = left == right
        in_code = plain_text(code_version)
        findings: list[Finding] = []
        if not consistent:
            findings.append(
                Finding(
                    "version.mismatch",
                    f"__version__ in {shown} is {in_code}, "
                    f"but pyproject.toml has {declared}",
                    CheckStatus.ERROR,
                )
            )
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=f"Version {declared} is consistent"
            if consistent
            else "Version mismatch",
            details=[
                f"pyproject.toml: {declared}",
                f"__version__ in {shown}: {in_code}",
            ],
            findings=findings,
        )

    # Sentinels returned when __version__ is not one string literal. The second
    # is for a value written out in the file, such as a number or a tuple.
    _DYNAMIC_VERSION = "<dynamic>"
    _NOT_A_STRING = "<not a string>"

    def _skipped(self, message: str, reason: str, *details: str) -> CheckResult:
        """Build the result for a project that has nothing to compare."""
        return CheckResult.skip(self.name, message, reason, details)

    @staticmethod
    def _find_version_source(layout: PackageLayout) -> Path | None:
        """Return the file that should declare ``__version__``.

        The ``__init__.py`` of the project's package, or the module itself
        for a single-module project. ``None`` when there is neither, or when
        the package was only guessed among several.
        """
        primary = layout.primary_candidate
        if primary is None or layout.primary_match == "guess":
            return None
        if primary.kind == "module":
            return primary.path
        if primary.package_dir is None:
            return None
        init_file = primary.package_dir / "__init__.py"
        return init_file if is_regular_file(init_file) else None

    def _extract_version(self, init_file: Path) -> str | None:
        """Extract __version__ from a Python file using AST.

        Only module-level assignments count, including those inside ``if``,
        ``try`` and ``with`` blocks but never those in a function or class.
        Returns the literal version string, the sentinel ``_NOT_A_STRING``
        if the value is a constant that is not a string or a tuple of
        constants (``1.0``, ``(1, 0)``), the sentinel ``_DYNAMIC_VERSION`` if
        any assignment is something else (a call such as
        ``importlib.metadata.version()``, a name, an import) or the literals
        differ, or ``None`` if no ``__version__`` assignment is found.
        """
        tree = parse_file(init_file)
        if tree is None:
            return None

        literals: set[str] = set()
        stack: list[ast.AST] = list(tree.body)
        while stack:
            node = stack.pop()
            if isinstance(node, _MODULE_LEVEL_BLOCKS):
                stack.extend(ast.iter_child_nodes(node))
            elif isinstance(node, ast.ImportFrom):
                # ``from ._version import __version__`` re-exports the version
                # from another module, which cannot be compared statically here.
                if any("__version__" in (a.name, a.asname) for a in node.names):
                    return self._DYNAMIC_VERSION
            elif isinstance(node, ast.AugAssign):
                # ``__version__ += ".dev0"`` changes the literal before it.
                target = node.target
                if isinstance(target, ast.Name) and target.id == "__version__":
                    return self._DYNAMIC_VERSION
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = (
                    node.targets if isinstance(node, ast.Assign) else [node.target]
                )
                if node.value is None or not any(
                    isinstance(t, ast.Name) and t.id == "__version__" for t in targets
                ):
                    continue
                value = node.value
                items = value.elts if isinstance(value, ast.Tuple) else [value]
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    literals.add(value.value)
                elif all(isinstance(item, ast.Constant) for item in items):
                    # Kept with the strings, so a string beside it is a difference.
                    literals.add(self._NOT_A_STRING)
                else:
                    return self._DYNAMIC_VERSION

        if len(literals) > 1:
            return self._DYNAMIC_VERSION
        return literals.pop() if literals else None
