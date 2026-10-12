"""Run external tools (pytest, ruff, mypy and others) for the checks.

A check finds its tool with :func:`tool_available`, reads its ``timeout`` and
``args`` options with :func:`resolve_timeout` and :func:`sanitize_args`, and
runs the tool with :func:`run_tool`, which returns a :class:`SubprocessResult`
and does not raise when the tool is missing, fails or runs too long. A tool
that is installed but did not do its job gets the result that
:func:`could_not_run` builds.
"""

from __future__ import annotations

import locale
import logging
import os
import re
import reprlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pycmdcheck.constants import LONG_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckResult, CheckStatus, Finding

logger = logging.getLogger(__name__)

# Longest timeout a check may ask for, in seconds (24 hours). Longer waits
# overflow the timers that subprocess uses on some platforms.
MAX_TIMEOUT: int = 86_400

# Seconds a tool gets to print its version.
VERSION_TIMEOUT: int = 30

# Environment variables that make tools colour their output even though it is
# captured. They are removed from the environment of every tool.
FORCED_COLOR_VARS: tuple[str, ...] = (
    "FORCE_COLOR",
    "CLICOLOR_FORCE",
    "PY_COLORS",
    "MYPY_FORCE_COLOR",
)

# Tools that only read source files, so a copy installed next to pycmdcheck
# (pipx, uv tool) gives the same answer as one in the project's environment.
# Every other tool has to import the project and is looked up on PATH only.
OWN_ENVIRONMENT_TOOLS: frozenset[str] = frozenset({"ruff", "black"})

# The pycmdcheck extra that installs a tool, for the tools that work from
# pycmdcheck's own environment. mypy and pyright have extras too, but they
# have to run where the project is installed.
TOOL_EXTRAS: dict[str, str] = {"ruff": "linting", "build": "build"}

# Held by the tests and doctests checks around their pytest runs. Two pytest
# processes in one directory share coverage and cache files and corrupt them.
PYTEST_LOCK = threading.RLock()

# Seconds to wait for the remaining output of a tool that has just been killed.
_KILL_GRACE: float = 3.0

# Terminal escape sequences: CSI (colours, cursor movement), OSC (hyperlinks,
# window titles), character set selection and the two-character forms. An
# escape byte that starts none of these is removed on its own.
_ANSI_ESCAPE_RE = re.compile(
    r"\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07\x1b]*(?:\x07|\x1b\\)"
    r"|[ -/]+[0-~]|[0-Z\\-~])?"
)

# A dotted version number that is not the tail of a word such as "python3.8"
# or "v1.2.3".
_VERSION_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)+(?:[-+.]?[A-Za-z0-9]+)*")

# The tools that run_tool() is waiting for, so kill_running_tools() can stop them.
_running: set[subprocess.Popen[bytes]] = set()
_running_lock = threading.Lock()


@dataclass(frozen=True, slots=True)
class SubprocessResult:
    """What a tool printed and how it ended.

    Attributes:
        returncode: Exit code of the tool, or -1 when it never started or was
            stopped at the time limit.
        stdout: Standard output without terminal escape sequences. After a
            timeout this is what the tool printed before it was stopped.
        stderr: Standard error, cleaned in the same way.
        timed_out: Whether the tool ran past the time limit.
        error: Why the tool could not be run, when it could not.
    """

    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        """Whether the tool ran to its end and exited with code 0."""
        return self.returncode == 0 and not self.timed_out and self.error is None

    @property
    def output(self) -> str:
        """Standard output followed by standard error."""
        return self.stdout + self.stderr

    def output_lines(self) -> list[str]:
        """Return the lines of standard output that are not empty."""
        return [line for line in self.stdout.splitlines() if line]

    def stderr_lines(self) -> list[str]:
        """Return the lines of standard error that are not empty."""
        return [line for line in self.stderr.splitlines() if line]

    def tail(self) -> list[str]:
        """Return the last lines the tool printed, for a "could not run" detail.

        Tools report usage and configuration problems on standard error, so
        that is used when it holds any text, and standard output otherwise.
        """
        for text in (self.stderr, self.stdout):
            lines = [line.rstrip() for line in text.splitlines() if line.strip()]
            if lines:
                return lines[-MAX_DETAIL_LINES:]
        return []


def find_tool(tool_name: str) -> str | None:
    """Return the absolute path of an external tool, or None when it is missing.

    PATH is searched first, so a version the project pins in its own
    environment wins. ruff and black are then looked up next to the Python
    that runs pycmdcheck, which is where pipx and uv tool install them.
    """
    found = shutil.which(tool_name)
    if found is None and tool_name in OWN_ENVIRONMENT_TOOLS:
        # An embedded interpreter may have no sys.executable.
        found = shutil.which(tool_name, path=os.path.dirname(sys.executable or ""))
    return os.path.abspath(found) if found else None


def tool_available(tool_name: str) -> bool:
    """Return whether :func:`find_tool` finds the tool."""
    return find_tool(tool_name) is not None


