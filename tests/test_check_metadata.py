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


class TestMetadataDynamicFields:
    """Tests for PEP 621 ``dynamic`` field handling.

    A field listed in ``[project].dynamic`` is supplied by the build backend
    (e.g. setuptools_scm, hatch-vcs, uv-dynamic-versioning) and must NOT be
    reported as missing. See https://github.com/eliotwrobson/tldm which uses
    ``dynamic = ["version"]``.
    """

    def test_dynamic_version_not_required_missing(self, tmp_path: Path) -> None:
        """`dynamic = ["version"]` must not be reported as a missing field."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\n'
            'dynamic = ["version"]\n'
            'description = "test"\nreadme = "README.md"\n'
            'license = "MIT"\nrequires-python = ">=3.10"\n'
            'authors = [{name = "Test"}]\n'
            'classifiers = ["Development Status :: 3 - Alpha"]\n'
            "\n[project.urls]\n"
            'Homepage = "https://example.com"\n'
        )
        check = MetadataCheck()
        result = check.run(tmp_path, {})
        assert result.status != CheckStatus.ERROR
        assert not any("missing required" in d.lower() for d in result.details)
        assert not any(
            "version" in d.lower() and "missing" in d.lower() for d in result.details
        )

    def test_legacy_poetry_no_false_missing_required(self, tmp_path: Path) -> None:
        """Legacy Poetry ([tool.poetry], no [project]) must not ERROR on version."""
        pkg_dir = tmp_path / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            'name = "mypkg"\nversion = "1.2.3"\n'
            'description = "A legacy poetry package"\n'
            'authors = ["Jane Doe <jane@example.com>"]\n'
            'license = "MIT"\nreadme = "README.md"\n'
            'classifiers = ["Programming Language :: Python :: 3"]\n'
            'homepage = "https://example.com"\n'
            "\n[tool.poetry.dependencies]\n"
            'python = "^3.10"\n'
            "\n[build-system]\n"
            'requires = ["poetry-core>=1.0.0"]\n'
            'build-backend = "poetry.core.masonry.api"\n'
        )
        check = MetadataCheck()
        result = check.run(tmp_path, {})
        assert result.status != CheckStatus.ERROR
        assert not any("missing required" in d.lower() for d in result.details)

    def test_dynamic_recommended_field_not_missing(self, tmp_path: Path) -> None:
        """A recommended field declared dynamic is not flagged as missing."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            'dynamic = ["readme", "classifiers"]\n'
            'description = "test"\n'
            'license = "MIT"\nrequires-python = ">=3.10"\n'
            'authors = [{name = "Test"}]\n'
            "\n[project.urls]\n"
            'Homepage = "https://example.com"\n'
        )
        check = MetadataCheck()
        result = check.run(tmp_path, {})
        assert not any("readme" in d.lower() for d in result.details)
        assert not any("classifiers" in d.lower() for d in result.details)
