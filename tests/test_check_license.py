"""Tests for the license check."""

import json
import os
import runpy
from pathlib import Path
from unittest.mock import patch

import pytest

from pycmdcheck import _osi_licenses
from pycmdcheck._osi_licenses import OSI_APPROVED_LICENSES, SPDX_LICENSE_IDS
from pycmdcheck.checks.license import LicenseCheck
from pycmdcheck.results import CheckResult, CheckStatus
from tests.helpers import codes, write_files

MIT_TEXT = (
    "Copyright (c) 2024 Jane Doe\n\n"
    "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
    'of this software and associated documentation files (the "Software"), to deal\n'
    "in the Software without restriction.\n"
)

# Openings of the canonical texts, each with the sentence that names another
# license and used to cause a wrong label.
GPL3_TEXT = (
    "                    GNU GENERAL PUBLIC LICENSE\n"
    "                       Version 3, 29 June 2007\n\n"
    "  13. Use with the GNU Affero General Public License.\n\n"
    "If this is what you want to do, use the GNU Lesser General\n"
    "Public License instead of this License.\n"
)
MPL2_TEXT = (
    "Mozilla Public License Version 2.0\n"
    "==================================\n\n"
    '1.12. "Secondary License"\n'
    "    means either the GNU General Public License, Version 2.0, the GNU\n"
    "    Lesser General Public License, Version 2.1, the GNU Affero General\n"
    "    Public License, Version 3.0, or any later versions of those licenses.\n"
)
LICENSE_TEXTS = {
    "AGPL-3.0": (
        "                    GNU AFFERO GENERAL PUBLIC LICENSE\n"
        "                       Version 3, 19 November 2007\n\n"
        "  13. Remote Network Interaction; Use with the GNU General Public License.\n"
    ),
    "LGPL-3.0": (
        "                   GNU LESSER GENERAL PUBLIC LICENSE\n"
        "                       Version 3, 29 June 2007\n\n"
        "  This version of the GNU Lesser General Public License incorporates\n"
        "the terms and conditions of version 3 of the GNU General Public\n"
        "License, supplemented by the additional permissions listed below.\n\n"
        "                    GNU GENERAL PUBLIC LICENSE\n"
        "                       Version 3, 29 June 2007\n"
    ),
    "LGPL-2.1": (
        "                  GNU LESSER GENERAL PUBLIC LICENSE\n"
        "                       Version 2.1, February 1999\n\n"
        "[This is the first released version of the Lesser GPL.  It also counts\n"
        " as the successor of the GNU Library Public License, version 2, hence\n"
        " the version number 2.1.]\n\n"
        "  It is quite different from the ordinary GNU General Public License.\n"
    ),
    "GPL-3.0": GPL3_TEXT,
    "GPL-2.0": (
        "                    GNU GENERAL PUBLIC LICENSE\n"
        "                       Version 2, June 1991\n\n"
        "If this is what you want to do, use the GNU Lesser General\n"
        "Public License instead of this License.\n"
    ),
    "MPL-2.0": MPL2_TEXT,
    "EPL-2.0": (
        "Eclipse Public License - v 2.0\n\n"
        '"Secondary License" means either the GNU General Public License,\n'
        "Version 2.0, or any later versions of that license.\n"
    ),
    "Apache-2.0": (
        "                                 Apache License\n"
        "                           Version 2.0, January 2004\n"
        "                        http://www.apache.org/licenses/\n\n"
        "   TERMS AND CONDITIONS FOR USE, REPRODUCTION, AND DISTRIBUTION\n"
    ),
    "MIT": MIT_TEXT,
    "BSD-3-Clause": (
        "Copyright 2014 Pallets\n\n"
        "Redistribution and use in source and binary forms, with or without\n"
        "modification, are permitted provided that the following conditions are\n"
        "met:\n\n"
        "3.  Neither the name of the copyright holder nor the names of its\n"
        "    contributors may be used to endorse or promote products derived from\n"
        "    this software without specific prior written permission.\n"
    ),
    "BSD-2-Clause": (
        "Copyright (c) 2020, Jane Doe\n\n"
        "Redistribution and use in source and binary forms, with or without\n"
        "modification, are permitted provided that the following conditions are\n"
        "met:\n\n"
        "1. Redistributions of source code must retain the above copyright notice.\n"
    ),
    "ISC": (
        "Copyright (c) 2020, Jane Doe\n\n"
        "Permission to use, copy, modify, and/or distribute this software for any\n"
        "purpose with or without fee is hereby granted, provided that the above\n"
        "copyright notice and this permission notice appear in all copies.\n"
    ),
    "Unlicense": (
        "This is free and unencumbered software released into the public domain.\n\n"
        "Anyone is free to copy, modify, publish, use, compile, sell, or distribute\n"
    ),
}


