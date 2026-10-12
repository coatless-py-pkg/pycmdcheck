"""Tests for the pyproject.toml, setup.cfg and setup.py reader."""

import re
from pathlib import Path
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet

from pycmdcheck import pyproject_reader
from pycmdcheck.constants import MAX_PYPROJECT_BYTES, MAX_TOML_DEPTH
from pycmdcheck.pyproject_reader import (
    POETRY_OPTIONAL_EXTRA,
    SETUP_PY_DYNAMIC,
    clear_cache,
    get_dev_dependencies,
    get_dict,
    get_effective_project_table,
    get_list,
    get_number,
    get_project_table,
    get_setup_cfg_value,
    get_setup_py_keywords,
    get_str,
    get_str_dict,
    get_str_list,
    get_table,
    get_tool_table,
    is_dynamic,
    parse_requirement,
    parse_specifier_set,
    parse_version,
    poetry_constraint_to_pep440,
    poetry_dependency_to_requirements,
    project_metadata_source,
    pyproject_error,
    pyproject_status,
    read_pyproject,
)

# More digits than int() converts, which packaging answers with a plain
# ValueError, and more parentheses than its marker parser can nest.
DIGITS = "9" * 5000
PARENS = "(" * 3000

DEEP = MAX_TOML_DEPTH + 5
TOO_DEEP = f"more than {MAX_TOML_DEPTH} deep"
# Content of an unusable pyproject.toml (None is a directory), what
# read_pyproject() raises for it, and part of the reason reported.
BROKEN: dict[str, tuple[bytes | None, type[Exception], str]] = {
    "invalid": (b"[project\nname = 'x'\n", ValueError, "(at line 1, column 9)"),
    "bom": (b"\xef\xbb\xbf[project]\n", ValueError, "UTF-8 byte order mark"),
    "latin1": (
        b"[project]\nname = 'caf\xe9'\n",
        UnicodeDecodeError,
        "pyproject.toml is not valid UTF-8 (invalid continuation byte at byte 21)",
    ),
    "directory": (None, OSError, "could not be read: not a regular file"),
    "deep-for-the-parser": (b"y = " + b"[" * 3000 + b"]" * 3000, ValueError, TOO_DEEP),
    "deep-for-a-copy": (b"y = " + b"[" * DEEP + b"]" * DEEP, ValueError, TOO_DEEP),
    "long-dotted-key": (b"a." * MAX_TOML_DEPTH + b"a = 1\n", ValueError, TOO_DEEP),
}


def _write(root: Path, text: str) -> Path:
    (root / "pyproject.toml").write_text(text)
    return root


def _count_calls(
    monkeypatch: pytest.MonkeyPatch, owner: object, name: str
) -> list[tuple[object, ...]]:
    """Replace ``owner.name`` by a wrapper that records its arguments."""
    calls: list[tuple[object, ...]] = []
    real = getattr(owner, name)

    def wrapper(*args: object) -> object:
        calls.append(args)
        return real(*args)

    monkeypatch.setattr(owner, name, wrapper)
    return calls


