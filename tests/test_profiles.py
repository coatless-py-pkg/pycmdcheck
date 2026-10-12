"""Tests for the profile system."""

import copy
import inspect
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from pycmdcheck import check, discovery
from pycmdcheck.cli import main
from pycmdcheck.config import get_check_config
from pycmdcheck.profiles import (
    ALL_CHECKS,
    CORE_CHECKS,
    DEPRECATED_CHECKS,
    DYNAMIC_CHECKS,
    ORIGINAL_CHECKS,
    PROFILES,
    STATIC_CHECKS,
    apply_profile,
    get_profile,
    list_profiles,
)
from pycmdcheck.pyproject_reader import read_pyproject
from pycmdcheck.runner import run_checks
from tests.helpers import TRIAGE_CHECKS


class TestProfileModule:
    """Tests for profiles.py module."""

    def test_all_checks_matches_registered_built_in_checks(
        self, repo_root: Path
    ) -> None:
        """The rosters must equal the built-in checks registered in pyproject.toml.

        This guards against silent drift: adding or removing a check entry point
        without updating ALL_CHECKS (and thus the pyopensci/strict/triage
        profiles) fails here instead of quietly shipping an incomplete profile.
        A deprecated check stays registered, so that naming it keeps working.
        """
        data = read_pyproject(repo_root)
        assert data is not None
        registered = set(data["project"]["entry-points"]["pycmdcheck.checks"])
        assert ALL_CHECKS | DEPRECATED_CHECKS == registered

    def test_curated_subsets_reference_real_checks(self) -> None:
        """The curated/derived profile sets only reference real built-in checks."""
        assert CORE_CHECKS <= ALL_CHECKS
        # The old name of the constant still works.
        assert ORIGINAL_CHECKS is CORE_CHECKS
        assert DYNAMIC_CHECKS <= ALL_CHECKS
        assert STATIC_CHECKS <= ALL_CHECKS

    def test_no_roster_or_profile_holds_a_deprecated_check(self) -> None:
        """A deprecated check only runs when it is named."""
        assert DEPRECATED_CHECKS == {"imports"}
        assert DEPRECATED_CHECKS.isdisjoint(ALL_CHECKS)
        for profile in PROFILES.values():
            assert DEPRECATED_CHECKS.isdisjoint(profile.checks), profile.name

    def test_get_profile_valid(self) -> None:
        """Each known profile name returns its Profile."""
        for name in PROFILES:
            assert get_profile(name) is PROFILES[name]

    def test_get_profile_invalid(self) -> None:
        """Unknown profile name returns None."""
        assert get_profile("nonexistent") is None

    def test_list_profiles(self) -> None:
        """list_profiles returns all profiles."""
        names = [name for name, _ in list_profiles()]
        assert names == ["minimal", "triage", "core", "default", "pyopensci", "strict"]
        assert names == list(PROFILES)

    def test_triage_profile_excludes_dynamic_checks(self) -> None:
        """The triage profile contains no install/tool/network checks."""
        prof = get_profile("triage")
        assert prof is not None
        assert prof.checks.isdisjoint(DYNAMIC_CHECKS)


class TestCLIProfile:
    """Tests for --profile flag in CLI."""

    def test_list_profiles_flag(self) -> None:
        """--list-profiles shows available profiles."""
        runner = CliRunner()
        result = runner.invoke(main, ["--list-profiles"])
        assert result.exit_code == 0
        assert "minimal" in result.output
        assert "pyopensci" in result.output

    def test_profile_minimal(self, temp_package) -> None:
        """--profile minimal runs only 3 checks."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [str(temp_package), "--json", "--no-parallel", "--profile", "minimal"],
        )
        import json

        data = json.loads(result.output)
        names = {r["name"] for r in data["results"]}
        assert names == {"metadata", "structure", "license"}

    def test_profile_with_skip(self, temp_package) -> None:
        """--profile combined with --skip."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                str(temp_package),
                "--json",
                "--no-parallel",
                "--profile",
                "minimal",
                "-s",
                "license",
            ],
        )
        import json

        data = json.loads(result.output)
        names = {r["name"] for r in data["results"]}
        assert "license" not in names
        assert "metadata" in names


