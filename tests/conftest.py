"""Pytest configuration and fixtures.

Besides the package fixtures, this file keeps the suite independent of the
machine it runs on:

* No test reaches the network. ``urllib`` requests get a canned HTTP 200
  (opt out with ``@pytest.mark.real_urlopen``) and a socket may only connect
  to the local machine.
* Terminal width is fixed at 80 columns.
* ``@pytest.mark.tools("ruff", ...)`` marks a test that runs real external
  tools. It is skipped when one of them is not installed.
* ``repo_root`` gives the source checkout and skips when the tests run
  against an installed copy.

``options_read`` scans the source of a check for the options it reads.
``tool`` replaces the tool seam of a check that runs an external tool.
"""

import ast
import importlib.util
import ipaddress
import shutil
import socket
import urllib.request
from collections.abc import Callable, Collection, Mapping, Sequence
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from packaging.requirements import Requirement

import pycmdcheck.cli
from pycmdcheck.ast_cache import clear_cache as clear_ast_cache
from pycmdcheck.profiles import ALL_CHECKS, DEPRECATED_CHECKS
from pycmdcheck.pyproject_reader import clear_cache
from pycmdcheck.subprocess_runner import SubprocessResult

# pytest shows the values of a failed assert only in test files, in this file
# and in modules named here. This has to run before the first import of
# tests/helpers.py.
pytest.register_assert_rewrite("tests.helpers")

from tests.helpers import write_files  # noqa: E402


def pytest_configure(config: pytest.Config) -> None:
    """Register the markers."""
    config.addinivalue_line(
        "markers",
        "tools(*names): the test runs real external tools (ruff, mypy, pytest, "
        "black, build, ...) and is skipped when one of them is not installed. "
        "Select with `-m tools`, leave out with `-m 'not tools'`.",
    )
    config.addinivalue_line(
        "markers",
        "real_urlopen: do not stub urllib for this test. It may then talk to a "
        "server on the local machine only.",
    )


def _tool_available(name: str) -> bool:
    """Return whether an external tool can be run from this environment."""
    if name == "build":
        # The build check runs ``python -m build``, so look for the module.
        try:
            return importlib.util.find_spec("build.__main__") is not None
        except (ImportError, ValueError):
            return False
    return shutil.which(name) is not None


def pytest_runtest_setup(item: pytest.Item) -> None:
    """Skip a ``tools``-marked test when a tool it names is not installed."""
    for marker in item.iter_markers(name="tools"):
        missing = [name for name in marker.args if not _tool_available(name)]
        if missing:
            pytest.skip(f"needs {', '.join(missing)} installed")


def _build_check_registry() -> dict[str, type]:
    """Build a registry of all built-in check classes.

    This mirrors what ``discover_checks()`` would return if the package
    were installed and entry points were registered.  By importing the
    classes directly we avoid the need for a ``pip install -e .`` step
    in the test environment.

    The names come from the rosters in ``pycmdcheck.profiles``, so the
    registry cannot fall out of step with them. They are sorted, as
    ``discover_checks()`` returns them, because this is the order the checks
    run in when none is named. A deprecated check is still registered.
    """
    registry: dict[str, type] = {}
    for name in sorted(ALL_CHECKS | DEPRECATED_CHECKS):
        # Each check is the one class with that name in the module of that name.
        module = importlib.import_module(f"pycmdcheck.checks.{name}")
        (registry[name],) = (
            cls
            for cls in vars(module).values()
            if isinstance(cls, type) and getattr(cls, "name", None) == name
        )
    return registry


# Built once, when pytest starts. Each test gets its own copy.
_CHECK_REGISTRY = _build_check_registry()


@pytest.fixture(autouse=True)
def _clear_pyproject_cache():
    """Clear the pyproject_reader cache before each test."""
    clear_cache()
    yield
    clear_cache()


@pytest.fixture(autouse=True)
def _clear_ast_cache():
    """Clear the AST parse cache before each test."""
    clear_ast_cache()
    yield
    clear_ast_cache()


# ---------------------------------------------------------------------------
# Environment isolation
# ---------------------------------------------------------------------------


class NetworkBlockedError(RuntimeError):
    """A test tried to reach a host other than the local machine."""


