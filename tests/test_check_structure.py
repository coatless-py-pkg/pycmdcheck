"""Tests for the structure check, which grades what PackageLayout finds."""

from pathlib import Path

import pytest

from pycmdcheck.checks.structure import StructureCheck
from pycmdcheck.results import CheckResult, CheckStatus
from pycmdcheck.runner import fill_hints
from tests.helpers import found, write_files

MATURIN = '[build-system]\nrequires = ["maturin>=1.0"]\nbuild-backend = "maturin"\n'
SETUPTOOLS = (
    '[build-system]\nrequires = ["setuptools>=61"]\n'
    'build-backend = "setuptools.build_meta"\n'
)


def project(name: str, head: str = "") -> str:
    """Return a pyproject.toml that names the project."""
    return f'{head}[project]\nname = "{name}"\nversion = "0.1.0"\n'


def run(root: Path, files: dict[str, str]) -> CheckResult:
    """Write *files* under *root* and run the structure check there."""
    write_files(root, files)
    return StructureCheck().run(root, {})


# Layouts that are valid: files, expected message, expected detail lines.
VALID = {
    "src package": (
        {"pyproject.toml": project("mypkg"), "src/mypkg/__init__.py": ""},
        "Valid src layout structure",
        ["Using src layout", "Found package: mypkg"],
    ),
    "flat package": (
        {"pyproject.toml": project("mypkg"), "mypkg/__init__.py": ""},
        "Valid flat layout structure",
        ["Using flat layout", "Found package: mypkg"],
    ),
    "single module in src": (
        {"pyproject.toml": project("foo"), "src/foo.py": "x = 1\n"},
        "Valid single-file module structure",
        ["Using src layout", "Found single-file module: foo.py"],
    ),
    "single module at the root": (
        {
            "pyproject.toml": project("six"),
            "six.py": "",
            "setup.py": "",
            "test_six.py": "",
        },
        "Valid single-file module structure",
        ["Using flat layout", "Found single-file module: six.py"],
    ),
    "namespace package in src": (
        {
            "pyproject.toml": project("acme-widgets"),
            "src/acme/widgets/__init__.py": "",
        },
        "Valid namespace package structure",
        ["Using src layout", "Found namespace package: acme"],
    ),
    "namespace package at the root": (
        {"pyproject.toml": project("acme-widgets"), "acme/widgets/__init__.py": ""},
        "Valid namespace package structure",
        ["Using flat layout", "Found namespace package: acme"],
    ),
    "underscore namespace package asked for by packages.find": (
        {
            "pyproject.toml": project("acme-core")
            + '[tool.setuptools.packages.find]\ninclude = ["_acme*"]\n',
            "_acme/widgets/__init__.py": "",
        },
        "Valid namespace package structure",
        ["Using flat layout", "Found namespace package: _acme"],
    ),
    "stub-only package at the root": (
        {"pyproject.toml": project("foo-stubs"), "foo-stubs/__init__.pyi": ""},
        "Valid stub-only package structure",
        ["Using flat layout", "Found stub-only package: foo-stubs"],
    ),
    "stub-only package in src": (
        {"pyproject.toml": project("foo-stubs"), "src/foo-stubs/__init__.pyi": ""},
        "Valid stub-only package structure",
        ["Using src layout", "Found stub-only package: foo-stubs"],
    ),
    "underscore package named by the project": (
        {"pyproject.toml": project("_private"), "src/_private/__init__.py": ""},
        "Valid src layout structure",
        ["Using src layout", "Found package: _private"],
    ),
    "flat package with a reserved name": (
        {"pyproject.toml": project("test"), "test/__init__.py": ""},
        "Valid flat layout structure",
        ["Using flat layout", "Found package: test"],
    ),
    "Pillow: C directories beside the package": (
        {
            "pyproject.toml": project("pillow", SETUPTOOLS),
            "src/PIL/__init__.py": "",
            "src/libImaging/Draw.c": "",
            "src/Tk/tkImaging.c": "",
            "src/thirdparty/raqm/raqm.c": "",
        },
        "Valid src layout structure",
        [
            "Using src layout",
            "Found package: PIL",
            "Ignored non-Python directories in src/: Tk, libImaging, thirdparty",
        ],
    ),
    "orjson: Rust in src/, Python in a declared directory": (
        {
            "pyproject.toml": project("orjson", MATURIN)
            + '[tool.maturin]\npython-source = "pysrc"\n',
            "src/lib.rs": "",
            "src/serialize/mod.rs": "",
            "src/ffi/mod.rs": "",
            "pysrc/orjson/__init__.py": "",
        },
        "Valid package structure",
        [
            "Using source directory pysrc/",
            "Found package: orjson",
            "Ignored non-Python directories in src/: ffi, serialize",
        ],
    ),
    "watchfiles: maturin mixed layout": (
        {
            "pyproject.toml": project("watchfiles", MATURIN),
            "src/lib.rs": "",
            "watchfiles/__init__.py": "",
        },
        "Valid flat layout structure",
        ["Using flat layout", "Found package: watchfiles"],
    ),
    "egg-info left by a build": (
        {
            "pyproject.toml": project("mypkg"),
            "src/mypkg/__init__.py": "",
            "src/mypkg.egg-info/PKG-INFO": "",
        },
        "Valid src layout structure",
        ["Using src layout", "Found package: mypkg"],
    ),
    "package in python/": (
        {"pyproject.toml": project("mypkg"), "python/mypkg/__init__.py": ""},
        "Valid package structure",
        ["Using source directory python/", "Found package: mypkg"],
    ),
    "helper packages are not listed": (
        {
            "pyproject.toml": project("zeta"),
            "zeta/__init__.py": "",
            "examples/__init__.py": "",
            "benchmarks/__init__.py": "",
        },
        "Valid flat layout structure",
        ["Using flat layout", "Found package: zeta"],
    ),
    "several packages are listed in a stable order": (
        {"zeta/__init__.py": "", "alpha/__init__.py": ""},
        "Valid flat layout structure",
        ["Using flat layout", "Found packages: alpha, zeta"],
    ),
    "a sibling without __init__.py is not flagged": (
        {"src/zzz/__init__.py": "", "src/aaa/mod.py": ""},
        "Valid src layout structure",
        ["Using src layout", "Found package: zzz"],
    ),
    "Pillow-SIMD: another import name beside C sources with a script": (
        {
            "pyproject.toml": project("pillow-simd", SETUPTOOLS),
            "PIL/__init__.py": "",
            "src/core.cpp": "",
            "src/third_party/googletest/scripts/fuse.py": "",
        },
        "Valid flat layout structure",
        ["Using flat layout", "Found package: PIL"],
    ),
    "a long list is cut and says how much is left out": (
        {f"p{i:02}/__init__.py": "" for i in range(1, 13)},
        "Valid flat layout structure",
        [
            "Using flat layout",
            "Found packages: p01, p02, p03, p04, p05, p06, p07, p08, p09, p10"
            " (+2 more)",
        ],
    ),
    "a list as long as the limit is shown whole": (
        {f"p{i:02}/__init__.py": "" for i in range(1, 11)},
        "Valid flat layout structure",
        [
            "Using flat layout",
            "Found packages: p01, p02, p03, p04, p05, p06, p07, p08, p09, p10",
        ],
    ),
}


