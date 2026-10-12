"""Tests for the CLI interface."""

import io
import json
import os
import re
import subprocess
import sys
import tomllib
from collections.abc import Callable
from pathlib import Path, PurePosixPath

import click
import pytest
from click.testing import CliRunner
from rich.console import Console

from pycmdcheck import __version__
from pycmdcheck.cli import (
    _closing_line,
    _output_rich,
    _render_markdown,
    _resolve_fail_on,
    _show_rules,
    main,
)
from pycmdcheck.profiles import ALL_CHECKS, PROFILES
from pycmdcheck.results import CheckResult, CheckStatus, Finding, Report
from pycmdcheck.runner import RUNNER_HINTS, RUNNER_RULES
from tests.helpers import JSON_KEYS, RESULT_KEYS, TRIAGE


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

    def test_exit_zero_suppresses_findings_exit(self, bad_package) -> None:
        """--exit-zero exits 0 even when checks fail (results still reported)."""
        runner = CliRunner()
        result = runner.invoke(
            main,
            [
                str(bad_package),
                "--no-parallel",
                "--exit-zero",
                "--json",
                "-c",
                "metadata",
            ],
        )
        assert result.exit_code == 0, result.output
        # The failure is still reported in the output, not hidden.
        data = json.loads(result.output)
        assert data["passed"] is False
        assert data["summary"]["error"] >= 1

    def test_exit_zero_still_reports_usage_errors(self, temp_package) -> None:
        """--exit-zero suppresses findings, not genuine misuse (unknown check)."""
        runner = CliRunner()
        result = runner.invoke(
            main, [str(temp_package), "--exit-zero", "-c", "nonexistent"]
        )
        assert result.exit_code == 2


class TestCLIVersion:
    """Test --version flag."""

    def test_version_output(self) -> None:
        runner = CliRunner()
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        # click writes the words in between, so only the two ends are compared.
        assert result.output.startswith("pycmdcheck")
        assert result.output.endswith(f" {__version__}\n")


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

    def test_json_schema(self, temp_package) -> None:
        runner = CliRunner()
        result = runner.invoke(
            main, [str(temp_package), "--json", "--no-parallel", "-c", "metadata"]
        )
        assert result.exit_code == 0, result.output
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
    """Test --check filtering."""

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


class TestCLIFailOn:
    """Test --fail-on flag."""

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


# ---------------------------------------------------------------------------
# Contract tests: exit codes, streams, and rendering
# ---------------------------------------------------------------------------


def _report(*statuses: CheckStatus) -> Report:
    """Build a report with one result per status."""
    report = Report(package_path="/pkg")
    for index, status in enumerate(statuses):
        report.add(CheckResult(f"check{index}", status, f"{status.value} result"))
    return report


@pytest.fixture
def fake_run(monkeypatch: pytest.MonkeyPatch):
    """Make the CLI report the given statuses without running any check."""

    def _install(*statuses: CheckStatus) -> None:
        monkeypatch.setattr(
            "pycmdcheck.cli.run_checks", lambda **kwargs: _report(*statuses)
        )

    return _install


@pytest.fixture
def asked(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, object]]:
    """Record what the runner is asked for, without running any check."""
    calls: list[dict[str, object]] = []

    def record(**kwargs: object) -> Report:
        calls.append(kwargs)
        return _report(CheckStatus.OK)

    monkeypatch.setattr("pycmdcheck.cli.run_checks", record)
    return calls


def _styled(show: Callable[[Console], None]) -> list[str]:
    """Return the lines that *show* prints on a console that renders styles."""
    buffer = io.StringIO()
    show(Console(file=buffer, width=80, force_terminal=True, color_system="standard"))
    return buffer.getvalue().splitlines()


def _rows(text: str) -> list[list[str]]:
    """Return the cells of each table row in a text report, without styling."""
    bars = "\N{BOX DRAWINGS LIGHT VERTICAL}\N{BOX DRAWINGS HEAVY VERTICAL}|"
    return [
        [cell.strip() for cell in re.split(f"[{bars}]", line)[1:-1]]
        for line in re.sub(r"\x1b\[[0-9;]*m", "", text).splitlines()
    ]


class TestMalformedInput:
    """A fault inside pycmdcheck is not reported as a result of the project."""

    def test_internal_error_exits_3_with_empty_stdout(
        self, tmp_path, monkeypatch
    ) -> None:
        def explode(**kwargs: object) -> Report:
            raise RuntimeError("boom")

        monkeypatch.setattr("pycmdcheck.cli.run_checks", explode)
        result = CliRunner().invoke(main, [*TRIAGE, str(tmp_path)])
        assert result.exit_code == 3
        assert result.stdout == ""
        assert "internal error: RuntimeError: boom" in result.stderr


MINIMAL = ",".join(PROFILES["minimal"].checks)


