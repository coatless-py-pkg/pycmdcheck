"""Tests for the test suite check."""

import ast
import os
from collections.abc import Callable
from pathlib import Path

import pytest

from pycmdcheck.checks import test_suite
from pycmdcheck.checks import tests as tests_check
from pycmdcheck.checks.test_suite import TestSuiteCheck, find_tests
from pycmdcheck.checks.tests import TestsCheck
from pycmdcheck.results import CheckResult, CheckStatus
from tests.helpers import codes


def run(package: Path) -> CheckResult:
    return TestSuiteCheck().run(package, {})


class TestSuiteFound:
    """Each place a project can keep its tests."""

    @pytest.mark.parametrize(
        ("files", "found"),
        [
            ({"tests/test_a.py": "def test_a(): pass\n"}, "tests/"),
            ({"test/test_a.py": "def test_a(): pass\n"}, "test/"),
            ({"Tests/unit/test_a.py": ""}, "Tests/"),
            ({"src/mypackage/tests/test_a.py": ""}, "src/mypackage/tests/"),
            ({"test_mypackage.py": ""}, "test_mypackage.py"),
            ({"mypackage_test.py": ""}, "mypackage_test.py"),
            # Kept deeper inside the package, as numpy and scipy do.
            (
                {
                    "src/mypackage/sub/__init__.py": "",
                    "src/mypackage/sub/tests/test_a.py": "",
                },
                "src/mypackage/sub/tests/test_a.py",
            ),
            (
                {
                    "src/mypackage/sub/__init__.py": "",
                    "src/mypackage/sub/test_a.py": "",
                },
                "src/mypackage/sub/test_a.py",
            ),
            # A Django application keeps its tests in tests.py.
            ({"src/mypackage/tests.py": "import unittest\n"}, "src/mypackage/tests.py"),
            # A tests directory may hold unittest modules under any name.
            ({"tests/basic.py": "import unittest\n"}, "tests/"),
            # Places a test runner still collects from.
            ({"src/tests/test_core.py": ""}, "src/tests/test_core.py"),
            ({"unit_tests/test_a.py": ""}, "unit_tests/test_a.py"),
            ({"test.py": "import unittest\n"}, "test.py"),
            ({"tests.py": "import unittest\n"}, "tests.py"),
            # testpaths in the other files pytest reads its settings from.
            (
                {
                    "testing/test_one.py": "",
                    "pytest.ini": "[pytest]\ntestpaths = testing",
                },
                "testing/",
            ),
            (
                {"testing/test_one.py": "", "tox.ini": "[pytest]\ntestpaths = testing"},
                "testing/",
            ),
            (
                {
                    "testing/test_one.py": "",
                    "setup.cfg": "[tool:pytest]\ntestpaths = docs testing\n",
                },
                "testing/",
            ),
        ],
    )
    def test_found(
        self,
        make_package: Callable[..., Path],
        files: dict[str, str],
        found: str,
    ) -> None:
        result = run(make_package(tests=False, files=files))
        assert result.status == CheckStatus.OK, result.details
        assert result.message == "Test suite present"
        assert result.details == [f"Found tests: {found}"]
        assert result.findings == []

    @pytest.mark.parametrize("value", ['["testing"]', '"testing"'], ids=["list", "str"])
    def test_testpaths_of_the_pytest_configuration(
        self, make_package: Callable[..., Path], value: str
    ) -> None:
        package = make_package(
            tests=False,
            extra=f"[tool.pytest.ini_options]\ntestpaths = {value}\n",
            files={"testing/check_a.py": "def test_a(): pass\n"},
        )
        assert run(package).details == ["Found tests: testing/"]

    def test_testpaths_in_the_table_pytest_9_reads(
        self, make_package: Callable[..., Path]
    ) -> None:
        package = make_package(
            tests=False,
            extra='[tool.pytest]\ntestpaths = ["checks/unit"]\n',
            files={"checks/unit/check_a.py": "def test_a(): pass\n"},
        )
        assert run(package).details == ["Found tests: checks/unit/"]


