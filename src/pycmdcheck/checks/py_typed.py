"""PEP 561 py.typed marker check.

This module provides the PyTypedCheck class which verifies that packages
using type annotations include a ``py.typed`` marker file as required by
PEP 561 for downstream type checkers to recognise the package as typed.
"""

import ast
from pathlib import Path
from typing import Any

from pycmdcheck.ast_cache import parse_file, unparsed_files_detail
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_TYPE_SCAN_FILES, PY_TYPED_MIN_ANNOTATED_RATIO
from pycmdcheck.fileio import display_path, is_regular_file
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe


class PyTypedCheck(BaseCheck):
    """Check for PEP 561 ``py.typed`` marker file.

    Looks at the project's own package, as found by
    :class:`~pycmdcheck.package_layout.PackageLayout`. If it has no
    ``py.typed`` marker, a sample of its modules and stubs is scanned, and
    the marker is suggested only when most public functions are annotated.

    Attributes:
        name: The check identifier (``"py_typed"``).
        description: Human-readable description of this check.
        rules: Every code the check can emit, with what the rule means.
        hints: The same codes, each with what to do about the finding.

    Examples:
        Run the py.typed check:

        >>> from pathlib import Path
        >>> check = PyTypedCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'py_typed'
    """

    name = "py_typed"
    description = "Check for PEP 561 py.typed marker"
    rules = {
        "py_typed.missing": (
            "A package with mostly annotated public functions has no py.typed marker"
        ),
    }
    hints = {
        "py_typed.missing": (
            "Add an empty file named py.typed to the package directory."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for py.typed marker in typed packages.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if ``py.typed`` is present or the package is stub-only
            - NOTE status if most public functions are annotated but there
              is no ``py.typed`` marker
            - SKIPPED status ("not-applicable") if no package directory is
              found, or the package is not clearly typed (marker not needed)
            - SKIPPED status ("parse-failed") if a sampled file could not be
              parsed and no public function was counted in the rest. Once
              a function was counted, the package is graded on the files
              that were read
        """
        layout = PackageLayout(package_path)
        primary = layout.primary_candidate
        package_dir = primary.package_dir if primary else None
        if primary is None or package_dir is None:
            return CheckResult.skip(
                self.name,
                "No package directory found",
                "not-applicable",
                [
                    "The project is a single-file module, and a py.typed marker"
                    " needs a package directory"
                    if primary
                    else "Looked in the project root, src/, lib/, python/ and any"
                    " source directory the build configuration names"
                ],
            )

        guessed = layout.primary_match == "guess"
        marked = [
            c.package_dir
            for c in layout.packages
            if c.package_dir and is_regular_file(c.package_dir / "py.typed")
        ]
        if guessed and marked and package_dir not in marked:
            # Several candidates and no name to choose by: any marker counts.
            package_dir = marked[0]
        shown = display_path(package_dir, package_path)
        details = [f"Package directory: {shown}"]

        if package_dir in marked:
            details.append("py.typed marker file present")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="py.typed marker present",
                details=details,
            )
        if primary.kind == "stub-only":
            details.append("A stub-only package needs no py.typed marker (PEP 561)")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="py.typed not needed (stub-only package)",
                details=details,
            )

        # Runtime modules of the package. A stub replaces the module beside it.
        stubs = [f for f in layout.stub_files() if primary.path in f.parents]
        stubbed = {f.with_suffix(".py") for f in stubs}
        modules = [
            f
            for f in layout.source_files()
            if primary.path in f.parents and f not in stubbed
        ]
        files = sorted(modules + stubs)
        # An evenly spaced sample, so that no one sub-package decides.
        count = min(len(files), MAX_TYPE_SCAN_FILES)
        sample = [files[i * len(files) // count] for i in range(count)]
        total = annotated = 0
        skipped: list[Path] = []
        for path in sample:
            tree = parse_file(path, cache=False)
            if tree is None:
                skipped.append(path)
                continue
            found, typed = self._count_functions(tree)
            total += found
            annotated += typed
        unparsed = unparsed_files_detail(skipped, package_path)
        if unparsed and not total:
            # A file was left out and no function was counted in the rest, so
            # nothing is known of the package.
            return CheckResult.skip(
                self.name,
                "Could not parse the package source",
                "parse-failed",
                [*details, unparsed],
            )
        details.append(
            f"Scanned {len(sample)} of {len(files)} files:"
            f" {annotated} of {total} public functions are annotated"
        )
        if unparsed:
            details.append(unparsed)

        if total and annotated / total >= PY_TYPED_MIN_ANNOTATED_RATIO:
            # A guessed directory is not named in the finding or in its hint,
            # which is then the general one.
            hint = None if guessed else f"Add an empty file named py.typed to {shown}."
            findings = [
                Finding(
                    "py_typed.missing",
                    f"{'The package' if guessed else shown} has no py.typed marker"
                    f" and {annotated} of {total} public functions are annotated",
                    CheckStatus.NOTE,
                    hint,
                )
            ]
            return CheckResult(
                name=self.name,
                status=most_severe(findings),
                message="Missing py.typed marker for typed package",
                details=details,
                findings=findings,
            )
        # No marker is expected, so there is nothing here to grade.
        return CheckResult.skip(
            self.name,
            "py.typed not needed (package is not clearly typed)",
            "not-applicable",
            details,
        )

    @staticmethod
    def _count_functions(tree: ast.Module) -> tuple[int, int]:
        """Count public functions and how many of them carry an annotation.

        Variable annotations (dataclass fields) and ``typing`` imports are
        not counted: they say little about whether the API is typed.

        Returns:
            A tuple of (public functions, annotated public functions).
        """
        total = annotated = 0
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node.name.startswith("_"):
                continue
            total += 1
            args = node.args
            params = [*args.posonlyargs, *args.args, *args.kwonlyargs]
            params += [a for a in (args.vararg, args.kwarg) if a is not None]
            if node.returns is not None or any(a.annotation for a in params):
                annotated += 1
        return total, annotated
