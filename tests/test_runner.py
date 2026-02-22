"""Tests for the check runner."""

from pathlib import Path

from pycmdcheck import check
from pycmdcheck.results import CheckStatus
from pycmdcheck.runner import run_checks


class TestRunner:
    """Tests for run_checks function."""

    def test_run_all_checks(self, temp_package: Path) -> None:
        """Test running all checks on a valid package returns populated Report."""
        report = run_checks(temp_package)

        assert len(report.results) > 0
        assert report.package_path == str(temp_package.resolve())
        # Every result should be a CheckResult with a non-empty name
        for result in report.results:
            assert result.name != ""
            assert result.status in CheckStatus

    def test_run_specific_checks(self, temp_package: Path) -> None:
        """Test running only specific checks returns exactly those checks."""
        report = run_checks(temp_package, checks=["metadata", "structure"])

        assert len(report.results) == 2
        check_names = [r.name for r in report.results]
        assert "metadata" in check_names
        assert "structure" in check_names

    def test_run_single_check(self, temp_package: Path) -> None:
        """Test running a single check returns exactly one result."""
        report = run_checks(temp_package, checks=["metadata"])

        assert len(report.results) == 1
        assert report.results[0].name == "metadata"

    def test_skip_checks(self, temp_package: Path) -> None:
        """Test skipping checks removes them from results."""
        full_report = run_checks(temp_package)
        skipped_report = run_checks(temp_package, skip=["typing", "linting"])

        full_names = {r.name for r in full_report.results}
        skipped_names = {r.name for r in skipped_report.results}

        assert "typing" not in skipped_names
        assert "linting" not in skipped_names
        # The skipped report should have fewer results
        assert len(skipped_report.results) < len(full_report.results)
        # All remaining checks should still be present
        expected = full_names - {"typing", "linting"}
        assert skipped_names == expected

    def test_skip_all_checks(self, temp_package: Path) -> None:
        """Test skipping every available check returns empty results."""
        full_report = run_checks(temp_package)
        all_names = [r.name for r in full_report.results]

        empty_report = run_checks(temp_package, skip=all_names)
        assert len(empty_report.results) == 0

    def test_parallel_execution(self, temp_package: Path) -> None:
        """Test parallel check execution produces same check names as sequential."""
        par_report = run_checks(temp_package, parallel=True)
        seq_report = run_checks(temp_package, parallel=False)

        par_names = {r.name for r in par_report.results}
        seq_names = {r.name for r in seq_report.results}
        assert par_names == seq_names
        assert len(par_report.results) > 0

    def test_sequential_execution(self, temp_package: Path) -> None:
        """Test sequential execution returns results in deterministic order."""
        report1 = run_checks(temp_package, parallel=False)
        report2 = run_checks(temp_package, parallel=False)

        names1 = [r.name for r in report1.results]
        names2 = [r.name for r in report2.results]
        assert names1 == names2

    def test_nonexistent_check_is_ignored(self, temp_package: Path) -> None:
        """Test requesting a check that does not exist yields empty results."""
        report = run_checks(temp_package, checks=["nonexistent_check_xyz"])
        # The runner filters to only checks that exist in available_checks
        assert len(report.results) == 0

    def test_run_checks_with_explicit_config(self, temp_package: Path) -> None:
        """Test passing explicit config overrides pyproject.toml loading."""
        config = {
            "fail_on": ["error"],
            "checks": {
                "metadata": True,
                "structure": True,
            },
        }
        report = run_checks(temp_package, checks=["metadata"], config=config)
        assert len(report.results) == 1
        assert report.results[0].name == "metadata"

    def test_results_have_duration(self, temp_package: Path) -> None:
        """Test every result has a non-negative duration."""
        report = run_checks(temp_package, checks=["metadata", "structure"])

        for result in report.results:
            assert result.duration >= 0.0

    def test_metadata_and_structure_pass_on_valid_package(
        self, temp_package: Path
    ) -> None:
        """Test that metadata and structure checks pass on a well-formed package."""
        report = run_checks(temp_package, checks=["metadata", "structure"])

        for result in report.results:
            assert result.status == CheckStatus.OK, (
                f"Expected OK for {result.name}, got {result.status}: {result.message}"
            )

        assert report.passed is True


