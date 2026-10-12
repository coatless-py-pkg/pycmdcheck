"""Tests for py.typed PEP 561 marker check."""

from pathlib import Path
from typing import Any

import pytest

from pycmdcheck.checks import py_typed
from pycmdcheck.checks.py_typed import PyTypedCheck
from pycmdcheck.results import CheckResult, CheckStatus
from tests.helpers import write_files

TYPED = "def foo(x: int) -> str: ...\n"
UNTYPED = "def foo(x): ...\n"
NOT_NEEDED = "py.typed not needed (package is not clearly typed)"
MISSING = "Missing py.typed marker for typed package"
# What each outcome carries: status, message, skip reason, finding codes.
OK_MARKED = (CheckStatus.OK, "py.typed marker present", None, [])
NOTE_MISSING = (CheckStatus.NOTE, MISSING, None, ["py_typed.missing"])
SKIP_UNTYPED = (CheckStatus.SKIPPED, NOT_NEEDED, "not-applicable", [])


def project(name: str, extra: str = "") -> str:
    """Return a pyproject.toml that names the project."""
    return f'[project]\nname = "{name}"\nversion = "0.1.0"\n{extra}'


def run(root: Path, files: dict[str, str]) -> CheckResult:
    """Write *files* under *root* and run the py.typed check there."""
    write_files(root, files)
    return PyTypedCheck().run(root, {})


def outcome(result: CheckResult) -> tuple[CheckStatus, str, str | None, list[str]]:
    """Return the status, message, skip reason and finding codes of *result*."""
    codes = [finding.code for finding in result.findings]
    return result.status, result.message, result.skip_reason, codes


class TestPyTypedCheck:
    """Tests for PyTypedCheck."""

    def test_marker_present_with_types(self, tmp_path: Path) -> None:
        result = run(
            tmp_path, {"src/mypkg/__init__.py": TYPED, "src/mypkg/py.typed": ""}
        )
        assert outcome(result) == OK_MARKED
        assert result.details == [
            "Package directory: src/mypkg",
            "py.typed marker file present",
        ]

    def test_marker_without_annotations_ok(self, tmp_path: Path) -> None:
        result = run(
            tmp_path, {"src/mypkg/__init__.py": "x = 1\n", "src/mypkg/py.typed": ""}
        )
        assert outcome(result) == OK_MARKED

    def test_marker_missing_with_types(self, tmp_path: Path) -> None:
        result = run(tmp_path, {"src/mypkg/__init__.py": TYPED})
        assert outcome(result) == NOTE_MISSING
        assert result.details == [
            "Package directory: src/mypkg",
            "Scanned 1 of 1 files: 1 of 1 public functions are annotated",
        ]
        assert result.findings[0].status == CheckStatus.NOTE
        assert result.findings[0].message == (
            "src/mypkg has no py.typed marker and 1 of 1 public functions are annotated"
        )

    def test_following_the_hint_clears_the_rule(self, tmp_path: Path) -> None:
        result = run(tmp_path, {"src/mypkg/__init__.py": TYPED})
        assert result.findings[0].hint == (
            "Add an empty file named py.typed to src/mypkg."
        )
        assert outcome(run(tmp_path, {"src/mypkg/py.typed": ""})) == OK_MARKED

    def test_untyped_package_is_skipped(self, tmp_path: Path) -> None:
        """No marker is expected of a package that is not clearly typed."""
        result = run(tmp_path, {"src/mypkg/__init__.py": "x = 1\n"})
        assert outcome(result) == SKIP_UNTYPED
        assert result.details == [
            "Package directory: src/mypkg",
            "Scanned 1 of 1 files: 0 of 0 public functions are annotated",
        ]

    def test_no_package_found(self, empty_dir: Path) -> None:
        result = PyTypedCheck().run(empty_dir, {})
        assert outcome(result) == (
            CheckStatus.SKIPPED,
            "No package directory found",
            "not-applicable",
            [],
        )
        assert result.details[0].startswith("Looked in the project root, src/")

    def test_single_module_is_skipped(self, tmp_path: Path) -> None:
        result = run(tmp_path, {"pyproject.toml": project("six"), "six.py": TYPED})
        assert outcome(result) == (
            CheckStatus.SKIPPED,
            "No package directory found",
            "not-applicable",
            [],
        )
        assert "single-file module" in result.details[0]


