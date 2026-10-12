"""Check runner that orchestrates check execution."""

import dataclasses
import logging
import threading
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed, wait
from pathlib import Path
from typing import Any

from pycmdcheck.config import get_check_config, is_check_enabled, load_config
from pycmdcheck.constants import MAX_WORKERS
from pycmdcheck.discovery import discover_checks
from pycmdcheck.fileio import clear_all_caches, plain_text
from pycmdcheck.profiles import DEPRECATED_CHECKS, PROFILES, apply_profile
from pycmdcheck.pyproject_reader import get_dict, get_tool_table
from pycmdcheck.results import CheckResult, CheckStatus, Finding, Report
from pycmdcheck.subprocess_runner import kill_running_tools

logger = logging.getLogger(__name__)

FAIL_FAST_MESSAGE = "Not run (fail-fast)"

# The runner's own rules, for a check that gave no result of its own. These
# are the only codes that do not start with a check name. The rule listing
# shows them under the check name "pycmdcheck".
RUNNER_RULES: dict[str, str] = {
    "pycmdcheck.check-not-found": "No check is registered under the requested name",
    "pycmdcheck.check-raised": "The check raised an exception",
    "pycmdcheck.check-exited": "The check called sys.exit()",
    "pycmdcheck.invalid-result": "The check did not return a CheckResult",
}

# What to do about each of them, as in the hints of a check.
RUNNER_HINTS: dict[str, str] = {
    "pycmdcheck.check-not-found": (
        "Run pycmdcheck --list and use one of the check names it prints."
    ),
    "pycmdcheck.check-raised": (
        "Run again with --debug and send the traceback to the author of the check."
    ),
    "pycmdcheck.check-exited": (
        "Tell the author of the check, or leave the check out with --skip."
    ),
    "pycmdcheck.invalid-result": (
        "Tell the author of the check, or leave the check out with --skip."
    ),
}


