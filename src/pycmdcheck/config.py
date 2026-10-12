"""Configuration loading for pycmdcheck."""

import codecs
import copy
import json
import logging
import math
import tomllib
from collections.abc import Callable, Collection
from pathlib import Path
from typing import Any

from pycmdcheck.constants import (
    MAX_PYPROJECT_BYTES,
    URL_TIMEOUT_MAX,
    URL_TIMEOUT_MIN,
)
from pycmdcheck.fileio import plain_text, read_whole_file
from pycmdcheck.profiles import ALL_CHECKS, PROFILES, apply_profile
from pycmdcheck.pyproject_reader import (
    get_tool_table,
    pyproject_error,
    pyproject_status,
)
from pycmdcheck.subprocess_runner import MAX_TIMEOUT

logger = logging.getLogger(__name__)

VALID_FAIL_ON = {"error", "warning", "note"}
# The most values or entries that one warning names. It is more than there
# are built-in checks, so a wrong value for any of them is always named.
_MAX_NAMED = 25


DEFAULT_CONFIG: dict[str, Any] = {
    "fail_on": ["error"],
    "checks": {
        "metadata": True,
        "structure": True,
        "tests": {"enabled": True, "runner": "pytest"},
        "linting": {"enabled": True, "tool": "auto"},
        "typing": {"enabled": True, "tool": "auto"},
        "license": True,
        "docs": True,
        "dependencies": True,
        "build": True,
        "formatting": {"enabled": True, "tool": "auto"},
        "version": True,
        "py_typed": True,
    },
}
"""Defaults that a project's ``[tool.pycmdcheck]`` table is merged over.

The ``checks`` table holds the tool defaults: pytest as the test runner, and
``"auto"`` for the linter, the type checker and the formatter, which makes each
of those checks use the tool the project itself is set up for. It is not the
list of checks that run: a check that is not named here is enabled too, with no
options.
"""


def load_config(package_path: Path) -> dict[str, Any]:
    """Load pycmdcheck configuration from pyproject.toml.

    Reads the [tool.pycmdcheck] section from the package's pyproject.toml
    and merges it with default configuration. If no pyproject.toml exists
    or it doesn't contain pycmdcheck configuration, returns defaults.

    Args:
        package_path: Path to the package directory containing pyproject.toml.

    Returns:
        Configuration dictionary with the following structure:
        - fail_on: The status to fail on, in a list (default: ["error"]).
          The command line reads it as a threshold, so "warning" also fails
          on errors.
        - checks: Dictionary of check configurations

    Examples:
        >>> from pathlib import Path
        >>> config = load_config(Path("."))
        >>> config["fail_on"]
        ['error']
        >>> config["checks"]["tests"]["runner"]
        'pytest'
    """
    config = copy.deepcopy(DEFAULT_CONFIG)
    if pyproject_status(package_path) == "unparseable":
        logger.warning(
            "Config: could not read pyproject.toml (%s). Using defaults.",
            pyproject_error(package_path),
        )
    tool_config = get_tool_table(package_path, "pycmdcheck")
    if tool_config:
        config = _merge_config(config, tool_config)
    for w in validate_config(config):
        logger.warning("Config: %s", w)
    if not isinstance(config.get("checks"), dict):
        # A value such as checks = "all" would break every per-check lookup.
        config["checks"] = copy.deepcopy(DEFAULT_CONFIG["checks"])
    logger.debug("Loaded config from %s", package_path)
    return config


