"""Tests for individual checks."""

from pathlib import Path

from pycmdcheck.results import CheckStatus

# ---------------------------------------------------------------------------
# Metadata check
# ---------------------------------------------------------------------------


class TestMetadataCheck:
    """Tests for metadata check."""

    def test_valid_pyproject(self, temp_package: Path) -> None:
        """Test valid pyproject.toml with all fields passes with OK."""
        from pycmdcheck.checks.metadata import MetadataCheck

        check = MetadataCheck()
        result = check.run(temp_package, {})

        assert result.name == "metadata"
        assert result.status == CheckStatus.OK
        assert result.message == "Package metadata is valid"
        assert any("All metadata fields present" in d for d in result.details)

    def test_missing_pyproject(self, empty_dir: Path) -> None:
        """Test missing pyproject.toml returns ERROR."""
        from pycmdcheck.checks.metadata import MetadataCheck

        check = MetadataCheck()
        result = check.run(empty_dir, {})

        assert result.name == "metadata"
        assert result.status == CheckStatus.ERROR
        assert result.message == "No package metadata found"
        assert "Missing pyproject.toml, setup.py, or setup.cfg" in result.details

    def test_minimal_pyproject_gives_note(self, minimal_package: Path) -> None:
        """Test minimal pyproject.toml with missing recommended fields gives NOTE."""
        from pycmdcheck.checks.metadata import MetadataCheck

        check = MetadataCheck()
        result = check.run(minimal_package, {})

        assert result.name == "metadata"
        assert result.status == CheckStatus.NOTE
        assert "recommended" in result.message.lower()
        # The detail should mention at least some of the missing recommended fields
        missing_detail = [d for d in result.details if "Missing recommended" in d]
        assert len(missing_detail) == 1
        assert "description" in missing_detail[0]

    def test_missing_required_fields(self, bad_package: Path) -> None:
        """Test pyproject.toml missing required fields returns ERROR."""
        from pycmdcheck.checks.metadata import MetadataCheck

        check = MetadataCheck()
        result = check.run(bad_package, {})

        assert result.status == CheckStatus.ERROR
        assert "required" in result.message.lower()
        missing_detail = [d for d in result.details if "Missing required" in d]
        assert len(missing_detail) == 1
        assert "name" in missing_detail[0]
        assert "version" in missing_detail[0]

    def test_invalid_toml(self, package_with_invalid_toml: Path) -> None:
        """Test invalid TOML file returns ERROR with parse error detail."""
        from pycmdcheck.checks.metadata import MetadataCheck

        check = MetadataCheck()
        result = check.run(package_with_invalid_toml, {})

        assert result.status == CheckStatus.ERROR
        assert result.message == "Invalid pyproject.toml"
        assert any("TOML parse error" in d for d in result.details)

    def test_legacy_setup_py(self, package_with_setup_py: Path) -> None:
        """Test setup.py without pyproject.toml gives WARNING."""
        from pycmdcheck.checks.metadata import MetadataCheck

        check = MetadataCheck()
        result = check.run(package_with_setup_py, {})

        assert result.status == CheckStatus.WARNING
        assert "pyproject.toml" in result.message
        assert any("legacy" in d.lower() for d in result.details)


# ---------------------------------------------------------------------------
# Structure check
# ---------------------------------------------------------------------------


