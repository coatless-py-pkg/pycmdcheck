"""End-to-end tests of the triage profile, the command the pyOpenSci bot runs.

Every test builds a small project on disk and runs the twelve static checks
over it, through the Python API, the command line or a subprocess. The
projects are shaped like real repositories that pycmdcheck 0.1.1 graded with
a false ERROR or WARNING, or they carry input that used to end in a traceback.
"""

import datetime
import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest
from click.testing import CliRunner

from pycmdcheck import check
from pycmdcheck.cli import main
from pycmdcheck.profiles import PROFILES
from pycmdcheck.results import CheckStatus
from tests.helpers import JSON_KEYS, RESULT_KEYS, TRIAGE, TRIAGE_CHECKS

# A project tree maps a relative path to content. None stands for a directory.
Tree = dict[str, str | bytes | None]

README = (
    "# Widget\n\nA small library that only exists as a test project. It gives the\n"
    "static checks something complete to read, with a description, an install\n"
    "line and a usage example, and it has enough words to pass the length rule\n"
    "that the documentation check applies to a README file.\n\n"
    "## Installation\n\n    pip install widget\n\n## Usage\n\n    import widget\n"
)
LICENSE = (
    "BSD 3-Clause License\n\nCopyright (c) 2024, The Widget Authors\n\n"
    "Redistribution and use in source and binary forms, with or without\n"
    "modification, are permitted provided that the following conditions are met.\n"
    "Neither the name of the copyright holder nor the names of its contributors\n"
    "may be used to endorse or promote products derived from this software.\n"
)
CHANGELOG = "Changelog\n=========\n\n1.0.0\n-----\n\n* First public release.\n"
DOCS: Tree = {"README.md": README, "LICENSE": LICENSE}
MINIMAL = '[project]\nname = "widget"\nversion = "1.0.0"\n'


def backend(name: str, *requires: str) -> str:
    """Return a [build-system] table for the build backend *name*."""
    listed = json.dumps(list(requires))
    return f'[build-system]\nrequires = {listed}\nbuild-backend = "{name}"\n'


SETUPTOOLS = backend("setuptools.build_meta", "setuptools", "wheel")
HATCHLING = backend("hatchling.build", "hatchling")
MATURIN = backend("maturin", "maturin>=1,<2")


def project(
    name: str,
    version: str = 'version = "1.0.0"',
    classifier: str = "Typing :: Typed",
) -> str:
    """Return a [project] table for *name* with every graded field filled in."""
    return (
        f'[project]\nname = "{name}"\n{version}\ndescription = "A test project"\n'
        'readme = "README.md"\nlicense = "BSD-3-Clause"\n'
        'requires-python = ">=3.10"\nauthors = [{name = "A. Author"}]\n'
        f'classifiers = ["{classifier}"]\n\n'
        '[project.urls]\nHomepage = "https://example.com"\n\n'
    )


def write_tree(root: Path, files: Tree) -> Path:
    """Write *files* under *root* and return *root*."""
    for relative, content in files.items():
        target = root / relative
        if content is None:
            target.mkdir(parents=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content if isinstance(content, bytes) else content.encode())
    return root


@pytest.fixture(autouse=True)
def _fixed_date(monkeypatch: pytest.MonkeyPatch) -> None:
    """Freeze the date, so no python_versions result moves with the calendar."""
    monkeypatch.setattr(
        "pycmdcheck.checks.python_versions._today", lambda: datetime.date(2026, 10, 9)
    )


