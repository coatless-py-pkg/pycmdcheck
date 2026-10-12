"""Integration tests: pycmdcheck on itself, and several checks on one package."""

import json
from pathlib import Path

import pytest
from click.testing import CliRunner

from pycmdcheck.cli import main
from pycmdcheck.results import CheckStatus
from pycmdcheck.runner import run_checks


class TestSelfCheck:
    """Run pycmdcheck against its own source."""

    @pytest.mark.parametrize("name", ["metadata", "structure", "license"])
    def test_self_check(self, repo_root: Path, name: str) -> None:
        """pycmdcheck's own metadata, structure and license checks should pass."""
        runner = CliRunner()
        result = runner.invoke(
            main, [str(repo_root), "--json", "--no-parallel", "-c", name]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["passed"] is True
        assert data["results"][0]["status"] == "ok"


class TestStaticChecksTogether:
    """Several static checks over one package in a single run."""

    def test_valid_package_passes(self, temp_package: Path) -> None:
        """A complete package is OK in every check, and the report passes."""
        report = run_checks(temp_package, checks=["metadata", "structure", "license"])
        assert [(r.name, r.status, r.message) for r in report.results] == [
            ("metadata", CheckStatus.OK, "Package metadata is valid"),
            ("structure", CheckStatus.OK, "Valid src layout structure"),
            ("license", CheckStatus.OK, "License file present"),
        ]
        assert report.passed is True

    def test_bad_package_fails(self, bad_package: Path) -> None:
        """An empty [project] table and an empty src/ are an ERROR each."""
        report = run_checks(bad_package, checks=["metadata", "structure"])
        assert [(r.name, r.status, r.message) for r in report.results] == [
            ("metadata", CheckStatus.ERROR, "Missing required metadata fields"),
            ("structure", CheckStatus.ERROR, "No package directory found in src/"),
        ]
        assert all(r.details for r in report.results)
        assert report.passed is False
