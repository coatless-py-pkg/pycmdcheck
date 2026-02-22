"""Tests for community check."""

from pathlib import Path

from pycmdcheck.checks.community import CommunityCheck
from pycmdcheck.results import CheckStatus


class TestCommunityCheck:
    """Tests for CommunityCheck."""

    def test_both_files_present(self, temp_package: Path) -> None:
        """Both CONTRIBUTING and CODE_OF_CONDUCT present -> OK."""
        contributing = temp_package / "CONTRIBUTING.md"
        contributing.write_text("# Contributing\n\nHow to contribute.\n")
        coc = temp_package / "CODE_OF_CONDUCT.md"
        coc.write_text("# Code of Conduct\n\nBe nice.\n")
        check = CommunityCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.name == "community"

    def test_missing_both(self, temp_package: Path) -> None:
        """Neither file present -> NOTE."""
        check = CommunityCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert "2" in result.message

    def test_only_contributing(self, temp_package: Path) -> None:
        """Only CONTRIBUTING present -> NOTE (missing COC)."""
        (temp_package / "CONTRIBUTING.md").write_text("# Contributing\n")
        check = CommunityCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert "1" in result.message

    def test_only_code_of_conduct(self, temp_package: Path) -> None:
        """Only CODE_OF_CONDUCT present -> NOTE (missing CONTRIBUTING)."""
        (temp_package / "CODE_OF_CONDUCT.md").write_text("# Code of Conduct\n")
        check = CommunityCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert "1" in result.message

    def test_rst_variants(self, temp_package: Path) -> None:
        """RST variants are also recognized."""
        (temp_package / "CONTRIBUTING.rst").write_text("Contributing\n============\n")
        coc = temp_package / "CODE_OF_CONDUCT.rst"
        coc.write_text("Code of Conduct\n===============\n")
        check = CommunityCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
