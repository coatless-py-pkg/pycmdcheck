"""License file check.

This module provides the LicenseCheck class which verifies that a
package has a proper license file and optionally identifies the license type.
"""

from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MIN_LICENSE_LENGTH
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

        # Look for license file
        license_file = None
        for filename in self.LICENSE_FILENAMES:
            candidate = package_path / filename
            if candidate.exists():
                license_file = candidate
                break

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

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="License file present",
            details=details,
        )

    def _identify_license(self, content: str) -> str | None:
        """Try to identify the license type from content."""
        content_upper = content.upper()

        for license_name, markers in self.KNOWN_LICENSES.items():
            if all(marker.upper() in content_upper for marker in markers):
                return license_name

        return None
