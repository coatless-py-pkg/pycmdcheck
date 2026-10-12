"""Tests for formatting check."""

import re
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pycmdcheck.checks.formatting import FormattingCheck
from pycmdcheck.config import get_check_config, load_config
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult
from tests.helpers import only_code, write_files

# The tool fixture in conftest.py reads this to know which module to patch.
MODULE = "pycmdcheck.checks.formatting"

# "ruff format --check" on stdout, ruff 0.14.10.
RUFF_OUTPUT = """\
Would reformat: src/demo/__init__.py
Would reformat: src/demo/core.py
2 files would be reformatted, 1 file already formatted
"""

# The same from ruff 0.16, which prints a diagnostic and a diff per file.
RUFF_016_OUTPUT = """\
unformatted: File would be reformatted
 --> src/demo/__init__.py:1:1
  |
1 | -x=1
1 | +x = 1
  |

1 file would be reformatted
"""

# black 25 writes everything to stderr.
BLACK_STDERR = """\
would reformat /work/demo/src/demo/__init__.py
would reformat /work/demo/src/demo/core.py

Oh no! \U0001f4a5 \U0001f494 \U0001f4a5
2 files would be reformatted, 1 file would be left unchanged.
"""

BLACK_CRASH_STDERR = """\
error: cannot format /work/demo/src/demo/bad.py: Cannot parse: 1:6: def f(:

Oh no! \U0001f4a5 \U0001f494 \U0001f4a5
1 file failed to reformat.
"""


def _pyproject(root: Path, extra: str, files: dict[str, str] | None = None) -> Path:
    """Write pyproject.toml with *extra* below its project table, then *files*."""
    project = f'[project]\nname = "pkg"\nversion = "0.1.0"\n\n{extra}'
    return write_files(root, {"pyproject.toml": project, **(files or {})})


def run_with_table(root: Path, options: str) -> CheckResult:
    """Run the check with *options* as its table in the project's pyproject.toml."""
    _pyproject(root, f"[tool.pycmdcheck.checks.formatting]\n{options}\n")
    config = get_check_config(load_config(root), "formatting")
    return FormattingCheck().run(root, config)


