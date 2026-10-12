"""Tests for changelog check."""

import os
import sys
from pathlib import Path

import pytest

from pycmdcheck.checks import changelog
from pycmdcheck.checks.changelog import ChangelogCheck
from pycmdcheck.checks.metadata import MetadataCheck
from pycmdcheck.pyproject_reader import clear_cache
from pycmdcheck.results import CheckStatus, Finding
from tests.helpers import codes

ENTRIES = "# Changelog\n\n## 0.1.0 (2024-01-01)\n\n- Initial release of the package.\n"


def write(root: Path, relative: str, content: str | bytes = ENTRIES) -> Path:
    """Write *content* to *relative* under *root*, creating directories."""
    target = root / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        target.write_bytes(content)
    else:
        target.write_text(content, encoding="utf-8")
    return target


class TestChangelogCheck:
    """Tests for ChangelogCheck."""

    @pytest.mark.parametrize("stem", ["CHANGELOG", "CHANGES", "NEWS", "HISTORY"])
    @pytest.mark.parametrize("extension", [".md", ".rst", ".txt", ""])
    def test_classic_root_names(
        self, tmp_path: Path, stem: str, extension: str
    ) -> None:
        """The sixteen names accepted since 0.1.0 -> OK."""
        write(tmp_path, stem + extension)
        result = ChangelogCheck().run(tmp_path, {})
        assert result.name == "changelog"
        assert result.status == CheckStatus.OK
        assert result.message == "Changelog present"
        assert result.details == [f"Found: {stem}{extension}"]
        assert result.findings == []

    @pytest.mark.parametrize(
        "relative",
        [
            # Letter case differs from the conventional spelling.
            "changelog.md",
            "Changelog.md",
            "ChangeLog",
            "CHANGELOG.MD",
            # More names for the same thing.
            "RELEASES.md",
            "RELEASE_NOTES.md",
            "release-notes.rst",
            "RELEASENOTES.md",
            "WHATSNEW.md",
            "WHATS-NEW.md",
            "WHATS_NEW.md",
            # Kept with the documentation. These are not graded on length.
            "docs/changelog.md",
            "docs/changes.rst",
            "docs/release-notes.md",
            "doc/whats-new.rst",
            "doc/changes.md",
        ],
    )
    def test_name_and_location_variants(self, tmp_path: Path, relative: str) -> None:
        """Other spellings and docs locations -> OK, reported as on disk."""
        content = "See the root file.\n" if "/" in relative else ENTRIES
        write(tmp_path, relative, content)
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.details == [f"Found: {relative}"]

    @pytest.mark.parametrize(
        ("relative", "shown"),
        [
            ("changes/123.feature.rst", "changes/"),
            ("newsfragments/45.bugfix", "newsfragments/"),
            ("changelog.d/20240101_fix.md", "changelog.d/"),
            ("doc/whats_new/v1.5.rst", "doc/whats_new/"),
            ("doc/whatsnew/v0.12.rst", "doc/whatsnew/"),
            ("docs/release-notes/1.0.md", "docs/release-notes/"),
        ],
    )
    def test_directories_of_notes(
        self, tmp_path: Path, relative: str, shown: str
    ) -> None:
        """News fragment and per-release directories -> OK."""
        write(tmp_path, relative, "Fixed a bug.\n")
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.details == [f"Found: {shown}"]

    def test_python_package_is_not_a_directory_of_notes(self, tmp_path: Path) -> None:
        """A flat-layout package named changes is not a changelog."""
        write(tmp_path, "changes/__init__.py", "")
        write(tmp_path, "changes/core.py", "VALUE = 1\n")
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No changelog found"
        assert codes(result) == ["changelog.missing"]

        # The package does not hide real news fragments kept elsewhere.
        write(tmp_path, "newsfragments/45.bugfix", "Fixed a bug.\n")
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.details == ["Found: newsfragments/"]

    @pytest.mark.parametrize(
        "label",
        ["Changelog", "changes", "What's New", "History", "Release Notes"],
    )
    def test_project_url_label(self, tmp_path: Path, label: str) -> None:
        """A well-known changelog label in [project.urls] -> OK."""
        write(
            tmp_path,
            "pyproject.toml",
            '[project]\nname = "x"\n\n[project.urls]\n'
            f'"{label}" = "https://example.com/releases"\n',
        )
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert any(label in d for d in result.details)

    def test_no_changelog(self, temp_package: Path) -> None:
        """No changelog -> NOTE that says where the check looked."""
        result = ChangelogCheck().run(temp_package, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No changelog found"
        hint = ChangelogCheck.hints["changelog.missing"]
        assert result.findings == [
            Finding("changelog.missing", "No changelog found", hint=hint)
        ]
        text = "\n".join(result.details)
        for place in ("CHANGELOG", "docs/", "newsfragments/", "[project.urls]"):
            assert place in text

    @pytest.mark.parametrize("content", ["", "# Changelog\n", "x" * 49])
    def test_short_changelog_is_note(self, tmp_path: Path, content: str) -> None:
        """An empty or heading-only changelog ranks no lower than none."""
        write(tmp_path, "CHANGELOG.md", content)
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "Changelog file is too short"
        assert result.details[0] == "Found: CHANGELOG.md"
        assert "50 characters" in result.details[1]
        assert result.findings == [
            Finding(
                "changelog.too-short",
                "CHANGELOG.md has fewer than 50 characters",
                hint="Write at least 50 characters of release notes in CHANGELOG.md.",
            )
        ]

    def test_fifty_characters_is_enough(self, tmp_path: Path) -> None:
        """The length threshold is inclusive."""
        write(tmp_path, "CHANGELOG.md", "x" * 50)
        assert ChangelogCheck().run(tmp_path, {}).status == CheckStatus.OK

    @pytest.mark.parametrize(
        ("first", "full"),
        [
            ("See CHANGES.rst\n", "CHANGES.rst"),
            ("See the docs.\n", "docs/changelog.md"),
            # None stands for a CHANGELOG.md that cannot be read.
            (None, "CHANGES.rst"),
            (None, "docs/changelog.md"),
        ],
        ids=["stub", "stub-and-docs", "unreadable", "unreadable-and-docs"],
    )
    def test_stub_does_not_hide_a_full_file(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        first: str | None,
        full: str,
    ) -> None:
        """A CHANGELOG.md that is a stub or cannot be read, beside a full file -> OK."""
        write(tmp_path, "CHANGELOG.md", first or ENTRIES)
        write(tmp_path, full)
        read = changelog.read_text_capped

        def refuse(path: Path) -> str:
            if first is None and path.name == "CHANGELOG.md":
                raise PermissionError(13, "Permission denied", str(path))
            return read(path)

        monkeypatch.setattr(changelog, "read_text_capped", refuse)
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert result.details == [f"Found: {full}"]

    @pytest.mark.parametrize(
        "content",
        [
            (
                "Changelog\n\n0.1.0\n\n- Première version, corrigée par André.\n" * 2
            ).encode("latin-1"),
            ENTRIES.encode("utf-16"),
        ],
        ids=["latin-1", "utf-16"],
    )
    def test_encoding_is_not_graded(self, tmp_path: Path, content: bytes) -> None:
        """A changelog that is not UTF-8 is judged on its content -> OK."""
        write(tmp_path, "CHANGELOG.md", content)
        assert ChangelogCheck().run(tmp_path, {}).status == CheckStatus.OK

    def test_directory_named_like_a_file(self, tmp_path: Path) -> None:
        """A CHANGELOG.md directory is the same as no changelog."""
        (tmp_path / "CHANGELOG.md").mkdir()
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No changelog found"
        assert codes(result) == ["changelog.missing"]
        assert str(tmp_path) not in "\n".join(result.details)

    @pytest.mark.skipif(sys.platform == "win32", reason="needs /dev/null")
    def test_link_to_a_device_is_not_a_changelog(self, tmp_path: Path) -> None:
        """CHANGELOG.md linked to a device is ignored, like a directory."""
        (tmp_path / "CHANGELOG.md").symlink_to("/dev/null")
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No changelog found"
        assert codes(result) == ["changelog.missing"]

    @pytest.mark.parametrize(
        "pyproject",
        ["[project\nname = ", '[project]\nname = "x"\nurls = ["Changelog"]\n'],
        ids=["invalid-toml", "urls-not-a-table"],
    )
    def test_malformed_pyproject_is_tolerated(
        self, tmp_path: Path, pyproject: str
    ) -> None:
        """A pyproject.toml that cannot be used does not stop the check."""
        write(tmp_path, "pyproject.toml", pyproject)
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "No changelog found"
        assert codes(result) == ["changelog.missing"]

    @pytest.mark.parametrize(
        ("name", "stub"),
        [("HISTORY.rst", None), ("CHANGELOG.md", "CHANGES.rst")],
        ids=["alone", "beside-a-stub"],
    )
    def test_unreadable_changelog_warns(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        name: str,
        stub: str | None,
    ) -> None:
        """A genuine read error -> WARNING with a relative path.

        A stub beside the file is named in the details and does not take over.
        """
        write(tmp_path, name)
        if stub:
            write(tmp_path, stub, "See CHANGELOG.md\n")
        read = changelog.read_text_capped

        def refuse(path: Path) -> str:
            if path.name == name:
                raise PermissionError(13, "Permission denied", str(path))
            return read(path)

        monkeypatch.setattr(changelog, "read_text_capped", refuse)
        result = ChangelogCheck().run(tmp_path, {})
        assert result.status == CheckStatus.WARNING
        assert result.message == "Could not read changelog file"
        assert result.details == [
            f"Could not read {name}: Permission denied",
            *([f"{stub} has fewer than 50 characters"] if stub else []),
        ]
        # The command in the hint can be typed as it is shown.
        assert result.findings == [
            Finding(
                "changelog.unreadable",
                f"Could not read {name}: Permission denied",
                CheckStatus.WARNING,
                "Give yourself read permission on the changelog file, for example "
                f"with chmod u+r {name}.",
            )
        ]
        # The hint the rule carries is the same sentence, for CHANGELOG.md.
        named = (result.findings[0].hint or "").replace(name, "CHANGELOG.md")
        assert named == ChangelogCheck.hints["changelog.unreadable"]

    def test_following_the_hint_clears_the_rule(self, tmp_path: Path) -> None:
        """Doing what a hint says makes its rule stop firing.

        The project has no pyproject.toml, so the hint names no link.
        """
        result = ChangelogCheck().run(tmp_path, {})
        said = "Add a CHANGELOG.md file with your release notes to the project root."
        assert [(f.code, f.hint) for f in result.findings] == [
            ("changelog.missing", said)
        ]
        assert len(ENTRIES) >= 50
        write(tmp_path, "CHANGELOG.md")
        assert codes(ChangelogCheck().run(tmp_path, {})) == []

    @pytest.mark.parametrize(
        ("pyproject", "table"),
        [
            ('[project]\nname = "x"\nversion = "1"\n', "[project.urls]"),
            ('[tool.poetry]\nname = "x"\nversion = "1"\n', "[tool.poetry.urls]"),
        ],
    )
    def test_link_is_advised_in_the_table_the_project_has(
        self, tmp_path: Path, pyproject: str, table: str
    ) -> None:
        """The second way of the hint clears the rule and upsets no other check."""
        write(tmp_path, "pyproject.toml", pyproject)
        before = MetadataCheck().run(tmp_path, {})
        result = ChangelogCheck().run(tmp_path, {})
        (finding,) = result.findings
        assert finding.hint == (
            "Add a CHANGELOG.md file with your release notes to the project root, "
            f"or link them as Changelog in {table} of pyproject.toml."
        )
        # The details name the same table as the place that was looked in.
        assert result.details[1].endswith(f" and for a changelog link in {table}")
        link = f'{table}\nChangelog = "https://example.com/releases"\n'
        write(tmp_path, "pyproject.toml", pyproject + link)
        clear_cache()
        assert codes(ChangelogCheck().run(tmp_path, {})) == []
        after = MetadataCheck().run(tmp_path, {})
        assert before.status == after.status
        assert set(codes(after)) <= set(codes(before))

    def test_no_link_is_advised_without_a_metadata_table(self, tmp_path: Path) -> None:
        """A [project.urls] table there would start a [project] with no name."""
        write(tmp_path, "pyproject.toml", '[build-system]\nrequires = ["setuptools"]\n')
        result = ChangelogCheck().run(tmp_path, {})
        assert "pyproject.toml" not in (result.findings[0].hint or "")
        # No table of links was read, so the details claim no look for a link.
        assert result.details[1].endswith("under docs/ or doc/)")

    @pytest.mark.parametrize(
        "stub", [None, "See CHANGELOG.md\n"], ids=["alone", "beside-a-stub"]
    )
    def test_read_permission_clears_unreadable(
        self, tmp_path: Path, stub: str | None
    ) -> None:
        """So does the hint for a file that cannot be read, also beside a stub."""
        target = write(tmp_path, "CHANGELOG.md")
        if stub:
            write(tmp_path, "CHANGES.rst", stub)
        target.chmod(0)
        if os.access(target, os.R_OK):
            pytest.skip("a file with no permissions can still be read here")
        assert codes(ChangelogCheck().run(tmp_path, {})) == ["changelog.unreadable"]
        target.chmod(0o400)  # chmod u+r CHANGELOG.md
        assert codes(ChangelogCheck().run(tmp_path, {})) == []