class TestSuiteMissing:
    """A project with nothing a test runner would pick up."""

    def test_no_tests_is_a_warning(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(tests=False))
        assert result.status == CheckStatus.WARNING
        assert result.message == "No tests found"
        assert codes(result) == ["test_suite.missing"]
        assert "tests/ or test/" in result.details[0]

    @pytest.mark.parametrize(
        "files",
        [
            {"tests/__init__.py": ""},
            {"tests/conftest.py": "", "tests/_helpers.py": ""},
            {"tests/data/expected.json": "{}"},
            {"tests/.gitkeep": ""},
            {"src/mypackage/tests/__init__.py": ""},
            {"src/mypackage/conftest.py": ""},
            # A package's testing helpers are part of the package.
            {
                "src/mypackage/testing/__init__.py": "",
                "src/mypackage/testing/asserts.py": "",
            },
            # Somebody else's tests, in an environment kept inside the project.
            {"venv/lib/python3.12/site-packages/other/test_other.py": ""},
            {"node_modules/tool/test_tool.py": ""},
        ],
    )
    def test_a_tests_directory_with_no_python_file_is_not_a_suite(
        self, make_package: Callable[..., Path], files: dict[str, str]
    ) -> None:
        result = run(make_package(tests=False, files=files))
        assert codes(result) == ["test_suite.missing"]

    def test_testpaths_naming_the_root_is_not_a_test_directory(
        self, make_package: Callable[..., Path]
    ) -> None:
        """The package's own modules are not a suite, so test files decide."""
        package = make_package(
            tests=False, extra='[tool.pytest.ini_options]\ntestpaths = ["."]\n'
        )
        assert codes(run(package)) == ["test_suite.missing"]
        (package / "test_one.py").write_text("")
        assert run(package).details == ["Found tests: test_one.py"]

    def test_empty_directory(self, tmp_path: Path) -> None:
        assert codes(run(tmp_path)) == ["test_suite.missing"]

    def test_following_the_hint_clears_the_rule(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Doing what the hint says makes the rule stop firing."""
        package = make_package(tests=False)
        assert codes(run(package)) == ["test_suite.missing"]
        hint = TestSuiteCheck.hints["test_suite.missing"]
        assert "Add a tests/ directory" in hint
        assert "such as tests/test_basic.py" in hint
        (package / "tests").mkdir()
        (package / "tests" / "test_basic.py").write_text("def test_basic(): pass\n")
        assert codes(run(package)) == []

    def test_a_linked_tests_directory_is_not_followed(
        self, make_package: Callable[..., Path], tmp_path: Path
    ) -> None:
        """A link could point anywhere, so the check stays inside the project."""
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "test_a.py").write_text("")
        package = make_package(tests=False)
        try:
            os.symlink(outside, package / "tests", target_is_directory=True)
        except OSError:
            pytest.skip("cannot create a symbolic link here")
        assert codes(run(package)) == ["test_suite.missing"]

    def test_testpaths_are_names_and_not_patterns(
        self, make_package: Callable[..., Path]
    ) -> None:
        """A wildcard entry would walk the whole project once for each copy."""
        package = make_package(
            tests=False,
            extra='[tool.pytest.ini_options]\ntestpaths = ["**/checks", "*"]\n',
            files={"deep/checks/check_a.py": ""},
        )
        assert find_tests(package) is None

    def test_testpaths_that_leave_the_project_are_ignored(
        self, make_package: Callable[..., Path]
    ) -> None:
        package = make_package(
            tests=False,
            extra='[tool.pytest.ini_options]\ntestpaths = ["../elsewhere", "/tmp"]\n',
        )
        assert find_tests(package) is None


class TestOneOwner:
    """The static check owns the finding, so the tests check adds none."""

    def test_both_checks_use_one_finder(self) -> None:
        """They cannot disagree about whether a project has tests."""
        assert tests_check.find_tests is test_suite.find_tests
        assert "tests.missing" not in TestsCheck.rules


def test_the_module_cannot_start_a_process() -> None:
    """The check runs on repositories nobody trusts, so it only reads files."""
    tree = ast.parse(Path(test_suite.__file__).read_text("utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert not {
        name
        for name in imported
        if name.split(".")[0] == "subprocess"
        or name in ("pycmdcheck.subprocess_runner", "pycmdcheck.checks.tests")
    }
