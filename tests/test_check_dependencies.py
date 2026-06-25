"""Tests for dependencies check."""

from pathlib import Path

from pycmdcheck.checks.dependencies import DependenciesCheck
from pycmdcheck.results import CheckStatus


class TestDependenciesCheck:
    """Tests for DependenciesCheck."""

    def test_no_pyproject(self, empty_dir: Path) -> None:
        check = DependenciesCheck()
        result = check.run(empty_dir, {})
        assert result.status == CheckStatus.NOTE

    def test_pyproject_without_project_table(self, tmp_path: Path) -> None:
        """pyproject present but no [project]/[tool.poetry] -> clear NOTE (#16)."""
        (tmp_path / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools"]\n'
        )
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert "project" in result.message.lower()

    def test_extra_import_not_undeclared(self, tmp_path: Path) -> None:
        """Importing an extra-declared library is not flagged undeclared (#3)."""
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import click\nimport rich\n")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
            'dependencies = ["click>=8.0"]\n\n'
            "[project.optional-dependencies]\n"
            'pretty = ["rich>=13.0"]\n'
        )
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status != CheckStatus.WARNING
        assert not any(
            "undeclared" in d.lower() and "rich" in d.lower() for d in result.details
        )

    def test_single_module_source_scanned(self, tmp_path: Path) -> None:
        """A single-module package's imports are scanned, not all deps unused (#15)."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
            'dependencies = ["click>=8.0"]\n'
        )
        src = tmp_path / "src"
        src.mkdir()
        (src / "mypkg.py").write_text("import click\n")
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert not any("unused" in d.lower() for d in result.details)

    def test_no_dependencies_declared(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\nversion = "0.1.0"\n'
        )
        pkg = tmp_path / "src" / "test"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import os\n")
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_all_deps_used(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\nversion = "0.1.0"\ndependencies = ["click"]\n'
        )
        pkg = tmp_path / "src" / "test"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import click\n")
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_unused_dependency(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\nversion = "0.1.0"\n'
            'dependencies = ["click", "rich"]\n'
        )
        pkg = tmp_path / "src" / "test"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import click\n")
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert any("rich" in d for d in result.details)

    def test_undeclared_import(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\nversion = "0.1.0"\ndependencies = ["requests"]\n'
        )
        pkg = tmp_path / "src" / "test"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import click\n")
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.WARNING
        assert any("click" in d for d in result.details)

    def test_pypi_name_mapping(self, tmp_path: Path) -> None:
        """PyPI name 'PyYAML' should map to import name 'yaml'."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "test"\nversion = "0.1.0"\ndependencies = ["PyYAML"]\n'
        )
        pkg = tmp_path / "src" / "test"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("import yaml\n")
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_importlib_metadata_resolution(self, tmp_path: Path) -> None:
        """Test that importlib.metadata is used for name resolution."""
        from pycmdcheck.checks.dependencies import _resolve_import_name

        # 'click' is installed (it's a dependency of pycmdcheck)
        # importlib.metadata should resolve it
        result = _resolve_import_name("click")
        assert result == "click"

    def test_resolve_survives_read_text_error(self) -> None:
        """_resolve_import_name falls back when dist.read_text raises OSError."""
        from unittest.mock import MagicMock, patch

        from pycmdcheck.checks.dependencies import _resolve_import_name

        mock_dist = MagicMock()
        mock_dist.read_text.side_effect = FileNotFoundError("top_level.txt")
        mock_dist.files = None

        with patch(
            "pycmdcheck.checks.dependencies.importlib.metadata.distribution",
            return_value=mock_dist,
        ):
            result = _resolve_import_name("some-package")
        # Falls back to name normalization
        assert result == "some_package"
