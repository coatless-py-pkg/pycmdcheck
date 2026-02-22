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
