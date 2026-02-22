"""Tests for metadata check enhancements."""

from pathlib import Path

from pycmdcheck.checks.metadata import MetadataCheck
from pycmdcheck.results import CheckStatus


class TestMetadataExtended:
    """Tests for extended metadata validation."""

    def test_all_extended_fields_present(self, temp_package: Path) -> None:
        """Package with authors, urls, classifiers -> no extended warnings."""
        # temp_package already includes authors, classifiers, and [project.urls]
        check = MetadataCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert not any("missing extended" in d.lower() for d in result.details)

    def test_missing_authors(self, tmp_path: Path) -> None:
        """Package without authors -> details mention authors."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            'description = "test"\nreadme = "README.md"\n'
            'license = "MIT"\nrequires-python = ">=3.10"\n'
            'classifiers = ["Development Status :: 3 - Alpha"]\n'
            "\n[project.urls]\n"
            'Homepage = "https://example.com"\n'
        )
        check = MetadataCheck()
        result = check.run(tmp_path, {})
        assert any("authors" in d.lower() for d in result.details)

    def test_missing_urls(self, tmp_path: Path) -> None:
        """Package without project.urls -> details mention urls."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            'description = "test"\nreadme = "README.md"\n'
            'license = "MIT"\nrequires-python = ">=3.10"\n'
            'authors = [{name = "Test"}]\n'
            'classifiers = ["Development Status :: 3 - Alpha"]\n'
        )
        check = MetadataCheck()
        result = check.run(tmp_path, {})
        assert any("urls" in d.lower() for d in result.details)