def make_project(
    root: Path, project: str = "", files: dict[str, str] | None = None
) -> Path:
    """Write a pyproject.toml holding *project* lines, plus the given files."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "pkg"\nversion = "0.1.0"\n{project}\n', encoding="utf-8"
    )
    return write_files(root, files or {})


def run(root: Path) -> CheckResult:
    return LicenseCheck().run(root, {})


class TestLicenseOSI:
    """Tests for OSI-approved license validation."""

    def test_mit_is_osi_approved(self, temp_package: Path) -> None:
        """MIT license identified as OSI-approved."""
        result = run(temp_package)
        assert result.status == CheckStatus.OK
        assert codes(result) == []
        assert "License type: MIT" in result.details
        assert "License is OSI-approved (MIT)" in result.details

    def test_unknown_license_noted(self, tmp_path: Path) -> None:
        """A word that is not an SPDX identifier is named in neutral wording."""
        make_project(
            tmp_path,
            'license = "Custom-License"',
            {"LICENSE": "Custom license terms apply to this software.\n" * 3},
        )
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert "License type: Custom-License" in result.details
        assert any(
            d.startswith("Not in pycmdcheck's list") and d.endswith(": Custom-License")
            for d in result.details
        )

    def test_license_file_table_not_treated_as_spdx(self, tmp_path: Path) -> None:
        """license = {file = ...} must not produce a false 'not OSI-approved' NOTE."""
        make_project(
            tmp_path,
            'license = {file = "LICENSE"}',
            {"LICENSE": "Some license text that is reasonably long.\n" * 3},
        )
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert "License type: Unknown/Custom" in result.details
        assert not any("OSI" in d for d in result.details)

    @pytest.mark.parametrize(
        ("project", "approved", "unlisted"),
        [
            ('license = "MIT OR Apache-2.0"', "MIT, Apache-2.0", None),
            ('license = "mit"', "mit", None),
            ('license = {text = "BSD-3-Clause"}', "BSD-3-Clause", None),
            (
                'license = "GPL-2.0-only WITH Classpath-exception-2.0"',
                "GPL-2.0-only",
                None,
            ),
            ('license = "MIT AND LicenseRef-Vendor"', "MIT", "LicenseRef-Vendor"),
            # One approved license is enough.
            ('license = "CC-BY-NC-4.0 OR MIT"', "MIT", "CC-BY-NC-4.0"),
            ('license = "UPL-1.0"', "UPL-1.0", None),
            # Deprecated identifiers are still declared, also with a plus sign.
            ('license = "GPL-3.0+"', "GPL-3.0", None),
            # Approved by hand: the SPDX list does not mark it.
            ('license = "PSF-2.0"', "PSF-2.0", None),
            (
                'classifiers = ["License :: OSI Approved :: BSD License"]',
                "License :: OSI Approved :: BSD License",
                None,
            ),
            # Free text is not graded, so the classifier decides.
            (
                'license = {text = "BSD License"}\n'
                'classifiers = ["License :: OSI Approved :: BSD License"]',
                "License :: OSI Approved :: BSD License",
                None,
            ),
        ],
    )
    def test_declared_license_osi_status(
        self, tmp_path: Path, project: str, approved: str, unlisted: str | None
    ) -> None:
        """Each id of a declared expression is looked up on its own."""
        make_project(tmp_path, project, {"LICENSE": "Terms of this project.\n" * 5})
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert codes(result) == []
        assert f"License is OSI-approved ({approved})" in result.details
        listed = [d for d in result.details if d.startswith("Not in pycmdcheck's")]
        assert [d.rpartition(": ")[2] for d in listed] == (
            [unlisted] if unlisted else []
        )

    @pytest.mark.parametrize(
        ("project", "named"),
        [
            ('license = "CC-BY-NC-4.0"', "CC-BY-NC-4.0"),
            ('license = "cc-by-nc-4.0"', "cc-by-nc-4.0"),
            ('license = {text = "CC0-1.0"}', "CC0-1.0"),
            ('license = "LicenseRef-Proprietary"', "LicenseRef-Proprietary"),
            (
                'license = "CC-BY-4.0 OR LicenseRef-Vendor"',
                "CC-BY-4.0, LicenseRef-Vendor",
            ),
            # MIT stands where an exception belongs, so it is not a license here.
            ('license = "CC-BY-NC-4.0 WITH MIT"', "CC-BY-NC-4.0"),
            # A classifier does not outvote a declared expression.
            (
                'license = "CC-BY-NC-4.0"\n'
                'classifiers = ["License :: OSI Approved :: MIT License"]',
                "CC-BY-NC-4.0",
            ),
        ],
    )
    def test_expression_without_an_approved_license_is_a_note(
        self, tmp_path: Path, project: str, named: str
    ) -> None:
        """An SPDX expression that names no approved license is a NOTE."""
        make_project(tmp_path, project, {"LICENSE": "Terms of this project.\n" * 5})
        result = run(tmp_path)
        assert result.status == CheckStatus.NOTE
        assert result.message == "Declared license is not OSI-approved"
        assert codes(result) == ["license.not-osi-approved"]
        said = f"Declared license is not OSI-approved: {named}"
        assert result.findings[0].message == said
        assert result.details[-1] == said
        assert not any(d.startswith("License is OSI-approved") for d in result.details)

    @pytest.mark.parametrize(
        "project",
        [
            # Common legacy values that look like an identifier and are not one.
            'license = "BSD"',
            'license = {text = "GPLv3"}',
            'license = "Proprietary"',
            'license = "GPLv3 OR CC-BY-NC-4.0"',
            'license = {text = "Creative Commons Attribution-NonCommercial"}',
            'classifiers = ["License :: Other/Proprietary License"]',
            "",
        ],
    )
    def test_free_text_or_no_declared_license_is_not_graded(
        self, tmp_path: Path, project: str
    ) -> None:
        """Only a real SPDX expression can turn the result into a NOTE."""
        make_project(tmp_path, project, {"LICENSE": "Terms of this project.\n" * 5})
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert result.message == "License file present"
        assert codes(result) == []

    def test_poetry_license_is_read(self, tmp_path: Path) -> None:
        """A legacy Poetry license string counts as declared."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "pkg"\nversion = "0.1.0"\nlicense = "MIT"\n'
        )
        (tmp_path / "LICENSE").write_text("Terms of this project.\n" * 5)
        assert "License is OSI-approved (MIT)" in run(tmp_path).details