class TestUsageErrors:
    """Misuse exits 2 and keeps stdout clean."""

    def test_unknown_check_with_json(self, tmp_path) -> None:
        result = CliRunner().invoke(main, ["--json", "-c", "nope", str(tmp_path)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Unknown check(s): nope" in result.stderr

    @pytest.mark.parametrize("name", ["missing", "file.txt"])
    def test_path_that_is_not_a_directory(self, tmp_path, name) -> None:
        (tmp_path / "file.txt").write_text("x")
        assert CliRunner().invoke(main, [str(tmp_path / name)]).exit_code == 2

    @pytest.mark.parametrize(
        "selection", [["--profile", "triage"], ["-c", "license"], ["--exit-zero"]]
    )
    def test_unknown_skip_name(self, tmp_path, fake_run, selection) -> None:
        """A misspelled name would leave in the check it was meant to take out."""
        fake_run(CheckStatus.OK)
        args = ["--json", *selection, "-s", "typo", "-s", "docs", "-s", "nope"]
        result = CliRunner().invoke(main, [*args, str(tmp_path)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Unknown check(s) given to --skip: nope, typo." in result.stderr
        assert "Available checks: " in result.stderr

    @pytest.mark.parametrize(
        "selection",
        [
            ["-c", "docs", "-s", "docs"],
            ["--profile", "minimal", "-s", MINIMAL],
            # No check and no profile is named, so the run starts from them all.
            ["-s", ",".join(ALL_CHECKS)],
        ],
    )
    @pytest.mark.parametrize("mode", [[], ["--json"], ["--format", "markdown"]])
    def test_skip_that_leaves_no_check(self, tmp_path, asked, selection, mode) -> None:
        """An empty report would look like a pass."""
        args = [*mode, *selection, "--exit-zero", str(tmp_path)]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "--skip leaves no check to run." in result.stderr
        assert asked == []

    @pytest.mark.parametrize("off", ["false", "{enabled = false}", "{enabled = 0}"])
    @pytest.mark.parametrize(
        "on", ["", "docs = true\n", "docs = {require_readme = false}\n"]
    )
    @pytest.mark.parametrize("mode", [[], ["--json"]])
    def test_skip_of_the_one_check_a_project_leaves_on(
        self, tmp_path, asked, off, on, mode
    ) -> None:
        """What the project turned off does not run, so nothing would be left."""
        rest = "".join(f"{name} = {off}\n" for name in ALL_CHECKS if name != "docs")
        (tmp_path / "pyproject.toml").write_text(
            f"[tool.pycmdcheck.checks]\n{rest}{on}"
        )
        args = [*mode, "-s", "docs", "--exit-zero", str(tmp_path)]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "--skip leaves no check to run." in result.stderr
        assert asked == []

    @pytest.mark.parametrize(
        ("off", "selection", "runs"),
        [
            # Named checks and a profile run whatever the project turned off.
            (ALL_CHECKS, ["-c", "docs", "-s", "docs"], False),
            (ALL_CHECKS, ["--profile", "minimal", "-s", MINIMAL], False),
            (ALL_CHECKS, ["-c", "docs,license", "-s", "docs"], True),
            (
                ["license", "structure"],
                ["--profile", "minimal", "-s", "metadata"],
                True,
            ),
            (["license"], ["-c", "license,metadata", "-s", "metadata"], True),
        ],
    )
    def test_project_table_is_not_read_for_named_checks_or_a_profile(
        self, tmp_path, asked, off, selection, runs
    ) -> None:
        table = "".join(f"{name} = false\n" for name in off)
        (tmp_path / "pyproject.toml").write_text(f"[tool.pycmdcheck.checks]\n{table}")
        result = CliRunner().invoke(main, ["--json", *selection, str(tmp_path)])
        assert result.exit_code == (0 if runs else 2), result.output
        assert ("--skip leaves no check to run." in result.stderr) is not runs
        assert len(asked) == runs

    @pytest.mark.parametrize("skipped", ["docs", "metadata", "docs,metadata"])
    def test_skip_that_leaves_a_check_the_project_has_on(
        self, tmp_path, asked, skipped
    ) -> None:
        keep = ("docs", "license")
        rest = "".join(f"{name} = false\n" for name in ALL_CHECKS if name not in keep)
        (tmp_path / "pyproject.toml").write_text(f"[tool.pycmdcheck.checks]\n{rest}")
        result = CliRunner().invoke(main, ["-s", skipped, str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert [call["skip"] for call in asked] == [skipped.split(",")]

    @pytest.mark.parametrize("skip", [[], ["-s", "docs"], ["-s", "docs,license"]])
    def test_project_that_turns_every_check_off(self, tmp_path, skip) -> None:
        """That is the choice of the project and no mistake on the command line."""
        off = "".join(f"{name} = false\n" for name in ALL_CHECKS)
        (tmp_path / "pyproject.toml").write_text(f"[tool.pycmdcheck.checks]\n{off}")
        result = CliRunner().invoke(main, [*skip, str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert "No checks were run." in result.stdout

    def test_no_registered_checks(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: {})
        result = CliRunner().invoke(main, ["--json", str(tmp_path)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "No checks are registered" in result.stderr

    def test_no_registered_checks_is_not_blamed_on_the_config_file(
        self, tmp_path, monkeypatch
    ) -> None:
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: {})
        config = tmp_path / "triage.toml"
        config.write_text('profile = "triage"\n[checks]\ncitation = false\n')
        result = CliRunner().invoke(main, ["--config", str(config), str(tmp_path)])
        assert result.exit_code == 2
        assert "No checks are registered" in result.stderr
        assert "Config file" not in result.stderr

    def test_short_help(self) -> None:
        result = CliRunner().invoke(main, ["-h"])
        assert result.exit_code == 0
        assert "Exit codes:" in result.output


class TestNameLists:
    """--check and --skip take one name, or several separated by commas."""

    @pytest.mark.parametrize(
        ("args", "names"),
        [
            (["--check", "tests,linting,docs"], ["tests", "linting", "docs"]),
            (["-c", " tests , linting "], ["tests", "linting"]),
            # The option repeated, and the two forms mixed.
            (["-c", "tests", "-c", "linting,docs"], ["tests", "linting", "docs"]),
            # The order of first mention, and each name once.
            (["-c", "docs,tests", "-c", "tests,ci,docs"], ["docs", "tests", "ci"]),
        ],
    )
    def test_check(self, tmp_path, asked, args, names) -> None:
        result = CliRunner().invoke(main, [str(tmp_path), "--json", *args])
        assert result.exit_code == 0, result.output
        assert asked[0]["checks"] == names
        assert json.loads(result.stdout)["requested_checks"] == names

    @pytest.mark.parametrize(
        ("args", "names"),
        [
            (["--skip", "citation,community"], ["citation", "community"]),
            (["-s", " docs ,ci", "-s", "docs", "-s", "urls"], ["docs", "ci", "urls"]),
        ],
    )
    def test_skip(self, tmp_path, asked, args, names) -> None:
        args = [str(tmp_path), "--json", "--profile", "triage", *args]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert asked[0]["skip"] == names
        assert json.loads(result.stdout)["skipped_checks"] == names

    @pytest.mark.parametrize("option", ["--check", "--skip"])
    @pytest.mark.parametrize("value", ["", " ", ",", " , ,"])
    def test_value_that_names_no_check(self, tmp_path, asked, option, value) -> None:
        """An empty list built by a script must not end up running every check."""
        # A name given beside it does not make up for the empty value.
        for args in ([option, value], [option, "docs", option, value]):
            result = CliRunner().invoke(main, [str(tmp_path), "--json", *args])
            assert result.exit_code == 2
            assert result.stdout == ""
            assert f"{option} needs at least one check name." in result.stderr
        assert asked == []

    @pytest.mark.parametrize("option", ["--check", "--skip"])
    @pytest.mark.parametrize("value", ["license,", ",license", "license,,docs"])
    def test_empty_name_in_a_list(self, tmp_path, asked, option, value) -> None:
        """A space typed after a comma would make the next name the PATH.

        The shell splits "--check license, docs" into the value "license,"
        and the word "docs", which is a directory in most projects.
        """
        (tmp_path / "docs").mkdir()
        args = ["--json", option, value, str(tmp_path / "docs")]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert f'{option} has an empty name in "{value}".' in result.stderr
        assert "as in tests,linting." in result.stderr
        assert asked == []

    def test_list_with_a_config_file(self, tmp_path, asked) -> None:
        """A list is held to the checks of the file, as single names are."""
        config = tmp_path / "two.toml"
        config.write_text('profile = "minimal"\n[checks]\nstructure = false\n')
        args = [str(tmp_path), "--json", "--config", str(config)]
        result = CliRunner().invoke(main, [*args, "-c", "metadata,license"])
        assert result.exit_code == 0, result.output
        assert asked[0]["checks"] == ["metadata", "license"]
        result = CliRunner().invoke(main, [*args, "-c", "license,changelog"])
        assert result.exit_code == 2
        assert "Check(s) not run by the config file: changelog." in result.stderr
        result = CliRunner().invoke(main, [*args, "-s", "license,metadata"])
        assert result.exit_code == 2
        assert "--skip leaves no check to run." in result.stderr
        assert len(asked) == 1

    def test_unknown_names_in_a_list(self, tmp_path, asked) -> None:
        result = CliRunner().invoke(main, [str(tmp_path), "-c", "docs,nope, typo"])
        assert result.exit_code == 2
        assert "Unknown check(s): nope, typo." in result.stderr
        result = CliRunner().invoke(main, [str(tmp_path), "-s", "docs,nope"])
        assert result.exit_code == 2
        assert "Unknown check(s) given to --skip: nope." in result.stderr
        assert asked == []


class TestJSONListings:
    @pytest.mark.parametrize("as_json", [["--json"], ["--format", "json"]])
    @pytest.mark.parametrize(
        ("flag", "expected"), [("--list", "metadata"), ("--list-profiles", "triage")]
    )
    def test_listing_is_json(self, flag, expected, as_json) -> None:
        result = CliRunner().invoke(main, [flag, *as_json])
        assert result.exit_code == 0
        listing = json.loads(result.stdout)
        assert expected in {item["name"] for item in listing}
        assert all(isinstance(item["description"], str) for item in listing)

    def test_a_check_has_a_name_and_a_description_only(self) -> None:
        listing = json.loads(CliRunner().invoke(main, ["--list", "--json"]).stdout)
        assert {tuple(item) for item in listing} == {("name", "description")}


class TestListProfiles:
    """--list-profiles shows the checks of each profile and the options it sets."""

    def test_json(self) -> None:
        result = CliRunner().invoke(main, ["--list-profiles", "--json"])
        assert result.exit_code == 0, result.output
        listing = json.loads(result.stdout)
        assert [item["name"] for item in listing] == list(PROFILES)
        for item in listing:
            profile = PROFILES[item["name"]]
            assert list(item) == ["name", "description", "checks", "options"]
            assert item["description"] == profile.description
            assert item["checks"] == sorted(profile.checks)
            assert item["options"] == profile.config_overrides
        by_name = {item["name"]: item for item in listing}
        assert by_name["minimal"]["checks"] == ["license", "metadata", "structure"]
        assert by_name["minimal"]["options"] == {}
        assert by_name["strict"]["options"]["typing"] == {
            "tool": "mypy",
            "strict": True,
        }

    @pytest.mark.parametrize("columns", [80, 100])
    def test_text(self, monkeypatch, columns) -> None:
        # A width of its own, whatever COLUMNS was when the tests started.
        monkeypatch.setattr("pycmdcheck.cli.console", Console(width=columns))
        result = CliRunner().invoke(main, ["--list-profiles"])
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        assert max(map(len, lines)) <= columns
        # Nothing is cut short to fit.
        assert "\N{HORIZONTAL ELLIPSIS}" not in result.stdout
        # The list of checks may run over several lines of its cell.
        flat = " ".join(re.sub(r"[^ -~]", " ", result.stdout).split())
        for profile in PROFILES.values():
            assert profile.description in flat
            assert f"Runs {', '.join(sorted(profile.checks))}" in flat
        # Each option has a line of its own, written as in a TOML file.
        cells = {line.strip("\N{BOX DRAWINGS LIGHT VERTICAL} ") for line in lines}
        assert "Sets docs.require_readme = true" in cells
        assert "Sets docs.check_docstrings = false" in cells
        assert "Sets docs.min_docstring_coverage = 80" in cells
        assert 'Sets linting.tool = "ruff"' in cells
        options = [
            option
            for profile in PROFILES.values()
            for options in profile.config_overrides.values()
            for option in options
        ]
        assert sum(" Sets " in line for line in lines) == len(options)


class TestListRules:
    """--list-rules prints every rule of every registered check."""

    RULES = [
        {
            "check": "alpha",
            "code": "alpha.only-rule",
            "description": "The only rule",
            "hint": "Add the file.",
        },
        # A plugin with rules and no hints.
        {"check": "zeta", "code": "zeta.a-rule", "description": "First", "hint": None},
        {"check": "zeta", "code": "zeta.b-rule", "description": "Second", "hint": None},
    ]
    # The runner's own rules, which no check class holds.
    RUNNER = [
        {
            "check": "pycmdcheck",
            "code": code,
            "description": description,
            "hint": RUNNER_HINTS[code],
        }
        for code, description in sorted(RUNNER_RULES.items())
    ]

    @pytest.fixture
    def stub_rules(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Register two checks with rules and a plugin that has none."""

        class Zeta:
            rules = {"zeta.b-rule": "Second", "zeta.a-rule": "First"}

        class Alpha:
            rules = {"alpha.only-rule": "The only rule"}
            hints = {"alpha.only-rule": "Add the file."}

        class Plain:
            description = "A plugin written before there were rules"

        registry = {"zeta": Zeta, "plain": Plain, "alpha": Alpha}
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: registry)

    @pytest.mark.parametrize("as_json", [["--json"], ["--format", "json"]])
    def test_json_is_sorted_by_code(self, stub_rules, as_json) -> None:
        result = CliRunner().invoke(main, ["--list-rules", *as_json])
        assert result.exit_code == 0, result.output
        alpha, *zeta = self.RULES
        assert json.loads(result.stdout) == [alpha, *self.RUNNER, *zeta]

    def test_text(self, stub_rules) -> None:
        result = CliRunner().invoke(main, ["--list-rules"])
        assert result.exit_code == 0, result.output
        lines = result.stdout.splitlines()
        for rule in [*self.RULES, *self.RUNNER]:
            start = lines.index(f"{rule['code']} ({rule['check']})")
            # The lines of a rule end at the next empty line.
            end = lines.index("", start) if "" in lines[start:] else len(lines)
            expected = [rule["description"]]
            if rule["hint"]:
                expected.append(f"Suggestion: {rule['hint']}")
            assert " ".join(map(str.strip, lines[start + 1 : end])) == " ".join(
                expected
            )

    def test_text_keeps_the_names_in_a_long_hint_whole(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """At 80 columns a hint wraps between words, never inside one.

        The rest of the hint is two spaces further in than its first line.
        """
        hint = (
            "Remove --outdir from args in [tool.pycmdcheck.checks.build] of "
            "pyproject.toml, so the build writes where the check looks."
        )

        class Build:
            rules = {"build.missing-artifacts": "The build produced no files"}
            hints = {"build.missing-artifacts": hint}

        registry = {"build": Build}
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: registry)
        lines = CliRunner().invoke(main, ["--list-rules"]).stdout.splitlines()
        start = lines.index("build.missing-artifacts (build)")
        assert max(map(len, lines)) <= 80
        assert lines[start + 2 : start + 4] == [
            "  Suggestion: Remove --outdir from args in "
            "[tool.pycmdcheck.checks.build] of",
            "    pyproject.toml, so the build writes where the check looks.",
        ]

    def test_text_keeps_a_long_description_inside_its_rule(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class Build:
            rules = {
                "build.no-files": (
                    "The build wrote no wheel and no source archive to the "
                    "dist directory"
                )
            }

        registry = {"build": Build}
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: registry)
        buffer = io.StringIO()
        _show_rules(Console(file=buffer, width=40))
        lines = buffer.getvalue().splitlines()
        start = lines.index("build.no-files (build)")
        assert lines[start + 1 : start + 3] == [
            "  The build wrote no wheel and no source",
            "    archive to the dist directory",
        ]

    def test_text_shows_the_label_in_bold(self, stub_rules) -> None:
        assert "  \x1b[1mSuggestion:\x1b[0m Add the file." in _styled(_show_rules)

    @pytest.mark.parametrize("hints", [None, 42, {"odd.rule": 42}, {"odd.rule": " "}])
    def test_hints_table_that_is_not_text_is_left_out(
        self, monkeypatch: pytest.MonkeyPatch, hints: object
    ) -> None:
        """A plugin's mistake does not stop the listing of every other rule."""

        class Odd:
            rules = {"odd.rule": "A rule"}

        Odd.hints = hints  # type: ignore[attr-defined]
        registry = {"odd": Odd}
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: registry)
        result = CliRunner().invoke(main, ["--list-rules", "--json"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)[0] == {
            "check": "odd",
            "code": "odd.rule",
            "description": "A rule",
            "hint": None,
        }
        assert CliRunner().invoke(main, ["--list-rules"]).exit_code == 0

    @pytest.mark.parametrize("rules", [None, 42, ["odd.rule"]])
    def test_rules_table_that_is_not_a_dict_is_left_out(
        self, monkeypatch: pytest.MonkeyPatch, rules: object
    ) -> None:
        """The same holds for the rules: the other checks are still listed."""

        class Odd:
            hints = {"odd.rule": "Add the file."}

        Odd.rules = rules  # type: ignore[attr-defined]
        registry = {"odd": Odd}
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: registry)
        result = CliRunner().invoke(main, ["--list-rules", "--json"])
        assert result.exit_code == 0, result.output
        assert {rule["check"] for rule in json.loads(result.stdout)} == {"pycmdcheck"}
        assert CliRunner().invoke(main, ["--list-rules"]).exit_code == 0

    def test_it_runs_no_check(self, stub_rules, tmp_path, monkeypatch) -> None:
        def explode(**kwargs: object) -> Report:
            raise AssertionError("--list-rules must not run a check")

        monkeypatch.setattr("pycmdcheck.cli.run_checks", explode)
        result = CliRunner().invoke(main, ["--list-rules", str(tmp_path)])
        assert result.exit_code == 0, result.output

    def test_built_in_rules_are_well_formed(self) -> None:
        """A code is "<check name>.<rule>", lower case, hyphens in the rule part."""
        result = CliRunner().invoke(main, ["--list-rules", "--json"])
        assert result.exit_code == 0, result.output
        rules = json.loads(result.stdout)
        codes = [rule["code"] for rule in rules]
        assert codes == sorted(set(codes))
        for rule in rules:
            assert list(rule) == ["check", "code", "description", "hint"]
            check, _, name = rule["code"].partition(".")
            assert check == rule["check"], rule
            assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name), rule
            description = rule["description"]
            assert description.strip() and "\n" not in description, rule


class TestFailOnResolution:
    """--fail-on wins, then the configured fail_on, then error.

    A level is a threshold: it brings every more severe level with it.
    """

    @pytest.mark.parametrize(
        ("flag", "configured", "expected"),
        [
            ((), None, ["error"]),
            ((), ["error"], ["error"]),
            ((), ["warning"], ["error", "warning"]),
            ((), ["note"], ["error", "warning", "note"]),
            ((), ["error", "note"], ["error", "warning", "note"]),
            ((), ["bogus", ["error"]], ["error"]),
            ((), ["bogus", "warning"], ["error", "warning"]),
            ((), "warning", ["error"]),
            (("error",), ["note"], ["error"]),
            (("warning",), ["note"], ["error", "warning"]),
            (("note",), [], ["error", "warning", "note"]),
            (("warning", "error"), [], ["error", "warning"]),
            (("error", "note"), [], ["error", "warning", "note"]),
            (("error", "error"), [], ["error"]),
        ],
    )
    def test_resolve(self, flag, configured, expected) -> None:
        config = {} if configured is None else {"fail_on": configured}
        assert _resolve_fail_on(flag, config) == expected

    @pytest.mark.parametrize(
        ("status", "level", "exit_code"),
        [
            (CheckStatus.ERROR, "error", 1),
            (CheckStatus.ERROR, "warning", 1),
            (CheckStatus.ERROR, "note", 1),
            (CheckStatus.WARNING, "error", 0),
            (CheckStatus.WARNING, "note", 1),
            (CheckStatus.NOTE, "warning", 0),
            (CheckStatus.NOTE, "note", 1),
            (CheckStatus.SKIPPED, "note", 0),
        ],
    )
    def test_flag_is_a_threshold(
        self, tmp_path, fake_run, status, level, exit_code
    ) -> None:
        fake_run(status)
        result = CliRunner().invoke(main, [str(tmp_path), "--fail-on", level])
        assert result.exit_code == exit_code, result.output

    def test_configured_level_is_a_threshold(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.WARNING)
        package = self._package(tmp_path, '["note"]')
        assert CliRunner().invoke(main, [package]).exit_code == 1

    def _package(self, tmp_path: Path, fail_on: str) -> str:
        (tmp_path / "pyproject.toml").write_text(
            f"[tool.pycmdcheck]\nfail_on = {fail_on}\n"
        )
        return str(tmp_path)

    def test_configured_fail_on_sets_the_exit_code(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.WARNING)
        package = self._package(tmp_path, '["warning"]')
        assert CliRunner().invoke(main, [package]).exit_code == 1
        assert CliRunner().invoke(main, [package, "--fail-on", "error"]).exit_code == 0
        assert CliRunner().invoke(main, [package, "--exit-zero"]).exit_code == 0

    def test_invalid_configured_value_falls_back_to_error(
        self, tmp_path, fake_run
    ) -> None:
        fake_run(CheckStatus.WARNING)
        package = self._package(tmp_path, '["bogus"]')
        assert CliRunner().invoke(main, [package]).exit_code == 0


@pytest.mark.parametrize(
    ("args", "key", "value"),
    [
        ([], "fail_fast", False),
        (["--fail-fast"], "fail_fast", True),
        (["-x"], "fail_fast", True),
        ([], "parallel", True),
        (["--no-parallel"], "parallel", False),
        (["--profile", "strict"], "profile", "strict"),
    ],
)
def test_option_reaches_the_runner(tmp_path, asked, args, key, value) -> None:
    result = CliRunner().invoke(main, [str(tmp_path), *args])
    assert result.exit_code == 0, result.output
    assert asked[0][key] == value


def test_every_short_option_has_a_long_name() -> None:
    """A long name says what an option does, so no option is a letter only."""
    for param in main.params:
        short = [name for name in param.opts if not name.startswith("--")]
        long = [name for name in param.opts if name.startswith("--")]
        if isinstance(param, click.Option) and short:
            assert long, f"{short} has no long name"
    assert "--help" in main.context_settings["help_option_names"]


def test_help_shows_name_lists_and_suggestions() -> None:
    result = CliRunner().invoke(main, ["--help"])
    assert result.exit_code == 0, result.output
    # click wraps the help text, so it is read without its line breaks.
    flat = " ".join(result.output.split())
    # Once for --check and once for --skip.
    assert flat.count("Separate the names with commas") == 2
    assert "--check tests,linting" in flat
    helps = {
        name: param.help
        for param in main.params
        if isinstance(param, click.Option)
        for name in param.opts
    }
    for option in ("--verbose", "--list-rules"):
        assert "suggestion" in helps[option]
    assert "what to do" not in flat.lower()


class TestClosingLine:
    """The closing line follows the same rule as the exit code."""

    @pytest.mark.parametrize(
        ("statuses", "fail_on", "expected"),
        [
            ((CheckStatus.OK, CheckStatus.NOTE), ["error"], "All checks passed!"),
            (
                (CheckStatus.OK, CheckStatus.WARNING),
                ["error"],
                "No errors (1 warning).",
            ),
            (
                (CheckStatus.SKIPPED, CheckStatus.SKIPPED),
                ["error"],
                "No errors (2 skipped).",
            ),
            ((CheckStatus.ERROR, CheckStatus.ERROR), ["error"], "Failed: 2 errors."),
            (
                (CheckStatus.OK, CheckStatus.WARNING),
                ["error", "warning"],
                "Failed: 1 warning.",
            ),
            (
                (
                    CheckStatus.NOTE,
                    CheckStatus.WARNING,
                    CheckStatus.NOTE,
                    CheckStatus.ERROR,
                ),
                ["error", "warning", "note"],
                "Failed: 1 error, 1 warning, 2 notes.",
            ),
        ],
    )
    def test_line(self, statuses, fail_on, expected) -> None:
        assert _closing_line(_report(*statuses), fail_on)[0] == expected

    @pytest.mark.parametrize(
        ("reasons", "expected"),
        [
            (["not-applicable"], "All checks passed!"),
            (["not-applicable", "deprecated"], "All checks passed!"),
            (["tool-missing"], "No errors (1 skipped)."),
            (["import-failed"], "No errors (1 skipped)."),
            (["parse-failed"], "No errors (1 skipped)."),
            (["not-applicable", "prerequisite-failed"], "No errors (1 skipped)."),
            ([None], "No errors (1 skipped)."),
        ],
    )
    def test_skipped_checks_beside_a_passing_one(self, reasons, expected) -> None:
        """Only a check that could not look keeps the report from passing."""
        report = _report(CheckStatus.OK)
        for index, reason in enumerate(reasons):
            report.add(
                CheckResult(
                    f"skip{index}",
                    CheckStatus.SKIPPED,
                    "Skipped",
                    skip_reason=reason,
                )
            )
        assert _closing_line(report, ["error"])[0] == expected

    def test_nothing_looked_at_does_not_pass(self) -> None:
        report = Report(package_path="/pkg")
        report.add(CheckResult.skip("a", "Nothing here", "not-applicable"))
        report.add(CheckResult.skip("b", "Nothing here", "not-applicable"))
        assert _closing_line(report, ["error"])[0] == "No errors (2 skipped)."

    @pytest.mark.parametrize("fail_on", [[], ["warning"], ["note"]])
    def test_an_error_never_passes(self, fail_on) -> None:
        """Even a fail_on list without "error" does not turn an error into a pass."""
        line, style = _closing_line(_report(CheckStatus.OK, CheckStatus.ERROR), fail_on)
        assert line == "Failed: 1 error."
        assert style == "bold red"

    def test_line_stays_true_under_exit_zero(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.ERROR)
        result = CliRunner().invoke(main, [str(tmp_path), "--exit-zero"])
        assert result.exit_code == 0
        assert "Failed: 1 error." in result.output
        assert "All checks passed!" not in result.output


class TestRendering:
    """Text from checks and paths is printed as written."""

    def test_layout_of_the_text_report(self) -> None:
        """A row holds the status symbol, the name, the message and the time."""
        statuses = (CheckStatus.OK, CheckStatus.WARNING, CheckStatus.ERROR)
        buffer = io.StringIO()
        _output_rich(_report(*statuses), True, Console(file=buffer, width=80))
        rows = _rows(buffer.getvalue())
        assert ["", "Check", "Message", "Time"] in rows
        for index, status in enumerate(statuses):
            row = [status.symbol, f"check{index}", f"{status.value} result", "0.00s"]
            assert row in rows
        assert "1 ok | 1 warning | 1 error" in buffer.getvalue().splitlines()

    def test_brackets_are_not_markup(self) -> None:
        report = Report(package_path="/pkg")
        report.add(
            CheckResult(
                "metadata",
                CheckStatus.WARNING,
                "Missing [project] table",
                details=["Add [project] or [tool.poetry]", "stray [/x] closing tag"],
            )
        )
        buffer = io.StringIO()
        _output_rich(report, True, Console(file=buffer, width=120))
        text = buffer.getvalue()
        assert "Missing [project] table" in text
        assert "Add [project] or [tool.poetry]" in text
        assert "stray [/x] closing tag" in text

    def test_bracketed_directory_name(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.OK)
        odd = tmp_path / "[bold]dir"
        odd.mkdir()
        result = CliRunner().invoke(main, [str(odd)])
        assert result.exit_code == 0, result.output
        assert "[bold]dir" in result.output

    def test_profile_label_when_checks_are_narrowed(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.OK)
        args = [str(tmp_path), "--profile", "strict"]
        assert "(profile: strict)" in CliRunner().invoke(main, args).output
        narrowed = CliRunner().invoke(main, [*args, "-c", "docs"]).output
        assert "(options from profile: strict)" in narrowed

    def test_console_that_is_not_utf8(self, tmp_path, fake_run) -> None:
        """Glyphs and a path the console cannot encode do not stop the run."""
        fake_run(CheckStatus.OK, CheckStatus.ERROR, CheckStatus.SKIPPED)
        package = tmp_path / "\u043f\u0430\u043a\u0435\u0442"
        package.mkdir()
        result = CliRunner(charset="cp1252").invoke(main, [str(package), "-v"])
        assert result.exception is None or isinstance(result.exception, SystemExit)
        assert result.exit_code == 1
        # Each status has a marker that any console can show.
        rows = _rows(result.output)
        assert ["+", "check0", "ok result", "0.00s"] in rows
        assert ["x", "check1", "error result", "0.00s"] in rows
        assert ["-", "check2", "skipped result", "0.00s"] in rows

    def test_colon_codes_in_details_are_not_emoji(self) -> None:
        report = Report(package_path="/pkg")
        report.add(
            CheckResult(
                "typing",
                CheckStatus.ERROR,
                "Found 1 type error(s)",
                details=["src/pkg/mod.py:100: error: bad [assignment]"],
            )
        )
        buffer = io.StringIO()
        _output_rich(report, True, Console(file=buffer, width=120))
        assert "src/pkg/mod.py:100: error: bad [assignment]" in buffer.getvalue()


class TestJSONRunFacts:
    """The JSON report says how the run was made, after the report itself."""

    def test_plain_run(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.WARNING)
        result = CliRunner().invoke(main, [str(tmp_path), "--json"])
        data = json.loads(result.stdout)
        assert list(data) == JSON_KEYS
        assert [list(result) for result in data["results"]] == [RESULT_KEYS]
        assert data["schema_version"] == 1
        assert data["pycmdcheck_version"] == __version__
        assert data["profile"] is None
        assert data["config_file"] is None
        assert data["fail_on"] == ["error"]
        assert data["failed"] is False
        assert data["requested_checks"] is None
        assert data["skipped_checks"] == []

    def test_options_are_reported(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.WARNING)
        args = ["--profile", "triage", "--fail-on", "warning", "--exit-zero"]
        names = ["-c", "license", "-c", "docs", "-c", "docs", "-s", "docs"]
        result = CliRunner().invoke(main, [str(tmp_path), "--json", *args, *names])
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["profile"] == "triage"
        assert data["fail_on"] == ["error", "warning"]
        # The exit rule is met, and --exit-zero does not hide that.
        assert data["failed"] is True
        assert data["passed"] is True
        assert data["requested_checks"] == ["license", "docs"]
        assert data["skipped_checks"] == ["docs"]

    def test_findings_and_skip_reason_are_reported(self, tmp_path, monkeypatch) -> None:
        report = Report(package_path=str(tmp_path))
        finding = Finding("changelog.missing", "No changelog found")
        report.add(
            CheckResult("changelog", CheckStatus.NOTE, "No file", findings=[finding])
        )
        report.add(CheckResult.skip("typing", "No type checker", "not-configured"))
        monkeypatch.setattr("pycmdcheck.cli.run_checks", lambda **kwargs: report)
        result = CliRunner().invoke(main, [str(tmp_path), "--json"])
        changelog_result, typing_result = json.loads(result.stdout)["results"]
        assert changelog_result["findings"] == [
            {
                "code": "changelog.missing",
                "status": "note",
                "message": "No changelog found",
                "hint": None,
            }
        ]
        assert changelog_result["skip_reason"] is None
        assert typing_result["findings"] == []
        assert typing_result["skip_reason"] == "not-configured"

    def test_text_and_markdown_do_not_show_the_new_fields(
        self, tmp_path, monkeypatch
    ) -> None:
        report = Report(package_path=str(tmp_path))
        finding = Finding("changelog.missing", "Finding text")
        report.add(
            CheckResult("changelog", CheckStatus.NOTE, "No file", findings=[finding])
        )
        report.add(CheckResult.skip("typing", "No type checker", "not-configured"))
        monkeypatch.setattr("pycmdcheck.cli.run_checks", lambda **kwargs: report)
        for args in (["-v"], ["--format", "markdown", "-v"]):
            output = CliRunner().invoke(main, [str(tmp_path), *args]).stdout
            assert "No file" in output
            for hidden in ("changelog.missing", "Finding text", "not-configured"):
                assert hidden not in output


POINTER = "Run again with --verbose to see a suggestion for each finding."


class TestHints:
    """Every output format says what to do about a finding."""

    @pytest.fixture
    def report(self, tmp_path, monkeypatch: pytest.MonkeyPatch) -> Report:
        """Make the CLI report two checks with hints and one without."""
        add, fill = "Add a CHANGELOG.md file.", "Fill <it> in, see @ann."
        report = Report(package_path=str(tmp_path))
        findings = [
            Finding("changelog.missing", "No changelog found", hint=add),
            Finding("changelog.empty", "Empty", CheckStatus.WARNING, fill),
            Finding("changelog.gone", "Also no changelog", hint=add),
        ]
        report.add(
            CheckResult(
                "changelog", CheckStatus.WARNING, "No file", ["Looked in the root"]
            )
        )
        report.results[0].findings = findings
        # A result with a finding and no detail lines.
        bare = [Finding("ci.missing", "No workflow", hint="Add a workflow file.")]
        report.add(CheckResult("ci", CheckStatus.NOTE, "No workflow", findings=bare))
        # A plugin with no hints.
        plain = [Finding("plugin.rule", "Odd")]
        report.add(CheckResult("plugin", CheckStatus.NOTE, "Odd", findings=plain))
        monkeypatch.setattr("pycmdcheck.cli.run_checks", lambda **kwargs: report)
        return report

    def _lines(self, report: Report, *args: str) -> list[str]:
        result = CliRunner().invoke(main, [report.package_path, *args])
        return [line.rstrip() for line in result.stdout.splitlines()]

    @pytest.mark.parametrize("flag", ["-v", "--verbose"])
    def test_verbose_text_shows_each_hint_once(self, report, flag) -> None:
        lines = self._lines(report, flag)
        start = lines.index("changelog details:")
        assert lines[start + 1 : start + 4] == [
            "  Looked in the root",
            "  Suggestion: Add a CHANGELOG.md file.",
            "  Suggestion: Fill <it> in, see @ann.",
        ]
        start = lines.index("ci details:")
        assert lines[start + 1] == "  Suggestion: Add a workflow file."
        assert "plugin details:" not in lines
        assert POINTER not in lines

    def test_plain_text_points_to_verbose(self, report) -> None:
        lines = self._lines(report)
        assert lines[-2:] == ["No errors (1 warning).", POINTER]
        assert not any("Suggestion" in line for line in lines)

    def test_label_is_bold_and_has_no_colour(self, report) -> None:
        """Colour in a report means a status, so the label has none."""
        lines = _styled(lambda con: _output_rich(report, True, con))
        assert "  \x1b[1mSuggestion:\x1b[0m Add a workflow file." in lines

    def test_detail_that_starts_with_the_label_is_not_bold(self, report) -> None:
        """Only a hint gets the bold label, not a line that a project wrote."""
        report.results[0].details = ["Suggestion: from the project"]
        lines = _styled(lambda con: _output_rich(report, True, con))
        assert "  Suggestion: from the project" in lines

    def test_no_pointer_without_a_hint(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.WARNING)
        assert POINTER not in CliRunner().invoke(main, [str(tmp_path)]).stdout

    def test_markdown(self, report) -> None:
        lines = self._lines(report, "--format", "markdown")
        start = lines.index("<summary>changelog: warning</summary>")
        assert lines[start + 2 : start + 6] == [
            "- Looked in the root",
            "- **Suggestion:** Add a CHANGELOG.md file.",
            "- **Suggestion:** Fill \\<it\\> in, see @\u200bann.",
            "",
        ]
        start = lines.index("<summary>ci: note</summary>")
        assert lines[start + 1 : start + 4] == [
            "",
            "- **Suggestion:** Add a workflow file.",
            "",
        ]
        assert "<summary>plugin: note</summary>" not in lines
        assert POINTER not in lines

    def test_json(self, report) -> None:
        document = json.loads("\n".join(self._lines(report, "--json")))
        changelog, ci, plugin = document["results"]
        assert changelog["findings"][0] == {
            "code": "changelog.missing",
            "status": "note",
            "message": "No changelog found",
            "hint": "Add a CHANGELOG.md file.",
        }
        assert plugin["findings"][0]["hint"] is None
        assert POINTER not in json.dumps(document)


class TestWrappedLines:
    """A long line of the --verbose report stays inside its section."""

    HINT = "Add a file named CHANGELOG.md to the project root."

    def _section(self, width: int, *details: object) -> list[str]:
        """Return the lines that the details and the hint take at *width*."""
        report = Report(package_path="/pkg")
        finding = Finding("docs.rule", "Found", CheckStatus.WARNING, self.HINT)
        result = CheckResult("docs", CheckStatus.WARNING, "Found", list(details))
        result.findings = [finding]
        report.add(result)
        buffer = io.StringIO()
        _output_rich(report, True, Console(file=buffer, width=width))
        lines = buffer.getvalue().splitlines()
        start = lines.index("docs details:") + 1
        return lines[start : lines.index("", start)]

    def test_rest_of_a_long_line_is_two_spaces_further_in(self) -> None:
        detail = "Looked for a changelog in the project root and found none"
        assert self._section(40, detail) == [
            "  Looked for a changelog in the project",
            "    root and found none",
            "  Suggestion: Add a file named",
            "    CHANGELOG.md to the project root.",
        ]

    def test_line_with_an_indent_of_its_own_keeps_it(self) -> None:
        details = [
            "Missing docstrings",
            "  - No docstring for the function load_all in pkg.module",
            "  ... and 3 more",
        ]
        assert self._section(40, *details)[:4] == [
            "  Missing docstrings",
            "    - No docstring for the function",
            "      load_all in pkg.module",
            "    ... and 3 more",
        ]

    def test_word_longer_than_the_line_is_left_whole(self) -> None:
        path = "src/a_long_package_name/a_long_module_name/another_part.py"
        lines = self._section(40, f"Wrote {path} here")
        assert lines[:3] == ["  Wrote", f"    {path}", "    here"]

    def test_name_with_hyphens_is_left_whole(self) -> None:
        detail = "Use the option --doctest-ignore-import-errors here"
        assert self._section(40, detail)[:2] == [
            "  Use the option",
            "    --doctest-ignore-import-errors here",
        ]

    def test_line_break_in_a_detail_starts_a_new_line(self) -> None:
        lines = self._section(40, "first line\nsecond line")
        assert lines[:2] == ["  first line", "  second line"]

    def test_detail_that_is_not_text_is_still_shown(self) -> None:
        """A plugin may give a path or a number where a line of text belongs."""
        details = [PurePosixPath("src/pkg"), 3]
        assert self._section(40, *details)[:2] == ["  src/pkg", "  3"]

    @pytest.mark.parametrize("width", [60, 80, 120])
    def test_no_line_is_wider_than_the_console(self, width) -> None:
        detail = " ".join(["word"] * 60)
        lines = self._section(width, detail)
        assert max(map(len, lines)) <= width
        text = " ".join(line.strip() for line in lines)
        assert text == f"{detail} Suggestion: {self.HINT}"
        # Only the detail and the hint start a line. The rest is further in.
        starts = [line for line in lines if not line.startswith("    ")]
        assert [line[:12] for line in starts] == ["  word word ", "  Suggestion"]


class TestDeprecatedCheck:
    """A deprecated check is left out unless it is named with -c."""

    @pytest.fixture(autouse=True)
    def _stub_registry(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Register a deprecated check and a current one that only return OK."""

        class Stub:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                return CheckResult("stub", CheckStatus.OK, "done")

        registry = {"imports": Stub, "license": Stub}
        monkeypatch.setattr("pycmdcheck.discovery.discover_checks", lambda: registry)
        monkeypatch.setattr("pycmdcheck.runner.discover_checks", lambda: registry)

    def test_selection(self, tmp_path) -> None:
        def names(*args: str) -> list[str]:
            result = CliRunner().invoke(main, [str(tmp_path), "--json", *args])
            assert result.exit_code == 0, result.output
            return [r["name"] for r in json.loads(result.stdout)["results"]]

        assert names() == ["license"]
        assert names("-c", "imports") == ["imports"]
        assert names("-c", "imports", "-c", "license") == ["imports", "license"]

    @pytest.mark.parametrize(
        ("pyproject", "warned"),
        [("", False), ("[tool.pycmdcheck.checks]\nimports = true\n", True)],
        ids=["defaults", "enabled"],
    )
    def test_project_that_enables_it_is_told(
        self, tmp_path, caplog, pyproject, warned
    ) -> None:
        """The defaults enable it too, and that is not the project's doing."""
        (tmp_path / "pyproject.toml").write_text(pyproject)
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            result = CliRunner().invoke(main, [str(tmp_path), "--json"])
        assert result.exit_code == 0, result.output
        assert [r["name"] for r in json.loads(result.stdout)["results"]] == ["license"]
        logged = [message for message in caplog.messages if "deprecated" in message]
        assert len(logged) == warned
        assert all("imports" in message for message in logged)


class TestFormatAndOutput:
    """--format picks the report format and --output sends it to a file."""

    def test_format_json_is_the_json_flag(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.OK)
        flag = CliRunner().invoke(main, [str(tmp_path), "--json"])
        for args in (["--format", "json"], ["--json", "--format", "json"]):
            result = CliRunner().invoke(main, [str(tmp_path), *args])
            assert result.exit_code == 0, result.output
            assert result.stdout == flag.stdout

    def test_format_markdown(self, tmp_path, fake_run) -> None:
        fake_run(CheckStatus.OK, CheckStatus.ERROR)
        result = CliRunner().invoke(main, [str(tmp_path), "--format", "markdown"])
        assert result.exit_code == 1
        assert result.stdout.startswith("## pycmdcheck report\n")
        assert "Checking package:" not in result.stdout

    @pytest.mark.parametrize("other", ["text", "markdown"])
    def test_json_with_another_format_is_a_usage_error(self, tmp_path, other) -> None:
        result = CliRunner().invoke(main, [str(tmp_path), "--json", "--format", other])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "--json" in result.stderr
        assert f"--format {other}" in result.stderr

    @pytest.mark.parametrize(
        ("fmt", "expected"),
        [
            ("text", "Checking package:"),
            ("json", '"schema_version": 1'),
            ("markdown", "## pycmdcheck report"),
        ],
    )
    def test_output_file(self, tmp_path, fake_run, fmt, expected) -> None:
        fake_run(CheckStatus.OK, CheckStatus.ERROR)
        target = tmp_path / "report.out"
        args = [str(tmp_path), "--format", fmt, "--output", str(target)]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 1
        assert result.stdout == ""
        written = target.read_text(encoding="utf-8")
        assert expected in written
        assert "check1" in written
        assert written.endswith("\n")
        assert "\x1b" not in written

    def test_output_file_is_utf8(self, tmp_path, monkeypatch) -> None:
        report = Report(package_path=str(tmp_path))
        report.add(CheckResult("docs", CheckStatus.NOTE, "caf\u00e9 \u2713"))
        monkeypatch.setattr("pycmdcheck.cli.run_checks", lambda **kwargs: report)
        target = tmp_path / "report.md"
        args = [str(tmp_path), "--format", "markdown", "--output", str(target)]
        assert CliRunner(charset="cp1252").invoke(main, args).exit_code == 0
        assert "caf\u00e9 \u2713" in target.read_bytes().decode("utf-8")

    def test_warnings_are_not_part_of_the_output(self, tmp_path, caplog) -> None:
        target = tmp_path / "report.json"
        settings = "[tool.pycmdcheck.checks]\ntypo = true\n"
        (tmp_path / "pyproject.toml").write_text(settings, encoding="utf-8")
        args = ["--json", "-c", "license", "--output", str(target)]
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            result = CliRunner().invoke(main, [*args, str(tmp_path)])
        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        assert "typo" in caplog.text
        assert json.loads(target.read_text(encoding="utf-8"))["results"]

    @pytest.mark.parametrize(
        ("target", "expected"),
        [
            ("{tmp}/missing/report.md", "does not exist"),
            ("{tmp}/a_file/report.md", "does not exist"),
            ("{tmp}", "is a directory"),
            ("", "--output needs a file name"),
        ],
    )
    def test_unusable_output_is_refused_before_any_check_runs(
        self, tmp_path, asked, target, expected
    ) -> None:
        (tmp_path / "a_file").write_text("x")
        args = [str(tmp_path), "--output", target.format(tmp=tmp_path)]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert expected in result.stderr
        assert asked == []

    def test_output_that_fails_at_the_end_is_a_usage_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """The file can still be refused once the checks are over."""
        target = tmp_path / "reports" / "report.md"
        target.parent.mkdir()

        def run(**kwargs: object) -> Report:
            target.parent.rmdir()
            return _report(CheckStatus.OK)

        monkeypatch.setattr("pycmdcheck.cli.run_checks", run)
        result = CliRunner().invoke(main, [str(tmp_path), "--output", str(target)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Cannot write the report to" in result.stderr

    def test_output_dash_is_standard_output(
        self, tmp_path, fake_run, monkeypatch
    ) -> None:
        fake_run(CheckStatus.OK)
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, [str(tmp_path), "--json", "--output", "-"])
        assert result.exit_code == 0, result.output
        assert json.loads(result.stdout)["results"]
        assert not (tmp_path / "-").exists()

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["--list"], "Available Checks"),
            (["--list-profiles"], "Available Profiles"),
            (["--list", "--json"], '"name": "metadata"'),
            (["--list-profiles", "--json"], '"name": "triage"'),
            (["--list-rules"], "license.missing"),
            (["--list-rules", "--json"], '"code": "license.missing"'),
        ],
    )
    def test_listing_goes_to_the_output_file(self, tmp_path, args, expected) -> None:
        target = tmp_path / "listing.out"
        result = CliRunner().invoke(main, [*args, "--output", str(target)])
        assert result.exit_code == 0, result.output
        assert result.stdout == ""
        written = target.read_text(encoding="utf-8")
        assert expected in written
        assert written.endswith("\n")
        assert "\x1b" not in written


class TestMarkdown:
    """The Markdown report is safe to post as a comment."""

    def _report(self) -> Report:
        report = Report(package_path="/work/wid_get")
        report.add(CheckResult("py_typed", CheckStatus.OK, "Fine", details=["kept"]))
        report.add(
            CheckResult(
                "docs",
                CheckStatus.WARNING,
                "a | b\nc <b>x</b> `y` __init__ [z](u)",
                details=["first", "  </details> <script> | `code`"],
            )
        )
        report.add(CheckResult("ci", CheckStatus.NOTE, "No workflow"))
        return report

    def test_layout(self) -> None:
        text = _render_markdown(self._report(), False, ["error"], " (profile: triage)")
        lines = text.split("\n")
        assert lines[0] == "## pycmdcheck report"
        assert lines[2] == (
            f"Checked wid\\_get (profile: triage) with pycmdcheck {__version__}."
        )
        assert lines[4:6] == ["| Status | Check | Message |", "| --- | --- | --- |"]
        assert lines[6] == "| ok | py\\_typed | Fine |"
        assert lines[8] == "| note | ci | No workflow |"
        assert "**1 ok, 1 note, 1 warning. No errors (1 warning).**" in lines
        # Only the result that is not ok and has details gets a block.
        assert text.count("<details>") == text.count("</details>") == 1
        assert "<summary>docs: warning</summary>" in lines
        assert "- first" in lines

    def test_text_from_checks_is_escaped(self) -> None:
        text = _render_markdown(self._report(), False, ["error"], "")
        row = next(line for line in text.split("\n") if line.startswith("| warning"))
        assert row == (
            "| warning | docs | a \\| b c \\<b\\>x\\</b\\> \\`y\\` "
            "\\_\\_init\\_\\_ \\[z\\](u) |"
        )
        assert "- \\</details\\> \\<script\\> \\| \\`code\\`" in text.split("\n")

    def test_verbose_shows_every_result_with_details(self) -> None:
        text = _render_markdown(self._report(), True, ["error"], "")
        assert "<summary>py_typed: ok</summary>" in text
        assert "<summary>docs: warning</summary>" in text
        assert text.count("<details>") == 2

    def test_no_results(self) -> None:
        text = _render_markdown(Report(package_path="/work/pkg"), False, ["error"], "")
        assert text.endswith("No checks were run.")
        assert "| Status |" not in text

    def _lines(self, message: str, *details: str, name: str = "docs") -> list[str]:
        """Render one warning and return the lines of the Markdown report."""
        report = Report(package_path="/work/pkg")
        report.add(CheckResult(name, CheckStatus.WARNING, message, list(details)))
        return _render_markdown(report, False, ["error"], "").split("\n")

    def test_at_sign_does_not_mention_anyone(self) -> None:
        lines = self._lines("Ask @octocat", "cc @org/team or a@b.io", name="@bot")
        assert "| warning | @\u200bbot | Ask @\u200boctocat |" in lines
        assert "<summary>@\u200bbot: warning</summary>" in lines
        assert "- cc @\u200borg/team or a@\u200bb.io" in lines
        assert "@" not in "\n".join(lines).replace("@\u200b", "")

    def test_web_addresses_do_not_become_links(self) -> None:
        lines = self._lines("See https://evil.example/prize", "or WWW.evil.example")
        text = "\n".join(lines)
        assert "| See https:\u200b//evil.example/prize |" in text
        assert "- or WWW\u200b.evil.example" in lines
        assert "://" not in text and "www." not in text.lower()

    def test_colon_codes_are_not_emoji(self) -> None:
        lines = self._lines(
            "Found :tada: and :thumbs_up:",
            "src/pkg/mod.py:100: error: bad",
            "at 12:30:45, :+1::-1:",
            name=":x:",
        )
        assert (
            "| warning | :\u200bx: | Found :\u200btada: and :\u200bthumbs\\_up: |"
            in lines
        )
        assert "<summary>:\u200bx:: warning</summary>" in lines
        # The line number is still there, and it is not the "100" emoji.
        assert "- src/pkg/mod.py:\u200b100: error: bad" in lines
        assert "- at 12:\u200b30:45, :\u200b+1::\u200b-1:" in lines

    def test_detail_cannot_start_a_nested_list(self) -> None:
        details = ["- dash", "* star", "+ plus", "12. dot", "3) paren", "-", "7."]
        plain = ["-x is not a marker", "1.5 is not a marker", "a - b"]
        lines = self._lines("m", *details, *plain)
        start = lines.index("<summary>docs: warning</summary>") + 2
        assert lines[start : lines.index("</details>") - 1] == [
            "- \\- dash",
            "- \\* star",
            "- \\+ plus",
            "- 12\\. dot",
            "- 3\\) paren",
            "- \\-",
            "- 7\\.",
            *(f"- {text}" for text in plain),
        ]

    def test_detail_of_hyphens_is_not_a_rule(self) -> None:
        """Markdown reads "- ---" as a horizontal rule, so the hyphen is escaped."""
        lines = self._lines("m", "---", "-- --", "--flag is kept")
        start = lines.index("<summary>docs: warning</summary>") + 2
        assert lines[start : lines.index("</details>") - 1] == [
            "- \\---",
            "- \\-- --",
            "- --flag is kept",
        ]


class TestInstallSkill:
    """--install-skill copies the skill that pycmdcheck carries and exits."""

    # Where the skill goes inside a skills directory.
    SKILL = Path("pycmdcheck", "SKILL.md")

    def test_default_directory(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["--install-skill"])
        assert result.exit_code == 0, result.output
        written = Path(".claude", "skills") / self.SKILL
        # A relative directory is printed as a relative path.
        assert result.stdout == f"Installed the skill in {written}.\n"
        assert (tmp_path / written).is_file()

    def test_given_directory(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        result = CliRunner().invoke(main, ["--install-skill", ".agents/skills"])
        assert result.exit_code == 0, result.output
        # The path is printed as it was given, so it stays relative.
        written = Path(".agents", "skills") / self.SKILL
        assert result.stdout == f"Installed the skill in {written}.\n"
        assert (tmp_path / written).is_file()

    def test_tilde_is_the_home_directory(self, tmp_path, monkeypatch) -> None:
        """A shell leaves the ~ after an equals sign as it is."""
        home, work = tmp_path / "home", tmp_path / "work"
        work.mkdir()
        monkeypatch.chdir(work)
        # Windows reads the second name.
        for name in ("HOME", "USERPROFILE"):
            monkeypatch.setenv(name, str(home))
        result = CliRunner().invoke(main, ["--install-skill=~/.claude/skills"])
        assert result.exit_code == 0, result.output
        written = home / ".claude" / "skills" / self.SKILL
        assert result.stdout == f"Installed the skill in {written}.\n"
        assert written.is_file()
        # No directory named ~ is left behind.
        assert list(work.iterdir()) == []

    @pytest.mark.parametrize("directory", [".", "mypackage", "skills/pycmdcheck", ""])
    def test_directory_that_is_not_named_skills(
        self, tmp_path, monkeypatch, directory
    ) -> None:
        """The word after the option is its directory, also when PATH was meant."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / "mypackage").mkdir()
        result = CliRunner().invoke(main, ["--install-skill", directory])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert f'--install-skill read "{directory}" as its directory.' in result.stderr
        assert "must be named skills, as in .claude/skills." in result.stderr
        assert [path.name for path in tmp_path.rglob("*")] == ["mypackage"]

    def test_home_directory_that_cannot_be_found(self, tmp_path, monkeypatch) -> None:
        """The name of a user that does not exist must not become a folder."""
        monkeypatch.chdir(tmp_path)
        # Windows guesses the home of another user from the second name.
        for name in ("HOME", "USERPROFILE"):
            monkeypatch.setenv(name, str(tmp_path / "home"))
        directory = "~no-such-user-of-pycmdcheck/skills"
        result = CliRunner().invoke(main, ["--install-skill", directory])
        assert result.exit_code == 2
        assert result.stdout == ""
        said = f'cannot find the home directory that "{directory}" starts with.'
        assert said in result.stderr
        assert list(tmp_path.iterdir()) == []

    def test_three_sentences(self, tmp_path) -> None:
        skills = tmp_path / "skills"
        written = skills / self.SKILL
        args = ["--install-skill", str(skills)]
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert result.stdout == f"Installed the skill in {written}.\n"
        carried = written.read_bytes()

        written.write_text("the skill of an earlier version")
        # A file of the person's own beside the skill is left alone.
        notes = written.with_name("NOTES.md")
        notes.write_text("my own notes")
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert result.stdout == f"Updated the skill in {written}.\n"
        assert written.read_bytes() == carried
        assert notes.read_text() == "my own notes"

        # A file that is already the same is not written again.
        os.utime(written, ns=(0, 0))
        result = CliRunner().invoke(main, args)
        assert result.exit_code == 0, result.output
        assert result.stdout == f"The skill in {written} is already up to date.\n"
        assert written.stat().st_mtime_ns == 0

    @pytest.mark.parametrize(
        ("link", "target"),
        [
            ("pycmdcheck", "package"),
            ("pycmdcheck/SKILL.md", "package/SKILL.md"),
            # A link that points nowhere is a link all the same.
            ("pycmdcheck", "gone"),
        ],
    )
    def test_symbolic_link_is_left_alone(self, tmp_path, link, target) -> None:
        """Another tool links skills here, and its file must stay as it is."""
        theirs = tmp_path / "package" / "SKILL.md"
        theirs.parent.mkdir()
        theirs.write_text("the skill of another tool")
        skills = tmp_path / "skills"
        (skills / link).parent.mkdir(parents=True)
        try:
            (skills / link).symlink_to(
                tmp_path / target, target_is_directory=target == "package"
            )
        except OSError:
            pytest.skip("symlinks are not available")
        result = CliRunner().invoke(main, ["--install-skill", str(skills)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert f"because {skills / link} is a symbolic link" in result.stderr
        assert "remove the link or pick another directory." in result.stderr
        assert theirs.read_text() == "the skill of another tool"
        assert (skills / link).is_symlink()
        assert not (tmp_path / "gone").exists()

    def test_write_that_fails_is_a_usage_error(self, tmp_path) -> None:
        """A file where the directory goes, and a directory where the file goes."""
        (tmp_path / "file").mkdir()
        (tmp_path / "file" / "skills").write_text("x")
        (tmp_path / "directory" / "skills" / self.SKILL).mkdir(parents=True)
        for name in ("file", "directory"):
            skills = tmp_path / name / "skills"
            result = CliRunner().invoke(main, ["--install-skill", str(skills)])
            # An error that nothing caught would give exit code 1.
            assert result.exit_code == 2, name
            assert result.stdout == ""
            assert f"Cannot write the skill to {skills / self.SKILL}: " in result.stderr

    def test_directory_that_cannot_be_entered_is_a_usage_error(self, tmp_path) -> None:
        """Before Python 3.14 the test for a link fails here, ahead of any write."""
        skills = tmp_path / "skills"
        skills.mkdir()
        skills.chmod(0)
        try:
            if os.access(skills, os.X_OK):
                pytest.skip("a directory with no permissions can still be entered here")
            result = CliRunner().invoke(main, ["--install-skill", str(skills)])
        finally:
            skills.chmod(0o755)
        assert result.exit_code == 2
        assert result.stdout == ""
        assert f"Cannot write the skill to {skills / self.SKILL}: " in result.stderr
        assert list(skills.iterdir()) == []

    def test_it_runs_no_check_and_reads_no_settings(
        self, tmp_path, monkeypatch
    ) -> None:
        def explode(*args: object, **kwargs: object) -> None:
            raise AssertionError("--install-skill must only copy the skill")

        for name in ("run_checks", "load_config", "_load_config_file"):
            monkeypatch.setattr(f"pycmdcheck.cli.{name}", explode)
        config = tmp_path / "broken.toml"
        config.write_text("profile =")
        skills = tmp_path / "skills"
        # No other option is looked at, so a mistake in one is not reported.
        report = tmp_path / "missing" / "report.json"
        others = ["--json", "--format", "markdown", "--config", str(config)]
        others += ["--output", str(report), "-c", "nope", str(tmp_path)]
        result = CliRunner().invoke(main, ["--install-skill", str(skills), *others])
        assert result.exit_code == 0, result.output
        assert result.stdout == f"Installed the skill in {skills / self.SKILL}.\n"
        assert result.stderr == ""
        assert not report.parent.exists()

    def test_copy_equals_the_skill_of_the_checkout(self, tmp_path, repo_root) -> None:
        """A checkout has no skill inside the package, so skills/ is the source."""
        skills = tmp_path / "skills"
        result = CliRunner().invoke(main, ["--install-skill", str(skills)])
        assert result.exit_code == 0, result.output
        source = repo_root / "skills" / self.SKILL
        assert (skills / self.SKILL).read_bytes() == source.read_bytes()

    def test_installed_copy_carries_the_skill_inside_the_package(
        self, tmp_path, monkeypatch, repo_root
    ) -> None:
        """The build puts the folder there. Every file in it is copied."""
        # The place is read from the settings of the build, so the build
        # and the program cannot come to name two places.
        text = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
        wheel = tomllib.loads(text)["tool"]["hatch"]["build"]["targets"]["wheel"]
        ((source, place),) = wheel["force-include"].items()
        assert source == "skills/pycmdcheck"
        site = tmp_path / "site-packages"
        carried = site / place
        (carried / "references").mkdir(parents=True)
        (carried / "SKILL.md").write_text("the skill of a wheel")
        (carried / "references" / "rules.md").write_text("the rules")
        (carried / ".DS_Store").write_text("not part of the skill")
        monkeypatch.setattr("pycmdcheck._skill.files", lambda name: site / name)
        skills = tmp_path / "skills"
        result = CliRunner().invoke(main, ["--install-skill", str(skills)])
        assert result.exit_code == 0, result.output
        copied = {
            path.relative_to(skills).as_posix(): path.read_text()
            for path in skills.rglob("*")
            if path.is_file()
        }
        assert copied == {
            "pycmdcheck/SKILL.md": "the skill of a wheel",
            "pycmdcheck/references/rules.md": "the rules",
        }

    @pytest.mark.parametrize(
        ("folder", "stray"), [("src", False), ("site-packages", True)]
    )
    def test_copy_that_carries_no_skill(
        self, tmp_path, monkeypatch, folder, stray
    ) -> None:
        """Neither place holds a SKILL.md, as when src/ alone was copied.

        A SKILL.md beside the folder of an installed copy is not its skill.
        """
        module = tmp_path / folder / "pycmdcheck" / "_skill.py"
        # A folder with no SKILL.md in it is not taken for the skill.
        (tmp_path / "skills" / "pycmdcheck").mkdir(parents=True)
        if stray:
            (tmp_path / "skills" / self.SKILL).write_text("the skill of another tool")
        monkeypatch.setattr("pycmdcheck._skill.files", lambda name: module.parent)
        monkeypatch.setattr("pycmdcheck._skill.__file__", str(module))
        skills = tmp_path / "home" / "skills"
        result = CliRunner().invoke(main, ["--install-skill", str(skills)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "This copy of pycmdcheck carries no skill." in result.stderr
        assert not skills.parent.exists()

    def test_carried_skill_that_cannot_be_read(self, tmp_path, monkeypatch) -> None:
        """A damaged copy gets a line of its own, since no write is at fault."""
        package = tmp_path / "site-packages" / "pycmdcheck"
        carried = package / ".agents" / "skills" / self.SKILL
        carried.parent.mkdir(parents=True)
        carried.write_text("the skill of a wheel")
        carried.chmod(0)
        if os.access(carried, os.R_OK):
            pytest.skip("a file with no permissions can still be read here")
        monkeypatch.setattr("pycmdcheck._skill.files", lambda name: package)
        skills = tmp_path / "home" / "skills"
        result = CliRunner().invoke(main, ["--install-skill", str(skills)])
        assert result.exit_code == 2
        assert result.stdout == ""
        assert "Cannot read the skill that pycmdcheck carries: " in result.stderr
        assert not skills.parent.exists()

    def test_help_names_the_option(self, tmp_path, monkeypatch) -> None:
        monkeypatch.chdir(tmp_path)
        # Help wins over the option, so asking for help writes nothing.
        result = CliRunner().invoke(main, ["--install-skill", "--help"])
        assert result.exit_code == 0, result.output
        assert "--install-skill [DIR]" in result.output
        assert list(tmp_path.iterdir()) == []


class TestInstalledCommand:
    """Run the real program in a subprocess, as the triage bot does."""

    def _run(
        self, args: list[str], cwd: Path, timeout: float = 120
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "pycmdcheck", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_debug_logs_to_standard_error(self, tmp_path: Path) -> None:
        """pytest owns the logging of its own process, so this needs another."""
        args = ["--debug", "--json", "-c", "license", str(tmp_path)]
        done = self._run(args, cwd=tmp_path, timeout=60)
        # The report on standard output is still JSON and nothing else.
        assert json.loads(done.stdout)["results"]
        logged = done.stderr.splitlines()
        assert any(line.startswith("pycmdcheck.") for line in logged)

    def test_repository_cannot_ship_its_own_checks(self, tmp_path: Path) -> None:
        """python -m from inside a repository must not load checks it ships."""
        marker = tmp_path / "loaded.txt"
        (tmp_path / "evilplugin.py").write_text(
            f"open({str(marker)!r}, 'w').write('x')\n"
            "class Evil:\n    name = 'evil'\n    description = 'evil'\n"
        )
        egg_info = tmp_path / "evilplugin.egg-info"
        egg_info.mkdir()
        (egg_info / "PKG-INFO").write_text("Metadata-Version: 2.1\nName: evilplugin\n")
        (egg_info / "entry_points.txt").write_text(
            "[pycmdcheck.checks]\nevil = evilplugin:Evil\n"
        )
        done = self._run(["--list", "--json"], cwd=tmp_path)
        assert done.returncode == 0, done.stderr
        assert "evil" not in {item["name"] for item in json.loads(done.stdout)}
        assert not marker.exists()
