"""Tests for the check runner."""

import sys
import threading
import time
from pathlib import Path

import pytest

from pycmdcheck import ast_cache, check
from pycmdcheck.pyproject_reader import get_project_table
from pycmdcheck.results import CheckResult, CheckStatus, Finding
from pycmdcheck.runner import (
    FAIL_FAST_MESSAGE,
    RUNNER_HINTS,
    RUNNER_RULES,
    _run_single_check,
    fill_hints,
    run_checks,
)


class TestRunner:
    """Tests for run_checks function."""

    def test_run_specific_checks(self, temp_package: Path) -> None:
        """Test running only specific checks returns exactly those checks."""
        report = run_checks(temp_package, checks=["metadata", "structure"])

        assert len(report.results) == 2
        check_names = [r.name for r in report.results]
        assert "metadata" in check_names
        assert "structure" in check_names

    def test_skip_checks(self, temp_package: Path) -> None:
        """Test skipping checks removes them from results."""
        full_report = run_checks(temp_package, profile="triage")
        skipped_report = run_checks(
            temp_package, profile="triage", skip=["docs", "license"]
        )

        full_names = {r.name for r in full_report.results}
        skipped_names = {r.name for r in skipped_report.results}

        assert "docs" not in skipped_names
        assert "license" not in skipped_names
        # The skipped report should have fewer results
        assert len(skipped_report.results) < len(full_report.results)
        # All remaining checks should still be present
        expected = full_names - {"docs", "license"}
        assert skipped_names == expected

    def test_skip_all_checks(self, temp_package: Path) -> None:
        """Test skipping every available check returns empty results."""
        full_report = run_checks(temp_package, profile="triage")
        all_names = [r.name for r in full_report.results]

        empty_report = run_checks(temp_package, profile="triage", skip=all_names)
        assert len(empty_report.results) == 0


class TestPublicAPI:
    """Tests for the public check() function."""

    def test_check_passes_its_arguments_on(self, tmp_path, stub_checks) -> None:
        """skip, config and fail_fast reach the runner, and the path may be text."""
        failing = _stub(CheckStatus.ERROR)
        stub_checks({"a": failing, "b": _stub(), "c": _stub(), "d": _stub()})
        report = check(
            str(tmp_path),
            skip=["d"],
            config={"checks": {"c": False}},
            parallel=False,
            fail_fast=True,
        )
        assert [(r.name, r.status, r.skip_reason) for r in report.results] == [
            ("a", CheckStatus.ERROR, None),
            ("b", CheckStatus.SKIPPED, "fail-fast"),
        ]
        assert report.package_path == str(tmp_path.resolve())


class TestCheckResultNameValidation:
    """Test that runner corrects mismatched result names."""

    @pytest.mark.parametrize(
        "returned",
        [
            CheckResult(
                "wrong",
                CheckStatus.WARNING,
                "No file",
                ["detail"],
                findings=[Finding("right.no-file", "No file", CheckStatus.WARNING)],
            ),
            CheckResult.skip("wrong", "No tool", "tool-missing", ["detail"]),
        ],
        ids=["findings", "skip_reason"],
    )
    def test_correction_keeps_every_other_field(
        self, tmp_path, stub_checks, returned
    ) -> None:
        class Wrong:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                return returned

        stub_checks({"right": Wrong})
        (result,) = run_checks(tmp_path).results
        expected = {**returned.to_dict(), "name": "right", "duration": result.duration}
        assert result.to_dict() == expected


# ---------------------------------------------------------------------------
# Wiring: stub checks stand in for the real ones
# ---------------------------------------------------------------------------


def _stub(status: CheckStatus = CheckStatus.OK, message: str = "done") -> type:
    """Build a check class that returns a fixed result."""

    class Stub:
        description = "stub"

        def run(self, package_path: Path, config: dict) -> CheckResult:
            return CheckResult(name="stub", status=status, message=message)

    return Stub


@pytest.fixture
def stub_checks(monkeypatch: pytest.MonkeyPatch):
    """Replace the registry with the given name-to-class mapping."""

    def _install(registry: dict[str, type]) -> None:
        monkeypatch.setattr("pycmdcheck.runner.discover_checks", lambda: registry)

    return _install


