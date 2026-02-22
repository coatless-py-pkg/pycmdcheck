"""Tests for the profile system."""

from click.testing import CliRunner

from pycmdcheck.cli import main
from pycmdcheck.profiles import get_profile, list_profiles


class TestProfileModule:
    """Tests for profiles.py module."""

    def test_get_profile_valid(self) -> None:
        """Known profile names return a Profile."""
        for name in ("minimal", "default", "pyopensci", "strict"):
            assert get_profile(name) is not None

    def test_get_profile_invalid(self) -> None:
        """Unknown profile name returns None."""
        assert get_profile("nonexistent") is None

    def test_list_profiles(self) -> None:
        """list_profiles returns all profiles."""
        profiles = list_profiles()
        names = [name for name, _ in profiles]
        assert "minimal" in names
        assert "default" in names
        assert "pyopensci" in names
        assert "strict" in names

    def test_minimal_profile_checks(self) -> None:
        """Minimal profile has only metadata, structure, license."""
        prof = get_profile("minimal")
        assert prof is not None
        assert prof.checks == frozenset({"metadata", "structure", "license"})

    def test_pyopensci_profile_has_new_checks(self) -> None:
        """pyOpenSci profile includes community, ci, changelog, etc."""
        prof = get_profile("pyopensci")
        assert prof is not None
        assert "community" in prof.checks
        assert "ci" in prof.checks
        assert "changelog" in prof.checks
        assert "citation" in prof.checks
        assert "python_versions" in prof.checks

    def test_pyopensci_config_overrides(self) -> None:
        """pyOpenSci profile enables docstring and section checks."""
        prof = get_profile("pyopensci")
        assert prof is not None
        docs_config = prof.config_overrides.get("docs", {})
        assert docs_config.get("check_docstrings") is True
        assert docs_config.get("check_readme_sections") is True


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
