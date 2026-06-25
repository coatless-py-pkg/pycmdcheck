"""Tests for the cached pyproject.toml reader utility."""

from pathlib import Path

import pytest

from pycmdcheck.pyproject_reader import (
    clear_cache,
    get_effective_project_table,
    get_project_table,
    get_tool_table,
    poetry_python_to_pep440,
    read_pyproject,
)


@pytest.fixture(autouse=True)
def _clear_reader_cache() -> None:
    """Clear the read_pyproject cache before each test."""
    clear_cache()


# ---------------------------------------------------------------------------
# TestReadPyproject
# ---------------------------------------------------------------------------


class TestReadPyproject:
    """Tests for read_pyproject()."""

    def test_reads_valid_file(self, temp_package: Path) -> None:
        """read_pyproject returns a parsed dict for a valid pyproject.toml."""
        data = read_pyproject(temp_package)
        assert data is not None
        assert isinstance(data, dict)
        assert "project" in data
        assert data["project"]["name"] == "mypackage"
        assert data["project"]["version"] == "0.1.0"

    def test_returns_none_for_missing(self, empty_dir: Path) -> None:
        """read_pyproject returns None when pyproject.toml does not exist."""
        data = read_pyproject(empty_dir)
        assert data is None

    def test_raises_on_invalid_toml(self, package_with_invalid_toml: Path) -> None:
        """read_pyproject raises on malformed TOML content."""
        with pytest.raises(ValueError):
            read_pyproject(package_with_invalid_toml)

    def test_caches_result(self, temp_package: Path) -> None:
        """Repeated calls return the same cached object (identity check)."""
        data1 = read_pyproject(temp_package)
        data2 = read_pyproject(temp_package)
        assert data1 is data2


# ---------------------------------------------------------------------------
# TestGetProjectTable
# ---------------------------------------------------------------------------


class TestGetProjectTable:
    """Tests for get_project_table()."""

    def test_returns_project_section(self, temp_package: Path) -> None:
        """get_project_table returns the [project] table with name and version."""
        project = get_project_table(temp_package)
        assert project["name"] == "mypackage"
        assert project["version"] == "0.1.0"

    def test_returns_empty_dict_when_missing(self, empty_dir: Path) -> None:
        """get_project_table returns {} when pyproject.toml is absent."""
        project = get_project_table(empty_dir)
        assert project == {}


# ---------------------------------------------------------------------------
# TestGetToolTable
# ---------------------------------------------------------------------------


class TestGetToolTable:
    """Tests for get_tool_table()."""

    def test_returns_tool_section(self, tmp_path: Path) -> None:
        """get_tool_table returns the [tool.<tool_name>] table."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text(
            '[project]\nname = "pkg"\nversion = "1.0"\n\n'
            '[tool.mytool]\nkey = "value"\noption = true\n'
        )
        result = get_tool_table(tmp_path, "mytool")
        assert result == {"key": "value", "option": True}

    def test_returns_empty_dict_for_missing_tool(self, temp_package: Path) -> None:
        """get_tool_table returns {} when [tool.<name>] does not exist."""
        result = get_tool_table(temp_package, "nonexistent_tool")
        assert result == {}

    def test_returns_empty_dict_when_no_file(self, empty_dir: Path) -> None:
        """get_tool_table returns {} when pyproject.toml is absent."""
        result = get_tool_table(empty_dir, "sometool")
        assert result == {}


# ---------------------------------------------------------------------------
# TestEffectiveProjectTable (legacy Poetry normalization)
# ---------------------------------------------------------------------------


class TestEffectiveProjectTable:
    """Tests for get_effective_project_table() and Poetry normalization."""

    def test_pep621_passthrough(self, temp_package: Path) -> None:
        """A PEP 621 [project] table is returned unchanged."""
        project = get_effective_project_table(temp_package)
        assert project["name"] == "mypackage"
        assert project["version"] == "0.1.0"

    def test_synthesizes_from_poetry(self, tmp_path: Path) -> None:
        """Legacy [tool.poetry] is normalized into a PEP 621-shaped dict."""
        (tmp_path / "pyproject.toml").write_text(
            "[tool.poetry]\n"
            'name = "mypkg"\nversion = "1.2.3"\n'
            'description = "desc"\nreadme = "README.md"\nlicense = "MIT"\n'
            'authors = ["Jane Doe <jane@example.com>"]\n'
            'classifiers = ["Programming Language :: Python :: 3"]\n'
            'homepage = "https://example.com"\n'
            "[tool.poetry.dependencies]\n"
            'python = "^3.10"\nrequests = "^2.0"\n'
        )
        project = get_effective_project_table(tmp_path)
        assert project["name"] == "mypkg"
        assert project["version"] == "1.2.3"
        assert project["requires-python"] == ">=3.10"
        assert project["authors"] == [{"name": "Jane Doe", "email": "jane@example.com"}]
        assert project["urls"]["Homepage"] == "https://example.com"
        assert "requests" in project["dependencies"]
        assert "python" not in project["dependencies"]

    def test_empty_when_no_metadata(self, tmp_path: Path) -> None:
        """pyproject.toml with neither [project] nor [tool.poetry] -> {}."""
        (tmp_path / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools"]\n'
        )
        assert get_effective_project_table(tmp_path) == {}

    def test_poetry_python_translation(self) -> None:
        """poetry_python_to_pep440 maps caret/tilde to a PEP 440 lower bound."""
        assert poetry_python_to_pep440("^3.9") == ">=3.9"
        assert poetry_python_to_pep440("~3.8") == ">=3.8"
        assert poetry_python_to_pep440(">=3.10,<4") == ">=3.10,<4"
        assert poetry_python_to_pep440("3.11") == ">=3.11"
        assert poetry_python_to_pep440("") is None
