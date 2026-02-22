"""Tests for py.typed PEP 561 marker check."""

from pathlib import Path

from pycmdcheck.checks.py_typed import PyTypedCheck
from pycmdcheck.results import CheckStatus


class TestPyTypedCheck:
    """Tests for PyTypedCheck."""

    def test_marker_present_with_types(self, tmp_path: Path) -> None:
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("def foo(x: int) -> str: ...\n")
        (pkg / "py.typed").write_text("")
        check = PyTypedCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_marker_missing_with_types(self, tmp_path: Path) -> None:
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("def foo(x: int) -> str: ...\n")
        check = PyTypedCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.NOTE

    def test_no_types_no_marker(self, tmp_path: Path) -> None:
        pkg = tmp_path / "src" / "mypkg"
        pkg.mkdir(parents=True)
        (pkg / "__init__.py").write_text("x = 1\n")
        check = PyTypedCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_no_package_found(self, empty_dir: Path) -> None:
        check = PyTypedCheck()
        result = check.run(empty_dir, {})
        assert result.status == CheckStatus.NOTE
