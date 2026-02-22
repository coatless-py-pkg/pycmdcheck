"""Pytest configuration and fixtures."""

from pathlib import Path
from unittest.mock import patch

import pytest

from pycmdcheck.ast_cache import clear_cache as clear_ast_cache
from pycmdcheck.pyproject_reader import clear_cache


def _build_check_registry() -> dict[str, type]:
    """Build a registry of all built-in check classes.

    This mirrors what ``discover_checks()`` would return if the package
    were installed and entry points were registered.  By importing the
    classes directly we avoid the need for a ``pip install -e .`` step
    in the test environment.
    """
    from pycmdcheck.checks.build import BuildCheck
    from pycmdcheck.checks.dependencies import DependenciesCheck
    from pycmdcheck.checks.docs import DocsCheck
    from pycmdcheck.checks.formatting import FormattingCheck
    from pycmdcheck.checks.imports import ImportsCheck
    from pycmdcheck.checks.license import LicenseCheck
    from pycmdcheck.checks.linting import LintingCheck
    from pycmdcheck.checks.metadata import MetadataCheck
    from pycmdcheck.checks.py_typed import PyTypedCheck
    from pycmdcheck.checks.structure import StructureCheck
    from pycmdcheck.checks.tests import TestsCheck
    from pycmdcheck.checks.typing import TypingCheck
    from pycmdcheck.checks.version import VersionCheck

    return {
        "metadata": MetadataCheck,
        "structure": StructureCheck,
        "tests": TestsCheck,
        "linting": LintingCheck,
        "typing": TypingCheck,
        "imports": ImportsCheck,
        "license": LicenseCheck,
        "docs": DocsCheck,
        "dependencies": DependenciesCheck,
        "build": BuildCheck,
        "formatting": FormattingCheck,
        "version": VersionCheck,
        "py_typed": PyTypedCheck,
    }


@pytest.fixture(autouse=True)
def _clear_pyproject_cache():
    """Clear the pyproject_reader LRU cache before each test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(autouse=True)
def _clear_ast_cache():
    """Clear the AST parse cache before each test."""
    clear_ast_cache()
    yield
    clear_ast_cache()


@pytest.fixture(autouse=True)
def _mock_discover_checks():
    """Automatically mock discover_checks so runner tests work without install.

    Entry points are only available when a package is installed via pip.
    In a development / CI environment the package may just have ``src/``
    on ``sys.path`` (via ``pythonpath`` in pyproject.toml).  This fixture
    patches ``discover_checks`` (and its companion ``list_available_checks``)
    so every test sees the full built-in check registry.
    """
    registry = _build_check_registry()

    descriptions = []
    for name, cls in sorted(registry.items()):
        desc = getattr(cls, "description", "No description")
        descriptions.append((name, desc))

    with (
        patch("pycmdcheck.runner.discover_checks", return_value=registry),
        patch("pycmdcheck.discovery.discover_checks", return_value=registry),
        patch(
            "pycmdcheck.discovery.list_available_checks",
            return_value=descriptions,
        ),
        patch("pycmdcheck.cli.list_available_checks", return_value=descriptions),
    ):
        yield


# ---------------------------------------------------------------------------
# Package fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def temp_package(tmp_path: Path) -> Path:
    """Create a minimal valid Python package structure."""
    # Create src layout
    pkg_dir = tmp_path / "src" / "mypackage"
    pkg_dir.mkdir(parents=True)

    # Create __init__.py
    (pkg_dir / "__init__.py").write_text('"""My package."""\n\n__version__ = "0.1.0"\n')

    # Create pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "mypackage"
version = "0.1.0"
description = "A test package"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")

    # Create README
    readme = tmp_path / "README.md"
    readme.write_text("""# My Package

A test package for pycmdcheck testing.

## Installation

```bash
pip install mypackage
```

## Usage

```python
import mypackage
```
""")

    # Create LICENSE
    license_file = tmp_path / "LICENSE"
    license_file.write_text("""MIT License

