"""Continuous integration configuration check.

Verifies that a package has CI configuration files for automated
testing and deployment.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.fileio import display_path, find_declared_files
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# Most configuration files counted in one provider's directory.
_COUNT_LIMIT = 50


class CICheck(BaseCheck):
    """Check for continuous integration configuration.

    Searches the project root for the configuration files of common CI
    providers such as GitHub Actions, GitLab CI or CircleCI. The check is
    about presence only: it does not read what the configuration runs.

    Attributes:
        name: The check identifier ("ci").
        description: Human-readable description of this check.
        rules: The codes this check can report.
        hints: What to do about each code.
    """

    name = "ci"
    description = "Check for CI configuration"
    rules = {"ci.missing": "No CI configuration file found"}
    hints = {
        "ci.missing": (
            "Add a GitHub Actions workflow such as .github/workflows/ci.yml, or the "
            "configuration file of another CI provider."
        )
    }

    # (glob pattern relative to the root, provider). Names are matched without
    # regard to letter case, only regular files count, and a pattern ending in
    # ".yml" accepts ".yaml" as well.
    CI_INDICATORS: list[tuple[str, str]] = [
        (".github/workflows/*.yml", "GitHub Actions"),
        (".circleci/*.yml", "CircleCI"),
        (".travis.yml", "Travis CI"),
        (".gitlab-ci.yml", "GitLab CI"),
        ("Jenkinsfile", "Jenkins"),
        ("azure-pipelines.yml", "Azure Pipelines"),
        (".azure-pipelines.yml", "Azure Pipelines"),
        (".azure-pipelines/*.yml", "Azure Pipelines"),
        (".buildkite/*.yml", "Buildkite"),
        (".buildkite/*.json", "Buildkite"),
        ("appveyor.yml", "AppVeyor"),
        (".appveyor.yml", "AppVeyor"),
        (".cirrus.yml", "Cirrus CI"),
        (".cirrus.star", "Cirrus CI"),
        (".woodpecker.yml", "Woodpecker CI"),
        (".woodpecker/*.yml", "Woodpecker CI"),
        (".forgejo/workflows/*.yml", "Forgejo Actions"),
        (".gitea/workflows/*.yml", "Gitea Actions"),
        ("bitbucket-pipelines.yml", "Bitbucket Pipelines"),
        (".drone.yml", "Drone"),
        (".build.yml", "sourcehut"),
        (".builds/*.yml", "sourcehut"),
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
        # Provider name to what was found for it: the file, or for a provider
        # that keeps its files in a directory, how many there are.
        found: dict[str, str] = {}
        for pattern, ci_name in self.CI_INDICATORS:
            if ci_name in found:
                continue
            patterns = [pattern]
            if pattern.endswith(".yml"):
                patterns.append(pattern[:-4] + ".yaml")
            files = find_declared_files(package_path, patterns, limit=_COUNT_LIMIT)
            if not files:
                continue
            if "*" not in pattern:
                found[ci_name] = display_path(files[0], package_path)
                continue
            count = str(len(files))
            if len(files) >= _COUNT_LIMIT:
                count = f"at least {_COUNT_LIMIT}"
            where = display_path(files[0].parent, package_path)
            found[ci_name] = f"{count} config file(s) in {where}/"

        if found:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message=f"CI configured ({', '.join(found)})",
                details=[f"Found {ci_name}: {path}" for ci_name, path in found.items()],
            )

        providers = dict.fromkeys(ci_name for _pattern, ci_name in self.CI_INDICATORS)
        findings = [
            Finding("ci.missing", "No CI configuration found", CheckStatus.NOTE)
        ]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message="No CI configuration found",
            details=[
                "Looked in the project root for the configuration files of "
                + ", ".join(providers),
            ],
            findings=findings,
        )