class TestFailFast:
    """Fail-fast stops new checks and reports them as skipped."""

    def test_sequential_marks_the_rest_as_not_run(self, tmp_path, stub_checks) -> None:
        stub_checks({"a": _stub(CheckStatus.ERROR), "b": _stub(), "c": _stub()})
        report = run_checks(tmp_path, parallel=False, fail_fast=True)
        assert [(r.name, r.status) for r in report.results] == [
            ("a", CheckStatus.ERROR),
            ("b", CheckStatus.SKIPPED),
            ("c", CheckStatus.SKIPPED),
        ]
        assert report.results[1].message == FAIL_FAST_MESSAGE
        assert [r.skip_reason for r in report.results] == [
            None,
            "fail-fast",
            "fail-fast",
        ]
        assert report.results[1].findings == []

    def test_parallel_keeps_running_checks_and_skips_queued_ones(
        self, tmp_path, stub_checks, monkeypatch
    ) -> None:
        monkeypatch.setattr("pycmdcheck.runner.MAX_WORKERS", 2)
        running_started = threading.Event()
        boom_done = threading.Event()

        class Boom:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                assert running_started.wait(10)
                boom_done.set()
                return CheckResult("boom", CheckStatus.ERROR, "failed")

        class Running:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                running_started.set()
                assert boom_done.wait(10)
                return CheckResult("running", CheckStatus.OK, "finished")

        stub_checks({"boom": Boom, "running": Running, "queued": _stub()})
        report = run_checks(tmp_path, fail_fast=True)
        assert [(r.name, r.status) for r in report.results] == [
            ("boom", CheckStatus.ERROR),
            ("running", CheckStatus.OK),
            ("queued", CheckStatus.SKIPPED),
        ]
        assert report.results[2].skip_reason == "fail-fast"

    def test_without_fail_fast_everything_runs(self, tmp_path, stub_checks) -> None:
        stub_checks({"a": _stub(CheckStatus.ERROR), "b": _stub()})
        report = run_checks(tmp_path)
        assert [r.status for r in report.results] == [
            CheckStatus.ERROR,
            CheckStatus.OK,
        ]


class TestCheckFailures:
    """A check that misbehaves becomes an ERROR result, not a crash."""

    @pytest.mark.parametrize(
        ("body", "expected", "code"),
        [
            (lambda: 1 / 0, "Check raised exception", "pycmdcheck.check-raised"),
            (lambda: sys.exit(3), "sys.exit(3)", "pycmdcheck.check-exited"),
            (
                lambda: "not a result",
                "Check raised exception: run() returned str, expected a CheckResult",
                "pycmdcheck.invalid-result",
            ),
        ],
    )
    def test_misbehaving_check(
        self, tmp_path, stub_checks, body, expected, code
    ) -> None:
        class Bad:
            def run(self, package_path: Path, config: dict) -> object:
                return body()

        stub_checks({"bad": Bad, "good": _stub()})
        report = run_checks(tmp_path, parallel=False)
        bad, good = report.results
        assert bad.status == CheckStatus.ERROR
        assert expected in bad.message
        assert bad.details
        # The one finding carries the runner's own code, message and hint.
        assert bad.findings == [
            Finding(code, bad.message, CheckStatus.ERROR, RUNNER_HINTS[code])
        ]
        assert code in RUNNER_RULES
        assert bad.skip_reason is None
        assert good.status == CheckStatus.OK
        assert good.findings == []

    def test_check_that_is_not_registered(self, tmp_path) -> None:
        """run_checks refuses an unknown name before this can happen."""
        result = _run_single_check(tmp_path, "nope", {}, {})
        assert result.status == CheckStatus.ERROR
        assert result.message == "Check 'nope' not found"
        code = "pycmdcheck.check-not-found"
        assert result.findings == [
            Finding(code, result.message, CheckStatus.ERROR, RUNNER_HINTS[code])
        ]
        assert code in RUNNER_RULES

    def test_runner_rules_are_a_closed_set(self) -> None:
        assert set(RUNNER_RULES) == {
            "pycmdcheck.check-not-found",
            "pycmdcheck.check-raised",
            "pycmdcheck.check-exited",
            "pycmdcheck.invalid-result",
        }

    def test_finding_message_is_one_line(self, tmp_path, stub_checks) -> None:
        class Bad:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                raise ValueError("first line\n  second line")

        stub_checks({"bad": Bad})
        (bad,) = run_checks(tmp_path).results
        assert bad.message == "Check raised exception: first line\n  second line"
        assert bad.findings == [
            Finding(
                "pycmdcheck.check-raised",
                "Check raised exception: first line second line",
                CheckStatus.ERROR,
                RUNNER_HINTS["pycmdcheck.check-raised"],
            )
        ]

    def test_result_without_findings_is_accepted(self, tmp_path, stub_checks) -> None:
        """A plugin does not have to follow the model."""
        stub_checks({"plugin": _stub(CheckStatus.WARNING, "Something is off")})
        (result,) = run_checks(tmp_path).results
        assert (result.status, result.message) == (
            CheckStatus.WARNING,
            "Something is off",
        )
        assert result.findings == []
        assert result.skip_reason is None


