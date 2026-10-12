"""Tests for the metadata check."""

import os
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

import pytest

from pycmdcheck.checks import metadata
from pycmdcheck.checks.metadata import MetadataCheck
from pycmdcheck.checks.python_versions import PythonVersionsCheck
from pycmdcheck.pyproject_reader import clear_cache
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.runner import fill_hints
from tests import helpers

BUILD_SYSTEM = '[build-system]\nrequires = ["setuptools"]\n'
SETUP_PY = 'from setuptools import setup\nsetup(name="legacy", version="1.0")\n'
SETUP_CFG = "[metadata]\nname = legacy\nversion = 1.0\n"
PROJECT = '[project]\nname = "legacy"\nversion = "1.0"\n'
INVALID = "metadata.invalid-value"
# The classifier that make_package writes, and a License classifier after it.
TWO_CLASSIFIERS = (
    'classifiers = ["Development Status :: 3 - Alpha", "License :: OSI Approved"]'
)
# For each rule, a project in which it fires, the same project after its hint
# was followed, and the words of the hint that say what was changed. All but
# the last are options of make_package.
FOLLOWED: dict[str, tuple[dict[str, Any], dict[str, Any], str]] = {
    "metadata.unreadable-pyproject": (
        {"pyproject": '[project]\nname = "legacy\n'},
        {"pyproject": '[project]\nname = "legacy"\n'},
        "Correct pyproject.toml so that it is valid TOML",
    ),
    "metadata.missing": (
        {"pyproject": False},
        {"pyproject": PROJECT},
        "Add a [project] table with name and version to pyproject.toml",
    ),
    "metadata.legacy-setup": (
        {"pyproject": False, "files": {"setup.cfg": SETUP_CFG}},
        {"pyproject": PROJECT},
        "Move the metadata from setup.py or setup.cfg to a [project] table",
    ),
    "metadata.missing-project-table": (
        {"pyproject": BUILD_SYSTEM, "files": {"Cargo.toml": ""}},
        {"pyproject": BUILD_SYSTEM + PROJECT, "files": {"Cargo.toml": ""}},
        "Add a [project] table to pyproject.toml that sets name, and sets version",
    ),
    "metadata.missing-required": (
        {"version": None},
        {},
        "Add each missing field to the [project] table",
    ),
    "metadata.missing-recommended": (
        {"omit": ("description", "readme", "license", "requires-python")},
        {},
        "Add each missing field to the [project] table",
    ),
    "metadata.missing-extended": (
        {"omit": ("authors", "urls", "classifiers")},
        {},
        "Add each missing field to the [project] table",
    ),
    "metadata.missing-build-system": (
        {"backend": None},
        {},
        "Add a [build-system] table to pyproject.toml",
    ),
    "metadata.invalid-value": (
        {"version": None, "project": "version = 1.0"},
        {"version": None, "project": 'version = "1.2.0"'},
        "Set version in the [project] table of pyproject.toml to a value such as "
        '"1.2.0"',
    ),
    "metadata.missing-readme-file": (
        {"readme": False},
        {},
        "Add the file that readme names",
    ),
    "metadata.license-classifier-conflict": (
        {"backend": "setuptools", "omit": ("classifiers",), "project": TWO_CLASSIFIERS},
        {"backend": "setuptools"},
        "Remove every classifier that starts with License ::",
    ),
}


def run(package: Path) -> CheckResult:
    return MetadataCheck().run(package, {})


def text(result: CheckResult) -> str:
    return "\n".join(result.details).lower()


# The codes of the findings, which must agree with the result and the rules.
codes = partial(helpers.codes, rules=MetadataCheck.rules)