class TestLoading:
    """read_pyproject(), pyproject_status() and pyproject_error()."""

    def test_valid_file(self, temp_package: Path) -> None:
        data = read_pyproject(temp_package)
        assert data is not None and data["project"]["name"] == "mypackage"
        assert read_pyproject(temp_package) is data
        assert pyproject_status(temp_package) == "ok"
        assert pyproject_error(temp_package) is None

    def test_missing_file(self, tmp_path: Path) -> None:
        for root in (tmp_path, tmp_path / "nowhere"):
            assert read_pyproject(root) is None
            assert pyproject_status(root) == "missing"
            assert pyproject_error(root) is None

    def test_empty_file_is_ok(self, tmp_path: Path) -> None:
        assert pyproject_status(_write(tmp_path, "")) == "ok"
        assert read_pyproject(tmp_path) == {}

    @pytest.mark.parametrize("kind", BROKEN)
    def test_unusable_file(self, tmp_path: Path, kind: str) -> None:
        """Only read_pyproject() raises. The rest reads as empty, with a reason."""
        content, exception, reason = BROKEN[kind]
        if content is None:
            (tmp_path / "pyproject.toml").mkdir()
        else:
            (tmp_path / "pyproject.toml").write_bytes(content)
        for _ in range(2):  # not only on the first call
            with pytest.raises(exception):
                read_pyproject(tmp_path)
        assert pyproject_status(tmp_path) == "unparseable"
        error = pyproject_error(tmp_path)
        assert error is not None and reason in error and "\n" not in error
        assert get_table(tmp_path) == get_table(tmp_path, "build-system") == {}
        assert get_project_table(tmp_path) == {}
        assert get_effective_project_table(tmp_path) == {}
        assert get_tool_table(tmp_path, "pycmdcheck") == {}
        assert get_dev_dependencies(tmp_path) == []
        assert project_metadata_source(tmp_path) == "none"

    def test_parser_message_is_kept(self, tmp_path: Path) -> None:
        error = pyproject_error(_write(tmp_path, '[project]\nname = "demo\n'))
        assert error is not None and error.endswith("(at line 2, column 13)")
        with pytest.raises(pyproject_reader.tomllib.TOMLDecodeError) as raised:
            read_pyproject(tmp_path)
        assert str(raised.value) == error

    def test_long_parser_message_is_cut_in_the_middle(self, tmp_path: Path) -> None:
        """The parser quotes a key in full, however long it is."""
        key = "k" * 2000
        error = pyproject_error(_write(tmp_path, f'x = {{"{key}" = 1, "{key}" = 2}}\n'))
        assert error is not None and len(error) == 200
        assert "kkk...kkk" in error
        assert re.search(r" \(at line 1, column 40\d\d\)$", error)

    def test_nesting_at_the_limit_is_ok(self, tmp_path: Path) -> None:
        depth = MAX_TOML_DEPTH - 1
        _write(tmp_path, "y = " + "[" * depth + "]" * depth + "\n")
        assert pyproject_status(tmp_path) == "ok"
        assert get_table(tmp_path) == read_pyproject(tmp_path)

    def test_file_over_the_size_cap_is_refused(self, tmp_path: Path) -> None:
        content = b'[project]\nname = "demo"\n'.ljust(MAX_PYPROJECT_BYTES + 1, b"#")
        (tmp_path / "pyproject.toml").write_bytes(content)
        assert pyproject_status(tmp_path) == "unparseable"
        assert pyproject_error(tmp_path) == "pyproject.toml is larger than 1 MiB"
        assert get_project_table(tmp_path) == {}


class TestCaching:
    """A file is parsed once, and again only after it changes."""

    def test_an_edit_is_seen_without_clearing(self, tmp_path: Path) -> None:
        assert pyproject_status(tmp_path) == "missing"
        assert pyproject_status(_write(tmp_path, "[project")) == "unparseable"
        _write(tmp_path, '[project]\nversion = "1.0.0"\n')
        assert get_project_table(tmp_path) == {"version": "1.0.0"}
        _write(tmp_path, '[project]\nversion = "2.0.0"\n')
        assert get_project_table(tmp_path) == {"version": "2.0.0"}
        assert get_effective_project_table(tmp_path) == {"version": "2.0.0"}
        (tmp_path / "pyproject.toml").unlink()
        assert pyproject_status(tmp_path) == "missing"

    @pytest.mark.parametrize("text", ['[tool.poetry]\nname = "demo"\n', "[project"])
    def test_file_is_parsed_and_translated_once(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, text: str
    ) -> None:
        _write(tmp_path, text)
        parsed = _count_calls(monkeypatch, pyproject_reader.tomllib, "loads")
        translated = _count_calls(monkeypatch, pyproject_reader, "_effective_project")
        for _ in range(3):
            get_project_table(tmp_path)
            get_effective_project_table(tmp_path)
            get_dev_dependencies(tmp_path)
            pyproject_status(tmp_path)
        assert len(parsed) == 1
        assert len(translated) <= 1

    def test_clear_cache(self, temp_package: Path) -> None:
        first = read_pyproject(temp_package)
        clear_cache()
        second = read_pyproject(temp_package)
        assert first == second and first is not second


class TestTables:
    """get_table() and the accessors built on it."""

    def test_get_table_follows_a_key_path(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            '[project]\nname = "demo"\n'
            '[tool.hatch.envs.default]\ndependencies = ["pytest"]\n'
            '[tool.other]\nvalue = "text"\n',
        )
        assert sorted(get_table(tmp_path)) == ["project", "tool"]
        assert get_project_table(tmp_path) == {"name": "demo"}
        assert get_tool_table(tmp_path, "other") == {"value": "text"}
        envs = get_table(tmp_path, "tool", "hatch", "envs")
        assert envs == {"default": {"dependencies": ["pytest"]}}
        assert get_table(tmp_path, "tool", "missing", "deeper") == {}
        assert get_table(tmp_path, "tool", "other", "value") == {}
        assert get_table(tmp_path, "tool", "other", "value", "deeper") == {}

    @pytest.mark.parametrize(
        "text", ['project = "x"\ntool = 5\n', "[tool]\nproject = 1\npycmdcheck = [1]\n"]
    )
    def test_value_that_is_not_a_table(self, tmp_path: Path, text: str) -> None:
        _write(tmp_path, text)
        assert get_project_table(tmp_path) == {}
        assert get_effective_project_table(tmp_path) == {}
        assert get_tool_table(tmp_path, "pycmdcheck") == {}
        assert project_metadata_source(tmp_path) == "none"

    def test_results_are_private_copies(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            '[project]\nname = "demo"\nclassifiers = ["A"]\n'
            "[tool.pycmdcheck.checks]\ndocs = { enabled = true }\n",
        )
        get_tool_table(tmp_path, "pycmdcheck")["checks"]["docs"]["enabled"] = False
        get_project_table(tmp_path)["classifiers"].append("B")
        get_effective_project_table(tmp_path)["classifiers"].append("C")
        get_table(tmp_path)["project"]["name"] = "changed"

        checks = get_tool_table(tmp_path, "pycmdcheck")["checks"]
        assert checks == {"docs": {"enabled": True}}
        assert get_project_table(tmp_path) == {"name": "demo", "classifiers": ["A"]}
        assert get_effective_project_table(tmp_path) == get_project_table(tmp_path)


