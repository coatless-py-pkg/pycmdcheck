"""pycmdcheck - Python package quality checker.

A Python equivalent of R's `R CMD check` for validating Python packages.

Example usage:

    from pycmdcheck import check

    # Check current directory
    report = check(".")

    # Check specific package
    report = check("/path/to/package")

    # Check with options
    report = check(
        ".",
        checks=["metadata", "tests"],
        skip=["linting"],
    )

    # Check results
    if report.passed:
        print("All checks passed!")
    else:
        for result in report.results:
            print(f"{result.status}: {result.name} - {result.message}")
"""

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _get_version
from typing import Any

from pycmdcheck.results import CheckResult, CheckStatus, Report
from pycmdcheck.runner import run_checks

try:
    __version__ = _get_version("pycmdcheck")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "check",
    "run_checks",
    "CheckResult",
    "CheckStatus",
    "Report",
    "__version__",
]


def check(
    path: str = ".",
    *,
    checks: list[str] | None = None,
    skip: list[str] | None = None,
    config: dict[str, Any] | None = None,
    parallel: bool = True,
    fail_fast: bool = False,
) -> Report:
    """Check a Python package for quality issues.

    This is the main entry point for programmatic use of pycmdcheck.
    It discovers available checks via entry points, loads configuration
    from pyproject.toml, and runs the enabled checks.

    Args:
        path: Path to the package directory. Defaults to current directory.
        checks: List of check names to run. If None, runs all enabled checks
            from configuration. Available checks: build, dependencies, docs,
            formatting, imports, license, linting, metadata, py_typed,
            structure, tests, typing, version.
        skip: List of check names to skip, even if enabled in configuration.
        config: Pre-loaded configuration dictionary. If None, configuration
            is loaded from the package's pyproject.toml.
        parallel: Whether to run checks in parallel. Defaults to True.
        fail_fast: Whether to stop after the first ERROR. Defaults to False.

    Returns:
        A Report object containing all check results. Use report.passed to
        check if all checks passed, or iterate report.results for details.

    Examples:
        Basic usage - check current directory:

        >>> from pycmdcheck import check  # doctest: +SKIP
        >>> report = check(".")
        >>> report.passed
        True

        Run only specific checks:

        >>> report = check(".", checks=["metadata", "tests"])  # doctest: +SKIP
    """
    report = run_checks(
        package_path=path,
        checks=checks,
        skip=skip,
        config=config,
        parallel=parallel,
        fail_fast=fail_fast,
    )

    return report