class TestHints:
    """The runner gives a finding the hint of its rule."""

    def _check(self, hints: object) -> type:
        class Notice:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                findings = [
                    Finding("notice.missing", "No NOTICE file"),
                    Finding("notice.empty", "NOTICE is empty", hint="Write to Ann."),
                    Finding("notice.other", "Not in the table"),
                ]
                return CheckResult("notice", CheckStatus.NOTE, "x", findings=findings)

        if hints is not None:
            Notice.hints = hints  # type: ignore[attr-defined]
        return Notice

    def test_hint_comes_from_the_table_of_the_check(
        self, tmp_path, stub_checks
    ) -> None:
        hints = {"notice.missing": "Add a NOTICE file.", "notice.empty": "Fill it."}
        stub_checks({"notice": self._check(hints)})
        (result,) = run_checks(tmp_path).results
        # A hint that the check set itself is kept.
        assert [finding.hint for finding in result.findings] == [
            "Add a NOTICE file.",
            "Write to Ann.",
            None,
        ]

    def test_plugin_without_hints_keeps_working(self, tmp_path, stub_checks) -> None:
        stub_checks({"notice": self._check(None)})
        (result,) = run_checks(tmp_path).results
        assert result.status is CheckStatus.NOTE
        assert [finding.hint for finding in result.findings] == [
            None,
            "Write to Ann.",
            None,
        ]

    @pytest.mark.parametrize(
        "hints", [42, {"notice.missing": 42, "notice.other": "  "}]
    )
    def test_hints_table_that_is_not_text_counts_as_none(
        self, tmp_path, stub_checks, hints
    ) -> None:
        """The plugin keeps its own result, and its JSON hint stays text or null."""
        stub_checks({"notice": self._check(hints)})
        (result,) = run_checks(tmp_path).results
        assert (result.status, result.message) == (CheckStatus.NOTE, "x")
        assert [finding.hint for finding in result.findings] == [
            None,
            "Write to Ann.",
            None,
        ]

    def test_blank_hint_on_a_finding_counts_as_no_hint(self) -> None:
        findings = [
            Finding("notice.a", "A", hint="  "),
            Finding("notice.b", "B", hint=42),  # type: ignore[arg-type]
        ]
        result = CheckResult("notice", CheckStatus.NOTE, "x", findings=findings)
        fill_hints(result, {"notice.a": "Do A."})
        assert [finding.hint for finding in result.findings] == ["Do A.", None]