def find_python() -> str:
    """Return the Python interpreter of the project's environment.

    That is the one on PATH (``python``, then ``python3``), because it has the
    project installed. Without one, the interpreter running pycmdcheck is used.
    """
    found = shutil.which("python") or shutil.which("python3")
    return os.path.abspath(found) if found else sys.executable


def missing_tool_hint(tool_name: str) -> str:
    """Return the detail line for a check that is skipped for want of a tool.

    The line names the pycmdcheck extra when one installs the tool.
    """
    install = f"'pip install {tool_name}'"
    if tool_name in TOOL_EXTRAS:
        install = f"'pip install \"pycmdcheck[{TOOL_EXTRAS[tool_name]}]\"' or {install}"
    if tool_name == "build":
        return (
            f"Install it with {install} in the environment that runs "
            f"pycmdcheck ({sys.executable})"
        )
    if tool_name in OWN_ENVIRONMENT_TOOLS:
        return (
            f"Install {tool_name} with {install}. pycmdcheck "
            "looked on PATH and next to its own Python interpreter."
        )
    return (
        f"Install {tool_name} with 'pip install {tool_name}' in the project's "
        f"environment and run pycmdcheck from there. {tool_name} is only looked "
        "up on PATH because it has to run where the package and its "
        "dependencies are installed."
    )


def could_not_run(
    check: str, tool: str, result: SubprocessResult, preamble: Sequence[str]
) -> CheckResult:
    """Return the WARNING for a tool that is installed but did not do its job.

    Its one finding has the code ``<check>.could-not-run``, which the check
    lists in its ``rules``.

    Args:
        check: The name of the check that ran the tool.
        tool: The tool name, which the message starts with.
        result: How the tool ended. The reason shown is its ``error``, or
            else the exit code and the last lines the tool printed.
        preamble: The detail lines of the check that come before the reason.
    """
    if result.error is not None:
        reason = [result.error]
    else:
        reason = [f"{tool} exited with code {result.returncode}", *result.tail()]
    finding = Finding(f"{check}.could-not-run", reason[0], CheckStatus.WARNING)
    return CheckResult(
        name=check,
        status=CheckStatus.WARNING,
        message=f"{tool} could not run",
        details=[*preamble, *reason],
        findings=[finding],
    )


def _kill(proc: subprocess.Popen[bytes]) -> None:
    """Kill a tool and, on POSIX, every process in its process group.

    Does nothing once the exit status has been collected, because the process
    ID may by then belong to an unrelated process.
    """
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32":
            proc.kill()
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except OSError:
        pass  # The tool is already gone.


def _clean(data: bytes | None) -> str:
    """Decode captured output and remove terminal escape sequences."""
    encoding = locale.getpreferredencoding(False)
    text = (data or b"").decode(encoding, errors="replace")
    return _ANSI_ESCAPE_RE.sub("", text.replace("\r\n", "\n").replace("\r", "\n"))


def _wait(proc: subprocess.Popen[bytes], timeout: float) -> tuple[str, str, bool]:
    """Wait for a tool and return its output and whether it timed out.

    At the time limit the tool is killed and what it printed so far is kept.
    """
    out: bytes | None
    err: bytes | None
    try:
        out, err = proc.communicate(timeout=timeout)
        return _clean(out), _clean(err), False
    except subprocess.TimeoutExpired:
        _kill(proc)
    try:
        out, err = proc.communicate(timeout=_KILL_GRACE)
    except subprocess.TimeoutExpired as late:
        # A process that left the group still holds the pipes open.
        out, err = late.stdout, late.stderr
    # A tool that had already exited by itself, leaving a helper that kept its
    # output open, did not time out. It keeps its own exit status. A status
    # below zero means the kill above ended it. Windows cannot tell the two
    # apart.
    code = proc.returncode
    exited_by_itself = sys.platform != "win32" and code is not None and code >= 0
    return _clean(out), _clean(err), not exited_by_itself


