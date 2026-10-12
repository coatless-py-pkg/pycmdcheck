"""Tests for result types, with no runner and no disk I/O."""

import dataclasses

import pytest

import pycmdcheck
from pycmdcheck.results import (
    SKIP_REASONS,
    CheckResult,
    CheckStatus,
    Finding,
    Report,
    most_severe,
)


class TestCheckStatus:
    """Tests for CheckStatus enum."""

    def test_values(self) -> None:
        assert CheckStatus.OK.value == "ok"
        assert CheckStatus.NOTE.value == "note"
        assert CheckStatus.WARNING.value == "warning"
        assert CheckStatus.ERROR.value == "error"
        assert CheckStatus.SKIPPED.value == "skipped"

    def test_str(self) -> None:
        assert str(CheckStatus.OK) == "ok"
        assert str(CheckStatus.ERROR) == "error"

    def test_symbols(self) -> None:
        assert CheckStatus.OK.symbol == "✓"
        assert CheckStatus.NOTE.symbol == "ℹ"
        assert CheckStatus.WARNING.symbol == "⚠"
        assert CheckStatus.ERROR.symbol == "✗"
        assert CheckStatus.SKIPPED.symbol == "○"

    def test_colors(self) -> None:
        assert CheckStatus.OK.color == "green"
        assert CheckStatus.ERROR.color == "red"
        assert CheckStatus.WARNING.color == "yellow"
        assert CheckStatus.NOTE.color == "blue"
        assert CheckStatus.SKIPPED.color == "dim"

    def test_invalid_value_raises(self) -> None:
        with pytest.raises(ValueError):
            CheckStatus("invalid")


class TestCheckResult:
    """Tests for CheckResult dataclass."""

    def test_basic_construction(self) -> None:
        result = CheckResult(name="test", status=CheckStatus.OK, message="Passed")
        assert result.name == "test"
        assert result.status == CheckStatus.OK
        assert result.message == "Passed"
        assert result.details == []
        assert result.duration == 0.0
        assert result.findings == []
        assert result.skip_reason is None

    def test_positional_construction_still_works(self) -> None:
        """The new fields come after duration and have defaults."""
        result = CheckResult("test", CheckStatus.OK, "Passed", ["detail"], 1.5)
        assert (result.details, result.duration) == (["detail"], 1.5)
        names = [f.name for f in dataclasses.fields(CheckResult)]
        assert names[-3:] == ["duration", "findings", "skip_reason"]

    def test_str_format(self) -> None:
        result = CheckResult(name="test", status=CheckStatus.OK, message="Passed")
        assert str(result) == "✓ test: Passed"

    def test_to_dict(self) -> None:
        result = CheckResult(
            name="test",
            status=CheckStatus.OK,
            message="OK",
            details=["detail1"],
            duration=1.5,
        )
        d = result.to_dict()
        assert d == {
            "name": "test",
            "status": "ok",
            "message": "OK",
            "details": ["detail1"],
            "duration": 1.5,
            "findings": [],
            "skip_reason": None,
        }
        # The new keys come after the ones that 0.1 already had.
        assert list(d)[-2:] == ["findings", "skip_reason"]

    def test_to_dict_with_findings(self) -> None:
        finding = Finding("changelog.missing", "No changelog found")
        result = CheckResult(
            "changelog", CheckStatus.NOTE, "No changelog found", findings=[finding]
        )
        assert result.to_dict()["findings"] == [
            {
                "code": "changelog.missing",
                "status": "note",
                "message": "No changelog found",
                "hint": None,
            }
        ]

    def test_default_details_are_independent(self) -> None:
        r1 = CheckResult(name="a", status=CheckStatus.OK, message="ok")
        r2 = CheckResult(name="b", status=CheckStatus.OK, message="ok")
        r1.details.append("x")
        r1.findings.append(Finding("a.rule", "x"))
        assert r2.details == []
        assert r2.findings == []

    def test_skip(self) -> None:
        result = CheckResult.skip(
            "linting", "No linter configured", "not-configured", ("one", "two")
        )
        assert result == CheckResult(
            name="linting",
            status=CheckStatus.SKIPPED,
            message="No linter configured",
            details=["one", "two"],
            skip_reason="not-configured",
        )
        assert result.findings == []
        assert result.to_dict()["skip_reason"] == "not-configured"

    def test_skip_without_details(self) -> None:
        result = CheckResult.skip(
            "build", "'build' module not installed", "tool-missing"
        )
        assert result.details == []
        assert result.skip_reason in SKIP_REASONS


