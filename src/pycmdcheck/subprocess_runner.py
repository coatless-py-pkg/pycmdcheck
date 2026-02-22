"""Shared subprocess runner utility for pycmdcheck.

This module provides a unified interface for running external tools
(pytest, ruff, mypy, pyright, etc.) as subprocesses.  It replaces
duplicated subprocess-handling code across the check modules with a
single, well-tested implementation.

Typical usage::

    from pycmdcheck.subprocess_runner import run_tool, tool_available

    if not tool_available("ruff"):
        ...  # handle missing tool

    result = run_tool(["ruff", "check", "."], cwd=package_path)
    if result.success:
        ...
    else:
        for line in result.output_lines():
            ...
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from pycmdcheck.constants import LONG_TIMEOUT

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SubprocessResult:
    """Immutable result of a subprocess execution.

    Attributes:
        returncode: Exit code of the process.  Defaults to ``-1`` when
            the process was never started.
        stdout: Captured standard output.
        stderr: Captured standard error.
        timed_out: Whether the process exceeded the timeout.
        error: Human-readable error message when the process could not
            be started or an unexpected exception occurred.
    """

    returncode: int = -1
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def success(self) -> bool:
        """Return ``True`` when the process exited cleanly with code 0.

        A result is *not* considered successful if the process timed out
        or an error prevented it from running, even if the return code
        happens to be 0.
        """
        return self.returncode == 0 and not self.timed_out and self.error is None

    @property
    def output(self) -> str:
        """Return combined stdout and stderr."""
        return self.stdout + self.stderr

    def output_lines(self, *, strip_blank: bool = True) -> list[str]:
        """Split *stdout* into individual lines.

        Parameters:
            strip_blank: When ``True`` (the default), blank/empty lines
                are removed from the result.

        Returns:
            A list of stdout lines.  A trailing newline does not produce
            a trailing empty string.
        """
        if not self.stdout:
            return []

        # splitlines() already handles the trailing-newline case nicely:
        # "a\nb\n".splitlines() == ["a", "b"]
        lines = self.stdout.splitlines()

        if strip_blank:
            lines = [line for line in lines if line]

        return lines


def tool_available(tool_name: str) -> bool:
    """Check whether an external tool is available on ``$PATH``.

    This is a thin wrapper around :func:`shutil.which`.

    Parameters:
        tool_name: The command name to look up (e.g. ``"ruff"``).

    Returns:
        ``True`` if the tool was found, ``False`` otherwise.
    """
    return shutil.which(tool_name) is not None


def run_tool(
    cmd: list[str],
    *,
    cwd: Path | str | None = None,
    timeout: int = LONG_TIMEOUT,
) -> SubprocessResult:
    """Run an external tool as a subprocess and return a structured result.

    Parameters:
        cmd: The command and its arguments as a list of strings.
        cwd: Working directory for the subprocess.
        timeout: Maximum number of seconds to wait.  Defaults to 300 (5
            minutes).

    Returns:
        A :class:`SubprocessResult` with captured stdout, stderr, and
        metadata about the execution.
    """
    try:
        logger.debug("Running: %s (cwd=%s, timeout=%ds)", cmd, cwd, timeout)
        proc = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        logger.debug("Process exited with code %d", proc.returncode)
        return SubprocessResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired:
        logger.warning("Process timed out after %ds: %s", timeout, cmd)
        return SubprocessResult(timed_out=True)
    except Exception as exc:
        logger.error("Failed to run %s: %s", cmd, exc)
        return SubprocessResult(error=str(exc))


def sanitize_args(args: list[str]) -> list[str]:
    """Validate and return subprocess args from check configuration.

    Ensures all arguments are strings.  Both flags (``--select``) and
    their values (``"E,F,W"``) are allowed, since ``subprocess.run``
    is always called with ``shell=False`` which prevents injection.

    Raises:
        TypeError: If any element is not a string.
    """
    for arg in args:
        if not isinstance(arg, str):
            raise TypeError(
                f"Non-string argument in check config args: {arg!r}. "
                f"All arguments must be strings."
            )
    return list(args)