class TestSelection:
    """Which checks run, and in what order."""

    def test_results_keep_the_requested_order_in_parallel(
        self, tmp_path, stub_checks
    ) -> None:
        class Slow:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                time.sleep(0.05)
                return CheckResult("slow", CheckStatus.OK, "done")

        stub_checks({"slow": Slow, "fast": _stub(), "other": _stub()})
        report = run_checks(tmp_path, checks=["slow", "other", "fast"])
        assert [r.name for r in report.results] == ["slow", "other", "fast"]
        # The time the check took is measured.
        assert report.results[0].duration > 0

    def test_duplicate_names_run_once(self, tmp_path, stub_checks) -> None:
        stub_checks({"a": _stub()})
        assert len(run_checks(tmp_path, checks=["a", "a"]).results) == 1

    def test_disabled_check_does_not_run(self, tmp_path, stub_checks) -> None:
        stub_checks({"a": _stub(), "b": _stub()})
        report = run_checks(tmp_path, config={"checks": {"a": False}})
        assert [r.name for r in report.results] == ["b"]

    def test_config_without_a_checks_table_runs_every_check(
        self, tmp_path, stub_checks
    ) -> None:
        stub_checks({"a": _stub(), "b": _stub()})
        for config in ({}, {"checks": "all"}):
            report = run_checks(tmp_path, config=config)
            assert [r.name for r in report.results] == ["a", "b"]

    def test_deprecated_check_only_runs_when_it_is_named(
        self, tmp_path, stub_checks
    ) -> None:
        """A run that names no check leaves out the deprecated ones."""
        stub_checks({"imports": _stub(), "license": _stub(), "metadata": _stub()})

        def names(**kwargs: object) -> list[str]:
            return [r.name for r in run_checks(tmp_path, **kwargs).results]

        assert names() == ["license", "metadata"]
        assert names(config={"checks": {"imports": True}}) == ["license", "metadata"]
        assert names(profile="minimal") == ["license", "metadata"]
        assert names(checks=["imports"]) == ["imports"]
        assert names(checks=["metadata", "imports"]) == ["metadata", "imports"]
        assert names(checks=["imports"], profile="minimal") == ["imports"]

    @pytest.mark.parametrize(
        ("entry", "kwargs", "warned"),
        [
            ("imports = true", {}, True),
            ("imports = {}", {}, True),
            ("imports = false", {}, False),
            # The defaults enable every check, and that is not the project's doing.
            ("license = true", {}, False),
            ("imports = true", {"checks": "imports"}, False),
            ("imports = true", {"profile": "minimal"}, False),
        ],
        ids=["enabled", "table", "disabled", "not-named", "requested", "profile"],
    )
    def test_deprecated_check_that_the_project_enables_is_logged(
        self, tmp_path, stub_checks, caplog, entry, kwargs, warned
    ) -> None:
        """A plain run says once which deprecated check it left out."""
        (tmp_path / "pyproject.toml").write_text(f"[tool.pycmdcheck.checks]\n{entry}\n")
        stub_checks({"imports": _stub(), "license": _stub()})
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            run_checks(tmp_path, **kwargs)
        logged = [m for m in caplog.messages if "deprecated" in m]
        assert len(logged) == warned
        for message in logged:
            assert "imports" in message
            assert "left out" in message

    @pytest.mark.parametrize("run", [run_checks, check])
    def test_unknown_check_name_is_an_error(self, tmp_path, stub_checks, run) -> None:
        stub_checks({"a": _stub(), "b": _stub()})
        with pytest.raises(ValueError) as raised:
            run(tmp_path, checks=["a", "typo", "nope"])
        message = str(raised.value)
        assert message == "Unknown check(s): typo, nope. Available checks: a, b"

    def test_unknown_skip_name_is_logged(self, tmp_path, stub_checks, caplog) -> None:
        """Skipping a plugin that is not installed must keep working."""
        stub_checks({"a": _stub()})
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            report = run_checks(tmp_path, checks=["a"], skip=["nope"])
        assert [r.name for r in report.results] == ["a"]
        assert "nope" in caplog.text

    def test_unknown_name_is_cleaned_before_it_is_logged(
        self, tmp_path, stub_checks, caplog
    ) -> None:
        stub_checks({"a": _stub()})
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            run_checks(tmp_path, checks=["a"], skip=["x\n::error::forged\x1b[0m"])
        logged = caplog.records[0].getMessage()
        assert logged.endswith("in skip: x\\n::error::forged\\x1b[0m")

    def test_profile_check_that_is_not_registered_is_logged(
        self, tmp_path, stub_checks, caplog
    ) -> None:
        """Only names the caller wrote in checks= are an error."""
        stub_checks({"metadata": _stub()})
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            report = run_checks(tmp_path, profile="minimal")
        assert [r.name for r in report.results] == ["metadata"]
        assert "license" in caplog.text

    def test_single_string_is_one_name(self, tmp_path, stub_checks) -> None:
        """A bare string is one check name, not a sequence of letters."""
        stub_checks({"a": _stub(), "ab": _stub()})
        assert [r.name for r in run_checks(tmp_path, checks="ab").results] == ["ab"]
        assert [r.name for r in run_checks(tmp_path, skip="a").results] == ["ab"]
        # A check that is asked for and skipped does not run.
        report = run_checks(tmp_path, checks=["a", "ab"], skip=["a"])
        assert [r.name for r in report.results] == ["ab"]

    def test_unknown_configured_check_is_logged(
        self, tmp_path, stub_checks, caplog
    ) -> None:
        stub_checks({"metadata": _stub()})
        with caplog.at_level("WARNING", logger="pycmdcheck.runner"):
            run_checks(tmp_path, config={"checks": {"metdata": False}})
        assert "metdata" in caplog.text
        assert "[tool.pycmdcheck.checks]" in caplog.text

    def test_path_must_be_a_directory(self, tmp_path) -> None:
        a_file = tmp_path / "file.txt"
        a_file.write_text("x")
        with pytest.raises(NotADirectoryError):
            run_checks(a_file)
        with pytest.raises(FileNotFoundError):
            run_checks(tmp_path / "missing")