class TestDetection:
    """Which formatter tool = "auto" picks, and what happens without one."""

    @pytest.mark.parametrize(
        ("pyproject", "files"),
        [
            ("", {}),
            # ruff set up for linting says nothing about the formatter
            ("[tool.ruff]\nline-length = 88\n", {}),
            ("[tool.ruff.lint]\nselect = ['E']\n", {"ruff.toml": "line-length = 88\n"}),
            # a key that only ends in "format", and its older name
            ("", {"ruff.toml": 'output-format = "full"\n'}),
            ("", {"ruff.toml": 'format = "github"\n'}),
            # keys with these names that are not tables
            ("[tool]\nblack = true\n\n[tool.ruff]\nformat = 'x'\n", {}),
        ],
        ids=[
            "no-config",
            "bare-ruff-table",
            "ruff-toml-without-format",
            "ruff-toml-output-format",
            "ruff-toml-format-that-is-no-table",
            "not-tables",
        ],
    )
    def test_no_formatter_configured_is_skipped(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        pyproject: str,
        files: dict[str, str],
    ) -> None:
        """No formatter section -> SKIPPED."""
        run = tool()
        _pyproject(tmp_path, pyproject, files)
        result = FormattingCheck().run(tmp_path, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "not-configured"
        assert result.findings == []
        assert result.message == "No formatter configured"
        assert any(
            d.startswith("Looked for [tool.ruff.format]") for d in result.details
        )
        assert any(
            'tool = "ruff"' in d and "[tool.pycmdcheck.checks.formatting]" in d
            for d in result.details
        )
        # A run from a config file does not read that table.
        assert any('tool = "' in d and "--config file" in d for d in result.details)
        run.assert_not_called()

    def test_unreadable_pyproject_is_a_failed_prerequisite(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The metadata check reports the file. That is not "none configured"."""
        run = tool()
        _pyproject(tmp_path, "[project\nname = 1\n")
        (tmp_path / "ruff.toml").write_text("line-length = 88\n")
        result = FormattingCheck().run(tmp_path, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "prerequisite-failed"
        assert result.findings == []
        assert result.message == "pyproject.toml could not be read"
        assert result.details == ["The metadata check reports the reason"]
        run.assert_not_called()

    @pytest.mark.parametrize(
        ("files", "config", "expected"),
        [
            ({"ruff.toml": "[format]\nquote-style = 'single'\n"}, {}, "ruff"),
            ({}, {"tool": "black"}, "black"),
        ],
        ids=["other-file", "explicit-tool"],
    )
    def test_unreadable_pyproject_does_not_stop_a_formatter_named_elsewhere(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        files: dict[str, str],
        config: dict[str, str],
        expected: str,
    ) -> None:
        run = tool()
        _pyproject(tmp_path, "[project\nname = 1\n", files)
        result = FormattingCheck().run(tmp_path, config)
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0][0] == expected

    def test_explicit_tool_overrides_detection(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        _pyproject(tmp_path, "[tool.black]\nline-length = 100\n")
        result = FormattingCheck().run(tmp_path, {"tool": "ruff"})
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0][0] == "ruff"

    @pytest.mark.parametrize(
        ("pyproject", "files", "expected"),
        [
            ("[tool.black]\nline-length = 100\n", {}, "black"),
            # an empty table is enough to opt in to ruff's default style
            ("[tool.ruff.format]\n", {}, "ruff"),
            ("", {"ruff.toml": "[format]\nquote-style = 'single'\n"}, "ruff"),
            # a dotted key says the same as the section
            ("", {"ruff.toml": "format.quote-style = 'single'\n"}, "ruff"),
            # and so does the table written on one line
            ("", {"ruff.toml": 'format = { quote-style = "single" }\n'}, "ruff"),
            ("", {".ruff.toml": 'format = { quote-style = "single" }\n'}, "ruff"),
            # ruff used for linting only must not override a configured black
            ("[tool.black]\n\n[tool.ruff.lint]\nselect = ['E']\n", {}, "black"),
            ("[tool.black]\n", {"ruff.toml": "[lint]\nselect = ['E']\n"}, "black"),
            (
                "[tool.black]\n\n[tool.ruff.format]\nquote-style = 'single'\n",
                {},
                "ruff",
            ),
            (
                "[tool.black]\n",
                {"ruff.toml": "[format]\nquote-style = 'single'\n"},
                "ruff",
            ),
            pytest.param(
                "[tool.black]\n",
                {".ruff.toml": "[format]\nquote-style = 'single'\n"},
                "ruff",
                id="dot-ruff-toml",
            ),
            # ruff is looked for first, however its table is written
            pytest.param(
                "[tool.black]\n",
                {"ruff.toml": 'format = { quote-style = "single" }\n'},
                "ruff",
                id="one-line-table-beside-black",
            ),
        ],
    )
    def test_auto_detects_configured_formatter(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        pyproject: str,
        files: dict[str, str],
        expected: str,
    ) -> None:
        run = tool()
        _pyproject(tmp_path, pyproject, files)
        result = FormattingCheck().run(tmp_path, {"tool": "auto"})
        assert result.status == CheckStatus.OK
        assert result.findings == []
        assert result.skip_reason is None
        assert run.call_args.args[0][0] == expected
        assert result.details == [f"Using formatter: {expected} 9.9"]


class TestFormattingCheck:
    """Tests for FormattingCheck."""

    def test_unsupported_tool(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = FormattingCheck().run(temp_package, {"tool": "unsupported"})
        assert result.status == CheckStatus.ERROR
        assert only_code(result) == "formatting.unsupported-tool"
        assert result.details == []
        run.assert_not_called()

    @pytest.mark.parametrize("name", ["ruff", "black"])
    def test_not_installed(
        self, temp_package: Path, tool: Callable[..., MagicMock], name: str
    ) -> None:
        run = tool(available=False)
        result = FormattingCheck().run(temp_package, {"tool": name})
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
        ("name", "command"),
        [
            ("ruff", ["ruff", "format", "--check", "--no-cache", ".", "--diff"]),
            ("black", ["black", "--check", ".", "--diff"]),
        ],
    )
    def test_command(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        name: str,
        command: list[str],
    ) -> None:
        run = tool()
        result = FormattingCheck().run(temp_package, {"tool": name, "args": ["--diff"]})
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0] == command
        assert run.call_args.kwargs == {"cwd": temp_package, "timeout": DEFAULT_TIMEOUT}

    @pytest.mark.parametrize(
        ("name", "output", "count", "listed"),
        [
            ("ruff", {"stdout": RUFF_OUTPUT}, 2, "Would reformat: src/demo/core.py"),
            ("ruff", {"stdout": RUFF_016_OUTPUT}, 1, "--> src/demo/__init__.py:1:1"),
            ("black", {"stderr": BLACK_STDERR}, 2, "/work/demo/src/demo/core.py"),
            (
                "ruff",
                {"stdout": "Would reformat: a.py\nWould reformat: b.py\n"},
                2,
                "b.py",
            ),
        ],
        ids=["ruff-0.14", "ruff-0.16", "black-stderr", "no-summary-line"],
    )
    def test_needs_reformatting_counts_and_lists_files(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        name: str,
        output: dict[str, str],
        count: int,
        listed: str,
    ) -> None:
        tool(SubprocessResult(returncode=1, **output))
        result = FormattingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.WARNING
        assert result.message == f"{count} file(s) need reformatting"
        # one finding for the rule, with the count, not one for each file
        assert only_code(result) == "formatting.unformatted-files"
        assert result.findings[0].message == f"{count} file(s) need reformatting"
        assert len(result.details) == 1 + count
        assert any(listed in d for d in result.details)

    def test_long_file_list_says_how_many_were_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        count = MAX_DETAIL_LINES + 2
        lines = [f"Would reformat: src/demo/m{n:02}.py" for n in range(count)]
        lines.append(f"{count} files would be reformatted")
        tool(SubprocessResult(returncode=1, stdout="\n".join(lines)))
        result = FormattingCheck().run(temp_package, {"tool": "ruff"})
        assert result.message == f"{count} file(s) need reformatting"
        assert result.details[-1] == "... and 2 more files"

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
                    returncode=2, stderr="error: Failed to parse src/demo/bad.py:1:7\n"
                ),
                [
                    "ruff exited with code 2",
                    "error: Failed to parse src/demo/bad.py:1:7",
                ],
            ),
            (
                "black",
                SubprocessResult(returncode=123, stderr=BLACK_CRASH_STDERR),
                [
                    "black exited with code 123",
                    *(line for line in BLACK_CRASH_STDERR.splitlines() if line),
                ],
            ),
            # black exits with 1 on an unhandled exception, with a traceback
            (
                "black",
                SubprocessResult(returncode=1, stdout="Oh no! Something went wrong\n"),
                ["black exited with code 1", "Oh no! Something went wrong"],
            ),
        ],
        ids=["not-started", "ruff-parse-error", "black-crash", "black-exception"],
    )
    def test_could_not_run_is_not_a_formatting_finding(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        name: str,
        outcome: SubprocessResult,
        reason: list[str],
    ) -> None:
        tool(outcome)
        result = FormattingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.WARNING
        assert result.message == f"{name} could not run"
        assert only_code(result) == "formatting.could-not-run"
        assert result.details == [f"Using formatter: {name} 9.9", *reason]

    def test_timeout(self, temp_package: Path, tool: Callable[..., MagicMock]) -> None:
        """A timed-out formatter returns ERROR and names the configured limit."""
        run = tool(SubprocessResult(timed_out=True))
        result = FormattingCheck().run(temp_package, {"tool": "ruff", "timeout": 7})
        assert run.call_args.kwargs["timeout"] == 7
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()
        assert only_code(result) == "formatting.timed-out"
        assert "7 seconds" in result.findings[0].message
        assert any("7 seconds" in d for d in result.details)

    def test_invalid_timeout_uses_the_default(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = FormattingCheck().run(temp_package, {"tool": "ruff", "timeout": "1m"})
        assert run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT
        assert any("Ignored timeout" in d for d in result.details)

    def test_invalid_args_option(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = FormattingCheck().run(temp_package, {"tool": "ruff", "args": [1]})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "formatting.invalid-args"
        assert any("list of strings" in d for d in result.details)
        run.assert_not_called()

    def test_exit_one_without_output_is_a_formatting_finding(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """A quiet run (args = ["-q"]) reports through the exit code alone."""
        tool(SubprocessResult(returncode=1))
        result = FormattingCheck().run(temp_package, {"tool": "black"})
        assert result.status == CheckStatus.WARNING
        assert result.message == "Code formatting issues found"
        assert only_code(result) == "formatting.unformatted-files"

    def test_black_paths_are_shown_relative_to_the_project(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        path = temp_package.resolve() / "src" / "mypackage" / "__init__.py"
        stderr = f"would reformat {path}\n\n1 file would be reformatted.\n"
        tool(SubprocessResult(returncode=1, stderr=stderr))
        result = FormattingCheck().run(temp_package, {"tool": "black"})
        assert result.message == "1 file(s) need reformatting"
        # The rest of the line is as black wrote it, backslashes on Windows.
        shown = Path("src", "mypackage", "__init__.py")
        assert result.details[1:] == [f"would reformat {shown}"]


class TestHints:
    """Doing what a hint says makes its rule stop firing.

    Only the hints about options are here, each written to pyproject.toml the
    way the hint spells it. Following the others changes what the formatter
    reports, and these tests do not start it.
    """

    def test_every_tool_the_hint_names_is_accepted(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        result = run_with_table(tmp_path, 'tool = "nosuchformatter"')
        assert only_code(result) == "formatting.unsupported-tool"
        hint = FormattingCheck.hints["formatting.unsupported-tool"]
        named = re.findall(r'"(\w+)"', hint)
        assert named == ["auto", *FormattingCheck.SUPPORTED_TOOLS]
        for name in named:
            assert run_with_table(tmp_path, f'tool = "{name}"').findings == []

    def test_args_written_as_the_hint_shows_are_accepted(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        hint = FormattingCheck.hints["formatting.invalid-args"]
        wrong, right = re.search(r'so (".+") becomes (\[.+\])\.$', hint).groups()
        result = run_with_table(tmp_path, f'tool = "ruff"\nargs = {wrong}')
        assert only_code(result) == "formatting.invalid-args"
        result = run_with_table(tmp_path, f'tool = "ruff"\nargs = {right}')
        assert result.findings == []

    def test_longer_timeout_lets_a_slow_formatter_finish(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The formatter is a stub that needs 400 seconds, more than the default."""
        run = tool()
        run.side_effect = lambda cmd, **kw: SubprocessResult(
            returncode=0, timed_out=kw["timeout"] < 400
        )
        result = run_with_table(tmp_path, 'tool = "ruff"')
        assert only_code(result) == "formatting.timed-out"
        # run_with_table writes the option to the table that the hint names.
        assert FormattingCheck.hints["formatting.timed-out"].startswith(
            "Set a longer timeout in [tool.pycmdcheck.checks.formatting]"
        )
        result = run_with_table(tmp_path, 'tool = "ruff"\ntimeout = 900')
        assert result.findings == []