class TestStructureCheck:
    """Tests for structure check."""

    def test_src_layout(self, temp_package: Path) -> None:
        """Test src layout is detected and passes."""
        from pycmdcheck.checks.structure import StructureCheck

        check = StructureCheck()
        result = check.run(temp_package, {})

        assert result.name == "structure"
        assert result.status == CheckStatus.OK
        assert result.message == "Valid src layout structure"
        assert "Using src layout" in result.details
        assert any("mypackage" in d for d in result.details)

    def test_flat_layout(self, flat_layout_package: Path) -> None:
        """Test flat layout is detected and passes."""
        from pycmdcheck.checks.structure import StructureCheck

        check = StructureCheck()
        result = check.run(flat_layout_package, {})

        assert result.name == "structure"
        assert result.status == CheckStatus.OK
        assert "flat layout" in result.message.lower()
        assert "Using flat layout" in result.details
        assert any("myflatpkg" in d for d in result.details)

    def test_no_package(self, empty_dir: Path) -> None:
        """Test empty directory fails with ERROR."""
        from pycmdcheck.checks.structure import StructureCheck

        check = StructureCheck()
        result = check.run(empty_dir, {})

        assert result.status == CheckStatus.ERROR
        assert "No package or module found" in result.message

    def test_src_dir_but_no_package_inside(self, bad_package: Path) -> None:
        """Test src/ exists but contains no package directory."""
        from pycmdcheck.checks.structure import StructureCheck

        check = StructureCheck()
        result = check.run(bad_package, {})

        assert result.status == CheckStatus.ERROR
        assert "No package directory found in src/" in result.message

    def test_src_layout_missing_init(self, tmp_path: Path) -> None:
        """Test src layout with a directory but no __init__.py gives WARNING."""
        from pycmdcheck.checks.structure import StructureCheck

        src_dir = tmp_path / "src" / "noinit"
        src_dir.mkdir(parents=True)
        # Intentionally do NOT create __init__.py
        (src_dir / "module.py").write_text("x = 1\n")

        check = StructureCheck()
        result = check.run(tmp_path, {})

        assert result.status == CheckStatus.WARNING
        assert "missing __init__.py" in result.message.lower()


# ---------------------------------------------------------------------------
# License check
# ---------------------------------------------------------------------------


class TestLicenseCheck:
    """Tests for license check."""

    def test_license_present(self, temp_package: Path) -> None:
        """Test MIT LICENSE file is detected and identified."""
        from pycmdcheck.checks.license import LicenseCheck

        check = LicenseCheck()
        result = check.run(temp_package, {})

        assert result.name == "license"
        assert result.status == CheckStatus.OK
        assert result.message == "License file present"
        assert any("Found license file: LICENSE" in d for d in result.details)
        assert any("MIT" in d for d in result.details)

    def test_license_missing(self, minimal_package: Path) -> None:
        """Test missing LICENSE gives WARNING."""
        from pycmdcheck.checks.license import LicenseCheck

        check = LicenseCheck()
        result = check.run(minimal_package, {})

        assert result.status == CheckStatus.WARNING
        assert result.message == "No license file found"

    def test_empty_license_file(self, tmp_path: Path) -> None:
        """Test empty LICENSE file gives WARNING."""
        from pycmdcheck.checks.license import LicenseCheck

        (tmp_path / "LICENSE").write_text("MIT")  # Too short (< 50 chars)

        check = LicenseCheck()
        result = check.run(tmp_path, {})

        assert result.status == CheckStatus.WARNING
        assert "empty or incomplete" in result.message.lower()


# ---------------------------------------------------------------------------
# Docs check
# ---------------------------------------------------------------------------


