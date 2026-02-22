"""Tests for the cached pyproject.toml reader utility."""

from pathlib import Path

import pytest

from pycmdcheck.pyproject_reader import (
    clear_cache,
    get_project_table,
    get_tool_table,
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
