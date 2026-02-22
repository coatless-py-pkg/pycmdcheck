"""Tests for configuration loading."""

from pathlib import Path

from pycmdcheck.config import (
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

    def test_load_from_pyproject(self, temp_package: Path) -> None:
        """Test loading config from pyproject.toml."""
        config = load_config(temp_package)

        assert isinstance(config, dict)
        assert "checks" in config

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