class TestProfileContents:
    """Pin what each profile selects, so a change is always deliberate."""

    def test_minimal(self) -> None:
        assert PROFILES["minimal"].checks == {"metadata", "structure", "license"}

    def test_triage_is_the_static_set(self) -> None:
        assert PROFILES["triage"].checks == {
            "changelog",
            "ci",
            "citation",
            "community",
            "docs",
            "license",
            "metadata",
            "py_typed",
            "python_versions",
            "structure",
            "test_suite",
            "version",
        }

    def test_triage_sets_every_option_of_the_docs_check(self) -> None:
        assert PROFILES["triage"].config_overrides == {
            "docs": {
                "require_readme": True,
                "check_readme_sections": True,
                "check_docs_site": True,
                "check_docstrings": False,
                "min_docstring_coverage": 0,
            },
        }

    def test_core(self) -> None:
        assert PROFILES["core"].checks == {
            "build",
            "dependencies",
            "docs",
            "formatting",
            "license",
            "linting",
            "metadata",
            "py_typed",
            "structure",
            "test_suite",
            "tests",
            "typing",
            "version",
        }
        assert len(PROFILES["core"].checks) == 13

    def test_default_is_an_alias_of_core(self) -> None:
        core, default = PROFILES["core"], PROFILES["default"]
        assert default.checks == core.checks
        assert default.config_overrides == core.config_overrides
        assert default.description == "Deprecated alias of core"

    def test_pyopensci_and_strict_run_everything(self) -> None:
        assert PROFILES["pyopensci"].checks == ALL_CHECKS
        assert PROFILES["strict"].checks == ALL_CHECKS
        assert len(ALL_CHECKS) == 20

    def test_strict_names_its_linter_and_type_checker(self) -> None:
        """strict lints and type checks a project that has adopted no tool."""
        assert PROFILES["strict"].config_overrides == {
            "docs": {
                "require_readme": True,
                "check_docstrings": True,
                "check_readme_sections": True,
                "check_docs_site": True,
                "min_docstring_coverage": 80,
            },
            "linting": {"tool": "ruff"},
            "typing": {"tool": "mypy", "strict": True},
        }

    def test_pyopensci_leaves_the_tools_to_detection(self) -> None:
        """pyopensci sets every option of the docs check and names no tool."""
        assert PROFILES["pyopensci"].config_overrides == {
            "docs": {
                "require_readme": True,
                "check_docstrings": True,
                "check_readme_sections": True,
                "check_docs_site": True,
                "min_docstring_coverage": 50,
            },
        }


class TestApplyProfile:
    """apply_profile merges a profile's options without touching its inputs."""

    def test_options_reach_the_check_config(self) -> None:
        merged = apply_profile({"checks": {}}, "strict")
        assert get_check_config(merged, "typing")["strict"] is True
        assert get_check_config(merged, "typing")["tool"] == "mypy"
        assert get_check_config(merged, "linting")["tool"] == "ruff"
        assert get_check_config(merged, "docs")["min_docstring_coverage"] == 80

    def test_profile_wins_and_other_user_options_survive(self) -> None:
        config = {"checks": {"docs": {"check_docstrings": False, "min_words": 10}}}
        docs = apply_profile(config, "pyopensci")["checks"]["docs"]
        assert docs["check_docstrings"] is True
        assert docs["min_words"] == 10

    def test_boolean_shorthand_keeps_its_enabled_flag(self) -> None:
        merged = apply_profile({"checks": {"docs": False}}, "triage")
        assert merged["checks"]["docs"]["enabled"] is False
        assert merged["checks"]["docs"]["require_readme"] is True

    def test_inputs_are_not_modified(self) -> None:
        config = {"checks": {"docs": {"check_docstrings": False}}}
        before = copy.deepcopy(PROFILES["strict"].config_overrides)
        merged = apply_profile(config, "strict")
        merged["checks"]["typing"]["strict"] = "changed"
        assert config == {"checks": {"docs": {"check_docstrings": False}}}
        assert PROFILES["strict"].config_overrides == before

    def test_config_with_no_checks_table(self) -> None:
        """A configuration passed through the Python API may hold none."""
        for config in ({}, {"checks": "all"}):
            options = apply_profile(config, "strict")["checks"]["typing"]
            assert options == {"tool": "mypy", "strict": True}

    def test_unknown_profile(self) -> None:
        with pytest.raises(ValueError, match="Unknown profile 'nope'"):
            apply_profile({}, "nope")


