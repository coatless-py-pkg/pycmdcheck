"""Configuration loading for pycmdcheck."""

import copy
import logging
from pathlib import Path
from typing import Any

from pycmdcheck.pyproject_reader import get_tool_table

logger = logging.getLogger(__name__)

VALID_FAIL_ON = {"error", "warning", "note"}


DEFAULT_CONFIG: dict[str, Any] = {
    "fail_on": ["error"],
    "checks": {
        "metadata": True,
        "structure": True,
        "tests": {"enabled": True, "runner": "pytest"},
        "linting": {"enabled": True, "tool": "ruff"},
        "typing": {"enabled": True, "tool": "mypy"},
        "imports": True,
        "license": True,
        "docs": True,
        "dependencies": True,
        "build": True,
        "formatting": {"enabled": True, "tool": "ruff"},
        "version": True,
        "py_typed": True,
    },
}
"""Default configuration applied when no pyproject.toml is found.

This configuration enables all built-in checks with sensible defaults:
- tests: Uses pytest as the test runner
- linting: Uses ruff as the linter
- typing: Uses mypy as the type checker
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
        - fail_on: List of statuses to fail on (default: ["error"])
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
    tool_config = get_tool_table(package_path, "pycmdcheck")
    if tool_config:
        config = _merge_config(config, tool_config)
    config_warnings = validate_config(config)
    for w in config_warnings:
        logger.warning("Config: %s", w)
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


def validate_config(config: dict[str, Any]) -> list[str]:
    """Validate config values and return a list of warning messages."""
    warnings: list[str] = []

    fail_on = config.get("fail_on", [])
    if not isinstance(fail_on, list):
        warnings.append(f"'fail_on' should be a list, got {type(fail_on).__name__}")
    else:
        for value in fail_on:
            if value not in VALID_FAIL_ON:
                warnings.append(
                    f"Invalid fail_on value '{value}'. "
                    f"Valid values: {', '.join(sorted(VALID_FAIL_ON))}"
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
            result[key] = value

    return result