class TestTypedGetters:
    """get_str() and its siblings return a default for a value of the wrong type."""

    def test_get_str(self) -> None:
        assert get_str({"name": "demo"}, "name") == "demo"
        assert get_str({"name": ""}, "name") == ""
        assert get_str({"name": 5}, "name") is None
        assert get_str({}, "name", "fallback") == "fallback"

    def test_get_list_and_get_str_list(self) -> None:
        values = ["click", "", {"name": "x"}, 3, None, "rich"]
        assert get_list({"k": values}, "k") is values
        assert get_str_list({"k": values}, "k") == ["click", "", "rich"]
        for wrong in ("requests", {"a": 1}, 5, None):
            assert get_list({"k": wrong}, "k") == get_str_list({"k": wrong}, "k") == []

    def test_get_dict_and_get_str_dict(self) -> None:
        urls = {"Home": "https://example.org", "Count": 3, "Docs": None}
        assert get_dict({"urls": urls}, "urls") is urls
        assert get_str_dict({"urls": urls}, "urls") == {"Home": "https://example.org"}
        for wrong in ("MIT", ["https://example.org"], 5, None):
            assert get_dict({"k": wrong}, "k") == get_str_dict({"k": wrong}, "k") == {}

    def test_get_number_keeps_the_result_within_limits(self) -> None:
        def read(value: object) -> float:
            return get_number({"t": value}, "t", 10, minimum=1, maximum=30)

        assert [read(v) for v in (5, 2.5, 600, 0, 10**400)] == [5, 2.5, 30, 1, 30]
        assert get_number({}, "t", 99, maximum=30) == 30
        assert get_number({"t": -5}, "t", 0.5) == -5

    @pytest.mark.parametrize("value", ["30", True, float("nan"), float("inf")])
    def test_get_number_rejects_what_is_not_a_finite_number(
        self, value: object
    ) -> None:
        assert get_number({"t": value}, "t", 10) == 10

    def test_is_dynamic(self) -> None:
        assert is_dynamic({"dynamic": ["readme", "version"]}, "version") is True
        assert is_dynamic({"dynamic": ["readme"]}, "version") is False
        # A string used to match by substring, and a number used to raise.
        for wrong in ("version", "conversion", 5, {"version": 1}):
            assert is_dynamic({"dynamic": wrong}, "version") is False

    @pytest.mark.parametrize("table", [None, "hello", ["name"]])
    def test_anything_that_is_not_a_table_is_empty(self, table: object) -> None:
        assert get_str(table, "name") is None
        assert get_list(table, "name") == get_str_list(table, "name") == []
        assert get_dict(table, "name") == get_str_dict(table, "name") == {}
        assert get_number(table, "name", 7) == 7
        assert is_dynamic(table, "version") is False


class TestTolerantParsing:
    """parse_version, parse_specifier_set and parse_requirement never raise."""

    def test_valid_text_is_parsed(self) -> None:
        version = parse_version(" 1.2.3 ")
        assert version is not None and version.release == (1, 2, 3)
        specifier = parse_specifier_set(">=3.9, <4")
        assert specifier is not None and "3.12.1" in specifier
        assert parse_specifier_set("") == SpecifierSet("")
        # A clause that compares text needs no version that can be parsed.
        assert parse_specifier_set("===1.0-custom") is not None
        requirement = parse_requirement("Rich[jupyter] >=13 ; python_version < '4'")
        assert requirement is not None
        assert (requirement.name, str(requirement.specifier)) == ("Rich", ">=13")

    @pytest.mark.parametrize("value", [None, 3, 3.9, True, ["1.0"], b"1.0"])
    def test_values_that_are_not_strings(self, value: object) -> None:
        assert parse_version(value) is None
        assert parse_specifier_set(value) is None
        assert parse_requirement(value) is None

    @pytest.mark.parametrize(
        "text", ["", "latest", "1.0 beta", pytest.param(DIGITS, id="5000-digits")]
    )
    def test_text_that_is_not_a_version(self, text: str) -> None:
        assert parse_version(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "three point ten",
            "^3.9",
            ">=",
            pytest.param(f">={DIGITS}", id="5000-digits"),
            pytest.param(f"==3.{DIGITS}.*", id="5000-digits-wildcard"),
        ],
    )
    def test_text_that_is_not_a_specifier(self, text: str) -> None:
        """packaging accepts the last one and fails when it is compared."""
        assert parse_specifier_set(text) is None

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "two words",
            "-e .",
            pytest.param(f"foo ; {PARENS}", id="3000-parens"),
            pytest.param(f"foo==1.{DIGITS}.*", id="5000-digits-wildcard"),
        ],
    )
    def test_text_that_is_not_a_requirement(self, text: str) -> None:
        assert parse_requirement(text) is None