@pytest.mark.parametrize("label", VALID)
def test_valid_layout_is_ok(tmp_path: Path, label: str) -> None:
    files, message, details = VALID[label]
    result = run(tmp_path, files)
    assert (result.status, result.message) == (CheckStatus.OK, message)
    assert result.details == details
    assert (result.findings, result.skip_reason) == ([], None)


# Projects with no Python package to find: files, and the build evidence that
# must be in the details (None when nothing says the build is native). With
# evidence no package is expected and the check is skipped. Without it the
# sources are unexplained, which is a note.
NATIVE = {
    "maturin with src/lib.rs": (
        {"pyproject.toml": project("rustpkg", MATURIN), "src/lib.rs": ""},
        "build-backend is maturin",
    ),
    "maturin with nested Rust modules": (
        {
            "pyproject.toml": project("orjson", MATURIN),
            "src/lib.rs": "",
            "src/serialize/mod.rs": "",
        },
        "build-backend is maturin",
    ),
    "maturin without src/": (
        {"pyproject.toml": project("rustpkg", MATURIN), "Cargo.toml": ""},
        "Cargo.toml is present",
    ),
    "setuptools-rust behind setuptools": (
        {
            "pyproject.toml": project(
                "rustpkg",
                '[build-system]\nrequires = ["setuptools", "setuptools-rust"]\n'
                'build-backend = "setuptools.build_meta"\n',
            ),
            "src/lib.rs": "",
        },
        "build-system.requires lists setuptools-rust",
    ),
    "scikit-build-core": (
        {
            "pyproject.toml": project(
                "cpkg",
                '[build-system]\nrequires = ["scikit-build-core"]\n'
                'build-backend = "scikit_build_core.build"\n',
            ),
            "src/main.cpp": "",
        },
        "build-backend is scikit_build_core.build",
    ),
    "ujson: C sources in src/ and a plain setuptools backend": (
        {
            "pyproject.toml": project("ujson", SETUPTOOLS),
            "src/ujson/ujson.c": "",
            "src/ujson/ujson.h": "",
        },
        None,
    ),
    "data files only in src/": (
        {"pyproject.toml": project("mydata"), "src/mydata/table.csv": ""},
        None,
    ),
}