# Shapes of real repositories. Each entry holds the files, then the statuses
# the named checks may have. No shape may give an ERROR in any check.
SHAPES: dict[str, tuple[Tree, dict[str, set[str]]]] = {
    "dateutil: metadata in setup.cfg": (
        {
            **DOCS,
            "pyproject.toml": SETUPTOOLS + '\n[tool.towncrier]\npackage = "dateutil"\n',
            "setup.py": "from setuptools import setup\n\nsetup(use_scm_version=True)\n",
            "setup.cfg": (
                "[metadata]\nname = python-dateutil\n\n"
                "[options]\npython_requires = >=3.10\npackage_dir =\n    =src\n"
            ),
            "src/dateutil/__init__.py": "",
        },
        {"metadata": {"warning"}},
    ),
    "PyYAML: metadata in setup.py": (
        {
            **DOCS,
            "pyproject.toml": SETUPTOOLS,
            "setup.py": (
                "from setuptools import setup\n\n"
                "setup(name='PyYAML', version='6.0.2', package_dir={'': 'lib'})\n"
            ),
            "lib/yaml/__init__.py": "__version__ = '6.0.2'\n",
        },
        {"metadata": {"warning"}},
    ),
    "click: static version and no __version__": (
        {
            **DOCS,
            "pyproject.toml": project("click")
            + backend("flit_core.buildapi", "flit_core<4"),
            "src/click/__init__.py": "from .core import Command as Command\n",
            "src/click/core.py": "class Command:\n    pass\n",
        },
        {"version": {"skipped"}},
    ),
    "orjson: Rust crate in src/, package in python-source": (
        {
            **DOCS,
            "pyproject.toml": project("orjson")
            + MATURIN
            + '\n[tool.maturin]\npython-source = "pysrc"\n',
            "Cargo.toml": '[package]\nname = "orjson"\nversion = "1.0.0"\n',
            "src/lib.rs": "",
            "src/serialize/mod.rs": "",
            "pysrc/orjson/__init__.py": "",
        },
        {"structure": {"ok"}},
    ),
    "orjson: Rust crate in src/, package at the root": (
        {
            **DOCS,
            "pyproject.toml": project("orjson") + MATURIN,
            "Cargo.toml": '[package]\nname = "orjson"\nversion = "1.0.0"\n',
            "src/lib.rs": "",
            "src/serialize/mod.rs": "",
            "orjson/__init__.py": "",
        },
        {"structure": {"ok"}},
    ),
    "six: single module with setup.py only": (
        {
            **DOCS,
            "setup.py": (
                "from setuptools import setup\n\n"
                "setup(name='six', py_modules=['six'], python_requires='>=3.11')\n"
            ),
            "setup.cfg": "[bdist_wheel]\nuniversal = 1\n",
            "six.py": '__version__ = "1.17.0"\n',
        },
        {"structure": {"ok"}, "metadata": {"warning"}, "python_versions": {"ok"}},
    ),
    "httpx: license expression and License classifier": (
        {
            **DOCS,
            "pyproject.toml": project(
                "httpx", classifier="License :: OSI Approved :: BSD License"
            )
            + HATCHLING,
            "httpx/__init__.py": '__version__ = "1.0.0"\n',
        },
        {"metadata": {"ok"}},
    ),
    "attrs: dynamic version through hatch-vcs": (
        {
            **DOCS,
            "pyproject.toml": project("attrs", version='dynamic = ["version"]')
            + backend("hatchling.build", "hatchling", "hatch-vcs")
            + '\n[tool.hatch.version]\nsource = "vcs"\n',
            "src/attrs/__init__.py": "",
        },
        {"metadata": {"ok"}, "version": {"skipped", "ok"}},
    ),
    "dynamic version through setuptools-scm": (
        {
            **DOCS,
            "pyproject.toml": project("widget", version='dynamic = ["version"]')
            + backend("setuptools.build_meta", "setuptools>=64", "setuptools-scm>=8")
            + "\n[tool.setuptools_scm]\n",
            "src/widget/__init__.py": (
                "from importlib.metadata import version\n\n"
                '__version__ = version("widget")\n'
            ),
        },
        {"metadata": {"ok"}, "version": {"skipped", "ok"}},
    ),
    "rich before Poetry 2: only a tool.poetry table": (
        {
            **DOCS,
            "pyproject.toml": (
                '[tool.poetry]\nname = "rich"\nversion = "13.7.1"\n'
                'description = "Rich text"\nreadme = "README.md"\n'
                'license = "BSD-3-Clause"\nauthors = ["A. Author <a@example.com>"]\n\n'
                '[tool.poetry.dependencies]\npython = ">=3.10"\n\n'
                + backend("poetry.core.masonry.api", "poetry-core>=1.0.0")
            ),
            "rich/__init__.py": "",
        },
        {"metadata": {"ok", "note"}, "version": {"skipped"}},
    ),
    "lower-case and mixed-case file names": (
        {
            "readme.md": README,
            "License": LICENSE,
            "changelog.rst": CHANGELOG,
            "Docs/Index.MD": "# Widget\n",
            "Tests/Test_Widget.py": "",
            "pyproject.toml": MINIMAL + "\n" + HATCHLING,
            "widget/__init__.py": "",
        },
        {
            "docs": {"ok"},
            "license": {"ok"},
            "changelog": {"ok"},
            "test_suite": {"ok"},
        },
    ),
}