class TestPoetryConstraints:
    """poetry_constraint_to_pep440()."""

    @pytest.mark.parametrize(
        ("constraint", "expected"),
        [
            # Caret: up to the next change of the leftmost non-zero part.
            ("^1.2.3", ">=1.2.3,<2"),
            ("^0.2.3", ">=0.2.3,<0.3"),
            ("^0.0.3", ">=0.0.3,<0.0.4"),
            ("^0.0", ">=0.0,<0.1"),
            ("^0", ">=0,<1"),
            # Tilde: up to the next minor version.
            ("~1.2.3", ">=1.2.3,<1.3"),
            ("~1", ">=1,<2"),
            # Wildcards and bare versions.
            ("1.2.*", "==1.2.*"),
            ("!=1.2.*", "!=1.2.*"),
            ("1.2.3", "==1.2.3"),
            ("=1.2.3", "==1.2.3"),
            ("1.x", "==1.*"),
            ("*", ""),
            # PEP 440 operators, separated by commas or spaces.
            ("~=1.4.2", "~=1.4.2"),
            (">= 7.5.1 <9", ">=7.5.1,<9"),
            ("^2.13.0, !=2.14.0", ">=2.13.0,<3,!=2.14.0"),
            # Alternatives keep only the lowest lower bound.
            ("^4.0 || ^3.2", ">=3.2"),
            (">=1.0,<2 | >=3.0", ">=1.0"),
            ("<2 || >3", ""),
            # A wildcard, a ">" and an exact version each state a lower bound.
            ("2.7.* || ^3.5", ">=2.7"),
            (">3 || ^2", ">=2"),
            ("1.5 || ~=3.1", ">=1.5"),
        ],
    )
    def test_translation(self, constraint: str, expected: str) -> None:
        assert poetry_constraint_to_pep440(constraint) == expected
        SpecifierSet(expected)

    @pytest.mark.parametrize(
        "constraint",
        ["", "  ", "latest", "^", "^1.0 ||", "1.0 - 2.0", ">1.2.*"]
        + [pytest.param(f"^{DIGITS}", id="5000-digits")]
        + [None, 3],
    )
    def test_what_cannot_be_translated(self, constraint: object) -> None:
        assert poetry_constraint_to_pep440(constraint) is None
        assert poetry_constraint_to_pep440(constraint, python=True) is None

    @pytest.mark.parametrize(
        ("constraint", "expected"),
        [
            ("^3.9", ">=3.9,<4"),
            ("^3.8 <3.12", ">=3.8,<4,<3.12"),
            ("~2.7 || ^3.5", ">=2.7"),
            ("3.11.4", "==3.11.4"),
            # Only for Python is a bare version the whole series.
            ("3.11", "==3.11.*"),
            ("3", ">=3,<4"),
        ],
    )
    def test_python_constraint(self, constraint: str, expected: str) -> None:
        assert poetry_constraint_to_pep440(constraint, python=True) == expected
        assert poetry_constraint_to_pep440("3.11") == "==3.11"


NT = "os_name == 'nt'"