def get_check_config(config: dict[str, Any], check_name: str) -> dict[str, Any]:
    """Get configuration for a specific check.

    Extracts the configuration for a named check from the full configuration.
    Handles both boolean shorthand (e.g., `metadata = true`) and dictionary
    configuration (e.g., `tests = { enabled = true, runner = "pytest" }`).

    Args:
        config: Full pycmdcheck configuration dictionary, as returned by
            load_config().
        check_name: Name of the check to get configuration for (e.g.,
            "metadata", "tests", "linting").

    Returns:
        Configuration dictionary for the check, always containing an
        "enabled" key. Additional keys depend on the check type.

    Examples:
        Boolean config is converted to dict:

        >>> config = {"checks": {"metadata": True}}
        >>> get_check_config(config, "metadata")
        {'enabled': True}

        Dict config is passed through with enabled defaulting to True:

        >>> config = {"checks": {"tests": {"runner": "pytest"}}}
        >>> get_check_config(config, "tests")
        {'runner': 'pytest', 'enabled': True}

        Unknown checks default to enabled:

        >>> config = {"checks": {}}
        >>> get_check_config(config, "unknown")
        {'enabled': True}
    """
    checks_config = config.get("checks", {})
    if not isinstance(checks_config, dict):
        checks_config = {}
    check_config = checks_config.get(check_name, True)

    # Handle boolean shorthand
    if isinstance(check_config, bool):
        return {"enabled": check_config}

    # Handle dict config (ensure 'enabled' key exists)
    if isinstance(check_config, dict):
        result = dict(check_config)
        result.setdefault("enabled", True)
        return result

    # Default to enabled
    return {"enabled": True}


def is_check_enabled(config: dict[str, Any], check_name: str) -> bool:
    """Check if a specific check is enabled.

    Convenience function to determine if a check should run based on
    the configuration.

    Args:
        config: Full pycmdcheck configuration dictionary.
        check_name: Name of the check to query.

    Returns:
        True if the check is enabled, False otherwise.

    Examples:
        >>> config = {"checks": {"metadata": True, "linting": False}}
        >>> is_check_enabled(config, "metadata")
        True
        >>> is_check_enabled(config, "linting")
        False
        >>> is_check_enabled(config, "unknown")  # Defaults to enabled
        True
    """
    check_config = get_check_config(config, check_name)
    return bool(check_config.get("enabled", True))


def _named(items: list[str]) -> str:
    """Join *items* for one warning, and count those past the first few."""
    listed = ", ".join(items[:_MAX_NAMED])
    more = len(items) - _MAX_NAMED
    return f"{listed}, and {more} more" if more > 0 else listed


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate config values and return a list of warning messages.

    There is one message for each kind of mistake, and it names the values
    or entries that have it. A table full of mistakes stays a few lines long
    and still says which entries they are. Past the first few it gives a
    count, with the built-in checks named first.
    """
    warnings: list[str] = []

    fail_on = config.get("fail_on", [])
    if not isinstance(fail_on, list):
        warnings.append(f"'fail_on' should be a list, got {type(fail_on).__name__}")
    else:
        invalid = [
            _shown(value)
            for value in fail_on
            if not isinstance(value, str) or value not in VALID_FAIL_ON
        ]
        if invalid:
            warnings.append(
                f"Invalid fail_on value {_named(invalid)}. "
                f"Valid values: {', '.join(sorted(VALID_FAIL_ON))}"
            )

    checks = config.get("checks", {})
    if not isinstance(checks, dict):
        warnings.append(
            f"'checks' should be a table, got {type(checks).__name__}. Ignoring it."
        )
    else:
        # Built-in checks come first, so other names cannot crowd one out.
        wrong = sorted(
            (
                name
                for name, value in checks.items()
                if not isinstance(value, bool | dict)
            ),
            key=lambda name: name not in ALL_CHECKS,
        )
        if wrong:
            # A name that is no check is reported by the runner, so nothing
            # here says what becomes of the check.
            named = [
                f"{plain_text(str(name), 40)} ({type(checks[name]).__name__})"
                for name in wrong
            ]
            warnings.append(
                "Each entry in 'checks' should be true, false, or a table. "
                f"Ignoring the value of: {_named(named)}"
            )

    return warnings


def _merge_config(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge override into base configuration.

    Values in override take precedence. Nested dictionaries are merged
    recursively rather than replaced.

    Args:
        base: Base configuration dictionary.
        override: Override configuration dictionary.

    Returns:
        Merged configuration dictionary.
    """
    result = copy.deepcopy(base)

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _merge_config(result[key], value)
        else:
            result[key] = copy.deepcopy(value)

    return result