# Marker files that 0.1.1 missed: files and the directory that must be inspected.
MARKED = {
    "xarray: a test package sorts first": (
        {
            "pyproject.toml": project("xarray"),
            "properties/__init__.py": TYPED,
            "xarray/__init__.py": TYPED,
            "xarray/py.typed": "",
        },
        "xarray",
    ),
    "name in mixed case": (
        {
            "pyproject.toml": project("MyPkg"),
            "benchmarks/__init__.py": TYPED,
            "mypkg/__init__.py": TYPED,
            "mypkg/py.typed": "",
        },
        "mypkg",
    ),
    "no name: any package with the marker counts": (
        {"alpha/__init__.py": UNTYPED, "zeta/__init__.py": TYPED, "zeta/py.typed": ""},
        "zeta",
    ),
    "poetry-core: namespace package": (
        {
            "pyproject.toml": project("poetry-core"),
            "src/poetry/core/__init__.py": TYPED,
            "src/poetry/core/py.typed": "",
        },
        "src/poetry/core",
    ),
    "matplotlib: lib/ layout": (
        {
            "pyproject.toml": project("matplotlib"),
            "lib/matplotlib/__init__.py": TYPED,
            "lib/matplotlib/py.typed": "",
        },
        "lib/matplotlib",
    ),
    "pydantic-core: maturin python-source": (
        {
            "pyproject.toml": project(
                "pydantic-core", '[tool.maturin]\npython-source = "python"\n'
            ),
            "src/lib.rs": "",
            "python/pydantic_core/__init__.py": TYPED,
            "python/pydantic_core/py.typed": "",
        },
        "python/pydantic_core",
    ),
    "tiktoken: package beside a Rust src/": (
        {
            "pyproject.toml": project("tiktoken"),
            "src/lib.rs": "",
            "tiktoken/__init__.py": TYPED,
            "tiktoken/py.typed": "",
        },
        "tiktoken",
    ),
}


@pytest.mark.parametrize("label", MARKED)
def test_marker_is_found_in_the_projects_package(tmp_path: Path, label: str) -> None:
    files, directory = MARKED[label]
    result = run(tmp_path, files)
    assert outcome(result) == OK_MARKED
    assert result.details[0] == f"Package directory: {directory}"


def test_stub_only_package_needs_no_marker(tmp_path: Path) -> None:
    """A stub-only package follows PEP 561 as it is, which is a pass."""
    result = run(
        tmp_path,
        {"pyproject.toml": project("foo-stubs"), "foo-stubs/__init__.pyi": TYPED},
    )
    assert outcome(result) == (
        CheckStatus.OK,
        "py.typed not needed (stub-only package)",
        None,
        [],
    )


def test_guessed_package_is_not_named_in_the_advice(tmp_path: Path) -> None:
    result = run(tmp_path, {"alpha/__init__.py": TYPED, "zeta/__init__.py": TYPED})
    assert outcome(result) == NOTE_MISSING
    # The check sets no hint, so the general one from its hints is used.
    assert result.findings[0].hint is None
    assert result.findings[0].message == (
        "The package has no py.typed marker and 1 of 1 public functions are annotated"
    )
    # A marker in either package follows the general hint.
    assert outcome(run(tmp_path, {"zeta/py.typed": ""})) == OK_MARKED