class TestPublicAPI:
    """Tests for the public check() function."""

    def test_check_function_returns_report(self, temp_package: Path) -> None:
        """Test the check() function returns a Report with results."""
        report = check(str(temp_package))

        assert len(report.results) > 0
        assert report.package_path == str(temp_package.resolve())

    def test_check_with_checks_filter(self, temp_package: Path) -> None:
        """Test check() with checks= filters to only those checks."""
        report = check(
            str(temp_package),
            checks=["metadata"],
        )

        assert len(report.results) == 1
        assert report.results[0].name == "metadata"

    def test_check_with_skip_filter(self, temp_package: Path) -> None:
        """Test check() with skip= removes specified checks."""
        report = check(
            str(temp_package),
            skip=["linting", "typing", "tests"],
        )

        result_names = {r.name for r in report.results}
        assert "linting" not in result_names
        assert "typing" not in result_names
        assert "tests" not in result_names

    def test_check_with_combined_filters(self, temp_package: Path) -> None:
        """Test check() with both checks= and skip= options."""
        report = check(
            str(temp_package),
            checks=["metadata"],
            skip=["linting"],
        )

        assert len(report.results) == 1
        assert report.results[0].name == "metadata"


class TestCheckResultNameValidation:
    """Test that runner corrects mismatched result names."""

    def test_corrects_mismatched_name(self, temp_package: Path) -> None:
        """Runner should correct a check that returns wrong name."""
        from unittest.mock import MagicMock

        from pycmdcheck.results import CheckResult, CheckStatus
        from pycmdcheck.runner import _run_single_check

        # Create a mock check class that returns wrong name
        mock_check = MagicMock()
        mock_check.return_value.run.return_value = CheckResult(
            name="wrong_name",
            status=CheckStatus.OK,
            message="All good",
        )

        available = {"my_check": mock_check}
        result = _run_single_check(temp_package, "my_check", available, {})
        assert result.name == "my_check"  # Should be corrected


class TestReportFromRunner:
    """Tests for Report behaviour when populated by the runner."""

    def test_passed_is_true_for_valid_package(self, temp_package: Path) -> None:
        """Test passed is True for a valid package with metadata and structure."""
        report = run_checks(temp_package, checks=["metadata", "structure"])

        assert report.passed is True

    def test_count_by_status_values(self, temp_package: Path) -> None:
        """Test count_by_status returns correct counts for a valid package."""
        report = run_checks(temp_package, checks=["metadata", "structure"])
        counts = report.count_by_status()

        # Both metadata and structure should be OK on temp_package
        assert counts[CheckStatus.OK] == 2
        assert counts[CheckStatus.ERROR] == 0
        assert counts[CheckStatus.WARNING] == 0
        # All statuses should be present as keys
        for status in CheckStatus:
            assert status in counts

    def test_count_by_status_sums_to_total(self, temp_package: Path) -> None:
        """Test that the sum of all counts equals total number of results."""
        report = run_checks(temp_package)
        counts = report.count_by_status()

        total = sum(counts.values())
        assert total == len(report.results)

    def test_failed_on_error_false_for_valid(self, temp_package: Path) -> None:
        """Test failed_on(['error']) is False for a valid package's metadata."""
        report = run_checks(temp_package, checks=["metadata"])

        assert report.failed_on(["error"]) is False

    def test_failed_on_error_true_for_bad_package(self, bad_package: Path) -> None:
        """Test failed_on(['error']) is True when checks produce errors."""
        report = run_checks(bad_package, checks=["metadata"])

        assert report.results[0].status == CheckStatus.ERROR
        assert report.failed_on(["error"]) is True

    def test_to_dict_schema(self, temp_package: Path) -> None:
        """Test JSON serialization has the expected top-level keys and types."""
        report = run_checks(temp_package, checks=["metadata"])
        data = report.to_dict()

        assert data["package_path"] == str(temp_package.resolve())
        assert data["passed"] is True
        assert "ok" in data["summary"]
        assert "error" in data["summary"]
        assert "warning" in data["summary"]
        assert "note" in data["summary"]
        assert "skipped" in data["summary"]
        assert len(data["results"]) == 1
        assert data["results"][0]["name"] == "metadata"
        assert data["results"][0]["status"] == "ok"
