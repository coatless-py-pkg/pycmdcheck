"""Tests for the linting check."""

import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pycmdcheck import discovery
from pycmdcheck.checks.linting import LintingCheck
from pycmdcheck.config import get_check_config, load_config
from pycmdcheck.constants import DEFAULT_TIMEOUT, LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult
from tests.helpers import only_code, write_files

# The tool fixture in conftest.py reads this to know which module to patch.
MODULE = "pycmdcheck.checks.linting"

# The end of "ruff check" output in its default format (ruff 0.14.10), for a
# file with two unused imports: 19 non-blank lines for 2 findings.
RUFF_FULL_OUTPUT = """\
F401 [*] `os` imported but unused
 --> src/demo/__init__.py:1:8
  |
1 | import os
  |        ^^
2 | import sys
  |
help: Remove unused import: `os`

F401 [*] `sys` imported but unused
 --> src/demo/__init__.py:2:8
  |
1 | import os
2 | import sys
  |        ^^^
3 |
4 | x = 1
  |
help: Remove unused import: `sys`

Found 2 errors.
[*] 2 fixable with the `--fix` option.
"""

# pylint 3 text output on its second run, when it adds the rating line.
PYLINT_OUTPUT = """\
************* Module demo
src/demo/__init__.py:1:0: C0114: Missing module docstring (missing-module-docstring)
src/demo/__init__.py:3:6: E0602: Undefined variable 'y' (undefined-variable)

------------------------------------------------------------------
Your code has been rated at 0.00/10 (previous run: 0.00/10, +0.00)
"""


def ruff_json(root: Path, count: int) -> str:
    """Build ``ruff check --output-format=json`` output (shape of ruff 0.14.10).

    The last entry is given no code, as some versions of ruff write for a
    syntax error. The check shows it as "error".
    """
    entries = [
        {
            "cell": None,
            "code": "F401" if row < count else None,
            "end_location": {"column": 10, "row": row},
            "filename": str(root.resolve() / "src" / "demo" / "__init__.py"),
            "fix": None,
            "location": {"column": 8, "row": row},
            "message": "`os` imported but unused",
            "noqa_row": row,
            "url": "https://docs.astral.sh/ruff/rules/unused-import",
        }
        for row in range(1, count + 1)
    ]
    return json.dumps(entries, indent=2)


def run_with_table(root: Path, options: str) -> CheckResult:
    """Run the check with *options* as its table in the project's pyproject.toml."""
    table = f"[tool.pycmdcheck.checks.linting]\n{options}\n"
    write_files(root, {"pyproject.toml": table})
    return LintingCheck().run(root, get_check_config(load_config(root), "linting"))


