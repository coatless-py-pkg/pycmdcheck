"""Tests for ``pycmdcheck.subprocess_runner``.

Some tests start a real child process (the running Python interpreter), so the
process handling is exercised for real. Every such process ends by itself
within seconds, so a failing test leaves nothing behind.

The checks that use the runner are tested in their own ``test_check_*`` files,
with the runner mocked.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from pycmdcheck import subprocess_runner as sr
from pycmdcheck.constants import MAX_DETAIL_LINES
from pycmdcheck.pyproject_reader import get_table
from pycmdcheck.results import CheckStatus, Finding
from pycmdcheck.subprocess_runner import SubprocessResult, run_tool

PY = sys.executable

posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="needs POSIX process groups and shell scripts"
)

# A tool that prints a line and then hangs.
HANG = [PY, "-c", "import time; print('started', flush=True); time.sleep(10)"]

# A helper that appends to the file named by argv[1] every 20 ms, so a test
# can tell whether it is still running.
HEARTBEAT = """\
import sys, time
for _ in range(500):
    with open(sys.argv[1], "a") as fh:
        fh.write("x")
    time.sleep(0.02)
"""

# A tool that starts the heartbeat helper, waits for its first beat, prints a
# line and hangs.
HANG_WITH_HELPER = """\
import os, subprocess, sys, time
subprocess.Popen([sys.executable, "-c", sys.argv[1], sys.argv[2]])
for _ in range(500):
    if os.path.exists(sys.argv[2]):
        break
    time.sleep(0.01)
print("started", flush=True)
time.sleep(10)
"""

# A tool whose helper moves to a session of its own, out of reach of a group
# kill, and keeps the output pipes open. The tool prints the helper's ID.
HANG_WITH_ESCAPED_HELPER = """\
import subprocess, sys, time
helper = subprocess.Popen(
    [sys.executable, "-c", "import time; time.sleep(10)"], start_new_session=True
)
print(helper.pid, flush=True)
time.sleep(10)
"""


def _make_tool(directory: Path, name: str, script: str = "echo ok") -> Path:
    """Create an executable shell script called ``name`` in ``directory``."""
    tool = directory / name
    tool.write_text(f"#!/bin/sh\n{script}\n")
    tool.chmod(0o755)
    return tool


@pytest.fixture
def empty_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Point PATH at one empty directory and return that directory."""
    directory = tmp_path / "path"
    directory.mkdir()
    monkeypatch.setenv("PATH", str(directory))
    return directory


