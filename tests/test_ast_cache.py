"""Tests for AST cache."""

import ast
import sys
import warnings
from pathlib import Path
from typing import Any

import pytest

from pycmdcheck import ast_cache
from pycmdcheck.ast_cache import clear_cache, parse_file, unparsed_files_detail
from pycmdcheck.constants import MAX_SOURCE_BYTES


def _assigned_value(tree: ast.Module | None) -> object:
    """Return the constant assigned by a module of the form ``name = <constant>``."""
    assert tree is not None
    statement = tree.body[-1]
    assert isinstance(statement, ast.Assign)
    assert isinstance(statement.value, ast.Constant)
    return statement.value.value


def _reason(path: Path) -> str:
    """Return why *path* does not parse, as unparsed_files_detail() words it."""
    detail = unparsed_files_detail([path], path.parent)
    assert detail is not None
    start = f"Could not parse 1 file(s), skipped: {path.name} ("
    assert detail.startswith(start) and detail.endswith(")")
    return detail[len(start) : -1]


def _patch_parse(
    monkeypatch: pytest.MonkeyPatch, error: BaseException | None = None
) -> list[str]:
    """Record the files given to ast.parse, and make it raise *error* if set.

    A call that names no file is passed on untouched. The coverage tracer
    makes such calls of its own on Python 3.14.
    """
    calls: list[str] = []
    real_parse = ast.parse

    def fake_parse(
        source: Any, filename: Any = "<unknown>", *args: Any, **kwargs: Any
    ) -> Any:
        if filename == "<unknown>":
            return real_parse(source, *args, **kwargs)
        calls.append(filename)
        if error is not None:
            raise error
        return real_parse(source, filename, *args, **kwargs)

    monkeypatch.setattr(ast_cache.ast, "parse", fake_parse)
    return calls