class TestPoetryDependencies:
    """poetry_dependency_to_requirements()."""

    @pytest.mark.parametrize(
        ("spec", "expected"),
        [
            ("^2.31", ["foo>=2.31,<3"]),
            ("*", ["foo"]),
            ({"version": "^2.1", "optional": True}, ["foo>=2.1,<3"]),
            ({"version": "^0.3", "extras": ["a", "b"]}, ["foo[a,b]>=0.3,<0.4"]),
            ({"version": "*", "markers": NT}, ["foo"]),
            ({"version": "^2", "markers": NT}, [f"foo>=2,<3 ; {NT}"]),
            ({"git": "https://e.org/f.git"}, ["foo @ git+https://e.org/f.git"]),
            ({"url": "https://e.org/foo.whl"}, ["foo @ https://e.org/foo.whl"]),
            ({"path": "../foo"}, ["foo @ file:///../foo"]),
            ([{"version": "<2"}, {"version": "^2"}], ["foo<2", "foo>=2,<3"]),
            # What cannot be translated is dropped, down to the bare name.
            ("latest", ["foo"]),
            (5, ["foo"]),
            ({"version": f"^{DIGITS}"}, ["foo"]),
            ({"version": "^1", "markers": "not a marker"}, ["foo>=1,<2"]),
            ({"version": "^1", "markers": PARENS}, ["foo>=1,<2"]),
            ({"url": "not a url with spaces"}, ["foo"]),
        ],
    )
    def test_conversion(self, spec: object, expected: list[str]) -> None:
        assert poetry_dependency_to_requirements("foo", spec) == expected
        for requirement in expected:
            assert Requirement(requirement).name == "foo"

    @pytest.mark.parametrize("name", ["", "   ", "two words", "-dash", 5])
    def test_invalid_names_give_nothing(self, name: object) -> None:
        assert poetry_dependency_to_requirements(name, "^1.0") == []


POETRY_2 = (
    '[project]\nname = "demo"\ndescription = "from project"\n'
    'dynamic = ["version", "dependencies"]\n'
    '[tool.poetry]\nversion = "1.2.3"\ndescription = "from poetry"\n'
    '[tool.poetry.dependencies]\npython = "^3.10"\nrequests = "^2.31"\n'
    '[tool.poetry.group.dev.dependencies]\nblack = "^24.0"\n'
)


