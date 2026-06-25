"""Tests for license check enhancements."""

from pathlib import Path

from pycmdcheck.checks.license import LicenseCheck
from pycmdcheck.results import CheckStatus


class TestLicenseOSI:
    """Tests for OSI-approved license validation."""

    def test_mit_is_osi_approved(self, temp_package: Path) -> None:
        """MIT license identified as OSI-approved."""
        check = LicenseCheck()
        result = check.run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert any("osi" in d.lower() for d in result.details)

    def test_unknown_license_noted(self, tmp_path: Path) -> None:
        """Unknown license type -> detail notes unrecognized."""
        (tmp_path / "LICENSE").write_text(
            "Custom license terms apply to this software.\n"
            "You may use this under certain conditions.\n"
            "Please contact the author for details.\n"
        )
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "0.1.0"\nlicense = "Custom-License"\n'
        )
        check = LicenseCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK
        has_note = any(
            "unknown" in d.lower() or "custom" in d.lower() for d in result.details
        )
        assert has_note

    def test_no_pyproject_still_ok(self, tmp_path: Path) -> None:
        """License file without pyproject.toml still passes."""
        (tmp_path / "LICENSE").write_text(
            "MIT License\n\nPermission is hereby granted, free of charge...\n"
            "to any person obtaining a copy of this software.\n"
        )
        check = LicenseCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK


class TestLicenseFileDiscovery:
    """Tests for license file discovery (#10) and SPDX handling (#14)."""

    def test_dual_license_suffixed_filename_found(self, tmp_path: Path) -> None:
        """A suffixed filename like LICENSE-MIT is recognized, not 'No license'."""
        (tmp_path / "LICENSE-MIT").write_text(
            "MIT License\n\nPermission is hereby granted, free of charge, to any\n"
            "person obtaining a copy of this software and associated docs.\n"
        )
        (tmp_path / "LICENSE-APACHE").write_text(
            "Apache License\nVersion 2.0\n\nTerms and conditions for use.\n"
        )
        check = LicenseCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert any("LICENSE-MIT" in d or "LICENSE-APACHE" in d for d in result.details)

    def test_copying_lesser_found(self, tmp_path: Path) -> None:
        """COPYING.LESSER (LGPL convention) is recognized."""
        (tmp_path / "COPYING.LESSER").write_text(
            "GNU LESSER GENERAL PUBLIC LICENSE\nVersion 3\n\n"
            "This library is free software you can redistribute it.\n"
        )
        check = LicenseCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK

    def test_license_file_table_not_treated_as_spdx(self, tmp_path: Path) -> None:
        """license = {file = ...} must not produce a false 'not OSI-approved' NOTE."""
        (tmp_path / "LICENSE").write_text(
            "Some license text that is reasonably long and present here.\n"
            "Additional clauses follow on subsequent lines for length.\n"
        )
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "0.1.0"\nlicense = {file = "LICENSE"}\n'
        )
        check = LicenseCheck()
        result = check.run(tmp_path, {})
        assert not any("not in pycmdcheck" in d for d in result.details)
        assert not any("not recognized" in d.lower() for d in result.details)

    def test_spdx_expression_is_osi(self, tmp_path: Path) -> None:
        """An SPDX expression like 'MIT OR Apache-2.0' is recognized as OSI."""
        (tmp_path / "LICENSE").write_text(
            "Dual licensed under MIT or Apache-2.0. See terms for the details\n"
            "of each license option provided to downstream users here.\n"
        )
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "pkg"\nversion = "0.1.0"\n'
            'license = "MIT OR Apache-2.0"\n'
        )
        check = LicenseCheck()
        result = check.run(tmp_path, {})
        assert result.status == CheckStatus.OK
        assert any("osi-approved" in d.lower() for d in result.details)