class TestDetection:
    """Which linter tool = "auto" picks, and what happens without one."""

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({"pyproject.toml": "[tool.ruff]\nline-length = 100\n"}, "ruff"),
            ({"pyproject.toml": "[tool.ruff.lint]\nselect = ['E']\n"}, "ruff"),
            ({"ruff.toml": "line-length = 100\n"}, "ruff"),
            ({".ruff.toml": "line-length = 100\n"}, "ruff"),
            ({".flake8": "[flake8]\nmax-line-length = 100\n"}, "flake8"),
            ({"setup.cfg": "[flake8]\nmax-line-length = 100\n"}, "flake8"),
            ({"tox.ini": "[flake8]\nmax-line-length = 100\n"}, "flake8"),
            ({".pylintrc": "[MAIN]\njobs = 1\n"}, "pylint"),
            ({"pylintrc": "[MAIN]\njobs = 1\n"}, "pylint"),
            ({"pyproject.toml": "[tool.pylint.main]\njobs = 1\n"}, "pylint"),
            # ruff is looked for first, then flake8, then pylint
            ({"ruff.toml": "", ".flake8": "", ".pylintrc": ""}, "ruff"),
            ({".flake8": "", ".pylintrc": ""}, "flake8"),
        ],
    )
    def test_auto_runs_the_configured_linter(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        files: dict[str, str],
        expected: str,
    ) -> None:
        run = tool()
        result = LintingCheck().run(write_files(tmp_path, files), {"tool": "auto"})
        assert result.status == CheckStatus.OK
        assert result.findings == []
        assert result.skip_reason is None
        assert run.call_args.args[0][0] == expected
        assert result.details == [f"Using linter: {expected} 9.9"]

    @pytest.mark.parametrize(
        "files",
        [
            {},
            {"pyproject.toml": "[tool.black]\n", "setup.cfg": "[metadata]\n"},
            {"tox.ini": "not an ini file\n"},
        ],
        ids=["empty", "other-tools", "unreadable-ini"],
    )
    def test_no_linter_configured_is_skipped(
        self, tmp_path: Path, tool: Callable[..., MagicMock], files: dict[str, str]
    ) -> None:
        run = tool()
        result = LintingCheck().run(write_files(tmp_path, files), {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "not-configured"
        assert result.findings == []
        assert result.message == "No linter configured"
        assert any(d.startswith("Looked for [tool.ruff]") for d in result.details)
        assert any(
            'tool = "ruff"' in d and "[tool.pycmdcheck.checks.linting]" in d
            for d in result.details
        )
        # A run from a config file does not read that table.
        assert any('tool = "' in d and "--config file" in d for d in result.details)
        run.assert_not_called()

    def test_unreadable_pyproject_is_a_failed_prerequisite(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The metadata check reports the file. That is not "no linter configured"."""
        run = tool()
        files = {"pyproject.toml": "[tool\n", "tox.ini": "not an ini file\n"}
        result = LintingCheck().run(write_files(tmp_path, files), {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "prerequisite-failed"
        assert result.findings == []
        assert result.message == "pyproject.toml could not be read"
        assert result.details == ["The metadata check reports the reason"]
        run.assert_not_called()

    @pytest.mark.parametrize(
        ("files", "config", "expected"),
        [
            ({".flake8": "[flake8]\n"}, {}, "flake8"),
            ({}, {"tool": "pylint"}, "pylint"),
        ],
        ids=["other-file", "explicit-tool"],
    )
    def test_unreadable_pyproject_does_not_stop_a_linter_named_elsewhere(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        files: dict[str, str],
        config: dict[str, str],
        expected: str,
    ) -> None:
        run = tool()
        root = write_files(tmp_path, {"pyproject.toml": "[tool\n", **files})
        result = LintingCheck().run(root, config)
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0][0] == expected

    def test_explicit_tool_overrides_detection(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        root = write_files(tmp_path, {"ruff.toml": "line-length = 100\n"})
        result = LintingCheck().run(root, {"tool": "flake8"})
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0][0] == "flake8"


class TestCouldNotRun:
    """A linter that is installed but could not do its job is a WARNING."""

    @pytest.mark.parametrize(
        ("name", "outcome", "reason"),
        [
            (
                "ruff",
                SubprocessResult(error="Could not start ruff: denied"),
                ["Could not start ruff: denied"],
            ),
            (
                "ruff",
                SubprocessResult(
                    returncode=2, stderr="error: invalid value 'bogus' for '--select'\n"
                ),
                [
                    "ruff exited with code 2",
                    "error: invalid value 'bogus' for '--select'",
                ],
            ),
            (
                "flake8",
                SubprocessResult(
                    returncode=2, stderr="flake8: error: unrecognized arguments: -Z\n"
                ),
                [
                    "flake8 exited with code 2",
                    "flake8: error: unrecognized arguments: -Z",
                ],
            ),
            (
                "ruff",
                SubprocessResult(returncode=1, stderr="thread 'main' panicked\n"),
                ["ruff exited with code 1", "thread 'main' panicked"],
            ),
            ("ruff", SubprocessResult(returncode=-9), ["ruff exited with code -9"]),
            (
                "pylint",
                SubprocessResult(
                    returncode=32,
                    stderr="usage: pylint [options]\npylint: error: no such option\n",
                ),
                [
                    "pylint exited with code 32",
                    "usage: pylint [options]",
                    "pylint: error: no such option",
                ],
            ),
            (
                "pylint",
                SubprocessResult(returncode=1, stderr="ImportError: no astroid\n"),
                ["pylint exited with code 1", "ImportError: no astroid"],
            ),
        ],
        ids=[
            "not-started",
            "ruff-usage",
            "flake8-usage",
            "ruff-crash",
            "ruff-killed",
            "pylint-usage",
            "pylint-crash",
        ],
    )
    def test_reports_the_exit_code_and_the_end_of_the_output(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        name: str,
        outcome: SubprocessResult,
        reason: list[str],
    ) -> None:
        tool(outcome)
        result = LintingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.WARNING
        assert result.message == f"{name} could not run"
        assert only_code(result) == "linting.could-not-run"
        assert result.details == [f"Using linter: {name} 9.9", *reason]


class TestOptions:
    """Options and tool handling shared by the three linters."""

    def test_unsupported_tool(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = LintingCheck().run(temp_package, {"tool": "nosuchlinter"})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Unsupported linting tool: nosuchlinter"
        assert only_code(result) == "linting.unsupported-tool"
        assert result.details == []
        run.assert_not_called()

    @pytest.mark.parametrize("name", ["ruff", "flake8", "pylint"])
    def test_not_installed(
        self, temp_package: Path, tool: Callable[..., MagicMock], name: str
    ) -> None:
        run = tool(available=False)
        result = LintingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "tool-missing"
        assert result.findings == []
        assert result.message == f"{name} not installed"
        assert any(f"pip install {name}" in d for d in result.details)
        # only ruff comes with a pycmdcheck extra
        names_extra = any("pycmdcheck[linting]" in d for d in result.details)
        assert names_extra is (name == "ruff")
        run.assert_not_called()

    @pytest.mark.parametrize(
        ("name", "default"),
        [
            ("ruff", DEFAULT_TIMEOUT),
            ("flake8", DEFAULT_TIMEOUT),
            ("pylint", LONG_TIMEOUT),
        ],
    )
    def test_clean_run_shows_version_and_uses_the_default_timeout(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        name: str,
        default: int,
    ) -> None:
        run = tool()
        result = LintingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.OK
        assert result.details == [f"Using linter: {name} 9.9"]
        assert run.call_args.kwargs == {"cwd": temp_package, "timeout": default}

    def test_timeout_option(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool(SubprocessResult(timed_out=True))
        result = LintingCheck().run(temp_package, {"tool": "ruff", "timeout": 7})
        assert run.call_args.kwargs["timeout"] == 7
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()
        assert only_code(result) == "linting.timed-out"
        assert "7 seconds" in result.findings[0].message
        assert any("7 seconds" in d for d in result.details)

    def test_invalid_timeout_uses_the_default(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = LintingCheck().run(temp_package, {"tool": "ruff", "timeout": -1})
        assert run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT
        assert any("Ignored timeout" in d for d in result.details)

    def test_invalid_args_option(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        config = {"tool": "ruff", "args": "--select=E"}
        result = LintingCheck().run(temp_package, config)
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "linting.invalid-args"
        assert any("list of strings" in d for d in result.details)
        run.assert_not_called()


class TestHints:
    """Doing what a hint says makes its rule stop firing.

    Only the hints about options are here, each written to pyproject.toml the
    way the hint spells it. Following the others changes what the linter
    reports, and these tests do not start it.
    """

    def test_every_tool_the_hint_names_is_accepted(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        result = run_with_table(tmp_path, 'tool = "nosuchlinter"')
        assert only_code(result) == "linting.unsupported-tool"
        named = re.findall(r'"(\w+)"', LintingCheck.hints["linting.unsupported-tool"])
        assert named == ["auto", *LintingCheck.SUPPORTED_TOOLS]
        for name in named:
            assert run_with_table(tmp_path, f'tool = "{name}"').findings == []

    def test_args_written_as_the_hint_shows_are_accepted(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        result = run_with_table(tmp_path, 'tool = "ruff"\nargs = "--fix"')
        assert only_code(result) == "linting.invalid-args"
        hint = result.findings[0].hint or ""
        wrong, right = re.search(r'so (".+") becomes (\[.+\])\.$', hint).groups()
        result = run_with_table(tmp_path, f'tool = "ruff"\nargs = {wrong}')
        assert only_code(result) == "linting.invalid-args"
        result = run_with_table(tmp_path, f'tool = "ruff"\nargs = {right}')
        assert result.findings == []

    def test_ruff_options_the_hint_names_are_the_ones_to_remove(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Each cause has its own hint, so neither names what was not written."""
        tool()
        args = ["--select=E", "--add-noqa", "--output-file=x", "--watch", "-ox", "-w"]
        result = run_with_table(tmp_path, f'tool = "ruff"\nargs = {json.dumps(args)}')
        assert only_code(result) == "linting.invalid-args"
        hint = result.findings[0].hint or ""
        start, end = "Remove ", " from the args option of the linting check."
        assert hint.startswith(start) and hint.endswith(end)
        named = hint[len(start) : -len(end)].split(", ")
        kept = [arg for arg in args if arg.split("=")[0] not in named]
        assert kept == ["--select=E"]
        result = run_with_table(tmp_path, f'tool = "ruff"\nargs = {json.dumps(kept)}')
        assert result.findings == []

    def test_longer_timeout_lets_a_slow_linter_finish(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The linter is a stub that needs 400 seconds, more than the default."""
        run = tool()
        run.side_effect = lambda cmd, **kw: SubprocessResult(
            returncode=0, timed_out=kw["timeout"] < 400
        )
        result = run_with_table(tmp_path, 'tool = "ruff"')
        assert only_code(result) == "linting.timed-out"
        # run_with_table writes the option to the table that the hint names.
        assert LintingCheck.hints["linting.timed-out"].startswith(
            "Set a longer timeout in [tool.pycmdcheck.checks.linting]"
        )
        result = run_with_table(tmp_path, 'tool = "ruff"\ntimeout = 900')
        assert result.findings == []


# A configured value that would start a second line of a report.
TWO_LINES = "ruff\nERROR forged"


@pytest.mark.parametrize(
    ("name", "config", "code"),
    [
        ("linting", {"tool": TWO_LINES}, "linting.unsupported-tool"),
        ("typing", {"tool": TWO_LINES}, "typing.unsupported-tool"),
        ("formatting", {"tool": TWO_LINES}, "formatting.unsupported-tool"),
        ("tests", {"runner": TWO_LINES}, "tests.unsupported-runner"),
        (
            "linting",
            {"tool": "ruff", "args": [f"-o{TWO_LINES}"]},
            "linting.invalid-args",
        ),
    ],
    ids=["linting-tool", "typing-tool", "formatting-tool", "tests-runner", "ruff-args"],
)
def test_configured_value_stays_on_one_line(
    name: str,
    config: dict[str, object],
    code: str,
    temp_package: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A value the checked project sets cannot break a finding into lines.

    This covers every check that shows such a value, so the other three are
    tested here as well.
    """
    check_class = discovery.discover_checks()[name]
    module = check_class.__module__
    run = MagicMock()
    monkeypatch.setattr(f"{module}.tool_available", lambda tool: True)
    monkeypatch.setattr(f"{module}.describe_tool", lambda tool, **kw: tool)
    monkeypatch.setattr(f"{module}.run_tool", run)
    result = check_class().run(temp_package, config)
    assert result.status == CheckStatus.ERROR
    assert only_code(result) == code
    assert r"ruff\nERROR forged" in result.findings[0].message
    assert "\n" not in result.message + "".join(result.details)
    run.assert_not_called()


class TestRuff:
    """The ruff backend, with the tool replaced."""

    @pytest.mark.parametrize(
        ("args", "named"),
        [
            (["--add-noqa"], "--add-noqa"),
            (["--output-file=NOTES.txt"], "--output-file"),
            (["-o", "NOTES.txt"], "-o"),
            (["--select", "E", "--watch"], "--watch"),
        ],
    )
    def test_arguments_that_write_or_wait_are_refused(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        args: list[str],
        named: str,
    ) -> None:
        """The check only reads, so ruff is not started with such an argument."""
        run = tool()
        result = LintingCheck().run(temp_package, {"tool": "ruff", "args": args})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "linting.invalid-args"
        assert named in result.findings[0].message
        assert named in result.details[0]
        # The option may come from a config file, so no table is named.
        assert "[tool.pycmdcheck" not in result.details[0]
        run.assert_not_called()

    def test_command_never_fixes_or_caches(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """A project with fix or fix-only set must not have its files rewritten."""
        run = tool()
        config = {"tool": "ruff", "args": ["--select", "E,F"]}
        LintingCheck().run(temp_package, config)
        assert run.call_args.args[0] == [
            "ruff",
            "check",
            ".",
            "--no-cache",
            "--output-format=json",
            "--select",
            "E,F",
            "--no-fix",
            "--no-fix-only",
        ]

    def test_a_fix_flag_in_the_project_args_cannot_win(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """ruff takes the last of --fix and --no-fix, so ours come last."""
        run = tool()
        config = {"tool": "ruff", "args": ["--fix", "--fix-only"]}
        LintingCheck().run(temp_package, config)
        assert run.call_args.args[0][-2:] == ["--no-fix", "--no-fix-only"]

    def test_json_entries_are_counted_and_rendered(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool(SubprocessResult(returncode=1, stdout=ruff_json(temp_package, 2)))
        result = LintingCheck().run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.WARNING
        assert result.message == "Found 2 linting issue(s)"
        assert only_code(result) == "linting.issues"
        assert result.findings[0].message == "Found 2 linting issue(s)"
        assert result.details[1:] == [
            "src/demo/__init__.py:1:8 F401 `os` imported but unused",
            "src/demo/__init__.py:2:8 error `os` imported but unused",
        ]

    def test_long_list_says_how_many_were_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        count = MAX_DETAIL_LINES + 4
        tool(SubprocessResult(returncode=1, stdout=ruff_json(temp_package, count)))
        result = LintingCheck().run(temp_package, {"tool": "ruff"})
        assert result.message == f"Found {count} linting issue(s)"
        assert result.details[-1] == "... and 4 more issues"
        # one finding for the rule, with the count, not one for each issue
        assert only_code(result) == "linting.issues"
        assert result.findings[0].message == f"Found {count} linting issue(s)"

    @pytest.mark.parametrize(
        "args",
        [["--output-format", "full"], ["--output-format=full"], ["--statistics"]],
    )
    def test_user_output_format_is_kept_and_the_trailer_counted(
        self, temp_package: Path, tool: Callable[..., MagicMock], args: list[str]
    ) -> None:
        """ruff refuses a repeated --output-format, so none is added."""
        run = tool(SubprocessResult(returncode=1, stdout=RUFF_FULL_OUTPUT))
        result = LintingCheck().run(temp_package, {"tool": "ruff", "args": args})
        assert "--output-format=json" not in run.call_args.args[0]
        assert result.status == CheckStatus.WARNING
        assert result.message == "Found 2 linting issue(s)"
        assert "lines of output" in result.details[-1]

    def test_one_line_per_issue_output(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Output that is neither JSON nor has a trailer is counted by line."""
        tool(
            SubprocessResult(
                returncode=1,
                stdout=(
                    "file.py:1:1: E501 line too long\n"
                    "file.py:2:1: W291 trailing whitespace\n"
                ),
            )
        )
        result = LintingCheck().run(temp_package, {"tool": "ruff"})
        assert result.status == CheckStatus.WARNING
        assert "2" in result.message


class TestOtherLinters:
    """flake8 and pylint, with the tool replaced."""

    def test_pylint_rating_line_is_not_an_issue(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool(SubprocessResult(returncode=18, stdout=PYLINT_OUTPUT))
        result = LintingCheck().run(temp_package, {"tool": "pylint"})
        assert run.call_args.args[0] == [
            "pylint",
            str(temp_package / "src"),
            "--output-format=text",
            "--score=n",
        ]
        assert result.status == CheckStatus.WARNING
        assert result.message == "Found 2 linting issue(s)"
        assert only_code(result) == "linting.issues"
        assert not any("rated" in d or "*****" in d for d in result.details)

    def test_pylint_other_message_template_does_not_claim_zero_issues(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """A msg-template of the project's own is a finding, not a failed run."""
        tool(
            SubprocessResult(
                returncode=16,
                stdout="mod.py(3): missing docstring\n",
                stderr="UserWarning: 'Exception' is not a proper value\n",
            )
        )
        result = LintingCheck().run(temp_package, {"tool": "pylint"})
        assert result.status == CheckStatus.WARNING
        assert result.message == "pylint exited with code 16 without listing issues"
        assert only_code(result) == "linting.issues"
        assert len(result.details) > 1

    def test_flake8_counts_one_issue_per_line(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        stdout = "./a.py:1:1: F401 'os' imported but unused\n./a.py:2:1: E302 x\n"
        run = tool(SubprocessResult(returncode=1, stdout=stdout))
        result = LintingCheck().run(temp_package, {"tool": "flake8"})
        assert run.call_args.args[0] == ["flake8", "."]
        assert result.status == CheckStatus.WARNING
        assert result.message == "Found 2 linting issue(s)"
        assert only_code(result) == "linting.issues"


def _hashes(root: Path) -> dict[str, str]:
    """Return the SHA-256 of every file directly under ``root``."""
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        if path.is_file()
        else ""
        for path in root.iterdir()
    }


class TestRealRuff:
    """The one test here that starts ruff: the project must be left untouched."""

    @pytest.mark.tools("ruff")
    @pytest.mark.parametrize("setting", ["fix", "fix-only"])
    def test_fixing_configuration_does_not_rewrite_files(
        self, tmp_path: Path, setting: str
    ) -> None:
        project = '[project]\nname = "pkg"\nversion = "0.1.0"\n'
        (tmp_path / "pyproject.toml").write_text(
            f"{project}\n[tool.ruff]\n{setting} = true\n"
        )
        (tmp_path / "mod.py").write_text("import os\nimport sys\n\nx = 1\n")
        before = _hashes(tmp_path)
        result = LintingCheck().run(tmp_path, {})
        # Same two files with the same content, and no .ruff_cache beside them.
        assert _hashes(tmp_path) == before
        assert result.status == CheckStatus.WARNING
        assert result.message == "Found 2 linting issue(s)"

    @pytest.mark.tools("ruff")
    @pytest.mark.parametrize("flag", ["--fix", "--fix-only"])
    def test_fix_flag_in_project_args_does_not_rewrite_files(
        self, tmp_path: Path, flag: str
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "0.1.0"\n'
        )
        (tmp_path / "mod.py").write_text("import os\nimport sys\n\nx = 1\n")
        before = _hashes(tmp_path)
        result = LintingCheck().run(tmp_path, {"tool": "ruff", "args": [flag]})
        assert _hashes(tmp_path) == before
        assert result.status == CheckStatus.WARNING