class _StubResponse:
    """Stand-in for the response urllib returns for a successful request."""

    status = 200
    code = 200
    reason = "OK"

    def __init__(self, url: str) -> None:
        self.url = url
        self.headers: dict[str, str] = {}

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False

    def read(self, amt: int | None = None) -> bytes:
        return b""

    def getcode(self) -> int:
        return self.status

    def geturl(self) -> str:
        return self.url

    def info(self) -> dict[str, str]:
        return self.headers

    def close(self) -> None:
        return None


def _stub_response(target: Any) -> _StubResponse:
    return _StubResponse(getattr(target, "full_url", None) or str(target))


def _is_local_host(host: object) -> bool:
    """Return whether *host* names the local machine."""
    if host is None or host == "" or host == b"":
        return True
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    if not isinstance(host, str):
        return False
    if host.lower() in ("localhost", "localhost.localdomain", "ip6-localhost"):
        return True
    try:
        return ipaddress.ip_address(host.split("%")[0]).is_loopback
    except ValueError:
        return False


@pytest.fixture(autouse=True)
def _no_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """Keep every test off the network.

    The ``urls`` check (and anything else that goes through ``urllib``) gets
    an HTTP 200 without a request being made. A test that patches ``urlopen``
    itself simply overrides this. Mark a test ``real_urlopen`` to use the real
    ``urllib`` against a server on the local machine.

    Underneath that, name lookups and connections to any host other than the
    local machine raise :class:`NetworkBlockedError`.
    """
    if request.node.get_closest_marker("real_urlopen") is None:
        monkeypatch.setattr(
            urllib.request, "urlopen", lambda url, *a, **k: _stub_response(url)
        )
        monkeypatch.setattr(
            urllib.request.OpenerDirector,
            "open",
            lambda self, fullurl, *a, **k: _stub_response(fullurl),
        )

    real_getaddrinfo = socket.getaddrinfo
    real_connect = socket.socket.connect
    real_connect_ex = socket.socket.connect_ex

    def check(address: object) -> None:
        host = address[0] if isinstance(address, tuple) and address else None
        if isinstance(address, tuple) and not _is_local_host(host):
            raise NetworkBlockedError(
                f"test tried to connect to {host!r}; tests must not use the network"
            )

    def getaddrinfo(host: object, *args: Any, **kwargs: Any) -> Any:
        if not _is_local_host(host):
            raise NetworkBlockedError(
                f"test tried to look up {host!r}; tests must not use the network"
            )
        return real_getaddrinfo(host, *args, **kwargs)

    def connect(self: socket.socket, address: Any) -> None:
        check(address)
        return real_connect(self, address)

    def connect_ex(self: socket.socket, address: Any) -> int:
        check(address)
        return real_connect_ex(self, address)

    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect_ex)


