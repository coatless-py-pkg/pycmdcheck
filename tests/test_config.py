"""Tests for configuration loading."""

from pathlib import Path

import pytest

from pycmdcheck.config import (
    DEFAULT_CONFIG,
    get_check_config,
    is_check_enabled,
    load_config,
)


class TestLoadConfig:
    """Tests for load_config function."""

    def test_load_default_config(self, empty_dir: Path) -> None:
        """Test loading defaults when no pyproject.toml exists."""
        config = load_config(empty_dir)

        assert "fail_on" in config
        assert "checks" in config
        assert config["fail_on"] == ["error"]

    @pytest.mark.parametrize("name", ["linting", "typing", "formatting"])
    def test_tool_defaults_to_auto(self, empty_dir: Path, name: str) -> None:
        """Each tool check picks the project's own tool unless told otherwise."""
        assert DEFAULT_CONFIG["checks"][name]["tool"] == "auto"
        assert get_check_config(load_config(empty_dir), name)["tool"] == "auto"

    def test_configured_tool_wins_over_auto(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pycmdcheck.checks.linting]\ntool = "flake8"\n'
        )
        config = load_config(tmp_path)
        assert get_check_config(config, "linting")["tool"] == "flake8"
        assert get_check_config(config, "typing")["tool"] == "auto"

    def test_load_from_pyproject(self, temp_package: Path) -> None:
        """Test that a pyproject.toml with no pycmdcheck table gives the defaults."""
        assert load_config(temp_package) == DEFAULT_CONFIG

    def test_config_override(self, tmp_path: Path) -> None:
        """Test that pyproject.toml overrides defaults."""
        pyproject = tmp_path / "pyproject.toml"
        pyproject.write_text("""[project]
name = "test"
version = "0.1.0"

[tool.pycmdcheck]
fail_on = ["error", "warning"]

[tool.pycmdcheck.checks]
linting = false
""")

        config = load_config(tmp_path)

        assert config["fail_on"] == ["error", "warning"]
        assert config["checks"]["linting"] is False


class TestCheckConfig:
    """Tests for check configuration helpers."""

    def test_get_check_config_bool(self) -> None:
        """Test getting config for boolean check setting."""
        config = {"checks": {"metadata": True}}
        check_config = get_check_config(config, "metadata")

        assert check_config["enabled"] is True

    def test_get_check_config_dict(self) -> None:
        """Test getting config for dict check setting."""
        config = {"checks": {"tests": {"enabled": True, "runner": "pytest"}}}
        check_config = get_check_config(config, "tests")

        assert check_config["enabled"] is True
        assert check_config["runner"] == "pytest"

    def test_is_check_enabled_true(self) -> None:
        """Test check is enabled."""
        config = {"checks": {"metadata": True}}
        assert is_check_enabled(config, "metadata") is True

    def test_is_check_enabled_false(self) -> None:
        """Test check is disabled."""
        config = {"checks": {"linting": False}}
        assert is_check_enabled(config, "linting") is False

    def test_is_check_enabled_dict(self) -> None:
        """Test check enabled from dict config."""
        config = {"checks": {"tests": {"enabled": False}}}
        assert is_check_enabled(config, "tests") is False

    def test_missing_check_defaults_enabled(self) -> None:
        """Test missing check defaults to enabled."""
        config = {"checks": {}}
        assert is_check_enabled(config, "unknown") is True
        # So does every check when "checks" is not a table.
        assert get_check_config({"checks": "all"}, "docs") == {"enabled": True}
