"""Tests for urls check."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from pycmdcheck.checks.urls import URLsCheck
from pycmdcheck.results import CheckStatus


class TestURLsCheck:
    """Tests for URLsCheck."""

    def test_no_pyproject(self, empty_dir: Path) -> None:
        """No pyproject.toml -> NOTE."""
        check = URLsCheck()
        result = check.run(empty_dir, {})
        assert result.status == CheckStatus.NOTE

    def test_no_urls_defined(self, tmp_path: Path) -> None:
        """pyproject.toml without [project.urls] -> NOTE."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
        )
        check = URLsCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.NOTE

    def test_all_urls_reachable(self, temp_package: Path) -> None:
        """All URLs reachable -> OK."""

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)

        check = URLsCheck()
        target = "pycmdcheck.checks.urls.urllib.request.urlopen"
        with patch(target, return_value=mock_resp):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK

    def test_unreachable_url(self, temp_package: Path) -> None:
        """Unreachable URL -> WARNING."""
        import urllib.error

        pyproject = temp_package / "pyproject.toml"
        content = pyproject.read_text()
        content = content.replace(
            'Homepage = "https://example.com"',
            'Homepage = "https://nonexistent.invalid"',
        )
        pyproject.write_text(content)

        check = URLsCheck()
        with patch(
            "pycmdcheck.checks.urls.urllib.request.urlopen",
            side_effect=urllib.error.URLError("Name or service not known"),
        ):
            result = check.run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert "unreachable" in result.message.lower()
