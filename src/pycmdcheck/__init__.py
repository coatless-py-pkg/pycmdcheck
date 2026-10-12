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
from pathlib import Path
from typing import Any

from pycmdcheck.results import CheckResult, CheckStatus, Finding, Report
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
    "Finding",
    "Report",
    "__version__",
]


def check(
    path: str | Path = ".",
    *,
    checks: list[str] | None = None,
    skip: list[str] | None = None,
    config: dict[str, Any] | None = None,
    parallel: bool = True,
    fail_fast: bool = False,
    profile: str | None = None,
) -> Report:
    """Check a Python package for quality issues.

    This is the main entry point for programmatic use of pycmdcheck.
    It discovers available checks via entry points, loads configuration
    from pyproject.toml, and runs the enabled checks.

    Args:
        path: Path to the package directory. Defaults to current directory.
        checks: Names of checks to run. If None, runs all enabled checks from
            configuration. The available check names are discovered dynamically
            via entry points; run ``pycmdcheck --list`` to see the current set.
            An unknown name is an error. A deprecated check (``imports``) only
            runs when it is named here.
        skip: List of check names to skip, even if enabled in configuration.
            An unknown name is ignored with a logged warning.
        config: Pre-loaded configuration dictionary. If None, configuration
            is loaded from the package's pyproject.toml.
        parallel: Whether to run checks in parallel. Defaults to True.
        fail_fast: Whether to stop after the first ERROR. Defaults to False.
        profile: Name of a profile (see ``pycmdcheck --list-profiles``), with
            the same meaning as the ``--profile`` option. Its options win over
            the configuration, and its check set is used when ``checks`` is
            not given.

    Returns:
        A Report object containing all check results. Use report.passed to
        check if all checks passed, or iterate report.results for details.

    Raises:
        FileNotFoundError: If ``path`` does not exist.
        NotADirectoryError: If ``path`` is not a directory.
        ValueError: If ``profile`` is not a known profile name, or if
            ``checks`` holds a name that no registered check has.

    Examples:
        Basic usage - check current directory:

        >>> from pycmdcheck import check  # doctest: +SKIP
        >>> report = check(".")  # doctest: +SKIP
        >>> report.passed  # doctest: +SKIP
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
        profile=profile,
    )

    return report