Copyright (c) 2024 Test

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
""")

    # Create tests directory
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_mypackage.py").write_text('''"""Tests for mypackage."""


def test_version():
    """Test version is defined."""
    from mypackage import __version__
    assert __version__ == "0.1.0"
''')

    return tmp_path


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """Create an empty directory."""
    return tmp_path


@pytest.fixture
def minimal_package(tmp_path: Path) -> Path:
    """Create a minimal package with only required files."""
    pkg_dir = tmp_path / "src" / "minimal"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "minimal"
version = "0.1.0"
""")

    return tmp_path


@pytest.fixture
def bad_package(tmp_path: Path) -> Path:
    """Create a package that will cause checks to fail.

    This package has:
    - Missing required metadata fields (no name, no version)
    - Invalid pyproject.toml structure for [project]
    - No README
    - No LICENSE
    - No tests directory
    - An empty src directory (no actual package inside)
    """
    # Create an empty src dir with no package
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Create a pyproject.toml with an empty [project] table (missing name, version)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")

    return tmp_path


@pytest.fixture
def package_with_syntax_error(tmp_path: Path) -> Path:
    """Create a package that contains a Python file with a syntax error.

    This exercises checks that parse Python files (e.g. imports, docs).
    """
    pkg_dir = tmp_path / "src" / "badcode"
    pkg_dir.mkdir(parents=True)

    (pkg_dir / "__init__.py").write_text('"""Bad code package."""\n')

    # Write a file with a deliberate syntax error
    (pkg_dir / "broken.py").write_text(
        '"""Broken module."""\n\ndef foo(\n    # missing closing paren and colon\n'
    )

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "badcode"
version = "0.1.0"
description = "A package with syntax errors"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")

    readme = tmp_path / "README.md"
    readme.write_text(
        "# Bad Code\n\nThis package has intentional syntax errors for testing.\n"
        "It exists purely to validate that pycmdcheck correctly catches and\n"
        "reports issues with malformed Python source files.\n"
        "Extra words to meet the fifty word minimum for the docs check so\n"
        "the README length heuristic does not flag this as too short.\n"
    )

    license_file = tmp_path / "LICENSE"
    license_file.write_text("""MIT License

Copyright (c) 2024 Test

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
""")

    return tmp_path


@pytest.fixture
def package_with_invalid_toml(tmp_path: Path) -> Path:
    """Create a package whose pyproject.toml is not valid TOML."""
    pkg_dir = tmp_path / "src" / "invalidtoml"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("this is not valid toml [[[")

    return tmp_path


@pytest.fixture
def flat_layout_package(tmp_path: Path) -> Path:
    """Create a package using flat layout (no src/ directory)."""
    pkg_dir = tmp_path / "myflatpkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text('"""Flat layout package."""\n')

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "myflatpkg"
version = "0.1.0"
description = "A flat layout package"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
""")

    readme = tmp_path / "README.md"
    readme.write_text(
        "# My Flat Package\n\nA flat layout test package for pycmdcheck.\n"
        "This package uses flat layout, meaning the package directory sits\n"
        "directly inside the project root instead of under a src/ directory.\n"
        "Extra words added to ensure we meet the fifty word threshold for\n"
        "the documentation completeness check in pycmdcheck tests.\n"
    )

    license_file = tmp_path / "LICENSE"
    license_file.write_text("""MIT License

Copyright (c) 2024 Test

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction.
""")

    return tmp_path


@pytest.fixture
def package_with_setup_py(tmp_path: Path) -> Path:
    """Create a legacy package using setup.py instead of pyproject.toml."""
    pkg_dir = tmp_path / "legacypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text('"""Legacy package."""\n')

    setup_py = tmp_path / "setup.py"
    setup_py.write_text("""from setuptools import setup

setup(
    name="legacypkg",
    version="0.1.0",
)
""")

    return tmp_path
