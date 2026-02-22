"""Integration test — run pycmdcheck on itself."""

import json
from pathlib import Path

from click.testing import CliRunner

from pycmdcheck.cli import main


class TestSelfCheck:
    """Run pycmdcheck against its own source."""

    def test_self_check_metadata(self) -> None:
        """pycmdcheck's own metadata check should pass."""
        runner = CliRunner()
        project_root = str(Path(__file__).parent.parent)
        result = runner.invoke(
            main, [project_root, "--json", "--no-parallel", "-c", "metadata"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["passed"] is True

    def test_self_check_structure(self) -> None:
        """pycmdcheck's own structure check should pass."""
        runner = CliRunner()
        project_root = str(Path(__file__).parent.parent)
        result = runner.invoke(
            main, [project_root, "--json", "--no-parallel", "-c", "structure"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["passed"] is True

    def test_self_check_license(self) -> None:
        """pycmdcheck's own license check should pass."""
        runner = CliRunner()
        project_root = str(Path(__file__).parent.parent)
        result = runner.invoke(
            main, [project_root, "--json", "--no-parallel", "-c", "license"]
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["passed"] is True