# ── The --config file ─────────────────────────────────────────────────

# What an option of a built-in check may hold: the words for a message, and
# the test a value has to pass.
_Kind = tuple[str, Callable[[Any], bool]]


def _number(value: object) -> float:
    """Return a number as it is, and anything else as NaN, which fits no range."""
    # A bool is an int in Python, so true would otherwise count as 1.
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return math.nan
    return value


def _one_of(*words: str) -> _Kind:
    return f"one of {', '.join(words)}", lambda value: value in words


_BOOL: _Kind = ("true or false", lambda value: isinstance(value, bool))
_PERCENT: _Kind = ("a number from 0 to 100", lambda value: 0 <= _number(value) <= 100)
_URL_SECONDS: _Kind = (
    f"a number from {URL_TIMEOUT_MIN} to {URL_TIMEOUT_MAX}",
    lambda value: URL_TIMEOUT_MIN <= _number(value) <= URL_TIMEOUT_MAX,
)
# The two options of every check that starts a tool.
_TOOL_OPTIONS: dict[str, _Kind] = {
    "args": (
        "a list of strings",
        lambda value: isinstance(value, list)
        and all(isinstance(arg, str) for arg in value),
    ),
    "timeout": (
        f"a number above 0 and at most {MAX_TIMEOUT}",
        lambda value: 0 < _number(value) <= MAX_TIMEOUT,
    ),
}

# The options each built-in check reads. A check that is not listed reads
# none. A test compares the names with the source of the checks. "enabled" is
# not listed, because the runner reads it, and every check accepts it.
_FILE_OPTIONS: dict[str, dict[str, _Kind]] = {
    "build": _TOOL_OPTIONS,
    "docs": {
        "require_readme": _BOOL,
        "check_readme_sections": _BOOL,
        "check_docs_site": _BOOL,
        "check_docstrings": _BOOL,
        "min_docstring_coverage": _PERCENT,
    },
    "doctests": _TOOL_OPTIONS,
    "formatting": {"tool": _one_of("auto", "ruff", "black"), **_TOOL_OPTIONS},
    "linting": {"tool": _one_of("auto", "ruff", "flake8", "pylint"), **_TOOL_OPTIONS},
    "tests": {"runner": _one_of("pytest", "unittest"), **_TOOL_OPTIONS},
    "typing": {
        "tool": _one_of("auto", "mypy", "pyright"),
        "strict": _BOOL,
        **_TOOL_OPTIONS,
    },
    "urls": {"timeout": _URL_SECONDS},
}


def _shown(value: object) -> str:
    """Return a value from a settings file as short text that is safe to show.

    The value is spelled as TOML spells it, so true and "word".
    """
    try:
        text = json.dumps(value, default=str, ensure_ascii=False)
    except (ValueError, RecursionError):
        # Python refuses to write out an integer of several thousand digits.
        text = "a value too large to show"
    return plain_text(text)


def _check_file_options(check: str, options: dict[str, Any]) -> None:
    """Raise ValueError for an option the check does not read or cannot use."""
    known = {"enabled": _BOOL, **_FILE_OPTIONS.get(check, {})}
    for option, value in options.items():
        if option not in known:
            raise ValueError(
                f"Unknown option {plain_text(option)} for the check {check}. "
                f"Its options are {', '.join(known)}."
            )
        words, accepts = known[option]
        if not accepts(value):
            found = f"is {_shown(value)}"
            if isinstance(value, list) and accepts([]):
                # A long list is cut short, so show the item that is wrong.
                wrong = next(item for item in value if not accepts([item]))
                found = f"holds {_shown(wrong)}"
            raise ValueError(f"{check}.{option} must be {words}, but it {found}.")


