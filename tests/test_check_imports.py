"""Tests for the deprecated imports check.

The check was merged into the dependencies check. What it used to report is
tested in test_check_dependencies.py, together with the import collector.
"""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pycmdcheck.checks.imports import ImportsCheck
from pycmdcheck.results import CheckStatus

MakePackage = Callable[..., Path]


@pytest.mark.parametrize(
    "project",
    [
        pytest.param({}, id="healthy"),
        pytest.param({"init": "import not_installed_xyz\n"}, id="undeclared-import"),
        pytest.param({"layout": "none", "pyproject": False}, id="nothing-to-scan"),
    ],
)
def test_imports_is_a_deprecated_alias(
    make_package: MakePackage, project: dict[str, Any]
) -> None:
    """Whatever the project holds, the check looks at nothing and says where to go."""
    result = ImportsCheck().run(make_package(**project), {})
    assert (result.status, result.skip_reason) == (CheckStatus.SKIPPED, "deprecated")
    assert result.message == "Merged into the dependencies check"
    assert result.details == [
        "The imports check will be removed in a later release. "
        "The dependencies check reports undeclared imports"
    ]
    assert result.findings == []


def test_imports_says_it_is_deprecated_and_has_no_rules() -> None:
    assert ImportsCheck.name == "imports"
    assert "deprecated" in ImportsCheck.description.lower()
    assert ImportsCheck.rules == {}
