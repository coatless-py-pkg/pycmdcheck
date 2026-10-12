"""Tests for python_versions check."""

import datetime
from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from pycmdcheck.checks import python_versions
from pycmdcheck.checks.metadata import MetadataCheck
from pycmdcheck.checks.python_versions import PythonVersionsCheck
from pycmdcheck.results import CheckResult, CheckStatus
from tests import helpers
from tests.helpers import skipped

# On this day 3.9 is end-of-life (2025-10-31) and 3.10 is not yet (2026-10-01).
FROZEN = datetime.date(2026, 6, 1)
TOOL_ONLY = "[tool.black]\nline-length = 88\n"
# The codes of the three rules.
EOL = "python_versions.allows-eol"
INVALID = "python_versions.invalid-requires-python"
MISSING = "python_versions.missing-requires-python"


def freeze(monkeypatch: pytest.MonkeyPatch, day: datetime.date) -> None:
    monkeypatch.setattr(python_versions, "_today", lambda: day)
    # Dates in this file are exact, so the grace period is set per test.
    monkeypatch.setattr(python_versions, "EOL_GRACE_DAYS", 0)


@pytest.fixture(autouse=True)
def _frozen_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """No test in this file reads the wall clock (C040)."""
    freeze(monkeypatch, FROZEN)


def run(package: Path) -> CheckResult:
    return PythonVersionsCheck().run(package, {})


def text(result: CheckResult) -> str:
    return "\n".join(result.details)


# The codes of the findings, which must agree with the result and the rules.
codes = partial(helpers.codes, rules=PythonVersionsCheck.rules)


def metadata_codes(package: Path) -> list[str]:
    """Return the codes of what the metadata check finds in the same project."""
    return [finding.code for finding in MetadataCheck().run(package, {}).findings]


def only(name: str, text: str) -> dict:
    """Return the options for a project whose metadata is in one legacy file."""
    return {"pyproject": False, "files": {name: text}}