class TestFinding:
    """Tests for the Finding dataclass and the helpers around it."""

    def test_status_defaults_to_note(self) -> None:
        finding = Finding("changelog.missing", "No changelog found")
        assert finding.status is CheckStatus.NOTE

    def test_to_dict(self) -> None:
        finding = Finding("metadata.missing-required", "No version", CheckStatus.ERROR)
        assert finding.to_dict() == {
            "code": "metadata.missing-required",
            "status": "error",
            "message": "No version",
            "hint": None,
        }
        assert list(finding.to_dict()) == ["code", "status", "message", "hint"]

    def test_hint_comes_after_status(self) -> None:
        finding = Finding("a.rule", "Found", CheckStatus.WARNING, "Add the file.")
        assert finding.hint == "Add the file."
        assert finding.to_dict()["hint"] == "Add the file."
        assert Finding("a.rule", "Found").hint is None

    def test_is_frozen(self) -> None:
        finding = Finding("changelog.missing", "No changelog found")
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.code = "changelog.other"  # type: ignore[misc]
        assert finding == Finding("changelog.missing", "No changelog found")

    def test_is_exported_from_the_package(self) -> None:
        assert pycmdcheck.Finding is Finding
        assert "Finding" in pycmdcheck.__all__

    @pytest.mark.parametrize(
        ("statuses", "expected"),
        [
            ([], CheckStatus.OK),
            ([CheckStatus.NOTE], CheckStatus.NOTE),
            ([CheckStatus.NOTE, CheckStatus.WARNING], CheckStatus.WARNING),
            ([CheckStatus.WARNING, CheckStatus.NOTE], CheckStatus.WARNING),
            (
                [CheckStatus.NOTE, CheckStatus.ERROR, CheckStatus.WARNING],
                CheckStatus.ERROR,
            ),
            ([CheckStatus.ERROR, CheckStatus.ERROR], CheckStatus.ERROR),
        ],
    )
    def test_most_severe(self, statuses, expected) -> None:
        findings = [Finding("x.rule", "text", status) for status in statuses]
        assert most_severe(findings) is expected
        # Any iterable will do, not only a list.
        assert most_severe(iter(findings)) is expected

    def test_skip_reasons_are_a_closed_set(self) -> None:
        assert SKIP_REASONS == {
            "tool-missing",
            "not-configured",
            "not-applicable",
            "prerequisite-failed",
            "network-unavailable",
            "import-failed",
            "parse-failed",
            "fail-fast",
            "deprecated",
        }
        assert isinstance(SKIP_REASONS, frozenset)


class TestReport:
    """Tests for Report dataclass."""

    def test_empty_report_passed(self) -> None:
        report = Report()
        assert report.passed is True

    def test_passed_all_ok(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        report.add(CheckResult("b", CheckStatus.OK, "ok"))
        assert report.passed is True

    def test_passed_with_warnings(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        report.add(CheckResult("b", CheckStatus.WARNING, "warn"))
        assert report.passed is True

    def test_not_passed_with_error(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        report.add(CheckResult("b", CheckStatus.ERROR, "bad"))
        assert report.passed is False

    def test_has_warnings_true(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.WARNING, "warn"))
        assert report.has_warnings is True

    def test_has_warnings_false(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        assert report.has_warnings is False

    def test_count_by_status(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        report.add(CheckResult("b", CheckStatus.OK, "ok"))
        report.add(CheckResult("c", CheckStatus.WARNING, "warn"))
        report.add(CheckResult("d", CheckStatus.ERROR, "err"))
        counts = report.count_by_status()
        assert counts[CheckStatus.OK] == 2
        assert counts[CheckStatus.WARNING] == 1
        assert counts[CheckStatus.ERROR] == 1
        assert counts[CheckStatus.NOTE] == 0
        assert counts[CheckStatus.SKIPPED] == 0

    def test_failed_on_no_match(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        assert report.failed_on(["error"]) is False

    def test_failed_on_match(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.ERROR, "bad"))
        assert report.failed_on(["error"]) is True

    def test_failed_on_warning(self) -> None:
        report = Report()
        report.add(CheckResult("a", CheckStatus.WARNING, "warn"))
        assert report.failed_on(["error"]) is False
        assert report.failed_on(["error", "warning"]) is True

    def test_failed_on_empty_report(self) -> None:
        report = Report()
        assert report.failed_on(["error"]) is False

    @pytest.mark.parametrize("fail_on", [["banana"], ["error", "banana"]])
    def test_failed_on_unknown_status_raises(self, fail_on: list[str]) -> None:
        """A name that is no status raises, even beside one that matches."""
        report = Report()
        report.add(CheckResult("a", CheckStatus.ERROR, "bad"))
        with pytest.raises(ValueError):
            report.failed_on(fail_on)

    def test_to_dict_structure(self) -> None:
        report = Report(package_path="/pkg")
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        d = report.to_dict()
        assert d["package_path"] == "/pkg"
        assert d["passed"] is True
        assert d["summary"]["ok"] == 1
        assert d["summary"]["error"] == 0
        assert set(d["summary"]) >= {"ok", "note", "warning", "error", "skipped"}
        assert len(d["results"]) == 1
        assert d["results"][0]["name"] == "a"
        assert d["results"][0]["findings"] == []
        assert d["results"][0]["skip_reason"] is None

    def test_add_preserves_order(self) -> None:
        report = Report()
        report.add(CheckResult("first", CheckStatus.OK, "ok"))
        report.add(CheckResult("second", CheckStatus.OK, "ok"))
        report.add(CheckResult("third", CheckStatus.OK, "ok"))
        names = [r.name for r in report.results]
        assert names == ["first", "second", "third"]
