"""Tests for python_versions check."""

from pathlib import Path

from pycmdcheck.checks.python_versions import PythonVersionsCheck
from pycmdcheck.results import CheckStatus


class TestPythonVersionsCheck:
    """Tests for PythonVersionsCheck."""

    def test_current_versions_ok(self, temp_package: Path) -> None:
        """requires-python >= 3.10 excludes all EOL versions -> OK."""
        check = PythonVersionsCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.name == "python_versions"

    def test_allows_eol_version(self, tmp_path: Path) -> None:
        """requires-python >= 3.8 allows EOL 3.8 -> NOTE."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\nrequires-python = ">=3.8"\n'
        )
        check = PythonVersionsCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert "eol" in result.message.lower()

    def test_no_requires_python(self, tmp_path: Path) -> None:
        """Missing requires-python -> WARNING."""
        pkg_dir = tmp_path / "src" / "mypkg"
        pkg_dir.mkdir(parents=True)
        (pkg_dir / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
        )
        check = PythonVersionsCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.WARNING

    def test_no_pyproject(self, empty_dir: Path) -> None:
        """No pyproject.toml -> NOTE."""
        check = PythonVersionsCheck()
        result = check.run(empty_dir, {})
        assert result.status == CheckStatus.NOTE

    def test_specifier_allows_simple(self) -> None:
        """Test _specifier_allows with simple >= pattern."""
        check = PythonVersionsCheck()
        assert check._specifier_allows(">=3.10", "3.8") is False
        assert check._specifier_allows(">=3.10", "3.10") is True
        assert check._specifier_allows(">=3.8", "3.8") is True
        assert check._specifier_allows(">=3.8", "3.9") is True

    def test_poetry_python_constraint(self, tmp_path: Path) -> None:
        """Legacy Poetry python constraint is recognized, not a false WARNING (#13)."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            'name = "mypkg"\nversion = "0.1.0"\n'
            "[tool.poetry.dependencies]\n"
            'python = "^3.10"\n'
        )
        check = PythonVersionsCheck()
        result = check.run(tmp_path, {})
        assert result.status != CheckStatus.WARNING
        assert result.status == CheckStatus.OK

    def test_setup_cfg_python_requires(self, tmp_path: Path) -> None:
        """setup.cfg python_requires is honored when [project] is absent (#13)."""
        (tmp_path / "setup.cfg").write_text(
            "[metadata]\nname = mypkg\n\n[options]\npython_requires = >=3.10\n"
        )
        check = PythonVersionsCheck()
        result = check.run(tmp_path, {})
        assert result.status != CheckStatus.WARNING
        assert result.status != CheckStatus.NOTE or "eol" in result.message.lower()

    def test_fallback_parser_is_conservative(self) -> None:
        """The packaging-free fallback never claims EOL is allowed when unsure (#21)."""
        check = PythonVersionsCheck()
        # Tilde and wildcard handled correctly.
        assert check._fallback_allows("~=3.10", "3.8") is False
        assert check._fallback_allows("~=3.8", "3.8") is True
        assert check._fallback_allows("==3.*", "3.8") is True
        assert check._fallback_allows(">=3.10", "3.8") is False
        # Unparseable -> conservative False (not True).
        assert check._fallback_allows("totally-bogus", "3.8") is False
