"""Continuous integration configuration check.

Verifies that a package has CI configuration files for automated
testing and deployment.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.results import CheckResult, CheckStatus


class CICheck(BaseCheck):
    """Check for continuous integration configuration.

    Searches for common CI configuration files and directories
    such as GitHub Actions workflows, CircleCI, Travis CI, etc.

    Attributes:
        name: The check identifier ("ci").
        description: Human-readable description of this check.
    """

    name = "ci"
    description = "Check for CI configuration"

    # (path_relative_to_root, description)
    CI_INDICATORS: list[tuple[str, str]] = [
        (".github/workflows", "GitHub Actions"),
        (".circleci", "CircleCI"),
        (".travis.yml", "Travis CI"),
        (".gitlab-ci.yml", "GitLab CI"),
        ("Jenkinsfile", "Jenkins"),
        ("azure-pipelines.yml", "Azure Pipelines"),
        (".buildkite", "Buildkite"),
        ("appveyor.yml", "AppVeyor"),
        (".appveyor.yml", "AppVeyor"),
    ]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for CI configuration presence.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if CI configuration is found
            - NOTE status if no CI configuration detected
        """
        details: list[str] = []
        found_ci: list[str] = []

        for path_str, ci_name in self.CI_INDICATORS:
            candidate = package_path / path_str
            if candidate.exists():
                # For directories, check they contain config files
                if candidate.is_dir():
                    config_files = list(candidate.glob("*.yml")) + list(
                        candidate.glob("*.yaml")
                    )
                    if config_files:
                        found_ci.append(ci_name)
                        details.append(
                            f"Found {ci_name}: {len(config_files)} config file(s)"
                        )
                else:
                    found_ci.append(ci_name)
                    details.append(f"Found {ci_name}: {candidate.name}")

        if found_ci:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message=f"CI configured ({', '.join(found_ci)})",
                details=details,
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.NOTE,
            message="No CI configuration found",
            details=["Consider adding GitHub Actions, CircleCI, or similar CI setup"],
        )