def _load_config_file(
    path: Path, installed: Collection[str]
) -> tuple[str, list[str], dict[str, Any]]:
    """Read the file given with --config.

    The file names a built-in profile. It can take checks out of the profile
    and set options for the checks in it, and it cannot add a check.

    Args:
        path: The file, which is TOML.
        installed: The names of the registered checks.

    Returns:
        The profile name, the sorted names of the checks to run, which is
        never empty, and a configuration for run_checks(). The configuration
        is the defaults, then the options of the profile, then those of the
        file. Nothing in it comes from the project that is checked.

    Raises:
        ValueError: If the file cannot be read or holds a mistake. The
            message is made of whole sentences.
    """
    read = read_whole_file(path, MAX_PYPROJECT_BYTES)
    if read.problem is not None:
        raise ValueError(f"The file could not be read ({read.problem}).")
    if read.data.startswith(codecs.BOM_UTF8):
        raise ValueError(
            "The file starts with a UTF-8 byte order mark, which TOML does not allow."
        )
    try:
        data = tomllib.loads(read.data.decode("utf-8"))
    except UnicodeDecodeError:
        raise ValueError("The file is not valid UTF-8.") from None
    except RecursionError:
        raise ValueError("The file nests tables or arrays too deeply.") from None
    except ValueError as exc:
        reason = plain_text(str(exc), tail=40)
        raise ValueError(f"The file is not valid TOML. {reason}.") from None

    # A pyproject.toml passed by mistake stops here, on project or tool.
    unknown = sorted(set(data) - {"profile", "fail_on", "checks"})
    if unknown:
        raise ValueError(
            f"Unknown key(s) {plain_text(', '.join(unknown))}. A config file "
            "holds profile, fail_on and a [checks] table."
        )

    # The deprecated name default is not offered to a new kind of file.
    profiles = [name for name in PROFILES if name != "default"]
    name = data.get("profile")
    entries = data.get("checks", {})
    if name is None and isinstance(entries, dict) and "profile" in entries:
        raise ValueError(
            "The profile line is below [checks], where it is read as a check. "
            "Move it above [checks]."
        )
    if name is None:
        raise ValueError(
            'The file names no profile. Add a line such as profile = "triage" '
            f"above [checks]. The profiles are {', '.join(profiles)}."
        )
    if not isinstance(name, str) or name not in profiles:
        raise ValueError(
            f"profile must be one of {', '.join(profiles)}, but it is {_shown(name)}."
        )
    profile = PROFILES[name]

    fail_on = data.get("fail_on", ["error"])
    levels = [fail_on] if isinstance(fail_on, str) else fail_on
    if not isinstance(levels, list) or not all(
        isinstance(level, str) and level in VALID_FAIL_ON for level in levels
    ):
        raise ValueError(
            "fail_on must be error, warning or note, or a list of them, "
            f"but it is {_shown(fail_on)}."
        )

    if not isinstance(entries, dict):
        raise ValueError(f"checks must be a table, but it is {_shown(entries)}.")
    selected = {check for check in profile.checks if check in installed}
    options: dict[str, dict[str, Any]] = {}
    for check, value in entries.items():
        if check not in installed:
            hint = ""
            if check in ("profile", "fail_on"):
                hint = (
                    f" {check} is a key of the file itself, so move its line "
                    "above [checks]."
                )
            raise ValueError(
                f"Unknown check {plain_text(check)} in [checks].{hint} "
                f"The profile {name} holds {', '.join(sorted(profile.checks))}."
            )
        if check not in profile.checks:
            raise ValueError(
                f"{check} is not in the profile {name}. A config file can take "
                "a check out of a profile and cannot add one."
            )
        if isinstance(value, dict):
            _check_file_options(check, value)
            options[check] = value
            value = value.get("enabled", True)
        if value is False:
            selected.discard(check)
        elif value is not True:
            raise ValueError(
                f"{check} must be true, false or a table of options, "
                f"but it is {_shown(value)}."
            )
    if not selected:
        raise ValueError("The file leaves no check to run.")

    config = apply_profile(DEFAULT_CONFIG, name)
    config = _merge_config(config, {"fail_on": levels, "checks": options})
    return name, sorted(selected), config
