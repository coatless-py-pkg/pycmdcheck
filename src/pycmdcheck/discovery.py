"""Plugin discovery via entry points.

This module provides functionality to discover and load check plugins
registered via Python entry points. Third-party packages can register
custom checks by adding entries to the ``pycmdcheck.checks`` entry point
group in their pyproject.toml.

Example entry point registration in pyproject.toml:

    [project.entry-points."pycmdcheck.checks"]
    my_check = "my_package.checks:MyCheck"
"""

import functools
import logging
from importlib.metadata import entry_points
from typing import TYPE_CHECKING

from pycmdcheck.profiles import ALL_CHECKS, DEPRECATED_CHECKS

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pycmdcheck.checks.base import Check

ENTRY_POINT_GROUP = "pycmdcheck.checks"
"""Entry point group name for check plugins."""

_BUILTIN_MODULE_PREFIX = "pycmdcheck.checks."

# The names a plugin cannot take: the built-in checks, and the deprecated
# ones that are still registered.
_RESERVED_NAMES = ALL_CHECKS | DEPRECATED_CHECKS


@functools.lru_cache(maxsize=1)
def discover_checks() -> dict[str, type["Check"]]:
    """Discover all registered checks via entry points.

    Scans the ``pycmdcheck.checks`` entry point group and loads all
    registered check classes. Built-in checks are registered in the
    package's pyproject.toml, and third-party packages can register
    additional checks.

    A plugin that fails to load, for any reason, is skipped with a logged
    warning, and discovery continues with the other checks. The names of the
    built-in checks, deprecated ones included, are reserved: a plugin that
    registers one of them is skipped with a warning, so a plugin cannot
    replace a built-in check.

    Returns:
        A dictionary mapping check names (strings) to check classes, sorted
        by name. The check classes implement the Check protocol from
        pycmdcheck.checks.base.

    Examples:
        Discover all available checks (returns a name -> class mapping):

        >>> checks = discover_checks()
        >>> isinstance(checks, dict)
        True

        Get a specific check class (requires the package to be installed so its
        entry points are registered):

        >>> MetadataCheck = discover_checks()["metadata"]  # doctest: +SKIP
        >>> MetadataCheck().name  # doctest: +SKIP
        'metadata'
    """
    checks: dict[str, type[Check]] = {}

    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        if ep.name in _RESERVED_NAMES and not ep.value.startswith(
            _BUILTIN_MODULE_PREFIX
        ):
            logger.warning(
                "Ignoring check '%s' from %s: the name belongs to a built-in check",
                ep.name,
                ep.value,
            )
            continue
        try:
            check_class = ep.load()
        except Exception as e:
            # One broken plugin must not take every command down with it.
            logger.warning(
                "Failed to load check '%s' from %s: %s",
                ep.name,
                ep.value,
                e,
            )
            continue
        if not callable(check_class):
            logger.warning(
                "Ignoring check '%s' from %s: it is not a class",
                ep.name,
                ep.value,
            )
            continue
        checks[ep.name] = check_class

    checks = dict(sorted(checks.items()))
    logger.debug("Discovered %d checks: %s", len(checks), ", ".join(checks))
    return checks


def list_available_checks() -> list[tuple[str, str]]:
    """List all available checks with their descriptions.

    Discovers all registered checks and returns their names and
    descriptions in a format suitable for display (e.g., in CLI help).
    Results are sorted alphabetically by check name.

    Returns:
        A list of (name, description) tuples, sorted alphabetically
        by name. Each tuple contains:

        - name: The check's unique identifier (str)
        - description: Human-readable description of what the check does (str)

        If a check's description cannot be retrieved, "No description
        available" is used as a fallback.

    Examples:
        List all checks for display:

        >>> checks = list_available_checks()
        >>> isinstance(checks, list)
        True

        Display in CLI:

        >>> for name, desc in list_available_checks():
        ...     print(f"  {name}: {desc}")  # doctest: +SKIP
          docs: Check documentation presence
          license: Check for license file
          ...
    """
    checks = discover_checks()
    result: list[tuple[str, str]] = []

    for name, check_class in sorted(checks.items()):
        description = getattr(check_class, "description", None)
        result.append((name, str(description or "No description available")))

    return result
