"""Tests for file reading and lookup, the file cache, constants and conftest."""

import contextlib
import datetime
import os
import runpy
import socket
import sys
import threading
import urllib.request
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from pycmdcheck import constants, fileio
from pycmdcheck.ast_cache import parse_file
from pycmdcheck.constants import (
    MAX_PROSE_BYTES,
    MAX_PYPROJECT_BYTES,
    MAX_SOURCE_BYTES,
    PYTHON_EOL_DATES,
    STDLIB_MODULE_NAMES,
)
from pycmdcheck.fileio import (
    NON_PROSE_SUFFIXES,
    FileCache,
    FileRead,
    clear_all_caches,
    decode_text,
    display_path,
    find_declared_file,
    find_declared_files,
    find_project_file,
    find_project_files,
    find_subdir,
    format_size,
    is_regular_file,
    is_safe_relative,
    os_error_text,
    plain_text,
    read_bytes_capped,
    read_ini,
    read_text_capped,
    read_whole_file,
    resolve_declared_file,
    try_read_text,
)
from pycmdcheck.pyproject_reader import get_project_table, get_table

needs_links = pytest.mark.skipif(
    sys.platform == "win32", reason="needs symlinks and /dev/null"
)


def _write(root: Path, *names: str, text: str = "text") -> None:
    """Write files byte for byte, so that a size is the same on every platform."""
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_bytes(text.encode())


def _shown(paths: list[Path], root: Path) -> list[str]:
    return [display_path(path, root) for path in paths]