class TestLicenseFileDiscovery:
    """Tests for where a license file is looked for."""

    @pytest.mark.parametrize(
        "name",
        [
            "LICENSE",
            "LICENSE.txt",
            "LICENCE",
            "license",
            "License.md",
            "COPYING",
            "COPYING.LESSER",
            "LICENSE-MIT",
            "LICENSE_MIT",
            "UNLICENSE",
        ],
    )
    def test_accepted_file_names(self, tmp_path: Path, name: str) -> None:
        """Every conventional name is found, whatever its letter case."""
        (tmp_path / name).write_text(MIT_TEXT)
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert result.message == "License file present"
        assert f"Found license file: {name}" in result.details

    @pytest.mark.parametrize(
        ("project", "files", "found"),
        [
            # matplotlib: LICENSE is a directory named by license.file.
            (
                'license = {file = "LICENSE/LICENSE"}',
                {"LICENSE/LICENSE": MIT_TEXT, "LICENSE/LICENSE_AMSFONTS": "OFL"},
                "LICENSE/LICENSE",
            ),
            (
                "",
                {"LICENSE/LICENSE_FONT": "x", "LICENSE/LICENSE": MIT_TEXT},
                "LICENSE/LICENSE",
            ),
            # REUSE layout.
            ("", {"LICENSES/MIT.txt": MIT_TEXT}, "LICENSES/MIT.txt"),
            ("", {"licenses/MIT.txt": MIT_TEXT}, "licenses/MIT.txt"),
            (
                'license-files = ["legal/*.txt"]',
                {"legal/terms.txt": MIT_TEXT},
                "legal/terms.txt",
            ),
            (
                'license = {file = "legal/terms.txt"}',
                {"legal/terms.txt": MIT_TEXT},
                "legal/terms.txt",
            ),
            # A short pointer file does not hide the real text.
            (
                "",
                {"LICENSE": "See LICENSES/", "LICENSES/MIT.txt": MIT_TEXT},
                "LICENSES/MIT.txt",
            ),
            (
                'license-files = ["AUTHORS", "LICENSES/*"]',
                {"AUTHORS": "Jane", "LICENSES/MIT.txt": MIT_TEXT},
                "LICENSES/MIT.txt",
            ),
        ],
    )
    def test_license_locations(
        self, tmp_path: Path, project: str, files: dict[str, str], found: str
    ) -> None:
        """Directories and files named in pyproject.toml are license locations."""
        result = run(make_project(tmp_path, project, files))
        assert result.status == CheckStatus.OK
        assert result.details[0] == f"Found license file: {found}"

    def test_license_directory_beside_file(self, tmp_path: Path) -> None:
        """A LICENSE/ directory does not hide LICENSE.txt."""
        make_project(tmp_path, "", {"LICENSE/extra.txt": "x", "LICENSE.txt": MIT_TEXT})
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert "Found license file: LICENSE.txt" in result.details

    @pytest.mark.parametrize(
        "project",
        [
            'license-files = ["../outside.txt"]',
            'license = {file = "../outside.txt"}',
            'license = {file = "/etc/hosts"}',
            'license-files = ["/etc/*"]',
            'license-files = [""]',
            "license-files = 5",
            "license = {file = 5}",
            'classifiers = "License :: OSI Approved"',
        ],
    )
    def test_unusable_declared_paths_find_nothing(
        self, tmp_path: Path, project: str
    ) -> None:
        """Declared paths that leave the project, or are malformed, are ignored."""
        root = make_project(tmp_path / "repo", project, {"../outside.txt": MIT_TEXT})
        result = run(root)
        assert result.status == CheckStatus.WARNING
        assert result.message == "No license file found"
        assert codes(result) == ["license.missing"]
        assert result.details[0].startswith("Looked for LICENSE*, LICENCE*, COPYING*")

    def test_missing_file_names_declared_license(self, tmp_path: Path) -> None:
        """Without a file, a declared SPDX id is mentioned and the WARNING stays."""
        result = run(make_project(tmp_path, 'license = "MIT"'))
        assert result.status == CheckStatus.WARNING
        assert result.message == "No license file found"
        assert codes(result) == ["license.missing"]
        assert result.findings[0].message == "No license file found"
        assert result.details[1:] == [
            "pyproject.toml declares license = MIT, "
            "but the repository has no license file"
        ]

    @pytest.mark.parametrize(
        ("project", "declared"),
        [
            ('license = "MIT/Apache-2.0"', "MIT/Apache-2.0"),
            ('license = {text = "MIT License"}', "MIT License"),
            ('license = {text = "MIT License\\nSee the LICENSE file"}', None),
        ],
        ids=["slash", "text", "two-lines"],
    )
    def test_missing_file_shows_declared_value_that_is_no_expression(
        self, tmp_path: Path, project: str, declared: str | None
    ) -> None:
        """Without a file, the declared value is the only word on the license."""
        result = run(make_project(tmp_path, project))
        shown = f"Declared license: {declared} (not an SPDX expression)"
        assert result.details[1:] == ([shown] if declared else [])
        assert result.details[0].startswith("Looked for LICENSE*, LICENCE*, COPYING*")
        assert result.status == CheckStatus.WARNING
        assert codes(result) == ["license.missing"]

    @pytest.mark.parametrize(
        ("files", "message", "code"),
        [
            ({}, "No license file found", "license.missing"),
            (
                {"LICENSE": "See the website.\n"},
                "License file appears empty or incomplete",
                "license.too-short",
            ),
        ],
    )
    def test_declared_license_is_graded_without_a_usable_file(
        self, tmp_path: Path, files: dict[str, str], message: str, code: str
    ) -> None:
        """A license that is not approved is reported beside the file problem."""
        result = run(make_project(tmp_path, 'license = "CC-BY-NC-4.0"', files))
        assert (result.status, result.message) == (CheckStatus.WARNING, message)
        assert codes(result) == [code, "license.not-osi-approved"]
        statuses = [finding.status for finding in result.findings]
        assert statuses == [CheckStatus.WARNING, CheckStatus.NOTE]
        said = "Declared license is not OSI-approved: CC-BY-NC-4.0"
        assert [finding.message for finding in result.findings] == [message, said]
        assert result.details[-1] == said

    def test_stems_and_directories_drive_the_search(self, tmp_path: Path) -> None:
        """LICENSE_STEMS and LICENSE_DIRS are what a subclass overrides."""

        class TermsCheck(LicenseCheck):
            LICENSE_STEMS = ["TERMS"]
            LICENSE_DIRS = ["legal"]

        make_project(tmp_path, "", {"LICENSE": MIT_TEXT, "legal/mit.txt": MIT_TEXT})
        result = TermsCheck().run(tmp_path, {})
        assert result.details[0] == "Found license file: legal/mit.txt"
        # The unused regex stays at its 0.1.1 meaning.
        assert not LicenseCheck.LICENSE_FILE_RE.match("UNLICENSE")


