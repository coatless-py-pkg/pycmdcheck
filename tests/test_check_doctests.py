"""Tests for doctests check."""

import threading
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pycmdcheck.checks.doctests import DoctestsCheck
from pycmdcheck.config import get_check_config, load_config
from pycmdcheck.constants import DEFAULT_TIMEOUT, MAX_DETAIL_LINES
from pycmdcheck.results import CheckStatus
from pycmdcheck.subprocess_runner import PYTEST_LOCK, SubprocessResult
from tests.helpers import only_code, write_files

# The tool fixture in conftest.py reads this to know which module to patch.
MODULE = "pycmdcheck.checks.doctests"

BASE_COMMAND = [
    "pytest",
    "--doctest-modules",
    "--doctest-ignore-import-errors",
    "--tb=short",
    "--verbosity=-1",
    "-rsfE",
]

# pytest prints its FAILED and SKIPPED lines below this line.
SUMMARY = f"{'=' * 27} short test summary info {'=' * 28}\n"

# "pytest --doctest-modules -q" with one failing doctest and one file that
# could not be collected (pytest 9).
FAILURE_OUTPUT = """\
F                                                                        [100%]
==================================== ERRORS ====================================
__________________ ERROR collecting src/mypackage/broken.py ____________________
=================================== FAILURES ===================================
___________________ [doctest] mypackage.core.add ___________________
=========================== short test summary info ============================
FAILED src/mypackage/core.py::mypackage.core.add
1 failed, 1 error in 0.03s
"""

# What pytest 9.0.2 printed, with the command of the check, for a tiny project
# kept at /proj. It has six docstrings with a wrong example, one that passes,
# one whose examples are all marked +SKIP and one file that cannot be
# imported. Paths are shortened and spaces at the end of a line are removed.
# The line for the file that cannot be imported is left out, because it holds
# the full path of the project. not_imported() writes it.
FAILED_RUN = """\
FFFF.FFs                                                                 [100%]
=================================== FAILURES ===================================
___________________________ [doctest] demo.Box.size ____________________________
064 Give the size.
065
066         >>> Box().size()
Expected:
    2
Got:
    1

/proj/src/demo/__init__.py:66: DocTestFailure
_____________________________ [doctest] demo.empty _____________________________
052 Return a greeting.
053
054     >>> empty(4)
Expected:
    'hello'
Got nothing

/proj/src/demo/__init__.py:54: DocTestFailure
_____________________________ [doctest] demo.first _____________________________
005 Add two numbers.
006
007     >>> first(1, "x")
UNEXPECTED EXCEPTION: TypeError("unsupported operand type(s) for +: 'int' and 'str'")
Traceback (most recent call last):
  File "/usr/lib/python3.12/doctest.py", line 1368, in __run
    exec(compile(example.source, filename, "single",
  File "<doctest demo.first[0]>", line 1, in <module>
  File "/proj/src/demo/__init__.py", line 10, in first
    return a + b
           ~~^~~
TypeError: unsupported operand type(s) for +: 'int' and 'str'
/proj/src/demo/__init__.py:7: UnexpectedException
_____________________________ [doctest] demo.quiet _____________________________
044 Store a number and say nothing.
045
046     >>> quiet(4)
Expected nothing
Got:
    stored 4

/proj/src/demo/__init__.py:46: DocTestFailure
_____________________________ [doctest] demo.table _____________________________
032 Print a small table.
033
034     >>> table(3)
Differences (unified diff with -expected +actual):
    @@ -1,3 +1,3 @@
     1 1
     2 4
    -3 8
    +3 9

/proj/src/demo/__init__.py:34: DocTestFailure
_____________________________ [doctest] demo.third _____________________________
023 Square a number.
024
025     >>> third(3)
Expected:
    10
Got:
    9

/proj/src/demo/__init__.py:25: DocTestFailure
=========================== short test summary info ============================
FAILED src/demo/__init__.py::demo.Box.size
FAILED src/demo/__init__.py::demo.empty
FAILED src/demo/__init__.py::demo.first
FAILED src/demo/__init__.py::demo.quiet
FAILED src/demo/__init__.py::demo.table
FAILED src/demo/__init__.py::demo.third
SKIPPED [1] ../site-packages/_pytest/doctest.py:458: all tests skipped by +SKIP option
6 failed, 1 passed, 2 skipped in 0.02s
"""

# A run with one failed docstring. BLOCK stands where pytest prints its lines.
ONE_FAILED = """\
F                                                                        [100%]
=================================== FAILURES ===================================
_____________________________ [doctest] demo.table _____________________________
BLOCK
=========================== short test summary info ============================
FAILED src/demo/__init__.py::demo.table
1 failed in 0.02s
"""