class TestReading:
    """Tests for the capped, tolerant readers."""

    @needs_links
    def test_only_regular_files_are_read_and_links_are_followed(
        self, tmp_path: Path
    ) -> None:
        """Monorepos link python/LICENSE to ../LICENSE. A device is refused."""
        _write(tmp_path, "LICENSE", text="MIT License")
        (tmp_path / "link").symlink_to(tmp_path / "LICENSE")
        (tmp_path / "broken").symlink_to(tmp_path / "nowhere")
        (tmp_path / "device").symlink_to("/dev/null")
        assert is_regular_file(tmp_path / "link") is True
        assert read_bytes_capped(tmp_path / "link", 100) == (b"MIT License", False)
        for other in ("broken", "device", "missing", ".", "bad\0name"):
            assert is_regular_file(tmp_path / other) is False
            assert try_read_text(tmp_path / other) is None
        for refused in (tmp_path / "device", tmp_path):
            with pytest.raises(OSError, match="not a regular file"):
                read_text_capped(refused)

    def test_read_bytes_capped(self, tmp_path: Path) -> None:
        (tmp_path / "a.bin").write_bytes(b"0123456789")
        assert read_bytes_capped(tmp_path / "a.bin", 10) == (b"0123456789", False)
        assert read_bytes_capped(tmp_path / "a.bin", 4) == (b"0123", True)
        with pytest.raises(FileNotFoundError) as info:
            read_bytes_capped(tmp_path / "missing", 10)
        assert str(tmp_path) not in os_error_text(info.value)
        assert os_error_text(OSError("not a regular file")) == "not a regular file"
        with pytest.raises(OSError):
            read_bytes_capped(Path("bad\0name"), 10)

    @pytest.mark.parametrize(
        ("data", "text"),
        [
            (b"\xef\xbb\xbfcaf\xc3\xa9", "café"),
            ("MIT".encode("utf-16"), "MIT"),
            (b"\xfe\xff" + "MIT".encode("utf-16-be"), "MIT"),
            ("MIT".encode("utf-32"), "MIT"),
            ("José".encode("latin-1"), "Jos\ufffd"),
            (b"a\r\nb\rc\n", "a\nb\nc\n"),
        ],
    )
    def test_decode_text_never_fails(self, data: bytes, text: str) -> None:
        assert decode_text(data) == text

    def test_read_text_is_cut_at_the_cap(self, tmp_path: Path) -> None:
        (tmp_path / "README.md").write_bytes("ééé".encode())
        assert read_text_capped(tmp_path / "README.md", max_bytes=3) == "é\ufffd"
        assert try_read_text(tmp_path / "README.md", 2) == "é"
        assert read_text_capped.__defaults__ == (MAX_PROSE_BYTES,)
        assert try_read_text.__defaults__ == (MAX_PROSE_BYTES,)

    def test_read_ini(self, tmp_path: Path) -> None:
        """Latin-1 bytes, percent signs and duplicates do not hide the options."""
        cfg = tmp_path / "setup.cfg"
        cfg.write_bytes(
            "[metadata]\nauthor = José\ndescription = 100% typed, %(nothing)s\n"
            "[options]\nzip_safe = false\nzip_safe = true\n".encode("latin-1")
        )
        parser = read_ini(cfg)
        assert parser is not None
        assert parser.get("metadata", "description") == "100% typed, %(nothing)s"
        assert parser.get("options", "zip_safe") == "true"
        cfg.write_text("no section header\n")
        assert read_ini(cfg) is None
        assert read_ini(tmp_path) is None
        assert read_ini(tmp_path / "missing.cfg") is None

    @pytest.mark.parametrize(
        ("cut_after", "late_options"),
        [("[sectio", ["unfinished_option", "key"]), ("unfinished_opt", [])],
    )
    def test_read_ini_cut_inside_a_line_keeps_what_came_before(
        self, tmp_path: Path, cut_after: str, late_options: list[str]
    ) -> None:
        text = (
            "[options]\npython_requires = >=3.8\n"
            "[late]\nunfinished_option = 1\nkey = value\n[section1]\nlast = 1\n"
        )
        _write(tmp_path, "setup.cfg", text=text)
        cap = text.index(cut_after) + len(cut_after)
        parser = read_ini(tmp_path / "setup.cfg", max_bytes=cap)
        assert parser is not None
        assert parser.sections() == ["options", "late"]
        assert parser.options("late") == late_options

    def test_read_whole_file(self, tmp_path: Path) -> None:
        source = tmp_path / "a.py"
        source.write_bytes(b"x = 1\n")
        assert read_whole_file(source, 100) == FileRead(b"x = 1\n")
        missing = FileRead(b"", "file not found", True)
        assert read_whole_file(tmp_path / "missing.py", 100) == missing
        assert read_whole_file(source / "under-a-file", 100) == missing
        assert read_whole_file(tmp_path, 100) == FileRead(b"", "not a regular file")
        # A file over the cap is refused, never returned in part.
        assert read_whole_file(source, 5) == FileRead(b"", "larger than 5 bytes")
        assert format_size(MAX_SOURCE_BYTES) == "2 MiB"
        assert format_size(1536) == "1.5 KiB"


