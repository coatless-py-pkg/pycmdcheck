"""Code formatting check."""

import os
import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.fileio import is_regular_file, plain_text, try_read_text
from pycmdcheck.pyproject_reader import get_dict, get_table, pyproject_status
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.subprocess_runner import (
    ToolArgsError,
    could_not_run,
    describe_tool,
    missing_tool_hint,
    resolve_timeout,
    run_tool,
    sanitize_args,
    tool_available,
)

# A [format] section, a dotted format key, or the format table written on one
# line, in ruff.toml.
_RUFF_FORMAT_SECTION_RE = re.compile(
    r"^\s*(\[format\]|format\.|format\s*=\s*\{)", re.MULTILINE
)

# The closing line of ruff (stdout) and black (stderr):
# "2 files would be reformatted, 1 file already formatted"
_SUMMARY_RE = re.compile(r"(\d+) files? would be reformatted")

# A line naming one file: "Would reformat: a.py" (ruff), " --> a.py:1:1"
# (ruff 0.16 and later) or "would reformat /abs/a.py" (black).
_FILE_LINE_RE = re.compile(r"^\s*([Ww]ould reformat:? |--> )\S")


class FormattingCheck(BaseCheck):
    """Check code formatting without modifying files."""

    name = "formatting"
    description = "Check code formatting (ruff format/black)"
    rules = {
        "formatting.unsupported-tool": "The tool option names an unsupported formatter",
        "formatting.invalid-args": "The args option is not a list of strings",
        "formatting.timed-out": "The formatter ran longer than the timeout",
        "formatting.could-not-run": "The formatter is installed but could not run",
        "formatting.unformatted-files": "The formatter would change files",
    }
    hints = {
        "formatting.unsupported-tool": (
            'Set tool in [tool.pycmdcheck.checks.formatting] to "auto", "ruff" or '
            '"black".'
        ),
        "formatting.invalid-args": (
            "Write args as a list with one string for each argument, so "
            '"--line-length 100" becomes ["--line-length", "100"].'
        ),
        "formatting.timed-out": (
            "Set a longer timeout in [tool.pycmdcheck.checks.formatting], or in the "
            "--config file when the run uses one."
        ),
        "formatting.could-not-run": (
            "Run 'ruff format --check .' or 'black --check .' with the same args in "
            "the project directory and fix the error it prints."
        ),
        "formatting.unformatted-files": (
            "Run 'ruff format .' or 'black .' with the same args in the project "
            "directory, whichever formatter the check used."
        ),
    }

    SUPPORTED_TOOLS = ["ruff", "black"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run formatting check.

        The formatter defaults to ``"auto"``: the one the package has
        configured, which is ruff for a ``[tool.ruff.format]`` table or a
        ``[format]`` section in ``ruff.toml``, and black for ``[tool.black]``.
        A package that has not opted into a formatter is SKIPPED rather than
        being held to one tool's default style (pyOpenSci does not mandate a
        specific formatter). A ruff configuration without a format section is
        no opt-in. When pyproject.toml could not be read and no other file
        configures a formatter, the skip reason is "prerequisite-failed",
        because the metadata check reports that file. An explicit ``tool``
        always runs. Also supports ``args`` (list[str]) and ``timeout``
        (seconds, default 120). A formatter that is installed but could not
        run is a WARNING.
        """
        tool = config.get("tool", "auto")

        if tool == "auto":
            detected = self._detect_formatter(package_path)
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
                    "No formatter configured",
                    "not-configured",
                    [
                        "Looked for [tool.ruff.format] or [tool.black] in "
                        "pyproject.toml, and [format] in ruff.toml or .ruff.toml",
                        'To check the formatting anyway, set tool = "ruff" (or '
                        '"black") in [tool.pycmdcheck.checks.formatting], or in '
                        "the --config file when the run uses one",
                    ],
                )
            tool = detected

        if tool not in self.SUPPORTED_TOOLS:
            message = f"Unsupported formatting tool: {plain_text(str(tool))}"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=message,
                findings=[
                    Finding("formatting.unsupported-tool", message, CheckStatus.ERROR)
                ],
            )

        return self._run_formatter(tool, package_path, config)

    @staticmethod
    def _detect_formatter(package_path: Path) -> str | None:
        """Return the formatter the project has configured, or ``None``.

        ruff is looked for first (a ``[tool.ruff.format]`` table, or a
        ``[format]`` section, a dotted ``format`` key or a one-line ``format``
        table in ``ruff.toml`` or ``.ruff.toml``), then black (a
        ``[tool.black]`` table). Other ruff configuration selects nothing,
        because a project can lint with ruff without formatting with it.
        """
        ruff_files = [
            path
            for name in ("ruff.toml", ".ruff.toml")
            if is_regular_file(path := package_path / name)
        ]
        tables = get_table(package_path, "tool")
        if isinstance(get_dict(tables, "ruff").get("format"), dict) or any(
            _RUFF_FORMAT_SECTION_RE.search(try_read_text(path) or "")
            for path in ruff_files
        ):
            return "ruff"
        if isinstance(tables.get("black"), dict):
            return "black"
        return None

    def _run_formatter(
        self, tool: str, package_path: Path, config: dict[str, Any]
    ) -> CheckResult:
        """Run ``ruff format --check`` or ``black --check``."""
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
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Invalid args option",
                details=[str(exc)],
                findings=[
                    Finding("formatting.invalid-args", str(exc), CheckStatus.ERROR)
                ],
            )
        timeout, note = resolve_timeout(config, DEFAULT_TIMEOUT, check=self.name)
        details = [f"Using formatter: {describe_tool(tool)}"]
        if note:
            details.append(note)

        if tool == "ruff":
            # --no-cache leaves no .ruff_cache behind in the project.
            cmd = ["ruff", "format", "--check", "--no-cache", ".", *args]
        else:
            cmd = ["black", "--check", ".", *args]
        result = run_tool(cmd, cwd=package_path, timeout=timeout)

        if result.timed_out:
            detail = f"Formatting check took longer than {timeout} seconds"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Formatting check timed out",
                details=[detail],
                findings=[Finding("formatting.timed-out", detail, CheckStatus.ERROR)],
            )
        if result.error is not None:
            return could_not_run(self.name, tool, result, details)
        if result.success:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Code is properly formatted",
                details=details,
            )

        # ruff reports on stdout, black on stderr. black prints absolute
        # paths, which are shown relative to the project.
        root = f"{package_path.resolve()}{os.sep}"
        lines = [*result.output_lines(), *result.stderr_lines()]
        files = [
            line.strip().replace(root, "")
            for line in lines
            if _FILE_LINE_RE.match(line)
        ]
        summary = _SUMMARY_RE.search(result.output)

        # Only exit code 1 means "files would be reformatted". ruff exits with
        # 2 and black with 123 when they cannot read the configuration or
        # parse a file, and black exits with 1 on an unhandled exception.
        # Output that names no file and has no summary is not a finding.
        unexplained = not files and summary is None and bool(result.tail())
        if result.returncode != 1 or unexplained:
            return could_not_run(self.name, tool, result, details)

        # Files need reformatting. A quiet run lists none of them.
        file_count = int(summary.group(1)) if summary else len(files)
        details.extend(files[:MAX_DETAIL_LINES] or result.tail())
        if len(files) > MAX_DETAIL_LINES:
            details.append(f"... and {len(files) - MAX_DETAIL_LINES} more files")

        message = (
            f"{file_count} file(s) need reformatting"
            if file_count
            else "Code formatting issues found"
        )
        return CheckResult(
            name=self.name,
            status=CheckStatus.WARNING,
            message=message,
            details=details,
            findings=[
                Finding("formatting.unformatted-files", message, CheckStatus.WARNING)
            ],
        )
