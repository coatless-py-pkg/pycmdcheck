"""Tests for result types — no runner, no disk I/O."""

import pytest

from pycmdcheck.results import CheckResult, CheckStatus, Report


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

    def test_construct_from_value(self) -> None:
        assert CheckStatus("ok") is CheckStatus.OK
        assert CheckStatus("error") is CheckStatus.ERROR

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

    def test_str_format(self) -> None:
        result = CheckResult(name="test", status=CheckStatus.OK, message="Passed")
        assert str(result) == "✓ test: Passed"

    def test_str_format_error(self) -> None:
        result = CheckResult(name="lint", status=CheckStatus.ERROR, message="Failed")
        assert str(result) == "✗ lint: Failed"

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
        }

    def test_default_details_are_independent(self) -> None:
        r1 = CheckResult(name="a", status=CheckStatus.OK, message="ok")
        r2 = CheckResult(name="b", status=CheckStatus.OK, message="ok")
        r1.details.append("x")
        assert r2.details == []


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

    def test_to_dict_structure(self) -> None:
        report = Report(package_path="/pkg")
        report.add(CheckResult("a", CheckStatus.OK, "ok"))
        d = report.to_dict()
        assert d["package_path"] == "/pkg"
        assert d["passed"] is True
        assert d["summary"]["ok"] == 1
        assert d["summary"]["error"] == 0
        assert len(d["results"]) == 1
        assert d["results"][0]["name"] == "a"

    def test_add_preserves_order(self) -> None:
        report = Report()
        report.add(CheckResult("first", CheckStatus.OK, "ok"))
        report.add(CheckResult("second", CheckStatus.OK, "ok"))
        report.add(CheckResult("third", CheckStatus.OK, "ok"))
        names = [r.name for r in report.results]
        assert names == ["first", "second", "third"]