def run_checks(
    package_path: str | Path,
    checks: list[str] | None = None,
    skip: list[str] | None = None,
    config: dict[str, Any] | None = None,
    parallel: bool = True,
    fail_fast: bool = False,
    *,
    profile: str | None = None,
) -> Report:
    """Run checks on a package.

    This is the core function that orchestrates check discovery and execution.
    It loads configuration, discovers available checks via entry points,
    and runs them either in parallel or sequentially.

    Args:
        package_path: Path to the package directory to check. Can be a string
            or Path object. Will be resolved to an absolute path.
        checks: List of specific check names to run. If None or empty, runs
            all checks that are enabled in the configuration, or the checks of
            ``profile`` when one is given. The available check names are
            discovered dynamically via entry points; run ``pycmdcheck --list``
            to see the current set. An unknown name is an error, a name given
            twice runs once, and a single string is read as one name. A
            deprecated check (``imports``) only runs when it is named here.
            A run that names no check and no profile logs a warning when
            ``[tool.pycmdcheck.checks]`` enables one, and leaves it out.
        skip: List of check names to skip, even if they would otherwise run.
            An unknown name is ignored with a logged warning, so skipping a
            plugin that is not installed keeps working.
        config: Pre-loaded configuration dictionary. If None, configuration
            is loaded from the package's pyproject.toml file.
        parallel: Whether to run checks in parallel using threads. Defaults
            to True. Results come back in the same order either way.
        fail_fast: Whether to stop after the first ERROR result. Checks that
            are already running finish, and checks that were not started are
            reported as SKIPPED. Defaults to False.
        profile: Name of a profile, as for ``pycmdcheck --profile``. Its
            options win over the configuration, and its check set is used when
            ``checks`` is not given.

    Returns:
        A Report object containing all check results. The report includes
        the package path and a list of CheckResult objects.

    Raises:
        FileNotFoundError: If ``package_path`` does not exist.
        NotADirectoryError: If ``package_path`` is not a directory.
        ValueError: If ``profile`` is not a known profile name, or if
            ``checks`` holds a name that no registered check has.

    Examples:
        Run all enabled checks:

        >>> report = run_checks("/path/to/package")  # doctest: +SKIP
        >>> report.passed  # doctest: +SKIP
        True

        Run specific checks only:

        >>> report = run_checks(".", checks=["metadata", "tests"])  # doctest: +SKIP
        >>> len(report.results)  # doctest: +SKIP
        2

        Run sequentially for debugging:

        >>> report = run_checks(".", parallel=False)  # doctest: +SKIP
    """
    package_path = Path(package_path).resolve()
    if not package_path.exists():
        raise FileNotFoundError(f"No such directory: {package_path}")
    if not package_path.is_dir():
        raise NotADirectoryError(f"Not a directory: {package_path}")
    requested = _as_names(checks)
    skipped = _as_names(skip)
    if profile is not None and profile not in PROFILES:
        raise ValueError(
            f"Unknown profile '{profile}'. Available profiles: {', '.join(PROFILES)}"
        )

    try:
        # Discover available checks
        available_checks = discover_checks()
        unknown = [name for name in requested if name not in available_checks]
        if unknown:
            raise ValueError(
                f"Unknown check(s): {', '.join(unknown)}. "
                f"Available checks: {', '.join(available_checks)}"
            )

        # Load configuration
        if config is None:
            config = load_config(package_path)
        if profile is not None:
            config = apply_profile(config, profile)
            if not requested:
                requested = sorted(PROFILES[profile].checks)
                _warn_unknown(requested, available_checks, f"profile '{profile}'")

        _warn_unknown(skipped, available_checks, "skip")
        configured = config.get("checks")
        if isinstance(configured, dict):
            # A misspelled name here would otherwise be ignored in silence.
            _warn_unknown(
                list(configured), available_checks, "[tool.pycmdcheck.checks]"
            )

        # Determine which checks to run
        if requested:
            checks_to_run = [name for name in requested if name in available_checks]
        else:
            # A deprecated check only runs when it is named.
            _warn_deprecated(package_path)
            checks_to_run = [
                name
                for name in available_checks
                if name not in DEPRECATED_CHECKS and is_check_enabled(config, name)
            ]
        checks_to_run = [c for c in checks_to_run if c not in skipped]

        report = Report(package_path=str(package_path))
        if not checks_to_run:
            return report

        if parallel and len(checks_to_run) > 1:
            results = _run_parallel(
                package_path,
                checks_to_run,
                available_checks,
                config,
                fail_fast=fail_fast,
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
    finally:
        # The file caches notice changed files on their own. This only frees
        # the parsed trees once the run is over.
        clear_all_caches()


def _as_names(value: Iterable[str] | str | None) -> list[str]:
    """Return *value* as a list of unique names in their original order."""
    if value is None:
        return []
    if isinstance(value, str):
        # A single name is accepted as it is, not read letter by letter.
        return [value]
    return list(dict.fromkeys(value))


def _warn_unknown(names: list[str], available: dict[str, type], argument: str) -> None:
    """Log a warning for names that match no registered check."""
    unknown = [name for name in names if name not in available]
    if unknown:
        # A name may come from the table of the project that is checked.
        logger.warning(
            "Ignoring unknown check name(s) in %s: %s",
            argument,
            plain_text(", ".join(unknown)),
        )


def _warn_deprecated(package_path: Path) -> None:
    """Log a warning for deprecated checks that the project enables.

    The table is read as the project wrote it, because the defaults enable
    the built-in checks by name, the deprecated ones included.
    """
    written = get_dict(get_tool_table(package_path, "pycmdcheck"), "checks")
    enabled = [
        name
        for name in sorted(DEPRECATED_CHECKS)
        if name in written and is_check_enabled({"checks": written}, name)
    ]
    if enabled:
        logger.warning(
            "The deprecated check(s) enabled in [tool.pycmdcheck.checks] "
            "were left out: %s",
            ", ".join(enabled),
        )


def _not_run(name: str) -> CheckResult:
    """Result for a check that fail-fast stopped before it started."""
    return CheckResult.skip(
        name,
        FAIL_FAST_MESSAGE,
        "fail-fast",
        ["An earlier check returned an error and fail-fast is on"],
    )


def _is_hint(hint: object) -> bool:
    """Say whether *hint* is text that holds more than white space."""
    return isinstance(hint, str) and bool(hint.strip())


def hints_of(check_class: object) -> dict[str, str]:
    """Return the hints of a check, less every entry that is not text.

    A plugin written before there were hints has no table of them, and a
    table that is not a dict counts as none.
    """
    hints = getattr(check_class, "hints", None)
    if not isinstance(hints, dict):
        return {}
    return {code: hint for code, hint in hints.items() if _is_hint(hint)}


def fill_hints(result: CheckResult, hints: dict[str, str]) -> None:
    """Give each finding that has no hint the hint of its code, if there is one.

    A hint that is not text, or is blank, counts as no hint.
    """
    result.findings = [
        finding
        if _is_hint(finding.hint)
        else dataclasses.replace(finding, hint=hints.get(finding.code))
        for finding in result.findings
    ]


def _check_failed(
    name: str, code: str, message: str, detail: str, start_time: float
) -> CheckResult:
    """Result for a check that did not return a result of its own.

    The code is a key of RUNNER_RULES.
    """
    # A finding is one line, and the text of an exception may be several.
    text = " ".join(message.split())
    return CheckResult(
        name=name,
        status=CheckStatus.ERROR,
        message=message,
        details=[detail],
        duration=time.time() - start_time,
        findings=[Finding(code, text, CheckStatus.ERROR, RUNNER_HINTS[code])],
    )


def _run_sequential(
    package_path: Path,
    check_names: list[str],
    available_checks: dict[str, type],
    config: dict[str, Any],
    fail_fast: bool = False,
) -> list[CheckResult]:
    """Run checks sequentially."""
    results: list[CheckResult] = []
    stopped = False

    for name in check_names:
        if stopped:
            results.append(_not_run(name))
            continue
        result = _run_single_check(package_path, name, available_checks, config)
        results.append(result)
        if fail_fast and result.status == CheckStatus.ERROR:
            stopped = True

    return results


def _run_parallel(
    package_path: Path,
    check_names: list[str],
    available_checks: dict[str, type],
    config: dict[str, Any],
    fail_fast: bool = False,
) -> list[CheckResult]:
    """Run checks in parallel using threads.

    With fail-fast, no check starts after the first ERROR. Checks that are
    already running finish and keep their results.
    """
    results: dict[str, CheckResult] = {}
    stop = threading.Event()

    def run_one(name: str) -> CheckResult | None:
        # A worker takes its next check as soon as it is free, so the stop
        # flag is set here, in the worker, before that can happen.
        if stop.is_set():
            return None
        result = _run_single_check(package_path, name, available_checks, config)
        if fail_fast and result.status == CheckStatus.ERROR:
            stop.set()
        return result

    with ThreadPoolExecutor(max_workers=min(len(check_names), MAX_WORKERS)) as executor:
        futures = {executor.submit(run_one, name): name for name in check_names}

        try:
            for future in as_completed(futures):
                # _run_single_check turns every failure into a result.
                result = future.result()
                if result is not None:
                    results[futures[future]] = result
        except KeyboardInterrupt:
            # Tools run in their own session and do not see the terminal's
            # Ctrl-C, so stop them here instead of waiting for them to finish.
            stop.set()
            for f in futures:
                f.cancel()
            while wait(futures, timeout=0.1).not_done:
                kill_running_tools()
            raise

    # Report in the requested order, whatever order the checks finished in.
    return [results.get(name) or _not_run(name) for name in check_names]


def _run_single_check(
    package_path: Path,
    name: str,
    available_checks: dict[str, type],
    config: dict[str, Any],
) -> CheckResult:
    """Run a single check."""
    check_class = available_checks.get(name)
    if not check_class:
        message = f"Check '{name}' not found"
        return CheckResult(
            name=name,
            status=CheckStatus.ERROR,
            message=message,
            findings=[
                Finding(
                    "pycmdcheck.check-not-found",
                    message,
                    CheckStatus.ERROR,
                    RUNNER_HINTS["pycmdcheck.check-not-found"],
                )
            ],
        )

    logger.debug("Starting check: %s", name)
    start_time = time.time()
    code = "pycmdcheck.check-raised"
    try:
        check_config = get_check_config(config, name)
        check_instance = check_class()
        result: CheckResult = check_instance.run(package_path, check_config)
        if not isinstance(result, CheckResult):
            code = "pycmdcheck.invalid-result"
            raise TypeError(
                f"run() returned {type(result).__name__}, expected a CheckResult"
            )
        if result.name != name:
            logger.warning(
                "Check '%s' returned result with name='%s'; correcting",
                name,
                result.name,
            )
            result = dataclasses.replace(result, name=name)
        fill_hints(result, hints_of(check_class))
        result.duration = time.time() - start_time
        logger.debug(
            "Check %s completed in %.2fs: %s",
            name,
            result.duration,
            result.status.value,
        )
        return result
    except SystemExit as e:
        return _check_failed(
            name,
            "pycmdcheck.check-exited",
            f"Check called sys.exit({e.code!r})",
            "A check must return a CheckResult and never exit the process",
            start_time,
        )
    except Exception as e:
        logger.debug("Check %s raised exception", name, exc_info=True)
        return _check_failed(
            name,
            code,
            f"Check raised exception: {e}",
            f"{type(e).__name__} in the '{name}' check",
            start_time,
        )