class TestEffectiveProjectTable:
    """get_effective_project_table() and project_metadata_source()."""

    def test_project_table_is_returned_as_written(self, temp_package: Path) -> None:
        declared = get_project_table(temp_package)
        assert declared["name"] == "mypackage"
        assert get_effective_project_table(temp_package) == declared
        assert project_metadata_source(temp_package) == "project"

    @pytest.mark.parametrize("text", [None, '[build-system]\nrequires = ["x"]\n'])
    def test_no_metadata(self, tmp_path: Path, text: str | None) -> None:
        if text is not None:
            _write(tmp_path, text)
        assert get_effective_project_table(tmp_path) == {}
        assert project_metadata_source(tmp_path) == "none"

    def test_legacy_poetry_is_translated(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            '[tool.poetry]\nname = "demo"\nversion = "1.2.3"\nlicense = "MIT"\n'
            'authors = ["Jane Doe <jane@example.com>", "Ann", "<a@e.org>", 5]\n'
            'classifiers = ["Programming Language :: Python :: 3"]\n'
            'homepage = "https://example.com"\n'
            '[tool.poetry.urls]\nTracker = "https://example.com/issues"\n'
            "[tool.poetry.dependencies]\n"
            'python = "^3.10"\n'
            'pygments = "^2.13.0"\n'
            'ipywidgets = { version = ">=7.5.1,<9", optional = true }\n'
            'PyMySQL = { version = "^1.1", optional = true }\n'
            'psycopg2 = { version = "^2.9", optional = true }\n'
            "[tool.poetry.extras]\n"
            'jupyter = ["ipywidgets"]\n'
            'mysql = ["pymysql", "cryptography"]\n',
        )
        assert get_effective_project_table(tmp_path) == {
            "name": "demo",
            "version": "1.2.3",
            "license": "MIT",
            "authors": [
                {"name": "Jane Doe", "email": "jane@example.com"},
                {"name": "Ann"},
                {"email": "a@e.org"},
            ],
            "classifiers": ["Programming Language :: Python :: 3"],
            "requires-python": ">=3.10,<4",
            "dependencies": ["pygments>=2.13.0,<3"],
            "optional-dependencies": {
                "jupyter": ["ipywidgets>=7.5.1,<9"],
                "mysql": ["PyMySQL>=1.1,<2", "cryptography"],
                POETRY_OPTIONAL_EXTRA: ["psycopg2>=2.9,<3"],
            },
            "urls": {
                "Homepage": "https://example.com",
                "Tracker": "https://example.com/issues",
            },
        }
        assert project_metadata_source(tmp_path) == "poetry"
        assert get_project_table(tmp_path) == {}

    @pytest.mark.parametrize(
        ("python", "expected"),
        [
            ('{ version = "^3.9" }', ">=3.9,<4"),
            ('">=3.8 <3.12"', ">=3.8,<3.12"),
            # Kept as written, so a check can say it could not read it.
            ('"three point ten"', "three point ten"),
            # Any Python, or no constraint at all, declares nothing.
            ('"*"', None),
            ("5", None),
        ],
    )
    def test_poetry_python(
        self, tmp_path: Path, python: str, expected: str | None
    ) -> None:
        _write(tmp_path, f"[tool.poetry.dependencies]\npython = {python}\n")
        assert get_effective_project_table(tmp_path).get("requires-python") == expected

    def test_poetry_2_fills_in_what_project_lacks(self, tmp_path: Path) -> None:
        project = get_effective_project_table(_write(tmp_path, POETRY_2))
        assert project == {
            "name": "demo",
            "description": "from project",
            "dynamic": ["version", "dependencies"],
            "version": "1.2.3",
            "requires-python": ">=3.10,<4",
            "dependencies": ["requests>=2.31,<3"],
        }
        assert project_metadata_source(tmp_path) == "project"
        # Poetry may replace such a value at build time, so it stays dynamic.
        assert is_dynamic(project, "version") is True
        assert "version" not in get_project_table(tmp_path)

    def test_empty_project_table_falls_back_to_poetry(self, tmp_path: Path) -> None:
        _write(tmp_path, '[project]\n[tool.poetry]\nname = "demo"\n')
        assert get_effective_project_table(tmp_path) == {"name": "demo"}
        assert project_metadata_source(tmp_path) == "poetry"

    def test_oddly_typed_poetry_table_does_not_raise(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            "[tool.poetry]\nname = 5\nauthors = 'Jane'\nclassifiers = 'x'\n"
            "urls = ['a']\nhomepage = 3\nextras = 'all'\ndependencies = ['requests']\n",
        )
        assert get_effective_project_table(tmp_path) == {"name": 5}
        _write(
            tmp_path,
            "[tool.poetry.dependencies]\npython = 3\n'two words' = '^1'\nok = 7\n"
            f"long = '{DIGITS}'\n"
            "[tool.poetry.extras]\nbad = 'ok'\nalso = [1, 'ok']\n",
        )
        assert get_effective_project_table(tmp_path) == {
            "dependencies": ["ok", "long"],
            "optional-dependencies": {"also": ["ok"]},
        }

    def test_failed_translation_falls_back_to_the_project_table(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Last resort: whatever the translation raises, the reader does not."""

        def explode(name: object, spec: object) -> list[str]:
            raise RuntimeError("unexpected")

        monkeypatch.setattr(
            pyproject_reader, "poetry_dependency_to_requirements", explode
        )
        _write(tmp_path, POETRY_2)
        assert pyproject_status(tmp_path) == "ok"
        assert get_effective_project_table(tmp_path) == get_project_table(tmp_path)
        assert get_dev_dependencies(tmp_path) == []


class TestDevDependencies:
    """get_dev_dependencies()."""

    def test_reads_every_development_table(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            '[project]\nname = "demo"\ndependencies = ["click"]\n'
            '[project.optional-dependencies]\ncli = ["rich"]\n'
            "[dependency-groups]\n"
            'test = ["pytest>=8", "hypothesis"]\n'
            'all = [{ include-group = "test" }, "coverage"]\n'
            '[tool.poetry.dependencies]\nrequests = "^2"\n'
            "[tool.poetry.group.dev.dependencies]\n"
            'black = "^24.0"\n'
            'mypy = { version = "^1.8", optional = true }\n'
            "[tool.poetry.dev-dependencies]\n"
            'python = "^3.10"\ntox = "*"\n'
            "[tool.hatch.envs.default]\n"
            'dependencies = ["pytest>=8", "pytest-cov"]\n'
            "[tool.hatch.envs.docs]\n"
            'extra-dependencies = ["mkdocs"]\n'
            "[tool.uv]\n"
            'dev-dependencies = ["ruff"]\n'
            "[tool.pdm.dev-dependencies]\n"
            'lint = ["flake8", "-e file:///${PROJECT_ROOT}/plugin"]\n',
        )
        expected = (
            "pytest>=8 hypothesis coverage tox black>=24.0,<25 mypy>=1.8,<2 "
            "pytest-cov mkdocs ruff flake8"
        ).split()
        assert get_dev_dependencies(tmp_path) == expected
        get_dev_dependencies(tmp_path).append("changed")
        assert get_dev_dependencies(tmp_path) == expected

    def test_oddly_typed_tables_do_not_raise(self, tmp_path: Path) -> None:
        _write(
            tmp_path,
            'dependency-groups = "all"\n[tool]\nuv = "x"\npdm = 3\nhatch = 5\n'
            "[tool.poetry]\ngroup = ['dev']\ndev-dependencies = 'pytest'\n",
        )
        assert get_dev_dependencies(tmp_path) == []
        _write(
            tmp_path,
            "[dependency-groups]\ntest = 'pytest'\nlint = [1, '', 'ruff']\n"
            "[tool.hatch.envs]\ndefault = 'x'\ndocs = { dependencies = 'mkdocs' }\n"
            "[tool.poetry.group]\ndev = 'x'\ntest = { dependencies = ['pytest'] }\n",
        )
        assert get_dev_dependencies(tmp_path) == ["ruff"]


class TestSetupCfg:
    """get_setup_cfg_value()."""

    def test_reads_a_value_whatever_the_encoding(self, tmp_path: Path) -> None:
        """An accented author name in Latin-1 used to raise UnicodeDecodeError."""
        (tmp_path / "setup.cfg").write_bytes(
            "[metadata]\nname = demo\nauthor = José García\n\n"
            "[options]\npython_requires = >=3.8\nzip_safe =\n".encode("latin-1")
        )
        assert get_setup_cfg_value(tmp_path, "options", "python_requires") == ">=3.8"
        assert get_setup_cfg_value(tmp_path, "metadata", "name") == "demo"
        assert get_setup_cfg_value(tmp_path, "options", "zip_safe") is None
        assert get_setup_cfg_value(tmp_path, "options", "missing") is None
        assert get_setup_cfg_value(tmp_path, "missing", "name") is None

    def test_missing_or_invalid_file_gives_none(self, tmp_path: Path) -> None:
        assert get_setup_cfg_value(tmp_path, "options", "python_requires") is None
        (tmp_path / "setup.cfg").write_text("python_requires = >=3.9\n")
        assert get_setup_cfg_value(tmp_path, "options", "python_requires") is None

    def test_file_is_parsed_once_and_again_after_an_edit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Every option asked for used to read and parse the file again."""
        calls = _count_calls(monkeypatch, pyproject_reader, "read_ini")
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = demo\n")
        for _ in range(3):
            assert get_setup_cfg_value(tmp_path, "metadata", "name") == "demo"
            assert get_setup_cfg_value(tmp_path, "options", "packages") is None
        assert len(calls) == 1
        (tmp_path / "setup.cfg").write_text("[metadata]\nname = other\n")
        assert get_setup_cfg_value(tmp_path, "metadata", "name") == "other"
        assert len(calls) == 2


def _setup_py(root: Path, body: str) -> dict[str, Any]:
    (root / "setup.py").write_text("from setuptools import setup\n" + body)
    return get_setup_py_keywords(root)


class TestSetupPy:
    """get_setup_py_keywords()."""

    def test_literal_keywords_are_read_without_running_the_file(
        self, tmp_path: Path
    ) -> None:
        marker = tmp_path / "executed"
        keywords = _setup_py(
            tmp_path,
            f"open({str(marker)!r}, 'w').close()\n"
            "setup(name='six', python_requires='>=2.7, !=3.0.*', zip_safe=False,\n"
            "      extras_require={'dev': ['pytest', ('a', {'b': [1]})]})\n",
        )
        assert keywords == {
            "name": "six",
            "python_requires": ">=2.7, !=3.0.*",
            "zip_safe": False,
            "extras_require": {"dev": ["pytest", ("a", {"b": [1]})]},
        }
        assert not marker.exists()

    def test_computed_values_are_marked(self, tmp_path: Path) -> None:
        (tmp_path / "setup.py").write_text(
            "import setuptools\n"
            "PYTHON = '>=3.9'\n"
            "REQUIREMENTS: list[str] = ['click']\n"
            "VERSION = get_version()\n"
            "setuptools.setup(python_requires=PYTHON, install_requires=REQUIREMENTS,\n"
            "                 version=VERSION, name=UNKNOWN, license='M' + 'IT')\n"
        )
        keywords = get_setup_py_keywords(tmp_path)
        # A module-level name bound to a literal is read.
        assert keywords["python_requires"] == ">=3.9"
        assert keywords["install_requires"] == ["click"]
        for computed in ("version", "name", "license"):
            assert keywords[computed] is SETUP_PY_DYNAMIC
        assert "**" not in keywords
        assert get_str(keywords, "version") is None

    @pytest.mark.parametrize(
        ("body", "packages"),
        [
            # Reading the list a second time does not change it.
            (
                "packages = ['yaml']\nfirst = packages[0]\n"
                "setup(name='demo', packages=packages)\n",
                ["yaml"],
            ),
            (
                "packages = ['yaml']\nprint(packages.count('yaml'))\n"
                "setup(name='demo', packages=packages)\n",
                ["yaml"],
            ),
            # The list is changed after it was built.
            (
                "packages = ['yaml']\npackages.append('_yaml')\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ['yaml']\npackages[0] = '_yaml'\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = []\npackages += ['yaml', '_yaml']\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ['yaml']\nif sys.platform == 'win32':\n"
                "    packages += ['_yaml']\nsetup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ('yaml',)\npackages += ('_yaml',)\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            # An item is taken out and kept, so the call is part of a line.
            (
                "packages = ['yaml', '_yaml']\nlast = packages.pop()\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ['yaml', '_yaml']\nprint(packages.pop(0))\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = {'a': 1, 'b': 2}\npair = packages.popitem()\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = {}\nroot = packages.setdefault('', 'src')\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            # A function called pop is not a method of the list.
            (
                "packages = ['yaml']\nlast = pop(packages)\n"
                "setup(name='demo', packages=packages)\n",
                ["yaml"],
            ),
            # A name bound again in a block is not what setup() gets.
            (
                "packages = ['demo']\nif sys.platform == 'win32':\n"
                "    packages = packages + ['demo_win']\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ['demo']\ntry:\n    import fast\nexcept ImportError:\n"
                "    packages = ['demo', 'demo_slow']\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ['demo']\nwith open('more.txt') as packages:\n    pass\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            (
                "packages = ['demo']\nfor packages in [['a'], ['b']]:\n    pass\n"
                "setup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            # Scopes are not followed, so a local of the same name counts.
            (
                "packages = ['demo']\ndef helper():\n    packages = ['other']\n"
                "    return packages\nsetup(name='demo', packages=packages)\n",
                SETUP_PY_DYNAMIC,
            ),
            # Bound twice at module level, the last value is the one read.
            (
                "packages = ['old']\npackages = ['demo']\n"
                "setup(name='demo', packages=packages)\n",
                ["demo"],
            ),
            # A string cannot be changed in place, so NAME is read however
            # often it is named. A list that holds a name is not a literal.
            ("NAME = 'demo'\nsetup(name=NAME, packages=[NAME])\n", SETUP_PY_DYNAMIC),
        ],
        ids=[
            "list read twice",
            "list read through a method",
            "append",
            "item set",
            "added to",
            "added to in a block",
            "tuple added to",
            "popped into a name",
            "popped inside a call",
            "popitem",
            "setdefault",
            "function named pop",
            "bound again in an if",
            "bound again in a try",
            "bound by a with",
            "bound by a for",
            "local of the same name in a function",
            "bound twice at module level",
            "string named three times",
        ],
    )
    def test_list_changed_after_it_was_built_is_computed(
        self, tmp_path: Path, body: str, packages: object
    ) -> None:
        assert _setup_py(tmp_path, body) == {"name": "demo", "packages": packages}

    @pytest.mark.parametrize(
        "body",
        [
            "kwargs = dict(name='demo', python_requires='>=3.8')\nsetup(**kwargs)\n",
            "kwargs = {'name': 'demo', 'python_requires': '>=3.8'}\n"
            "if __name__ == '__main__':\n    setup(**kwargs)\n",
            "setup(**{'name': 'demo'}, **dict(python_requires='>=3.8'))\n",
        ],
    )
    def test_unpacked_mapping_is_followed(self, tmp_path: Path, body: str) -> None:
        expected = {"name": "demo", "python_requires": ">=3.8"}
        assert _setup_py(tmp_path, body) == expected

    @pytest.mark.parametrize(
        "body",
        [
            "setup(name='demo', **load_metadata())\n",
            "setup(name='demo', *args)\n",
            "from meta import kwargs\nsetup(name='demo', **kwargs)\n",
            "kwargs = dict(name='demo', **more)\nsetup(**kwargs)\n",
            "kwargs = {'name': 'demo', **more}\nsetup(**kwargs)\n",
            # The mapping is named again, so it may have been changed.
            "kwargs = dict(name='demo')\nkwargs['zip_safe'] = True\nsetup(**kwargs)\n",
            "kwargs = dict(name='demo')\nkwargs.update(extra())\nsetup(**kwargs)\n",
            "kwargs = {'name': 'demo'}\nkwargs |= extra()\nsetup(**kwargs)\n",
        ],
    )
    def test_keywords_that_cannot_be_followed_are_flagged(
        self, tmp_path: Path, body: str
    ) -> None:
        """A check can then say 'could not determine' instead of 'not set'."""
        assert _setup_py(tmp_path, body) == {"name": "demo", "**": SETUP_PY_DYNAMIC}

    def test_first_setup_call_wins(self, tmp_path: Path) -> None:
        body = (
            "if True:\n    setup(name='first', python_requires='>=3.9')\n"
            "else:\n    setup(name='second', version='2')\n"
        )
        expected = {"name": "first", "python_requires": ">=3.9", "version": "2"}
        assert _setup_py(tmp_path, body) == expected

    def test_nothing_to_read(self, tmp_path: Path) -> None:
        assert get_setup_py_keywords(tmp_path) == {}
        assert _setup_py(tmp_path, "print('no setup call here')\n") == {}
        assert _setup_py(tmp_path, "def broken(:\n") == {}

    def test_each_call_returns_a_new_dict_and_sees_an_edit(
        self, tmp_path: Path
    ) -> None:
        first = _setup_py(tmp_path, "setup(name='demo', packages=['demo'])\n")
        first["packages"].append("changed")
        assert get_setup_py_keywords(tmp_path) == {"name": "demo", "packages": ["demo"]}
        assert _setup_py(tmp_path, "setup(name='other')\n") == {"name": "other"}
