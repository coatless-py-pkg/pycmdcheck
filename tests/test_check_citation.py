"""Tests for citation check."""

from pathlib import Path

from pycmdcheck.checks.citation import CitationCheck
from pycmdcheck.results import CheckStatus


class TestCitationCheck:
    """Tests for CitationCheck."""

    def test_citation_cff_present(self, temp_package: Path) -> None:
        """Valid CITATION.cff -> OK."""
        content = (
            "cff-version: 1.2.0\n"
            "title: My Package\n"
            "authors:\n"
            "  - given-names: Jane\n"
            "    family-names: Doe\n"
        )
        (temp_package / "CITATION.cff").write_text(content)
        check = CitationCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.name == "citation"
        assert any("required keys" in d for d in result.details)

    def test_citation_bib_present(self, temp_package: Path) -> None:
        """CITATION.bib variant -> OK."""
        (temp_package / "CITATION.bib").write_text("@software{mypackage,\n}\n")
        check = CitationCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK

    def test_no_citation(self, temp_package: Path) -> None:
        """No citation file -> NOTE."""
        check = CitationCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.NOTE

    def test_cff_missing_keys(self, temp_package: Path) -> None:
        """CITATION.cff missing required keys -> OK with warning in details."""
        (temp_package / "CITATION.cff").write_text("cff-version: 1.2.0\n")
        check = CitationCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert any("missing keys" in d for d in result.details)

    def test_cff_valid_keys(self, temp_package: Path) -> None:
        """CITATION.cff with all required keys -> OK with confirmation."""
        content = "title: Test\nauthors:\n  - name: Test\n"
        (temp_package / "CITATION.cff").write_text(content)
        check = CitationCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert any("required keys" in d.lower() for d in result.details)