class TestCaching:
    """A tree is shared while its file is unchanged, and replaced when it changes."""

    def test_file_is_parsed_once_while_unchanged(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        monkeypatch.chdir(tmp_path)
        # Patch only around the calls: pytest itself parses source when it
        # reports a failure.
        with monkeypatch.context() as patched:
            calls = _patch_parse(patched)
            trees = [parse_file(f), parse_file(Path("mod.py")), parse_file(f)]
        assert trees[0] is not None
        assert trees[0] is trees[1] is trees[2]
        assert len(calls) == 1

    def test_clearing_releases_the_trees(self, tmp_path: Path) -> None:
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        tree = parse_file(f)
        assert len(ast_cache._cache) == 1
        clear_cache()
        assert len(ast_cache._cache) == 0
        assert parse_file(f) is not tree

    def test_edit_is_seen_without_clearing(self, tmp_path: Path) -> None:
        """A second check() in one process used to see the first content."""
        f = tmp_path / "__init__.py"
        f.write_text('__version__ = "1.0.0"\n')
        assert _assigned_value(parse_file(f)) == "1.0.0"
        f.write_text('__version__ = "3.0.0"\n')
        assert _assigned_value(parse_file(f)) == "3.0.0"
        f.write_text("def foo(\n")
        assert parse_file(f) is None
        f.write_text("def foo(): ...\n")
        assert parse_file(f) is not None
        f.unlink()
        assert parse_file(f) is None

    def test_cache_false_keeps_no_tree_but_uses_a_kept_one(
        self, tmp_path: Path
    ) -> None:
        """cache=False lets a check scan many files without keeping their trees."""
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        tree = parse_file(f, cache=False)
        assert tree is not None
        assert parse_file(f, cache=False) is not tree
        assert len(ast_cache._cache) == 0
        kept = parse_file(f)
        assert parse_file(f, cache=False) is kept


class TestEncodings:
    """Source files are decoded the way the interpreter decodes them."""

    @pytest.mark.parametrize(
        ("source", "value"),
        [
            # Windows editors write a BOM. Such a file used to be dropped.
            (b"\xef\xbb\xbfname = '0.1.0'\n", "0.1.0"),
            (b"# -*- coding: latin-1 -*-\nname = 'caf\xe9'\n", "café"),
            (b'"""Doc."""\r\n\r\nname = 1\r\n', 1),
        ],
        ids=["utf8-bom", "pep263-cookie", "crlf"],
    )
    def test_source_is_read_as_bytes(
        self, tmp_path: Path, source: bytes, value: object
    ) -> None:
        f = tmp_path / "mod.py"
        f.write_bytes(source)
        assert _assigned_value(parse_file(f)) == value
        assert unparsed_files_detail([], tmp_path) is None

    def test_non_utf8_bytes_without_a_cookie(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_bytes(b"name = 'caf\xe9'\n")
        assert parse_file(f) is None
        assert _reason(f).startswith("syntax error")


class TestUnparseableInput:
    """Nothing a repository can contain makes parse_file raise."""

    def test_syntax_error_reason_names_the_line(self, tmp_path: Path) -> None:
        f = tmp_path / "bad.py"
        f.write_text("x = 1\n\ndef foo(:\n    pass\n")
        assert parse_file(f) is None
        assert _reason(f).startswith("syntax error on line 3: ")

    def test_null_byte(self, tmp_path: Path) -> None:
        """ast.parse raises ValueError for this on Python 3.11."""
        f = tmp_path / "nul.py"
        f.write_bytes(b"x = 1\x00\n")
        assert parse_file(f) is None
        assert "null bytes" in _reason(f)

    @pytest.mark.parametrize(
        ("error", "reason"),
        [
            (ValueError("cannot contain null bytes"), "cannot contain null bytes"),
            (RecursionError("maximum depth"), "nested too deeply to parse"),
            (MemoryError(), "too large to parse"),
            (SyntaxError("invalid syntax"), "syntax error: invalid syntax"),
        ],
    )
    def test_every_parser_error_gives_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        error: Exception,
        reason: str,
    ) -> None:
        """Which error a file triggers depends on the Python version."""
        f = tmp_path / "mod.py"
        f.write_text("x = 1\n")
        with monkeypatch.context() as patched:
            _patch_parse(patched, error)
            assert parse_file(f) is None
            assert _reason(f) == reason

    @pytest.mark.skipif(sys.platform == "win32", reason="needs symlinks, /dev/null")
    def test_files_that_cannot_be_read(self, tmp_path: Path) -> None:
        (tmp_path / "odd.py").mkdir()
        (tmp_path / "device.py").symlink_to("/dev/null")
        for name, reason in [
            ("missing.py", "file not found"),
            ("odd.py", "not a regular file"),
            ("device.py", "not a regular file"),
        ]:
            assert parse_file(tmp_path / name) is None
            assert _reason(tmp_path / name) == reason

    def test_oversized_file_is_skipped(self, tmp_path: Path) -> None:
        assert ast_cache._cache._max_bytes == MAX_SOURCE_BYTES
        f = tmp_path / "big.py"
        f.write_bytes(b"x = 1\n".ljust(MAX_SOURCE_BYTES + 1, b"#"))
        assert parse_file(f) is None
        assert _reason(f) == "larger than 2 MiB"


class TestParserWarnings:
    """Warnings about the checked code are not pycmdcheck's to show."""

    def _source(self, tmp_path: Path) -> Path:
        f = tmp_path / "warns.py"
        f.write_text('pattern = "\\d+"\nvalue = 1if pattern else 2\n')
        return f

    def test_no_warning_reaches_the_caller(self, tmp_path: Path) -> None:
        """Each such line was printed to stderr, once per place in the file."""
        f = self._source(tmp_path)
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            before = list(warnings.filters)
            tree = parse_file(f)
            after = list(warnings.filters)
        assert tree is not None
        assert caught == []
        assert before == after

    def test_warnings_as_errors_do_not_make_a_valid_file_unparseable(
        self, tmp_path: Path
    ) -> None:
        """Under ``-W error`` this file was reported as a syntax error."""
        f = self._source(tmp_path)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            assert parse_file(f) is not None


class TestUnparsedFilesDetail:
    """The detail line is short and on one line, whatever the files hold."""

    def test_names_files_relative_to_the_project_with_reasons(
        self, tmp_path: Path
    ) -> None:
        pkg = tmp_path / "src" / "pkg"
        pkg.mkdir(parents=True)
        (pkg / "broken.py").write_text("def foo(\n")
        (pkg / "odd.py").mkdir()
        detail = unparsed_files_detail(
            [pkg / "odd.py", pkg / "broken.py", pkg / "broken.py"], tmp_path
        )
        assert detail is not None
        assert detail.startswith("Could not parse 2 file(s), skipped: ")
        assert "src/pkg/broken.py (syntax error on line 1: " in detail
        assert "src/pkg/odd.py (not a regular file)" in detail
        assert detail.index("broken.py") < detail.index("odd.py")

    def test_long_lists_are_cut_with_a_count(self, tmp_path: Path) -> None:
        paths = [tmp_path / f"m{index}.py" for index in range(8)]
        detail = unparsed_files_detail(paths, tmp_path, limit=3)
        assert detail is not None
        assert detail.startswith("Could not parse 8 file(s), skipped: m0.py (")
        assert "m2.py" in detail and "m3.py" not in detail
        assert detail.endswith(" (+5 more)")

    def test_reason_is_one_short_printable_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A reason quotes the file, an encoding name of any length included."""
        error = SyntaxError("first line\nsecond \x1b[31mline\udcff " + "y" * 500)
        f = tmp_path / "m.py"
        f.write_text("x = 1\n")
        with monkeypatch.context() as patched:
            _patch_parse(patched, error)
            reason = _reason(f)
        assert reason.startswith(
            "syntax error: first line\\nsecond \\x1b[31mline\\udcff yyy"
        )
        assert len(reason) == 200 and reason.endswith("...")
        assert reason.isprintable()

    @pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX file name")
    def test_line_stays_one_bounded_line_for_odd_and_long_paths(
        self, tmp_path: Path
    ) -> None:
        odd = tmp_path / "a\nb\x1b.py"
        deep = tmp_path.joinpath(*(["d" * 40] * 30), "missing.py")
        detail = unparsed_files_detail([deep, odd], tmp_path)
        assert detail is not None
        assert "\n" not in detail and "\x1b" not in detail
        assert len(detail) < 400
        assert "a\\nb\\x1b.py (file not found), dddd" in detail
        assert "ddd/missing.py (" in detail