# Sources of one package: files, annotated public functions, all public functions.
RATIOS = {
    "one dataclass field": (
        {
            "__init__.py": "from dataclasses import dataclass\n\n"
            "@dataclass\nclass C:\n    x: int = 0\n\ndef f(a): ...\ndef g(a): ...\n"
        },
        (0, 2),
    ),
    "typing import only": (
        {"__init__.py": "from typing import NamedTuple\n\ndef f(a): ...\n"},
        (0, 1),
    ),
    "annotations only in tests": (
        {"__init__.py": UNTYPED, "tests/test_f.py": "def test_f() -> None: ...\n"},
        (0, 1),
    ),
    "private functions do not count": (
        {"__init__.py": "def _f(a: int) -> int: ...\ndef g(a): ...\n"},
        (0, 1),
    ),
    "just under half": (
        {"__init__.py": TYPED + "def g(a): ...\ndef h(a): ...\n"},
        (1, 3),
    ),
    "half": (
        {
            "__init__.py": TYPED + "def g(a): ...\n",
            "m.py": "def h(*a: int): ...\ndef k(a): ...\n",
        },
        (2, 4),
    ),
    "stub beside an untyped module": (
        {"__init__.py": UNTYPED, "core.py": UNTYPED, "core.pyi": TYPED},
        (1, 2),
    ),
    "async function with a keyword-only parameter": (
        {"__init__.py": "async def f(*, key: int): ...\n"},
        (1, 1),
    ),
    "methods of a class": (
        {
            "__init__.py": "class C:\n"
            "    def f(self) -> int: ...\n    def g(self, a): ...\n"
        },
        (1, 2),
    ),
}


@pytest.mark.parametrize("label", RATIOS)
def test_marker_is_suggested_only_for_a_mostly_annotated_package(
    tmp_path: Path, label: str
) -> None:
    sources, (annotated, total) = RATIOS[label]
    files = {f"src/mypkg/{name}": text for name, text in sources.items()}
    result = run(tmp_path, files)
    typed = annotated * 2 >= total
    assert outcome(result) == (NOTE_MISSING if typed else SKIP_UNTYPED)
    assert f"{annotated} of {total} public functions are annotated" in result.details[1]


def test_sample_is_spread_over_the_package_and_disclosed(tmp_path: Path) -> None:
    """The 20 untyped modules that sort first must not decide for the 40 typed."""
    files = {f"src/mypkg/a{i:02}.py": UNTYPED for i in range(20)}
    files.update({f"src/mypkg/z{i:02}.py": TYPED for i in range(40)})
    files["src/mypkg/__init__.py"] = ""
    result = run(tmp_path, files)
    assert outcome(result) == NOTE_MISSING
    assert result.details[1] == (
        "Scanned 20 of 61 files: 13 of 19 public functions are annotated"
    )


def test_unparsed_file_is_skipped_not_graded(tmp_path: Path) -> None:
    result = run(
        tmp_path, {"src/mypkg/__init__.py": TYPED, "src/mypkg/broken.py": "def f(:\n"}
    )
    assert outcome(result) == NOTE_MISSING
    assert result.details[2].startswith(
        "Could not parse 1 file(s), skipped: src/mypkg/broken.py (syntax error"
    )


@pytest.mark.parametrize(
    ("init", "left_out"),
    [("def f(:\n", 2), ("", 1)],
    ids=["no file parses", "the file that parses has no function"],
)
def test_no_function_counted_and_a_file_left_out_is_not_called_untyped(
    tmp_path: Path, init: str, left_out: int
) -> None:
    result = run(
        tmp_path, {"src/mypkg/__init__.py": init, "src/mypkg/m.py": "def g(:\n"}
    )
    assert outcome(result) == (
        CheckStatus.SKIPPED,
        "Could not parse the package source",
        "parse-failed",
        [],
    )
    assert result.details[0] == "Package directory: src/mypkg"
    assert result.details[1].startswith(
        f"Could not parse {left_out} file(s), skipped: src/mypkg/"
    )
    assert len(result.details) == 2


def test_trees_are_not_kept(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict[str, Any]] = []
    real = py_typed.parse_file

    def spy(path: Path, **kwargs: Any) -> Any:
        calls.append(kwargs)
        return real(path, **kwargs)

    monkeypatch.setattr(py_typed, "parse_file", spy)
    run(tmp_path, {"src/mypkg/__init__.py": TYPED, "src/mypkg/m.py": UNTYPED})
    assert calls == [{"cache": False}, {"cache": False}]
