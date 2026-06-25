"""License file check.

This module provides the LicenseCheck class which verifies that a
package has a proper license file and optionally identifies the license type.
"""

import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MIN_LICENSE_LENGTH
from pycmdcheck.pyproject_reader import get_project_table
from pycmdcheck.results import CheckResult, CheckStatus


class LicenseCheck(BaseCheck):
    """Check that the package has a license file.

    Looks for common license file names (LICENSE, COPYING, etc.) and
    attempts to identify the license type (MIT, Apache, GPL, etc.).

    Attributes:
        name: The check identifier ("license").
        description: Human-readable description of this check.
        LICENSE_FILENAMES: List of recognized license file names.
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

    OSI_APPROVED_SPDX: set[str] = {
        "MIT",
        "Apache-2.0",
        "GPL-2.0-only",
        "GPL-2.0-or-later",
        "GPL-3.0-only",
        "GPL-3.0-or-later",
        "BSD-2-Clause",
        "BSD-3-Clause",
        "ISC",
        "MPL-2.0",
        "LGPL-2.1-only",
        "LGPL-2.1-or-later",
        "LGPL-3.0-only",
        "LGPL-3.0-or-later",
        "EUPL-1.2",
        "Artistic-2.0",
        "Zlib",
        "PSF-2.0",
        "BSL-1.0",
        "Unlicense",
        "0BSD",
        "AGPL-3.0-only",
        "AGPL-3.0-or-later",
        "EPL-1.0",
        "EPL-2.0",
        "CDDL-1.0",
        "MPL-1.1",
        "Python-2.0",
        "PostgreSQL",
        "NCSA",
        "MIT-0",
        # Common short forms also accepted
        "GPL-2.0",
        "GPL-3.0",
        "LGPL-2.1",
        "LGPL-3.0",
        "AGPL-3.0",
    }

    # Matches LICENSE / LICENCE / COPYING with optional suffix, e.g.
    # LICENSE-MIT, LICENSE.txt, COPYING.LESSER, LICENCE.md.
    LICENSE_FILE_RE = re.compile(r"(?i)^(licen[cs]e|copying)([._-].*)?$")

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

    KNOWN_LICENSES = {
        "MIT": ["MIT License", "Permission is hereby granted, free of charge"],
        "Apache-2.0": ["Apache License", "Version 2.0"],
        "GPL-3.0": ["GNU GENERAL PUBLIC LICENSE", "Version 3"],
        "GPL-2.0": ["GNU GENERAL PUBLIC LICENSE", "Version 2"],
        "BSD-3-Clause": ["BSD 3-Clause License", "Redistribution and use"],
        "BSD-2-Clause": ["BSD 2-Clause License", "Simplified"],
        "ISC": ["ISC License", "Permission to use, copy, modify"],
        "MPL-2.0": ["Mozilla Public License", "Version 2.0"],
        "LGPL-3.0": ["GNU LESSER GENERAL PUBLIC LICENSE", "Version 3"],
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check for license file presence and validity.

        Searches for a license file using common naming conventions,
        verifies it's not empty, and attempts to identify the license type.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Currently
                no check-specific options are used.

        Returns:
            A CheckResult with:

            - OK status if a valid license file is found
            - WARNING status if no license file found or file is empty
        """
        details: list[str] = []

        # Look for a license file (canonical names, then suffixed/dual-license
        # names like LICENSE-MIT, then a file referenced from pyproject.toml).
        license_file = self._find_license_file(package_path)

        if not license_file:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No license file found",
                details=[
                    f"Consider adding one of: {', '.join(self.LICENSE_FILENAMES[:3])}"
                ],
            )

        details.append(f"Found license file: {license_file.name}")

        # Try to identify the license type
        try:
            content = license_file.read_text(encoding="utf-8")
            license_type = self._identify_license(content)

            if license_type:
                details.append(f"License type: {license_type}")
            else:
                details.append("License type: Unknown/Custom")

            # Check if license file is not empty
            if len(content.strip()) < MIN_LICENSE_LENGTH:
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.WARNING,
                    message="License file appears empty or incomplete",
                    details=details,
                )

        except (OSError, UnicodeDecodeError) as e:
            details.append(f"Could not read license file: {e}")
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="Could not read license file",
                details=details,
            )

        # OSI-approved license validation
        self._check_osi_status(package_path, license_type, details)

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="License file present",
            details=details,
        )

    def _find_license_file(self, package_path: Path) -> Path | None:
        """Locate a license file.

        Tries canonical names first (preserving prior selection order), then
        any suffixed/dual-license filename (``LICENSE-MIT``, ``COPYING.LESSER``),
        then a file referenced via PEP 621 ``license = {file = ...}`` / PEP 639
        ``license-files`` in pyproject.toml.
        """
        # 1) Canonical names (LICENSE, LICENSE.txt, COPYING, …).
        for filename in self.LICENSE_FILENAMES:
            candidate = package_path / filename
            if candidate.exists():
                return candidate

        # 2) Suffixed / dual-license filenames (LICENSE-MIT, LICENSE-APACHE, …).
        try:
            entries = sorted(package_path.iterdir())
        except OSError:
            entries = []
        for entry in entries:
            if entry.is_file() and self.LICENSE_FILE_RE.match(entry.name):
                return entry

        # 3) A file referenced from pyproject.toml metadata.
        return self._license_file_from_metadata(package_path)

    def _license_file_from_metadata(self, package_path: Path) -> Path | None:
        """Return a license file referenced from pyproject.toml, if it exists."""
        project = get_project_table(package_path)

        # PEP 639 license-files: list of glob patterns.
        license_files = project.get("license-files")
        if isinstance(license_files, list):
            for pattern in license_files:
                if isinstance(pattern, str):
                    for match in sorted(package_path.glob(pattern)):
                        if match.is_file():
                            return match

        # PEP 621 license = {file = "..."}.
        lic = project.get("license")
        if isinstance(lic, dict):
            file_ref = lic.get("file")
            if isinstance(file_ref, str):
                candidate = package_path / file_ref
                if candidate.is_file():
                    return candidate

        return None

    def _identify_license(self, content: str) -> str | None:
        """Try to identify the license type from content."""
        content_upper = content.upper()

        for license_name, markers in self.KNOWN_LICENSES.items():
            if all(marker.upper() in content_upper for marker in markers):
                return license_name

        return None

    def _check_osi_status(
        self,
        package_path: Path,
        license_type: str | None,
        details: list[str],
    ) -> None:
        """Add OSI-approved status details based on pyproject.toml and content scan.

        This checks both the SPDX identifier declared in ``pyproject.toml``
        and the license type identified from the file content against the
        :attr:`OSI_APPROVED_SPDX` set.  Results are appended to *details*
        without changing the overall check status.
        """
        # Check the content-scanned license type against OSI set
        if license_type and license_type in self.OSI_APPROVED_SPDX:
            details.append(f"License is OSI-approved ({license_type})")
            return

        # Check the SPDX identifier from pyproject.toml
        project = get_project_table(package_path)
        spdx = project.get("license")

        if spdx is None:
            return

        # The license field can be a string (SPDX expression) or a table. Only
        # {text = "..."} carries an SPDX value; {file = "..."} points at a file
        # and must NOT be treated as an SPDX identifier.
        if isinstance(spdx, dict):
            spdx = spdx.get("text")

        if not isinstance(spdx, str) or not spdx.strip():
            return

        if self._spdx_is_osi(spdx):
            details.append(f"License is OSI-approved ({spdx})")
        else:
            details.append(
                f"NOTE: License '{spdx}' is not in pycmdcheck's known "
                "OSI-approved list (it may still be OSI-approved)"
            )

    def _spdx_is_osi(self, expression: str) -> bool:
        """Return whether an SPDX expression contains an OSI-approved license.

        Handles SPDX expressions (``MIT OR Apache-2.0``, ``GPL-3.0-only WITH
        ...``, parenthesised groups). Returns ``True`` if any licensed
        sub-identifier is in :attr:`OSI_APPROVED_SPDX` — lenient by design, so
        valid-but-unlisted licenses are not falsely flagged as non-OSI.
        """
        tokens = re.split(
            r"\s+(?:OR|AND|WITH)\s+|[()]", expression, flags=re.IGNORECASE
        )
        identifiers = [t.strip().rstrip("+") for t in tokens if t.strip()]
        return any(ident in self.OSI_APPROVED_SPDX for ident in identifiers)