@pytest.mark.parametrize("label", NATIVE)
def test_other_sources_without_python_are_skipped_or_a_note(
    tmp_path: Path, label: str
) -> None:
    files, evidence = NATIVE[label]
    result = run(tmp_path, files)
    in_src = " in src/" if any(name.startswith("src/") for name in files) else ""
    kind = "native-extension layout" if evidence else "non-Python sources only"
    assert result.message == f"No Python package{in_src} ({kind})"
    assert evidence is None or evidence in result.details
    assert result.details[-1].startswith("Looked in the project root, src/")
    assert (result.status, result.skip_reason, found(result)) == (
        (CheckStatus.SKIPPED, "not-applicable", [])
        if evidence
        else (
            CheckStatus.NOTE,
            None,
            [("structure.non-python-sources", CheckStatus.NOTE)],
        )
    )


@pytest.mark.parametrize(
    "files",
    [
        {"src/mypkg/core.py": "x = 1\n"},
        {"pyproject.toml": project("mypkg"), "mypkg/core.py": "x = 1\n"},
    ],
    ids=["src", "flat"],
)
def test_package_without_init_is_a_warning(
    tmp_path: Path, files: dict[str, str]
) -> None:
    """The same forgotten __init__.py gets the same grade in both layouts."""
    result = run(tmp_path, files)
    assert result.status == CheckStatus.WARNING
    assert result.message == "Some packages missing __init__.py"
    assert "Missing __init__.py in: mypkg" in result.details
    assert found(result) == [("structure.missing-init", CheckStatus.WARNING)]
    assert result.findings[0].message == "Missing __init__.py in: mypkg"


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({}, "No package or module found"),
        ({"pyproject.toml": "[project]\n", "src/.keep": ""}, "No package directory"),
        (
            {"pyproject.toml": project("x"), "tests/__init__.py": "", "setup.py": ""},
            "No package or module found",
        ),
        (
            {"pyproject.toml": project("x"), "conftest.py": ""},
            "No package or module found",
        ),
        (
            {
                "pyproject.toml": project("x"),
                "__pypackages__/3.12/lib/foo/__init__.py": "",
            },
            "No package or module found",
        ),
        (
            {
                "pyproject.toml": project("x"),
                "_build/x/__init__.py": "",
                "_vendor/six/__init__.py": "",
            },
            "No package or module found",
        ),
    ],
    ids=[
        "empty",
        "empty src",
        "tests and setup.py only",
        "conftest.py only",
        "__pypackages__ only",
        "_build and _vendor only",
    ],
)
def test_nothing_importable_is_an_error(
    tmp_path: Path, files: dict[str, str], message: str
) -> None:
    result = run(tmp_path, files)
    assert result.status == CheckStatus.ERROR
    assert result.message.startswith(message)
    assert any(d.startswith("Looked in") for d in result.details)
    assert found(result) == [("structure.missing-package", CheckStatus.ERROR)]
    assert result.findings[0].message == result.message


@pytest.mark.parametrize(
    ("files", "seen"),
    [
        ({"pyproject.toml": project("x"), "noxfile.py": "", "tasks.py": ""}, "noxfile"),
        (
            {
                "pyproject.toml": project("ws"),
                "packages/core/src/core/__init__.py": "",
            },
            "packages",
        ),
        (
            {
                "pyproject.toml": project("x"),
                "env/pyvenv.cfg": "",
                "env/lib/site-packages/foo/__init__.py": "",
            },
            "env",
        ),
    ],
    ids=["tooling scripts only", "workspace root", "virtualenv only"],
)
def test_python_outside_a_known_layout_is_a_note(
    tmp_path: Path, files: dict[str, str], seen: str
) -> None:
    """Shapes that 0.1.1 called valid are neither a package nor a new ERROR."""
    result = run(tmp_path, files)
    assert result.status == CheckStatus.NOTE
    assert result.message == "No package found in a recognized layout"
    assert result.details[0].startswith("Python files outside a recognized layout: ")
    assert seen in result.details[0]
    assert found(result) == [("structure.unrecognized-layout", CheckStatus.NOTE)]
    assert result.findings[0].message == result.details[0]


