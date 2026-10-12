"""Tests for plugin discovery through entry points."""

import importlib
from pathlib import Path

import pytest

from pycmdcheck import discovery
from pycmdcheck.pyproject_reader import get_project_table

# conftest replaces discover_checks with a fixed registry for every test, so
# keep the real functions, taken at import time, to test them here.
_real_discover = discovery.discover_checks.__wrapped__
_real_list = discovery.list_available_checks


class FakeEntryPoint:
    """Stand-in for importlib.metadata.EntryPoint."""

    def __init__(self, name: str, value: str, target: object) -> None:
        self.name = name
        self.value = value
        self._target = target

    def load(self) -> object:
        if isinstance(self._target, BaseException):
            raise self._target
        return self._target


class Plugin:
    description = "A plugin check"


@pytest.fixture
def entry_points(monkeypatch: pytest.MonkeyPatch):
    """Make discovery see the given entry points."""

    def _install(*eps: FakeEntryPoint) -> None:
        monkeypatch.setattr(discovery, "entry_points", lambda group: list(eps))

    return _install


class TestDiscoverChecks:
    def test_loads_and_sorts(self, entry_points) -> None:
        entry_points(
            FakeEntryPoint("zeta", "plugin.mod:Zeta", Plugin),
            FakeEntryPoint("alpha", "plugin.mod:Alpha", Plugin),
        )
        assert list(_real_discover()) == ["alpha", "zeta"]

    @pytest.mark.parametrize(
        "error", [ImportError("gone"), RuntimeError("boom"), SyntaxError("bad")]
    )
    def test_broken_plugin_is_skipped(self, entry_points, caplog, error) -> None:
        entry_points(
            FakeEntryPoint("broken", "plugin.mod:Broken", error),
            FakeEntryPoint("fine", "plugin.mod:Fine", Plugin),
        )
        with caplog.at_level("WARNING", logger="pycmdcheck.discovery"):
            assert list(_real_discover()) == ["fine"]
        assert "broken" in caplog.text

    # "imports" is deprecated: no roster holds it, and its name stays reserved.
    @pytest.mark.parametrize("name", ["metadata", "imports"])
    def test_plugin_cannot_replace_a_built_in(self, entry_points, caplog, name) -> None:
        entry_points(
            FakeEntryPoint(name, "evil.mod:Evil", Plugin),
            FakeEntryPoint(name, f"pycmdcheck.checks.{name}:Real", Plugin),
        )
        with caplog.at_level("WARNING", logger="pycmdcheck.discovery"):
            checks = _real_discover()
        assert list(checks) == [name]
        assert "evil.mod" in caplog.text

    def test_object_that_is_not_a_class_is_skipped(self, entry_points) -> None:
        entry_points(FakeEntryPoint("odd", "plugin.mod:VALUE", "a string"))
        assert _real_discover() == {}


def test_description_is_always_text(monkeypatch: pytest.MonkeyPatch) -> None:
    class NoDescription:
        pass

    class NumberDescription:
        description = 42

    registry = {"a": NoDescription, "b": NumberDescription}
    monkeypatch.setattr(discovery, "discover_checks", lambda: registry)
    assert _real_list() == [("a", "No description available"), ("b", "42")]


def test_every_registered_entry_point_imports(repo_root: Path) -> None:
    """Each built-in entry point in pyproject.toml names a real class."""
    entry_points = get_project_table(repo_root)["entry-points"]["pycmdcheck.checks"]
    for name, value in entry_points.items():
        module_name, _, attribute = value.partition(":")
        check_class = getattr(importlib.import_module(module_name), attribute)
        assert check_class.name == name
