"""Type checking with configurable backends.

This module provides the TypingCheck class which runs static type
checking tools (mypy or pyright) on the package and reports any errors.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.fileio import is_regular_file, plain_text, read_ini
from pycmdcheck.package_layout import PackageLayout
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

# pyright has no --strict flag and exits with code 4 when it is given one.
PYRIGHT_STRICT_NOTE = (
    "The strict option is not passed to pyright, which has no --strict flag. "
    'Set typeCheckingMode = "strict" in [tool.pyright] or pyrightconfig.json'
)


def _cut(lines: list[str], what: str) -> list[str]:
    """Return the first MAX_DETAIL_LINES lines and say how many were left out."""
    if len(lines) <= MAX_DETAIL_LINES:
        return lines
    more = len(lines) - MAX_DETAIL_LINES
    return [*lines[:MAX_DETAIL_LINES], f"... and {more} more {what}"]


class TypingCheck(BaseCheck):
    """Run type checking tools on the package.

    Executes a static type checker to verify type annotations. Supports
    mypy and pyright, and by default runs the one the project has
    configured. A project that has configured neither is skipped.

    Attributes:
        name: The check identifier ("typing").
        description: Human-readable description of this check.
        rules: The codes of the findings this check can report.
        hints: What to do about each code.
        SUPPORTED_TOOLS: List of supported type checking tools.

    Configuration Options:
        tool (str): Type checker to use ("auto", "mypy" or "pyright").
            Defaults to "auto", the type checker the project has configured.
        strict (bool): Whether to pass ``--strict`` to mypy. Defaults to
            False. pyright reads strict mode from its own configuration.
        args (list[str]): Additional arguments to pass to the type checker.
        timeout (float): Seconds the type checker may run. Defaults to 300.

    Examples:
        Run the type checker the project has configured:

        >>> from pathlib import Path
        >>> check = TypingCheck()
        >>> result = check.run(Path("."), {"enabled": True})  # doctest: +SKIP
        >>> result.name  # doctest: +SKIP
        'typing'

        Run pyright, whatever the project has configured:

        >>> config = {"enabled": True, "tool": "pyright"}
        >>> result = check.run(Path("."), config)  # doctest: +SKIP

        Run mypy in strict mode:

        >>> config = {"enabled": True, "tool": "mypy", "strict": True}
        >>> result = check.run(Path("."), config)  # doctest: +SKIP
    """

    name = "typing"
    description = "Run type checking (mypy/pyright)"
    rules = {
        "typing.unsupported-tool": "The tool option names an unsupported type checker",
        "typing.invalid-args": "The args option is not a list of strings",
        "typing.timed-out": "The type checker ran longer than the timeout",
        "typing.could-not-run": "The type checker is installed but could not run",
        "typing.errors": "The type checker reported errors",
    }
    hints = {
        "typing.unsupported-tool": (
            'Set tool in [tool.pycmdcheck.checks.typing] to "auto", "mypy" or '
            '"pyright".'
        ),
        "typing.invalid-args": (
            "Write args as a list with one string for each argument, so "
            '"--python-version 3.11" becomes ["--python-version", "3.11"].'
        ),
        "typing.timed-out": (
            "Set a longer timeout in [tool.pycmdcheck.checks.typing], or in the "
            "--config file when the run uses one."
        ),
        "typing.could-not-run": (
            "Run mypy or pyright, whichever the check used, with the same args in "
            "the project directory and fix the error it prints."
        ),
        "typing.errors": (
            "Fix the code or its annotations at each file and line that mypy or "
            "pyright reports."
        ),
    }

    SUPPORTED_TOOLS = ["mypy", "pyright"]

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run type checking on the package.

        Executes the type checker and collects any errors found. With
        ``tool = "auto"`` that is the type checker the project has
        configured, and a project that has configured none is skipped.
        Errors are reported in the result details (the first
        ``MAX_DETAIL_LINES`` of them). A pyright run that ends with exit
        code 1 lists its warnings there too, and the message counts them
        apart. When the check can read no line of the output, the details
        hold the end of it.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary with options:

                - tool (str): "auto", "mypy" or "pyright" (default: "auto")
                - strict (bool): Pass --strict to mypy (default: False)
                - args (list[str]): Additional arguments for the checker
                - timeout (float): Time limit in seconds (default: 300)

        Returns:
            A CheckResult with:

            - OK status if no type errors found
            - WARNING status if the type checker is installed but could
              not run
            - ERROR status if type errors found, the type checker times out,
              or an unsupported tool or an invalid option is specified
            - SKIPPED status if the project has configured no type checker,
              the type checker is not installed, or pyproject.toml could not
              be read and no other file configures a type checker, which the
              metadata check reports ("prerequisite-failed")
        """
        tool = config.get("tool", "auto")

        if tool == "auto":
            detected = self._detect_checker(package_path)
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
                    "No type checker configured",
                    "not-configured",
                    [
                        "Looked for [tool.mypy] or [tool.pyright] in pyproject.toml, "
                        "[mypy] in setup.cfg, and the files mypy.ini, .mypy.ini and "
                        "pyrightconfig.json",
                        'To type check anyway, set tool = "mypy" (or "pyright") in '
                        "[tool.pycmdcheck.checks.typing], or in the --config file "
                        "when the run uses one",
                    ],
                )
            tool = detected

        if tool not in self.SUPPORTED_TOOLS:
            message = f"Unsupported type checker: {plain_text(str(tool))}"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message=message,
                findings=[
                    Finding("typing.unsupported-tool", message, CheckStatus.ERROR)
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
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Invalid args option",
                details=[str(exc)],
                findings=[Finding("typing.invalid-args", str(exc), CheckStatus.ERROR)],
            )
        timeout, note = resolve_timeout(config, LONG_TIMEOUT, check=self.name)

        # The first pyright run may have to download its runtime, so the
        # version probe gets the check's own time limit.
        preamble = [f"Using type checker: {describe_tool(tool, timeout=timeout)}"]
        if note:
            preamble.append(note)

        strict = config.get("strict", False)
        if tool == "mypy":
            cmd = ["mypy", "--namespace-packages", *self._mypy_targets(package_path)]
            if strict:
                cmd.append("--strict")
        else:
            cmd = ["pyright"]
            if strict:
                preamble.append(PYRIGHT_STRICT_NOTE)
        cmd.extend(args)

        result = run_tool(cmd, cwd=package_path, timeout=timeout)

        if result.timed_out:
            detail = f"Type checking took longer than {timeout} seconds"
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Type checking timed out",
                details=[detail],
                findings=[Finding("typing.timed-out", detail, CheckStatus.ERROR)],
            )

        # Both tools exit with 0 when clean and with 1 for type errors. Any
        # other code is a usage, configuration or internal error (2 for mypy,
        # 2 to 4 for pyright). Exit code 1 with nothing on stdout and text on
        # stderr is a crash.
        code = result.returncode
        crashed = not result.stdout.strip() and bool(result.stderr.strip())
        if result.error is not None or code not in (0, 1) or (code == 1 and crashed):
            return could_not_run(self.name, tool, result, preamble)

        if tool == "mypy":
            return self._mypy_result(result, preamble)
        return self._pyright_result(result, preamble)

    @staticmethod
    def _detect_checker(package_path: Path) -> str | None:
        """Return the type checker the project has configured, or ``None``.

        mypy is looked for first (``[tool.mypy]``, ``mypy.ini``,
        ``.mypy.ini``, a ``[mypy]`` section in ``setup.cfg``), then pyright
        (``[tool.pyright]``, ``pyrightconfig.json``).
        """
        tables = get_table(package_path, "tool")

        def has_table(name: str) -> bool:
            return isinstance(tables.get(name), dict)

        def has_file(*names: str) -> bool:
            return any(is_regular_file(package_path / name) for name in names)

        setup_cfg = read_ini(package_path / "setup.cfg")
        if (
            has_table("mypy")
            or has_file("mypy.ini", ".mypy.ini")
            or (setup_cfg is not None and setup_cfg.has_section("mypy"))
        ):
            return "mypy"
        if has_table("pyright") or has_file("pyrightconfig.json"):
            return "pyright"
        return None

    @staticmethod
    def _mypy_targets(package_path: Path) -> list[str]:
        """Return the paths mypy is pointed at."""
        # Target the discovered package(s)/module(s) rather than ".", which on
        # flat/single-module layouts makes mypy scan tests/build/etc. and emit
        # spurious "Duplicate module" discovery failures. mypy auto-detects the
        # base directory by walking up past __init__.py, so module names stay
        # correct (we deliberately do NOT pass --explicit-package-bases, which
        # would mis-root packages relative to the cwd).
        layout = PackageLayout(package_path)
        targets = [str(p) for p in layout.package_dirs]
        targets += [str(m) for m in layout.top_level_modules()]
        if not targets:
            src_dir = package_path / "src"
            targets = [str(src_dir) if src_dir.is_dir() else "."]
        return targets

    def _mypy_result(
        self, result: SubprocessResult, preamble: list[str]
    ) -> CheckResult:
        """Turn a finished mypy run into a result."""
        stdout = result.stdout.strip()

        # Exit code 0 means no errors, also when mypy prints notes and no
        # "Success" line (error_summary = false).
        if result.returncode == 0:
            notes = [line.strip() for line in stdout.split("\n") if "note:" in line]
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No type errors found",
                details=[*preamble, *_cut(notes, "notes")],
            )

        # Parse mypy output for errors
        errors = []
        for line in stdout.split("\n"):
            if ": error:" in line:
                errors.append(line.strip())

        if not errors and "error:" in stdout:
            errors = [line.strip() for line in stdout.split("\n") if line.strip()]

        return self._errors_result(errors, result, preamble)

    def _pyright_result(
        self, result: SubprocessResult, preamble: list[str]
    ) -> CheckResult:
        """Turn a finished pyright run into a result."""
        if result.returncode == 0:
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="No type errors found",
                details=list(preamble),
            )

        # Parse pyright output
        lines = []
        for line in result.stdout.split("\n"):
            if " - error:" in line or " - warning:" in line:
                lines.append(line.strip())
        # A line without the error marker is a warning.
        warnings = sum(" - error:" not in line for line in lines)

        return self._errors_result(lines, result, preamble, warnings)

    def _errors_result(
        self,
        lines: list[str],
        result: SubprocessResult,
        preamble: list[str],
        warnings: int = 0,
    ) -> CheckResult:
        """Return the ERROR for type errors, with one finding that counts them.

        *lines* are the errors the tool listed, and *warnings* says how many
        of them are pyright warnings.
        """
        errors = len(lines) - warnings
        message = f"Found {errors if lines else 'some'} type error(s)"
        what = "errors"
        if warnings:
            message += f" and {warnings} warning(s)"
            what = "errors and warnings"
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message=message,
            # With no line the check can read, show the end of standard
            # output instead, which is where both tools list their errors.
            details=[
                *preamble,
                *_cut(lines or result.output_lines()[-MAX_DETAIL_LINES:], what),
            ],
            findings=[Finding("typing.errors", message, CheckStatus.ERROR)],
        )