# For each rule, the files of a project in which it fires, the files of the
# same project after its hint was followed, and the words of the hint that
# say what was added.
FOLLOWED = {
    "structure.missing-package": (
        {"pyproject.toml": project("sum-tools"), "sumlib/core.py": "x = 1\n"},
        {
            "pyproject.toml": project("sum-tools"),
            "sumlib/core.py": "x = 1\n",
            "sumlib/__init__.py": "",
        },
        "Add an __init__.py file to the directory that holds your code",
    ),
    "structure.missing-init": (
        {"src/mypkg/core.py": "x = 1\n"},
        {"src/mypkg/core.py": "x = 1\n", "src/mypkg/__init__.py": ""},
        "Add an empty __init__.py file to the package directory",
    ),
    "structure.non-python-sources": (
        {"src/mydata/table.csv": ""},
        {"src/mydata/table.csv": "", "src/mydata/__init__.py": ""},
        "Add a package directory with an __init__.py file to src/",
    ),
    "structure.unrecognized-layout": (
        {"packages/core/src/core/__init__.py": ""},
        {"src/core/__init__.py": ""},
        "Move the package to src/<name>/",
    ),
}


def hint(result: CheckResult) -> str:
    """Return the hint that a report shows for the one finding of *result*."""
    fill_hints(result, StructureCheck.hints)
    (finding,) = result.findings
    return finding.hint or ""


@pytest.mark.parametrize("code", StructureCheck.rules)
def test_following_the_hint_clears_the_rule(tmp_path: Path, code: str) -> None:
    before, after, said = FOLLOWED[code]
    result = run(tmp_path / "before", before)
    assert [rule for rule, _ in found(result)] == [code]
    assert hint(result).startswith(said)
    assert run(tmp_path / "after", after).status == CheckStatus.OK


@pytest.mark.parametrize(
    "stray",
    [{"noxfile.py": ""}, {"env/pyvenv.cfg": "", "env/lib/foo/__init__.py": ""}],
    ids=["script", "virtualenv"],
)
def test_stray_python_that_is_no_package_is_told_to_create_one(
    tmp_path: Path, stray: dict[str, str]
) -> None:
    """There is nothing to move, and moving a script would make a WARNING."""
    files = {"pyproject.toml": project("mypkg"), **stray}
    result = run(tmp_path / "before", files)
    assert [rule for rule, _ in found(result)] == ["structure.unrecognized-layout"]
    assert hint(result).startswith("Create src/<name>/__init__.py, where <name>")
    after = run(tmp_path / "after", {**files, "src/mypkg/__init__.py": ""})
    assert after.status == CheckStatus.OK


@pytest.mark.parametrize(
    ("code", "files", "said"),
    [
        (
            "structure.non-python-sources",
            {
                "pyproject.toml": project("core")
                + '[build-system]\nrequires = ["maturin"]\n',
                "src/core/main.rs": "",
            },
            "or list the build tool, such as maturin or Cython, in requires",
        ),
        (
            "structure.unrecognized-layout",
            {
                "pyproject.toml": project("core")
                + '[tool.setuptools.packages.find]\nwhere = ["packages/core/src"]\n',
                "packages/core/src/core/__init__.py": "",
            },
            "or name the directory that holds it in the build settings, such as "
            "where in [tool.setuptools.packages.find].",
        ),
    ],
)
def test_second_way_of_a_hint_clears_the_rule(
    tmp_path: Path, code: str, files: dict[str, str], said: str
) -> None:
    assert said in StructureCheck.hints[code]
    assert run(tmp_path, files).findings == []


def test_package_linked_from_outside_the_project_is_found(tmp_path: Path) -> None:
    """Only paths taken from pyproject.toml are confined to the project."""
    shared = tmp_path / "shared" / "pkg"
    shared.mkdir(parents=True)
    (shared / "__init__.py").write_text("", encoding="utf-8")
    root = tmp_path / "proj"
    (root / "src").mkdir(parents=True)
    try:
        (root / "src" / "pkg").symlink_to(shared, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")
    result = run(root, {})
    assert (result.status, result.message) == (
        CheckStatus.OK,
        "Valid src layout structure",
    )
    assert result.details == ["Using src layout", "Found package: pkg"]
