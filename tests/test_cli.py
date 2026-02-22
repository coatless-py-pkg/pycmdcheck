"""Tests for the CLI interface."""

import json

from click.testing import CliRunner

from pycmdcheck.cli import main


class TestCLIBasic:
    """Basic CLI operation tests."""

    def test_exit_code_zero_on_valid_package(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [str(temp_package), "--no-parallel", "-c", "metadata", "-c", "structure"],
        )
        assert result.exit_code == 0, result.output

    def test_exit_code_one_on_bad_package(self, bad_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(bad_package), "--no-parallel", "-c", "metadata"]
        )
        assert result.exit_code == 1, result.output

    def test_invalid_path_errors(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["/nonexistent/path"])
        assert result.exit_code != 0

    def test_invalid_check_name_errors(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [str(temp_package), "-c", "nonexistent"])
        assert result.exit_code == 2
        assert "nonexistent" in result.output


class TestCLIVersion:
    """Test --version flag."""

    def test_version_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert (
            "pycmdcheck" in result.output.lower() or "version" in result.output.lower()
        )


class TestCLIList:
    """Test --list flag."""

    def test_list_shows_checks(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--list"])
        assert result.exit_code == 0
        assert "metadata" in result.output
        assert "structure" in result.output
        assert "tests" in result.output


class TestCLIJSON:
    """Test --json output."""

    def test_json_valid(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(temp_package), "--json", "--no-parallel", "-c", "metadata"]
        )
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, dict)

    def test_json_schema(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(temp_package), "--json", "--no-parallel", "-c", "metadata"]
        )
        data = json.loads(result.output)
        assert "package_path" in data
        assert "passed" in data
        assert "summary" in data
        assert "results" in data
        assert "ok" in data["summary"]
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "metadata"
        assert "status" in data["results"][0]
        assert "message" in data["results"][0]


class TestCLICheckFiltering:
    """Test --check and --skip filtering."""

    def test_single_check(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(temp_package), "--json", "--no-parallel", "-c", "metadata"]
        )
        data = json.loads(result.output)
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "metadata"

    def test_multiple_checks(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                str(temp_package),
                "--json",
                "--no-parallel",
                "-c",
                "metadata",
                "-c",
                "structure",
            ],
        )
        data = json.loads(result.output)
        names = {r["name"] for r in data["results"]}
        assert names == {"metadata", "structure"}

    def test_skip_check(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                str(temp_package),
                "--json",
                "--no-parallel",
                "-s",
                "typing",
                "-s",
                "linting",
            ],
        )
        data = json.loads(result.output)
        names = {r["name"] for r in data["results"]}
        assert "typing" not in names
        assert "linting" not in names


class TestCLIVerbose:
    """Test --verbose flag."""

    def test_verbose_shows_timing(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(temp_package), "--no-parallel", "-c", "metadata", "-v"]
        )
        assert result.exit_code == 0
        # Verbose mode adds a Time column with duration values
        assert "s" in result.output


class TestCLIFailOn:
    """Test --fail-on flag."""

    def test_fail_on_error_default(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main,
            [str(temp_package), "--no-parallel", "-c", "metadata", "-c", "structure"],
        )
        assert result.exit_code == 0

    def test_fail_on_warning(self, minimal_package) -> None:
        runner = CliRunner()
        # minimal_package has no LICENSE, which produces a WARNING
        result = runner.invoke(
            main,
            [
                str(minimal_package),
                "--no-parallel",
                "-c",
                "license",
                "--fail-on",
                "error",
                "--fail-on",
                "warning",
            ],
        )
        assert result.exit_code == 1


class TestCLIFailFast:
    """Test --fail-fast flag."""

    def test_fail_fast_stops_early_sequential(self, bad_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(bad_package), "--json", "--no-parallel", "--fail-fast"]
        )
        data = json.loads(result.output)
        # Should have fewer results than running all checks
        error_results = [r for r in data["results"] if r["status"] == "error"]
        assert len(error_results) >= 1

    def test_fail_fast_stops_early_parallel(self, bad_package) -> None:
        runner = CliRunner()
        result = runner.invoke(main, [str(bad_package), "--json", "--fail-fast"])
        data = json.loads(result.output)
        error_results = [r for r in data["results"] if r["status"] == "error"]
        assert len(error_results) >= 1
