"""Check runner that orchestrates check execution."""

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from pycmdcheck.config import get_check_config, is_check_enabled, load_config
from pycmdcheck.constants import MAX_WORKERS
from pycmdcheck.discovery import discover_checks
from pycmdcheck.results import CheckResult, CheckStatus, Report

logger = logging.getLogger(__name__)


def run_checks(
    package_path: str | Path,
    checks: list[str] | None = None,
    skip: list[str] | None = None,
    config: dict[str, Any] | None = None,
    parallel: bool = True,
    fail_fast: bool = False,
) -> Report:
    """Run checks on a package.

    This is the core function that orchestrates check discovery and execution.
    It loads configuration, discovers available checks via entry points,
    and runs them either in parallel or sequentially.

    Args:
        package_path: Path to the package directory to check. Can be a string
            or Path object. Will be resolved to an absolute path.
        checks: List of specific check names to run. If None, runs all checks
            that are enabled in the configuration. Available built-in checks:
            build, dependencies, docs, formatting, imports, license, linting,
            metadata, py_typed, structure, tests, typing, version.
        skip: List of check names to skip, even if they would otherwise run.
        config: Pre-loaded configuration dictionary. If None, configuration
            is loaded from the package's pyproject.toml file.
        parallel: Whether to run checks in parallel using threads. Defaults
            to True. Set to False for deterministic ordering or debugging.
        fail_fast: Whether to stop after the first ERROR result. Defaults
            to False.

    Returns:
        A Report object containing all check results. The report includes
        the package path and a list of CheckResult objects.

    Examples:
        Run all enabled checks:

        >>> report = run_checks("/path/to/package")
        >>> report.passed
        True

        Run specific checks only:

        >>> report = run_checks(".", checks=["metadata", "tests"])
        >>> len(report.results)
        2

        Run sequentially for debugging:

        >>> report = run_checks(".", parallel=False)
    """
    package_path = Path(package_path).resolve()
    skip = skip or []

    # Load configuration
    if config is None:
        config = load_config(package_path)

    # Discover available checks
    available_checks = discover_checks()

    # Determine which checks to run
    checks_to_run: list[str] = []
    if checks:
        # Run only specified checks
        for name in checks:
            if name in available_checks:
                checks_to_run.append(name)
    else:
        # Run all enabled checks
        for name in available_checks:
            if is_check_enabled(config, name):
                checks_to_run.append(name)

    # Remove skipped checks
    checks_to_run = [c for c in checks_to_run if c not in skip]

    # Create report
    report = Report(package_path=str(package_path))

    if not checks_to_run:
        return report

    # Run checks
    if parallel and len(checks_to_run) > 1:
        results = _run_parallel(
            package_path, checks_to_run, available_checks, config, fail_fast=fail_fast
        )
    else:
        results = _run_sequential(
            package_path,
            checks_to_run,
            available_checks,
            config,
            fail_fast=fail_fast,
        )

    for result in results:
        report.add(result)

    return report


def _run_sequential(
    package_path: Path,
    check_names: list[str],
    available_checks: dict[str, type],
    config: dict[str, Any],
    fail_fast: bool = False,
) -> list[CheckResult]:
    """Run checks sequentially."""
    results: list[CheckResult] = []

    for name in check_names:
        result = _run_single_check(package_path, name, available_checks, config)
        results.append(result)
        if fail_fast and result.status == CheckStatus.ERROR:
            break

    return results


def _run_parallel(
    package_path: Path,
    check_names: list[str],
    available_checks: dict[str, type],
    config: dict[str, Any],
    fail_fast: bool = False,
) -> list[CheckResult]:
    """Run checks in parallel using threads."""
    results: list[CheckResult] = []

    with ThreadPoolExecutor(max_workers=min(len(check_names), MAX_WORKERS)) as executor:
        futures = {
            executor.submit(
                _run_single_check, package_path, name, available_checks, config
            ): name
            for name in check_names
        }

        for future in as_completed(futures):
            try:
                result = future.result()
                results.append(result)
                if fail_fast and result.status == CheckStatus.ERROR:
                    for f in futures:
                        f.cancel()
                    break
            except Exception as e:
                name = futures[future]
                logger.exception("Check %s raised exception in thread", name)
                results.append(
                    CheckResult(
                        name=name,
                        status=CheckStatus.ERROR,
                        message=f"Check failed with exception: {e}",
                    )
                )
                if fail_fast:
                    for f in futures:
                        f.cancel()
                    break

    # Sort by original order
    name_order = {name: i for i, name in enumerate(check_names)}
    results.sort(key=lambda r: name_order.get(r.name, 999))

    return results


def _run_single_check(
    package_path: Path,
    name: str,
    available_checks: dict[str, type],
    config: dict[str, Any],
) -> CheckResult:
    """Run a single check."""
    check_class = available_checks.get(name)
    if not check_class:
        return CheckResult(
            name=name,
            status=CheckStatus.ERROR,
            message=f"Check '{name}' not found",
        )

    check_config = get_check_config(config, name)

    logger.debug("Starting check: %s", name)
    start_time = time.time()
    try:
        check_instance = check_class()
        result: CheckResult = check_instance.run(package_path, check_config)
        if result.name != name:
            logger.warning(
                "Check '%s' returned result with name='%s'; correcting",
                name,
                result.name,
            )
            result = CheckResult(
                name=name,
                status=result.status,
                message=result.message,
                details=result.details,
                duration=result.duration,
            )
        result.duration = time.time() - start_time
        logger.debug(
            "Check %s completed in %.2fs: %s",
            name,
            result.duration,
            result.status.value,
        )
        return result
    except Exception as e:
        logger.exception("Check %s raised exception", name)
        return CheckResult(
            name=name,
            status=CheckStatus.ERROR,
            message=f"Check raised exception: {e}",
            duration=time.time() - start_time,
        )