@pytest.mark.parametrize(("files", "expected"), SHAPES.values(), ids=SHAPES.keys())
def test_real_repository_shape(
    tmp_path: Path, files: Tree, expected: dict[str, set[str]]
) -> None:
    """A shape seen in the field gets the grade it deserves and no ERROR."""
    report = check(write_tree(tmp_path, files), profile="triage")
    results = {result.name: result for result in report.results}
    assert len(results) == TRIAGE_CHECKS
    for name, allowed in expected.items():
        assert results[name].status.value in allowed, results[name]
    assert [r for r in report.results if r.status is CheckStatus.ERROR] == []


def hostile(pyproject: str | bytes | None, docs: Tree = DOCS) -> Tree:
    """Return an otherwise sound project whose pyproject.toml is *pyproject*."""
    return {
        **docs,
        "src/widget/__init__.py": '__version__ = "1.0.0"\n',
        "pyproject.toml": pyproject,
    }


# Input that is broken or oddly typed. Most rows used to stop the run, or a
# check. The last one is here so that one row holds source that cannot be
# parsed.
MALFORMED: dict[str, Tree] = {
    "invalid TOML": hostile("[project\nname ="),
    "byte order mark": hostile("\N{BYTE ORDER MARK}" + MINIMAL),
    "Latin-1 bytes": hostile(MINIMAL.encode() + b'description = "caf\xe9"\n'),
    "project is a string": hostile('project = "a string"\n'),
    "name is a number": hostile(MINIMAL.replace('"widget"', "3")),
    "dependencies is a string": hostile(MINIMAL + 'dependencies = "x"\n'),
    "urls is a list": hostile(MINIMAL + "urls = []\n"),
    "classifiers is a string": hostile(MINIMAL + 'classifiers = "x"\n'),
    "authors is a string": hostile(MINIMAL + 'authors = "x"\n'),
    "dynamic is a string": hostile(MINIMAL + 'dynamic = "version"\n'),
    "license-files outside the project": hostile(
        MINIMAL + 'license-files = ["/LICENSE", ""]\n', {"README.md": README}
    ),
    "tool is a number": hostile("tool = 1\n" + MINIMAL),
    "checks is a string": hostile(MINIMAL + '\n[tool.pycmdcheck]\nchecks = "all"\n'),
    "check setting is a string": hostile(
        MINIMAL + '\n[tool.pycmdcheck.checks]\ndocs = "yes"\n'
    ),
    "check setting is a number": hostile(
        MINIMAL + "\n[tool.pycmdcheck.checks]\ndocs = 3\n"
    ),
    "fail_on holds a list": hostile(
        MINIMAL + '\n[tool.pycmdcheck]\nfail_on = [["error"]]\n'
    ),
    "pyproject.toml is a directory": hostile(None),
    "empty directory": {},
    "README is a directory": hostile(MINIMAL) | {"README.md": None},
    "Latin-1 bytes in setup.cfg": hostile(MINIMAL)
    | {"setup.cfg": b"[metadata]\nauthor = Jos\xe9\n"},
    "syntax error in the package": hostile(MINIMAL)
    | {"src/widget/__init__.py": "def broken(:\n"},
}


