"""License file check.

This module provides the LicenseCheck class which verifies that a
package has a proper license file and optionally identifies the license type.
"""

import re
from pathlib import Path
from typing import Any

from pycmdcheck._osi_licenses import OSI_APPROVED_LICENSES, SPDX_LICENSE_IDS
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_DETAIL_LINES, MIN_LICENSE_LENGTH
from pycmdcheck.fileio import (
    display_path,
    find_declared_files,
    find_project_files,
    os_error_text,
    plain_text,
    read_text_capped,
    resolve_declared_file,
)
from pycmdcheck.pyproject_reader import (
    get_dict,
    get_effective_project_table,
    get_str,
    get_str_list,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

# A declared license longer than this is license text, not an SPDX expression.
_MAX_SPDX_LENGTH = 200
_SPDX_ID_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.-]*\+?")
_SPDX_OPERATORS = ("AND", "OR", "WITH")
_SPDX_LICENSES = frozenset(spdx.lower() for spdx in SPDX_LICENSE_IDS)


def _spdx_ids(expression: str) -> list[str] | None:
    """Return the license ids of an SPDX expression, or None if it is not one.

    Operands of ``WITH`` are exceptions, not licenses, and are left out.
    """
    if len(expression) > _MAX_SPDX_LENGTH:
        return None
    tokens = re.sub(r"[()]", " ", expression).split()
    if len(tokens) % 2 == 0:
        return None
    ids: list[str] = []
    for index, token in enumerate(tokens):
        if index % 2:
            if token.upper() not in _SPDX_OPERATORS:
                return None
        elif token.upper() in _SPDX_OPERATORS or not _SPDX_ID_RE.fullmatch(token):
            return None
        elif index == 0 or tokens[index - 1].upper() != "WITH":
            ids.append(token.rstrip("+"))
    return list(dict.fromkeys(ids))


def _is_spdx_license(spdx: str) -> bool:
    """Return whether *spdx* is a license of the SPDX list or a ``LicenseRef-``.

    A word that only has the shape of an identifier, such as ``BSD`` or
    ``GPLv3``, is neither. Letter case is not compared.
    """
    lowered = spdx.lower()
    return lowered.startswith("licenseref-") or lowered in _SPDX_LICENSES


# The hint for a license file that could not be read, with the file to name.
_READ = (
    "Give yourself read permission on the license file, for example with chmod u+r {}."
)
_NOT_SPDX = "Declared license: {} (not an SPDX expression)"


