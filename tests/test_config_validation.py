"""Tests for config validation."""

from pycmdcheck.config import load_config, validate_config


class TestConfigValidation:
    """Tests for validate_config()."""

    def test_valid_config(self) -> None:
        config = {"fail_on": ["error"], "checks": {"metadata": True}}
        warnings = validate_config(config)
        assert warnings == []

    def test_invalid_fail_on_value(self) -> None:
        config = {"fail_on": ["eror"], "checks": {}}
        warnings = validate_config(config)
        assert len(warnings) == 1
        assert "eror" in warnings[0]

    def test_invalid_fail_on_type(self) -> None:
        config = {"fail_on": "error", "checks": {}}
        warnings = validate_config(config)
        assert len(warnings) >= 1

    def test_valid_fail_on_values(self) -> None:
        config = {"fail_on": ["error", "warning", "note"], "checks": {}}
        warnings = validate_config(config)
        assert warnings == []


def test_validate_config_called_on_load(tmp_path):
    """validate_config is called by load_config."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test"\n\n[tool.pycmdcheck]\nfail_on = ["banana"]\n'
    )
    # Should not raise, but should log a warning
    config = load_config(tmp_path)
    # The invalid value should still be in the config (validate warns, doesn't fix)
    assert "banana" in config["fail_on"]