class TestDocsCheck:
    """Tests for docs check."""

    def test_readme_present(self, temp_package: Path) -> None:
        """Test README is detected and reported."""
        from pycmdcheck.checks.docs import DocsCheck

        check = DocsCheck()
        result = check.run(temp_package, {})

        assert result.name == "docs"
        assert result.status in (CheckStatus.OK, CheckStatus.NOTE)
        assert any("README" in d for d in result.details)

    def test_readme_missing_required(self, minimal_package: Path) -> None:
        """Test missing README with require_readme=True gives WARNING."""
        from pycmdcheck.checks.docs import DocsCheck

        check = DocsCheck()
        result = check.run(minimal_package, {"require_readme": True})

        assert result.status == CheckStatus.WARNING
        assert "incomplete" in result.message.lower()

    def test_readme_missing_not_required(self, minimal_package: Path) -> None:
        """Test missing README with require_readme=False does not warn."""
        from pycmdcheck.checks.docs import DocsCheck

        check = DocsCheck()
        result = check.run(minimal_package, {"require_readme": False})

        # Should not be WARNING since README is not required
        assert result.status != CheckStatus.WARNING

    def test_short_readme(self, tmp_path: Path) -> None:
        """Test very short README triggers a NOTE about brevity."""
        from pycmdcheck.checks.docs import DocsCheck

        (tmp_path / "README.md").write_text("# Short\n\nToo short.\n")

        check = DocsCheck()
        result = check.run(tmp_path, {})

        assert result.status == CheckStatus.NOTE
        issues = [d for d in result.details if "short" in d.lower()]
        assert len(issues) > 0

    def test_docstrings_check(self, temp_package: Path) -> None:
        """Test docstring checking when enabled."""
        from pycmdcheck.checks.docs import DocsCheck

        check = DocsCheck()
        result = check.run(temp_package, {"check_docstrings": True})

        # The __init__.py has a docstring, so it should be fine
        assert result.status in (CheckStatus.OK, CheckStatus.NOTE)

    def test_missing_docstrings_detected(self, tmp_path: Path) -> None:
        """Docstring checker detects missing docstrings on public items."""
        from pycmdcheck.checks.docs import DocsCheck

        pkg = tmp_path / "src" / "nodocs"
        pkg.mkdir(parents=True)
        # Module, class, and function all missing docstrings
        (pkg / "__init__.py").write_text("")
        (pkg / "api.py").write_text(
            "class PublicClass:\n    pass\n\ndef public_func():\n    pass\n"
        )

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "nodocs"\nversion = "0.1.0"\n'
        )
        # Need a README so the check doesn't flag that instead
        (tmp_path / "README.md").write_text(
            "# No Docs\n\nA package without docstrings for testing purposes. "
            "Extra words so we pass the minimum word count for the README check. "
            "More filler text to be safe.\n"
        )

        check = DocsCheck()
        result = check.run(tmp_path, {"check_docstrings": True, "require_readme": True})

        assert result.status == CheckStatus.NOTE
        combined = " ".join(result.details)
        assert "Missing docstrings" in combined


# ---------------------------------------------------------------------------
# Tests check
# ---------------------------------------------------------------------------


class TestTestsCheck:
    """Tests for tests check."""

    def test_tests_present(self, temp_package: Path) -> None:
        """Test tests directory is detected and runner is reported."""
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        result = check.run(temp_package, {"runner": "pytest"})

        assert result.name == "tests"
        # May be SKIPPED if pytest not installed, or OK/ERROR based on results
        assert result.status in (
            CheckStatus.OK,
            CheckStatus.ERROR,
            CheckStatus.SKIPPED,
        )
        assert any("pytest" in d.lower() for d in result.details)

    def test_no_tests(self, minimal_package: Path) -> None:
        """Test missing tests directory gives WARNING."""
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        result = check.run(minimal_package, {"runner": "pytest"})

        assert result.status == CheckStatus.WARNING
        assert result.message == "No tests found"
        assert any("tests/" in d for d in result.details)

    def test_unknown_runner(self, temp_package: Path) -> None:
        """Test specifying an unknown runner gives ERROR."""
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        result = check.run(temp_package, {"runner": "nonexistent_runner"})

        assert result.status == CheckStatus.ERROR
        assert "Unknown test runner" in result.message

    def test_default_runner_is_pytest(self, temp_package: Path) -> None:
        """Test that the default runner config key defaults to pytest."""
        from pycmdcheck.checks.tests import TestsCheck

        check = TestsCheck()
        result = check.run(temp_package, {})

        # Whether it passes or skips, the details should mention pytest
        assert any("pytest" in d.lower() for d in result.details)


# ---------------------------------------------------------------------------
# Linting check
# ---------------------------------------------------------------------------


class TestLintingCheck:
    """Tests for linting check."""

    def test_unsupported_tool(self, temp_package: Path) -> None:
        """Test unsupported linting tool gives ERROR."""
        from pycmdcheck.checks.linting import LintingCheck

        check = LintingCheck()
        result = check.run(temp_package, {"tool": "nosuchlinter"})

        assert result.status == CheckStatus.ERROR
        assert "Unsupported" in result.message
        assert any("Supported tools" in d for d in result.details)

    def test_default_tool_is_ruff(self, temp_package: Path) -> None:
        """Test that the default tool is ruff."""
        from pycmdcheck.checks.linting import LintingCheck

        check = LintingCheck()
        result = check.run(temp_package, {})

        # Should either run ruff or skip if ruff is not installed
        assert result.status in (
            CheckStatus.OK,
            CheckStatus.WARNING,
            CheckStatus.SKIPPED,
        )
        assert any("ruff" in d.lower() for d in result.details)


