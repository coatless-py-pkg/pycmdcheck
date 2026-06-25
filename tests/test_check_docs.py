"""Tests for docs check enhancements."""

from pathlib import Path

from pycmdcheck.checks.docs import DocsCheck
from pycmdcheck.results import CheckStatus

# Helper to build a README with enough words to pass the minimum word count.
_LONG_README_WITH_SECTIONS = (
    "# My Package\n\n"
    "A test package that provides useful utilities for developers who need "
    "to validate and inspect Python project structure and documentation. "
    "This package was created to exercise the pycmdcheck documentation "
    "checking logic in an automated test suite.\n\n"
    "## Installation\n\n"
    "```bash\npip install mypackage\n```\n\n"
    "## Usage\n\n"
    "```python\nimport mypackage\n```\n"
)

_LONG_README_NO_SECTIONS = (
    "# My Package\n\n"
    "A test package that provides useful utilities for developers who need "
    "to validate and inspect Python project structure and documentation. "
    "This package was created to exercise the pycmdcheck documentation "
    "checking logic in an automated test suite. It contains several "
    "modules that demonstrate best practices for packaging Python code "
    "and distributing it via PyPI.\n"
)


class TestDocsReadmeSections:
    """Tests for README section validation."""

    def test_readme_with_sections(self, temp_package: Path) -> None:
        """README with install and usage sections -> OK."""
        readme = temp_package / "README.md"
        readme.write_text(_LONG_README_WITH_SECTIONS)
        check = DocsCheck()
        result = check.run(temp_package, {"check_readme_sections": True})
        assert result.status == CheckStatus.OK

    def test_rst_readme_sections_recognized(self, tmp_path: Path) -> None:
        """RST README with underline headings is not falsely flagged (#11)."""
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("")
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "mypkg"\nversion = "0.1.0"\n'
        )
        (tmp_path / "README.rst").write_text(
            "My Package\n==========\n\n"
            "A useful library for developers that need to validate Python "
            "project structure and documentation in an automated fashion.\n\n"
            "Installation\n------------\n\n"
            "    pip install mypkg\n\n"
            "Quick Start\n-----------\n\n"
            "    import mypkg\n"
        )
        check = DocsCheck()
        result = check.run(tmp_path, {"check_readme_sections": True})
        assert not any("README missing section" in d for d in result.details)

    def test_readme_missing_sections(self, temp_package: Path) -> None:
        """README without install/usage sections -> details note them."""
        readme = temp_package / "README.md"
        readme.write_text(_LONG_README_NO_SECTIONS)
        check = DocsCheck()
        result = check.run(temp_package, {"check_readme_sections": True})
        missing = [d for d in result.details if "README missing section" in d]
        assert len(missing) == 2
        assert any("Installation" in m for m in missing)
        assert any("Usage" in m for m in missing)

    def test_sections_check_disabled_by_default(self, temp_package: Path) -> None:
        """Section checking disabled by default."""
        readme = temp_package / "README.md"
        readme.write_text(_LONG_README_NO_SECTIONS)
        check = DocsCheck()
        result = check.run(temp_package, {})
        # No section-related issues when the check is disabled
        all_text = result.details
        assert not any("README missing section" in d for d in all_text)


class TestDocsDocstringCoverage:
    """Tests for docstring coverage percentage."""

    def test_docstring_coverage_reported(self, temp_package: Path) -> None:
        """When check_docstrings enabled, coverage % is reported."""
        check = DocsCheck()
        result = check.run(temp_package, {"check_docstrings": True})
        assert any("coverage" in d.lower() for d in result.details)

    def test_min_coverage_threshold_met(self, temp_package: Path) -> None:
        """No issue when coverage meets the threshold."""
        check = DocsCheck()
        result = check.run(
            temp_package,
            {"check_docstrings": True, "min_docstring_coverage": 50.0},
        )
        all_output = result.details
        assert not any("below" in d.lower() for d in all_output)

    def test_min_coverage_threshold_not_met(self, temp_package: Path) -> None:
        """Issue raised when coverage is below the threshold."""
        # Add a Python file with public items missing docstrings
        pkg_dir = temp_package / "src" / "mypackage"
        (pkg_dir / "nodocs.py").write_text(
            "def public_one():\n    pass\n\n"
            "def public_two():\n    pass\n\n"
            "class PublicClass:\n    pass\n"
        )
        check = DocsCheck()
        result = check.run(
            temp_package,
            {"check_docstrings": True, "min_docstring_coverage": 100.0},
        )
        # The overall result should flag the coverage gap
        assert result.status in (CheckStatus.NOTE, CheckStatus.WARNING)
