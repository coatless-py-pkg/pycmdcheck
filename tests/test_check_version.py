"""Tests for version consistency check."""

from pathlib import Path

from pycmdcheck.checks.version import VersionCheck
from pycmdcheck.results import CheckStatus


class TestVersionCheck:
    """Tests for VersionCheck."""

    def test_versions_match(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "1.0.0"\n'
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text('__version__ = "1.0.0"\n')
        check = VersionCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_versions_mismatch(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "1.0.0"\n'
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text('__version__ = "2.0.0"\n')
        check = VersionCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.ERROR

    def test_dynamic_version_skips(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\ndynamic = ["version"]\n'
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        check = VersionCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.SKIPPED

    def test_no_version_in_code(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "1.0.0"\n'
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        check = VersionCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.WARNING

    def test_no_pyproject(self, empty_dir: Path) -> None:
        check = VersionCheck()
        result = check.run(empty_dir, {})
        assert result.status == CheckStatus.NOTE

    def test_dynamic_version_via_call(self, tmp_path: Path) -> None:
        """__version__ set via function call (e.g. importlib.metadata) returns OK."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "1.0.0"\n'
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            'from importlib.metadata import version\n__version__ = version("mypkg")\n'
        )
        check = VersionCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert "dynamic" in result.message.lower()

    def test_unparseable_init(self, tmp_path: Path) -> None:
        """Unparseable __init__.py returns WARNING (no __version__ found)."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "1.0.0"\n'
        )
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("def broken(\n")
        check = VersionCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.WARNING
        assert "no __version__" in result.message.lower()
