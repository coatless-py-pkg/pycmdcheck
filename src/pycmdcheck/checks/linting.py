"""Linting check with configurable backends.

This module provides the LintingCheck class which runs code linting
tools (ruff, flake8, or pylint) on the package and reports any issues.
"""

import json
import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.fileio import display_path, is_regular_file, plain_text, read_ini
from pycmdcheck.pyproject_reader import get_table, pyproject_status
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.subprocess_runner import (
    SubprocessResult,
    ToolArgsError,
    could_not_run,
    describe_tool,
    missing_tool_hint,
    resolve_timeout,
    run_tool,
    sanitize_args,
    tool_available,
)

# ruff's own count at the end of its text output: "Found 2 errors."
_RUFF_TRAILER_RE = re.compile(r"^Found (\d+) errors?\b")

# A pylint message: "src/pkg/mod.py:3:0: C0114: Missing module docstring ..."
_PYLINT_MESSAGE_RE = re.compile(r"^.+:\d+:\d+: [A-Z]\d{4}: ")


def _writes_or_waits(arg: str) -> bool:
    """Return whether a ruff argument writes a file or never finishes.

    The fix options are not listed. They are switched off by the flags that
    the check puts last on the command line.
    """
    if arg.split("=", 1)[0] in ("--add-noqa", "--output-file", "--watch"):
        return True
    return arg.startswith(("-o", "-w")) and not arg.startswith("--")


