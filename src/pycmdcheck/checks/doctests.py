"""Doctest execution check.

Runs doctests embedded in module docstrings using pytest's
``--doctest-modules`` flag.
"""

import logging
import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.checks.test_suite import pytest_ini_option
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.fileio import display_path, plain_text
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.subprocess_runner import (
    PYTEST_LOCK,
    ToolArgsError,
    could_not_run,
    describe_tool,
    missing_tool_hint,
    resolve_timeout,
    run_tool,
    sanitize_args,
    tool_available,
)

logger = logging.getLogger(__name__)

# pytest's count line, "2 skipped in 0.01s" or "1 passed, 1 skipped in 0.02s".
# It is framed with "=" signs when a -v in the args option makes pytest
# print more. The whole line has to match, so a SKIPPED or FAILED line, which
# holds the name of a file, cannot pass for it.
_COUNT_LINE_RE = re.compile(
    r"(?:=+ )?(\d+ \w+(?:, \d+ \w+)*) in [\d.]+s(?: \([\d:]+\))?(?: =+)?"
)


def _reason(lines: list[str], name: str) -> str:
    """Return why the docstring *name* failed, in a few words.

    pytest prints a block for each failed docstring under FAILURES. The block
    opens with "___ [doctest] <name> ___", and its first failure closes with
    "<file>:<line>: DocTestFailure" or "<file>:<line>: UnexpectedException".
    The result is "" when the block is missing or is not understood. It is
    "" as well when two blocks have the name, as two text files of one name
    do, because nothing says which block belongs to which FAILED line.
    """
    opening = f"[doctest] {name}"
    starts = [
        n
        for n, ln in enumerate(lines)
        if ln.startswith("_") and ln.strip("_").strip() == opening
    ]
    if len(starts) != 1:
        return ""

    want: list[str] = []
    got: list[str] = []
    diff: list[str] = []
    raised = number = ""
    into: list[str] | None = None
    for line in lines[starts[0] + 1 :]:
        if line.startswith(("_", "=")):
            break
        elif into is not None and line.startswith("    "):
            into.append(line[4:])
        elif line.startswith("UNEXPECTED EXCEPTION: "):
            raised = line.removeprefix("UNEXPECTED EXCEPTION: ")
        elif line in ("Expected:", "Expected nothing", "Got:", "Got nothing"):
            into = want if line.startswith("Expected") else got
            if line.endswith("nothing"):
                into.append("no output")
        elif line.startswith("Differences (unified"):
            into = diff
        else:
            into = None
            where, _, kind = line.rpartition(": ")
            if kind in ("DocTestFailure", "UnexpectedException"):
                number = where.rpartition(":")[2]
                break

    # Results of three lines or more are printed as a diff, where "-" marks
    # a line that was expected and "+" a line that came.
    want += [ln[1:] for ln in diff if ln.startswith("-")]
    got += [ln[1:] for ln in diff if ln.startswith("+")]
    # The first lines can agree, so the first pair that differs is shown. A
    # traceback names its exception on its last line.
    traceback = any(
        ln.endswith("Traceback (most recent call last):") for ln in want + got + diff
    )
    pairs = enumerate(zip(want, got, strict=False))
    at = -1 if traceback else next((n for n, (a, b) in pairs if a != b), 0)
    # Quotes show white space when it is all that differs.
    spaces = (
        bool(want and got)
        and want[at] != got[at]
        and want[at].strip() == got[at].strip()
    )
    parts = []
    for word, part in (("expected", want), ("got", got)):
        if part:
            text = repr(part[at]) if spaces else part[at].strip()
            more = " ..." if part[1:] and not traceback else ""
            parts.append(f"{word} {plain_text(text + more, 60)}")
    said = raised or ", ".join(parts)
    if said and number.isascii() and number.isdigit():
        said = f"line {number}: {said}"
    return plain_text(said, 160)


def _not_imported(lines: list[str], root: Path) -> tuple[str, list[str]]:
    """Describe the files that pytest could not import.

    pytest leaves such a file out and prints "SKIPPED [1] <where>: unable to
    import module PosixPath('<file>')" for it. A docstring whose examples are
    all marked +SKIP gives a SKIPPED line as well, with another reason.

    Returns:
        The words to add to the result message, and the detail line that
        names the files. Both are empty when every file was imported.
    """
    files = []
    for line in lines:
        start, found, shown = line.partition("unable to import module ")
        if found and start.startswith("SKIPPED "):
            # The path is printed as its repr, quotes and all.
            path = shown.partition("(")[2].rpartition(")")[0][1:-1] or shown
            files.append(display_path(Path(path), root))
    if not files:
        return "", []
    count = "1 file" if len(files) == 1 else f"{len(files)} files"
    its = "its" if len(files) == 1 else "their"
    named = ", ".join(files[:MAX_DETAIL_LINES])
    if len(files) > MAX_DETAIL_LINES:
        named += f" ... and {len(files) - MAX_DETAIL_LINES} more"
    return (
        f", {count} not imported",
        [f"{count} could not be imported, so {its} examples did not run: {named}"],
    )