class LicenseCheck(BaseCheck):
    """Check that the package has a license file.

    Looks for common license file names (LICENSE, COPYING, etc.) and
    attempts to identify the license type (MIT, Apache, GPL, etc.).

    Attributes:
        name: The check identifier ("license").
        description: Human-readable description of this check.
        rules: The codes of the findings, each with what the rule means.
        hints: The same codes, each with what to do about the finding.
        OSI_APPROVED_SPDX: License identifiers that count as approved by the
            Open Source Initiative, compared without regard to letter case.
        LICENSE_STEMS: File name stems searched for in the project root, in
            any letter case and with any suffix. Override to change the search.
        LICENSE_DIRS: Directories whose files count as license texts.
        LICENSE_FILENAMES: No longer used. Kept for code that still reads it.
        KNOWN_LICENSES: Dictionary mapping license identifiers to
            text markers used for identification.

    Examples:
        Run the license check:

        >>> from pathlib import Path
        >>> check = LicenseCheck()
        >>> result = check.run(Path("."), {"enabled": True})
        >>> result.name
        'license'

        The check identifies common licenses:

        >>> # For a package with MIT license
        >>> # result.status == CheckStatus.OK
        >>> # "License type: MIT" in result.details
    """

    name = "license"
    description = "Check for license file"

    rules = {
        "license.missing": "No license file was found",
        "license.unreadable": "The license file could not be read",
        "license.too-short": (
            "The license file is empty or too short to be a license text"
        ),
        "license.not-osi-approved": (
            "The declared SPDX license expression names no license approved by "
            "the Open Source Initiative"
        ),
    }
    hints = {
        "license.missing": (
            "Add a file named LICENSE with the full text of your license to the "
            "project root."
        ),
        "license.unreadable": _READ.format("LICENSE"),
        "license.too-short": (
            f"Put the full text of your license, at least {MIN_LICENSE_LENGTH} "
            "characters, in the license file."
        ),
        "license.not-osi-approved": (
            "Choose an OSI-approved license such as MIT or Apache-2.0, then put its "
            "text in the license file and its identifier in license in pyproject.toml."
        ),
    }

    # The identifiers that the SPDX license list marks as approved by the Open
    # Source Initiative, written by tools/gen_osi_licenses.py. PSF-2.0 is the
    # one exception added by hand: SPDX does not mark it as approved, but the
    # package index has an "OSI Approved :: Python Software Foundation License"
    # classifier and projects such as matplotlib declare it.
    OSI_APPROVED_SPDX: set[str] = {*OSI_APPROVED_LICENSES, "PSF-2.0"}

    # No longer used: the search goes through LICENSE_STEMS and LICENSE_DIRS.
    # Kept at its 0.1.1 value for code that still reads it.
    LICENSE_FILE_RE = re.compile(r"(?i)^(licen[cs]e|copying)([._-].*)?$")

    # A file matches a stem bare or followed by ".", "-" or "_" and a suffix.
    LICENSE_STEMS = ["LICENSE", "LICENCE", "COPYING", "UNLICENSE"]

    # matplotlib keeps its license in LICENSE/, REUSE projects in LICENSES/.
    LICENSE_DIRS = ["LICENSE", "LICENSES"]

    # No longer used: the hint of license.missing names the file to add.
    # Kept for code that still reads it.
    LICENSE_FILENAMES = [
        "LICENSE",
        "LICENSE.txt",
        "LICENSE.md",
        "LICENSE.rst",
        "LICENCE",
        "LICENCE.txt",
        "LICENCE.md",
        "COPYING",
        "COPYING.txt",
    ]

    # All markers of an entry must occur, compared without regard to case or
    # line breaks. When several entries match, the one whose first marker comes
    # first in the text wins, then the one listed first. The GNU entries need
    # the title followed by its version, because GPL, MPL, EPL and PSF texts
    # all name other GNU licenses in passing.
    KNOWN_LICENSES = {
        "AGPL-3.0": ["GNU AFFERO GENERAL PUBLIC LICENSE Version 3"],
        "LGPL-3.0": ["GNU LESSER GENERAL PUBLIC LICENSE Version 3"],
        "LGPL-2.1": ["GNU LESSER GENERAL PUBLIC LICENSE Version 2.1"],
        "GPL-3.0": ["GNU GENERAL PUBLIC LICENSE Version 3"],
        "GPL-2.0": ["GNU GENERAL PUBLIC LICENSE Version 2"],
        "MPL-2.0": ["Mozilla Public License", "Version 2.0"],
        "EPL-2.0": ["Eclipse Public License", "v 2.0"],
        "Apache-2.0": ["Apache License", "Version 2.0"],
        "MIT": ["Permission is hereby granted, free of charge"],
        "BSD-3-Clause": [
            "Redistribution and use in source and binary forms",
            "endorse or promote products",
        ],
        "BSD-2-Clause": ["Redistribution and use in source and binary forms"],
        "ISC": [
            "Permission to use, copy, modify, and",
            "this permission notice appear in all copies",
        ],
        "Unlicense": ["This is free and unencumbered software released into"],
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for license file presence and validity.

        Searches for a license file using common naming conventions,
        verifies it's not empty, and reports the license type.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if a valid license file is found
            - NOTE status if the declared license is an SPDX expression that
              names no license approved by the Open Source Initiative
            - WARNING status if no license file is found, or the file is
              empty or cannot be read
        """
        project = get_effective_project_table(package_path)
        expression, declared_ids, free_text = self._declared_license(project)
        # The declared license is graded whether or not its file is found.
        graded = self._not_approved(declared_ids)
        candidates = self._find_license_files(package_path, project)
        if not candidates:
            return self._missing_result(expression, free_text, graded)

        # Read candidates until one is long enough to be a license text, so
        # that a short pointer or notice file does not hide the real one.
        unreadable: list[str] = []
        unread = ""
        found: tuple[Path, str] | None = None
        for candidate in candidates[:MAX_DETAIL_LINES]:
            try:
                text = read_text_capped(candidate)
            except OSError as exc:
                shown = display_path(candidate, package_path)
                unreadable.append(f"Could not read {shown}: {os_error_text(exc)}")
                unread = unread or shown
                continue
            complete = len(text.strip()) >= MIN_LICENSE_LENGTH
            if found is None or complete:
                found = (candidate, text)
            if complete:
                break
        if found is None:
            # A declared expression or value is shown as beside a file read.
            if free_text:
                unreadable.append(_NOT_SPDX.format(free_text))
            elif expression:
                unreadable.append(f"License type: {expression}")
            return self._warning(
                "license.unreadable",
                "Could not read license file",
                unreadable,
                graded,
                # The command in the hint names the file, so it can be typed.
                _READ.format(unread),
            )

        license_file, content = found
        details = [f"Found license file: {display_path(license_file, package_path)}"]

        # A declared expression or classifier is the license type. A declared
        # value that is not an expression is shown on a line of its own. The
        # text is searched for a type only when none is declared, and the
        # result is worded as a guess.
        classifiers = [
            c
            for c in get_str_list(project, "classifiers")
            if c.startswith("License ::")
        ]
        guess = None
        if free_text:
            details.append(_NOT_SPDX.format(free_text))
        if expression:
            details.append(f"License type: {expression}")
        elif classifiers:
            names = ", ".join(c.rpartition("::")[2].strip() for c in classifiers)
            details.append(f"License type: {plain_text(names)}")
        else:
            guess = self._identify_license(content)
            if guess:
                details.append(f"License type: {guess} (guessed from the license text)")
            else:
                details.append("License type: Unknown/Custom")

        if len(content.strip()) < MIN_LICENSE_LENGTH:
            return self._warning(
                "license.too-short",
                "License file appears empty or incomplete",
                details,
                graded,
            )

        if graded:
            message = "Declared license is not OSI-approved"
            details += [finding.message for finding in graded]
        else:
            # Free text and a missing declaration are not graded. They fall
            # back on the classifiers and on the guess, which only add details.
            message = "License file present"
            ids = declared_ids or ([guess] if guess else [])
            details += self._osi_details(ids, classifiers)
        return CheckResult(
            name=self.name,
            status=most_severe(graded),
            message=message,
            details=details,
            findings=graded,
        )

    def _warning(
        self,
        code: str,
        message: str,
        details: list[str],
        graded: list[Finding],
        hint: str | None = None,
    ) -> CheckResult:
        """Build the WARNING for a license file that is missing or unusable.

        The finding for a declared license that is not approved, if there is
        one, is reported beside it. A *hint* given here is used in place of
        the one in ``hints``.
        """
        findings = [Finding(code, message, CheckStatus.WARNING, hint), *graded]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=message,
            details=details + [finding.message for finding in graded],
            findings=findings,
        )

    def _not_approved(self, ids: list[str]) -> list[Finding]:
        """Return the finding for a declared expression with no approved license.

        The rule fires only when every id is a license of the SPDX list or a
        ``LicenseRef-``, and none is in :attr:`OSI_APPROVED_SPDX`. A
        ``LicenseRef-`` is in no list, so it is never approved. Any other id
        makes the declared value free text, which is not graded.

        Returns:
            A list with the one finding, or an empty list.
        """
        approved = {spdx.lower() for spdx in self.OSI_APPROVED_SPDX}
        if not ids or any(spdx.lower() in approved for spdx in ids):
            return []
        if not all(map(_is_spdx_license, ids)):
            return []
        said = f"Declared license is not OSI-approved: {', '.join(ids)}"
        return [Finding("license.not-osi-approved", said, CheckStatus.NOTE)]

    def _declared_license(
        self, project: dict[str, Any]
    ) -> tuple[str | None, list[str], str | None]:
        """Return the SPDX expression declared in pyproject.toml and its ids.

        Reads ``license = "..."`` and ``license = {text = "..."}``. A value
        that is not an SPDX expression, such as prose or the license text
        itself, gives ``None`` and no ids. The third item is then the value
        to show, or ``None`` when it is empty, longer than
        ``_MAX_SPDX_LENGTH`` characters or more than one line.
        """
        value = get_str(project, "license")
        if value is None:
            value = get_str(get_dict(project, "license"), "text", "")
        ids = _spdx_ids(value)
        if ids is not None:
            return " ".join(value.split()), ids, None
        shown = value.strip()
        one_line = len(value) <= _MAX_SPDX_LENGTH and len(shown.splitlines()) == 1
        return None, [], plain_text(shown) if one_line else None

    def _find_license_files(
        self, package_path: Path, project: dict[str, Any]
    ) -> list[Path]:
        """List the files that may hold the license, most likely first.

        Looks at conventional names in the project root, then at the files
        named by ``license-files`` and ``license = {file = ...}`` in
        pyproject.toml, then inside ``LICENSE/`` and ``LICENSES/`` directories.
        Only regular files count, and declared paths stay inside the project.
        """
        files = find_project_files(package_path, self.LICENSE_STEMS, None)
        files += find_declared_files(package_path, project.get("license-files"))
        referenced = resolve_declared_file(
            package_path, get_dict(project, "license").get("file")
        )
        if referenced is not None:
            files.append(referenced)
        files += find_project_files(
            package_path, self.LICENSE_STEMS, None, subdirs=self.LICENSE_DIRS
        )
        files += find_declared_files(
            package_path, [f"{name}/*" for name in self.LICENSE_DIRS]
        )
        return list(dict.fromkeys(files))

    def _missing_result(
        self, expression: str | None, free_text: str | None, graded: list[Finding]
    ) -> CheckResult:
        """Build the result for a project in which no license file was found."""
        details = [
            "Looked for LICENSE*, LICENCE*, COPYING* and UNLICENSE* in the project "
            "root, in LICENSE/ and LICENSES/ directories, and for files named by "
            "license-files or license.file in pyproject.toml"
        ]
        if expression:
            details.append(
                f"pyproject.toml declares license = {expression}, "
                "but the repository has no license file"
            )
        if free_text:
            details.append(_NOT_SPDX.format(free_text))
        return self._warning(
            "license.missing", "No license file found", details, graded
        )

    def _identify_license(self, content: str) -> str | None:
        """Guess the license type from the text, or return None."""
        text = " ".join(content.upper().split())
        best: tuple[int, str] | None = None
        for license_name, markers in self.KNOWN_LICENSES.items():
            positions = [text.find(marker.upper()) for marker in markers]
            if min(positions, default=-1) < 0:
                continue
            if best is None or positions[0] < best[0]:
                best = (positions[0], license_name)
        return best[1] if best else None

    def _osi_details(self, ids: list[str], classifiers: list[str]) -> list[str]:
        """Say which license ids are in :attr:`OSI_APPROVED_SPDX`.

        Ids are compared without regard to case. A ``License :: OSI Approved``
        classifier counts when no id is in the set. This is only asked once
        :meth:`_not_approved` has found nothing, so the status of the check
        does not depend on the answer.
        """
        known = {spdx.lower() for spdx in self.OSI_APPROVED_SPDX}
        approved = [spdx for spdx in ids if spdx.lower() in known]
        unlisted = [spdx for spdx in ids if spdx.lower() not in known]
        osi_classifiers = [
            c for c in classifiers if c.startswith("License :: OSI Approved")
        ]
        details: list[str] = []
        if approved:
            details.append(f"License is OSI-approved ({', '.join(approved)})")
        elif osi_classifiers:
            details.append(
                f"License is OSI-approved ({plain_text(osi_classifiers[0])})"
            )
        if unlisted and (approved or not osi_classifiers):
            details.append(
                "Not in pycmdcheck's list of OSI-approved licenses "
                f"(may still be OSI-approved): {', '.join(unlisted)}"
            )
        return details