@pytest.fixture
def own_bin(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Pretend that the Python running pycmdcheck lives in a directory off PATH."""
    directory = tmp_path / "toolenv" / "bin"
    directory.mkdir(parents=True)
    monkeypatch.setattr(sys, "executable", str(directory / "python"))
    return directory


class TestSubprocessResult:
    """Test the result that ``run_tool`` returns."""

    @pytest.mark.parametrize(
        ("fields", "success"),
        [
            ({"returncode": 0}, True),
            ({}, False),
            ({"returncode": 1}, False),
            ({"returncode": 0, "timed_out": True}, False),
            ({"returncode": 0, "error": "could not start"}, False),
        ],
    )
    def test_success(self, fields: dict[str, Any], success: bool) -> None:
        """Only a run that finished with exit code 0 is a success."""
        assert SubprocessResult(**fields).success is success

    def test_defaults_and_immutability(self) -> None:
        result = SubprocessResult()
        assert (result.returncode, result.stdout, result.stderr) == (-1, "", "")
        assert result.timed_out is False
        assert result.error is None
        with pytest.raises(AttributeError):
            result.returncode = 1  # type: ignore[misc]

    def test_output_and_lines(self) -> None:
        """The line helpers drop empty lines, and output joins both streams."""
        result = SubprocessResult(returncode=0, stdout="a\n\nb\n", stderr="warn\n\n")
        assert result.output == "a\n\nb\nwarn\n\n"
        assert result.output_lines() == ["a", "b"]
        assert result.stderr_lines() == ["warn"]
        assert SubprocessResult().output_lines() == []

    def test_tail_prefers_stderr(self) -> None:
        result = SubprocessResult(
            returncode=4,
            stdout="collected 0 items\n",
            stderr="ERROR: usage: pytest [options]\n\npytest: error: bad -n  \n",
        )
        assert result.tail() == [
            "ERROR: usage: pytest [options]",
            "pytest: error: bad -n",
        ]

    def test_tail_falls_back_to_stdout_and_is_capped(self) -> None:
        stdout = "".join(f"line {i}\n" for i in range(MAX_DETAIL_LINES + 5))
        tail = SubprocessResult(returncode=2, stdout=stdout, stderr=" \n").tail()
        assert len(tail) == MAX_DETAIL_LINES
        assert tail[-1] == f"line {MAX_DETAIL_LINES + 4}"
        assert SubprocessResult(returncode=1).tail() == []


@posix_only
class TestFindTool:
    """Test where tools are looked up: PATH first, then beside pycmdcheck."""

    def test_path_is_searched(self, empty_path: Path) -> None:
        tool = _make_tool(empty_path, "mypy")
        assert sr.find_tool("mypy") == str(tool)
        assert sr.tool_available("mypy") is True
        assert sr.find_tool("pycmdcheck-no-such-tool") is None
        assert sr.tool_available("pycmdcheck-no-such-tool") is False

    @pytest.mark.parametrize(
        ("name", "found"),
        [("ruff", True), ("black", True), ("pytest", False), ("mypy", False)],
    )
    def test_only_ruff_and_black_are_also_found_beside_pycmdcheck(
        self, name: str, found: bool, empty_path: Path, own_bin: Path
    ) -> None:
        """Tools that import the project must come from its own environment."""
        tool = _make_tool(own_bin, name)
        assert sr.find_tool(name) == (str(tool) if found else None)

    def test_path_wins_over_the_copy_beside_pycmdcheck(
        self, empty_path: Path, own_bin: Path
    ) -> None:
        on_path = _make_tool(empty_path, "ruff")
        _make_tool(own_bin, "ruff")
        assert sr.find_tool("ruff") == str(on_path)

    def test_without_sys_executable_only_path_is_searched(
        self, empty_path: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An embedded interpreter does not make the current directory count."""
        _make_tool(tmp_path, "ruff")
        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sys, "executable", "")
        assert sr.find_tool("ruff") is None

    def test_run_tool_finds_a_bare_command_the_same_way(
        self, empty_path: Path, own_bin: Path, tmp_path: Path
    ) -> None:
        _make_tool(own_bin, "ruff", "echo ruff 9.9.9")
        assert run_tool(["ruff", "--version"], cwd=tmp_path).stdout == "ruff 9.9.9\n"
        assert sr.describe_tool("ruff") == "ruff 9.9.9"

    def test_find_python(self, empty_path: Path) -> None:
        """python on PATH comes first, then python3, then the running one."""
        assert sr.find_python() == sys.executable
        python3 = _make_tool(empty_path, "python3")
        assert sr.find_python() == str(python3)
        python = _make_tool(empty_path, "python")
        assert sr.find_python() == str(python)


@pytest.mark.parametrize(
    ("name", "extra", "expected"),
    [
        ("ruff", "linting", "next to its own Python"),
        ("build", "build", f"environment that runs pycmdcheck ({sys.executable})"),
        ("black", None, "next to its own Python"),
        ("pytest", None, "only looked up on PATH"),
        # mypy has an extra, but it has to run from the project's environment
        ("mypy", None, "only looked up on PATH"),
    ],
    ids=["ruff", "build", "black", "pytest", "mypy"],
)
def test_missing_tool_hint(name: str, extra: str | None, expected: str) -> None:
    """The hint says how to install the tool and where pycmdcheck looked.

    It names the pycmdcheck extra for the tools that one installs.
    """
    hint = sr.missing_tool_hint(name)
    assert f"'pip install {name}'" in hint
    assert expected in hint
    assert ("pycmdcheck[" in hint) is (extra is not None)
    if extra is not None:
        assert f"'pip install \"pycmdcheck[{extra}]\"' or 'pip install {name}'" in hint