class DoctestsCheck(BaseCheck):
    """Run doctests via pytest --doctest-modules.

    Executes all doctests embedded in module docstrings and reports
    failures. A file that pytest cannot import is left out and named.

    Attributes:
        name: The check identifier ("doctests").
        description: Human-readable description of this check.
        rules: The codes of the findings this check can report.
        hints: What to do about each of them, one sentence for each code.
    """

    name = "doctests"
    description = "Run doctests in package modules"
    rules = {
        "doctests.invalid-args": "The args option is not a list of strings",
        "doctests.timed-out": "The doctests ran longer than the timeout",
        "doctests.could-not-run": "pytest is installed but could not run",
        "doctests.failed": "Doctests failed",
    }
    hints = {
        "doctests.invalid-args": (
            'Write args as a list with one string for each argument, so "-x -q" '
            'becomes ["-x", "-q"].'
        ),
        "doctests.timed-out": (
            "Set a longer timeout in [tool.pycmdcheck.checks.doctests], or in the "
            "--config file when the run uses one."
        ),
        "doctests.could-not-run": (
            "Run pytest --doctest-modules on the package with the same args in the "
            "project directory and fix the error it prints."
        ),
        "doctests.failed": (
            "Correct each failed example or the code it calls, or mark an example "
            "that cannot run here with # doctest: +SKIP."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Run doctests for the package.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.
                Supports ``args`` (list[str]) for additional pytest args
                and ``timeout`` (seconds) to override the default of 120.

        Returns:
            A CheckResult with:

            - OK status if all doctests pass
            - WARNING status if pytest is installed but could not run
            - ERROR status if some doctests fail, pytest times out or an
              invalid option is set
            - SKIPPED status if pytest is not installed ("tool-missing"),
              if the package has no doctests or there is no package to
              collect them from ("not-applicable"), or if no doctest ran
              and a file could not be imported ("import-failed")

            Files that pytest could not import are counted in the message
            and named in the details. They change the status only when no
            doctest ran.
        """
        if not tool_available("pytest"):
            return CheckResult.skip(
                self.name,
                "pytest not installed",
                "tool-missing",
                [missing_tool_hint("pytest")],
            )

        try:
            extra_args = sanitize_args(config.get("args"), check=self.name)
        except ToolArgsError as exc:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Invalid args option",
                details=[str(exc)],
                findings=[
                    Finding("doctests.invalid-args", str(exc), CheckStatus.ERROR)
                ],
            )
        timeout, note = resolve_timeout(config, DEFAULT_TIMEOUT, check=self.name)

        # Only the package itself is collected. --doctest-modules imports
        # every file it is given, so the project root would run the unit
        # tests, docs/conf.py and example scripts as well. Helper packages
        # (examples/, benchmarks/) are left out whenever a real one exists,
        # the selection PackageLayout.source_files() makes.
        candidates = PackageLayout(package_path).packages
        runtime = [c for c in candidates if c.rank >= 2] or candidates
        kinds = ("package", "namespace", "module")
        targets = [c.path for c in runtime if c.kind in kinds]
        if not targets:
            return CheckResult.skip(
                self.name,
                "No doctests found",
                "not-applicable",
                ["Found no package or module to collect doctests from"],
            )

        # The cache plugin stays loaded: a project whose addopts use --ff or
        # --lf cannot start pytest without it.
        # -rs lists the files that could not be imported. -r replaces
        # pytest's default of fE, which prints the FAILED lines, so those
        # letters are passed as well. pytest prints the lines in the order
        # of the letters, and the s comes first so that the error of a run
        # that could not start stays among its last lines.
        # --verbosity=-1 is the level of one -q. A -q here would add to a -q
        # in the project's addopts, and pytest then prints no count line.
        cmd = [
            "pytest",
            "--doctest-modules",
            "--doctest-ignore-import-errors",
            "--tb=short",
            "--verbosity=-1",
            "-rsfE",
        ]
        # The project's addopts stay in force. When they turn coverage on, a
        # doctest-only run would miss the project's coverage threshold, so
        # coverage is switched off (pytest-cov is installed in that case).
        addopts = pytest_ini_option(package_path, "addopts")
        if any(option.startswith("--cov") for option in addopts):
            cmd.append("--no-cov")
        cmd += [*(str(target) for target in targets), *extra_args]

        details = [
            "Running: pytest --doctest-modules",
            f"Using {describe_tool('pytest')}",
        ]
        if note:
            details.append(note)

        # The tests check runs pytest in the same directory. Two runs at once
        # corrupt each other's coverage and cache files.
        with PYTEST_LOCK:
            result = run_tool(cmd, cwd=package_path, timeout=timeout)

        if result.timed_out:
            return CheckResult(
                name=self.name,
                status=CheckStatus.ERROR,
                message="Doctest execution timed out",
                details=[f"Timeout after {timeout}s"],
                findings=[
                    Finding(
                        "doctests.timed-out",
                        f"Doctests took longer than {timeout} seconds",
                        CheckStatus.ERROR,
                    )
                ],
            )

        if result.error is not None:
            return could_not_run(self.name, "pytest", result, details)

        # pytest prints its FAILED and SKIPPED lines below the last line
        # that opens its short summary. What the examples print is above
        # that line, so a line of this kind written by a project is not read.
        lines = result.output_lines()
        opens = [n for n, ln in enumerate(lines) if "= short test summary info =" in ln]
        banner = opens[-1] if opens else len(lines)
        blocks, summary = lines[:banner], lines[banner:]

        # A file that could not be imported is never a failure, because the
        # environment of the check may lack an optional dependency. It is
        # counted in the message and named in the details.
        suffix, left_out = _not_imported(summary, package_path)

        # pytest exit code 5 means "no tests were collected". For a package
        # with no doctests there was nothing to check, which is not a
        # failure. Only the exit code says so: a usage error prints "no tests
        # ran" as well. With a file left out, it is not known that the
        # package has no doctests, so the reason is "import-failed".
        # Exit code 0 with a file left out is the same case when pytest's
        # last count line names no passed example. A docstring whose examples
        # are all marked +SKIP is collected and skipped, which is not exit
        # code 5. A run with no count line is taken as passed.
        counts = [m[1] for ln in summary if (m := _COUNT_LINE_RE.fullmatch(ln))]
        none_passed = bool(counts) and "passed" not in counts[-1]
        if result.returncode == 5 or (
            result.returncode == 0 and left_out and none_passed
        ):
            if not left_out:
                details.append("No doctests found in package modules")
            return CheckResult.skip(
                self.name,
                f"No doctests ran{suffix}" if left_out else "No doctests found",
                "import-failed" if left_out else "not-applicable",
                details + left_out,
            )

        if result.returncode == 0:
            details.append("All doctests passed")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message=f"All doctests passed{suffix}",
                details=details + left_out,
            )

        # Only exit code 1 means failed doctests. Anything else (2 for a
        # collection error, 3 for an internal error, 4 for bad arguments) is
        # a problem with the run. That is a warning, where failed doctests
        # are an error. Exit code 1 with nothing on stdout and text on stderr
        # is a crash.
        if result.returncode != 1 or (not lines and result.stderr.strip()):
            return could_not_run(self.name, "pytest", result, details)

        # Doctests failed. The short summary has one "FAILED <id>" line per
        # doctest; banners and tracebacks also hold the words FAILED or ERROR.
        # Each line that is shown gets its reason, as pytest gives one for a
        # failed test: "FAILED <id> - <reason>".
        failed_lines = [ln for ln in summary if ln.startswith("FAILED ")]
        shown = []
        for line in failed_lines[:MAX_DETAIL_LINES]:
            reason = _reason(blocks, line.rpartition("::")[2])
            # The id holds the name of a file, which the project chose.
            line = plain_text(line, 240)
            shown.append(f"{line} - {reason}" if reason else line)
        details.extend(shown or result.tail())
        if len(failed_lines) > MAX_DETAIL_LINES:
            details.append(f"... and {len(failed_lines) - MAX_DETAIL_LINES} more")

        fail_count = len(failed_lines) if failed_lines else "some"
        message = f"{fail_count} doctest(s) failed"
        return CheckResult(
            name=self.name,
            status=CheckStatus.ERROR,
            message=f"{message}{suffix}",
            details=details + left_out,
            findings=[Finding("doctests.failed", message, CheckStatus.ERROR)],
        )
