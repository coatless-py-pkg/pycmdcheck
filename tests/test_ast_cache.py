"""Tests for AST cache."""

from pathlib import Path

from pycmdcheck.ast_cache import clear_cache, parse_file


class TestAstCache:
    def setup_method(self) -> None:
        clear_cache()

    def test_parses_valid_file(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        tree = parse_file(f)
        assert tree is not None

    def test_returns_none_for_syntax_error(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("def foo(\n")
        tree = parse_file(f)
        assert tree is None

    def test_caches_result(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        tree1 = parse_file(f)
        tree2 = parse_file(f)
        assert tree1 is tree2

    def test_clear_cache(self, tmp_path: Path) -> None:
        f = tmp_path / "test.py"
        f.write_text("x = 1\n")
        tree1 = parse_file(f)
        clear_cache()
        tree2 = parse_file(f)
        assert tree1 is not tree2
