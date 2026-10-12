"""Tests for version consistency check."""

from collections.abc import Callable
from functools import partial
from pathlib import Path

import pytest

from pycmdcheck.checks.version import VersionCheck
from pycmdcheck.results import CheckResult, CheckStatus
from tests import helpers
from tests.helpers import skipped

DYNAMIC_AFTER_PLACEHOLDER = (
    '__version__ = "unknown"\n'
    "try:\n"
    "    from importlib.metadata import version\n"
    '    __version__ = version("mypackage")\n'
    "except Exception:\n"
    "    pass\n"
)
MISMATCH = "version.mismatch"
# Other names are assigned too, as in most __init__.py files.
OTHER_NAMES = (
    '__all__ = ["f"]\n__author__ = "A"\ncount = 0\ncount += 1\n__version__ = "2.0.0"\n'
)
TWO_LITERALS = (
    'import sys\nif sys.argv:\n    __version__ = "1"\nelse:\n    __version__ = "2"\n'
)
# What a file with no __version__ to compare gives: skip reason, message and
# the start of the detail line about the file.
NO_VERSION = (
    "not-applicable",
    "No __version__ in code, nothing to compare",
    "src/mypackage/__init__.py assigns no __version__ at module level",
)
UNPARSED = (
    "parse-failed",
    "Could not read __version__ from code",
    "Could not parse 1 file(s), skipped: src/mypackage/__init__.py (syntax error",
)
# What a __version__ that is not one string gives: message and detail line.
AT_RUNTIME = (
    "Version set dynamically (e.g. importlib.metadata)",
    "__version__ in src/mypackage/__init__.py is set at runtime",
)
NOT_A_STRING = (
    "__version__ is not a string",
    "__version__ in src/mypackage/__init__.py has to be written as a string, "
    'such as "1.0", to be compared',
)


def run(package: Path) -> CheckResult:
    return VersionCheck().run(package, {})


# The codes of the findings, which must agree with the result and the rules.
codes = partial(helpers.codes, rules=VersionCheck.rules)