@pytest.fixture(autouse=True)
def _fixed_terminal_width(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the width Rich renders at, whatever terminal runs the tests.

    80 columns is what Rich falls back to when output is captured, so this
    matches a CI run. The console of the command line read its width when it
    was imported, so its width is set too. A console that a test builds must
    not be told to write colour by the machine either.
    """
    monkeypatch.setenv("COLUMNS", "80")
    monkeypatch.delenv("FORCE_COLOR", raising=False)
    monkeypatch.setattr(pycmdcheck.cli.console, "width", 80)


@pytest.fixture
def repo_root() -> Path:
    """Return the pycmdcheck source checkout the tests belong to.

    Skips the test when there is none, for example when only ``tests/`` was
    copied next to an installed wheel.
    """
    root = Path(__file__).resolve().parent.parent
    if not (root / "pyproject.toml").is_file() or not (root / "src").is_dir():
        pytest.skip("needs the pycmdcheck source checkout")
    return root


@pytest.fixture(autouse=True)
def _mock_discover_checks():
    """Automatically mock discover_checks so runner tests work without install.

    Entry points are only available when a package is installed via pip.
    In a development / CI environment the package may just have ``src/``
    on ``sys.path`` (via ``pythonpath`` in pyproject.toml).  This fixture
    patches ``discover_checks`` (and its companion ``list_available_checks``)
    so every test sees the full built-in check registry.
    """
    registry = dict(_CHECK_REGISTRY)

    descriptions = []
    for name, cls in sorted(registry.items()):
        desc = getattr(cls, "description", "No description")
        descriptions.append((name, desc))

    with (
        patch("pycmdcheck.runner.discover_checks", return_value=registry),
        patch("pycmdcheck.discovery.discover_checks", return_value=registry),
        patch(
            "pycmdcheck.discovery.list_available_checks",
            return_value=descriptions,
        ),
        patch("pycmdcheck.cli.list_available_checks", return_value=descriptions),
    ):
        yield


# ---------------------------------------------------------------------------
# Options read from the source of a check
# ---------------------------------------------------------------------------


def _options_read(source: str) -> tuple[set[str], list[int]]:
    """Return the option names that *source* reads from ``config``.

    A read is ``config.get("name")``, ``config["name"]``, or a helper called
    as ``helper(config, "name")``. The second value holds the line of every
    other use of ``config``, where the scan cannot tell what is read.
    """
    tree = ast.parse(source)
    uses = {
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id == "config"
    }
    names: set[str] = set()
    for node in ast.walk(tree):
        # Each pair is something that may be config, and the name it is asked for.
        pairs: list[tuple[ast.AST, ast.AST]] = []
        if isinstance(node, ast.Subscript):
            pairs.append((node.value, node.slice))
        elif isinstance(node, ast.Call):
            if isinstance(node.func, ast.Attribute) and node.func.attr == "get":
                pairs += [(node.func.value, key) for key in node.args[:1]]
            pairs += [(node.args[0], key) for key in node.args[1:2]]
        for owner, key in pairs:
            named = isinstance(key, ast.Constant) and isinstance(key.value, str)
            if owner in uses and named:
                names.add(key.value)
                uses.discard(owner)
    return names, sorted(node.lineno for node in uses)


@pytest.fixture
def options_read() -> Callable[[str], tuple[set[str], list[int]]]:
    """Return the scan for the options that the source of a check reads."""
    return _options_read


# ---------------------------------------------------------------------------
# Checks that run a tool
# ---------------------------------------------------------------------------


@pytest.fixture
def tool(
    request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch
) -> Callable[..., MagicMock]:
    """Replace the tool seam of the check and return the ``run_tool`` mock.

    The test module names the module of its check in ``MODULE``. The stand-in
    for ``describe_tool`` is a mock too, so a test can read its calls from
    that module.
    """
    module = getattr(request.module, "MODULE", None)
    if module is None:
        pytest.fail(
            f"{request.module.__name__} uses the tool fixture, so it must set "
            'MODULE to the module of its check, like "pycmdcheck.checks.linting"',
            pytrace=False,
        )

    def install(
        result: SubprocessResult | None = None, *, available: bool = True
    ) -> MagicMock:
        run = MagicMock(return_value=result or SubprocessResult(returncode=0))
        describe = MagicMock(side_effect=lambda name, **kw: f"{name} 9.9")
        monkeypatch.setattr(f"{module}.tool_available", lambda name: available)
        monkeypatch.setattr(f"{module}.describe_tool", describe)
        monkeypatch.setattr(f"{module}.run_tool", run)
        return run

    return install


# ---------------------------------------------------------------------------
# Package fixtures
# ---------------------------------------------------------------------------

_README = """# My Package

A test package for pycmdcheck testing. It exists so that the checks have a
small but complete project to look at, with a description, an install line
and a usage example, and enough words to pass the length heuristic that the
documentation check applies to a README file.

## Installation

```bash
pip install mypackage
```

## Usage

```python
import mypackage
```
"""

_MIT_LICENSE = """MIT License

Copyright (c) 2024 Test

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
"""

_BACKENDS: dict[str, tuple[str, str]] = {
    "hatchling": ("hatchling", "hatchling.build"),
    "setuptools": ("setuptools>=61", "setuptools.build_meta"),
    "flit": ("flit_core>=3.2", "flit_core.buildapi"),
    "pdm": ("pdm-backend", "pdm.backend"),
    "poetry": ("poetry-core>=1.0", "poetry.core.masonry.api"),
}


def _project_toml(
    name: str,
    version: str | None,
    backend: str | None,
    requires_python: str | None,
    dependencies: Sequence[str],
    project: str,
    extra: str,
    omit: Collection[str],
) -> str:
    """Render the pyproject.toml that ``make_package`` writes by default."""
    lines: list[str] = []

    def field(key: str, text: str) -> None:
        if key not in omit:
            lines.append(text)

    if backend == "poetry":
        # Poetry before 2.0: metadata under [tool.poetry], no [project] table.
        lines.append("[tool.poetry]")
        lines.append(f'name = "{name}"')
        if version is not None:
            field("version", f'version = "{version}"')
        field("description", 'description = "A test package"')
        field("readme", 'readme = "README.md"')
        field("license", 'license = "MIT"')
        field("authors", 'authors = ["Test Author <test@example.com>"]')
        field("classifiers", 'classifiers = ["Development Status :: 3 - Alpha"]')
        field("urls", 'homepage = "https://example.com"')
        if project:
            lines.append(project.rstrip("\n"))
        lines += ["", "[tool.poetry.dependencies]"]
        if requires_python is not None and "requires-python" not in omit:
            lines.append(f'python = "{requires_python}"')
        for dependency in dependencies:
            requirement = Requirement(dependency)
            lines.append(f'{requirement.name} = "{requirement.specifier or "*"}"')
    else:
        lines.append("[project]")
        lines.append(f'name = "{name}"')
        if version is not None:
            field("version", f'version = "{version}"')
        field("description", 'description = "A test package"')
        field("readme", 'readme = "README.md"')
        field("license", 'license = "MIT"')
        if requires_python is not None:
            field("requires-python", f'requires-python = "{requires_python}"')
        field(
            "authors",
            'authors = [{name = "Test Author", email = "test@example.com"}]',
        )
        field("classifiers", 'classifiers = ["Development Status :: 3 - Alpha"]')
        if dependencies:
            quoted = ", ".join(f'"{dependency}"' for dependency in dependencies)
            lines.append(f"dependencies = [{quoted}]")
        if project:
            lines.append(project.rstrip("\n"))
        if "urls" not in omit:
            lines += ["", "[project.urls]", 'Homepage = "https://example.com"']

    if backend is not None:
        requires, build_backend = _BACKENDS[backend]
        lines += [
            "",
            "[build-system]",
            f'requires = ["{requires}"]',
            f'build-backend = "{build_backend}"',
        ]
    if extra:
        lines += ["", extra.rstrip("\n")]
    return "\n".join(lines) + "\n"


@pytest.fixture
def make_package(tmp_path_factory: pytest.TempPathFactory) -> Callable[..., Path]:
    """Return a factory that writes a small project and returns its directory.

    Called with no arguments it builds a complete project much like the
    ``temp_package`` fixture: src layout, hatchling, full ``[project]``
    metadata, README, MIT LICENSE and a ``tests/`` directory. Each call makes
    a new directory, so one test can build several projects.

    Arguments of the factory (all but *name* are keyword-only):

    * ``name``: project name. The import name replaces ``-`` and ``.`` by ``_``.
    * ``version``: version string, or ``None`` to declare none.
    * ``layout``: ``"src"``, ``"flat"``, ``"module"`` (a single ``name.py``)
      or ``"none"`` (no source at all).
    * ``backend``: ``"hatchling"``, ``"setuptools"``, ``"flit"``, ``"pdm"``,
      ``"poetry"`` (metadata in ``[tool.poetry]``, no ``[project]`` table) or
      ``None`` for no ``[build-system]`` table.
    * ``requires_python``: specifier, or ``None`` to declare none.
    * ``dependencies``: PEP 508 strings for the runtime dependencies.
    * ``omit``: default metadata keys to leave out, from ``"version"``,
      ``"description"``, ``"readme"``, ``"license"``, ``"requires-python"``,
      ``"authors"``, ``"classifiers"`` and ``"urls"``.
    * ``project``: TOML lines added inside the metadata table.
    * ``extra``: TOML text added at the end of the file (other tables).
    * ``pyproject``: text or bytes to write as ``pyproject.toml`` verbatim,
      or ``False`` to write no such file. The default builds it from the
      arguments above.
    * ``init``: content of ``__init__.py`` (or of the module for
      ``layout="module"``).
    * ``readme``, ``license``, ``tests``: set to ``False`` to leave out the
      README, the LICENSE or the ``tests/`` directory.
    * ``files``: extra files as ``{relative path: text or bytes}``, written
      last so they can replace a default file. A value of ``None`` deletes
      the file.
    """

    def _make(
        name: str = "mypackage",
        *,
        version: str | None = "0.1.0",
        layout: str = "src",
        backend: str | None = "hatchling",
        requires_python: str | None = ">=3.10",
        dependencies: Sequence[str] = (),
        omit: Collection[str] = (),
        project: str = "",
        extra: str = "",
        pyproject: str | bytes | bool = True,
        init: str | None = None,
        readme: bool = True,
        license: bool = True,
        tests: bool = True,
        files: Mapping[str, str | bytes | None] | None = None,
    ) -> Path:
        if layout not in ("src", "flat", "module", "none"):
            raise ValueError(f"unknown layout: {layout!r}")
        if backend is not None and backend not in _BACKENDS:
            raise ValueError(f"unknown backend: {backend!r}")

        import_name = name.replace("-", "_").replace(".", "_")
        root = tmp_path_factory.mktemp(import_name)
        written: dict[str, str | bytes | None] = {}

        if init is None:
            init = f'"""{name}."""\n'
            if version is not None:
                init += f'\n__version__ = "{version}"\n'
        if layout == "src":
            written[f"src/{import_name}/__init__.py"] = init
        elif layout == "flat":
            written[f"{import_name}/__init__.py"] = init
        elif layout == "module":
            written[f"{import_name}.py"] = init

        if pyproject is True:
            written["pyproject.toml"] = _project_toml(
                name,
                version,
                backend,
                requires_python,
                dependencies,
                project,
                extra,
                omit,
            )
        elif pyproject is not False:
            written["pyproject.toml"] = pyproject

        if readme:
            written["README.md"] = _README
        if license:
            written["LICENSE"] = _MIT_LICENSE
        if tests:
            written["tests/__init__.py"] = ""
            written[f"tests/test_{import_name}.py"] = (
                f'"""Tests for {name}."""\n\n\n'
                "def test_import():\n"
                f'    """The package can be imported."""\n'
                f"    import {import_name}  # noqa: F401\n"
            )
        written.update(files or {})
        return write_files(root, written)

    return _make


@pytest.fixture
def temp_package(tmp_path: Path) -> Path:
    """Create a minimal valid Python package structure."""
    # Create src layout
    pkg_dir = tmp_path / "src" / "mypackage"
    pkg_dir.mkdir(parents=True)

    # Create __init__.py
    (pkg_dir / "__init__.py").write_text('"""My package."""\n\n__version__ = "0.1.0"\n')

    # Create pyproject.toml
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "mypackage"
version = "0.1.0"
description = "A test package"
readme = "README.md"
license = "MIT"
requires-python = ">=3.10"
authors = [{name = "Test Author", email = "test@example.com"}]
classifiers = ["Development Status :: 3 - Alpha"]

[project.urls]
Homepage = "https://example.com"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")

    # Create README
    readme = tmp_path / "README.md"
    readme.write_text("""# My Package

A test package for pycmdcheck testing.

## Installation

```bash
pip install mypackage
```

## Usage

```python
import mypackage
```
""")

    # Create LICENSE
    license_file = tmp_path / "LICENSE"
    license_file.write_text(_MIT_LICENSE)

    # Create tests directory
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir()
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_mypackage.py").write_text('''"""Tests for mypackage."""


def test_version():
    """Test version is defined."""
    from mypackage import __version__
    assert __version__ == "0.1.0"
''')

    return tmp_path


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    """Create an empty directory."""
    return tmp_path


@pytest.fixture
def minimal_package(tmp_path: Path) -> Path:
    """Create a minimal package with only required files."""
    pkg_dir = tmp_path / "src" / "minimal"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]
name = "minimal"
version = "0.1.0"
""")

    return tmp_path