class TestMetadataExtended:
    """Tests for extended metadata validation."""

    @pytest.mark.parametrize(
        ("field", "project"),
        [
            pytest.param("authors", "", id="authors-absent"),
            pytest.param("urls", "", id="urls-absent"),
            pytest.param("classifiers", "", id="classifiers-absent"),
            # An empty list or table is not a declaration (C063).
            pytest.param("authors", "authors = []", id="authors-empty"),
            pytest.param("classifiers", "classifiers = []", id="classifiers-empty"),
            pytest.param("urls", "[project.urls]", id="urls-empty"),
        ],
    )
    def test_missing_extended_field(
        self, make_package: Callable[..., Path], field: str, project: str
    ) -> None:
        """A missing extended field is a NOTE that names the field."""
        result = run(make_package(omit=(field,), project=project))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == ["metadata.missing-extended"]
        assert f"missing extended metadata fields: {field}" in text(result)


class TestMetadataDynamicFields:
    """Tests for PEP 621 ``dynamic`` field handling.

    A field listed in ``[project].dynamic`` is supplied by the build backend
    (e.g. setuptools_scm, hatch-vcs, uv-dynamic-versioning) and must NOT be
    reported as missing. See https://github.com/eliotwrobson/tldm which uses
    ``dynamic = ["version"]``.
    """

    def test_dynamic_version_not_required_missing(
        self, make_package: Callable[..., Path]
    ) -> None:
        """`dynamic = ["version"]` must not be reported as a missing field."""
        result = run(make_package(version=None, project='dynamic = ["version"]'))
        assert result.status == CheckStatus.OK
        assert "missing" not in text(result)

    def test_legacy_poetry_is_complete(self, make_package: Callable[..., Path]) -> None:
        """Legacy Poetry ([tool.poetry], no [project]) is read like [project]."""
        result = run(make_package(backend="poetry", layout="flat"))
        assert result.status == CheckStatus.OK, result.details

    def test_legacy_poetry_classifiers_not_required(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Poetry adds classifiers when it builds, so none is fine (C064)."""
        package = make_package(
            backend="poetry",
            omit=("classifiers", "readme"),
            project='readme = ["README.md", "MISSING.md"]',
        )
        result = run(package)
        assert result.status == CheckStatus.OK, result.details

    def test_legacy_poetry_python_constraint_belongs_to_python_versions(
        self, tmp_path: Path
    ) -> None:
        """A missing Poetry constraint is one note from python_versions, not two."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "pkg"\nversion = "1.0"\ndescription = "A package"\n'
            'license = "MIT"\nreadme = "README.md"\nauthors = ["A <a@b.io>"]\n'
        )
        (tmp_path / "README.md").write_text("# pkg\n")
        assert "requires-python" not in text(run(tmp_path))
        versions = PythonVersionsCheck().run(tmp_path, {})
        assert [f.code for f in versions.findings] == [
            "python_versions.missing-requires-python"
        ]

    def test_dynamic_recommended_field_not_missing(
        self, make_package: Callable[..., Path]
    ) -> None:
        """A recommended field declared dynamic is not flagged as missing."""
        package = make_package(
            omit=("readme", "classifiers"),
            project='dynamic = ["readme", "classifiers"]',
        )
        result = run(package)
        assert result.status == CheckStatus.OK, result.details


