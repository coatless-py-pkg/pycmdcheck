"""Tests for the typing check."""

import re
import sys
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pycmdcheck.checks.typing import TypingCheck
from pycmdcheck.config import get_check_config, load_config
from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.subprocess_runner import SubprocessResult
from tests.helpers import only_code, write_files

# The tool fixture in conftest.py reads this to know which module to patch.
MODULE = "pycmdcheck.checks.typing"

# "mypy --namespace-packages src/demo" on stdout, mypy 1.19.1: one error, one
# note and the closing line.
MYPY_OUTPUT = (
    'src/demo/__init__.py:2: error: Incompatible return value type (got "str", '
    'expected "int")  [return-value]\n'
    "src/demo/__init__.py:6: note: By default the bodies of untyped functions are "
    "not checked, consider using --check-untyped-defs  [annotation-unchecked]\n"
    "Found 1 error in 1 file (checked 1 source file)\n"
)

# "mypy --output json" prints one JSON object for each error.
MYPY_JSON_OUTPUT = (
    '{"file": "src/demo/__init__.py", "line": 2, "column": 11, "message": '
    '"Incompatible return value type", "hint": null, "code": "return-value", '
    '"severity": "error"}\n'
)

# "pyright --outputjson" prints one JSON document. This one is shortened to
# one error without its position.
PYRIGHT_JSON_OUTPUT = """\
{
    "version": "9.9",
    "generalDiagnostics": [
        {
            "file": "/p/a.py",
            "severity": "error",
            "message": "Type mismatch",
            "rule": "reportReturnType"
        }
    ],
    "summary": {
        "filesAnalyzed": 1,
        "errorCount": 1,
        "warningCount": 0,
        "informationCount": 0,
        "timeInSec": 0.25
    }
}
"""

# Lines of pyright's text output, without the two spaces each one starts with.
PYRIGHT_ERROR = "/p/a.py:4:12 - error: Type mismatch (reportReturnType)"
PYRIGHT_WARNINGS = [
    '/p/a.py:1:8 - warning: Import "yaml" could not be resolved from source',
    '/p/b.py:1:8 - warning: Import "attr" could not be resolved from source',
]


def run_with_table(root: Path, options: str) -> CheckResult:
    """Run the check with *options* as its table in the project's pyproject.toml."""
    table = f"[tool.pycmdcheck.checks.typing]\n{options}\n"
    write_files(root, {"pyproject.toml": table})
    return TypingCheck().run(root, get_check_config(load_config(root), "typing"))