# Two text files of one name, run with --doctest-glob (pytest 9.0.2). pytest
# names such a file by its base name, so the two blocks open alike.
SAME_NAME = """\
FF                                                                       [100%]
=================================== FAILURES ===================================
______________________________ [doctest] index.md ______________________________
001 # A
002
003     >>> "A"
Expected:
    'B'
Got:
    'A'

/proj/docs/a/index.md:3: DocTestFailure
______________________________ [doctest] index.md ______________________________
005 More words.
006
007     >>> int("x")
UNEXPECTED EXCEPTION: ValueError("invalid literal for int() with base 10: 'x'")
Traceback (most recent call last):
  File "/usr/lib/python3.12/doctest.py", line 1368, in __run
    exec(compile(example.source, filename, "single",
  File "<doctest index.md[0]>", line 1, in <module>
ValueError: invalid literal for int() with base 10: 'x'
/proj/docs/b/index.md:7: UnexpectedException
=========================== short test summary info ============================
FAILED docs/a/index.md::index.md
FAILED docs/b/index.md::index.md
2 failed in 0.01s
"""

# A project that raises an error whose message holds lines of pytest (pytest
# 9.0.2). Only demo.aa and demo.zz failed, and every file was imported. The
# line that gives the error is cut short here.
FORGED = """\
FF                                                                       [100%]
=================================== FAILURES ===================================
______________________________ [doctest] demo.aa _______________________________
019 Raise with lines that look like lines of pytest.
020
021     >>> aa()
UNEXPECTED EXCEPTION: ValueError("first\\nFAILED src/demo/evil.py::fake - all fine")
Traceback (most recent call last):
  File "/usr/lib/python3.12/doctest.py", line 1368, in __run
    exec(compile(example.source, filename, "single",
  File "<doctest demo.aa[0]>", line 1, in <module>
  File "/proj/src/demo/__init__.py", line 23, in aa
    raise ValueError(FORGED)
ValueError: first
FAILED src/demo/evil.py::fake - all fine
SKIPPED [1] a.py:1: unable to import module PosixPath('/etc/passwd')
=========================== short test summary info ============================
SKIPPED [1] a.py:1: unable to import module PosixPath('/etc/shadow')
_ [doctest] demo.zz _
Expected:
    all fine
Got:
    all fine
x.py:1: DocTestFailure
/proj/src/demo/__init__.py:21: UnexpectedException
______________________________ [doctest] demo.zz _______________________________
027 Fail for real.
028
029     >>> zz()
Expected:
    10
Got:
    9

/proj/src/demo/__init__.py:29: DocTestFailure
=========================== short test summary info ============================
FAILED src/demo/__init__.py::demo.aa
FAILED src/demo/__init__.py::demo.zz
2 failed in 0.01s
"""