class TestNoProjectTable:
    """pyproject.toml without [project] or [tool.poetry] (C001)."""

    @pytest.mark.parametrize(
        ("pyproject", "legacy"),
        [
            (BUILD_SYSTEM, {"setup.cfg": SETUP_CFG}),
            ("[tool.black]\nline-length = 88\n", {"setup.py": SETUP_PY}),
            ("", {"setup.py": SETUP_PY, "setup.cfg": SETUP_CFG}),
        ],
        ids=["build-system+setup.cfg", "tool-only+setup.py", "empty+both"],
    )
    def test_legacy_files_hold_the_metadata(
        self,
        make_package: Callable[..., Path],
        pyproject: str,
        legacy: dict[str, str],
    ) -> None:
        """setup.py or setup.cfg beside a tool-only pyproject.toml -> WARNING."""
        result = run(make_package(pyproject=pyproject, files=legacy))
        assert result.status == CheckStatus.WARNING
        assert codes(result) == ["metadata.legacy-setup"]
        assert "no [project] table" in text(result)
        assert all(name in text(result) for name in legacy)
        assert "instead of pyproject.toml" not in text(result)

    @pytest.mark.parametrize(
        ("pyproject", "files", "where"),
        [
            (
                '[tool.flit.metadata]\nmodule = "mypackage"\n',
                {},
                "[tool.flit.metadata]",
            ),
            (BUILD_SYSTEM, {"Cargo.toml": '[package]\nname = "x"\n'}, "Cargo.toml"),
            (BUILD_SYSTEM, {"meson.build": "project('x')\n"}, "meson.build"),
            # A setup.cfg that only configures tools holds no metadata.
            (
                '[tool.flit.metadata]\nmodule = "mypackage"\n',
                {"setup.cfg": "[flake8]\nmax-line-length = 88\n"},
                "[tool.flit.metadata]",
            ),
        ],
        ids=["old-flit", "maturin", "meson", "flit+tool-setup.cfg"],
    )
    def test_other_backend_source_is_a_note(
        self,
        make_package: Callable[..., Path],
        pyproject: str,
        files: dict[str, str],
        where: str,
    ) -> None:
        result = run(make_package(pyproject=pyproject, files=files))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == ["metadata.missing-project-table"]
        assert where in result.findings[0].message
        assert where.lower() in text(result)

    @pytest.mark.parametrize("files", [{}, {"setup.cfg": "[flake8]\n"}])
    def test_nothing_declares_metadata(
        self, make_package: Callable[..., Path], files: dict[str, str]
    ) -> None:
        result = run(make_package(pyproject=BUILD_SYSTEM, files=files))
        assert result.status == CheckStatus.ERROR
        assert codes(result) == ["metadata.missing"]
        assert result.message == "No package metadata found"
        assert "setup.py" in text(result)
        assert "missing required fields" not in text(result)

    def test_empty_project_table_is_still_an_error(self, bad_package: Path) -> None:
        result = run(bad_package)
        assert result.status == CheckStatus.ERROR
        assert codes(result) == ["metadata.missing-required"]
        assert "missing required fields: name, version" in text(result)


class TestBrokenPyproject:
    """metadata is the check that reports an unreadable pyproject.toml (C003)."""

    @pytest.mark.parametrize(
        ("content", "reason"),
        [
            ("this is not valid toml [[[", "toml parse error"),
            (b"\xef\xbb\xbf[project]\nname = 'x'\n", "byte order mark"),
            (b"[project]\nname = 'caf\xe9'\n", "utf-8"),
        ],
        ids=["syntax", "bom", "latin-1"],
    )
    def test_unparseable_file(
        self, make_package: Callable[..., Path], content: str | bytes, reason: str
    ) -> None:
        result = run(make_package(pyproject=content))
        assert result.status == CheckStatus.ERROR
        assert codes(result) == ["metadata.unreadable-pyproject"]
        assert result.message == "Invalid pyproject.toml"
        assert reason in text(result)

    def test_directory_named_pyproject(self, tmp_path: Path) -> None:
        (tmp_path / "pyproject.toml").mkdir()
        result = run(tmp_path)
        assert result.status == CheckStatus.ERROR
        assert codes(result) == ["metadata.unreadable-pyproject"]
        assert result.message == "Invalid pyproject.toml"
        assert len(result.details) == 1