class LintingCheck(BaseCheck):
    """Run linting tools on the package.

    Executes a linting tool to check code quality and style. Supports
    ruff, flake8, and pylint, and by default runs the one the project has
    configured. A project that has configured none is skipped.

    Attributes:
        name: The check identifier ("linting").
        description: Human-readable description of this check.
        rules: The codes of the findings this check can report.
        hints: What to do about each code.
        SUPPORTED_TOOLS: List of supported linting tools.

    Configuration Options:
        tool (str): Linter to use ("auto", "ruff", "flake8", or "pylint").
            Defaults to "auto", the linter the project has configured.
        args (list[str]): Additional arguments to pass to the linter.
        timeout (float): Seconds the linter may run. Defaults to 120
            (300 for pylint).

    Examples:
        Run the linter the project has configured:

        >>> from pathlib import Path
        >>> check = LintingCheck()
        >>> result = check.run(Path("."), {"enabled": True})  # doctest: +SKIP
        >>> result.name  # doctest: +SKIP
        'linting'

        Run flake8, whatever the project has configured:

        >>> config = {"enabled": True, "tool": "flake8"}
        >>> result = check.run(Path("."), config)  # doctest: +SKIP

        Pass additional ruff arguments:

        >>> config = {"enabled": True, "tool": "ruff", "args": ["--select", "E,W"]}
        >>> result = check.run(Path("."), config)  # doctest: +SKIP
    """

    name = "linting"
    description = "Run code linting (ruff/flake8/pylint)"
    rules = {
        "linting.unsupported-tool": "The tool option names an unsupported linter",
        "linting.invalid-args": (
            "The args option is not a list of strings, or makes ruff write "
            "files or keep running"
        ),
        "linting.timed-out": "The linter ran longer than the timeout",
        "linting.could-not-run": "The linter is installed but could not run",
        "linting.issues": "The linter reported issues",
    }
    hints = {
        "linting.unsupported-tool": (
            'Set tool in [tool.pycmdcheck.checks.linting] to "auto", "ruff", '
            '"flake8" or "pylint".'
        ),
        "linting.invalid-args": (
            "Write args as a list with one string for each argument, and for ruff "
            "leave out the options that write files or keep running."
        ),
        "linting.timed-out": (
            "Set a longer timeout in [tool.pycmdcheck.checks.linting], or in the "
            "--config file when the run uses one."
        ),
        "linting.could-not-run": (
            "Run ruff check, flake8 or pylint, whichever the check used, with the "
            "same args in the project directory and fix the error it prints."
        ),
        "linting.issues": (
            "Fix what the linter lists, or switch off a rule in the linter's "
            "configuration."
        ),
    }

    SUPPORTED_TOOLS = ["ruff", "flake8", "pylint"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run linting check on the package.

        Executes the linting tool and collects any issues found. With
        ``tool = "auto"`` that is the linter the project has configured, and
        a project that has configured none is skipped. Issues are reported
        in the result details (the first ``MAX_DETAIL_LINES`` of them). The
        linter never modifies files.

        Args:
            package_path: Path to the package directory to lint.
            config: Configuration dictionary with options:

                - tool (str): "auto", "ruff", "flake8", or "pylint"
                  (default: "auto")
                - args (list[str]): Additional arguments for the linter
                - timeout (float): Time limit in seconds

        Returns:
            A CheckResult with:

            - OK status if no linting issues found
            - WARNING status if linting issues found, or if the linter is
              installed but could not run
            - ERROR status if the linter times out or an unsupported tool or
              an invalid option is specified
            - SKIPPED status if the project has configured no linter, the
              linting tool is not installed, or pyproject.toml could not be
              read and no other file configures a linter, which the metadata
              check reports ("prerequisite-failed")
        """
        tool = config.get("tool", "auto")

        if tool == "auto":
            detected = self._detect_linter(package_path)
            if detected is None and pyproject_status(package_path) == "unparseable":
                # The metadata check is the one that reports the broken file.
                return CheckResult.skip(
                    self.name,
                    "pyproject.toml could not be read",
                    "prerequisite-failed",
                    ["The metadata check reports the reason"],
                )
            if detected is None:
                return CheckResult.skip(
                    self.name,
                    "No linter configured",
                    "not-configured",
                    [
                        "Looked for [tool.ruff] or [tool.pylint] in pyproject.toml, "
                        "[flake8] in setup.cfg or tox.ini, and the files ruff.toml, "
                        ".ruff.toml, .flake8, .pylintrc and pylintrc",
                        'To lint anyway, set tool = "ruff" (or "flake8" or "pylint") '
                        "in [tool.pycmdcheck.checks.linting], or in the --config "
                        "file when the run uses one",
                    ],
                )
            tool = detected

        if tool not in self.SUPPORTED_TOOLS:
            message = f"Unsupported linting tool: {plain_text(str(tool))}"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=message,
                findings=[
                    Finding("linting.unsupported-tool", message, CheckStatus.ERROR)
                ],
            )

        if not tool_available(tool):
            return CheckResult.skip(
                self.name,
                f"{tool} not installed",
                "tool-missing",
                [missing_tool_hint(tool)],
            )

        try:
            args = sanitize_args(config.get("args"), check=self.name)
        except ToolArgsError as exc:
            return self._invalid_args(
                str(exc),
                "Write args as a list with one string for each argument, so "
                '"--select E" becomes ["--select", "E"].',
            )
        default = LONG_TIMEOUT if tool == "pylint" else DEFAULT_TIMEOUT
        timeout, note = resolve_timeout(config, default, check=self.name)
        preamble = [f"Using linter: {describe_tool(tool)}"]
        if note:
            preamble.append(note)

        as_json = False
        if tool == "ruff":
            denied = sorted(
                {arg.split("=", 1)[0] for arg in args if _writes_or_waits(arg)}
            )
            if denied:
                named = plain_text(", ".join(denied))
                return self._invalid_args(
                    f"The args option of the linting check holds {named}, which "
                    "makes ruff write files or keep running. The linting check "
                    "only reads.",
                    f"Remove {named} from the args option of the linting check.",
                )
            # --no-cache leaves no .ruff_cache behind.
            cmd = ["ruff", "check", ".", "--no-cache"]
            # ruff refuses a repeated --output-format, and --statistics changes
            # the shape of the JSON, so a format the user chose is kept.
            as_json = not any(
                arg.startswith(("--output-format", "--statistics")) for arg in args
            )
            if as_json:
                cmd.append("--output-format=json")
        elif tool == "flake8":
            cmd = ["flake8", "."]
        else:
            src_dir = package_path / "src"
            target = str(src_dir) if src_dir.is_dir() else "."
            # --score=n drops the "Your code has been rated" line.
            cmd = ["pylint", target, "--output-format=text", "--score=n"]
        cmd.extend(args)
        if tool == "ruff":
            # The files being checked must never be rewritten. These go last
            # so that they win over "fix = true" or "fix-only = true" in the
            # project's configuration and over a --fix among its own args.
            cmd.extend(["--no-fix", "--no-fix-only"])

        result = run_tool(cmd, cwd=package_path, timeout=timeout)

        if result.timed_out:
            detail = f"Linting took longer than {timeout} seconds"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Linting timed out",
                details=[detail],
                findings=[Finding("linting.timed-out", detail, CheckStatus.ERROR)],
            )

        if result.error is not None:
            return could_not_run(self.name, tool, result, preamble)

        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No linting issues found",
                details=list(preamble),
            )

        if tool == "pylint":
            lines = result.output_lines()
            issues = [line for line in lines if _PYLINT_MESSAGE_RE.match(line)]
            issue_count = len(issues)
            # Another message template: show the end of the output instead.
            issues = issues or result.tail()
            # The exit code is a set of flags, one for each kind of message
            # (1 to 31). 32 is a usage error.
            found_issues = 0 < result.returncode < 32
            silent = not lines
        else:
            issues, issue_count = self._issues(result, package_path, as_json)
            # ruff and flake8 exit with 1 for findings and with 2 for a
            # configuration, usage or internal error.
            found_issues = result.returncode == 1
            silent = issue_count == 0

        # A linter that failed is not a code-quality finding. Neither is an
        # exit that reports nothing and writes to stderr, which is a crash.
        if not found_issues or (silent and result.stderr.strip()):
            return could_not_run(self.name, tool, result, preamble)

        details = [*preamble, *issues[:MAX_DETAIL_LINES]]
        if len(issues) > MAX_DETAIL_LINES:
            more = len(issues) - MAX_DETAIL_LINES
            what = "issues" if len(issues) == issue_count else "lines of output"
            details.append(f"... and {more} more {what}")

        message = f"Found {issue_count} linting issue(s)"
        if not issue_count:
            code = result.returncode
            message = f"{tool} exited with code {code} without listing issues"
        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=message,
            details=details,
            findings=[Finding("linting.issues", message, CheckStatus.WARNING)],
        )

    def _invalid_args(self, reason: str, hint: str) -> CheckResult:
        """Return the ERROR for an ``args`` option the check cannot use.

        Each of the two causes has a *hint* of its own.
        """
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message="Invalid args option",
            details=[reason],
            findings=[Finding("linting.invalid-args", reason, CheckStatus.ERROR, hint)],
        )

    @staticmethod
    def _issues(
        result: SubprocessResult, package_path: Path, as_json: bool
    ) -> tuple[list[str], int]:
        """Return the detail lines and the number of findings of ruff or flake8.

        ruff's JSON output gives one entry per finding. Its text formats
        spread a finding over several lines, so there the count comes from
        the "Found N errors." line. Without either (flake8, old one-line
        formats) each line is one finding.
        """
        if as_json:
            try:
                entries = json.loads(result.stdout)
            except ValueError:
                entries = None
            if isinstance(entries, list):
                root = package_path.resolve()
                found = [e for e in entries if isinstance(e, dict)]
                return [_ruff_line(e, root) for e in found], len(found)

        lines = result.output_lines()
        for line in reversed(lines):
            trailer = _RUFF_TRAILER_RE.match(line)
            if trailer is not None:
                return lines, int(trailer.group(1))
        return lines, len(lines)

    @staticmethod
    def _detect_linter(package_path: Path) -> str | None:
        """Return the linter the project has configured, or ``None``.

        ruff is looked for first (``[tool.ruff]``, ``ruff.toml``,
        ``.ruff.toml``), then flake8 (``.flake8``, a ``[flake8]`` section in
        ``setup.cfg`` or ``tox.ini``), then pylint (``.pylintrc``,
        ``pylintrc``, ``[tool.pylint]``).
        """
        tables = get_table(package_path, "tool")

        def has_table(name: str) -> bool:
            return isinstance(tables.get(name), dict)

        def has_file(*names: str) -> bool:
            return any(is_regular_file(package_path / name) for name in names)

        def has_section(section: str, *names: str) -> bool:
            parsers = (read_ini(package_path / name) for name in names)
            return any(p is not None and p.has_section(section) for p in parsers)

        if has_table("ruff") or has_file("ruff.toml", ".ruff.toml"):
            return "ruff"
        if has_file(".flake8") or has_section("flake8", "setup.cfg", "tox.ini"):
            return "flake8"
        if has_table("pylint") or has_file(".pylintrc", "pylintrc"):
            return "pylint"
        return None


def _ruff_line(entry: dict[str, Any], root: Path) -> str:
    """Render one entry of ruff's JSON output as ``path:row:col CODE message``."""
    location = entry.get("location")
    if not isinstance(location, dict):
        location = {}
    path = display_path(Path(str(entry.get("filename", "?"))), root)
    row, column = location.get("row", "?"), location.get("column", "?")
    code = entry.get("code") or "error"
    return f"{path}:{row}:{column} {code} {plain_text(str(entry.get('message', '')))}"