# The lines of one failed docstring, in other shapes. pytest 9.0.2 printed
# them, but for the last seven, which are written by hand.
BLOCKS = {
    # both results have fewer than three lines, so pytest prints them in full
    "two-lines": """\
009 Print two lines.
010
011     >>> pair_(1)
Expected:
    1 one
    1 two
Got:
    1 one
    1 deux

/proj/src/demo/__init__.py:11: DocTestFailure""",
    "ndiff": """\
032 Print a small table.
033
034     >>> table(3)
Differences (ndiff with -expected +actual):
      1 1
      2 4
    - 3 8
    ?   ^
    + 3 9
    ?   ^

/proj/src/demo/__init__.py:34: DocTestFailure""",
    "context-diff": """\
032 Print a small table.
033
034     >>> table(3)
Differences (context diff with expected followed by actual):
    ***************
    *** 1,3 ****
      1 1
      2 4
    ! 3 8
    --- 1,3 ----
      1 1
      2 4
    ! 3 9

/proj/src/demo/__init__.py:34: DocTestFailure""",
    # --doctest-continue-on-failure
    "two-failures": """\
037 Two examples fail.
038
039     >>> twice(1)
Expected:
    2
Got:
    1

/proj/src/demo/__init__.py:39: DocTestFailure
037 Two examples fail.
038
039     >>> twice(1)
040     2
041     >>> twice(2)
Expected:
    3
Got:
    2

/proj/src/demo/__init__.py:41: DocTestFailure""",
    # the line that came ends with a space, and nothing else differs
    "space-at-the-end": (
        "005 Print with a space at the end.\n"
        "006\n"
        "007     >>> ws_trailing()\n"
        "Expected:\n"
        "    1\n"
        "Got:\n"
        "    1 \n"
        "\n"
        "/proj/src/demo/__init__.py:7: DocTestFailure"
    ),
    # one error is expected and another is raised
    "wrong-exception": """\
042 Raise another error than the one expected.
043
044     >>> wrong_exception()
Differences (unified diff with -expected +actual):
    @@ -1,3 +1,8 @@
     Traceback (most recent call last):
    -    ...
    -ValueError: a
    +  File "/usr/lib/python3.12/doctest.py", line 1368, in __run
    +    exec(compile(example.source, filename, "single",
    +  File "<doctest demo.table[0]>", line 1, in <module>
    +    wrong_exception()
    +  File "/proj/src/demo/__init__.py", line 49, in wrong_exception
    +    raise KeyError("b")
    +KeyError: 'b'

/proj/src/demo/__init__.py:44: DocTestFailure""",
    # the same, where the error that is expected takes two lines
    "wrong-exception-in-full": """\
053 Raise another error than the one expected.
054
055     >>> wrong_exception_short()
Expected:
    Traceback (most recent call last):
    ValueError: a
Got:
    Traceback (most recent call last):
      File "/usr/lib/python3.12/doctest.py", line 1368, in __run
        exec(compile(example.source, filename, "single",
      File "<doctest demo.table[0]>", line 1, in <module>
        wrong_exception_short()
      File "/proj/src/demo/__init__.py", line 59, in wrong_exception_short
        raise KeyError("b")
    KeyError: 'b'

/proj/src/demo/__init__.py:55: DocTestFailure""",
    "no-exception": """\
063 Return where an error was expected.
064
065     >>> no_exception()
Expected:
    Traceback (most recent call last):
        ...
    ValueError: a
Got:
    5

/proj/src/demo/__init__.py:65: DocTestFailure""",
    "one-more-line": """\
074 Print one line more than expected.
075
076     >>> only_adds()
Differences (unified diff with -expected +actual):
    @@ -2,2 +2,3 @@
     two
     three
    +four

/proj/src/demo/__init__.py:76: DocTestFailure""",
    "the-word-nothing": """\
085 Print where nothing was expected.
086
087     >>> says_nothing()
Expected nothing
Got:
    nothing

/proj/src/demo/__init__.py:87: DocTestFailure""",
    "no-line-number": """\
EXAMPLE LOCATION UNKNOWN, not showing all tests of that example
??? >>> third(3)
Expected:
    10
Got:
    9

/proj/src/demo/__init__.py:None: DocTestFailure""",
    "hostile": f"""\
028 Give a long text.
029
030     >>> long(1)
Expected:
    'short'
Got:
    \u202e{"x" * 300}

/proj/src/demo/__init__.py:30: DocTestFailure""",
    "long-exception": f"""\
028 Give a long text.
029
030     >>> long(1)
UNEXPECTED EXCEPTION: Odd(\u202e{"x" * 300})
Traceback (most recent call last):
  File "<doctest demo.table[0]>", line 1, in <module>
demo.Odd
/proj/src/demo/__init__.py:30: UnexpectedException""",
    # the block of a docstring with a longer name comes next
    "longer-name": """\
023 Square a number.
024
025     >>> third(3)
Expected:
    10
Got:
    9

/proj/src/demo/__init__.py:25: DocTestFailure
__________________________ [doctest] demo.table_wide ___________________________
032 Print a wide table.
033
034     >>> table_wide(3)
Expected:
    1
Got:
    2

/proj/src/demo/__init__.py:34: DocTestFailure""",
    "cut-short": "Expected:",
    "no-path": ": DocTestFailure",
    "not-understood": "INTERNALERROR> boom",
}

# The line of pytest -rs for a file it could not import, where {} is the file.
# The second form is printed with --no-fold-skipped. The third is written by
# hand, for a pytest that prints the path as it is.
IMPORT_SKIPS = [
    "SKIPPED [1] ../site-packages/_pytest/doctest.py:551: "
    "unable to import module PosixPath('{}')",
    "SKIPPED src/demo/maps.py - Skipped: unable to import module PosixPath('{}')",
    "SKIPPED [1] ../site-packages/_pytest/doctest.py:551: unable to import module {}",
]


def not_imported(path: Path, line: str = IMPORT_SKIPS[0]) -> str:
    """Return what pytest -rs prints for a file that it could not import."""
    return line.format(path.as_posix()) + "\n"


# The line of pytest -rs for a docstring whose examples are all marked +SKIP.
ALL_SKIPPED = (
    "SKIPPED [1] ../site-packages/_pytest/doctest.py:458: "
    "all tests skipped by +SKIP option\n"
)


def slow_pytest(cmd: list[str], *, cwd: Path, timeout: float) -> SubprocessResult:
    """Stand in for a pytest run that takes 100 seconds and then passes."""
    if timeout < 100:
        return SubprocessResult(timed_out=True)
    return SubprocessResult(returncode=0)


