"""Deprecated imports check.

The imports check was merged into the dependencies check, which reports
undeclared imports from the same scan. This module keeps the name working
for one release.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.results import CheckResult


class ImportsCheck(BaseCheck):
    """Deprecated alias that points at the dependencies check.

    The check looks at nothing and is always skipped. It is left out of
    every profile and of a run that names no check, so it only runs when it
    is asked for by name. It will be removed in a later release.

    Attributes:
        name: The check identifier ("imports").
        description: Human-readable description of this check.

    Examples:
        >>> from pathlib import Path
        >>> result = ImportsCheck().run(Path("."), {})
        >>> result.status.value, result.skip_reason
        ('skipped', 'deprecated')
    """

    name = "imports"
    description = "Deprecated, use the dependencies check"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Say that the check was merged into the dependencies check.

        Args:
            package_path: Path to the package directory. It is not read.
            config: Configuration dictionary for this check. It is not used.

        Returns:
            A SKIPPED CheckResult with the skip reason "deprecated".
        """
        return CheckResult.skip(
            self.name,
            "Merged into the dependencies check",
            "deprecated",
            [
                "The imports check will be removed in a later release. "
                "The dependencies check reports undeclared imports"
            ],
        )
