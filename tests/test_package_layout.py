"""Tests for PackageLayout: small project trees and what is found in them."""

import os
from collections.abc import Iterable
from pathlib import Path

import pytest

from pycmdcheck.package_layout import NON_MODULE_FILES, PackageLayout, normalize_name

MATURIN = 'requires = ["maturin>=1,<2"]\nbuild-backend = "maturin"'
SETUPTOOLS = 'requires = ["setuptools>=61"]\nbuild-backend = "setuptools.build_meta"'
SETUP = "from setuptools import setup\n"


def tree(root: Path, paths: str = "", pyproject: str = "", **texts: str) -> Path:
    """Create a project under *root* and return *root*.

    *paths* names empty files, separated by spaces. *pyproject* is the text
    of pyproject.toml. Other keywords name files with content, with ``_`` for
    the dot, so ``setup_py="..."`` writes setup.py.
    """
    files = dict.fromkeys(paths.split(), "")
    files |= {name.replace("_", "."): text for name, text in texts.items()}
    if pyproject:
        files["pyproject.toml"] = pyproject
    for relative, content in files.items():
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return root


def project(name: str, extra: str = "", backend: str = "") -> str:
    """Return a small pyproject.toml for a project called *name*."""
    text = f'[project]\nname = "{name}"\nversion = "1.0.0"\n'
    if backend:
        text += f"\n[build-system]\n{backend}\n"
    return text + (f"\n{extra}\n" if extra else "")


def rel(paths: Iterable[Path], root: Path) -> list[str]:
    """Return *paths* relative to *root* as sorted POSIX strings."""
    return sorted(path.relative_to(root).as_posix() for path in paths)


def found(layout: PackageLayout, root: Path) -> list[str]:
    """Return ``"name kind package_dir"`` for every candidate, best first.

    A module has no package directory, so its file is shown.
    """
    return [
        f"{c.name} {c.kind} {rel([c.package_dir or c.path], root)[0]}"
        for c in layout.packages
    ]