class TestCommand:
    """What the check asks pytest to do."""

    def test_pytest_not_installed(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """pytest not available -> SKIPPED."""
        run = tool(available=False)
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "tool-missing"
        assert result.findings == []
        assert result.name == "doctests"
        assert any("pip install pytest" in d for d in result.details)
        run.assert_not_called()

    def test_src_layout_command(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = DoctestsCheck().run(temp_package, {"args": ["-x"], "timeout": 7})
        package = str(temp_package / "src" / "mypackage")
        assert run.call_args.args[0] == [*BASE_COMMAND, package, "-x"]
        assert run.call_args.kwargs == {"cwd": temp_package, "timeout": 7}
        assert result.status == CheckStatus.OK
        assert result.findings == []
        assert result.skip_reason is None
        assert "Using pytest 9.9" in result.details

    @pytest.mark.parametrize(
        ("layout", "extra", "target"),
        [
            ("flat", {}, "mypackage"),
            ("module", {}, "mypackage.py"),
            # helper packages beside the real one are not imported either
            (
                "flat",
                {"examples/__init__.py": "", "benchmarks/__init__.py": ""},
                "mypackage",
            ),
            # a namespace package has no __init__.py at its top
            ("none", {"src/mypackage/plugin/__init__.py": ""}, "src/mypackage"),
        ],
        ids=["flat", "module", "helper-packages", "namespace"],
    )
    def test_only_the_package_is_collected(
        self,
        make_package: Callable[..., Path],
        tool: Callable[..., MagicMock],
        layout: str,
        extra: dict[str, str],
        target: str,
    ) -> None:
        """A flat layout must not run the unit tests, docs/conf.py or examples."""
        files = {
            "docs/conf.py": "import missing_theme\n",
            "examples/demo.py": "x = 1\n",
            **extra,
        }
        root = make_package(layout=layout, files=files)
        run = tool()
        DoctestsCheck().run(root, {})
        assert run.call_args.args[0] == [*BASE_COMMAND, str(root / target)]
        assert run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT

    def test_nothing_to_collect(
        self, empty_dir: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """No package or module to collect from -> SKIPPED, not a pass."""
        run = tool()
        result = DoctestsCheck().run(empty_dir, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "not-applicable"
        assert result.findings == []
        assert result.message == "No doctests found"
        assert result.details
        run.assert_not_called()

    @pytest.mark.parametrize(
        ("name", "text", "expected"),
        [
            (
                "pytest.ini",
                "[pytest]\naddopts = --cov=mypackage --cov-fail-under=90\n",
                True,
            ),
            ("setup.cfg", "[tool:pytest]\naddopts = -ra --cov\n", True),
            ("tox.ini", "[pytest]\naddopts = --cov\n", True),
            # coverage named outside pytest's own settings: pytest-cov may be absent
            ("tox.ini", "[testenv]\ncommands = pytest --cov\n", False),
            ("pytest.ini", "[pytest]\naddopts = -ra\n", False),
            # one string in pyproject.toml, where the test below has a list
            (
                "pyproject.toml",
                '[tool.pytest.ini_options]\naddopts = "-ra --cov=mypackage"\n',
                True,
            ),
        ],
    )
    def test_coverage_addopts_are_switched_off(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        name: str,
        text: str,
        expected: bool,
    ) -> None:
        """A doctest-only run would fail the project's own coverage threshold."""
        (temp_package / name).write_text(text)
        run = tool()
        DoctestsCheck().run(temp_package, {})
        assert ("--no-cov" in run.call_args.args[0]) is expected

    def test_coverage_addopts_in_pyproject(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        with (temp_package / "pyproject.toml").open("a") as handle:
            handle.write('\n[tool.pytest.ini_options]\naddopts = ["-ra", "--cov=x"]\n')
        run = tool()
        DoctestsCheck().run(temp_package, {})
        assert "--no-cov" in run.call_args.args[0]

    def test_runs_under_the_pytest_lock(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """tests and doctests must not run pytest in one directory at once."""
        free_elsewhere: list[bool] = []

        def probe() -> None:
            free = PYTEST_LOCK.acquire(blocking=False)
            free_elsewhere.append(free)
            if free:
                PYTEST_LOCK.release()

        def fake_run_tool(cmd: list[str], **kwargs: object) -> SubprocessResult:
            thread = threading.Thread(target=probe)
            thread.start()
            thread.join()
            return SubprocessResult(returncode=0)

        run = tool()
        run.side_effect = fake_run_tool
        DoctestsCheck().run(temp_package, {})
        assert free_elsewhere == [False]

    def test_invalid_args_option(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = DoctestsCheck().run(temp_package, {"args": [1]})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid args option"
        assert only_code(result) == "doctests.invalid-args"
        assert any("list of strings" in d for d in result.details)
        run.assert_not_called()

    def test_invalid_timeout_uses_the_default(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        run = tool()
        result = DoctestsCheck().run(temp_package, {"timeout": "abc"})
        assert run.call_args.kwargs["timeout"] == DEFAULT_TIMEOUT
        assert any("Ignored timeout" in d for d in result.details)


class TestResults:
    """How a finished pytest run is graded."""

    @pytest.mark.parametrize(
        ("code", "stdout", "status", "reason", "message"),
        [
            (
                5,
                "no tests ran in 0.1s\n",
                CheckStatus.SKIPPED,
                "not-applicable",
                "No doctests found",
            ),
            (0, "2 passed in 0.5s\n", CheckStatus.OK, None, "All doctests passed"),
        ],
        ids=["none-collected", "passed"],
    )
    def test_no_failure(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        code: int,
        stdout: str,
        status: CheckStatus,
        reason: str | None,
        message: str,
    ) -> None:
        """Exit code 5 (nothing collected) is not a failure (#5), nor a pass."""
        tool(SubprocessResult(returncode=code, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == status
        assert result.skip_reason == reason
        assert result.findings == []
        assert result.message == message
        assert "Using pytest 9.9" in result.details

    def test_only_failed_lines_are_counted(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Banner lines that hold ERROR or FAILURES are not failed doctests."""
        tool(SubprocessResult(returncode=1, stdout=FAILURE_OUTPUT))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert result.message == "1 doctest(s) failed"
        assert only_code(result) == "doctests.failed"
        assert result.findings[0].message == "1 doctest(s) failed"
        assert result.details[-1] == "FAILED src/mypackage/core.py::mypackage.core.add"
        assert not any("ERRORS" in d or "FAILURES" in d for d in result.details)

    def test_failure_without_a_failed_line_has_no_count(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """With -rN in args, pytest lists no FAILED line."""
        tool(SubprocessResult(returncode=1, stdout="1 failed in 0.01s\n"))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert result.message == "some doctest(s) failed"
        assert only_code(result) == "doctests.failed"
        assert result.details[2:] == ["1 failed in 0.01s"]

    @pytest.mark.parametrize(
        ("outcome", "reason"),
        [
            (
                SubprocessResult(error="Could not start pytest: denied"),
                ["Could not start pytest: denied"],
            ),
            (
                SubprocessResult(
                    returncode=4,
                    stdout="no tests ran in 0.00s\n",
                    stderr="ERROR: file or directory not found: no_such_dir\n",
                ),
                [
                    "pytest exited with code 4",
                    "ERROR: file or directory not found: no_such_dir",
                ],
            ),
            (
                SubprocessResult(returncode=3, stdout="INTERNALERROR> boom\n"),
                ["pytest exited with code 3", "INTERNALERROR> boom"],
            ),
            # a warning here, where the tests check gives an error
            (
                SubprocessResult(
                    returncode=2, stdout="ERROR src/mypackage/broken.py\n"
                ),
                ["pytest exited with code 2", "ERROR src/mypackage/broken.py"],
            ),
            (
                SubprocessResult(returncode=1, stderr="ImportError: bad plugin\n"),
                ["pytest exited with code 1", "ImportError: bad plugin"],
            ),
        ],
        ids=["not-started", "usage", "internal", "collection", "crash"],
    )
    def test_could_not_run_is_not_counted_as_failed_doctests(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        outcome: SubprocessResult,
        reason: list[str],
    ) -> None:
        """A usage error prints 'no tests ran' too, and is not 'no doctests'."""
        tool(outcome)
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert result.message == "pytest could not run"
        assert only_code(result) == "doctests.could-not-run"
        assert result.details == [
            "Running: pytest --doctest-modules",
            "Using pytest 9.9",
            *reason,
        ]

    def test_timeout(self, temp_package: Path, tool: Callable[..., MagicMock]) -> None:
        """Doctest execution timeout -> ERROR."""
        tool(SubprocessResult(timed_out=True))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert "timed out" in result.message.lower()
        assert only_code(result) == "doctests.timed-out"
        assert "120 seconds" in result.findings[0].message
        assert any("120" in d for d in result.details)

    @pytest.mark.parametrize(
        ("code", "before", "after", "said"),
        [
            (
                "doctests.invalid-args",
                'args = "-x -q"',
                'args = ["-x", "-q"]',
                'so "-x -q" becomes ["-x", "-q"].',
            ),
            (
                "doctests.timed-out",
                "timeout = 60",
                "timeout = 300",
                "Set a longer timeout in [tool.pycmdcheck.checks.doctests]",
            ),
        ],
    )
    def test_following_the_hint_clears_the_code(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        code: str,
        before: str,
        after: str,
        said: str,
    ) -> None:
        """The options are read from the table that the hints name."""
        assert said in DoctestsCheck.hints[code]
        tool().side_effect = slow_pytest
        pyproject = temp_package / "pyproject.toml"
        project = pyproject.read_text()
        found = []
        for options in (before, after):
            pyproject.write_text(
                f"{project}\n[tool.pycmdcheck.checks.doctests]\n{options}\n"
            )
            config = get_check_config(load_config(temp_package), "doctests")
            result = DoctestsCheck().run(temp_package, config)
            found.append([finding.code for finding in result.findings])
        assert found == [[code], []]


class TestReasons:
    """What a failed line says about its docstring."""

    def test_each_failed_line_ends_with_its_reason(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """A failed example is an error, as a failed test is."""
        tool(SubprocessResult(returncode=1, stdout=FAILED_RUN))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.ERROR
        assert result.message == "6 doctest(s) failed"
        assert only_code(result) == "doctests.failed"
        assert result.findings[0].message == "6 doctest(s) failed"
        failed = "FAILED src/demo/__init__.py::demo"
        assert result.details[2:] == [
            f"{failed}.Box.size - line 66: expected 2, got 1",
            f"{failed}.empty - line 54: expected 'hello', got no output",
            f"{failed}.first - line 7: "
            "TypeError(\"unsupported operand type(s) for +: 'int' and 'str'\")",
            f"{failed}.quiet - line 46: expected no output, got stored 4",
            f"{failed}.table - line 34: expected 3 8, got 3 9",
            f"{failed}.third - line 25: expected 10, got 9",
        ]

    # What each shape of BLOCKS adds to its FAILED line.
    REASONS = {
        # the first lines agree, so the first that differ are given
        "two-lines": " - line 11: expected 1 two ..., got 1 deux ...",
        "two-failures": " - line 39: expected 2, got 1",
        # quotes show the space that would not be seen without them
        "space-at-the-end": " - line 7: expected '1', got '1 '",
        # a traceback names its error on its last line
        "wrong-exception": " - line 44: expected ValueError: a, got KeyError: 'b'",
        "wrong-exception-in-full": (
            " - line 55: expected ValueError: a, got KeyError: 'b'"
        ),
        "no-exception": " - line 65: expected ValueError: a, got 5",
        "one-more-line": " - line 76: got four",
        "the-word-nothing": " - line 87: expected no output, got nothing",
        "no-line-number": " - expected 10, got 9",
        # text of the project is made plain and cut
        "hostile": f" - line 30: expected 'short', got \\u202e{'x' * 51}...",
        "long-exception": f" - line 30: Odd(\\u202e{'x' * 138}...",
        "longer-name": " - line 25: expected 10, got 9",
        "cut-short": "",
        "no-path": "",
        # a diff style that the project asked for is not read, and nor is
        # text that pytest did not write
        "ndiff": "",
        "context-diff": "",
        "not-understood": "",
    }

    @pytest.mark.parametrize("shape", BLOCKS)
    def test_other_shapes(
        self, temp_package: Path, tool: Callable[..., MagicMock], shape: str
    ) -> None:
        """A line whose reason cannot be read stays as pytest printed it."""
        stdout = ONE_FAILED.replace("BLOCK", BLOCKS[shape])
        tool(SubprocessResult(returncode=1, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.message == "1 doctest(s) failed"
        assert result.details[2:] == [
            f"FAILED src/demo/__init__.py::demo.table{self.REASONS[shape]}"
        ]

    def test_two_blocks_of_one_name_get_no_reason(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Nothing says which block belongs to which line."""
        tool(SubprocessResult(returncode=1, stdout=SAME_NAME))
        result = DoctestsCheck().run(temp_package, {})
        assert result.message == "2 doctest(s) failed"
        assert result.details[2:] == [
            "FAILED docs/a/index.md::index.md",
            "FAILED docs/b/index.md::index.md",
        ]

    def test_lines_that_a_project_forged_are_not_counted(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Only the lines below the last summary line are lines of pytest."""
        tool(SubprocessResult(returncode=1, stdout=FORGED))
        result = DoctestsCheck().run(temp_package, {})
        assert result.message == "2 doctest(s) failed"
        failed = "FAILED src/demo/__init__.py::demo"
        assert result.details[2:] == [
            f'{failed}.aa - ValueError("first\\nFAILED src/demo/evil.py::fake - '
            'all fine")',
            # the forged block has its name, so its reason is not known
            f"{failed}.zz",
        ]

    def test_failed_line_is_made_plain(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The name of a file is text of the project as well."""
        failed = [
            "FAILED docs/a\u202eb.md::a\u202eb.md",
            # a failed test inside the package has a reason from pytest, and
            # only a SKIPPED line names a file that was not imported
            "FAILED src/demo/test_load.py::test_load - ImportError: unable to "
            "import module 'maps'",
        ]
        stdout = SUMMARY + "\n".join(failed) + "\n2 failed in 0.02s\n"
        tool(SubprocessResult(returncode=1, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.message == "2 doctest(s) failed"
        assert result.details[2:] == [
            "FAILED docs/a\\u202eb.md::a\\u202eb.md",
            failed[1],
        ]

    def test_long_list_of_failed_lines_is_cut(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        names = [f"demo.f{n:02}" for n in range(MAX_DETAIL_LINES + 2)]
        blocks = "".join(
            f"____ [doctest] {name} ____\nExpected:\n    1\nGot:\n    2\n"
            f"/proj/src/demo/__init__.py:{n}: DocTestFailure\n"
            for n, name in enumerate(names, start=1)
        )
        failed = [f"FAILED src/demo/__init__.py::{name}" for name in names]
        stdout = blocks + SUMMARY + "\n".join(failed) + "\n"
        tool(SubprocessResult(returncode=1, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.message == f"{len(names)} doctest(s) failed"
        assert result.details[2:] == [
            *(
                f"{line} - line {n}: expected 1, got 2"
                for n, line in enumerate(failed[:MAX_DETAIL_LINES], start=1)
            ),
            "... and 2 more",
        ]


class TestNotImported:
    """Files that pytest left out because it could not import them."""

    # The line is read the same way whether the run passed or failed, so only
    # the run that passed has every form of the line.
    @pytest.mark.parametrize(
        ("code", "stdout", "status", "message", "line"),
        [
            (0, SUMMARY, CheckStatus.OK, "All doctests passed", IMPORT_SKIPS[0]),
            (0, SUMMARY, CheckStatus.OK, "All doctests passed", IMPORT_SKIPS[1]),
            (0, SUMMARY, CheckStatus.OK, "All doctests passed", IMPORT_SKIPS[2]),
            # the docstring whose examples are all marked +SKIP is not a file
            (1, FAILED_RUN, CheckStatus.ERROR, "6 doctest(s) failed", IMPORT_SKIPS[0]),
        ],
        ids=["folded-passed", "unfolded-passed", "bare-passed", "folded-failed"],
    )
    def test_file_is_counted_and_named(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        code: int,
        stdout: str,
        status: CheckStatus,
        message: str,
        line: str,
    ) -> None:
        """The file adds to the message and the details, and to nothing else."""
        skipped = not_imported(temp_package / "src" / "mypackage" / "maps.py", line)
        tool(SubprocessResult(returncode=code, stdout=stdout))
        without = DoctestsCheck().run(temp_package, {})
        tool(SubprocessResult(returncode=code, stdout=stdout + skipped))
        result = DoctestsCheck().run(temp_package, {})
        assert without.message == message
        assert result.message == f"{message}, 1 file not imported"
        assert result.details == [
            *without.details,
            "1 file could not be imported, so its examples did not run: "
            "src/mypackage/maps.py",
        ]
        assert result.status == without.status == status
        assert result.findings == without.findings
        assert result.skip_reason == without.skip_reason

    def test_nothing_collected_and_a_file_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The check does not know that a file it could not read has no doctests."""
        skipped = not_imported(temp_package / "src" / "mypackage" / "__init__.py")
        stdout = f"{SUMMARY}{skipped}1 skipped in 0.02s\n"
        tool(SubprocessResult(returncode=5, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "import-failed"
        assert result.findings == []
        assert result.message == "No doctests ran, 1 file not imported"
        assert result.details[2:] == [
            "1 file could not be imported, so its examples did not run: "
            "src/mypackage/__init__.py"
        ]

    # The count line of pytest 9.0.2 for a package with one file that cannot
    # be imported and one docstring whose examples are all marked +SKIP. The
    # second form came with args = ["-v"]. The third is written by hand, for
    # a run of more than a minute that gave a warning. The fourth came with a
    # unit test marked xfail inside the package, which is not a passed one.
    ONLY_SKIPPED = [
        "2 skipped in 0.02s",
        f"{'=' * 30} 2 skipped in 0.02s {'=' * 30}",
        "2 skipped, 1 warning in 61.00s (0:01:01)",
        "2 skipped, 1 xfailed in 0.02s",
    ]

    @pytest.mark.parametrize(
        "counts", ONLY_SKIPPED, ids=["quiet", "framed", "long", "xfailed"]
    )
    def test_only_skipped_examples_and_a_file_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock], counts: str
    ) -> None:
        """Exit code 0 is not a pass when no example ran, as with exit code 5."""
        skipped = not_imported(temp_package / "src" / "mypackage" / "maps.py")
        stdout = f"{SUMMARY}{skipped}{ALL_SKIPPED}{counts}\n"
        tool(SubprocessResult(returncode=5, stdout=stdout))
        nothing_collected = DoctestsCheck().run(temp_package, {})
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "import-failed"
        assert result.findings == []
        assert result.message == "No doctests ran, 1 file not imported"
        assert result == nothing_collected

    @pytest.mark.parametrize(
        "counts",
        [
            "1 passed, 2 skipped in 0.02s",
            f"{'=' * 25} 1 passed, 2 skipped in 0.02s {'=' * 25}",
        ],
        ids=["quiet", "framed"],
    )
    def test_passed_example_and_a_file_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock], counts: str
    ) -> None:
        """One example that passed is enough for the run to pass."""
        skipped = not_imported(temp_package / "src" / "mypackage" / "maps.py")
        stdout = f"{SUMMARY}{skipped}{ALL_SKIPPED}{counts}\n"
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.skip_reason is None
        assert result.message == "All doctests passed, 1 file not imported"

    @pytest.mark.parametrize("name", ["maps.py", "2 skipped in 0.02s.py"])
    def test_no_count_line_and_a_file_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock], name: str
    ) -> None:
        """pytest prints no count line with -q in args. In doubt it passed."""
        skipped = not_imported(temp_package / "src" / "mypackage" / name)
        tool(SubprocessResult(returncode=0, stdout=SUMMARY + skipped + ALL_SKIPPED))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.skip_reason is None
        assert result.message == "All doctests passed, 1 file not imported"

    # The unfolded form prints the path as the folded one does, so it is left out.
    @pytest.mark.parametrize(
        "line", [IMPORT_SKIPS[0], IMPORT_SKIPS[2]], ids=["folded", "bare"]
    )
    def test_name_of_a_file_is_not_a_passed_example(
        self, temp_package: Path, tool: Callable[..., MagicMock], line: str
    ) -> None:
        """Only the count line of pytest says what passed."""
        path = temp_package / "src" / "mypackage" / "1 passed in 0.01s.py"
        stdout = f"{SUMMARY}{not_imported(path, line)}{ALL_SKIPPED}2 skipped in 0.17s\n"
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.SKIPPED
        assert result.skip_reason == "import-failed"
        assert result.message == "No doctests ran, 1 file not imported"
        assert result.details[2:] == [
            "1 file could not be imported, so its examples did not run: "
            "src/mypackage/1 passed in 0.01s.py"
        ]

    @pytest.mark.parametrize(
        ("forged", "counts", "status", "message"),
        [
            (
                "1 passed in 0.01s",
                "3 skipped in 0.02s",
                CheckStatus.SKIPPED,
                "No doctests ran",
            ),
            (
                "3 skipped in 0.01s",
                "1 passed, 3 skipped in 0.02s",
                CheckStatus.OK,
                "All doctests passed",
            ),
        ],
        ids=["forged-passed", "forged-skipped"],
    )
    def test_last_count_line_is_the_one_of_pytest(
        self,
        temp_package: Path,
        tool: Callable[..., MagicMock],
        forged: str,
        counts: str,
        status: CheckStatus,
        message: str,
    ) -> None:
        """A skip reason with a line break puts a whole line above the count line."""
        skipped = not_imported(temp_package / "src" / "mypackage" / "maps.py")
        # pytest 9.0.2 printed the reason "not here\n<forged>" of a skipped
        # unit test inside the package in this way.
        reason = f"SKIPPED [1] src/mypackage/test_load.py:4: not here\n{forged}\n"
        stdout = f"{SUMMARY}{skipped}{ALL_SKIPPED}{reason}{counts}\n"
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == status
        assert result.message == f"{message}, 1 file not imported"

    def test_only_skipped_examples_and_every_file_imported(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """Marking every example +SKIP is the author's choice, so it passes."""
        stdout = f"{SUMMARY}{ALL_SKIPPED}1 skipped in 0.02s\n"
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.skip_reason is None
        assert result.message == "All doctests passed"

    def test_could_not_run_with_files_left_out(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        """The error comes last, as the letters given to -r put the files first."""
        names = [f"src/mypackage/m{n:02}.py" for n in range(MAX_DETAIL_LINES + 2)]
        skipped = "".join(not_imported(temp_package / name) for name in names)
        error = "ERROR src/mypackage/config.py - KeyError: 'LEVEL'"
        stdout = f"{SUMMARY}{skipped}{error}\n12 skipped, 1 error in 0.09s\n"
        tool(SubprocessResult(returncode=2, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.WARNING
        assert result.message == "pytest could not run"
        assert only_code(result) == "doctests.could-not-run"
        assert result.details[-2:] == [error, "12 skipped, 1 error in 0.09s"]

    def test_long_list_of_files_is_cut(
        self, temp_package: Path, tool: Callable[..., MagicMock]
    ) -> None:
        names = [f"src/mypackage/m{n:02}.py" for n in range(MAX_DETAIL_LINES + 2)]
        skipped = "".join(not_imported(temp_package / name) for name in names)
        stdout = f"{SUMMARY}{skipped}1 passed, 12 skipped in 0.02s\n"
        tool(SubprocessResult(returncode=0, stdout=stdout))
        result = DoctestsCheck().run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.message == f"All doctests passed, {len(names)} files not imported"
        assert result.details[-1] == (
            f"{len(names)} files could not be imported, so their examples did not run: "
            f"{', '.join(names[:MAX_DETAIL_LINES])} ... and 2 more"
        )


class TestRealPytest:
    """The one test here that starts pytest, to read what it really prints."""

    @pytest.mark.tools("pytest")
    def test_failed_example_is_counted_and_explained(self, tmp_path: Path) -> None:
        write_files(tmp_path, {"demo/__init__.py": '"""Demo.\n\n>>> 1 + 1\n3\n"""\n'})
        result = DoctestsCheck().run(tmp_path, {"timeout": 60})
        assert result.message == "1 doctest(s) failed"
        assert result.details[-1] == (
            "FAILED demo/__init__.py::demo - line 3: expected 3, got 2"
        )