# ---------------------------------------------------------------------------
# Typing check
# ---------------------------------------------------------------------------


class TestTypingCheck:
    """Tests for typing check."""

    def test_unsupported_tool(self, temp_package: Path) -> None:
        """Test unsupported type checker gives ERROR."""
        from pycmdcheck.checks.typing import TypingCheck

        check = TypingCheck()
        result = check.run(temp_package, {"tool": "nosuchchecker"})

        assert result.status == CheckStatus.ERROR
        assert "Unsupported" in result.message
        assert any("Supported tools" in d for d in result.details)

    def test_default_tool_is_mypy(self, temp_package: Path) -> None:
        """Test that the default tool is mypy."""
        from pycmdcheck.checks.typing import TypingCheck

        check = TypingCheck()
        result = check.run(temp_package, {})

        # Should either run or skip if mypy is not installed
        assert result.status in (
            CheckStatus.OK,
            CheckStatus.ERROR,
            CheckStatus.SKIPPED,
        )
        assert any("mypy" in d.lower() for d in result.details)


# ---------------------------------------------------------------------------
# Imports check
# ---------------------------------------------------------------------------


class TestImportsCheck:
    """Tests for imports check."""

    def test_valid_imports(self, temp_package: Path) -> None:
        """Test package with valid stdlib imports passes."""
        from pycmdcheck.checks.imports import ImportsCheck

        check = ImportsCheck()
        result = check.run(temp_package, {})

        assert result.name == "imports"
        assert result.status in (CheckStatus.OK, CheckStatus.WARNING)

    def test_no_python_files(self, empty_dir: Path) -> None:
        """Test directory with no Python files gives NOTE."""
        from pycmdcheck.checks.imports import ImportsCheck

        check = ImportsCheck()
        result = check.run(empty_dir, {})

        assert result.status == CheckStatus.NOTE
        assert result.message == "No Python files found"

    def test_syntax_error_in_imports(self, package_with_syntax_error: Path) -> None:
        """Unparseable files are skipped gracefully, not reported as errors."""
        from pycmdcheck.checks.imports import ImportsCheck

        check = ImportsCheck()
        result = check.run(package_with_syntax_error, {})

        # Files that fail to parse are silently skipped (no imports extracted)
        assert result.status == CheckStatus.OK

    def test_relative_imports_ignored(self, tmp_path: Path) -> None:
        """Relative imports (from . import x) should not be flagged."""
        from pycmdcheck.checks.imports import ImportsCheck

        pkg = tmp_path / "src" / "relpkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text(
            "from .submodule import helper\nfrom . import utils\n"
        )
        (pkg / "submodule.py").write_text("def helper(): pass\n")
        (pkg / "utils.py").write_text("x = 1\n")

        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "relpkg"\nversion = "0.1.0"\n'
        )

        check = ImportsCheck()
        result = check.run(tmp_path, {})

        assert result.status == CheckStatus.OK


# ---------------------------------------------------------------------------
# Cross-check integration
# ---------------------------------------------------------------------------


class TestCrossCheckIntegration:
    """Integration tests running multiple checks together."""

    def test_all_pass_on_valid_package(self, temp_package: Path) -> None:
        """Test that metadata, structure, license, docs checks all pass on valid pkg."""
        from pycmdcheck.runner import run_checks

        report = run_checks(
            temp_package,
            checks=["metadata", "structure", "license"],
        )

        for result in report.results:
            assert result.status == CheckStatus.OK, (
                f"{result.name} was {result.status}: {result.message}"
            )

    def test_bad_package_has_errors(self, bad_package: Path) -> None:
        """Test that bad_package produces ERROR results for key checks."""
        from pycmdcheck.runner import run_checks

        report = run_checks(
            bad_package,
            checks=["metadata", "structure"],
        )

        statuses = {r.name: r.status for r in report.results}
        assert statuses["metadata"] == CheckStatus.ERROR
        assert statuses["structure"] == CheckStatus.ERROR
        assert report.passed is False