class TestDeclaredFields:
    """What counts as declared (C063, C064)."""

    @pytest.mark.parametrize(
        ("omit", "project", "missing"),
        [
            ("description", 'description = ""', "recommended fields: description"),
            ("readme", 'readme = ""', "recommended fields: readme"),
            ("license", "", "recommended fields: license"),
            ("requires-python", 'requires-python = " "', "fields: requires-python"),
        ],
    )
    def test_empty_recommended_field_is_missing(
        self, make_package: Callable[..., Path], omit: str, project: str, missing: str
    ) -> None:
        result = run(make_package(omit=(omit,), project=project))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == ["metadata.missing-recommended"]
        assert missing in text(result)

    def test_empty_version_is_missing(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(version=""))
        assert result.status == CheckStatus.ERROR
        assert codes(result) == ["metadata.missing-required"]
        assert "missing required fields: version" in text(result)

    @pytest.mark.parametrize(
        ("omit", "project"),
        [
            ("authors", 'maintainers = [{name = "Pallets"}]'),
            ("authors", 'dynamic = ["maintainers"]'),
            ("license", 'classifiers = ["License :: OSI Approved :: MIT License"]'),
            ("license", 'license-files = ["LICENSE"]'),
            ("license", 'license = {file = "LICENSE"}'),
            ("readme", 'readme = {file = "README.md", content-type = "text/markdown"}'),
            ("readme", 'readme = {text = "Hello", content-type = "text/plain"}'),
        ],
        ids=[
            "maintainers",
            "dynamic-maintainers",
            "license-classifier",
            "license-files",
            "license-table",
            "readme-table",
            "readme-text",
        ],
    )
    def test_alternative_declarations_count(
        self, make_package: Callable[..., Path], omit: str, project: str
    ) -> None:
        omitted = (omit, "classifiers") if "classifiers" in project else (omit,)
        result = run(make_package(omit=omitted, project=project))
        assert result.status == CheckStatus.OK, result.details

    def test_hybrid_poetry_fields_fill_project(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Poetry 2: [tool.poetry] supplies what [project] leaves out (C064)."""
        package = make_package(
            omit=("description", "classifiers"),
            extra='[tool.poetry]\ndescription = "From Poetry"\nclassifiers = ["A"]\n',
        )
        assert run(package).status == CheckStatus.OK

    def test_maturin_takes_authors_from_cargo(
        self, make_package: Callable[..., Path]
    ) -> None:
        package = make_package(
            backend=None,
            omit=("authors",),
            extra='[build-system]\nrequires = ["maturin"]\nbuild-backend = "maturin"\n',
            files={"Cargo.toml": '[package]\nname = "x"\nauthors = ["A <a@b.c>"]\n'},
        )
        assert run(package).status == CheckStatus.OK


class TestInvalidValues:
    """Values that are present but cannot be right are a NOTE (C063)."""

    @pytest.mark.parametrize(
        ("omit", "project", "problem", "code"),
        [
            ("version", 'version = "one-point-oh"', "version is not a valid", INVALID),
            ("version", "version = 1.0", "version is not a valid", INVALID),
            (
                "requires-python",
                'requires-python = "^3.9"',
                "requires-python is not",
                INVALID,
            ),
            ("authors", 'authors = "Jane"', "authors is not a list", INVALID),
            ("classifiers", 'classifiers = "x"', "classifiers is not a list", INVALID),
            ("urls", 'urls = ["https://example.com"]', "urls is not a table", INVALID),
            (
                "readme",
                'readme = "MISSING.md"',
                "readme file missing.md",
                "metadata.missing-readme-file",
            ),
        ],
    )
    def test_invalid_value(
        self,
        make_package: Callable[..., Path],
        omit: str,
        project: str,
        problem: str,
        code: str,
    ) -> None:
        version = None if omit == "version" else "0.1.0"
        result = run(make_package(version=version, omit=(omit,), project=project))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [code]
        assert problem in result.findings[0].message.lower()
        assert problem in text(result)
        assert "all metadata fields present" not in text(result)

    def test_invalid_name(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package("my package!", layout="none", tests=False))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == [INVALID]
        assert "name is not a valid project name" in text(result)

    def test_readme_outside_the_project_is_not_judged(
        self, make_package: Callable[..., Path]
    ) -> None:
        package = make_package(omit=("readme",), project='readme = "../README.md"')
        assert run(package).status == CheckStatus.OK

    def test_missing_build_system(self, make_package: Callable[..., Path]) -> None:
        result = run(make_package(backend=None))
        assert result.status == CheckStatus.NOTE
        assert codes(result) == ["metadata.missing-build-system"]
        assert "[build-system]" in text(result)

    @pytest.mark.parametrize(
        ("backend", "declared", "status", "found"),
        [
            ("hatchling", 'license = "MIT"', CheckStatus.OK, []),
            (
                "setuptools",
                'license = "MIT"',
                CheckStatus.WARNING,
                ["metadata.license-classifier-conflict"],
            ),
            # A license table, or no license at all, is not an expression.
            ("setuptools", 'license = {text = "MIT"}', CheckStatus.OK, []),
            ("setuptools", "", CheckStatus.OK, []),
        ],
    )
    def test_license_expression_beside_classifier(
        self,
        make_package: Callable[..., Path],
        backend: str,
        declared: str,
        status: CheckStatus,
        found: list[str],
    ) -> None:
        """Only setuptools (77 and later) refuses to build this combination."""
        classifier = 'classifiers = ["License :: OSI Approved :: MIT License"]'
        package = make_package(
            backend=backend,
            omit=("classifiers", "license"),
            project=f"{declared}\n{classifier}",
        )
        result = run(package)
        assert result.status == status
        assert codes(result) == found
        assert ("license classifier" in text(result)) == bool(found)

    def test_status_is_that_of_the_most_severe_finding(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Each finding keeps its own status, and the worst one grades the result."""
        package = make_package(
            backend="setuptools",
            omit=("classifiers", "description"),
            project='classifiers = ["License :: OSI Approved :: MIT License"]',
        )
        result = run(package)
        assert result.status == CheckStatus.WARNING
        assert [(f.code, f.status) for f in result.findings] == [
            ("metadata.missing-recommended", CheckStatus.NOTE),
            ("metadata.license-classifier-conflict", CheckStatus.WARNING),
        ]
        # People read the same lines that programs read.
        assert result.details == [f.message for f in result.findings]


class TestGrades:
    """The message and details that go with each basic grade."""

    @pytest.mark.parametrize(
        ("package", "status", "message", "details", "found"),
        [
            (
                {},
                CheckStatus.OK,
                "Package metadata is valid",
                ["All metadata fields present"],
                [],
            ),
            # The recommended fields give the message when extended ones are
            # missing too, and each group is listed once.
            (
                {"pyproject": '[project]\nname = "minimal"\nversion = "0.1.0"\n'},
                CheckStatus.NOTE,
                "Some recommended metadata fields are missing",
                [
                    "Missing recommended fields: "
                    "description, readme, license, requires-python",
                    "Missing extended metadata fields: "
                    "authors or maintainers, urls, classifiers",
                    "pyproject.toml has no [build-system] table",
                ],
                [
                    "metadata.missing-recommended",
                    "metadata.missing-extended",
                    "metadata.missing-build-system",
                ],
            ),
            (
                {"pyproject": "[project]\n"},
                CheckStatus.ERROR,
                "Missing required metadata fields",
                ["Missing required fields: name, version"],
                ["metadata.missing-required"],
            ),
            (
                {"pyproject": False, "files": {"setup.py": SETUP_PY}},
                CheckStatus.WARNING,
                "Metadata is in setup.py or setup.cfg",
                ["Using legacy setup.py/setup.cfg instead of pyproject.toml"],
                ["metadata.legacy-setup"],
            ),
            (
                {"pyproject": False},
                CheckStatus.ERROR,
                "No package metadata found",
                ["Missing pyproject.toml, setup.py, or setup.cfg"],
                ["metadata.missing"],
            ),
        ],
        ids=["complete", "bare", "empty-project", "setup.py-only", "no-files"],
    )
    def test_message_and_details(
        self,
        make_package: Callable[..., Path],
        package: dict[str, Any],
        status: CheckStatus,
        message: str,
        details: list[str],
        found: list[str],
    ) -> None:
        result = run(make_package(**package))
        assert (result.status, result.message) == (status, message)
        assert result.details == details
        assert codes(result) == found


class TestHints:
    """Doing what a hint says makes its rule stop firing."""

    @pytest.mark.parametrize("code", MetadataCheck.rules)
    def test_following_the_hint_clears_the_rule(
        self, make_package: Callable[..., Path], code: str
    ) -> None:
        before, after, said = FOLLOWED[code]
        result = run(make_package(**before))
        fill_hints(result, MetadataCheck.hints)
        hints = {finding.code: finding.hint or "" for finding in result.findings}
        assert hints[code].startswith(said)
        assert code not in codes(run(make_package(**after)))

    def test_file_that_cannot_be_read_gets_its_own_hint(
        self, make_package: Callable[..., Path]
    ) -> None:
        """Valid TOML is no cure for a file without read permission."""
        target = make_package() / "pyproject.toml"
        target.chmod(0)
        if os.access(target, os.R_OK):
            pytest.skip("a file with no permissions can still be read here")
        (finding,) = run(target.parent).findings
        assert finding.message == "pyproject.toml could not be read: Permission denied"
        assert finding.hint == (
            "Make pyproject.toml a regular file that you have permission to read, "
            "for example with chmod u+r pyproject.toml."
        )
        target.chmod(0o600)  # chmod u+r pyproject.toml
        clear_cache()
        assert codes(run(target.parent)) == []

    @pytest.mark.parametrize(
        ("reason", "hint"),
        [
            (
                "pyproject.toml nests tables or arrays more than 64 deep",
                "Nest the tables and arrays of pyproject.toml less deeply, until "
                "they are within the limit.",
            ),
            (
                "pyproject.toml is larger than 1 MiB",
                "Shorten pyproject.toml until it is within the size limit.",
            ),
        ],
        ids=["deep", "large"],
    )
    def test_file_past_a_limit_gets_its_own_hint(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reason: str, hint: str
    ) -> None:
        monkeypatch.setattr(metadata, "pyproject_status", lambda path: "unparseable")
        monkeypatch.setattr(metadata, "pyproject_error", lambda path: reason)
        (finding,) = run(tmp_path).findings
        assert (finding.message, finding.hint) == (reason, hint)

    @pytest.mark.parametrize(
        ("backend", "table"), [("hatchling", "[project]"), ("poetry", "[tool.poetry]")]
    )
    def test_missing_field_hint_names_the_table_in_use(
        self, make_package: Callable[..., Path], backend: str, table: str
    ) -> None:
        """A legacy Poetry project is sent to its own table."""
        result = run(make_package(backend=backend, omit=("license",)))
        assert [finding.hint for finding in result.findings] == [
            f"Add each missing field to the {table} table of pyproject.toml."
        ]
        assert codes(run(make_package(backend=backend))) == []

    @pytest.mark.parametrize(
        ("field", "bad"),
        [
            ("name", '"my package!"'),
            ("version", "1.0"),
            ("requires-python", '"^3.9"'),
            ("authors", '"Jane"'),
            ("maintainers", '"Jane"'),
            ("classifiers", '"x"'),
            ("urls", '["https://example.com"]'),
        ],
    )
    def test_value_shown_in_the_hint_is_accepted(
        self, tmp_path: Path, field: str, bad: str
    ) -> None:
        """The hint for a bad value shows a good one, which is then written."""

        def written(value: str) -> CheckResult:
            fields = {"name": '"pkg"', "version": '"1.0"', field: value}
            lines = [f"{key} = {text}" for key, text in fields.items()]
            (tmp_path / "pyproject.toml").write_text("[project]\n" + "\n".join(lines))
            return run(tmp_path)

        before = written(bad)
        assert codes(before)[-1] == INVALID
        hint = before.findings[-1].hint or ""
        start, _, shown = hint.removesuffix(".").partition(" to a value such as ")
        assert start == f"Set {field} in the [project] table of pyproject.toml"
        assert codes(written(shown)) == codes(before)[:-1]