class TestFindProjectFile:
    """Tests for find_project_file() and find_project_files()."""

    @pytest.mark.parametrize(
        ("stems", "name"),
        [
            (["README"], "README.md"),
            (["README"], "ReadMe.Rst"),
            (["README"], "readme"),
            (["CHANGELOG", "NEWS", "HISTORY"], "History.md"),
        ],
    )
    def test_letter_case_does_not_matter(
        self, tmp_path: Path, stems: list[str], name: str
    ) -> None:
        """readme.md is found on Linux as on macOS, under its on-disk name."""
        _write(tmp_path, name)
        assert find_project_file(tmp_path, stems) == tmp_path / name

    def test_case_sensitive_listing_is_matched_on_any_platform(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Feed a listing as a case-sensitive filesystem would report it."""
        listing = [("README.md", True, False), ("readme.md", True, False)]
        monkeypatch.setattr(fileio, "_list_dir", lambda directory: listing)
        found = find_project_files(tmp_path, ["README"])
        assert _shown(found, tmp_path) == ["README.md", "readme.md"]

    @needs_links
    def test_only_regular_files_count(self, tmp_path: Path) -> None:
        (tmp_path / "README").mkdir()
        (tmp_path / "README.md").symlink_to("/dev/null")
        assert find_project_file(tmp_path, ["README"]) is None

    def test_listed_extensions(self, tmp_path: Path) -> None:
        """Stem order decides first, then extension order, and nothing else fits."""
        _write(tmp_path, "README", "README.txt", "README.md", "NEWS.rst")
        _write(tmp_path, "README.html", "READMEFIRST.md", "README-old.md")
        found = find_project_files(tmp_path, ["NEWS", "README"])
        assert _shown(found, tmp_path) == [
            "NEWS.rst",
            "README.md",
            "README.txt",
            "README",
        ]
        first = find_project_file(tmp_path, ["README"], ("", ".txt"))
        assert first == tmp_path / "README"
        # A stem may itself contain a dot.
        found = find_project_files(tmp_path, ["README.TXT", "NEWS.RST"], ("",))
        assert _shown(found, tmp_path) == ["README.txt", "NEWS.rst"]

    def test_any_extension(self, tmp_path: Path) -> None:
        """Dual-licensed projects ship LICENSE-MIT and LICENSE-APACHE."""
        _write(tmp_path, "LICENSE-MIT", "LICENSE_BSD.txt", "license.rst")
        _write(tmp_path, "COPYING.LESSER", "LICENSEE.txt", "MIT-LICENSE")
        # Code, backups and pictures are not the document itself.
        _write(tmp_path, "license_check.py", "LICENSE.rej", "license-banner.png")
        found = find_project_files(tmp_path, ["LICENSE", "COPYING"], None)
        # A name that goes on with "." comes before one with "-" or "_".
        assert _shown(found, tmp_path) == [
            "license.rst",
            "LICENSE-MIT",
            "LICENSE_BSD.txt",
            "COPYING.LESSER",
        ]
        # A listed extension is taken at its word.
        found = find_project_files(tmp_path, ["LICENSE"], (".py", ".rej"))
        assert _shown(found, tmp_path) == ["LICENSE.rej"]
        # Suffixes that licenses and READMEs really use. README.pl is Polish.
        assert not {"", ".md", ".txt", ".html", ".pdf", ".pl"} & NON_PROSE_SUFFIXES

    def test_subdirectories_are_searched_in_order_without_regard_to_case(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "Docs/contributing.md", ".github/CONTRIBUTING.md")
        dirs = ("", ".github", "docs", "missing")
        found = find_project_files(tmp_path, ["CONTRIBUTING"], subdirs=dirs)
        assert _shown(found, tmp_path) == [
            ".github/CONTRIBUTING.md",
            "Docs/contributing.md",
        ]
        assert find_project_files(tmp_path / "nowhere", ["README"]) == []


class TestFindSubdir:
    """find_subdir: a directory by name, in any letter case, never through a link."""

    def test_on_disk_name_in_any_letter_case(self, tmp_path: Path) -> None:
        (tmp_path / "Docs" / "Guide").mkdir(parents=True)
        assert find_subdir(tmp_path, "docs") == tmp_path / "Docs"
        assert find_subdir(tmp_path, "docs/guide/") == tmp_path / "Docs" / "Guide"
        assert find_subdir(tmp_path, "docs\\guide") == tmp_path / "Docs" / "Guide"

    @pytest.mark.parametrize("name", ["", ".", "./", "missing", "docs/missing", ".."])
    def test_nothing_to_return(self, tmp_path: Path, name: str) -> None:
        (tmp_path / "docs").mkdir()
        assert find_subdir(tmp_path, name) is None

    def test_a_file_is_not_a_directory(self, tmp_path: Path) -> None:
        (tmp_path / "docs").write_text("")
        assert find_subdir(tmp_path, "docs") is None
        assert find_subdir(tmp_path / "missing", "docs") is None

    @needs_links
    def test_a_link_is_not_followed(self, tmp_path: Path) -> None:
        (tmp_path / "outside" / "inner").mkdir(parents=True)
        root = tmp_path / "project"
        root.mkdir()
        (root / "docs").symlink_to(tmp_path / "outside", target_is_directory=True)
        assert find_subdir(root, "docs") is None
        assert find_subdir(root, "docs/inner") is None


class TestShownText:
    """Tests for display_path() and plain_text()."""

    @pytest.mark.parametrize(
        ("name", "shown"),
        [
            ("docs/说明 x[1].md", "docs/说明 x[1].md"),
            ("pkg/a\nb.py", "pkg/a\\nb.py"),
            ("pkg/\x1b[31mred.py", "pkg/\\x1b[31mred.py"),
            ("pkg/\u202egnp.py", "pkg/\\u202egnp.py"),
            ("pkg/\udcff.py", "pkg/\\udcff.py"),
        ],
        ids=["plain", "newline", "escape", "bidi", "surrogate"],
    )
    def test_display_path_is_one_printable_line(self, name: str, shown: str) -> None:
        """A name that is not valid UTF-8 carries a lone surrogate on Linux."""
        text = display_path(Path("/r") / name, Path("/r"))
        assert text == shown
        assert text.isprintable() and text.encode("utf-8")

    def test_display_path_outside_the_root_or_very_long(self) -> None:
        assert display_path(Path("/elsewhere/LICENSE"), Path("/r")) == "LICENSE"
        deep = Path("/r").joinpath(*(["directory"] * 400), "module.py")
        text = display_path(deep, Path("/r"))
        assert len(text) == 240
        assert text.startswith("directory/directory/")
        assert text.endswith("/directory/module.py")
        assert "..." in text

    @pytest.mark.parametrize(
        ("text", "shown"),
        [
            ("a\x00b\nc", "a\\x00b\\nc"),
            ("zero\u200bwidth no\xa0break", "zero\\u200bwidth no\\xa0break"),
            ("café 说明 🙂", "café 说明 🙂"),
            pytest.param("x" * 200, "x" * 200, id="200-characters"),
            pytest.param("x" * 201, "x" * 197 + "...", id="201-characters"),
        ],
    )
    def test_plain_text(self, text: str, shown: str) -> None:
        assert plain_text(text) == shown

    def test_plain_text_limit_and_tail(self) -> None:
        assert plain_text("k" * 500 + " (at line 1, column 9)", 60, tail=22) == (
            "k" * 35 + "... (at line 1, column 9)"
        )
        # The limit holds when escapes make the text longer, and for odd limits.
        assert len(plain_text("\n" * 150)) == 200
        assert len(plain_text("\n" * 500, 80, tail=30)) == 80
        assert plain_text("abcdef", 0) == "..."
        assert plain_text("abcdef", 5, tail=99) == "...ef"


class TestDeclaredPaths:
    """Tests for is_safe_relative(), resolve_declared_file() and the glob helpers."""

    def test_is_safe_relative(self) -> None:
        for safe in ("LICENSE", "LICENSES/*.txt", "docs/**/LICENSE*", "a/./b"):
            assert is_safe_relative(safe) is True
        unsafe: list[object] = ["", "   ", "/etc/hosts", "../x", "a/../../b"]
        unsafe += ["..\\x", "C:\\Windows\\win.ini", "c:relative", "\\\\server\\x"]
        unsafe += ["bad\0name", "x" * 5000, None, ["LICENSE"]]
        for value in unsafe:
            assert is_safe_relative(value) is False

    def test_resolve_declared_file(self, tmp_path: Path) -> None:
        project = tmp_path / "project"
        _write(project, "Legal/License.txt", "NOTICE[1].txt")
        _write(tmp_path, "outside.txt")
        found = resolve_declared_file(project, "Legal/License.txt")
        assert found == project / "Legal" / "License.txt"
        # The same declaration resolves alike on macOS and on Linux.
        assert resolve_declared_file(project, "./legal//LICENSE.TXT") == found
        # The path is taken literally.
        assert resolve_declared_file(project, "NOTICE[1].txt") is not None
        outside = str(tmp_path / "outside.txt")
        missing: list[object] = ["NOTIC*", "NOTICE[12].txt", "Legal", "missing.txt", ""]
        missing += ["../outside.txt", outside, ".", "./", {"file": "Legal/License.txt"}]
        for value in missing:
            assert resolve_declared_file(project, value) is None

    def test_resolve_declared_file_prefers_the_exact_spelling(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        listing = [("LICENSE", True, False), ("license", True, False)]
        monkeypatch.setattr(fileio, "_list_dir", lambda directory: listing)
        assert resolve_declared_file(tmp_path, "license") == tmp_path / "license"
        assert resolve_declared_file(tmp_path, "License") == tmp_path / "LICENSE"

    def test_glob_patterns(self, tmp_path: Path) -> None:
        _write(tmp_path, "LICENSE-MIT", "LICENSE-APACHE", "Licence.txt", "NOTICE")
        _write(tmp_path, "LICENSES/MIT.txt", "LICENSES/.DS_Store", ".venv/x/LICENSE")
        _write(tmp_path, "src/_vendor/six/LICENSE", "src/_vendor/attr/LICENSE")

        def names(*patterns: object) -> list[str]:
            return _shown(find_declared_files(tmp_path, patterns), tmp_path)

        vendored = ["src/_vendor/attr/LICENSE", "src/_vendor/six/LICENSE"]
        assert names("licen[cs]e*") == ["LICENSE-APACHE", "LICENSE-MIT", "Licence.txt"]
        assert names("LICENSES/?IT.txt") == ["LICENSES/MIT.txt"]
        assert names("src/_vendor/*/LICENSE") == vendored
        # "**" and "*" leave hidden names alone unless the pattern names them.
        assert names("**/LICENSE") == vendored
        assert names("**/**/./license") == vendored
        assert names("LICENSES/**") == ["LICENSES/MIT.txt"]
        assert names("LICENSES/.DS_*") == ["LICENSES/.DS_Store"]
        # Pattern order first, each file once.
        assert names("NOTICE", "LICENSE-M*", "NOTICE") == ["NOTICE", "LICENSE-MIT"]
        # Directories never match, and neither does a pattern with no hit.
        assert names("LICENSES", "COPYING*") == []
        assert len(find_declared_files(tmp_path, ["**/*"], limit=3)) == 3

    def test_unsafe_and_malformed_patterns_are_skipped(self, tmp_path: Path) -> None:
        """These used to escape the project or raise NotImplementedError."""
        project = tmp_path / "project"
        _write(project, "LICENSE")
        _write(tmp_path, "outside.txt")
        patterns: list[object] = ["../outside.txt", "../../**/*.txt", "/etc/*", ""]
        patterns += ["[", "[!]", ".", "./", None, 7, ["LICENSE"]]
        assert find_declared_files(project, patterns) == []
        found = find_declared_file(project, [*patterns, "LICENSE"])
        assert found == project / "LICENSE"

    @pytest.mark.parametrize(
        ("patterns", "expected"),
        [("LICENSE*", ["LICENSE-MIT"]), (None, []), (5, []), ({"paths": ["x"]}, [])],
    )
    def test_value_that_is_not_a_list_of_patterns(
        self, tmp_path: Path, patterns: object, expected: list[str]
    ) -> None:
        """A bare string was read one character at a time, and "*" hit everything."""
        _write(tmp_path, "CHANGELOG.md", "LICENSE-MIT", "paths")
        found = find_declared_files(tmp_path, patterns)
        assert _shown(found, tmp_path) == expected
        assert find_declared_file(tmp_path, patterns) == (found[0] if found else None)

    @needs_links
    def test_directory_links(self, tmp_path: Path) -> None:
        """A package in a monorepo links to the license directory above it.

        Naming the link, or matching it with a wildcard, follows it. ``**``
        does not, which keeps it out of link loops.
        """
        project = tmp_path / "python"
        _write(tmp_path, "LICENSES/MIT.txt")
        _write(project, "LICENSE")
        (project / "LICENSES").symlink_to(tmp_path / "LICENSES")
        (project / "loop").symlink_to(project, target_is_directory=True)

        inside = project / "LICENSES" / "MIT.txt"
        assert resolve_declared_file(project, "LICENSES/MIT.txt") == inside
        assert find_declared_files(project, ["LICEN*/MIT.txt"]) == [inside]
        assert find_declared_files(project, ["**/MIT.txt"]) == []
        assert find_declared_files(project, ["**/LICENSE"]) == [project / "LICENSE"]


class _Counter:
    """Build function that records how often it ran."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, path: Path, read: FileRead) -> FileRead:
        self.calls += 1
        return read


def _age(path: Path, seconds: float = 3600.0) -> None:
    """Move a file's modification time into the past."""
    old = path.stat().st_mtime - seconds
    os.utime(path, (old, old))


class TestFileCache:
    """Tests for FileCache and clear_all_caches()."""

    def test_same_size_rewrite_with_identical_timestamp(self, tmp_path: Path) -> None:
        """Timestamps can be too coarse to show two writes in a row."""
        target = tmp_path / "a.txt"
        target.write_bytes(b"1.0.0")
        stamp = target.stat().st_mtime_ns
        cache = FileCache(_Counter(), 1024)
        assert cache.get(target).data == b"1.0.0"
        target.write_bytes(b"2.0.0")
        os.utime(target, ns=(stamp, stamp))
        assert cache.get(target).data == b"2.0.0"

    @pytest.mark.parametrize(("aged", "reads"), [(True, 0), (False, 1)])
    def test_unchanged_file_is_not_rebuilt(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, aged: bool, reads: int
    ) -> None:
        """An old file is trusted on its size and time, a young one on content."""
        target = tmp_path / "a.txt"
        target.write_text("one")
        if aged:
            _age(target)
        build = _Counter()
        cache = FileCache(build, 1024)
        first = cache.get(target)
        seen: list[Path] = []
        real_read = fileio.read_whole_file

        def counting_read(path: Path, max_bytes: int) -> FileRead:
            seen.append(path)
            return real_read(path, max_bytes)

        monkeypatch.setattr(fileio, "read_whole_file", counting_read)
        # A relative path is the same entry as the absolute one.
        monkeypatch.chdir(tmp_path)
        assert cache.get(Path("a.txt")) is first
        assert (len(seen), build.calls, len(cache)) == (reads, 1, 1)

    def test_every_change_of_an_old_file_rebuilds(self, tmp_path: Path) -> None:
        """Missing, unreadable and oversized files are values like any other."""
        target = tmp_path / "a.txt"
        build = _Counter()
        cache = FileCache(build, 4)
        assert cache.get(target) is cache.get(target)
        assert cache.get(target).missing is True
        target.write_text("one")
        _age(target)
        assert cache.get(target).data == b"one"
        _age(target, 7200.0)
        assert cache.get(target).data == b"one"
        target.write_text("too long")
        _age(target)
        assert cache.get(target) is cache.get(target)
        assert cache.get(target).problem == "larger than 4 bytes"
        target.unlink()
        target.mkdir()
        assert cache.get(target).problem == "not a regular file"
        assert build.calls == 5

    def test_store_false_keeps_nothing_but_uses_what_is_there(
        self, tmp_path: Path
    ) -> None:
        _write(tmp_path, "a.txt")
        build = _Counter()
        cache = FileCache(build, 1024)
        cache.get(tmp_path / "a.txt", store=False)
        cache.get(tmp_path / "a.txt", store=False)
        assert (build.calls, len(cache)) == (2, 0)
        kept = cache.get(tmp_path / "a.txt")
        assert cache.get(tmp_path / "a.txt", store=False) is kept

    def test_threads_share_one_build(self, tmp_path: Path) -> None:
        _write(tmp_path, "a.txt")
        entered = threading.Barrier(2, timeout=0.1)
        calls: list[Path] = []

        def build(path: Path, read: FileRead) -> object:
            calls.append(path)
            # Two builds at once would meet here. A single one waits in vain.
            with contextlib.suppress(threading.BrokenBarrierError):
                entered.wait()
            return object()

        cache = FileCache(build, 1024)
        with ThreadPoolExecutor(2) as pool:
            first, second = pool.map(cache.get, [tmp_path / "a.txt"] * 2)
        assert first is second and len(calls) == 1

    def test_clear_all_caches(self, tmp_path: Path) -> None:
        """One call empties every cache, the pyproject and AST ones included."""
        _write(tmp_path, "pyproject.toml", text='[project]\nname = "demo"\n')
        _write(tmp_path, "mod.py", text="x = 1\n")
        other = FileCache(_Counter(), 1024)
        other.get(tmp_path / "mod.py")
        tree = parse_file(tmp_path / "mod.py")
        assert parse_file(tmp_path / "mod.py") is tree
        assert get_project_table(tmp_path) == {"name": "demo"}

        clear_all_caches()

        assert len(other) == 0
        assert parse_file(tmp_path / "mod.py") is not tree


class TestConstants:
    """Tests for the standard-library names, the end-of-life table and the limits."""

    def test_stdlib_names_cover_every_supported_version(self) -> None:
        """A new Python release must not leave the static set behind."""
        missing = sorted(sys.stdlib_module_names - STDLIB_MODULE_NAMES)
        assert not missing, (
            "run tools/gen_stdlib_names.py with this interpreter to add: "
            + ", ".join(missing)
        )
        assert isinstance(STDLIB_MODULE_NAMES, frozenset)
        assert all("." not in name for name in STDLIB_MODULE_NAMES)
        # Added or removed along the way, and names that are never listed.
        expected = {"tomllib", "distutils", "imp", "cgi", "__main__", "__future__"}
        assert expected <= STDLIB_MODULE_NAMES
        third_party = {"numpy", "setuptools", "pkg_resources", "tomli"}
        assert not third_party & STDLIB_MODULE_NAMES

    def test_generated_file_matches_the_generator(self, repo_root: Path) -> None:
        """The data module is exactly what tools/gen_stdlib_names.py writes."""
        script = repo_root / "tools" / "gen_stdlib_names.py"
        data_file = repo_root / "src" / "pycmdcheck" / "_stdlib_names.py"
        if not script.is_file():
            pytest.skip("tools/gen_stdlib_names.py is not part of this checkout")
        # run_path compiles in memory, so no __pycache__ appears under tools/.
        generator = runpy.run_path(str(script), run_name="gen_stdlib_names")
        names, versions = generator["read_existing"](data_file)
        assert names == set(STDLIB_MODULE_NAMES)
        assert generator["EXTRA_NAMES"] <= STDLIB_MODULE_NAMES
        assert {"3.11", "3.12", "3.13", "3.14"} <= versions
        assert generator["render"](names, versions) == data_file.read_text("utf-8")

    def test_eol_table(self) -> None:
        minors = [int(version.split(".")[1]) for version in PYTHON_EOL_DATES]
        assert minors == list(range(minors[0], minors[-1] + 1))
        dates = list(PYTHON_EOL_DATES.values())
        assert dates == sorted(dates)
        assert PYTHON_EOL_DATES["3.9"] == datetime.date(2025, 10, 31)
        assert PYTHON_EOL_DATES["3.10"] == datetime.date(2026, 10, 1)
        # A scheduled version is not flagged before the last day of its month.
        assert PYTHON_EOL_DATES["3.11"] == datetime.date(2027, 10, 31)
        assert constants.EOL_GRACE_DAYS == 180
        assert constants.PYTHON_EOL_VERSIONS == {"3.8": (2024, 10), "3.9": (2025, 10)}

    def test_limits(self) -> None:
        assert 0 < MAX_PROSE_BYTES <= MAX_PYPROJECT_BYTES < MAX_SOURCE_BYTES
        assert MAX_SOURCE_BYTES <= 4 * 1024 * 1024
        assert 0 < constants.PY_TYPED_MIN_ANNOTATED_RATIO <= 1
        # The package being checked sets the timeout, so the bounds are fixed.
        assert (constants.URL_TIMEOUT_MIN, constants.URL_TIMEOUT_MAX) == (1, 30)
        assert (constants.URL_TIMEOUT, constants.URL_OVERALL_DEADLINE) == (10, 60)
        kept_from_0_1_1 = (
            "DEFAULT_TIMEOUT LONG_TIMEOUT MAX_DETAIL_LINES MAX_WORKERS "
            "MIN_README_WORDS MIN_LICENSE_LENGTH MAX_DOCSTRING_FILES "
            "MAX_TYPE_SCAN_FILES MAX_URL_CHECKS MIN_CHANGELOG_LENGTH"
        )
        for name in kept_from_0_1_1.split():
            assert isinstance(getattr(constants, name), int)


class TestSuiteIsolation:
    """Tests for the conftest fixtures that keep the suite off the network."""

    def test_urllib_requests_are_stubbed(self) -> None:
        with urllib.request.urlopen("https://example.com", timeout=1) as response:
            assert response.status == 200
        request = urllib.request.Request("https://example.org/x", method="HEAD")
        opened = urllib.request.build_opener().open(request, timeout=1)
        assert opened.geturl() == "https://example.org/x"

    @pytest.mark.real_urlopen
    def test_only_the_local_machine_can_be_reached(self) -> None:
        with pytest.raises(Exception, match="must not use the network"):
            urllib.request.urlopen("http://192.0.2.1/", timeout=1)
        with pytest.raises(RuntimeError, match="must not use the network"):
            socket.getaddrinfo("example.com", 443)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as server:
            server.bind(("127.0.0.1", 0))
            server.listen(1)
            port = server.getsockname()[1]
            assert socket.getaddrinfo("localhost", port)
            with socket.create_connection(("127.0.0.1", port), timeout=5):
                pass

    @pytest.mark.tools("pycmdcheck-test-tool-that-does-not-exist")
    def test_tools_marker_skips_when_a_tool_is_missing(self) -> None:
        pytest.fail("this test must be skipped")

    def test_environment(self, repo_root: Path, pytestconfig: pytest.Config) -> None:
        assert os.environ["COLUMNS"] == "80"
        assert (repo_root / "tests" / "conftest.py").is_file()
        registered = "\n".join(pytestconfig.getini("markers"))
        assert "tools(*names)" in registered and "real_urlopen" in registered


class TestMakePackage:
    """Tests for the make_package fixture."""

    def test_default_is_a_complete_src_layout_project(
        self, make_package: Callable[..., Path]
    ) -> None:
        root = make_package()
        project = get_project_table(root)
        assert (project["name"], project["version"]) == ("mypackage", "0.1.0")
        assert {"authors", "license", "readme", "requires-python"} <= set(project)
        init = root / "src" / "mypackage" / "__init__.py"
        assert '__version__ = "0.1.0"' in init.read_text()
        words = (root / "README.md").read_text().split()
        assert len(words) >= constants.MIN_README_WORDS
        assert "MIT License" in (root / "LICENSE").read_text()
        assert (root / "tests" / "test_mypackage.py").is_file()
        assert make_package() != root

    @pytest.mark.parametrize(
        ("layout", "backend", "source", "build_backend"),
        [
            ("src", "setuptools", "src/my_pkg/__init__.py", "setuptools.build_meta"),
            ("flat", "flit", "my_pkg/__init__.py", "flit_core.buildapi"),
            ("module", "poetry", "my_pkg.py", "poetry.core.masonry.api"),
            ("none", None, "pyproject.toml", None),
        ],
    )
    def test_layouts_and_backends(
        self,
        make_package: Callable[..., Path],
        layout: str,
        backend: str | None,
        source: str,
        build_backend: str | None,
    ) -> None:
        root = make_package("my-pkg", layout=layout, backend=backend)
        assert (root / source).is_file()
        assert get_table(root, "build-system").get("build-backend") == build_backend
        # Poetry keeps its metadata in [tool.poetry], with no [project] table.
        assert (get_project_table(root) == {}) is (backend == "poetry")