class TestProfileArgument:
    """profile= on the Python API matches --profile on the command line."""

    def test_profile_selects_its_checks(self, temp_package: Path) -> None:
        report = check(temp_package, profile="minimal")
        assert {r.name for r in report.results} == {"metadata", "structure", "license"}

    def test_explicit_checks_win_over_the_profile_set(self, temp_package) -> None:
        report = run_checks(temp_package, checks=["license"], profile="minimal")
        assert [r.name for r in report.results] == ["license"]

    def test_profile_options_reach_the_check(self, tmp_path, stub_checks) -> None:
        seen: dict[str, object] = {}

        class Docs:
            def run(self, package_path: Path, config: dict) -> CheckResult:
                seen.update(config)
                return CheckResult("docs", CheckStatus.OK, "done")

        stub_checks({"docs": Docs})
        run_checks(tmp_path, checks=["docs"], profile="strict")
        assert seen["check_docstrings"] is True

    def test_unknown_profile(self, tmp_path) -> None:
        with pytest.raises(ValueError, match="Unknown profile"):
            run_checks(tmp_path, profile="nope")


def test_second_run_sees_an_edited_file(tmp_path, stub_checks) -> None:
    """Results are not served from a stale cache."""

    class Name:
        def run(self, package_path: Path, config: dict) -> CheckResult:
            name = get_project_table(package_path).get("name", "")
            return CheckResult("name", CheckStatus.OK, str(name))

    stub_checks({"name": Name})
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text('[project]\nname = "first"\n')
    assert run_checks(tmp_path).results[0].message == "first"
    pyproject.write_text('[project]\nname = "second-name"\n')
    assert run_checks(tmp_path).results[0].message == "second-name"


def test_parsed_trees_are_released_after_a_run(temp_package: Path) -> None:
    """The run ends with empty file caches."""
    run_checks(temp_package, checks=["docs", "py_typed"], parallel=False)
    assert len(ast_cache._cache) == 0


def test_ctrl_c_stops_running_tools(tmp_path, stub_checks, monkeypatch) -> None:
    """An interrupt in parallel mode kills running tools and propagates."""
    release = threading.Event()
    kills: list[int] = []

    class Blocked:
        def run(self, package_path: Path, config: dict) -> CheckResult:
            release.wait(10)
            return CheckResult("blocked", CheckStatus.OK, "done")

    def interrupt(futures: object) -> object:
        raise KeyboardInterrupt

    def fake_kill() -> int:
        kills.append(1)
        release.set()
        return 0

    monkeypatch.setattr("pycmdcheck.runner.as_completed", interrupt)
    monkeypatch.setattr("pycmdcheck.runner.kill_running_tools", fake_kill)
    stub_checks({"a": Blocked, "b": Blocked})
    with pytest.raises(KeyboardInterrupt):
        run_checks(tmp_path)
    assert kills
