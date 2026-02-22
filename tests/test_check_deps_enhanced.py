"""Tests for enhanced dependencies check."""

from pathlib import Path

from pycmdcheck.checks.dependencies import DependenciesCheck


class TestDependenciesVersionBounds:
    """Tests for unbounded version warning."""

    def test_bounded_deps_no_warning(self, tmp_path: Path) -> None:
        """All deps have version bounds -> no unbounded warning."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("import click\n")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            'dependencies = ["click>=8.0"]\n'
        )
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert not any("unbounded" in d.lower() for d in result.details)

    def test_unbounded_deps_noted(self, tmp_path: Path) -> None:
        """Deps without version bounds -> detail notes them."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("import requests\n")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            'dependencies = ["requests"]\n'
        )
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        assert any("unbounded" in d.lower() for d in result.details)

    def test_mixed_bounded_unbounded(self, tmp_path: Path) -> None:
        """Mix of bounded and unbounded deps."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("import click\nimport requests\n")
        (tmp_path / "pyproject.toml").write_text(
            "[project]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            'dependencies = ["click>=8.0", "requests"]\n'
        )
        check = DependenciesCheck()
        result = check.run(tmp_path, {})
        unbounded_details = [d for d in result.details if "unbounded" in d.lower()]
        assert len(unbounded_details) == 1
        assert "requests" in unbounded_details[0].lower()