class TestVersionCheck:
    """Tests for VersionCheck."""

    def test_versions_match(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(version="1.0.0"))
        assert result.status == CheckStatus.OK
        assert codes(result) == []
        # The inspected file is named, relative to the project (C008).
        assert "__version__ in src/mypackage/__init__.py: 1.0.0" in result.details

    def test_versions_mismatch(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(version="1.0.0", init='__version__ = "2.0.0"\n'))
        assert result.status == CheckStatus.ERROR
        assert codes(result) == [MISMATCH]
        assert result.details == [
            "pyproject.toml: 1.0.0",
            "__version__ in src/mypackage/__init__.py: 2.0.0",
        ]
        # The finding is one line that names both versions and the file.
        found = result.findings[0].message
        assert all(part in found for part in ("1.0.0", "2.0.0", "__init__.py"))

    @pytest.mark.parametrize(
        "init",
        [
            '__version__ = "1.0.0"\n',
            'from importlib.metadata import version\n__version__ = version("x")\n',
        ],
        ids=["same", "importlib.metadata"],
    )
    def test_following_the_hint_clears_the_rule(
        self, make_package: Callable[..., Path], init: str
    ) -> None:
        """Both ways the hint names leave no mismatch to report."""
        before = make_package(version="1.0.0", init='__version__ = "2.0.0"\n')
        assert codes(run(before)) == [MISMATCH]
        assert VersionCheck.hints[MISMATCH] == (
            "Make __version__ in the code and version in pyproject.toml the same, "
            "or read __version__ with importlib.metadata.version()."
        )
        after = run(make_package(version="1.0.0", init=init))
        assert after.findings == []

    @pytest.mark.parametrize(
        ("declared", "in_code"),
        [("1.0", "1.0.0"), ("1.0.0rc1", "1.0.0-rc.1"), ("banana", "banana")],
    )
    def test_pep440_equal_versions_match(
        self, make_package: Callable[..., Path], declared: str, in_code: str
    ) -> None:
        """Versions are compared as PEP 440 versions, then as text (C066)."""
        package = make_package(version=declared, init=f'__version__ = "{in_code}"\n')
        assert run(package).status == CheckStatus.OK

    def test_dynamic_version_skips(self, make_package: Callable[..., Path]) -> None:
        package = make_package(version=None, project='dynamic = ["version"]')
        assert skipped(run(package)) == "not-applicable"

    @pytest.mark.parametrize(
        ("init", "expected"),
        [
            ("", NO_VERSION),
            ('"""Docs."""\n', NO_VERSION),
            ("def broken(\n", UNPARSED),
            ("def f():\n    __version__ = '9'\n", NO_VERSION),
            ("class Meta:\n    __version__ = '9.9.9'\n", NO_VERSION),
        ],
        ids=["empty", "docstring", "syntax-error", "inside-function", "inside-class"],
    )
    def test_no_version_in_code_is_skipped(
        self, make_package: Callable[..., Path], init: str, expected: tuple[str, ...]
    ) -> None:
        """No module-level __version__ means nothing to compare (C012).

        A file that could not be parsed was not read, and the reason says so.
        """
        reason, message, detail = expected
        result = run(make_package(version="1.0.0", init=init))
        assert skipped(result) == reason
        assert result.message == message
        assert result.details[0] == "pyproject.toml version: 1.0.0"
        assert result.details[1].startswith(detail)
        assert len(result.details) == 2
        assert not any("consider adding" in d.lower() for d in result.details)

    def test_no_pyproject(self, empty_dir: Path) -> None:
        """The metadata check is the one that reports the missing file."""
        result = run(empty_dir)
        assert skipped(result) == "prerequisite-failed"
        assert result.message == "No pyproject.toml found"
        assert "metadata check" in result.details[-1]

    @pytest.mark.parametrize(
        ("init", "expected"),
        [
            (
                'from importlib.metadata import version\n__version__ = version("x")\n',
                AT_RUNTIME,
            ),
            ("from ._version import __version__\n", AT_RUNTIME),
            ("from ._version import VERSION\n__version__ = VERSION\n", AT_RUNTIME),
            ('__version__ = f"{1}.0"\n', AT_RUNTIME),
            (DYNAMIC_AFTER_PLACEHOLDER, AT_RUNTIME),
            (TWO_LITERALS, AT_RUNTIME),
            ('__version__ = "1.0"\n__version__ += ".0"\n', AT_RUNTIME),
            # written out in the file, but 1.10 is 1.1 as a number
            ("__version__ = 1.0\n", NOT_A_STRING),
            ("__version__ = (1, 0)\n", NOT_A_STRING),
            ('__version__ = (1, 0, "dev")\n', NOT_A_STRING),
            ("__version__ = None\n", NOT_A_STRING),
            ("MAJOR = 1\n__version__ = (MAJOR, 0)\n", AT_RUNTIME),
            # a value that is not written out wins over a number
            ("__version__ = VERSION\n__version__ = 1.0\n", AT_RUNTIME),
            ('__version__ = "1.0"\n__version__ = 1.0\n', AT_RUNTIME),
        ],
        ids=[
            "call",
            "import",
            "name",
            "f-string",
            "placeholder",
            "two-literals",
            "augmented",
            "number",
            "tuple",
            "tuple-with-text",
            "none",
            "tuple-with-a-name",
            "name-and-number",
            "string-and-number",
        ],
    )
    def test_version_set_at_runtime_is_skipped(
        self, make_package: Callable[..., Path], init: str, expected: tuple[str, str]
    ) -> None:
        """A __version__ that is not one string literal cannot be compared (C066).

        A value written out in the file that is not a string, such as a number
        or a tuple, is not set at run time, and the message does not say that
        it is.
        """
        message, detail = expected
        result = run(make_package(version="1.0.0", init=init))
        assert skipped(result) == "not-applicable"
        assert result.message == message
        assert result.details == ["pyproject.toml version: 1.0.0", detail]

    @pytest.mark.parametrize(
        "init",
        [
            'try:\n    __version__ = "2.0.0"\nfinally:\n    pass\n',
            OTHER_NAMES,
            '__version__: str = "2.0.0"\n',
            '__version__ = "banana"\n',
        ],
        ids=["try-block", "other-names", "annotated", "not-pep440"],
    )
    def test_literal_is_compared(
        self, make_package: Callable[..., Path], init: str
    ) -> None:
        result = run(make_package(version="1.0.0", init=init))
        assert result.status == CheckStatus.ERROR
        assert codes(result) == [MISMATCH]

    def test_single_module_version(self, make_package: Callable[..., Path]) -> None:
        """Single-module package (no package dir) is checked (#17)."""
        result = run(make_package(version="1.0.0", layout="module"))
        assert result.status == CheckStatus.OK
        assert "__version__ in mypackage.py: 1.0.0" in result.details

    def test_legacy_poetry_version_checked(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Legacy Poetry version (under [tool.poetry]) is compared (#19)."""
        package = make_package(version="1.0.0", backend="poetry", layout="flat")
        assert run(package).status == CheckStatus.OK

    def test_pyproject_without_project_table(self, tmp_path: Path) -> None:
        """pyproject present but no [project]/[tool.poetry] -> SKIPPED (#19)."""
        (tmp_path / "pyproject.toml").write_text(
            '[build-system]\nrequires = ["setuptools"]\n'
        )
        result = run(tmp_path)
        assert skipped(result) == "prerequisite-failed"
        assert "project" in result.message.lower()
        assert "metadata check" in result.details[-1]


class TestPackageSelection:
    """The package that belongs to the project is the one inspected (C008, C010)."""

    def test_project_name_is_normalised(
        self, make_package: Callable[..., Path]
    ) -> None:
        """A helper package with its own __version__ is not picked."""
        root = make_package(
            "PyFoo",
            version="1.0.0",
            layout="none",
            files={
                "examples/__init__.py": '__version__ = "0.0.1"\n',
                "pyfoo/__init__.py": '__version__ = "2.0.0"\n',
            },
        )
        result = run(root)
        assert result.status == CheckStatus.ERROR
        assert codes(result) == [MISMATCH]
        assert "__version__ in pyfoo/__init__.py: 2.0.0" in result.details

    def test_namespace_package(self, make_package: Callable[..., Path]) -> None:
        root = make_package(
            "poetry-core",
            version="2.5.0",
            layout="none",
            files={"src/poetry/core/__init__.py": '__version__ = "2.5.0"\n'},
        )
        assert run(root).status == CheckStatus.OK

    @pytest.mark.parametrize(
        ("files", "message"),
        [
            ({}, "No __init__.py or module found to check __version__"),
            (
                {"aaa/__init__.py": '__version__ = "9"\n', "bbb/__init__.py": ""},
                "Could not tell which package holds __version__",
            ),
        ],
        ids=["no-module", "ambiguous"],
    )
    def test_no_package_to_inspect_is_skipped(
        self, make_package: Callable[..., Path], files: dict[str, str], message: str
    ) -> None:
        root = make_package(version="1.0.0", layout="none", tests=False, files=files)
        result = run(root)
        assert skipped(result) == "not-applicable"
        assert result.message == message


class TestMessyMetadata:
    """Odd pyproject.toml content never makes the check raise (C003, C065)."""

    def test_unparseable_pyproject_is_skipped(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Only the metadata check reports the broken file."""
        result = run(make_package(pyproject="[project\nname = 'x'"))
        assert skipped(result) == "prerequisite-failed"
        assert "metadata check" in result.details[-1]

    @pytest.mark.parametrize("project", ["dynamic = 5", 'dynamic = "conversion"'])
    def test_wrong_typed_dynamic(
        self, make_package: Callable[..., Path], project: str
    ) -> None:
        assert run(make_package(project=project)).status == CheckStatus.OK

    def test_non_string_name(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").write_text('[project]\nname = 5\nversion = "1"\n')
        (tmp_path / "mod.py").write_text('__version__ = "1"\n')
        assert run(tmp_path).status == CheckStatus.OK

    @pytest.mark.parametrize("version", ["version = 1.0", 'version = ""', ""])
    def test_no_static_version_is_skipped(
        self, make_package: Callable[..., Path], version: str
    ) -> None:
        """A missing version is the metadata check's finding (C064)."""
        result = run(make_package(version=None, project=version))
        assert skipped(result) == "prerequisite-failed"
        assert "metadata check" in result.details[-1]