@pytest.fixture
def bad_package(tmp_path: Path) -> Path:
    """Create a package that will cause checks to fail.

    This package has:
    - Missing required metadata fields (no name, no version)
    - Invalid pyproject.toml structure for [project]
    - No README
    - No LICENSE
    - No tests directory
    - An empty src directory (no actual package inside)
    """
    # Create an empty src dir with no package
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    # Create a pyproject.toml with an empty [project] table (missing name, version)
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("""[project]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
""")

    return tmp_path


@pytest.fixture
def package_with_invalid_toml(tmp_path: Path) -> Path:
    """Create a package whose pyproject.toml is not valid TOML."""
    pkg_dir = tmp_path / "src" / "invalidtoml"
    pkg_dir.mkdir(parents=True)
    (pkg_dir / "__init__.py").write_text("")

    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text("this is not valid toml [[[")

    return tmp_path


@pytest.fixture
def package_with_setup_py(tmp_path: Path) -> Path:
    """Create a legacy package using setup.py instead of pyproject.toml."""
    pkg_dir = tmp_path / "legacypkg"
    pkg_dir.mkdir()
    (pkg_dir / "__init__.py").write_text('"""Legacy package."""\n')

    setup_py = tmp_path / "setup.py"
    setup_py.write_text("""from setuptools import setup

setup(
    name="legacypkg",
    version="0.1.0",
)
""")

    return tmp_path