def test_install_hint_extras_are_declared(repo_root: Path) -> None:
    """An extra that a hint names has to exist, or the command it gives fails."""
    declared = get_table(repo_root, "project", "optional-dependencies")
    assert sorted(set(sr.TOOL_EXTRAS.values()) - set(declared)) == []


@pytest.mark.parametrize(
    ("outcome", "reason"),
    [
        (
            SubprocessResult(error="Could not start ruff: denied", stderr="unused\n"),
            ["Could not start ruff: denied"],
        ),
        (
            SubprocessResult(returncode=2, stdout="out\n", stderr="error: bad flag\n"),
            ["ruff exited with code 2", "error: bad flag"],
        ),
    ],
    ids=["not-started", "exit-code"],
)
def test_could_not_run(outcome: SubprocessResult, reason: list[str]) -> None:
    """The result is a WARNING that names the tool and says why it failed.

    Its one finding carries the code of the check that ran the tool.
    """
    result = sr.could_not_run("linting", "ruff", outcome, ["Using linter: ruff 9.9"])
    assert result.name == "linting"
    assert result.status == CheckStatus.WARNING
    assert result.message == "ruff could not run"
    assert result.details == ["Using linter: ruff 9.9", *reason]
    assert result.findings == [
        Finding("linting.could-not-run", reason[0], CheckStatus.WARNING)
    ]
    assert result.skip_reason is None


class TestClean:
    """Test decoding of captured output and removal of escape sequences."""

    @pytest.mark.parametrize(
        ("raw", "clean"),
        [
            (b"\x1b[1m\x1b[91mF401 \x1b[0m[\x1b[96m*\x1b[0m] one", "F401 [*] one"),
            (b"\x1b[2K\x1b[1Gdone\x1b[?25h", "done"),
            (b"\x1b]8;;https://e.org/E501\x1b\\E501\x1b]8;;\x1b\\ long", "E501 long"),
            (b"\x1b]0;title\x07text", "text"),
            (b"ok\x1b(B\x1b[m done", "ok done"),
            (b"\x1b7saved\x1b8 unfinished\x1b", "saved unfinished"),
            (b"[tool.ruff] a[0] = 1\n", "[tool.ruff] a[0] = 1\n"),
            (b"one\r\ntwo\rthree\n", "one\ntwo\nthree\n"),
            (None, ""),
        ],
    )
    def test_escape_sequences_and_line_endings(
        self, raw: bytes | None, clean: str
    ) -> None:
        assert sr._clean(raw) == clean

    def test_no_escape_byte_survives(self) -> None:
        raw = b"".join(b"a\x1b" + bytes([follower]) + b"b" for follower in range(128))
        assert "\x1b" not in sr._clean(raw)


