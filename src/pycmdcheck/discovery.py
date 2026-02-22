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

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from pycmdcheck.checks.base import Check

ENTRY_POINT_GROUP = "pycmdcheck.checks"
"""Entry point group name for check plugins."""


@functools.lru_cache(maxsize=1)
def discover_checks() -> dict[str, type["Check"]]:
    """Discover all registered checks via entry points.

    Scans the ``pycmdcheck.checks`` entry point group and loads all
    registered check classes. Built-in checks are registered in the
    package's pyproject.toml, and third-party packages can register
    additional checks.

    If a check fails to load (e.g., due to an import error), a
    UserWarning is issued but discovery continues with other checks.

    Returns:
        A dictionary mapping check names (strings) to check classes.
        The check classes implement the Check protocol from
        pycmdcheck.checks.base.

    Examples:
        Discover all available checks:

        >>> checks = discover_checks()
        >>> "metadata" in checks
        True
        >>> "tests" in checks
        True

        Get a specific check class:

        >>> checks = discover_checks()
        >>> MetadataCheck = checks["metadata"]
        >>> instance = MetadataCheck()
        >>> instance.name
        'metadata'
    """
    checks: dict[str, type[Check]] = {}

    eps = entry_points(group=ENTRY_POINT_GROUP)
    for ep in eps:
        try:
            check_class = ep.load()
            checks[ep.name] = check_class
        except (ImportError, AttributeError, TypeError) as e:
            # Log warning but continue with other checks
            logger.warning(
                "Failed to load check '%s' from %s: %s",
                ep.name,
                ep.value,
                e,
            )

    logger.debug("Discovered %d checks: %s", len(checks), ", ".join(sorted(checks)))
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
        >>> len(checks) > 0
        True
        >>> name, description = checks[0]
        >>> isinstance(name, str) and isinstance(description, str)
        True

        Display in CLI:

        >>> for name, desc in list_available_checks():
        ...     print(f"  {name}: {desc}")  # doctest: +SKIP
          docs: Check documentation presence
          imports: Check that package imports work
          ...
    """
    checks = discover_checks()
    result: list[tuple[str, str]] = []

    for name, check_class in sorted(checks.items()):
        try:
            description = getattr(check_class, "description", "No description")
            result.append((name, description))
        except (AttributeError, TypeError):
            result.append((name, "No description available"))

    return result