class TestDetection:
    """Which type checker tool = "auto" picks, and what happens without one."""

    @pytest.mark.parametrize(
        ("files", "expected"),
        [
            ({"pyproject.toml": "[tool.mypy]\nstrict = true\n"}, "mypy"),
            ({"mypy.ini": "[mypy]\nstrict = True\n"}, "mypy"),
            ({".mypy.ini": "[mypy]\nstrict = True\n"}, "mypy"),
            ({"setup.cfg": "[mypy]\nstrict = True\n"}, "mypy"),
            ({"pyproject.toml": "[tool.pyright]\nvenv = 'x'\n"}, "pyright"),
            ({"pyrightconfig.json": "{}\n"}, "pyright"),
            # mypy is looked for first, then pyright
            ({"mypy.ini": "[mypy]\n", "pyrightconfig.json": "{}\n"}, "mypy"),
        ],
    )
    def test_auto_runs_the_configured_type_checker(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        files: dict[str, str],
        expected: str,
    ) -> None:
        run = tool()
        result = TypingCheck().run(write_files(tmp_path, files), {"tool": "auto"})
        assert result.status == CheckStatus.OK
        assert result.findings == []
        assert result.skip_reason is None
        assert run.call_args.args[0][0] == expected
        assert result.details == [f"Using type checker: {expected} 9.9"]

    @pytest.mark.parametrize(
        "files",
        [
            {},
            {"pyproject.toml": "[tool.ruff]\n", "setup.cfg": "[metadata]\n"},
            {"setup.cfg": "not an ini file\n"},
        ],
        ids=["empty", "other-tools", "unreadable-ini"],
    )
    def test_no_type_checker_configured_is_skipped(
        self, tmp_path: Path, tool: Callable[..., MagicMock], files: dict[str, str]
    ) -> None:
        run = tool()
        result = TypingCheck().run(write_files(tmp_path, files), {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "not-configured"
        assert result.findings == []
        assert result.message == "No type checker configured"
        assert any(d.startswith("Looked for [tool.mypy]") for d in result.details)
        assert any(
            'tool = "mypy"' in d and "[tool.pycmdcheck.checks.typing]" in d
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
        files = {"pyproject.toml": "[tool\n", "setup.cfg": "not an ini file\n"}
        result = TypingCheck().run(write_files(tmp_path, files), {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "prerequisite-failed"
        assert result.findings == []
        assert result.message == "pyproject.toml could not be read"
        assert result.details == ["The metadata check reports the reason"]
        run.assert_not_called()

    @pytest.mark.parametrize(
        ("files", "config", "expected"),
        [
            ({"mypy.ini": "[mypy]\n"}, {}, "mypy"),
            ({}, {"tool": "pyright"}, "pyright"),
        ],
        ids=["other-file", "explicit-tool"],
    )
    def test_unreadable_pyproject_does_not_stop_a_checker_named_elsewhere(
        self,
        tmp_path: Path,
        tool: Callable[..., MagicMock],
        files: dict[str, str],
        config: dict[str, str],
        expected: str,
    ) -> None:
        run = tool()
        root = write_files(tmp_path, {"pyproject.toml": "[tool\n", **files})
        result = TypingCheck().run(root, config)
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0][0] == expected

    def test_explicit_tool_overrides_detection(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        root = write_files(tmp_path, {"mypy.ini": "[mypy]\n"})
        result = TypingCheck().run(root, {"tool": "pyright"})
        assert result.status == CheckStatus.OK
        assert run.call_args.args[0][0] == "pyright"


class TestCouldNotRun:
    """A type checker that is installed but could not do its job is a WARNING."""

    @pytest.mark.parametrize(
        ("name", "outcome", "reason"),
        [
            (
                "mypy",
                SubprocessResult(error="Could not start mypy: denied"),
                ["Could not start mypy: denied"],
            ),
            (
                "mypy",
                SubprocessResult(
                    returncode=2,
                    stdout="src/foo.py: error: Duplicate module named 'foo'\n",
                ),
                [
                    "mypy exited with code 2",
                    "src/foo.py: error: Duplicate module named 'foo'",
                ],
            ),
            (
                "mypy",
                SubprocessResult(returncode=1, stderr="ImportError: bad plugin\n"),
                ["mypy exited with code 1", "ImportError: bad plugin"],
            ),
            ("mypy", SubprocessResult(returncode=-11), ["mypy exited with code -11"]),
            (
                "pyright",
                SubprocessResult(returncode=4, stdout="Unexpected option --bogus.\n"),
                ["pyright exited with code 4", "Unexpected option --bogus."],
            ),
            (
                "pyright",
                SubprocessResult(returncode=1, stderr="Error: node not found\n"),
                ["pyright exited with code 1", "Error: node not found"],
            ),
        ],
        ids=[
            "not-started",
            "mypy-configuration",
            "mypy-crash",
            "mypy-killed",
            "pyright-usage",
            "pyright-crash",
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
        result = TypingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.WARNING
        assert result.message == f"{name} could not run"
        assert only_code(result) == "typing.could-not-run"
        assert result.details == [f"Using type checker: {name} 9.9", *reason]


class TestOptions:
    """Options and tool handling shared by both type checkers."""

    def test_unsupported_tool(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = TypingCheck().run(temp_package, {"tool": "nosuchchecker"})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Unsupported type checker: nosuchchecker"
        assert only_code(result) == "typing.unsupported-tool"
        assert result.details == []
        run.assert_not_called()

    def test_not_installed(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool(available=False)
        result = TypingCheck().run(temp_package, {"tool": "pyright"})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "tool-missing"
        assert result.findings == []
        assert result.message == "pyright not installed"
        assert any("pip install pyright" in d for d in result.details)
        # a type checker runs from the project's environment, so no extra is named
        assert not any("pycmdcheck[" in d for d in result.details)
        run.assert_not_called()

    @pytest.mark.parametrize("name", ["mypy", "pyright"])
    def test_version_and_timeout(
        self, temp_package: Path, tool: Callable[..., MagicMock], name: str
    ) -> None:
        run = tool()
        result = TypingCheck().run(temp_package, {"tool": name, "timeout": 7})
        assert result.status == CheckStatus.OK
        assert result.details == [f"Using type checker: {name} 9.9"]
        assert run.call_args.kwargs == {"cwd": temp_package, "timeout": 7}
        # The first pyright run may download its runtime, so the version
        # probe gets the same limit.
        describe = sys.modules[MODULE].describe_tool
        describe.assert_called_once_with(name, timeout=7)

    def test_timed_out_names_the_configured_limit(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool(SubprocessResult(timed_out=True))
        result = TypingCheck().run(temp_package, {"tool": "mypy", "timeout": 7})
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()
        assert only_code(result) == "typing.timed-out"
        assert "7 seconds" in result.findings[0].message
        assert any("7 seconds" in d for d in result.details)

    def test_invalid_timeout_uses_the_default(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = TypingCheck().run(temp_package, {"tool": "mypy", "timeout": -1})
        assert run.call_args.kwargs["timeout"] == LONG_TIMEOUT
        assert any("Ignored timeout" in d for d in result.details)

    def test_invalid_args_option(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        config = {"tool": "mypy", "args": "--strict"}
        result = TypingCheck().run(temp_package, config)
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "typing.invalid-args"
        assert any("list of strings" in d for d in result.details)
        run.assert_not_called()

    @pytest.mark.parametrize(
        ("name", "stdout"),
        [("mypy", MYPY_JSON_OUTPUT), ("pyright", PYRIGHT_JSON_OUTPUT)],
        ids=["mypy", "pyright"],
    )
    def test_output_the_check_cannot_read_shows_its_end(
        self, temp_package: Path, tool: Callable[..., MagicMock], name: str, stdout: str
    ) -> None:
        """A JSON format asked for in args has no line that names an error.

        The errors are on standard output, so its end is shown also when the
        tool wrote a warning to standard error.
        """
        stderr = "Warning: unused section(s) in mypy.ini\n"
        tool(SubprocessResult(returncode=1, stdout=stdout, stderr=stderr))
        result = TypingCheck().run(temp_package, {"tool": name})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Found some type error(s)"
        assert only_code(result) == "typing.errors"
        assert result.details == [
            f"Using type checker: {name} 9.9",
            *stdout.splitlines()[-MAX_DETAIL_LINES:],
        ]


class TestHints:
    """Doing what a hint says makes its rule stop firing.

    Only the hints about options are here, each written to pyproject.toml the
    way the hint spells it. Following the others changes what the type checker
    reports, and these tests do not start it.
    """

    def test_every_tool_the_hint_names_is_accepted(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        result = run_with_table(tmp_path, 'tool = "nosuchchecker"')
        assert only_code(result) == "typing.unsupported-tool"
        named = re.findall(r'"(\w+)"', TypingCheck.hints["typing.unsupported-tool"])
        assert named == ["auto", *TypingCheck.SUPPORTED_TOOLS]
        for name in named:
            assert run_with_table(tmp_path, f'tool = "{name}"').findings == []

    def test_args_written_as_the_hint_shows_are_accepted(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        tool()
        hint = TypingCheck.hints["typing.invalid-args"]
        wrong, right = re.search(r'so (".+") becomes (\[.+\])\.$', hint).groups()
        result = run_with_table(tmp_path, f'tool = "mypy"\nargs = {wrong}')
        assert only_code(result) == "typing.invalid-args"
        result = run_with_table(tmp_path, f'tool = "mypy"\nargs = {right}')
        assert result.findings == []

    def test_longer_timeout_lets_a_slow_type_checker_finish(
        self, tmp_path: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The type checker is a stub that needs 400 seconds, more than the default."""
        run = tool()
        run.side_effect = lambda cmd, **kw: SubprocessResult(
            returncode=0, timed_out=kw["timeout"] < 400
        )
        result = run_with_table(tmp_path, 'tool = "mypy"')
        assert only_code(result) == "typing.timed-out"
        # run_with_table writes the option to the table that the hint names.
        assert TypingCheck.hints["typing.timed-out"].startswith(
            "Set a longer timeout in [tool.pycmdcheck.checks.typing]"
        )
        result = run_with_table(tmp_path, 'tool = "mypy"\ntimeout = 900')
        assert result.findings == []


class TestMypy:
    """The mypy backend, with the tool replaced."""

    def test_exit_zero_with_notes_is_ok(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """error_summary = false leaves out the 'Success' line."""
        note = (
            "src/mypackage/a.py:2: note: By default the bodies of untyped "
            "functions are not checked  [annotation-unchecked]"
        )
        tool(SubprocessResult(returncode=0, stdout=note + "\n"))
        result = TypingCheck().run(temp_package, {"tool": "mypy"})
        assert result.status == CheckStatus.OK
        assert result.message == "No type errors found"
        assert note in result.details

    @pytest.mark.parametrize(
        ("layout", "target", "options", "flags"),
        [
            ("src", "src/mypackage", {}, []),
            ("src", "src/mypackage", {"strict": True}, ["--strict"]),
            # a project that is one module has no directory to name
            ("module", "mypackage.py", {}, []),
        ],
        ids=["default", "strict", "single-module"],
    )
    def test_command(
        self,
        make_package: Callable[..., Path],
        tool: Callable[..., MagicMock],
        layout: str,
        target: str,
        options: dict[str, bool],
        flags: list[str],
    ) -> None:
        root = make_package(layout=layout)
        run = tool()
        TypingCheck().run(root, {"tool": "mypy", "args": ["--pretty"], **options})
        assert run.call_args.args[0] == [
            "mypy",
            "--namespace-packages",
            str(root / target),
            *flags,
            "--pretty",
        ]

    def test_errors(self, temp_package: Path, tool: Callable[..., MagicMock]) -> None:
        """A note and the closing line of mypy are not errors."""
        tool(SubprocessResult(returncode=1, stdout=MYPY_OUTPUT))
        result = TypingCheck().run(temp_package, {"tool": "mypy"})
        assert result.status == CheckStatus.ERROR
        assert "1 type error" in result.message
        assert only_code(result) == "typing.errors"
        assert result.findings[0].message == "Found 1 type error(s)"
        assert any("Incompatible return value type" in d for d in result.details)

    def test_long_error_list_says_how_many_were_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        lines = [f"a.py:{n}: error: Bad" for n in range(MAX_DETAIL_LINES + 3)]
        tool(SubprocessResult(returncode=1, stdout="\n".join(lines)))
        result = TypingCheck().run(temp_package, {"tool": "mypy"})
        assert f"{MAX_DETAIL_LINES + 3} type error" in result.message
        assert result.details[-1] == "... and 3 more errors"
        # one finding for the rule, with the count, not one for each error
        assert only_code(result) == "typing.errors"
        assert result.findings[0].message == result.message


class TestPyright:
    """The pyright backend, with the tool replaced."""

    def test_strict_is_never_passed(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """pyright has no --strict flag and exits 4 when it is given one."""
        run = tool()
        config = {"tool": "pyright", "strict": True, "args": ["--warnings"]}
        result = TypingCheck().run(temp_package, config)
        assert run.call_args.args[0] == ["pyright", "--warnings"]
        assert result.status == CheckStatus.OK
        assert any(
            "typeCheckingMode" in d and "[tool.pyright]" in d for d in result.details
        )

    @pytest.mark.parametrize(
        ("lines", "summary", "message"),
        [
            (
                [PYRIGHT_ERROR],
                "1 error, 0 warnings, 0 informations",
                "Found 1 type error(s)",
            ),
            (
                [PYRIGHT_WARNINGS[0], PYRIGHT_ERROR, PYRIGHT_WARNINGS[1]],
                "1 error, 2 warnings, 0 informations",
                "Found 1 type error(s) and 2 warning(s)",
            ),
            # pyright --warnings exits with code 1 for warnings alone
            (
                PYRIGHT_WARNINGS,
                "0 errors, 2 warnings, 0 informations",
                "Found 0 type error(s) and 2 warning(s)",
            ),
        ],
        ids=["errors", "errors-and-warnings", "warnings"],
    )
    def test_errors(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        lines: list[str],
        summary: str,
        message: str,
    ) -> None:
        """Warnings are counted apart and listed where pyright printed them."""
        stdout = "".join(f"  {line}\n" for line in lines) + summary + "\n"
        tool(SubprocessResult(returncode=1, stdout=stdout))
        result = TypingCheck().run(temp_package, {"tool": "pyright"})
        assert result.status == CheckStatus.ERROR
        assert result.message == message
        assert only_code(result) == "typing.errors"
        assert result.findings[0].message == message
        assert result.details == ["Using type checker: pyright 9.9", *lines]

    def test_long_list_with_warnings_says_how_many_were_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        count = MAX_DETAIL_LINES + 2
        lines = [f"  /p/a.py:{n}:1 - warning: Bad" for n in range(count)]
        lines.append(f"  {PYRIGHT_ERROR}")
        tool(SubprocessResult(returncode=1, stdout="\n".join(lines)))
        result = TypingCheck().run(temp_package, {"tool": "pyright"})
        assert result.message == f"Found 1 type error(s) and {count} warning(s)"
        # the lines left out here are two warnings and the error
        assert result.details[-1] == "... and 3 more errors and warnings"
