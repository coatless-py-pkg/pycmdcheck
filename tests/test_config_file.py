"""Tests for --config FILE, a settings file kept outside the checked project."""

import inspect
import json
import logging
import re
import sys
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

from pycmdcheck import discovery
from pycmdcheck.cli import _render_markdown, main
from pycmdcheck.config import _FILE_OPTIONS
from pycmdcheck.profiles import PROFILES
from pycmdcheck.results import CheckResult, CheckStatus, Report
from pycmdcheck.subprocess_runner import resolve_timeout

# The file from the documentation of the option.
EXAMPLE = """\
# Start from the built-in triage profile, which only reads files.
profile = "triage"
fail_on = "warning"

[checks]
citation = false
docs = { check_docs_site = false }
"""

# An integer that Python refuses to turn into text.
HUGE = "0x" + "F" * 5000

# What a checked project may write to bend a run in its favour.
HOSTILE = """\
[project]
name = "widget"
version = "1.0"

[tool.pycmdcheck]
fail_on = ["note"]

[tool.pycmdcheck.checks]
license = false
metadata = 3
docs = { require_readme = false, check_docstrings = true }
"""


@pytest.fixture
def config_file(tmp_path: Path):
    """Write a config file beside the project directory and return its path."""

    def _write(content: str | bytes, name: str = "triage.toml") -> str:
        path = tmp_path / name
        path.write_bytes(content.encode() if isinstance(content, str) else content)
        return str(path)

    return _write


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """An empty project directory, separate from the config file."""
    directory = tmp_path / "project"
    directory.mkdir()
    return directory


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record how the CLI calls run_checks, without running any check."""
    recorded: list[dict[str, Any]] = []

    def fake(**kwargs: Any) -> Report:
        recorded.append(kwargs)
        report = Report(package_path=str(kwargs["package_path"]))
        report.add(CheckResult("license", CheckStatus.WARNING, "A warning"))
        return report

    monkeypatch.setattr("pycmdcheck.cli.run_checks", fake)
    return recorded


def _run(*args: str) -> Any:
    return CliRunner().invoke(main, list(args))


class TestOptionTable:
    """The table of options is tied to what the checks read."""

    def test_it_holds_exactly_what_each_built_in_check_reads(
        self, options_read
    ) -> None:
        """The names come from the source, so a new option fails here."""
        # The time limit of a tool is read in one shared helper.
        in_helper, _ = options_read(inspect.getsource(resolve_timeout))
        assert in_helper == {"timeout"}
        found: dict[str, set[str]] = {}
        for name, cls in discovery.discover_checks().items():
            if not cls.__module__.startswith("pycmdcheck.checks."):
                continue
            source = inspect.getsource(sys.modules[cls.__module__])
            read, unread = options_read(source)
            # The scan looks for the name config, so run() has to use it.
            assert list(inspect.signature(cls.run).parameters)[2] == "config", name
            for number in unread:
                line = source.splitlines()[number - 1]
                if "resolve_timeout(config" in line:
                    read |= in_helper
                    continue
                # Handing config on to a method of the same class, which the
                # scan reads only when the method calls it config as well.
                handed_to = re.fullmatch(r".*\bself\.(_\w+)\([\w, ]*\bconfig\b.*", line)
                assert handed_to, f"{name} line {number}: {line}"
                method = getattr(cls, handed_to[1])
                assert "config" in inspect.signature(method).parameters, line
            if read:
                found[name] = read
        # "enabled" is read by the runner and by no check, so it is not here.
        assert found == {name: set(options) for name, options in _FILE_OPTIONS.items()}

    @pytest.mark.parametrize(
        "name, option",
        [("linting", "tool"), ("typing", "tool"), ("formatting", "tool")],
    )
    def test_tool_names_are_the_supported_ones(self, name, option) -> None:
        words, accepts = _FILE_OPTIONS[name][option]
        supported = discovery.discover_checks()[name].SUPPORTED_TOOLS  # type: ignore[attr-defined]
        assert all(accepts(tool) for tool in ["auto", *supported])
        assert not accepts("nose")
        # The words name every value that is accepted, so none can be added.
        assert words == "one of " + ", ".join(["auto", *supported])


class TestRun:
    """A run from a file uses the file and nothing from the checked project."""

    def test_example_file(self, config_file, project) -> None:
        path = config_file(EXAMPLE)
        result = _run("--config", path, "--json", "--exit-zero", str(project))
        assert result.exit_code == 0, result.output
        data = json.loads(result.stdout)
        assert data["config_file"] == path
        assert data["profile"] == "triage"
        assert data["fail_on"] == ["error", "warning"]
        assert data["requested_checks"] is None
        assert data["skipped_checks"] == []
        expected = sorted(PROFILES["triage"].checks - {"citation"})
        assert [r["name"] for r in data["results"]] == expected

    def test_project_settings_are_not_read(self, config_file, project, caplog) -> None:
        args = ["--config", config_file(EXAMPLE), "--json", "--exit-zero", str(project)]
        (project / "pyproject.toml").write_text('[project]\nname = "widget"\n')
        plain = json.loads(_run(*args).stdout)
        (project / "pyproject.toml").write_text(HOSTILE.replace('1.0"', '1.0"\n', 1))
        with caplog.at_level(logging.WARNING):
            hostile = json.loads(_run(*args).stdout)
        assert [r for r in caplog.records if r.name.startswith("pycmdcheck")] == []
        assert hostile["fail_on"] == plain["fail_on"] == ["error", "warning"]
        # The version line differs, so the comparison leaves the metadata out.
        for document in (plain, hostile):
            for result in document["results"]:
                result["duration"] = 0
        assert "license" in [r["name"] for r in hostile["results"]]
        docs = [r for r in hostile["results"] if r["name"] == "docs"]
        assert docs == [r for r in plain["results"] if r["name"] == "docs"]
        assert "docstring" not in json.dumps(docs)

    def test_file_wins_over_profile_and_profile_over_defaults(
        self, config_file, project, calls
    ) -> None:
        content = (
            'profile = "strict"\n[checks]\ntyping = { strict = false }\n'
            'urls = { enabled = false }\ntests = { args = ["-x"], timeout = 1.5 }\n'
            "docs = true\n"
        )
        assert _run("--config", config_file(content), str(project)).exit_code == 0
        (call,) = calls
        assert call["profile"] is None
        assert call["checks"] == sorted(PROFILES["strict"].checks - {"urls"})
        checks = call["config"]["checks"]
        assert checks["typing"] == {"enabled": True, "tool": "mypy", "strict": False}
        assert checks["linting"] == {"enabled": True, "tool": "ruff"}
        assert checks["tests"]["runner"] == "pytest"
        assert checks["tests"]["args"] == ["-x"]
        assert checks["docs"]["min_docstring_coverage"] == 80

    @pytest.mark.parametrize(
        "content, flags, expected",
        [
            ('profile = "minimal"\n', [], 0),
            ('profile = "minimal"\nfail_on = "warning"\n', [], 1),
            ('profile = "minimal"\nfail_on = ["error", "note"]\n', [], 1),
            ('profile = "minimal"\nfail_on = "warning"\n', ["--fail-on", "error"], 0),
            ('profile = "minimal"\n', ["--fail-on", "warning"], 1),
        ],
    )
    def test_fail_on(
        self, config_file, project, calls, content, flags, expected
    ) -> None:
        # The project asks for the opposite of each case and is not heard.
        ask = "error" if expected else "note"
        (project / "pyproject.toml").write_text(
            f'[tool.pycmdcheck]\nfail_on = ["{ask}"]\n'
        )
        result = _run("--config", config_file(content), *flags, str(project))
        assert result.exit_code == expected, result.output

    def test_empty_fail_on_list_means_error(self, config_file, project) -> None:
        """An empty list is the default, and not a run that can never fail."""
        path = config_file('profile = "minimal"\nfail_on = []\n')
        # The project is empty, so its metadata cannot be read.
        result = _run("--config", path, "--json", str(project))
        assert result.exit_code == 1, result.output
        data = json.loads(result.stdout)
        assert data["fail_on"] == ["error"]
        assert data["failed"] is True

    def test_check_and_skip_narrow_the_run(self, config_file, project, calls) -> None:
        path = config_file(EXAMPLE)
        args = ["-c", "license", "-c", "docs", "-s", "docs", "--json", "--exit-zero"]
        data = json.loads(_run("--config", path, *args, str(project)).stdout)
        assert calls[0]["checks"] == ["license", "docs"]
        assert calls[0]["skip"] == ["docs"]
        assert data["requested_checks"] == ["license", "docs"]
        assert data["skipped_checks"] == ["docs"]


class TestLabel:
    """The first line of a report names the file beside the profile."""

    def test_text_and_markdown(self, config_file, project, calls) -> None:
        try:
            path = config_file(EXAMPLE, "my_bot*.toml")
        except OSError:
            pytest.skip("this system allows no * in a file name")
        text = _run("--config", path, str(project)).output
        assert "(profile: triage, config: my_bot*.toml)" in text
        narrowed = _run("--config", path, "-c", "docs", str(project)).output
        assert "(options from profile: triage, config: my_bot*.toml)" in narrowed
        markdown = _run("--config", path, "--format", "markdown", str(project)).output
        assert "project (profile: triage, config: my\\_bot\\*.toml) with" in markdown

    def test_markdown_label_is_escaped(self) -> None:
        report = Report(package_path="/work/pkg")
        text = _render_markdown(report, False, ["error"], " (config: <b>@x</b>)")
        assert "Checked pkg (config: \\<b\\>@​x\\</b\\>) with" in text


class TestUsageErrors:
    """Every mistake exits 2 with one line and runs no check."""

    @pytest.mark.parametrize(
        "content, message",
        [
            # The parser's own sentence follows, and its words are not ours.
            ("profile = ", "The file is not valid TOML. "),
            (b'profile = "tri\xff"\n', "The file is not valid UTF-8."),
            ('[project]\nname = "x"\n[tool.x]\n', "Unknown key(s) project, tool. "),
            ("[checks]\ndocs = false\n", "The file names no profile. "),
            ('profile = "default"\n', 'but it is "default".'),
            ('profile = "all"\n', "profile must be one of minimal, triage, core, "),
            ("profile = 3\n", "but it is 3."),
            ('profile = "triage"\nfail_on = "fatal"\n', "fail_on must be error, "),
            ('profile = "triage"\nfail_on = [3]\n', "but it is [3]."),
            ('profile = "triage"\nfail_on = true\n', "but it is true."),
            ('profile = "triage"\nchecks = "all"\n', "checks must be a table, "),
            (
                'profile = "triage"\n[checks]\nnope = false\n',
                "Unknown check nope in [checks]. The profile triage holds "
                "changelog, ci, citation, ",
            ),
            (
                '[checks]\ndocs = true\nprofile = "triage"\n',
                "The profile line is below [checks], where it is read as a check. "
                "Move it above [checks].",
            ),
            (
                'profile = "triage"\n[checks]\nfail_on = "note"\n',
                "Unknown check fail_on in [checks]. fail_on is a key of the file "
                "itself, so move its line above [checks]. The profile triage holds ",
            ),
            (
                'profile = "triage"\n[checks]\ntests = false\n',
                "tests is not in the profile triage. A config file can take a "
                "check out of a profile and cannot add one.",
            ),
            (
                'profile = "triage"\n[checks]\ntests = { timeout = 5 }\n',
                "tests is not in the profile triage.",
            ),
            (
                'profile = "triage"\n[checks]\ndocs = 3\n',
                "docs must be true, false or a table of options, but it is 3.",
            ),
            (
                'profile = "triage"\n[checks]\ndocs = { colour = 1 }\n',
                "Unknown option colour for the check docs. Its options are enabled, "
                "require_readme, ",
            ),
            (
                'profile = "triage"\n[checks]\nlicense = { strict = true }\n',
                "Unknown option strict for the check license. Its options are enabled.",
            ),
            (
                'profile = "triage"\n[checks]\ndocs = { check_docstrings = "no" }\n',
                'docs.check_docstrings must be true or false, but it is "no".',
            ),
            (
                'profile = "triage"\n[checks]\ndocs = { enabled = 1 }\n',
                "docs.enabled must be true or false, but it is 1.",
            ),
            (
                'profile = "triage"\n[checks]\n'
                "docs = { min_docstring_coverage = true }\n",
                "docs.min_docstring_coverage must be a number from 0 to 100, ",
            ),
            (
                'profile = "strict"\n[checks]\ntests = { timeout = -1 }\n',
                "tests.timeout must be a number above 0 and at most 86400, "
                "but it is -1.",
            ),
            (
                'profile = "strict"\n[checks]\ntests = { timeout = true }\n',
                "tests.timeout must be a number above 0 and at most 86400, ",
            ),
            (
                'profile = "strict"\n[checks]\ntests = { timeout = nan }\n',
                "tests.timeout must be ",
            ),
            (
                'profile = "strict"\n[checks]\nurls = { timeout = 31 }\n',
                "urls.timeout must be a number from 1 to 30, but it is 31.",
            ),
            (
                'profile = "strict"\n[checks]\ntests = { runner = "nose" }\n',
                'tests.runner must be one of pytest, unittest, but it is "nose".',
            ),
            (
                'profile = "strict"\n[checks]\nlinting = { tool = "nose" }\n',
                "linting.tool must be one of auto, ruff, flake8, pylint, ",
            ),
            (
                'profile = "strict"\n[checks]\nbuild = { args = "--wheel" }\n',
                'build.args must be a list of strings, but it is "--wheel".',
            ),
            (
                'profile = "strict"\n[checks]\nbuild = { args = ["a", 1] }\n',
                "build.args must be a list of strings, but it holds 1.",
            ),
            (
                'profile = "strict"\n[checks]\n'
                'tests = { args = ["a", "b", "c", "d", "e", "f", "g", 2026-10-10] }\n',
                'tests.args must be a list of strings, but it holds "2026-10-10".',
            ),
            (
                b'\xef\xbb\xbfprofile = "triage"\n',
                "The file starts with a UTF-8 byte order mark, which TOML does "
                "not allow.",
            ),
            (
                f'profile = "strict"\n[checks]\ntests = {{ timeout = {HUGE} }}\n',
                "tests.timeout must be a number above 0 and at most 86400, "
                "but it is a value too large to show.",
            ),
            (f"profile = {HUGE}\n", "but it is a value too large to show."),
            (
                'profile = "triage"\n' + "".join(f"k{n} = 1\n" for n in range(400)),
                "k127, k128, .... A config file holds profile, ",
            ),
            # Each rule of the option table, at the edge of what it allows.
            *[
                (f'profile = "strict"\n[checks]\n{entry}\n', f"{option} must be ")
                for option, entry in [
                    ("build.timeout", "build = { timeout = 0 }"),
                    ("doctests.timeout", "doctests = { timeout = 86401 }"),
                    ("formatting.timeout", "formatting = { timeout = inf }"),
                    ("linting.timeout", 'linting = { timeout = "9" }'),
                    ("typing.timeout", "typing = { timeout = 86400.5 }"),
                    ("urls.timeout", "urls = { timeout = 0.5 }"),
                    ("urls.timeout", "urls = { timeout = true }"),
                    (
                        "docs.min_docstring_coverage",
                        "docs = { min_docstring_coverage = -1 }",
                    ),
                    (
                        "docs.min_docstring_coverage",
                        "docs = { min_docstring_coverage = 100.5 }",
                    ),
                    ("docs.require_readme", "docs = { require_readme = 1 }"),
                    (
                        "docs.check_readme_sections",
                        'docs = { check_readme_sections = "no" }',
                    ),
                    ("docs.check_docs_site", "docs = { check_docs_site = 0 }"),
                    ("typing.strict", 'typing = { strict = "yes" }'),
                    ("typing.tool", 'typing = { tool = "pytype" }'),
                    ("formatting.tool", 'formatting = { tool = "yapf" }'),
                    ("tests.runner", "tests = { runner = true }"),
                    ("doctests.args", "doctests = { args = [[]] }"),
                ]
            ],
            (
                'profile = "minimal"\n[checks]\nmetadata = false\n'
                "structure = { enabled = false }\nlicense = false\n",
                "The file leaves no check to run.",
            ),
        ],
    )
    def test_mistake_in_the_file(
        self, config_file, project, calls, content, message
    ) -> None:
        path = config_file(content)
        result = _run("--config", path, "--json", "--exit-zero", str(project))
        assert result.exit_code == 2, result.output
        assert result.stdout == ""
        assert f"Error: Config file {path}: " in result.stderr
        assert message in result.stderr
        assert calls == []

    @pytest.mark.parametrize(
        "content",
        [
            # Found while the file is parsed, in an option, and at the very end.
            "profile = ",
            'profile = "strict"\n[checks]\ntests = { timeout = -1 }\n',
            'profile = "minimal"\n[checks]\nmetadata = false\n'
            "structure = { enabled = false }\nlicense = false\n",
        ],
    )
    @pytest.mark.parametrize("mode", [["--format", "markdown"], ["--list"]])
    def test_mistake_is_reported_in_the_other_modes(
        self, config_file, project, calls, content, mode
    ) -> None:
        """The file is read before the mode, so a listing reports it as well."""
        path = config_file(content)
        result = _run("--config", path, *mode, "--exit-zero", str(project))
        assert result.exit_code == 2, result.output
        assert result.stdout == ""
        assert f"Error: Config file {path}: " in result.stderr
        assert calls == []

    def test_file_above_the_size_cap(
        self, config_file, project, calls, monkeypatch
    ) -> None:
        monkeypatch.setattr("pycmdcheck.config.MAX_PYPROJECT_BYTES", 16)
        result = _run("--config", config_file(EXAMPLE), "--json", str(project))
        assert result.exit_code == 2
        assert "The file could not be read (larger than 16 bytes)." in result.stderr

    def test_file_that_nests_too_deeply(self, config_file, project, calls) -> None:
        """The parser runs out of stack, which must not be an internal error."""
        path = config_file('profile = "triage"\nfail_on = ' + "[" * 5000)
        result = _run("--config", path, "--json", str(project))
        assert result.exit_code == 2, result.output
        assert f"Config file {path}: The file " in result.stderr
        assert calls == []

    def test_values_at_the_edge_are_accepted(self, config_file, project, calls) -> None:
        content = (
            'profile = "strict"\n[checks]\nbuild = { timeout = 86400, args = [] }\n'
            "tests = { timeout = 0.5 }\nurls = { timeout = 30 }\n"
            "docs = { min_docstring_coverage = 100 }\n"
        )
        assert _run("--config", config_file(content), str(project)).exit_code == 0

    def test_text_from_the_file_is_cleaned(self, config_file, project, calls) -> None:
        content = 'profile = "triage"\n"\\u001b[31m\\n" = 1\n'
        result = _run("--config", config_file(content), str(project))
        assert result.exit_code == 2
        assert "Unknown key(s) \\x1b[31m\\n." in result.stderr

    @pytest.mark.parametrize("target", ["missing.toml", "project"])
    def test_file_that_is_missing_or_a_directory(
        self, tmp_path, project, calls, target
    ) -> None:
        result = _run("--config", str(tmp_path / target), "--json", str(project))
        assert result.exit_code == 2
        assert result.stdout == ""
        # The option refuses it, before the file is looked into.
        assert "--config" in result.stderr
        assert "Config file" not in result.stderr
        assert calls == []

    @pytest.mark.parametrize(
        "args, message",
        [
            (
                ["--profile", "triage"],
                "--config cannot be used with --profile. The config file names "
                "the profile.",
            ),
            (["-c", "tests"], "Check(s) not run by the config file: tests. It runs "),
            (
                ["-c", "docs", "-c", "citation"],
                "config file: citation. It runs changelog, ci, ",
            ),
            (["-c", "nope"], "Unknown check(s): nope."),
            (["-s", "nope"], "Unknown check(s) given to --skip: nope."),
            (["-c", "docs", "-s", "docs"], "--skip leaves no check to run."),
        ],
    )
    def test_command_line(self, config_file, project, calls, args, message) -> None:
        path = config_file(EXAMPLE)
        result = _run("--config", path, *args, "--json", "--exit-zero", str(project))
        assert result.exit_code == 2, result.output
        assert result.stdout == ""
        assert message in result.stderr
        assert calls == []

    @pytest.mark.parametrize("off", [[], PROFILES["minimal"].checks])
    def test_skipping_every_check_of_the_file(
        self, config_file, project, calls, off
    ) -> None:
        """What the project itself turns off plays no part in a run from a file."""
        table = "".join(f"{name} = false\n" for name in off)
        (project / "pyproject.toml").write_text(f"[tool.pycmdcheck.checks]\n{table}")
        skips = [arg for name in PROFILES["minimal"].checks for arg in ("-s", name)]
        path = config_file('profile = "minimal"\n')
        result = _run("--config", path, *skips, str(project))
        assert result.exit_code == 2
        assert "--skip leaves no check to run." in result.stderr
        assert calls == []
        # One check is left, and the project has it turned off.
        result = _run("--config", path, *skips[2:], "--exit-zero", str(project))
        assert result.exit_code == 0, result.output
        assert len(calls) == 1