class TestLicenseReading:
    """Tests for how the license file is read and measured."""

    def test_link_out_of_the_package_is_read(self, tmp_path: Path) -> None:
        """A license linked from the repository root is accepted (arrow, cudf)."""
        (tmp_path / "LICENSE.txt").write_text(MIT_TEXT)
        package = tmp_path / "python"
        package.mkdir()
        try:
            (package / "LICENSE.txt").symlink_to(tmp_path / "LICENSE.txt")
        except OSError:
            pytest.skip("symlinks are not available")
        result = run(package)
        assert result.status == CheckStatus.OK
        assert "License type: MIT (guessed from the license text)" in result.details

    @pytest.mark.parametrize("encoding", ["latin-1", "utf-16"])
    def test_encoding_is_not_graded(self, tmp_path: Path, encoding: str) -> None:
        """A license that is not UTF-8 is judged on its content."""
        text = MIT_TEXT.replace("Jane Doe", "José García")
        (tmp_path / "LICENSE").write_bytes(text.encode(encoding))
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert "License type: MIT (guessed from the license text)" in result.details

    def test_unreadable_file_warns(self, tmp_path: Path) -> None:
        """A file that cannot be opened is a WARNING that gives the reason."""
        (tmp_path / "COPYING.txt").write_text(MIT_TEXT)
        with patch(
            "pycmdcheck.checks.license.read_text_capped",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            result = run(tmp_path)
        assert result.status == CheckStatus.WARNING
        assert result.message == "Could not read license file"
        assert codes(result) == ["license.unreadable"]
        assert result.details == ["Could not read COPYING.txt: Permission denied"]
        # The command in the hint can be typed as it is shown.
        assert result.findings[0].hint == (
            "Give yourself read permission on the license file, for example "
            "with chmod u+r COPYING.txt."
        )

    @pytest.mark.parametrize(
        ("project", "declared"),
        [
            ('license = "MIT"', ["License type: MIT"]),
            (
                'license = "MIT/Apache-2.0"',
                ["Declared license: MIT/Apache-2.0 (not an SPDX expression)"],
            ),
            ("", []),
        ],
        ids=["expression", "no expression", "nothing declared"],
    )
    def test_unreadable_file_shows_declared_license(
        self, tmp_path: Path, project: str, declared: list[str]
    ) -> None:
        """A declared expression or value is shown as it is beside a file read."""
        make_project(tmp_path, project, {"LICENSE": MIT_TEXT})
        with patch(
            "pycmdcheck.checks.license.read_text_capped",
            side_effect=PermissionError(13, "Permission denied"),
        ):
            result = run(tmp_path)
        assert result.details == [
            "Could not read LICENSE: Permission denied",
            *declared,
        ]
        assert codes(result) == ["license.unreadable"]

    @pytest.mark.parametrize(
        ("length", "status", "message", "expected"),
        [
            (
                49,
                CheckStatus.WARNING,
                "License file appears empty or incomplete",
                ["license.too-short"],
            ),
            (50, CheckStatus.OK, "License file present", []),
        ],
    )
    def test_minimum_length_boundary(
        self,
        tmp_path: Path,
        length: int,
        status: CheckStatus,
        message: str,
        expected: list[str],
    ) -> None:
        """Fewer than 50 characters is incomplete, 50 is enough."""
        (tmp_path / "LICENSE").write_text("x" * length + "\n")
        result = run(tmp_path)
        assert (result.status, result.message) == (status, message)
        assert codes(result) == expected
        assert result.details[0] == "Found license file: LICENSE"


class TestLicenseType:
    """Tests for the reported license type."""

    @pytest.mark.parametrize("expected", sorted(LICENSE_TEXTS))
    def test_type_guessed_from_canonical_text(
        self, tmp_path: Path, expected: str
    ) -> None:
        """Without a declared license the type is guessed from the text."""
        (tmp_path / "LICENSE").write_text(LICENSE_TEXTS[expected])
        result = run(tmp_path)
        assert result.status == CheckStatus.OK
        assert (
            f"License type: {expected} (guessed from the license text)"
            in result.details
        )
        assert f"License is OSI-approved ({expected})" in result.details

    @pytest.mark.parametrize(
        ("declared", "filename", "text"),
        [
            ("MPL-2.0", "LICENSE", MPL2_TEXT),
            ("LGPL-3.0-or-later", "COPYING", GPL3_TEXT),
        ],
    )
    def test_declared_license_is_shown_instead_of_a_guess(
        self, tmp_path: Path, declared: str, filename: str, text: str
    ) -> None:
        """A declared SPDX expression is the license type, with no second opinion."""
        make_project(tmp_path, f'license = "{declared}"', {filename: text})
        assert run(tmp_path).details == [
            f"Found license file: {filename}",
            f"License type: {declared}",
            f"License is OSI-approved ({declared})",
        ]

    @pytest.mark.parametrize(
        ("project", "declared"),
        [
            ('license = "MIT/Apache-2.0"', "MIT/Apache-2.0"),
            ('license = {text = "MIT License"}', "MIT License"),
            (
                'license = "Released under the MIT license."',
                "Released under the MIT license.",
            ),
            # One line of 200 characters is the longest value that is shown.
            (f'license = "MIT {"x" * 196}"', f"MIT {'x' * 196}"),
            (f'license = "MIT {"x" * 197}"', None),
            ('license = {text = "MIT License\\nSee the LICENSE file"}', None),
            # A line break at the end and an escape character are not shown.
            ('license = {text = "MIT License\\n"}', "MIT License"),
            ('license = "\\u001b[31mMIT License"', "\\x1b[31mMIT License"),
        ],
        ids=["slash", "text", "sentence", "200", "201", "two-lines", "ends", "escape"],
    )
    def test_license_text_in_metadata_is_not_an_expression(
        self, tmp_path: Path, project: str, declared: str | None
    ) -> None:
        """A declared value that is not an SPDX expression is shown, not graded."""
        result = run(make_project(tmp_path, project, {"LICENSE": MIT_TEXT}))
        shown = f"Declared license: {declared} (not an SPDX expression)"
        assert result.details == [
            "Found license file: LICENSE",
            *([shown] if declared else []),
            "License type: MIT (guessed from the license text)",
            "License is OSI-approved (MIT)",
        ]
        assert result.status == CheckStatus.OK
        assert result.message == "License file present"
        assert codes(result) == []


class TestHints:
    """Doing what a hint says makes its rule stop firing."""

    @pytest.mark.parametrize(
        ("code", "before", "after", "said"),
        [
            (
                "license.missing",
                ("", {}),
                ("", {"LICENSE": MIT_TEXT}),
                "Add a file named LICENSE with the full text of your license",
            ),
            (
                "license.too-short",
                ("", {"LICENSE": "See the website.\n"}),
                ("", {"LICENSE": MIT_TEXT}),
                "Put the full text of your license, at least 50 characters, in",
            ),
            # The license itself changes: its text and what is declared.
            (
                "license.not-osi-approved",
                ('license = "CC-BY-NC-4.0"', {"LICENSE": "Creative Commons " * 9}),
                ('license = "MIT"', {"LICENSE": MIT_TEXT}),
                "such as MIT or Apache-2.0, then put its text in the license file "
                "and its identifier in license in pyproject.toml.",
            ),
        ],
    )
    def test_following_the_hint_clears_the_rule(
        self,
        tmp_path: Path,
        code: str,
        before: tuple[str, dict[str, str]],
        after: tuple[str, dict[str, str]],
        said: str,
    ) -> None:
        assert codes(run(make_project(tmp_path, *before))) == [code]
        assert said in LicenseCheck.hints[code]
        assert len(MIT_TEXT) >= 50
        assert codes(run(make_project(tmp_path, *after))) == []

    def test_read_permission_clears_unreadable(self, tmp_path: Path) -> None:
        license_file = tmp_path / "LICENSE"
        license_file.write_text(MIT_TEXT)
        license_file.chmod(0)
        if os.access(license_file, os.R_OK):
            pytest.skip("a file with no permissions can still be read here")
        assert codes(run(tmp_path)) == ["license.unreadable"]
        license_file.chmod(0o400)  # chmod u+r LICENSE
        assert codes(run(tmp_path)) == []


class TestOsiLicenseList:
    """Tests for the generated list of OSI-approved licenses."""

    def test_generated_sets_know_these_licenses(self) -> None:
        """A few licenses whose standing is not in doubt."""
        approved = {"MIT", "Apache-2.0", "BSD-3-Clause", "UPL-1.0", "GPL-3.0"}
        assert approved <= OSI_APPROVED_LICENSES < SPDX_LICENSE_IDS
        assert "CC-BY-NC-4.0" in SPDX_LICENSE_IDS - OSI_APPROVED_LICENSES
        assert not any(spdx.startswith("LicenseRef-") for spdx in SPDX_LICENSE_IDS)
        # The check adds one license by hand, and only that one.
        assert LicenseCheck.OSI_APPROVED_SPDX - OSI_APPROVED_LICENSES == {"PSF-2.0"}
        assert set(LicenseCheck.KNOWN_LICENSES) <= OSI_APPROVED_LICENSES

    def test_generated_file_matches_the_generator(self, repo_root: Path) -> None:
        """The data module is exactly what tools/gen_osi_licenses.py writes."""
        script = repo_root / "tools" / "gen_osi_licenses.py"
        data_file = repo_root / "src" / "pycmdcheck" / "_osi_licenses.py"
        if not script.is_file():
            pytest.skip("tools/gen_osi_licenses.py is not part of this checkout")
        # run_path compiles in memory, so no __pycache__ appears under tools/.
        generator = runpy.run_path(str(script), run_name="gen_osi_licenses")
        text = generator["render"](
            _osi_licenses.SPDX_LIST_VERSION,
            set(SPDX_LICENSE_IDS),
            set(OSI_APPROVED_LICENSES),
        )
        assert text == data_file.read_text("utf-8")

    def test_generator_reads_a_saved_list(
        self, repo_root: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """A saved copy of the SPDX list is turned into the module, or checked."""
        script = repo_root / "tools" / "gen_osi_licenses.py"
        if not script.is_file():
            pytest.skip("tools/gen_osi_licenses.py is not part of this checkout")
        main = runpy.run_path(str(script), run_name="gen_osi_licenses")["main"]
        entries = [
            {"licenseId": "MIT", "isOsiApproved": True},
            {"licenseId": "GPL-3.0", "isOsiApproved": True, "isDeprecatedLicenseId": 1},
            {"licenseId": "CC-BY-NC-4.0", "isOsiApproved": False},
        ]
        source = tmp_path / "licenses.json"
        output = tmp_path / "_osi_licenses.py"

        def save(version: str) -> list[str]:
            document = {"licenseListVersion": version, "licenses": entries}
            source.write_text(json.dumps(document), encoding="utf-8")
            return [str(source), "--output", str(output)]

        assert main(save("9.9")) == 0
        written = output.read_text("utf-8")
        module = runpy.run_path(str(output))
        assert module["SPDX_LIST_VERSION"] == "9.9"
        assert module["SPDX_LICENSE_IDS"] == {"MIT", "GPL-3.0", "CC-BY-NC-4.0"}
        assert module["OSI_APPROVED_LICENSES"] == {"MIT", "GPL-3.0"}
        assert main([*save("9.9"), "--check"]) == 0
        # A newer list makes the file out of date, and --check leaves it alone.
        assert main([*save("10.0"), "--check"]) == 1
        assert output.read_text("utf-8") == written
        assert "is out of date" in capsys.readouterr().err
