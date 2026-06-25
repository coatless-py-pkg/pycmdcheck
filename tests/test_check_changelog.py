"""Tests for changelog check."""

from pathlib import Path

from pycmdcheck.checks.changelog import ChangelogCheck
from pycmdcheck.results import CheckStatus


class TestChangelogCheck:
    """Tests for ChangelogCheck."""

    def test_changelog_md_present(self, temp_package: Path) -> None:
        """CHANGELOG.md with content -> OK."""
        line = "# Changelog\n\n## 0.1.0\n\n- Initial release\n"
        content = line * 3
        (temp_package / "CHANGELOG.md").write_text(content)
        check = ChangelogCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.name == "changelog"

    def test_news_md_present(self, temp_package: Path) -> None:
        """NEWS.md variant detected -> OK."""
        content = "# News\n\n## v0.1.0\n\n- First release with all features\n" * 3
        (temp_package / "NEWS.md").write_text(content)
        check = ChangelogCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK

    def test_no_changelog(self, temp_package: Path) -> None:
        """No changelog -> NOTE."""
        check = ChangelogCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE

    def test_empty_changelog(self, temp_package: Path) -> None:
        """Empty changelog -> WARNING."""
        (temp_package / "CHANGELOG.md").write_text("# Changelog\n")
        check = ChangelogCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.WARNING

    def test_history_variant(self, temp_package: Path) -> None:
        """HISTORY.md variant detected -> OK."""
        content = "# History\n\n## 0.1.0\n\n- Initial release with core features\n" * 3
        (temp_package / "HISTORY.md").write_text(content)
        check = ChangelogCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
