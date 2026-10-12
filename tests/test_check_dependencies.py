"""Tests for the import collector and the dependencies check."""

import ast
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from pycmdcheck.checks.dependencies import DependenciesCheck
from pycmdcheck.import_scan import collect_imports, scan_imports, third_party
from pycmdcheck.results import CheckResult, CheckStatus

MakePackage = Callable[..., Path]

UNDECLARED = "dependencies.undeclared"
UNUSED = "dependencies.unused"
UNPARSED = "dependencies.unparsed-sources"
# What graded() gives for a result with nothing to report.
OK: tuple[CheckStatus, list[str]] = (CheckStatus.OK, [])
# A pyproject.toml with its dependencies in each table the hints name.
PROJECT = '[project]\nname = "mypackage"\nversion = "0.1.0"\ndependencies = [{}]\n'
POETRY = '[tool.poetry]\nname = "mypackage"\n\n[tool.poetry.dependencies]\n{}'

COLLECTOR_CASES = [
    pytest.param(
        "import a.b\nfrom c.d import e\nfrom . import f\nfrom .g import h\n",
        {"a", "c"},
        set(),
        id="absolute-hard-relative-ignored",
    ),
    pytest.param(
        "from typing import TYPE_CHECKING\nimport typing\n"
        "if TYPE_CHECKING:\n    import numpy\n"
        "if typing.TYPE_CHECKING:\n    from pandas import DataFrame\n",
        {"typing"},
        {"numpy", "pandas"},
        id="type-checking",
    ),
    pytest.param(
        "try:\n    import ujson\nexcept ImportError:\n    import json\n"
        "else:\n    import OpenSSL\n"
        "try:\n    import cupy\nexcept (ModuleNotFoundError, OSError):\n    pass\n",
        set(),
        {"ujson", "json", "OpenSSL", "cupy"},
        id="try-except-import-error",
    ),
    pytest.param(
        "try:\n    import yaml\nexcept KeyError:\n    pass\n",
        {"yaml"},
        set(),
        id="try-without-import-error-is-hard",
    ),
    pytest.param(
        "class A:\n    import attr\n    def run(self):\n        import matplotlib\n"
        "async def go():\n    import trio\n",
        {"attr"},
        {"matplotlib", "trio"},
        id="function-local",
    ),
    pytest.param(
        "import sys\n"
        "if sys.version_info >= (3, 11):\n    import tomllib\n"
        "else:\n    import tomli as tomllib\n"
        "if sys.platform == 'win32':\n    import win32api\n",
        {"sys"},
        {"tomllib", "tomli", "win32api"},
        id="version-and-platform-guards",
    ),
    pytest.param(
        "if DEBUG:\n    import simplejson\n",
        {"simplejson"},
        set(),
        id="plain-if-stays-hard",
    ),
    pytest.param(
        "import importlib\n"
        "yaml = importlib.import_module('yaml.reader')\n"
        "requests = __import__('requests')\n"
        "rel = importlib.import_module('.sibling', __name__)\n"
        "dyn = importlib.import_module(name)\n",
        {"importlib"},
        {"yaml", "requests"},
        id="literal-dynamic-imports-count-as-guarded",
    ),
    pytest.param(
        "try:\n    import simplejson as json\nexcept ImportError:\n    import json\n"
        "if has_simplejson:\n    from simplejson import JSONDecodeError\n"
        "import numpy\n",
        {"numpy"},
        {"simplejson", "json"},
        id="optional-import-repeated-behind-a-flag",
    ),
    pytest.param(
        "import contextlib\nfrom contextlib import suppress\n"
        "with contextlib.suppress(ImportError):\n    import orjson\n"
        "with suppress(ModuleNotFoundError):\n    import uvloop\n"
        "with open(path):\n    import yaml\n",
        {"contextlib", "yaml"},
        {"orjson", "uvloop"},
        id="with-suppress-import-error",
    ),
    pytest.param(
        "if __name__ == '__main__':\n    import fire\n",
        set(),
        {"fire"},
        id="main-block",
    ),
    pytest.param(
        "try:\n    import a\nexcept Exception:\n    pass\n"
        "try:\n    import b\nexcept:\n    pass\n",
        set(),
        {"a", "b"},
        id="broad-and-bare-except",
    ),
    pytest.param(
        "match mode:\n    case 'fast':\n        import orjson\n",
        {"orjson"},
        set(),
        id="match-case-stays-hard",
    ),
]


