"""Test suite presence check.

This module provides the TestSuiteCheck class, which looks for a project's
tests without running them. The tests check runs the suite and uses the
same finder, so the two always agree on whether there is anything to run.
Nothing here starts a process, which keeps the check safe on a repository
that was only cloned.
"""

import os
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.fileio import (
    display_path,
    find_declared_file,
    find_declared_files,
    find_subdir,
    is_safe_relative,
    read_ini,
)
from pycmdcheck.package_layout import PackageLayout
from pycmdcheck.pyproject_reader import get_str, get_str_list, get_table
from pycmdcheck.results import CheckResult, CheckStatus, Finding

# The files pytest reads its settings from, with the section that holds them.
_PYTEST_INI_FILES = (
    ("pytest.ini", "pytest"),
    ("tox.ini", "pytest"),
    ("setup.cfg", "tool:pytest"),
)
# The most testpaths entries that are looked at. A project names a few.
_MAX_TESTPATHS = 20
# Directories that hold other projects' code, whose tests are not this
# project's tests.
_NOT_THE_PROJECT = frozenset({"site-packages", "node_modules"})


def pytest_ini_option(package_path: Path, option: str) -> list[str]:
    """Return a pytest setting of the project, split into words.

    Reads ``[tool.pytest.ini_options]`` in pyproject.toml, the ``[tool.pytest]``
    table that pytest 9 reads, then pytest.ini, tox.ini and setup.cfg, and
    returns the first value that is set.
    """
    values = []
    for keys in (("tool", "pytest", "ini_options"), ("tool", "pytest")):
        table = get_table(package_path, *keys)
        values.append(
            " ".join(get_str_list(table, option)) or get_str(table, option, "")
        )
    for name, section in _PYTEST_INI_FILES:
        parser = read_ini(package_path / name)
        if parser is not None:
            values.append(parser.get(section, option, fallback=""))
    return next((value.split() for value in values if value.strip()), [])


def _holds_tests(directory: Path) -> bool:
    """Return whether *directory* holds a test module, at any depth.

    A test module is a Python file whose name does not start with an
    underscore and is not ``conftest.py``. A directory with nothing but
    ``__init__.py``, shared fixtures or data files holds none.
    """
    modules = find_declared_files(directory, ["**/[!_]*.py"])
    return any(path.name != "conftest.py" for path in modules)


def find_tests(package_path: Path) -> Path | None:
    """Return where the project keeps its tests, or ``None``.

    Looked for, in this order: a ``tests`` or ``test`` directory at the
    project root or inside the package, a directory named by the
    ``testpaths`` of the pytest configuration, a file named ``test_*.py``,
    ``*_test.py``, ``test.py`` or ``tests.py`` at the root, and a file named
    ``test_*.py``, ``*_test.py`` or ``tests.py`` anywhere in the project. A
    directory counts only when it holds a test module, and a symbolic link
    to a directory is not followed.
    """
    bases = [package_path, *PackageLayout(package_path).package_dirs]
    candidates = [(base, name) for base in bases for name in ("tests", "test")]
    testpaths = dict.fromkeys(pytest_ini_option(package_path, "testpaths"))
    candidates += [
        (package_path, entry)
        for entry in list(testpaths)[:_MAX_TESTPATHS]
        if is_safe_relative(entry)
    ]
    for base, name in candidates:
        directory = find_subdir(base, name)
        if directory is not None and _holds_tests(directory):
            return directory
    at_root = find_declared_file(
        package_path, ["test_*.py", "*_test.py", "test.py", "tests.py"]
    )
    if at_root is not None:
        return at_root
    anywhere = find_declared_files(
        package_path, ["**/test_*.py", "**/*_test.py", "**/tests.py"], limit=200
    )
    for path in anywhere:
        if not _NOT_THE_PROJECT.intersection(path.relative_to(package_path).parts):
            return path
    return None


class TestSuiteCheck(BaseCheck):
    """Check that the project has a test suite.

    Looks for test directories and test files and reports where they are.
    The check is about presence only: it does not run the tests, and it does
    not read what they cover. The tests check runs them.

    Attributes:
        name: The check identifier ("test_suite").
        description: Human-readable description of this check.
        rules: The codes this check can report.
        hints: What to do about each code.

    Examples:
        >>> from pathlib import Path
        >>> check = TestSuiteCheck()
        >>> check.name
        'test_suite'
        >>> result = check.run(Path("."), {})  # doctest: +SKIP
    """

    name = "test_suite"
    description = "Check that the project has a test suite"
    rules = {"test_suite.missing": "The project has no tests"}
    hints = {
        "test_suite.missing": (
            "Add a tests/ directory to the project root with at least one test "
            "file, such as tests/test_basic.py."
        )
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Look for the project's tests.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if tests are found, with their place in the details
            - WARNING status if no tests are found
        """
        tests_at = find_tests(package_path)
        if tests_at is None:
            findings = [
                Finding("test_suite.missing", "No tests found", CheckStatus.WARNING)
            ]
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No tests found",
                details=[
                    "Looked for tests/ or test/ at the project root and inside the "
                    "package, the testpaths of the pytest configuration, and "
                    "files named test_*.py, *_test.py or tests.py anywhere in "
                    "the project",
                ],
                findings=findings,
            )

        shown = display_path(tests_at, package_path)
        if os.path.isdir(tests_at):
            shown += "/"
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Test suite present",
            details=[f"Found tests: {shown}"],
        )