class TestRunTool:
    """Test ``run_tool`` against real child processes."""

    def test_output_and_exit_code(self, tmp_path: Path) -> None:
        code = "import sys; print('out'); sys.stderr.write('warn\\n'); sys.exit(3)"
        failed = run_tool([PY, "-c", code], cwd=tmp_path)
        passed = run_tool([PY, "-c", "print('all good')"], cwd=tmp_path)

        assert failed.returncode == 3
        assert (failed.stdout, failed.stderr) == ("out\n", "warn\n")
        assert failed.success is False
        assert failed.timed_out is False
        assert failed.error is None
        assert passed.success is True
        assert passed.stdout == "all good\n"

    def test_runs_in_cwd_with_stdin_closed(self, tmp_path: Path) -> None:
        """A tool that reads standard input sees its end instead of waiting."""
        code = "import os, sys; print(repr(sys.stdin.read())); print(os.getcwd())"
        stdin, cwd = run_tool([PY, "-c", code], cwd=tmp_path).output_lines()
        assert stdin == "''"
        assert Path(cwd).resolve() == tmp_path.resolve()

    def test_forced_colour_variables_do_not_reach_the_tool(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        for name in sr.FORCED_COLOR_VARS:
            monkeypatch.setenv(name, "1")
        monkeypatch.setenv("PYCMDCHECK_TEST_KEEP", "kept")
        code = "import json, os; print(json.dumps(dict(os.environ)))"

        seen = json.loads(run_tool([PY, "-c", code], cwd=tmp_path).stdout)

        assert not set(sr.FORCED_COLOR_VARS) & set(seen)
        assert seen["PYCMDCHECK_TEST_KEEP"] == "kept"
        assert os.environ["FORCE_COLOR"] == "1"

    def test_output_is_decoded_and_cleaned(self, tmp_path: Path) -> None:
        """Bytes that are not valid text are replaced, and escapes are removed."""
        code = (
            "import sys; "
            "sys.stdout.buffer.write(b'\\x1b[31mFAILED\\x1b[0m \\xff end\\n'); "
            "sys.stderr.buffer.write(b'\\x1b[1mE501\\x1b[0m too long\\n')"
        )
        result = run_tool([PY, "-c", code], cwd=tmp_path)

        assert result.returncode == 0
        assert result.stdout.startswith("FAILED ")
        assert result.stdout.endswith(" end\n")
        assert result.stderr == "E501 too long\n"

    def test_tool_that_cannot_start_is_an_error_result(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """A missing tool or directory is reported by the check, not logged."""
        missing = run_tool(["pycmdcheck-no-such-tool"], cwd=tmp_path)
        no_cwd = run_tool([PY, "-c", "pass"], cwd=tmp_path / "gone")

        assert missing.success is False
        assert missing.error is not None
        assert missing.error.startswith("Could not start pycmdcheck-no-such-tool: ")
        assert no_cwd.error is not None
        assert no_cwd.error.startswith(f"Could not start {PY}: ")
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    def test_timeout_keeps_the_output_so_far(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        started = time.monotonic()
        result = run_tool(HANG, cwd=tmp_path, timeout=1.0)

        assert time.monotonic() - started < 5
        assert result.timed_out is True
        assert result.success is False
        assert result.error is None
        assert result.returncode == -1
        assert result.stdout == "started\n"
        assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []

    @posix_only
    def test_timeout_kills_the_processes_the_tool_started(self, tmp_path: Path) -> None:
        beat = tmp_path / "beat"
        result = run_tool(
            [PY, "-c", HANG_WITH_HELPER, HEARTBEAT, str(beat)],
            cwd=tmp_path,
            timeout=1.0,
        )

        assert result.timed_out is True
        assert result.stdout == "started\n"
        beats = beat.stat().st_size
        time.sleep(0.15)
        assert beat.stat().st_size == beats, "the helper process is still running"

    @posix_only
    def test_tool_that_exited_keeps_its_result_when_a_helper_lingers(
        self, tmp_path: Path
    ) -> None:
        """A helper that holds the output open does not turn a pass into a timeout."""
        script = (
            "import subprocess, sys\n"
            "print('done', flush=True)\n"
            "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
            "sys.exit(0)\n"
        )
        started = time.monotonic()
        result = run_tool([PY, "-c", script], cwd=tmp_path, timeout=1.0)

        assert result.timed_out is False
        assert result.returncode == 0
        assert result.stdout == "done\n"
        assert time.monotonic() - started < 5

    @posix_only
    def test_helper_outside_the_group_cannot_stall_the_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """What the tool printed is kept although its pipes never close."""
        monkeypatch.setattr(sr, "_KILL_GRACE", 0.2)
        started = time.monotonic()
        result = run_tool(
            [PY, "-c", HANG_WITH_ESCAPED_HELPER], cwd=tmp_path, timeout=0.5
        )
        elapsed = time.monotonic() - started

        helper = int(result.stdout.strip() or "0")
        if helper:
            os.kill(helper, signal.SIGKILL)
        assert helper, "the output so far was lost"
        assert result.timed_out is True
        assert elapsed < 5

    @pytest.mark.parametrize(
        "raised", [RuntimeError("pipe went away"), KeyboardInterrupt()]
    )
    def test_tool_is_killed_when_the_wait_fails(
        self, raised: BaseException, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An error becomes a result, and Ctrl-C goes on to the caller."""
        tools: list[subprocess.Popen[bytes]] = []

        def broken(self: subprocess.Popen[bytes], *args: Any, **kwargs: Any) -> Any:
            tools.append(self)
            raise raised

        monkeypatch.setattr(subprocess.Popen, "communicate", broken)
        if isinstance(raised, KeyboardInterrupt):
            with pytest.raises(KeyboardInterrupt):
                run_tool(HANG, cwd=tmp_path)
        else:
            result = run_tool(HANG, cwd=tmp_path)
            assert result.success is False
            assert result.error is not None
            assert "pipe went away" in result.error

        assert tools[0].wait(timeout=5) != 0
        assert sr._running == set()

    def test_kill_running_tools(self, tmp_path: Path) -> None:
        """A tool that a worker thread waits for is stopped from this thread."""
        sr.kill_running_tools()  # Nothing is running, which is fine.
        results: list[SubprocessResult] = []
        worker = threading.Thread(
            target=lambda: results.append(run_tool(HANG, cwd=tmp_path, timeout=10))
        )
        worker.start()
        try:
            deadline = time.monotonic() + 5
            while not sr._running and time.monotonic() < deadline:
                time.sleep(0.01)
            assert sr._running, "the tool never started"
            sr.kill_running_tools()
        finally:
            worker.join(timeout=5)

        assert not worker.is_alive()
        assert results[0].success is False
        assert results[0].timed_out is False
        assert sr._running == set()


class TestToolVersion:
    """Test reading a tool's version for a detail line."""

    @pytest.mark.parametrize(
        ("stdout", "stderr", "expected"),
        [
            ("ruff 0.14.10\n", "", "0.14.10"),
            ("black, 24.2.0 (compiled: yes)\nPython (CPython) 3.12.1\n", "", "24.2.0"),
            ("7.1.1 (mccabe: 0.7.0, pyflakes: 3.2.0) CPython 3.12.1\n", "", "7.1.1"),
            ("build 1.2.2 (/x/python3.12/site-packages/build)\n", "", "1.2.2"),
            ("mypy 1.20.0+dev.abc123 (compiled: no)\n", "", "1.20.0+dev.abc123"),
            ("", "This is pytest version 5.4.3, imported from /x/py3.8\n", "5.4.3"),
            # A number at the end of a word, ahead of the version, is passed over.
            ("/x/python3.8/site.py: warning\nmypy 1.5.0\n", "", "1.5.0"),
            # So is a version with a "v" in front, which leaves none to read.
            ("tool v1.2.3\n", "", None),
            ("no numbers here\n", "", None),
        ],
    )
    def test_version_is_read_from_the_output(
        self,
        stdout: str,
        stderr: str,
        expected: str | None,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        result = SubprocessResult(returncode=0, stdout=stdout, stderr=stderr)
        monkeypatch.setattr(sr, "run_tool", lambda cmd, **kwargs: result)
        assert sr.tool_version("tool") == expected

    def test_failed_version_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        result = SubprocessResult(returncode=2, stdout="tool 1.2.3\n")
        monkeypatch.setattr(sr, "run_tool", lambda cmd, **kwargs: result)
        assert sr.tool_version("tool") is None
        assert sr.describe_tool("tool") == "tool"

    def test_version_command_runs_in_an_empty_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The project's configuration and modules stay out of reach."""
        seen: dict[str, Any] = {}

        def fake_run_tool(cmd: list[str], **kwargs: Any) -> SubprocessResult:
            seen.update(kwargs, cmd=cmd, entries=os.listdir(kwargs["cwd"]))
            return SubprocessResult(returncode=0, stdout="mypy 1.19.1\n")

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr(sr, "run_tool", fake_run_tool)

        assert sr.describe_tool("mypy", timeout=7) == "mypy 1.19.1"
        assert seen["cmd"] == ["mypy", "--version"]
        assert seen["timeout"] == 7
        assert seen["entries"] == []
        assert Path(seen["cwd"]).resolve() != tmp_path.resolve()
        assert not Path(seen["cwd"]).exists()

    def test_real_commands(self) -> None:
        """A command override is run, and a missing tool has no version."""
        command = [PY, "-c", "print('build 1.2.2')"]
        assert sr.tool_version("build", command=command) == "1.2.2"
        assert sr.tool_version("pycmdcheck-no-such-tool") is None


class TestResolveTimeout:
    """Test the shared reader for a check's ``timeout`` option."""

    def test_default_when_not_configured(self) -> None:
        result = sr.resolve_timeout({"enabled": True}, 120, check="linting")
        assert result == (120, None)

    @pytest.mark.parametrize(
        ("raw", "expected"), [(7, 7), (2.5, 2.5), (90.0, 90), (86_400, 86_400)]
    )
    def test_valid_values(self, raw: float, expected: float) -> None:
        """Positive numbers are used as given, with whole floats as ints."""
        seconds, note = sr.resolve_timeout({"timeout": raw}, 300, check="tests")
        assert seconds == expected
        assert type(seconds) is type(expected)
        assert note is None

    @pytest.mark.parametrize(
        "raw",
        [0, -1, -0.5, "300", True, [300], float("nan")]
        + [pytest.param("x" * 5000, id="long-text")],
    )
    def test_invalid_values_fall_back_to_the_default(self, raw: Any) -> None:
        seconds, note = sr.resolve_timeout({"timeout": raw}, 300, check="tests")
        assert seconds == 300
        assert note is not None
        assert note.startswith("Ignored timeout = ")
        assert "[tool.pycmdcheck.checks.tests]" in note
        assert "default of 300 seconds" in note
        assert len(note) < 400

    @pytest.mark.parametrize("raw", [86_401, 1e12, float("inf"), 10**400])
    def test_long_timeouts_are_lowered_to_the_limit(self, raw: float) -> None:
        seconds, note = sr.resolve_timeout({"timeout": raw}, 300, check="build")
        assert seconds == sr.MAX_TIMEOUT == 86_400
        assert note is not None
        assert "[tool.pycmdcheck.checks.build]" in note
        assert "86400 seconds" in note


class TestSanitizeArgs:
    """Test validation of a check's ``args`` option."""

    def test_valid_values(self) -> None:
        given = ["--select", "E,F,W", "-v"]
        assert sr.sanitize_args(given, check="linting") == given
        assert sr.sanitize_args(given, check="linting") is not given
        assert sr.sanitize_args(("-x", "-q"), check="tests") == ["-x", "-q"]
        assert sr.sanitize_args([], check="tests") == []
        assert sr.sanitize_args(None, check="tests") == []

    @pytest.mark.parametrize(
        ("bad", "shown"),
        [
            ("-x -q", "it is '-x -q'"),
            (["--strict", 42], "it holds 42"),
            ([None], "it holds None"),
            (3, "it is 3"),
            ({"-x": True}, "it is {'-x': True}"),
            pytest.param("x" * 5000, "it is 'xxx", id="long-text"),
        ],
    )
    def test_anything_but_a_list_of_strings_is_rejected(
        self, bad: Any, shown: str
    ) -> None:
        with pytest.raises(sr.ToolArgsError) as excinfo:
            sr.sanitize_args(bad, check="linting")
        message = str(excinfo.value)
        assert isinstance(excinfo.value, TypeError)
        assert message.startswith("args in [tool.pycmdcheck.checks.linting] must be")
        assert "list of strings" in message
        assert shown in message
        assert len(message) < 400


def test_pytest_lock_keeps_other_threads_out() -> None:
    """While one thread holds the lock, another cannot take it."""
    taken: list[bool] = []

    def try_lock() -> None:
        taken.append(sr.PYTEST_LOCK.acquire(timeout=0.05))
        if taken[-1]:
            sr.PYTEST_LOCK.release()

    with sr.PYTEST_LOCK:
        thread = threading.Thread(target=try_lock)
        thread.start()
        thread.join()
    thread = threading.Thread(target=try_lock)
    thread.start()
    thread.join()

    assert taken == [False, True]