class TestDefaultIsDeprecated:
    """The old name still works and says once that it is going away."""

    def _warnings(self, caplog: pytest.LogCaptureFixture) -> list[str]:
        return [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]

    @pytest.mark.parametrize("run", [run_checks, check])
    def test_python_api_logs_one_warning(self, tmp_path, caplog, run) -> None:
        with caplog.at_level("WARNING", logger="pycmdcheck.profiles"):
            report = run(tmp_path, checks=["license"], profile="default")
        assert [r.name for r in report.results] == ["license"]
        (message,) = self._warnings(caplog)
        assert "'default'" in message
        assert "deprecated" in message
        assert "'core'" in message

    def test_cli_logs_one_warning(self, tmp_path, caplog) -> None:
        args = [str(tmp_path), "--json", "--profile", "default", "-c", "license"]
        with caplog.at_level("WARNING", logger="pycmdcheck.profiles"):
            result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert len(self._warnings(caplog)) == 1

    def test_core_is_quiet(self, tmp_path, caplog) -> None:
        args = [str(tmp_path), "--json", "--profile", "core", "-c", "license"]
        with caplog.at_level("WARNING", logger="pycmdcheck.profiles"):
            result = CliRunner().invoke(main, args)
            run_checks(tmp_path, checks=["license"], profile="core")
        assert result.exit_code == 0, result.output
        assert self._warnings(caplog) == []


class TestTriageOptions:
    """A checked repository cannot change a triage run through its settings."""

    def test_scan_reads_each_form(self, options_read) -> None:
        source = (
            'a = config.get("one", 1)\n'
            'b = config["two"]\n'
            'c = get_number(config, "three", 0)\n'
            "d = helper(config, default)\n"
            "e = config.get(name)\n"
        )
        assert options_read(source) == ({"one", "two", "three"}, [4, 5])

    def test_profile_sets_every_option_a_static_check_reads(self, options_read) -> None:
        """The names come from the source, so a new option fails here."""
        classes = discovery.discover_checks()
        found: set[str] = set()
        for name in sorted(STATIC_CHECKS):
            cls = classes[name]
            # The scan looks for the name config, so run() has to use it.
            assert list(inspect.signature(cls.run).parameters)[2] == "config", name
            read, unread = options_read(inspect.getsource(sys.modules[cls.__module__]))
            assert unread == [], f"the scan cannot read how {name} uses config"
            assert read <= set(PROFILES["triage"].config_overrides.get(name, {})), name
            found |= read
        # A scan that finds nothing would pass without testing anything.
        assert found


def test_triage_starts_no_process(temp_package: Path, monkeypatch) -> None:
    """The triage profile only reads files."""
    started: list[object] = []
    real_popen = subprocess.Popen

    def recording_popen(*args: object, **kwargs: object) -> object:
        started.append(args)
        return real_popen(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", recording_popen)
    report = run_checks(temp_package, profile="triage", parallel=False)
    assert len(report.results) == TRIAGE_CHECKS
    assert started == []