@pytest.mark.parametrize("files", MALFORMED.values(), ids=MALFORMED.keys())
def test_malformed_input_still_gives_a_report(tmp_path: Path, files: Tree) -> None:
    """The bot command prints one JSON report and exits 0, whatever it reads."""
    root = write_tree(tmp_path, files)
    result = CliRunner().invoke(main, [*TRIAGE, str(root)])
    assert result.exit_code == 0, result.output
    results = json.loads(result.stdout)["results"]
    assert len(results) == TRIAGE_CHECKS
    raised = [r for r in results if r["message"].startswith("Check raised exception")]
    assert raised == []
    # One cause gives one ERROR, from the metadata check. An empty directory
    # has no package either, and the structure check may say so.
    errors = {r["name"] for r in results if r["status"] == "error"}
    assert errors <= ({"metadata"} if files else {"metadata", "structure"})
    # The Python API reads the same input and grades it the same way.
    report = check(root, profile="triage")
    assert [r.status.value for r in report.results] == [r["status"] for r in results]


def test_bot_command_in_a_subprocess(
    make_package: Callable[..., Path], tmp_path: Path
) -> None:
    """The installed program works when started from outside the project."""
    done = subprocess.run(
        [sys.executable, "-m", "pycmdcheck", *TRIAGE, str(make_package())],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    data = json.loads(done.stdout)
    assert set(data) == set(JSON_KEYS)
    assert (data["schema_version"], data["profile"]) == (1, "triage")
    assert len(data["results"]) == TRIAGE_CHECKS
    assert {r["name"] for r in data["results"]} == set(PROFILES["triage"].checks)
    for result in data["results"]:
        assert set(result) == set(RESULT_KEYS)


# Settings a project could write to make a triage run look better than it is.
SETTINGS = (
    "\n[tool.pycmdcheck.checks]\nlicense = false\n"
    "docs = { require_readme = false, check_readme_sections = false, "
    "check_docs_site = false, check_docstrings = true, min_docstring_coverage = 90 }\n"
)


@pytest.mark.parametrize(
    "docs", [{"README.md": "# Widget\n"}, {}], ids=["short README", "no README"]
)
def test_project_settings_do_not_change_a_triage_run(
    tmp_path: Path, docs: Tree
) -> None:
    """The checked project gets the same results with and without its settings."""
    pyproject = project("widget") + HATCHLING
    files: Tree = {
        **docs,
        "pyproject.toml": pyproject,
        "src/widget/__init__.py": "def undocumented():\n    pass\n",
    }
    root = write_tree(tmp_path, files)

    def results() -> list[dict[str, object]]:
        result = CliRunner().invoke(main, [*TRIAGE, str(root)])
        assert result.exit_code == 0, result.output
        return [{**r, "duration": 0} for r in json.loads(result.stdout)["results"]]

    plain = results()
    assert len(plain) == TRIAGE_CHECKS
    (root / "pyproject.toml").write_text(pyproject + SETTINGS, encoding="utf-8")
    assert results() == plain


# Settings that try to stop the run, or to write lines of their own to its log.
HUGE = "0x" + "F" * 5000
BREAKING = (
    "\n[tool.pycmdcheck]\n"
    f'fail_on = ["\\u001b[31mred\\nforged", {HUGE}, [{HUGE}]]\n'
    "[tool.pycmdcheck.checks]\n"
    '"x\\n::error::forged" = true\n"y\\u001b[0m\\nforged" = 3\n'
)


def test_project_settings_cannot_stop_a_triage_run_or_forge_its_log(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Text from the project's table is logged on one line each, cut short."""
    root = write_tree(tmp_path, {"pyproject.toml": project("widget") + BREAKING})
    with caplog.at_level("WARNING", logger="pycmdcheck"):
        result = CliRunner().invoke(main, [*TRIAGE, str(root)])
    assert result.exit_code == 0, result.output
    assert len(json.loads(result.stdout)["results"]) == TRIAGE_CHECKS
    messages = [record.getMessage() for record in caplog.records]
    assert sum("forged" in message for message in messages) == 3
    for message in messages:
        assert message.isprintable() and len(message) < 400, message


def test_healthy_project_is_quiet(make_package: Callable[..., Path]) -> None:
    """A complete modern project gets neither an ERROR nor a WARNING."""
    report = check(make_package(), profile="triage")
    assert len(report.results) == TRIAGE_CHECKS
    loud = (CheckStatus.ERROR, CheckStatus.WARNING)
    assert [r for r in report.results if r.status in loud] == []
