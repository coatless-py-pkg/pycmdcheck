"""Tests for CI check."""

from pathlib import Path

import pytest

from pycmdcheck.checks import ci
from pycmdcheck.checks.ci import CICheck
from pycmdcheck.results import CheckStatus, Finding
from tests.helpers import codes


def write(root: Path, relative: str, content: str = "name: CI\n") -> None:
    """Write *content* to *relative* under *root*, creating directories."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


class TestCICheck:
    """Tests for CICheck."""

    @pytest.mark.parametrize(
        ("relative", "provider"),
        [
            # Recognized since 0.1.0.
            (".github/workflows/ci.yml", "GitHub Actions"),
            (".circleci/config.yml", "CircleCI"),
            (".travis.yml", "Travis CI"),
            (".gitlab-ci.yml", "GitLab CI"),
            ("Jenkinsfile", "Jenkins"),
            ("azure-pipelines.yml", "Azure Pipelines"),
            (".buildkite/pipeline.yml", "Buildkite"),
            ("appveyor.yml", "AppVeyor"),
            (".appveyor.yml", "AppVeyor"),
            # The .yaml spelling and more places for the same providers.
            (".github/workflows/tests.yaml", "GitHub Actions"),
            (".gitlab-ci.yaml", "GitLab CI"),
            ("azure-pipelines.yaml", "Azure Pipelines"),
            (".azure-pipelines.yml", "Azure Pipelines"),
            (".azure-pipelines/ci.yml", "Azure Pipelines"),
            (".buildkite/pipeline.json", "Buildkite"),
            # More providers.
            (".cirrus.yml", "Cirrus CI"),
            (".cirrus.star", "Cirrus CI"),
            (".woodpecker.yml", "Woodpecker CI"),
            (".woodpecker/test.yaml", "Woodpecker CI"),
            (".forgejo/workflows/ci.yml", "Forgejo Actions"),
            (".gitea/workflows/ci.yml", "Gitea Actions"),
            ("bitbucket-pipelines.yml", "Bitbucket Pipelines"),
            (".drone.yml", "Drone"),
            (".build.yml", "sourcehut"),
            (".builds/debian.yml", "sourcehut"),
        ],
    )
    def test_provider_detected(
        self, tmp_path: Path, relative: str, provider: str
    ) -> None:
        """Each recognized configuration file -> OK naming the provider."""
        write(tmp_path, relative)
        result = CICheck().run(tmp_path, {})
        assert result.name == "ci"
        assert result.status == CheckStatus.OK
        assert result.message == f"CI configured ({provider})"
        # A provider that keeps its files in a directory reports a count.
        directory, _, name = relative.rpartition("/")
        shown = f"1 config file(s) in {directory}/" if directory else name
        assert result.details == [f"Found {provider}: {shown}"]
        assert result.findings == []

    def test_no_ci(self, temp_package: Path) -> None:
        """No CI config -> NOTE that says what the check looked for."""
        result = CICheck().run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No CI configuration found"
        assert result.findings == [Finding("ci.missing", "No CI configuration found")]
        assert result.details[0].startswith("Looked in the project root")
        for provider in ("GitHub Actions", "GitLab CI", "sourcehut"):
            assert provider in result.details[0]

    def test_multiple_ci(self, tmp_path: Path) -> None:
        """Several providers -> OK with each listed once."""
        write(tmp_path, ".github/workflows/ci.yml")
        write(tmp_path, ".github/workflows/docs.yaml")
        write(tmp_path, ".travis.yml", "language: python\n")
        result = CICheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.message == "CI configured (GitHub Actions, Travis CI)"
        assert result.details == [
            "Found GitHub Actions: 2 config file(s) in .github/workflows/",
            "Found Travis CI: .travis.yml",
        ]

    def test_count_is_never_overstated(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """More files than the check counts -> "at least", not a wrong number."""
        monkeypatch.setattr(ci, "_COUNT_LIMIT", 2)
        for name in ("lock.yml", "publish.yml", "tests.yml"):
            write(tmp_path, f".github/workflows/{name}")
        result = CICheck().run(tmp_path, {})
        assert result.details == [
            "Found GitHub Actions: at least 2 config file(s) in .github/workflows/"
        ]

    def test_empty_workflows_dir(self, tmp_path: Path) -> None:
        """Empty .github/workflows/ directory -> NOTE (no yml files)."""
        (tmp_path / ".github" / "workflows").mkdir(parents=True)
        result = CICheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert codes(result) == ["ci.missing"]

    @pytest.mark.parametrize(
        "relative",
        [
            ".github/workflows",  # a file where the directory should be
            ".github/workflows/README.md",  # no workflow in the directory
            ".travis.yml/keep.txt",  # a directory where the file should be
        ],
    )
    def test_lookalikes_do_not_count(self, tmp_path: Path, relative: str) -> None:
        """Only regular files with the expected names count -> NOTE."""
        write(tmp_path, relative)
        result = CICheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No CI configuration found"
        assert codes(result) == ["ci.missing"]

    def test_following_the_hint_clears_the_rule(self, tmp_path: Path) -> None:
        """Doing what the hint says makes the rule stop firing."""
        assert codes(CICheck().run(tmp_path, {})) == ["ci.missing"]
        assert (
            "workflow such as .github/workflows/ci.yml" in CICheck.hints["ci.missing"]
        )
        write(tmp_path, ".github/workflows/ci.yml")
        assert codes(CICheck().run(tmp_path, {})) == []