class TestCollector:
    """Tests for import_scan."""

    @pytest.mark.parametrize(("source", "hard", "guarded"), COLLECTOR_CASES)
    def test_contexts(self, source: str, hard: set[str], guarded: set[str]) -> None:
        assert collect_imports(ast.parse(source)) == (hard, guarded)

    def test_scan_records_files_and_skips(self, tmp_path: Path) -> None:
        good = tmp_path / "good.py"
        good.write_text("import click\ndef f():\n    import rich\n")
        other = tmp_path / "other.py"
        other.write_text("import click\nimport rich\n")
        broken = tmp_path / "broken.py"
        broken.write_text("def f(:\n")

        scan = scan_imports([good, other, broken])

        assert scan.hard == {"click": good, "rich": other}
        assert scan.guarded == {"rich"}
        assert scan.used == {"click", "rich"}
        assert (scan.scanned, scan.skipped) == (2, [broken])

    def test_stdlib_of_any_python_version_is_not_third_party(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Version-guarded stdlib imports do not depend on the interpreter."""
        monkeypatch.setattr(sys, "stdlib_module_names", frozenset({"os"}))
        names = ["os", "tomllib", "distutils", "annotationlib", "__main__"]
        names += ["_typeshed", "mypkg", "numpy"]
        assert third_party(names, {"mypkg"}) == {"numpy"}


@pytest.fixture(autouse=True)
def installed(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[str]]:
    """Replace the environment's import-name to distribution map.

    Empty by default, as in an isolated install of pycmdcheck. A test adds
    entries to pretend a distribution is installed.
    """
    environment: dict[str, list[str]] = {}
    monkeypatch.setattr(
        "importlib.metadata.packages_distributions", lambda: environment
    )
    return environment


def run(path: Path) -> CheckResult:
    return DependenciesCheck().run(path, {})


def graded(result: CheckResult) -> tuple[CheckStatus, list[str]]:
    """Return the status of a result and the codes of its findings."""
    return result.status, [finding.code for finding in result.findings]


def lines(result: CheckResult, prefix: str) -> list[str]:
    return [d for d in result.details if d.startswith(prefix)]


class TestDeclaredSet:
    """Reading the declared dependencies."""

    @pytest.mark.parametrize(
        ("pyproject", "summary", "reason"),
        [
            (False, "No pyproject.toml found", "prerequisite-failed"),
            (
                '[build-system]\nrequires = ["setuptools"]\n',
                "No [project] table",
                "prerequisite-failed",
            ),
            ("[project\n", "pyproject.toml could not be parsed", "prerequisite-failed"),
            (
                '[project]\nname = "mypkg"\ndynamic = ["dependencies"]\n',
                "Dependencies are declared dynamically",
                "not-applicable",
            ),
            (
                '[project]\nname = "mypkg"\ndependencies = ["requests"]\n'
                'dynamic = ["optional-dependencies"]\n',
                "Dependencies are declared dynamically",
                "not-applicable",
            ),
        ],
    )
    def test_unknown_declared_set_is_skipped(
        self,
        make_package: MakePackage,
        pyproject: str | bool,
        summary: str,
        reason: str,
    ) -> None:
        """Dynamic or setup.py dependencies cannot be audited, so nothing is flagged."""
        root = make_package(
            "mypkg",
            pyproject=pyproject,
            init="import numpy\n",
            files={"setup.py": "from setuptools import setup\nsetup()\n"},
        )
        result = run(root)
        assert (result.status, result.skip_reason) == (CheckStatus.SKIPPED, reason)
        assert result.message == f"{summary}, skipping dependency audit"
        assert result.findings == []
        # What the metadata check reports is pointed at, not reported again.
        points_at_owner = "metadata check" in result.details[-1]
        assert points_at_owner == (reason == "prerequisite-failed")

    @pytest.mark.parametrize(
        ("requirement", "module"),
        [
            ("click>=8.0", "click"),
            ("requests (>=2.31,<3.0)", "requests"),
            ("mylib @ git+https://example.com/mylib.git", "mylib"),
            ("rich [jupyter] >= 13 ; os_name == 'nt'", "rich"),
            ("numpy>=1.0.*", "numpy"),
        ],
    )
    def test_requirement_forms(
        self, make_package: MakePackage, requirement: str, module: str
    ) -> None:
        """Every PEP 508 spelling declares its name, valid specifier or not."""
        root = make_package(
            dependencies=[requirement], init=f"from {module} import x\n"
        )
        result = run(root)
        assert graded(result) == OK
        assert result.message == "All dependencies match imports"
        assert not lines(result, "Undeclared") and not lines(result, "Unused")

    def test_malformed_entries_are_named_not_fatal(
        self, make_package: MakePackage
    ) -> None:
        root = make_package(
            project='dependencies = ["", "click", {name = "x"}, "numpy>=1.0.*"]',
            init="import click\nimport numpy\n",
        )
        result = run(root)
        assert graded(result) == OK
        assert lines(result, "Not valid requirements, matched by name only") == [
            "Not valid requirements, matched by name only: "
            "'', {'name': 'x'}, 'numpy>=1.0.*'"
        ]

    def test_no_dependencies_declared(self, make_package: MakePackage) -> None:
        result = run(make_package(init="import os\n"))
        assert graded(result) == OK
        assert result.message == "No dependencies declared"

    def test_no_dependencies_with_third_party_import_warns(
        self, make_package: MakePackage
    ) -> None:
        result = run(make_package(init="import os\nimport numpy\n"))
        assert graded(result) == (CheckStatus.WARNING, [UNDECLARED])
        assert result.details[0] == "Undeclared imports: numpy"

    def test_extras_and_dev_tables_explain_an_import_but_are_never_unused(
        self, make_package: MakePackage
    ) -> None:
        root = make_package(
            dependencies=["click>=8.0"],
            init="import click\nimport rich\nimport pytest\nimport nox\n",
            project='[project.optional-dependencies]\npretty = ["rich>=13.0"]\n',
            omit=["urls"],
            extra=(
                '[dependency-groups]\ndev = ["pytest>=8", "ruff"]\n\n'
                '[tool.hatch.envs.default]\ndependencies = ["nox"]\n'
            ),
        )
        result = run(root)
        assert graded(result) == OK
        assert result.details == ["All 1 dependencies verified"]

    @pytest.mark.parametrize(
        ("dependencies", "unbounded"),
        [
            (["click>=8.0"], None),
            (["requests"], "requests"),
            (["click>=8.0", "requests", "httpx[http2]"], "httpx, requests"),
            (["click (>=8.0)", "requests @ https://example.com/r.whl"], None),
        ],
    )
    def test_unbounded_detail(
        self, make_package: MakePackage, dependencies: list[str], unbounded: str | None
    ) -> None:
        """Dependencies with neither a specifier nor a URL get a detail line.

        The line is information only. It is not a finding.
        """
        root = make_package(dependencies=dependencies, init="import click\n")
        result = run(root)
        found = [d for d in result.details if "unbounded" in d.lower()]
        expected = f"Unbounded dependencies (no version specifier): {unbounded}"
        assert found == ([expected] if unbounded else [])
        assert not [f for f in result.findings if "nbounded" in f.message]


class TestNameMapping:
    """Mapping distribution names to import names."""

    @pytest.mark.parametrize(
        ("distribution", "module", "environment"),
        [
            ("PyYAML", "yaml", {}),
            ("attrs", "attrs", {}),
            ("attrs", "attr", {}),
            ("ipython", "IPython", {}),
            ("netCDF4", "netCDF4", {}),
            ("zope.interface", "zope", {}),
            ("rpds-py", "rpds", {}),
            ("typing-extensions", "typing_extensions", {}),
            ("nonexistent-declared-dep-xyz", "nonexistent_declared_dep_xyz", {}),
            ("pytest", "_pytest", {"_pytest": ["pytest"], "py": ["pytest"]}),
            ("Some_Dist", "weird", {"weird": ["some-dist"], "_w": ["some-dist"]}),
        ],
    )
    def test_distribution_to_import_name(
        self,
        make_package: MakePackage,
        installed: dict[str, list[str]],
        distribution: str,
        module: str,
        environment: dict[str, list[str]],
    ) -> None:
        installed.update(environment)
        root = make_package(
            dependencies=[f"{distribution}>=1"], init=f"import {module}\n"
        )
        result = run(root)
        assert graded(result) == OK
        assert result.details == ["All 1 dependencies verified"]

    def test_installed_metadata_replaces_the_prefix_guess(
        self, make_package: MakePackage, installed: dict[str, list[str]]
    ) -> None:
        """A name prefix is only a guess for a distribution that is not installed."""
        root = make_package(dependencies=["pytest-cov>=5"], init="import pytest\n")
        assert graded(run(root)) == OK

        installed["pytest_cov"] = ["pytest-cov"]
        result = run(root)
        assert graded(result) == (CheckStatus.WARNING, [UNDECLARED, UNUSED])
        assert result.details[0] == "Undeclared imports: pytest"

    def test_broken_environment_metadata_does_not_fail_the_check(
        self, make_package: MakePackage, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The names are then guessed, as for an environment with nothing in it."""

        def broken() -> dict[str, list[str]]:
            raise OSError("metadata of the environment cannot be read")

        monkeypatch.setattr("importlib.metadata.packages_distributions", broken)
        root = make_package(dependencies=["pyyaml>=6"], init="import yaml\n")
        assert graded(run(root)) == OK

    def test_stub_distribution_does_not_declare_the_runtime_package(
        self, make_package: MakePackage
    ) -> None:
        root = make_package(
            init="import pandas\n",
            extra='[dependency-groups]\ndev = ["pandas-stubs"]\n',
        )
        result = run(root)
        assert graded(result) == (CheckStatus.WARNING, [UNDECLARED])
        assert result.details[0] == "Undeclared imports: pandas"

    def test_private_extension_of_a_native_build_is_not_flagged(
        self, make_package: MakePackage
    ) -> None:
        """A module such as _speedups may only exist once the project is built."""
        root = make_package(
            init="import _speedups\nimport numpy\n",
            files={"setup.py": "from setuptools import setup\nsetup(ext_modules=[])\n"},
        )
        assert run(root).details[:3] == [
            "Undeclared imports: numpy",
            "First imported in: numpy (src/mypackage/__init__.py)",
            "Guarded or optional imports not declared (not flagged): _speedups",
        ]

    def test_version_guarded_tomli_is_neither_undeclared_nor_unused(
        self,
        make_package: MakePackage,
        installed: dict[str, list[str]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Regression: the tomllib fallback with a mypyc-compiled tomli installed."""
        installed.update({"c787785866c7b801238d__mypyc": ["tomli"], "tomli": ["tomli"]})
        older = sys.stdlib_module_names - {"tomllib"}
        monkeypatch.setattr(sys, "stdlib_module_names", older)
        root = make_package(
            dependencies=["tomli>=2.0; python_version < '3.11'"],
            init=(
                "import sys\n"
                "if sys.version_info >= (3, 11):\n    import tomllib\n"
                "else:\n    import tomli as tomllib\n"
            ),
        )
        result = run(root)
        assert graded(result) == OK
        assert result.details == ["All 1 dependencies verified"]


class TestAudit:
    """Comparing declared dependencies with the imports found."""

    def test_rules_are_the_three_the_check_can_report(self) -> None:
        assert set(DependenciesCheck.rules) == {UNDECLARED, UNUSED, UNPARSED}

    def test_unused_dependency(self, make_package: MakePackage) -> None:
        root = make_package(dependencies=["click", "rich>=13"], init="import click\n")
        result = run(root)
        assert graded(result) == (CheckStatus.NOTE, [UNUSED])
        assert result.message == "Found 1 unused declared dependency(ies)"
        assert result.details[0] == "Unused dependencies: rich"

    def test_undeclared_import(self, make_package: MakePackage) -> None:
        """Both rules can fire at once. The file that imports the name is given."""
        root = make_package(
            dependencies=["requests>=2"], init="from click import echo\n"
        )
        result = run(root)
        assert graded(result) == (CheckStatus.WARNING, [UNDECLARED, UNUSED])
        assert result.message == "Found 1 undeclared import(s)"
        assert result.details[:3] == [
            "Undeclared imports: click",
            "First imported in: click (src/mypackage/__init__.py)",
            "Unused dependencies: requests",
        ]
        # The check sets the hints, which name the table the project uses.
        assert [finding.to_dict() for finding in result.findings] == [
            {
                "code": UNDECLARED,
                "status": "warning",
                "message": "Undeclared imports: click",
                "hint": DependenciesCheck.hints[UNDECLARED],
            },
            {
                "code": UNUSED,
                "status": "note",
                "message": "Unused dependencies: requests",
                "hint": DependenciesCheck.hints[UNUSED],
            },
        ]

    def test_guarded_imports_are_optional_not_undeclared(
        self, make_package: MakePackage
    ) -> None:
        """Guarded imports are listed once and still count as use."""
        root = make_package(
            dependencies=["keyring>=24", "pyyaml>=6"],
            init=(
                "import importlib\nfrom typing import TYPE_CHECKING\n"
                "if TYPE_CHECKING:\n    import pandas\n"
                "try:\n    import ujson\nexcept ImportError:\n    ujson = None\n"
                "def plot():\n    import matplotlib\n    import keyring\n"
                "yaml = importlib.import_module('yaml')\n"
            ),
        )
        result = run(root)
        assert graded(result) == OK
        assert result.details == [
            "Guarded or optional imports not declared (not flagged): "
            "matplotlib, pandas, ujson",
            "All 2 dependencies verified",
        ]

    def test_first_party_names_are_not_undeclared(
        self, make_package: MakePackage
    ) -> None:
        root = make_package(
            "mainpkg",
            init="import _helper\nimport compat\nimport ns.sub\nimport mainpkg.core\n",
            files={
                "src/_helper.py": "",
                "src/compat.py": "",
                "src/ns/sub/__init__.py": "from ns.sub import core\n",
            },
        )
        result = run(root)
        assert graded(result) == OK
        assert not lines(result, "Undeclared")

    def test_tests_vendored_code_and_tooling_are_not_runtime_code(
        self, make_package: MakePackage
    ) -> None:
        """Only runtime files can make an import undeclared.

        A dependency used only by tests shipped in the package is still used.
        """
        root = make_package(
            "mypkg",
            dependencies=["numpy>=1"],
            init="",
            files={
                "src/mypkg/conftest.py": "import pytest\n",
                "src/mypkg/test_core.py": "import hypothesis\n",
                "src/mypkg/testing/__init__.py": "import numpy\n",
                "src/mypkg/_vendor/toml.py": "import six\n",
                "docs/conf.py": "import sphinx\n",
                "noxfile.py": "import nox\n",
            },
        )
        result = run(root)
        assert graded(result) == OK
        assert result.details == ["All 1 dependencies verified"]

    def test_single_module_source_scanned(self, make_package: MakePackage) -> None:
        """A single-module package's imports are scanned, not all deps unused."""
        root = make_package(
            dependencies=["click>=8.0"], layout="module", init="import click\n"
        )
        result = run(root)
        assert graded(result) == OK
        assert not lines(result, "Unused")

    def test_unparsed_file_keeps_status_and_drops_unused(
        self, make_package: MakePackage
    ) -> None:
        root = make_package(
            dependencies=["click>=8", "rich>=13"],
            init="import click\n",
            files={"src/mypackage/new.py": "def f(:\n"},
        )
        result = run(root)
        assert graded(result) == OK
        assert not lines(result, "Unused")
        assert len(lines(result, "Could not parse 1 file(s), skipped: src/")) == 1

    def test_long_lists_say_how_many_were_left_out(
        self, make_package: MakePackage
    ) -> None:
        init = "".join(f"import lib{number:02d}\n" for number in range(12))
        result = run(make_package(init=init))
        assert graded(result) == (CheckStatus.WARNING, [UNDECLARED])
        assert result.message == "Found 12 undeclared import(s)"
        assert result.details[0].endswith("lib08, lib09 (+2 more)")
        assert result.details[1].endswith("lib09 (src/mypackage/__init__.py)")

    @pytest.mark.parametrize("dependencies", [(), ("click",)])
    def test_no_source_is_skipped(
        self, make_package: MakePackage, dependencies: tuple[str, ...]
    ) -> None:
        """Without source nothing is compared, whatever is declared."""
        root = make_package(layout="none", tests=False, dependencies=dependencies)
        result = run(root)
        assert (result.status, result.skip_reason) == (
            CheckStatus.SKIPPED,
            "not-applicable",
        )
        assert result.message == (
            "Could not locate package source; skipping dependency audit"
        )
        assert result.details == [
            "Found no package or module under src/ or the project root"
        ]
        assert result.findings == []

    def test_source_that_cannot_be_parsed_is_a_note(
        self, make_package: MakePackage
    ) -> None:
        """Source was found, so this is not the skip for a project without any."""
        root = make_package(dependencies=["click"], init="def f(:\n")
        result = run(root)
        assert graded(result) == (CheckStatus.NOTE, [UNPARSED])
        assert result.skip_reason is None
        assert result.message == (
            "Could not parse package source; skipping dependency audit"
        )
        assert result.findings[0].message == "No source file could be parsed"
        (detail,) = result.details
        assert detail.startswith(
            "Could not parse 1 file(s), skipped: src/mypackage/__init__.py (syntax"
        )

    @pytest.mark.parametrize(
        ("code", "path", "before", "after"),
        [
            (
                UNDECLARED,
                "pyproject.toml",
                PROJECT.format(""),
                PROJECT.format('"pyyaml"'),
            ),
            (
                UNDECLARED,
                "pyproject.toml",
                POETRY.format(""),
                POETRY.format('pyyaml = "*"\n'),
            ),
            (
                UNUSED,
                "pyproject.toml",
                PROJECT.format('"pyyaml", "rich"'),
                PROJECT.format('"pyyaml"'),
            ),
            (
                UNUSED,
                "pyproject.toml",
                POETRY.format('pyyaml = "*"\nrich = "*"\n'),
                POETRY.format('pyyaml = "*"\n'),
            ),
            (
                UNPARSED,
                "src/mypackage/__init__.py",
                "import yaml\ndef f(:\n",
                "import yaml\n",
            ),
        ],
        ids=["undeclared", "undeclared-poetry", "unused", "unused-poetry", "unparsed"],
    )
    def test_following_the_hint_clears_the_code(
        self, make_package: MakePackage, code: str, path: str, before: str, after: str
    ) -> None:
        """A healthy project is broken in one file, then mended as the hint says."""
        root = make_package(dependencies=["pyyaml"], init="import yaml\n")
        (root / path).write_text(before)
        result = run(root)
        assert graded(result)[1] == [code]
        if code != UNPARSED:
            # The hint names the one table that this project keeps the list in.
            hint = result.findings[0].hint or ""
            poetry = "[tool.poetry.dependencies]"
            named = poetry if before.startswith("[tool.poetry]") else "[project]"
            assert f"{named} of pyproject.toml." in hint
            assert (poetry in hint) == (named == poetry)
        (root / path).write_text(after)
        assert graded(run(root)) == OK

    def test_hint_names_project_when_both_tables_list_dependencies(
        self, make_package: MakePackage
    ) -> None:
        """Poetry's table is not read once [project] lists dependencies."""
        root = make_package(dependencies=["rich"], init="import rich\nimport yaml\n")
        with (root / "pyproject.toml").open("a") as file:
            file.write('\n[tool.poetry.dependencies]\nrich = "*"\n')
        (finding,) = run(root).findings
        assert finding.hint == DependenciesCheck.hints[UNDECLARED]
        assert "[tool.poetry" not in (finding.hint or "")