class TestEndOfLife:
    """Which versions count as end-of-life, and how they are reported."""

    def test_current_versions_ok(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(requires_python=">=3.10"))
        assert result.status == CheckStatus.OK
        assert codes(result) == []
        assert result.name == "python_versions"

    def test_allows_eol_version(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(requires_python=">=3.8"))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [EOL]
        assert "3.8, 3.9" in result.findings[0].message
        assert result.message == "requires-python allows EOL version(s): 3.8, 3.9"
        assert result.details == [
            "requires-python: >=3.8",
            "Python 3.9 reached end of life on 2025-10-31",
        ]
        # The hint is built from the oldest version that is still supported.
        assert result.findings[0].hint == (
            "Raise the lowest version that requires-python allows to 3.10."
        )

    @pytest.mark.parametrize(
        ("day", "status", "found"),
        [
            (datetime.date(2026, 9, 30), CheckStatus.OK, []),
            (datetime.date(2026, 10, 1), CheckStatus.NOTE, [EOL]),
            (datetime.date(2027, 10, 30), CheckStatus.NOTE, [EOL]),
        ],
    )
    def test_eol_is_decided_by_day(
        self,
        make_package: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
        day: datetime.date,
        status: CheckStatus,
        found: list[str],
    ) -> None:
        """3.10 is reported from 2026-10-01, and 3.11 not before its last day."""
        freeze(monkeypatch, day)
        result = run(make_package(requires_python=">=3.10"))
        assert result.status == status
        assert codes(result) == found
        assert "3.11" not in result.message

    @pytest.mark.parametrize(
        ("day", "status"),
        [
            (datetime.date(2026, 10, 30), CheckStatus.OK),
            (datetime.date(2026, 10, 31), CheckStatus.NOTE),
        ],
    )
    def test_grace_days_delay_the_report(
        self,
        make_package: Callable[..., Path],
        monkeypatch: pytest.MonkeyPatch,
        day: datetime.date,
        status: CheckStatus,
    ) -> None:
        freeze(monkeypatch, day)
        monkeypatch.setattr(python_versions, "EOL_GRACE_DAYS", 30)
        assert run(make_package(requires_python=">=3.10")).status == status

    def test_version_inside_the_grace_period_is_named(
        self, make_package: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The result is OK, and the detail says what is coming and when."""
        freeze(monkeypatch, datetime.date(2026, 10, 9))
        monkeypatch.setattr(python_versions, "EOL_GRACE_DAYS", 180)
        result = run(make_package(requires_python=">=3.10"))
        assert result.status == CheckStatus.OK
        assert (
            "Python 3.10 reached end-of-life on 2026-10-01 "
            "and will be reported here from 2027-03-30"
        ) in result.details
        assert "All allowed Python versions are supported" not in result.details

    def test_versions_older_than_the_table_are_summarised(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Everything below the first table entry is end-of-life too."""
        result = run(make_package(requires_python=">=2.7"))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [EOL]
        assert result.message.endswith("2.7 to 3.9 (11 versions)")
        assert len(result.details) <= 4

    @pytest.mark.parametrize(
        ("specifier", "flagged"),
        [
            (">3.9", "3.9"),
            (">=3.9.2", "3.9"),
            (">=3.8.1,<4.0", "3.8, 3.9"),
            ("==3.5.*", "3.5"),
            ("==3.9.7", "3.9"),
        ],
    )
    def test_patch_level_bounds(
        self, make_package: Callable[..., Path], specifier: str, flagged: str
    ) -> None:
        """A late patch release and any release the specifier names are probed."""
        result = run(make_package(requires_python=specifier))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [EOL]
        assert result.message.endswith(f": {flagged}")

    def test_hint_ignores_the_grace_period(
        self, make_package: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A version inside its grace period is not offered as supported."""
        freeze(monkeypatch, datetime.date(2026, 10, 9))
        monkeypatch.setattr(python_versions, "EOL_GRACE_DAYS", 180)
        result = run(make_package(requires_python=">=3.9"))
        assert result.message.endswith(": 3.9")
        assert result.findings[0].hint == (
            "Raise the lowest version that requires-python allows to 3.11."
        )

    def test_hints_name_no_version_when_none_is_supported(
        self, make_package: Callable[..., Path], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """An install too old to know a supported version still reports."""
        # The day the newest version in the table reaches end of life.
        freeze(monkeypatch, max(python_versions.PYTHON_EOL_DATES.values()))
        result = run(make_package(requires_python=">=3.8"))
        assert codes(result) == [EOL]
        # The check sets no hint, so the general one from its hints is used.
        assert result.findings[0].hint is None
        result = run(make_package(requires_python=None, backend="poetry"))
        assert codes(result) == [MISSING]
        assert result.findings[0].hint == (
            "Add python to [tool.poetry.dependencies] in pyproject.toml."
        )


class TestDeclaredSources:
    """Where the constraint is read from (C022)."""

    @pytest.mark.parametrize(
        ("constraint", "status", "found"),
        [
            ("^3.10", CheckStatus.OK, []),
            ("^3.8,<3.12", CheckStatus.NOTE, [EOL]),
            (">=3.8 <3.12", CheckStatus.NOTE, [EOL]),
        ],
    )
    def test_legacy_poetry_constraint(
        self,
        make_package: Callable[..., Path],
        constraint: str,
        status: CheckStatus,
        found: list[str],
    ) -> None:
        result = run(make_package(backend="poetry", requires_python=constraint))
        assert result.status == status
        assert codes(result) == found

    def test_poetry_2_python_fills_project(
        self, make_package: Callable[..., Path]
    ) -> None:
        package = make_package(
            requires_python=None,
            extra='[tool.poetry.dependencies]\npython = "^3.10"\n',
        )
        result = run(package)
        assert result.status == CheckStatus.OK
        assert "python in [tool.poetry.dependencies]: >=3.10,<4" in result.details

    def test_setup_cfg(self, make_package: Callable[..., Path]) -> None:
        """A Latin-1 setup.cfg is still read (C065)."""
        cfg = "[metadata]\nauthor = Jos\xe9\n[options]\npython_requires = >=3.10\n"
        package = make_package(
            pyproject=False, files={"setup.cfg": cfg.encode("latin-1")}
        )
        result = run(package)
        assert result.status == CheckStatus.OK
        assert "python_requires in setup.cfg: >=3.10" in result.details

    def test_setup_py_literal(self, make_package: Callable[..., Path]) -> None:
        setup_py = 'from setuptools import setup\nsetup(python_requires=">=3.8")\n'
        package = make_package(pyproject=TOOL_ONLY, files={"setup.py": setup_py})
        result = run(package)
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [EOL]
        assert "python_requires in setup.py: >=3.8" in result.details

    @pytest.mark.parametrize(
        "call",
        [
            "setup(python_requires=minimum())\n",
            "kwargs = dict(name='x', **more)\nsetup(**kwargs)\n",
        ],
        ids=["call", "kwargs"],
    )
    def test_setup_py_computed(
        self, make_package: Callable[..., Path], call: str
    ) -> None:
        """A value that only exists when setup.py runs leaves nothing to read."""
        setup_py = "from setuptools import setup\n" + call
        package = make_package(pyproject=TOOL_ONLY, files={"setup.py": setup_py})
        result = run(package)
        assert skipped(result) == "not-applicable"
        assert "setup.py" in result.message
        assert "Add " not in text(result)

    def test_dynamic_is_skipped(self, make_package: Callable[..., Path]) -> None:
        package = make_package(
            requires_python=None, project='dynamic = ["requires-python"]'
        )
        assert skipped(run(package)) == "not-applicable"


class TestNothingDeclared:
    """A missing or unreadable constraint is a NOTE with a hint for the layout (C064).

    In a ``[project]`` table it is the metadata check's to report, and this
    check is skipped.
    """

    @pytest.mark.parametrize(
        ("options", "key", "place"),
        [
            (
                {"requires_python": None, "backend": "poetry"},
                "python",
                "[tool.poetry.dependencies] in pyproject.toml",
            ),
            (
                {"pyproject": TOOL_ONLY, "files": {"setup.py": "setup()\n"}},
                "python_requires",
                "setup.py",
            ),
            (
                {"pyproject": False, "files": {"setup.cfg": "[metadata]\n"}},
                "python_requires",
                "[options] in setup.cfg",
            ),
            (
                {
                    "pyproject": False,
                    "files": {"setup.py": "setup()\n", "setup.cfg": "[metadata]\n"},
                },
                "python_requires",
                "setup.py or [options] in setup.cfg",
            ),
        ],
        ids=["poetry", "setup.py", "setup.cfg", "both"],
    )
    def test_no_requires_python(
        self, make_package: Callable[..., Path], options: dict, key: str, place: str
    ) -> None:
        result = run(make_package(**options))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [MISSING]
        assert result.message == "No requires-python specified"
        assert result.details == []
        finding = result.findings[0]
        assert finding.message == f"No {key} in {place}"
        # The example is the oldest version that is still supported.
        # It is quoted where the file takes a quoted value.
        quote = "" if "setup.cfg" in place else '"'
        assert finding.hint == (
            f"Add {key} to {place}, for example {quote}>=3.10{quote}."
        )

    @pytest.mark.parametrize("python", ['"*"', '{version = "*"}'])
    def test_poetry_constraint_that_allows_every_version(
        self, make_package: Callable[..., Path], python: str
    ) -> None:
        """The key is there, so the user is told to change it, not to add it."""
        toml = '[tool.poetry]\nname = "x"\n[tool.poetry.dependencies]\npython = '
        result = run(make_package(pyproject=toml + python + "\n"))
        assert codes(result) == [MISSING]
        place = "[tool.poetry.dependencies] in pyproject.toml"
        finding = result.findings[0]
        assert finding.message == f"python in {place} sets no lowest version"
        assert finding.hint == f'Change python in {place}, for example ">=3.10".'
        assert codes(run(make_package(pyproject=toml + '">=3.10"\n'))) == []

    @pytest.mark.parametrize(
        ("options", "message", "owned", "code"),
        [
            (
                {"requires_python": None},
                "No requires-python specified",
                "a missing or invalid requires-python",
                "metadata.missing-recommended",
            ),
            (
                {"requires_python": None, "project": "requires-python = 3.10"},
                "No requires-python specified",
                "a missing or invalid requires-python",
                "metadata.invalid-value",
            ),
            (
                {"requires_python": "^3.9"},
                "Could not interpret requires-python",
                "the invalid requires-python",
                "metadata.invalid-value",
            ),
            (
                {"pyproject": TOOL_ONLY},
                "No [project] table",
                "the missing [project] table",
                "metadata.missing",
            ),
            # A setup.cfg that only configures tools holds no metadata.
            (
                {"pyproject": TOOL_ONLY, "files": {"setup.cfg": "[flake8]\n"}},
                "No [project] table",
                "the missing [project] table",
                "metadata.missing",
            ),
        ],
        ids=["missing", "not-a-string", "invalid", "no-table", "tool-cfg"],
    )
    def test_what_the_metadata_check_reports_is_skipped(
        self,
        make_package: Callable[..., Path],
        options: dict,
        message: str,
        owned: str,
        code: str,
    ) -> None:
        """One cause has one owner, so this check does not report it again."""
        package = make_package(**options)
        result = run(package)
        assert skipped(result) == "prerequisite-failed"
        assert result.message == message
        assert result.details[-1] == f"The metadata check reports {owned}"
        assert "Add " not in text(result)
        assert code in metadata_codes(package)

    def test_no_pyproject(self, empty_dir: Path) -> None:
        """The metadata check is the one that reports the missing file."""
        result = run(empty_dir)
        assert skipped(result) == "prerequisite-failed"
        assert result.message == "No pyproject.toml found"
        assert "metadata check" in result.details[-1]

    @pytest.mark.parametrize(
        ("options", "label", "specifier"),
        [
            (
                {"backend": "poetry", "requires_python": "three point ten"},
                "python in [tool.poetry.dependencies]",
                "three point ten",
            ),
            # Poetry 2: the [project] table leaves the constraint to Poetry.
            (
                {
                    "requires_python": None,
                    "extra": '[tool.poetry.dependencies]\npython = "three point ten"\n',
                },
                "python in [tool.poetry.dependencies]",
                "three point ten",
            ),
            (
                {
                    "pyproject": False,
                    "files": {"setup.cfg": "[options]\npython_requires = ^3.9\n"},
                },
                "python_requires in setup.cfg",
                "^3.9",
            ),
            (
                {
                    "pyproject": TOOL_ONLY,
                    "files": {"setup.py": 'setup(python_requires="3.9")\n'},
                },
                "python_requires in setup.py",
                "3.9",
            ),
        ],
        ids=["poetry", "poetry-2", "setup.cfg", "setup.py"],
    )
    def test_unreadable_specifier_is_neutral(
        self,
        make_package: Callable[..., Path],
        options: dict,
        label: str,
        specifier: str,
    ) -> None:
        """An invalid specifier is never reported as current (C022).

        The metadata check does not judge a constraint from these sources,
        so the NOTE is this check's own.
        """
        package = make_package(**options)
        result = run(package)
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [INVALID]
        assert specifier in result.findings[0].message
        assert "eol" not in result.message.lower()
        assert f"{label}: {specifier}" in result.details
        # The hint names the key the user wrote, with a value its file takes.
        quote = "" if "setup.cfg" in label else '"'
        assert result.findings[0].hint == (
            f"Write {label} as a version specifier, for example {quote}>=3.10{quote}."
        )
        assert "metadata.invalid-value" not in metadata_codes(package)

    def test_unparseable_pyproject_is_skipped(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Only the metadata check reports the broken file (C003)."""
        result = run(make_package(pyproject="[project\nname = 'x'"))
        assert skipped(result) == "prerequisite-failed"
        assert "metadata check" in result.details[-1]


class TestHints:
    """Doing what a hint says makes its rule stop firing."""

    @pytest.mark.parametrize(
        ("code", "options", "followed", "said"),
        [
            (
                EOL,
                {"backend": "poetry", "requires_python": "^3.8"},
                {"backend": "poetry", "requires_python": "^3.10"},
                "python in [tool.poetry.dependencies] allows to 3.10.",
            ),
            (
                MISSING,
                only("setup.py", "setup()\n"),
                only("setup.py", 'setup(python_requires=">=3.10")\n'),
                'Add python_requires to setup.py, for example ">=3.10".',
            ),
        ],
        ids=["eol-poetry", "setup.py"],
    )
    def test_following_the_hint_clears_the_rule(
        self,
        make_package: Callable[..., Path],
        code: str,
        options: dict,
        followed: dict,
        said: str,
    ) -> None:
        """*followed* writes the key and the value that the hint names."""
        result = run(make_package(**options))
        assert codes(result) == [code]
        assert (result.findings[0].hint or "").endswith(said)
        assert codes(run(make_package(**followed))) == []
