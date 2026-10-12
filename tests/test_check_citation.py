"""Tests for citation check."""

import sys
from pathlib import Path

import pytest

from pycmdcheck.checks import citation
from pycmdcheck.checks.citation import CitationCheck
from pycmdcheck.results import CheckStatus, Finding
from tests.helpers import write_files

COMPLETE_CFF = (
    "cff-version: 1.2.0\n"
    "message: If you use this software, please cite it.\n"
    "title: My Package\n"
    "authors:\n"
    "  - given-names: Jane\n"
    "    family-names: Doe\n"
)
ALL_KEYS = "Found keys in CITATION.cff: cff-version, message, title, authors"
MISSING = Finding(
    "citation.missing", "No citation file or other citation metadata found"
)


def run_cff(root: Path, content: str | bytes) -> list[str]:
    """Write CITATION.cff, expect OK, and return the lines after "Found:"."""
    write_files(root, {"CITATION.cff": content})
    result = CitationCheck().run(root, {})
    assert result.status == CheckStatus.OK
    assert result.message == "Citation file present"
    assert result.findings == []
    assert result.details[0] == "Found: CITATION.cff"
    return result.details[1:]


class TestCitationCheck:
    """Tests for CitationCheck."""

    @pytest.mark.parametrize(
        "filename",
        [
            "CITATION.cff",
            "CITATION.bib",
            "CITATION",
            "CITATION.md",
            "CITATION.txt",
            "CITATION.rst",
            "citation.cff",
            "Citation.bib",
        ],
    )
    def test_accepted_names(self, tmp_path: Path, filename: str) -> None:
        """Each accepted name, in any letter case -> OK, reported as on disk."""
        (tmp_path / filename).write_text(COMPLETE_CFF, encoding="utf-8")
        result = CitationCheck().run(tmp_path, {})
        assert result.name == "citation"
        assert result.status == CheckStatus.OK
        assert result.details[0] == f"Found: {filename}"

    def test_no_citation(self, temp_package: Path) -> None:
        """No citation file -> NOTE that says where the check looked."""
        result = CitationCheck().run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No citation file found"
        assert result.findings == [MISSING]
        assert result.details[0].startswith("Looked in the project root")
        assert "CITATION.cff" in result.details[0]
        assert "CITATION.rst" in result.details[0]
        assert result.details[1] == (
            "Also accepted: .zenodo.json, codemeta.json, paper.md with paper.bib, "
            "or a DOI link in the README"
        )

    @pytest.mark.parametrize(
        ("files", "detail"),
        [
            ({".zenodo.json": "{}"}, "Found: .zenodo.json"),
            ({"CodeMeta.json": "{}"}, "Found: CodeMeta.json"),
            (
                {"paper.md": "# A paper\n", "paper.bib": "@article{a}\n"},
                "Found: paper.md with paper.bib "
                "(a Journal of Open Source Software paper)",
            ),
            (
                {"README.md": "Cite [the paper](https://doi.org/10.21105/joss.01234)."},
                "Found: a DOI link in README.md",
            ),
            (
                {"README.rst": ".. image:: https://zenodo.org/badge/123456789.svg\n"},
                "Found: a DOI link in README.rst",
            ),
        ],
        ids=["zenodo", "codemeta", "joss-paper", "readme-doi", "readme-zenodo-badge"],
    )
    def test_other_citation_metadata_is_accepted(
        self, tmp_path: Path, files: dict[str, str], detail: str
    ) -> None:
        """Without a CITATION file, other citation metadata -> OK that names it."""
        write_files(tmp_path, files)
        result = CitationCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.message == "Citation metadata found"
        assert result.details == [detail]
        assert result.findings == []

    @pytest.mark.parametrize("folder", ["paper", "joss"])
    def test_joss_paper_in_its_own_directory(self, tmp_path: Path, folder: str) -> None:
        """paper.md with paper.bib under paper/ or joss/ counts like one in the root."""
        (tmp_path / folder).mkdir()
        (tmp_path / folder / "paper.md").write_text("# A paper\n", encoding="utf-8")
        (tmp_path / folder / "paper.bib").write_text("@article{a}\n", encoding="utf-8")
        result = CitationCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.message == "Citation metadata found"
        assert result.details == [
            f"Found: {folder}/paper.md with {folder}/paper.bib "
            "(a Journal of Open Source Software paper)"
        ]

    @pytest.mark.parametrize(
        "files",
        [
            {"paper.md": "# A paper\n"},
            {"paper.bib": "@article{a}\n"},
            {"README.md": "Archived on https://zenodo.org/ and https://doi.org/.\n"},
        ],
        ids=["paper-without-bib", "bib-without-paper", "readme-without-doi"],
    )
    def test_incomplete_citation_metadata_is_a_note(
        self, tmp_path: Path, files: dict[str, str]
    ) -> None:
        """Half a paper, or a README with no DOI in it, is no citation metadata."""
        write_files(tmp_path, files)
        result = CitationCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No citation file found"
        assert result.findings == [MISSING]

    def test_citation_file_wins_over_other_metadata(self, tmp_path: Path) -> None:
        """A CITATION file is what gets reported, whatever else is there."""
        (tmp_path / ".zenodo.json").write_text("{}", encoding="utf-8")
        (tmp_path / "README.md").write_text("https://doi.org/10.5281/zenodo.1\n")
        assert run_cff(tmp_path, COMPLETE_CFF) == [ALL_KEYS]

    def test_directory_named_like_a_file(self, tmp_path: Path) -> None:
        """A CITATION.cff directory is the same as no citation file."""
        (tmp_path / "CITATION.cff").mkdir()
        result = CitationCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.findings == [MISSING]

    @pytest.mark.parametrize(
        "content",
        [
            COMPLETE_CFF,
            '"cff-version": 1.2.0\n\'message\': Cite it.\n"title" : X\nauthors :\n',
            "\ufeff" + COMPLETE_CFF,
            "---\n" + COMPLETE_CFF,
            COMPLETE_CFF.replace("Jane", "José").encode("latin-1"),
        ],
        ids=["plain", "quoted-and-spaced", "bom", "document-marker", "latin-1"],
    )
    def test_cff_keys_found(self, tmp_path: Path, content: str | bytes) -> None:
        """All four CFF 1.2 keys, however they are written -> one detail."""
        assert run_cff(tmp_path, content) == [ALL_KEYS]

    def test_cff_extension_in_capitals_is_scanned(self, tmp_path: Path) -> None:
        """The extension is matched in any letter case, like the name."""
        write_files(tmp_path, {"CITATION.CFF": COMPLETE_CFF})
        assert CitationCheck().run(tmp_path, {}).details == [
            "Found: CITATION.CFF",
            "Found keys in CITATION.CFF: cff-version, message, title, authors",
        ]

    def test_cff_one_key_absent(self, tmp_path: Path) -> None:
        """A CFF without authors stays OK and names the key in details."""
        content = "cff-version: 1.2.0\nmessage: Cite it.\ntitle: My Package\n"
        assert run_cff(tmp_path, content) == [
            "Found keys in CITATION.cff: cff-version, message, title",
            "Not found by a line scan: authors (CFF 1.2 requires all four keys)",
        ]

    def test_cff_title_and_authors_are_not_enough(self, tmp_path: Path) -> None:
        """cff-version and message are required as well."""
        details = run_cff(tmp_path, "title: Test\nauthors:\n  - name: Test\n")
        assert details[0] == "Found keys in CITATION.cff: title, authors"
        assert details[1].startswith("Not found by a line scan: cff-version, message")

    def test_cff_nested_keys_do_not_count(self, tmp_path: Path) -> None:
        """Only unindented keys are top-level keys."""
        content = "cff-version: 1.2.0\nreferences:\n  - title: Other\n    authors: []\n"
        details = run_cff(tmp_path, content)
        assert details[0] == "Found keys in CITATION.cff: cff-version"
        assert "message, title, authors" in details[1]

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "title: [unterminated\nauthors: {{{\n",
            "{cff-version: 1.2.0, message: m, title: t, authors: [{name: n}]}\n",
        ],
        ids=["empty", "invalid-yaml", "valid-flow-style"],
    )
    def test_cff_makes_no_claim_it_cannot_support(
        self, tmp_path: Path, content: str
    ) -> None:
        """Never "has required keys" or "missing keys" from a line scan."""
        text = "\n".join(run_cff(tmp_path, content))
        assert "required keys" not in text
        assert "missing" not in text.lower()
        assert "Not found by a line scan" in text

    def test_unreadable_cff_stays_ok(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A read error is a detail, not a status."""

        def refuse(path: Path) -> str:
            raise PermissionError(13, "Permission denied", str(path))

        monkeypatch.setattr(citation, "read_text_capped", refuse)
        assert run_cff(tmp_path, COMPLETE_CFF) == [
            "Could not read CITATION.cff: Permission denied"
        ]

    @pytest.mark.skipif(sys.platform == "win32", reason="needs /dev/null")
    def test_link_to_a_device_is_not_a_citation_file(self, tmp_path: Path) -> None:
        """CITATION.cff linked to a device is ignored, like a directory."""
        (tmp_path / "CITATION.cff").symlink_to("/dev/null")
        result = CitationCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No citation file found"
        assert result.findings == [MISSING]

    def test_following_the_hint_clears_the_rule(self, tmp_path: Path) -> None:
        """Doing what the hint says makes the rule stop firing."""
        assert CitationCheck().run(tmp_path, {}).findings == [MISSING]
        hint = CitationCheck.hints["citation.missing"]
        assert hint.startswith("Add a CITATION.cff file to the project root")
        assert run_cff(tmp_path, COMPLETE_CFF) == [ALL_KEYS]
        assert (tmp_path / "CITATION.cff").is_file()