def run_tool(
    cmd: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: float = LONG_TIMEOUT,
) -> SubprocessResult:
    """Run an external tool and return what it printed, without raising.

    The tool runs with its standard input closed, in its own process group,
    and without the variables that force coloured output. At the time limit
    the tool is killed, and on POSIX every process still in its process group,
    and the output up to that point is returned. The tool is also killed on
    Ctrl-C.

    Args:
        cmd: The command and its arguments. A bare command name is located
            with :func:`find_tool`.
        cwd: Working directory for the tool.
        timeout: Seconds the tool may run.
    """
    program = cmd[0]
    argv = list(cmd)
    if not os.path.dirname(program):
        argv[0] = find_tool(program) or program
    env = {k: v for k, v in os.environ.items() if k not in FORCED_COLOR_VARS}

    logger.debug("Running: %s (cwd=%s, timeout=%ss)", argv, cwd, timeout)
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            # A session of its own also makes the tool the leader of a new
            # process group, which is what lets a timeout stop its children.
            start_new_session=sys.platform != "win32",
        )
    except Exception as exc:
        # Expected when the tool is missing, and reported by the check.
        logger.debug("Could not start %s: %s", argv, exc)
        return SubprocessResult(error=f"Could not start {program}: {exc}")

    with _running_lock:
        _running.add(proc)
    try:
        stdout, stderr, timed_out = _wait(proc, timeout)
    except Exception as exc:
        logger.error("Failed to run %s: %s", argv, exc)
        return SubprocessResult(error=f"{program} could not be run: {exc}")
    finally:
        # Also reached on Ctrl-C, which the tool did not receive because it
        # has its own session. Does nothing after a normal exit.
        _kill(proc)
        with _running_lock:
            _running.discard(proc)

    if timed_out:
        logger.debug("Timed out after %ss: %s", timeout, argv)
        return SubprocessResult(timed_out=True, stdout=stdout, stderr=stderr)
    return SubprocessResult(returncode=proc.returncode, stdout=stdout, stderr=stderr)


def kill_running_tools() -> None:
    """Kill every tool that :func:`run_tool` is waiting for.

    Tools run in their own sessions, so a Ctrl-C at the terminal does not
    reach them. The runner calls this when it is interrupted, and keeps
    calling it until its worker threads are done, because a check may start
    another tool after its first one was killed.
    """
    with _running_lock:
        for proc in _running:
            _kill(proc)


def tool_version(
    tool_name: str,
    *,
    command: Sequence[str] | None = None,
    timeout: float = VERSION_TIMEOUT,
) -> str | None:
    """Return the version a tool reports, or None when it cannot be read.

    Args:
        tool_name: The command name, such as ``"ruff"``.
        command: The command that prints the version, for a tool that is run
            as a module. Defaults to ``[tool_name, "--version"]``.
        timeout: Seconds the version command may take.
    """
    argv = list(command or [tool_name, "--version"])
    try:
        # An empty directory keeps the tool away from the project's
        # configuration and from project files that shadow its own modules.
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            result = run_tool(argv, cwd=scratch, timeout=timeout)
    except OSError as exc:
        logger.debug("Could not create a scratch directory: %s", exc)
        return None
    if not result.success:
        return None
    match = _VERSION_RE.search(result.stdout) or _VERSION_RE.search(result.stderr)
    return match.group(0) if match else None


def describe_tool(tool_name: str, *, timeout: float = VERSION_TIMEOUT) -> str:
    """Return the tool name with its version, for a "Using ..." detail line."""
    version = tool_version(tool_name, timeout=timeout)
    return f"{tool_name} {version}" if version else tool_name


def resolve_timeout(
    config: Mapping[str, Any], default: float, *, check: str
) -> tuple[float, str | None]:
    """Read the ``timeout`` option of a check.

    A value that cannot be used does not fail the check. The default applies
    and the caller gets a note to show as a detail line.

    Args:
        config: The check's configuration.
        default: The check's own default, in seconds.
        check: The check name, which the note uses to name the table.

    Returns:
        The seconds to wait, and a note that is None unless the configured
        value was ignored or lowered to :data:`MAX_TIMEOUT`.
    """
    raw = config.get("timeout")
    if raw is None:
        return default, None

    option = f"timeout = {reprlib.repr(raw)} in [tool.pycmdcheck.checks.{check}]"
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not raw > 0:
        return default, (
            f"Ignored {option}. It must be a positive number of seconds, so "
            f"the default of {default} seconds was used."
        )
    if raw > MAX_TIMEOUT:
        return MAX_TIMEOUT, (
            f"{option} is above the limit of {MAX_TIMEOUT} seconds (24 hours), "
            "so the limit was used."
        )
    # A whole number reads better in a message as 90 than as 90.0.
    return (int(raw) if raw == int(raw) else raw), None


class ToolArgsError(TypeError):
    """The ``args`` option of a check is not a list of strings.

    The message is a complete sentence that can be shown as a detail line.
    """


def sanitize_args(args: object, *, check: str) -> list[str]:
    """Return the ``args`` option of a check as a new list of strings.

    Flags and their values are both allowed, because tools are started
    without a shell. A bare string is rejected, since passing it on would
    hand the tool one argument for each character.

    Args:
        args: The configured value. None means no arguments.
        check: The check name, which the message uses to name the table.

    Raises:
        ToolArgsError: If the value is not a list of strings.
    """
    if args is None:
        return []
    if isinstance(args, (list, tuple)):
        wrong = [arg for arg in args if not isinstance(arg, str)]
        if not wrong:
            return list(args)
        found = f"it holds {reprlib.repr(wrong[0])}"
    else:
        found = f"it is {reprlib.repr(args)}"
    raise ToolArgsError(
        f"args in [tool.pycmdcheck.checks.{check}] must be a list of strings, "
        f"but {found}."
    )
