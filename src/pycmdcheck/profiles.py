"""Check profiles for pycmdcheck.

Profiles define named sets of checks and configuration overrides
for common use cases like pyOpenSci onboarding or strict quality gates.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The built-in check roster. Kept explicit (rather than derived from
# discover_checks()) because "built-in" must exclude third-party plugins that
# register the same entry-point group. A test asserts that this roster and
# DEPRECATED_CHECKS together stay equal to the checks registered in
# pyproject.toml, so it cannot silently drift.
# "imports" is not listed: it was merged into "dependencies" in 0.2.0.
ALL_CHECKS: frozenset[str] = frozenset(
    {
        "metadata",
        "structure",
        "tests",
        "test_suite",
        "linting",
        "typing",
        "license",
        "docs",
        "dependencies",
        "build",
        "formatting",
        "version",
        "py_typed",
        # New checks
        "community",
        "ci",
        "changelog",
        "citation",
        "python_versions",
        "urls",
        "doctests",
    }
)

# Checks that have been merged into another check. Each name stays registered
# for one release, so naming it with -c or checks= keeps working, but no
# roster and no profile holds it, and a run that names no check leaves it out.
DEPRECATED_CHECKS: frozenset[str] = frozenset({"imports"})

# The checks in the "core" profile: metadata, layout, license, docs, and the
# code-quality tools. This is a curated subset that predates the community and
# pyOpenSci checks. Keep it explicit. It is not meant to track ALL_CHECKS.
# "imports" is not listed: it was merged into "dependencies" in 0.2.0.
# "test_suite" is listed because it reports a project with no tests, which
# "tests" reported itself before 0.2.0.
CORE_CHECKS: frozenset[str] = frozenset(
    {
        "metadata",
        "structure",
        "tests",
        "test_suite",
        "linting",
        "typing",
        "license",
        "docs",
        "dependencies",
        "build",
        "formatting",
        "version",
        "py_typed",
    }
)

# The old name of CORE_CHECKS, kept so that existing imports keep working.
ORIGINAL_CHECKS = CORE_CHECKS

# Checks that run external tools (pytest/ruff/mypy), build or execute the
# package, compare imports with the running environment, or use the network.
# They need the package and its tools installed, and several of them execute
# code from the repository. They are excluded from the static "triage" profile,
# which must be safe on a repository that has only been cloned.
DYNAMIC_CHECKS: frozenset[str] = frozenset(
    {
        "tests",  # runs pytest
        # "imports" was listed here until it was merged into "dependencies".
        "dependencies",  # maps imports to distributions in that environment
        "build",  # builds the package
        "linting",  # runs ruff/flake8
        "typing",  # runs mypy/pyright
        "formatting",  # runs ruff/black
        "doctests",  # runs pytest --doctest
        "urls",  # validates links over the network
    }
)

# Static checks that only read files. They need no install and no network, so
# they are safe to run against a submitted repository that was only cloned.
STATIC_CHECKS: frozenset[str] = ALL_CHECKS - DYNAMIC_CHECKS


@dataclass(frozen=True)
class Profile:
    """A named profile defining which checks to run and configuration."""

    name: str
    description: str
    checks: frozenset[str]
    config_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


PROFILES: dict[str, Profile] = {
    "minimal": Profile(
        name="minimal",
        description="Quick basic checks (metadata, structure, license)",
        checks=frozenset({"metadata", "structure", "license"}),
    ),
    "triage": Profile(
        name="triage",
        description=(
            "Static checks that only read files. No install, external tools, "
            "or network (for CI triage of arbitrary repositories)"
        ),
        checks=STATIC_CHECKS,
        # Every option that a static check reads is set here, so a checked
        # repository cannot change the results of a triage run through its
        # own settings. The two docstring options were the last it could reach.
        config_overrides={
            "docs": {
                "require_readme": True,
                "check_readme_sections": True,
                "check_docs_site": True,
                "check_docstrings": False,
                "min_docstring_coverage": 0,
            },
        },
    ),
    "core": Profile(
        name="core",
        description=(
            "Metadata, layout, license, docs, and the code-quality tools "
            "(tests, linting, typing, formatting, build)"
        ),
        checks=CORE_CHECKS,
    ),
    "default": Profile(
        name="default",
        description="Deprecated alias of core",
        checks=CORE_CHECKS,
    ),
    "pyopensci": Profile(
        name="pyopensci",
        description="pyOpenSci onboarding requirements (all built-in checks)",
        checks=ALL_CHECKS,
        config_overrides={
            "docs": {
                "require_readme": True,
                "check_docstrings": True,
                "check_readme_sections": True,
                "check_docs_site": True,
                "min_docstring_coverage": 50,
            },
        },
    ),
    "strict": Profile(
        name="strict",
        description=(
            "All built-in checks, with ruff linting and strict mypy type checking"
        ),
        checks=ALL_CHECKS,
        config_overrides={
            "docs": {
                "require_readme": True,
                "check_docstrings": True,
                "check_readme_sections": True,
                "check_docs_site": True,
                "min_docstring_coverage": 80,
            },
            # Named tools, so that linting and typing also run on a project
            # that has adopted neither. Automatic detection would skip it.
            "linting": {"tool": "ruff"},
            "typing": {"tool": "mypy", "strict": True},
        },
    ),
}


def get_profile(name: str) -> Profile | None:
    """Look up a profile by name.

    Args:
        name: Profile name (minimal, triage, core, pyopensci, strict, or
            the deprecated default).

    Returns:
        The Profile, or None if not found.
    """
    return PROFILES.get(name)


def apply_profile(config: dict[str, Any], name: str) -> dict[str, Any]:
    """Return a copy of *config* with a profile's check options applied.

    A profile's options win over the same options set in pyproject.toml.
    Neither *config* nor the profile is modified. Which checks the profile
    selects is a separate matter: read ``get_profile(name).checks`` for that.
    The name "default" still works and logs a deprecation warning.

    Args:
        config: Full pycmdcheck configuration, as returned by load_config().
        name: Profile name (minimal, triage, core, pyopensci, strict, or
            the deprecated default).

    Returns:
        A new configuration dictionary.

    Raises:
        ValueError: If no profile has that name.
    """
    profile = PROFILES.get(name)
    if profile is None:
        raise ValueError(
            f"Unknown profile '{name}'. Available profiles: {', '.join(PROFILES)}"
        )
    if name == "default":
        logger.warning("The profile name 'default' is deprecated. Use 'core' instead.")

    merged = copy.deepcopy(config)
    checks = merged.get("checks")
    if not isinstance(checks, dict):
        checks = {}
        merged["checks"] = checks

    for check_name, overrides in profile.config_overrides.items():
        existing = checks.get(check_name)
        if isinstance(existing, dict):
            options = existing
        elif isinstance(existing, bool):
            options = {"enabled": existing}
        else:
            options = {}
        for key, value in overrides.items():
            if key in options and options[key] != value:
                logger.debug(
                    "Profile '%s' replaces %s.%s = %r with %r",
                    name,
                    check_name,
                    key,
                    options[key],
                    value,
                )
            options[key] = copy.deepcopy(value)
        checks[check_name] = options

    return merged


def list_profiles() -> list[tuple[str, str]]:
    """Return all profiles as (name, description) pairs."""
    return [(p.name, p.description) for p in PROFILES.values()]
