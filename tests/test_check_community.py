"""Tests for community check."""

from pathlib import Path

import pytest

from pycmdcheck.checks.community import CommunityCheck
from pycmdcheck.results import CheckStatus, Finding

SEARCHED = "Not found in repository (root, .github/, docs/, doc/): "
CAVEAT = (
    "Organization-level default community health files cannot be detected by this check"
)
NO_CONTRIBUTING = Finding(
    "community.missing-contributing", "CONTRIBUTING.md not found in repository"
)
NO_CODE_OF_CONDUCT = Finding(
    "community.missing-code-of-conduct", "CODE_OF_CONDUCT.md not found in repository"
)


def write(root: Path, *relatives: str) -> None:
    """Write a short file at each relative path under *root*."""
    for relative in relatives:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("Be kind and send patches.\n", encoding="utf-8")


class TestCommunityCheck:
    """Tests for CommunityCheck."""

    @pytest.mark.parametrize(
        ("contributing", "conduct"),
        [
            # Names and places accepted since 0.1.0.
            ("CONTRIBUTING.md", "CODE_OF_CONDUCT.md"),
            ("CONTRIBUTING.rst", "CODE_OF_CONDUCT.rst"),
            ("CONTRIBUTING.txt", "CODE_OF_CONDUCT.txt"),
            ("CONTRIBUTING", "CODE_OF_CONDUCT"),
            (".github/CONTRIBUTING.md", ".github/CODE_OF_CONDUCT.md"),
            ("docs/CONTRIBUTING.md", "docs/CODE_OF_CONDUCT.md"),
            # Letter case as real projects write it.
            ("docs/contributing.md", "docs/code_of_conduct.md"),
            ("Contributing.md", "Code_of_Conduct.md"),
            # doc/ and the other code of conduct names.
            ("doc/contributing.rst", "doc/CODE_OF_CONDUCT.rst"),
            ("CONTRIBUTING.md", "CODE-OF-CONDUCT.md"),
            ("CONTRIBUTING.md", "CONDUCT.md"),
        ],
    )
    def test_both_files_present(
        self, tmp_path: Path, contributing: str, conduct: str
    ) -> None:
        """Both files, in any accepted form -> OK, reported as on disk."""
        write(tmp_path, contributing, conduct)
        result = CommunityCheck().run(tmp_path, {})
        assert result.name == "community"
        assert result.status == CheckStatus.OK
        assert result.message == "Community health files present"
        assert result.details == [f"Found: {contributing}", f"Found: {conduct}"]
        assert result.findings == []

    def test_missing_both(self, temp_package: Path) -> None:
        """Neither file -> NOTE that says where it looked and what it cannot see."""
        result = CommunityCheck().run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "2 community file(s) not found in repository"
        assert result.details == [
            SEARCHED + "CONTRIBUTING.md, CODE_OF_CONDUCT.md",
            CAVEAT,
        ]
        assert result.findings == [NO_CONTRIBUTING, NO_CODE_OF_CONDUCT]

    @pytest.mark.parametrize(
        ("present", "absent", "finding"),
        [
            ("CONTRIBUTING.md", "CODE_OF_CONDUCT.md", NO_CODE_OF_CONDUCT),
            ("CODE_OF_CONDUCT.md", "CONTRIBUTING.md", NO_CONTRIBUTING),
        ],
    )
    def test_only_one_file(
        self, tmp_path: Path, present: str, absent: str, finding: Finding
    ) -> None:
        """One file present -> NOTE naming the other, under its own code."""
        write(tmp_path, present)
        result = CommunityCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "1 community file(s) not found in repository"
        assert result.details == [f"Found: {present}", SEARCHED + absent, CAVEAT]
        assert result.findings == [finding]

    def test_directories_do_not_count(self, tmp_path: Path) -> None:
        """Directories with the expected names are not community files."""
        write(tmp_path, "CONTRIBUTING/guide.md", "CODE_OF_CONDUCT/text.md")
        result = CommunityCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "2 community file(s) not found in repository"
        assert result.findings == [NO_CONTRIBUTING, NO_CODE_OF_CONDUCT]

    @pytest.mark.parametrize(
        ("added", "finding"),
        [
            ("CONTRIBUTING.md", NO_CONTRIBUTING),
            ("CODE_OF_CONDUCT.md", NO_CODE_OF_CONDUCT),
        ],
    )
    def test_following_the_hint_clears_the_rule(
        self, tmp_path: Path, added: str, finding: Finding
    ) -> None:
        """Doing what a hint says makes its rule stop firing."""
        assert finding in CommunityCheck().run(tmp_path, {}).findings
        hint = CommunityCheck.hints[finding.code]
        assert hint.startswith(f"Add a {added} file to the project root")
        write(tmp_path, added)
        assert finding not in CommunityCheck().run(tmp_path, {}).findings
