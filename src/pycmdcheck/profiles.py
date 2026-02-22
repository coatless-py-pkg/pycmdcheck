"""Check profiles for pycmdcheck.

Profiles define named sets of checks and configuration overrides
for common use cases like pyOpenSci onboarding or strict quality gates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# All checks that ship with pycmdcheck
ALL_CHECKS: frozenset[str] = frozenset(
    {
        "metadata",
        "structure",
        "tests",
        "linting",
        "typing",
        "imports",
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

# Original 13 checks (pre-pyOpenSci)
ORIGINAL_CHECKS: frozenset[str] = frozenset(
    {
        "metadata",
        "structure",
        "tests",
        "linting",
        "typing",
        "imports",
        "license",
        "docs",
        "dependencies",
        "build",
        "formatting",
        "version",
        "py_typed",
    }
)


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
        description="Quick sanity checks (metadata, structure, license)",
        checks=frozenset({"metadata", "structure", "license"}),
    ),
    "default": Profile(
        name="default",
        description="Standard checks (original 13 built-in checks)",
        checks=ORIGINAL_CHECKS,
    ),
    "pyopensci": Profile(
        name="pyopensci",
        description="pyOpenSci onboarding requirements",
        checks=ALL_CHECKS,
        config_overrides={
            "docs": {
                "require_readme": True,
                "check_docstrings": True,
                "check_readme_sections": True,
            },
        },
    ),
    "strict": Profile(
        name="strict",
        description="All checks at maximum strictness",
        checks=ALL_CHECKS,
        config_overrides={
            "docs": {
                "require_readme": True,
                "check_docstrings": True,
                "check_readme_sections": True,
            },
            "typing": {"strict": True},
        },
    ),
}


def get_profile(name: str) -> Profile | None:
    """Look up a profile by name.

    Args:
        name: Profile name (minimal, default, pyopensci, strict).

    Returns:
        The Profile, or None if not found.
    """
    return PROFILES.get(name)


def list_profiles() -> list[tuple[str, str]]:
    """Return all profiles as (name, description) pairs."""
    return [(p.name, p.description) for p in PROFILES.values()]
