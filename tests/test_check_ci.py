"""Tests for CI check."""

from pathlib import Path

from pycmdcheck.checks.ci import CICheck
from pycmdcheck.results import CheckStatus


class TestCICheck:
    """Tests for CICheck."""

    def test_github_actions(self, temp_package: Path) -> None:
        """GitHub Actions workflow detected -> OK."""
        workflows = temp_package / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: CI\non: push\n")
        check = CICheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "GitHub Actions" in result.message

    def test_travis_ci(self, temp_package: Path) -> None:
        """Travis CI config detected -> OK."""
        (temp_package / ".travis.yml").write_text("language: python\n")
        check = CICheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "Travis CI" in result.message

    def test_gitlab_ci(self, temp_package: Path) -> None:
        """GitLab CI config detected -> OK."""
        (temp_package / ".gitlab-ci.yml").write_text("stages:\n  - test\n")
        check = CICheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "GitLab CI" in result.message

    def test_no_ci(self, temp_package: Path) -> None:
        """No CI config -> NOTE."""
        check = CICheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert "no ci" in result.message.lower()

    def test_empty_workflows_dir(self, temp_package: Path) -> None:
        """Empty .github/workflows/ directory -> NOTE (no yml files)."""
        workflows = temp_package / ".github" / "workflows"
        workflows.mkdir(parents=True)
        check = CICheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE

    def test_multiple_ci(self, temp_package: Path) -> None:
        """Multiple CI configs detected -> OK with all listed."""
        workflows = temp_package / ".github" / "workflows"
        workflows.mkdir(parents=True)
        (workflows / "ci.yml").write_text("name: CI\n")
        (temp_package / ".travis.yml").write_text("language: python\n")
        check = CICheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert "GitHub Actions" in result.message
        assert "Travis CI" in result.message