def link(target: Path, at: Path) -> None:
    """Make *at* a symlink to the directory *target*, or skip the test."""
    try:
        os.symlink(target, at, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks are not available")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("My-Pkg", "my_pkg"),
        ("my.pkg", "my_pkg"),
        ("A-._B", "a_b"),
        ("  spaced  ", "spaced"),
        ("_Private", "_private"),
        ("", ""),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


# What is found in a project. Each row holds "project name: files" (with "-"
# for no pyproject.toml) and "how the first candidate was chosen: candidates",
# best first. A candidate is shown with the directory to inspect, or with its
# file if it is a module, so the import root shows as well.
SHAPES: dict[str, tuple[str, str]] = {
    "src package": ("mypkg: src/mypkg/__init__.py", "name: mypkg package src/mypkg"),
    "flat package": ("My-Pkg: my_pkg/__init__.py", "name: my_pkg package my_pkg"),
    "nothing": ("mypkg: README.md docs/conf.py tests/__init__.py setup.py", "none:"),
    "single module beside tooling files (six)": (
        "six: six.py test_six.py setup.py conftest.py noxfile.py",
        "name: six module six.py",
    ),
    "tooling script named by the project": (
        "tasks: tasks.py",
        "name: tasks module tasks.py",
    ),
    "namespace package (poetry-core)": (
        "poetry-core: src/poetry/core/__init__.py src/poetry/core/factory.py",
        "name: poetry namespace src/poetry/core",
    ),
    "namespace of loose modules (poetry)": (
        "poetry: src/poetry/factory.py src/poetry/console/__init__.py",
        "name: poetry namespace src/poetry",
    ),
    "flat namespace package (jaraco.functools)": (
        "jaraco.functools: jaraco/functools/__init__.py test_functools.py docs/conf.py",
        "name: jaraco namespace jaraco/functools",
    ),
    "dashed name follows namespace directories": (
        "google-cloud-storage: google/cloud/storage/__init__.py noxfile.py",
        "name: google namespace google/cloud/storage",
    ),
    "dashed name with a joined last directory": (
        "google-cloud-bigquery-storage: google/cloud/bigquery_storage/__init__.py",
        "name: google namespace google/cloud/bigquery_storage",
    ),
    "dotted name passes through a regular package (zope.interface)": (
        "zope.interface: src/zope/__init__.py src/zope/interface/__init__.py",
        "name: zope package src/zope/interface",
    ),
    "dotted name is followed as written": (
        "my_ns.pkg: src/my_ns/pkg/__init__.py",
        "name: my_ns namespace src/my_ns/pkg",
    ),
    "dotted name wins over the joined name": (
        "a.b: a_b/__init__.py a/b/__init__.py",
        "name: a namespace a/b, a_b package a_b",
    ),
    "dashed name stops at a regular package": (
        "foo-utils: foo/__init__.py foo/utils/__init__.py",
        "single: foo package foo",
    ),
    "name without its affix never matches a helper (pytest)": (
        "pytest: src/test/__init__.py src/realpkg/__init__.py",
        "single: realpkg package src/realpkg, test package src/test",
    ),
    "guessed split never starts in a helper directory": (
        "test-utils: test/utils/helpers.py realpkg/__init__.py",
        "single: realpkg package realpkg",
    ),
    "name as written comes before the name without its affix": (
        "pycore: pycore/__init__.py core/__init__.py",
        "name: pycore package pycore, core package core",
    ),
    "namespace package under another project name": (
        "other: src/ns/sub/__init__.py",
        "single: ns namespace src/ns/sub",
    ),
    "flat namespace package under another project name": (
        "other: acme/widgets/__init__.py examples/demo/__init__.py docs/x/__init__.py",
        "single: acme namespace acme/widgets, examples namespace examples/demo",
    ),
    "directory of nested packages beside a real package": (
        "other: mypkg/__init__.py third_party/lib/__init__.py",
        "single: mypkg package mypkg",
    ),
    "underscore directories at the root are no namespace guess": (
        "x: _build/x/__init__.py _vendor/six/__init__.py",
        "none:",
    ),
    "source directory names are no namespace guess either": (
        "other: lib/pkg/__init__.py",
        "single: pkg package lib/pkg",
    ),
    "underscore namespace package named by the project": (
        "_acme.widgets: _acme/widgets/__init__.py",
        "name: _acme namespace _acme/widgets",
    ),
    "workspace of sub-projects": (
        "-: libs/a/pyproject.toml libs/a/a/__init__.py libs/b/src/b/__init__.py",
        "none:",
    ),
    "sub-package that is really called src": (
        "-: acme/src/__init__.py acme/src/mod.py",
        "single: acme namespace acme/src",
    ),
    "environment directories beside a module": (
        "unrelated: solo.py env/conda-meta/history env/lib/json/__init__.py "
        "myenv/pyvenv.cfg myenv/lib/python3.12/site-packages/pip/__init__.py",
        "single: solo module solo.py",
    ),
    "stub-only package (pandas-stubs)": (
        "pandas-stubs: pandas-stubs/__init__.pyi scripts/__init__.py tests/__init__.py",
        "name: pandas-stubs stub-only pandas-stubs, scripts package scripts",
    ),
    "stub-only package beside C sources (ujson)": (
        "ujson: src/ujson/__init__.pyi src/ujson/lib/ujson.c",
        "name: ujson stub-only src/ujson",
    ),
    "stub-only package without the -stubs suffix": (
        "-: src/typedpkg/__init__.pyi",
        "single: typedpkg stub-only src/typedpkg",
    ),
    "directory names that cannot be imported": (
        "mypkg: src/mypkg/__init__.py src/9lives/__init__.py src/a-b/__init__.py",
        "name: mypkg package src/mypkg",
    ),
    "unimportable directory that the project name points at": (
        "my-pkg: src/my-pkg/__init__.py",
        "name: my-pkg package src/my-pkg",
    ),
    "helper directory ranks last": (
        "unrelated: benchmarks/__init__.py zzz/__init__.py",
        "single: zzz package zzz, benchmarks package benchmarks",
    ),
    "project that is really named like a helper": (
        "tools: tools/__init__.py zzz/__init__.py",
        "name: tools package tools, zzz package zzz",
    ),
    "helper directory and nothing else": (
        "-: scripts/__init__.py scripts/run.py",
        "guess: scripts package scripts",
    ),
    "script beside a package": (
        "mypkg: mypkg/__init__.py release.py",
        "name: mypkg package mypkg, release module release.py",
    ),
    "several packages and no name to choose by": (
        "frobnicator: src/zz/__init__.py src/aa/__init__.py src/_x/__init__.py",
        "guess: aa package src/aa, zz package src/zz, _x package src/_x",
    ),
    "guess prefers a name like the project name (scikit-learn)": (
        "scikit-learn: sklearn/__init__.py build_tools/__init__.py",
        "guess: sklearn package sklearn, build_tools package build_tools",
    ),
    "tests, docs and build are not flat packages": (
        "mypkg: mypkg/__init__.py tests/__init__.py docs/__init__.py build/__init__.py",
        "name: mypkg package mypkg",
    ),
    "flat project named like a reserved directory": (
        "docs: docs/__init__.py",
        "name: docs package docs",
    ),
    "src with its own __init__.py is a package": (
        "srcpkg: src/__init__.py src/core.py src/sub/__init__.py",
        "single: src package src",
    ),
    "src wins a tie with the root": (
        "unrelated: src/alpha/__init__.py zzz/__init__.py",
        "single: alpha package src/alpha",
    ),
    "src with nothing importable": (
        "-: src/.keep mypkg/__init__.py",
        "single: mypkg package mypkg",
    ),
    "C sources and a script in src, package at the root": (
        "mypkg: src/ext.c src/gen_tables.py mypkg/__init__.py",
        "name: mypkg package mypkg",
    ),
    "another import name beside C sources with a script (Pillow-SIMD)": (
        "pillow-simd: src/core.cpp src/third_party/gtest/fuse.py PIL/__init__.py",
        "single: PIL package PIL",
    ),
    "script named like the project does not hide a src package": (
        "mytool: src/core/__init__.py mytool.py",
        "single: core package src/core",
    ),
    "directory without __init__.py does not hide a src package": (
        "mytool: src/core/__init__.py mytool/notes.py",
        "single: core package src/core",
    ),
    "root package named like the project beats a src package": (
        "mytool: src/core/__init__.py mytool/__init__.py",
        "name: mytool package mytool",
    ),
    "root module named like the project beats a script in src": (
        "mytool: mytool.py src/gen.py",
        "name: mytool module mytool.py",
    ),
    "package in lib": (
        "mypkg: lib/mypkg/__init__.py tools/__init__.py",
        "name: mypkg package lib/mypkg",
    ),
    "root wins a tie with lib": (
        "unrelated: lib/vendored_thing/__init__.py mypkg/__init__.py",
        "single: mypkg package mypkg",
    ),
    "module in lib does not hide a root package": (
        "mytool: core/__init__.py lib/mytool.py",
        "single: core package core",
    ),
}


@pytest.mark.parametrize("label", SHAPES)
def test_candidates(tmp_path: Path, label: str) -> None:
    (name, _, paths), (match, _, candidates) = (
        row.partition(":") for row in SHAPES[label]
    )
    layout = PackageLayout(tree(tmp_path, paths, "" if name == "-" else project(name)))
    assert found(layout, tmp_path) == [c.strip() for c in candidates.split(",") if c]
    assert layout.primary_match == match
    assert (layout.primary_candidate is None) == (match == "none")


@pytest.mark.parametrize(
    ("name", "directory"),
    [
        ("MyPkg", "mypkg"),
        ("my.pkg", "my_pkg"),
        ("my_pkg", "My_Pkg"),
        ("python-dateutil", "dateutil"),
        ("PyYAML", "yaml"),
        ("foo-python", "foo"),
    ],
)
def test_project_name_selects_the_package(
    tmp_path: Path, name: str, directory: str
) -> None:
    """Case, separators and a python affix do not matter."""
    paths = f"src/aaa_helper/__init__.py src/{directory}/__init__.py"
    layout = PackageLayout(tree(tmp_path, paths, project(name)))
    assert found(layout, tmp_path)[0] == f"{directory} package src/{directory}"
    assert layout.primary_match == "name"


@pytest.mark.parametrize(
    ("config", "directory"),
    [
        ({"setup_cfg": "[metadata]\nname = Cfg-Pkg\n"}, "cfg_pkg"),
        ({"setup_py": SETUP + "setup(name='Old.Pkg')\n"}, "old_pkg"),
        ({"pyproject": '[tool.poetry]\nname = "poet"\n'}, "poet"),
    ],
    ids=["setup.cfg", "setup.py", "poetry"],
)
def test_project_name_is_read_from_older_metadata(
    tmp_path: Path, config: dict[str, str], directory: str
) -> None:
    tree(tmp_path, f"aaa/__init__.py {directory}/__init__.py", **config)
    layout = PackageLayout(tmp_path)
    assert found(layout, tmp_path)[0] == f"{directory} package {directory}"
    assert layout.primary_match == "name"


@pytest.mark.parametrize(
    "config",
    [
        {"pyproject": '[tool.maturin]\npython-source = "py"'},
        {"pyproject": '[tool.setuptools.package-dir]\n"" = "py"'},
        {"pyproject": '[tool.setuptools.packages.find]\nwhere = ["py"]'},
        {"pyproject": '[tool.hatch.build.targets.wheel]\npackages = ["py/mypkg"]'},
        {"pyproject": '[tool.hatch.build.targets.wheel]\nsources = ["py"]'},
        {"pyproject": '[tool.hatch.build.targets.wheel.sources]\n"py" = ""'},
        {"pyproject": '[tool.poetry]\npackages = [{include = "mypkg", from = "py"}]'},
        {"pyproject": '[tool.pdm.build]\npackage-dir = "py"'},
        {"pyproject": '[tool.scikit-build]\nwheel.packages = ["py/mypkg"]'},
        {"setup_cfg": "[options]\npackage_dir =\n    = py\npackages = find:\n"},
        {"setup_cfg": "[options.packages.find]\nwhere = py\n"},
        {"setup_py": SETUP + "setup(package_dir={'': 'py'}, packages=['mypkg'])\n"},
    ],
    ids="maturin package-dir find hatch-packages hatch-sources hatch-sources-table "
    "poetry pdm scikit-build setup.cfg-package_dir setup.cfg-find setup.py".split(),
)
def test_backend_names_the_source_directory(
    tmp_path: Path, config: dict[str, str]
) -> None:
    tree(tmp_path, "py/mypkg/__init__.py decoy/__init__.py", **config)
    assert found(PackageLayout(tmp_path), tmp_path) == ["mypkg package py/mypkg"]


@pytest.mark.parametrize(
    "value", ['"../outside"', '"/etc"', '"C:/Windows"', "42", '["a", "b"]', '""']
)
def test_unsafe_or_malformed_source_directory_is_ignored(
    tmp_path: Path, value: str
) -> None:
    tree(tmp_path / "outside", "evil/__init__.py")
    pyproject = project("mypkg", f"[tool.maturin]\npython-source = {value}")
    root = tree(tmp_path / "proj", "mypkg/__init__.py", pyproject)
    assert found(PackageLayout(root), root) == ["mypkg package mypkg"]


def test_declared_source_directory_special_cases(tmp_path: Path) -> None:
    """One without Python is passed over. One that is a package itself is used."""
    empty = project("mypkg", '[tool.maturin]\npython-source = "python"')
    root = tree(tmp_path / "empty", "python/README.txt mypkg/__init__.py", empty)
    assert found(PackageLayout(root), root) == ["mypkg package mypkg"]

    where = project("srcpkg", '[tool.setuptools.packages.find]\nwhere = ["src"]')
    root = tree(tmp_path / "init", "src/__init__.py src/srcpkg/__init__.py", where)
    assert found(PackageLayout(root), root) == ["srcpkg package src/srcpkg"]


def test_two_declared_source_directories_are_both_used(tmp_path: Path) -> None:
    where = '[tool.setuptools.packages.find]\nwhere = ["src", "plugins", "./src"]'
    paths = "src/core/__init__.py plugins/core_extra/__init__.py"
    layout = PackageLayout(tree(tmp_path, paths, project("core", where)))
    assert rel(layout.package_dirs, tmp_path) == ["plugins/core_extra", "src/core"]
    assert {"core", "core_extra"} <= layout.local_names()


# Packages that the build backend names or leaves out. Each row holds the
# configuration, the files, and "how the first candidate was chosen: name
# rank" for every candidate, best first.
ATTRS = '[tool.hatch.build.targets.wheel]\npackages = ["src/attr", "src/attrs"]'
FIND_EXCLUDE = "[options]\npackages = find:\n[options.packages.find]\nexclude = dev*\n"
APPENDED = "packages = ['yaml']\npackages.append('_yaml')\n"
FIND = '[tool.setuptools.packages.find]\ninclude = ["{}"]'
FIND_BOTH = FIND + '\nexclude = ["{}"]'
PRIVATE_NAMESPACE = ["_acme namespace _acme/widgets", "tools package tools"]
DECLARED: dict[str, tuple[dict[str, str], str, str]] = {
    "flit module name": (
        {"pyproject": project("dist", '[tool.flit.module]\nname = "realname"')},
        "aaa/__init__.py realname/__init__.py",
        "declared: realname 3, aaa 1",
    ),
    "setuptools package-dir mapping": (
        {"pyproject": '[tool.setuptools.package-dir]\nmypkg = "code/my-pkg"'},
        "code/aaa/__init__.py code/my-pkg/__init__.py",
        "declared: my-pkg 3, aaa 2",
    ),
    "maturin module-name (watchfiles)": (
        {"pyproject": '[tool.maturin]\nmodule-name = "watchfiles._rust"'},
        "aaa/__init__.py watchfiles/__init__.py",
        "declared: watchfiles 3, aaa 2",
    ),
    "maturin python-packages": (
        {"pyproject": '[tool.maturin]\npython-packages = ["extra"]'},
        "aaa/__init__.py extra/__init__.py",
        "declared: extra 3, aaa 2",
    ),
    "name match comes before a declared sibling (attrs)": (
        {"pyproject": project("attrs", ATTRS)},
        "src/attr/__init__.py src/attrs/__init__.py",
        "name: attrs 3, attr 3",
    ),
    "explicit list makes the rest helpers (PyYAML)": (
        {"setup_py": SETUP + "setup(name='PyYAML', packages=['yaml', '_yaml'])\n"},
        "yaml/__init__.py _yaml/__init__.py extras/__init__.py babel_runner.py",
        "name: yaml 3, _yaml 3, extras 1, babel_runner 1",
    ),
    "explicit list in setup.cfg makes the rest helpers": (
        {"setup_cfg": "[metadata]\nname = PyYAML\n[options]\npackages = yaml, _yaml\n"},
        "yaml/__init__.py _yaml/__init__.py extras/__init__.py babel_runner.py",
        "name: yaml 3, _yaml 3, extras 1, babel_runner 1",
    ),
    "declared public name comes before a declared private one": (
        {"setup_cfg": "[metadata]\nname = demo\n[options]\npackages = yaml, _yaml\n"},
        "yaml/__init__.py _yaml/__init__.py",
        "declared: yaml 3, _yaml 3",
    ),
    "computed list says nothing about the rest": (
        {"setup_py": SETUP + "setup(name='unrelated', packages=find_packages())\n"},
        "alpha/__init__.py beta/__init__.py",
        "guess: alpha 2, beta 2",
    ),
    "list changed after it was built says nothing about the rest": (
        {"setup_py": SETUP + APPENDED + "setup(name='demo', packages=packages)\n"},
        "yaml/__init__.py _yaml/__init__.py",
        "guess: yaml 2, _yaml 2",
    ),
    "packages.find include (xarray)": (
        {"pyproject": '[tool.setuptools.packages.find]\ninclude = ["xarray*"]'},
        "properties/__init__.py xarray/__init__.py",
        "single: xarray 2, properties 1",
    ),
    "packages.find exclude in setup.cfg": (
        {"setup_cfg": FIND_EXCLUDE},
        "devtools/__init__.py mylib/__init__.py",
        "single: mylib 2, devtools 1",
    ),
    "stale list leaves the real package a guess": (
        {"pyproject": '[tool.setuptools]\npackages = ["gone"]'},
        "real/__init__.py",
        "guess: real 1",
    ),
}


@pytest.mark.parametrize("label", DECLARED)
def test_backend_names_the_packages(tmp_path: Path, label: str) -> None:
    config, paths, expected = DECLARED[label]
    layout = PackageLayout(tree(tmp_path, paths, **config))
    ranked = ", ".join(f"{c.name} {c.rank}" for c in layout.packages)
    assert f"{layout.primary_match}: {ranked}" == expected
    # Helpers are left out of the source files whenever a real package exists.
    runtime = [c for c in layout.packages if c.rank >= 2] or layout.packages
    sources = layout.source_files()
    scanned = {f.parent if f.name == "__init__.py" else f for f in sources}
    assert scanned == {c.path for c in runtime}


FIND_UNDERSCORE = "[options]\npackages = find_namespace:\n[options.packages.find]\n"
FIND_PLAIN = FIND_UNDERSCORE.replace("find_namespace:", "find:")


@pytest.mark.parametrize(
    ("config", "expected"),
    [
        ({"pyproject": project("x", FIND.format("_acme*"))}, PRIVATE_NAMESPACE),
        ({"pyproject": project("x", FIND.format("_acme.*"))}, PRIVATE_NAMESPACE),
        ({"pyproject": project("x", FIND.format("_acme"))}, PRIVATE_NAMESPACE),
        ({"setup_cfg": FIND_UNDERSCORE + "include = _acme*\n"}, PRIVATE_NAMESPACE),
        # A pattern that starts with no underscore asks for no such directory.
        ({"pyproject": project("x", FIND.format("*"))}, ["tools package tools"]),
        ({"pyproject": project("x", FIND.format("?acme*"))}, ["tools package tools"]),
        ({"pyproject": project("x", FIND.format("_other*"))}, ["tools package tools"]),
        # What exclude takes out again is not asked for. A part below it may be.
        (
            {"pyproject": project("x", FIND_BOTH.format("_*", "_acme*"))},
            ["tools package tools"],
        ),
        (
            {"pyproject": project("x", FIND_BOTH.format("_*", "_acme.tests*"))},
            PRIVATE_NAMESPACE,
        ),
        # A search that setuptools would not make here asks for nothing.
        (
            {"pyproject": project("x", FIND.format("_acme*") + "\nnamespaces = false")},
            ["tools package tools"],
        ),
        (
            {"pyproject": project("x", FIND.format("_acme*") + '\nwhere = ["lib"]')},
            ["tools package tools"],
        ),
        (
            {"setup_cfg": FIND_PLAIN + "include = _acme*\n"},
            ["tools package tools"],
        ),
        (
            {"setup_cfg": FIND_UNDERSCORE + "include = _acme*\nwhere = lib\n"},
            ["tools package tools"],
        ),
        (
            {"pyproject": project("x", FIND.format("_acme*") + '\nwhere = ["."]')},
            PRIVATE_NAMESPACE,
        ),
    ],
    ids=[
        "prefix",
        "dotted",
        "exact",
        "setup.cfg",
        "star",
        "question mark",
        "other",
        "excluded",
        "part excluded",
        "namespaces off",
        "where elsewhere",
        "setup.cfg plain find",
        "setup.cfg where elsewhere",
        "where is the root",
    ],
)
def test_find_pattern_asks_for_an_underscore_namespace(
    tmp_path: Path, config: dict[str, str], expected: list[str]
) -> None:
    """Build output is named by no pattern, and a private namespace can be."""
    root = tree(tmp_path, "_acme/widgets/__init__.py tools/__init__.py", **config)
    assert found(PackageLayout(root), root) == expected


def test_candidate_details(tmp_path: Path) -> None:
    paths = (
        "src/regular/__init__.py src/deliberate/part/__init__.py "
        "src/branching/a/__init__.py src/branching/b/__init__.py "
        "src/forgotten/module.py src/examples/__init__.py src/solo.py"
    )
    layout = PackageLayout(tree(tmp_path, paths, project("regular")))
    by_name = {c.name: c for c in layout.packages}
    src = tmp_path / "src"
    assert {
        name: (c.kind, c.package_dir, c.contains_package, c.rank)
        for name, c in by_name.items()
    } == {
        "regular": ("package", src / "regular", True, 3),
        "deliberate": ("namespace", src / "deliberate" / "part", True, 2),
        "branching": ("namespace", src / "branching", True, 2),
        "forgotten": ("namespace", src / "forgotten", False, 2),
        "examples": ("package", src / "examples", True, 1),
        "solo": ("module", None, False, 2),
    }
    assert by_name["regular"].matches_name and by_name["examples"].is_helper
    assert by_name["solo"].path == src / "solo.py"
    assert by_name["solo"].root.path == src
    assert rel(layout.package_dirs, tmp_path) == ["src/examples", "src/regular"]
    assert layout.top_level_modules() == [src / "solo.py"]
    layout.packages.clear()
    assert len(layout.packages) == 6


def test_tooling_and_test_files_are_not_modules(tmp_path: Path) -> None:
    paths = " ".join(sorted(NON_MODULE_FILES)) + " test_x.py x_test.py real.py 9.py"
    layout = PackageLayout(tree(tmp_path, paths))
    assert layout.top_level_modules() == [tmp_path / "real.py"]


# ── Source files, stubs and first-party names ─────────────────────────


def test_source_files_leave_out_tests_vendored_code_and_helpers(tmp_path: Path) -> None:
    runtime = (
        "pkg/__init__.py pkg/build/__init__.py pkg/core.py pkg/vendor/domain.py "
        "pkg/venv/__init__.py"
    ).split()
    tests = (
        "pkg/conftest.py pkg/test_colocated.py pkg/testing/__init__.py pkg/tests.py "
        "pkg/tests/test_x.py pkg/thing_test.py"
    ).split()
    never = (
        "pkg/_vendor/toml/__init__.py pkg/extern/c.py pkg/data-files/template.py "
        "pkg/__pycache__/stale.py pkg/.hidden/secret.py pkg/node_modules/gyp/gyp.py "
        "pkg/venv_dir/pyvenv.cfg pkg/venv_dir/lib/site.py pkg/data.egg-info/x.py "
        "pkg/conda_env/conda-meta/history pkg/conda_env/lib/json/__init__.py "
        "scripts/__init__.py hatch_build.py test_root.py release.py docs/conf.py "
        "examples/demo.py"
    )
    tree(tmp_path, " ".join([*runtime, *tests, never]), project("pkg"))
    layout = PackageLayout(tmp_path)
    assert rel(layout.source_files(), tmp_path) == runtime
    assert rel(layout.source_files(include_tests=True), tmp_path) == sorted(
        runtime + tests
    )


@pytest.mark.parametrize(
    ("name", "paths", "expected"),
    [
        ("foo", "foo.py foo_compat.py test_foo.py noxfile.py docs/conf.py", 2),
        ("mainpkg", "src/mainpkg/__init__.py src/_helper.py src/_impl/__init__.py", 3),
        ("poetry-core", "src/poetry/core/__init__.py src/poetry/core/_vendor/x.py", 1),
        ("", "scripts/__init__.py", 1),
        ("p-stubs", "p-stubs/__init__.pyi", 0),
    ],
    ids=["modules", "src siblings", "namespace", "lone helper", "stub-only"],
)
def test_source_files_by_kind(
    tmp_path: Path, name: str, paths: str, expected: int
) -> None:
    """The first *expected* paths are the source files."""
    layout = PackageLayout(tree(tmp_path, paths, project(name) if name else ""))
    assert rel(layout.source_files(), tmp_path) == sorted(paths.split()[:expected])


@pytest.mark.parametrize(
    ("name", "path"),
    [
        ("testing", "testing/__init__.py"),
        ("zope.testing", "src/zope/testing/__init__.py"),
        ("my-vendored", "my/vendored/__init__.py"),
        ("extern", "src/extern/__init__.py"),
        ("build", "src/build/__init__.py"),
    ],
)
def test_directory_the_project_name_points_at_is_never_skipped(
    tmp_path: Path, name: str, path: str
) -> None:
    layout = PackageLayout(tree(tmp_path, path, project(name)))
    assert rel(layout.source_files(), tmp_path) == [path]


@pytest.mark.parametrize("ancestor", ["build", "env", "venv", ".venv", "tests"])
def test_checkout_below_a_pruned_name_still_works(
    tmp_path: Path, ancestor: str
) -> None:
    root = tree(tmp_path / ancestor / "proj", "src/mypkg/__init__.py", project("mypkg"))
    assert rel(PackageLayout(root).source_files(), root) == ["src/mypkg/__init__.py"]


def test_symlinked_package_directories_are_seen(tmp_path: Path) -> None:
    """A package may be a link, in or out of the project. Links in it are skipped."""
    outside = tree(tmp_path / "outside", "__init__.py core.py")
    root = tree(tmp_path / "proj", "code/inner/__init__.py src/.keep")
    link(outside, root / "src" / "shared")
    link(root / "code" / "inner", root / "src" / "inner")
    link(root, root / "code" / "inner" / "loop")
    layout = PackageLayout(root)
    assert rel(layout.package_dirs, root) == ["src/inner", "src/shared"]
    assert rel(layout.source_files(), root) == [
        "src/inner/__init__.py",
        "src/shared/__init__.py",
        "src/shared/core.py",
    ]


def test_source_directory_linked_from_outside_is_ignored(tmp_path: Path) -> None:
    outside = tree(tmp_path / "outside", "evil/__init__.py native/code.c")
    root = tree(tmp_path / "proj", "mypkg/__init__.py")
    link(outside, root / "src")
    layout = PackageLayout(root)
    assert found(layout, root) == ["mypkg package mypkg"]
    assert (layout.has_src_dir, layout.non_python_src_dirs) == (False, [])
    assert layout.src_has_non_python_sources is False


def test_declared_source_directory_linked_from_outside_is_ignored(
    tmp_path: Path,
) -> None:
    outside = tree(tmp_path / "outside", "evil/__init__.py")
    pyproject = project("mypkg", '[tool.maturin]\npython-source = "py"')
    root = tree(tmp_path / "proj", "mypkg/__init__.py", pyproject)
    link(outside, root / "py")
    assert found(PackageLayout(root), root) == ["mypkg package mypkg"]


def test_stub_files(tmp_path: Path) -> None:
    stubs = (
        "src/pkg-stubs/__init__.pyi src/pkg/core.pyi src/pkg/sub/fast.pyi src/one.pyi"
    )
    others = (
        "src/pkg/__init__.py src/pkg/tests/test_core.pyi src/pkg/_vendor/six.pyi "
        "src/one.py src/two.py src/other.pyi"
    )
    layout = PackageLayout(tree(tmp_path, f"{stubs} {others}", project("pkg")))
    assert rel(layout.stub_files(), tmp_path) == sorted(stubs.split())
    assert all(path.suffix == ".py" for path in layout.source_files())


def test_local_names(tmp_path: Path) -> None:
    paths = (
        "src/pkg/__init__.py src/_impl/__init__.py src/ns/sub/__init__.py "
        "src/conftest.py src/_speedups.pyx src/_native.cpython-312-darwin.so "
        "src/stubs_only.pyi src/tiktoken_ext/plugin.py "
        "src/README.md src/yaml/config.yaml src/data-files/x.py"
    )
    pyproject = project("My-Dist", '[tool.setuptools]\npy-modules = ["gone"]')
    names = PackageLayout(tree(tmp_path, paths, pyproject)).local_names()
    expected = "my_dist gone pkg _impl ns conftest _speedups _native stubs_only"
    assert names == {*expected.split(), "tiktoken_ext"}


def test_local_names_without_any_source(tmp_path: Path) -> None:
    """A compiled-only project still imports as its own name."""
    tree(tmp_path, "src/lib.rs x.py", project("My-Ext"))
    assert PackageLayout(tmp_path).local_names() == {"my_ext", "x"}


# ── Non-Python sources and native builds ──────────────────────────────

# Each row holds "project name: files" and what src/ holds: "native: " with
# the non-Python directories in it, or "python:" if it has no such sources.
NATIVE: dict[str, tuple[str, str]] = {
    "C directories beside the package (Pillow)": (
        "pillow: src/PIL/__init__.py src/libImaging/Access.c src/thirdparty/raqm/r.c "
        "src/_imaging.c src/mypkg.egg-info/PKG-INFO",
        "native: src/libImaging src/thirdparty",
    ),
    "C extension and no Python package (ujson)": (
        "ujson: src/ujson/python/u.c src/ujson/lib/dconv.cc",
        "native: src/ujson",
    ),
    "C sources with a stub beside them": (
        "ujson: src/ujson/python/ujson.c src/ujson/__init__.pyi",
        "native: src/ujson",
    ),
    "stub-only package": ("typed: src/typed/__init__.pyi", "python:"),
    "front end beside the package (repo-review)": (
        "repo-review: src/repo_review/__init__.py src/repo-review-app/index.ts "
        "src/repo-review-app/node_modules/node-gyp/gyp/gyp_main.py",
        "native: src/repo-review-app",
    ),
    "loose Rust files (tiktoken)": (
        "tiktoken: src/lib.rs tiktoken/__init__.py",
        "native:",
    ),
    "pure Python": ("mypkg: src/mypkg/__init__.py src/mypkg/data.csv", "python:"),
}


@pytest.mark.parametrize("label", NATIVE)
def test_non_python_sources_in_src(tmp_path: Path, label: str) -> None:
    (name, _, paths), (kind, _, directories) = (r.partition(":") for r in NATIVE[label])
    layout = PackageLayout(tree(tmp_path, paths, project(name)))
    assert rel(layout.non_python_src_dirs, tmp_path) == directories.split()
    assert layout.src_has_non_python_sources is (kind == "native")
    assert layout.has_src_dir is True


CYTHON = 'requires = ["setuptools", "Cython>=3 ; python_version >= \'3.9\'"]'


@pytest.mark.parametrize(
    ("backend", "evidence"),
    [
        (MATURIN, ["build-backend is maturin", "build-system.requires lists maturin"]),
        (
            'requires = ["meson-python", "pybind11>=2"]\nbuild-backend = "mesonpy"',
            [
                "build-backend is mesonpy",
                "build-system.requires lists meson-python",
                "build-system.requires lists pybind11",
            ],
        ),
        (CYTHON, ["build-system.requires lists Cython"]),
        (
            'requires = ["setuptools_rust"]\nbuild-backend = "setuptools.build_meta"',
            ["build-system.requires lists setuptools-rust"],
        ),
        (SETUPTOOLS, []),
        ('requires = ["hatchling"]\nbuild-backend = "hatchling.build"', []),
        ("requires = [42, {a = 1}]\nbuild-backend = 7", []),
        ("", []),
    ],
)
def test_native_build_evidence(
    tmp_path: Path, backend: str, evidence: list[str]
) -> None:
    layout = PackageLayout(tree(tmp_path, "", project("x", backend=backend)))
    assert layout.native_build_evidence == evidence
    assert layout.is_native_build is bool(evidence)


def test_native_build_evidence_outside_build_system(tmp_path: Path) -> None:
    ext = "[tool.setuptools]\next-modules = [{name = 'x', sources = ['x.c']}]"
    setup = SETUP + "setup(cffi_modules=['b:ffi'])\n"
    tree(tmp_path, "CMakeLists.txt", ext, setup_py=setup)
    assert PackageLayout(tmp_path).native_build_evidence == [
        "setup.py passes cffi_modules",
        "[tool.setuptools] ext-modules is set",
        "CMakeLists.txt is present",
    ]


# ── Nothing raises ────────────────────────────────────────────────────


def touch_everything(layout: PackageLayout) -> None:
    """Read every public property and call every public method."""
    assert layout.primary_match in ("name", "declared", "single", "guess", "none")
    assert layout.primary_candidate is None or layout.packages
    assert isinstance(layout.package_dirs + layout.source_files(), list)
    assert isinstance(layout.stub_files() + layout.top_level_modules(), list)
    assert isinstance(layout.local_names(), set)
    assert isinstance(layout.non_python_src_dirs, list)
    assert isinstance(layout.native_build_evidence, list)
    assert layout.has_src_dir in (True, False)
    assert layout.src_has_non_python_sources in (True, False)
    assert layout.is_native_build in (True, False)


def test_missing_directory_or_plain_file(tmp_path: Path) -> None:
    tree(tmp_path, "file.txt")
    for path in (tmp_path / "nope", tmp_path / "file.txt"):
        layout = PackageLayout(path)
        touch_everything(layout)
        assert (layout.packages, layout.primary_match) == ([], "none")


WRONG_VALUES = (
    "[project]\nname = ['a']\n"
    "[tool.setuptools]\npackages = 5\npy-modules = {a = 1}\n"
    '[tool.setuptools.package-dir]\n"" = 3\nx = ["y"]\n'
    "[tool.hatch.build.targets.wheel]\npackages = [1, 2]\nsources = 9\n"
    "[tool.poetry]\npackages = [1, 'x', {include = 4, from = 5}]\n"
    "[tool.maturin]\npython-source = []\nmodule-name = 1\n"
    "[tool.flit.module]\nname = {}\n"
    "[tool.pdm]\npackage-dir = 1\n"
    "[build-system]\nrequires = 'setuptools'\nbuild-backend = []\n"
)


@pytest.mark.parametrize(
    "pyproject",
    [
        "[project\nname = ",
        "\x00\x01\x02",
        'project = "not a table"\ntool = 3\n',
        "[project]\nname = 42\n[tool]\nmaturin = 1\nsetuptools = []\n",
        WRONG_VALUES,
        project("a-" * 400 + "a"),
        project("-".join(f"p{i}" for i in range(40))),
    ],
    ids=["cut off", "binary", "top level", "tables", "values", "long name", "parts"],
)
def test_malformed_configuration_is_read_as_absent(
    tmp_path: Path, pyproject: str
) -> None:
    broken = {"setup_cfg": "not an ini file\n[[[\n", "setup_py": "def broken(:\n"}
    layout = PackageLayout(tree(tmp_path, "src/mypkg/__init__.py", pyproject, **broken))
    touch_everything(layout)
    assert found(layout, tmp_path) == ["mypkg package src/mypkg"]


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="needs a non-root POSIX user",
)
def test_unreadable_directory_is_skipped(tmp_path: Path) -> None:
    tree(tmp_path, "src/mypkg/__init__.py src/mypkg/locked/mod.py")
    locked = tmp_path / "src" / "mypkg" / "locked"
    locked.chmod(0)
    try:
        layout = PackageLayout(tmp_path)
        touch_everything(layout)
        assert rel(layout.source_files(), tmp_path) == ["src/mypkg/__init__.py"]
    finally:
        locked.chmod(0o755)


def test_tree_is_read_once(tmp_path: Path) -> None:
    layout = PackageLayout(tree(tmp_path, "src/mypkg/__init__.py"))
    assert found(layout, tmp_path) == ["mypkg package src/mypkg"]
    tree(tmp_path, "src/later/__init__.py")
    assert found(layout, tmp_path) == ["mypkg package src/mypkg"]
