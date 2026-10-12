"""Tests for config validation."""

import pytest

from pycmdcheck.config import (
    DEFAULT_CONFIG,
    _merge_config,
    get_check_config,
    is_check_enabled,
    load_config,
    validate_config,
)


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
        assert warnings == ["'fail_on' should be a list, got str"]

    def test_valid_fail_on_values(self) -> None:
        config = {"fail_on": ["error", "warning", "note"], "checks": {}}
        warnings = validate_config(config)
        assert warnings == []


def test_validate_config_called_on_load(tmp_path, caplog):
    """validate_config is called by load_config."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "test"\n\n[tool.pycmdcheck]\nfail_on = ["banana"]\n'
    )
    # Should not raise, but should log a warning
    with caplog.at_level("WARNING", logger="pycmdcheck.config"):
        config = load_config(tmp_path)
    assert 'Config: Invalid fail_on value "banana".' in caplog.text
    # The invalid value should still be in the config (validate warns, doesn't fix)
    assert "banana" in config["fail_on"]


class TestMalformedConfig:
    """A project's own configuration can be wrong without breaking the run."""

    @pytest.mark.parametrize(
        ("table", "warning"),
        [
            ('checks = "all"', "'checks' should be a table, got str. Ignoring it."),
            ("checks = 3", "'checks' should be a table, got int. Ignoring it."),
            ('fail_on = [["error"]]', 'Invalid fail_on value ["error"]. '),
            ('fail_on = "error"', "'fail_on' should be a list, got str"),
            ('[tool.pycmdcheck.checks]\ndocs = "yes"', "value of: docs (str)"),
            ("[tool.pycmdcheck.checks]\ndocs = 3", "value of: docs (int)"),
        ],
    )
    def test_wrong_shapes_load_with_a_warning(
        self, tmp_path, caplog, table, warning
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(f"[tool.pycmdcheck]\n{table}\n")
        with caplog.at_level("WARNING", logger="pycmdcheck.config"):
            config = load_config(tmp_path)
        assert "Config:" in caplog.text
        assert warning in caplog.text
        assert isinstance(config["checks"], dict)
        assert is_check_enabled(config, "docs") is True
        assert get_check_config(config, "tests")["enabled"] is True

    def test_many_mistakes_are_one_line_that_names_each_entry(
        self, tmp_path, caplog
    ) -> None:
        """One line for each kind of mistake, and it names the entries."""
        entries = "\n".join(f'check{i} = "x"' for i in range(20))
        (tmp_path / "pyproject.toml").write_text(
            '[tool.pycmdcheck]\nfail_on = ["eror", "warn"]\n'
            f'[tool.pycmdcheck.checks]\n{entries}\nchangelog = "off"\n'
        )
        with caplog.at_level("WARNING", logger="pycmdcheck.config"):
            load_config(tmp_path)
        fail_on, checks = (record.getMessage() for record in caplog.records)
        assert fail_on == (
            'Config: Invalid fail_on value "eror", "warn". '
            "Valid values: error, note, warning"
        )
        assert checks.startswith(
            "Config: Each entry in 'checks' should be true, false, or a table. "
            "Ignoring the value of: "
        )
        named = checks.partition("of: ")[2].split(", ")
        # A built-in check is named first, wherever it sits in the table.
        assert named == ["changelog (str)", *(f"check{i} (str)" for i in range(20))]

    def test_a_long_list_of_mistakes_ends_in_a_count(self, tmp_path, caplog) -> None:
        """A checked project cannot make the line grow without limit."""
        entries = "\n".join(f'check{i} = "x"' for i in range(300))
        values = ", ".join(f'"v{i}"' for i in range(300))
        (tmp_path / "pyproject.toml").write_text(
            f"[tool.pycmdcheck]\nfail_on = [{values}]\n"
            f'[tool.pycmdcheck.checks]\n{entries}\nchangelog = "off"\n'
        )
        with caplog.at_level("WARNING", logger="pycmdcheck.config"):
            load_config(tmp_path)
        fail_on, checks = (record.getMessage() for record in caplog.records)
        assert '"v24", and 275 more. Valid values' in fail_on
        assert "of: changelog (str), check0 (str)," in checks
        assert checks.endswith("check23 (str), and 276 more")

    def test_unparseable_pyproject_falls_back_to_defaults(self, tmp_path, caplog):
        (tmp_path / "pyproject.toml").write_text("[project\nname =")
        with caplog.at_level("WARNING", logger="pycmdcheck.config"):
            config = load_config(tmp_path)
        assert "could not read pyproject.toml" in caplog.text
        assert config == DEFAULT_CONFIG

    def test_merge_does_not_share_values_with_the_override(self) -> None:
        override = {"checks": {"linting": {"args": ["--select=E"]}}}
        merged = _merge_config(DEFAULT_CONFIG, override)
        merged["checks"]["linting"]["args"].append("changed")
        assert override["checks"]["linting"]["args"] == ["--select=E"]
        assert merged["checks"]["linting"]["tool"] == "auto"
