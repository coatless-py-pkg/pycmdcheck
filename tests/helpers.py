"""Helpers that several test files share.

conftest.py registers this module with pytest, so a failed assert in here
shows the values it compared.
"""

from collections.abc import Collection, Mapping
from pathlib import Path

from pycmdcheck.results import CheckResult, CheckStatus, most_severe

# The options the triage bot passes, without the path.
TRIAGE = ["--profile", "triage", "--json", "--exit-zero"]
# Number of checks in the triage profile.
TRIAGE_CHECKS = 12

# The keys of the JSON report and of each result in it, in the order written.
JSON_KEYS = [
    "package_path",
    "passed",
    "summary",
    "results",
    "schema_version",
    "pycmdcheck_version",
    "profile",
    "config_file",
    "fail_on",
    "failed",
    "requested_checks",
    "skipped_checks",
]
RESULT_KEYS = [
    "name",
    "status",
    "message",
    "details",
    "duration",
    "findings",
    "skip_reason",
]


def only_code(result: CheckResult) -> str:
    """Return the code of the result's one finding, which carries its status."""
    (finding,) = result.findings
    assert finding.status == result.status
    assert finding.message and "\n" not in finding.message
    assert result.skip_reason is None
    return finding.code


def codes(result: CheckResult, rules: Collection[str] | None = None) -> list[str]:
    """Return the codes of the findings, which must agree with the result.

    With *rules*, the rules of the check, every code must be one of them.
    """
    assert result.status == most_severe(result.findings)
    assert result.skip_reason is None
    listed = [finding.code for finding in result.findings]
    if rules is not None:
        assert set(listed) <= set(rules)
    return listed


def skipped(result: CheckResult) -> str | None:
    """Return why the check was skipped, after checking that it was."""
    assert result.status == CheckStatus.SKIPPED
    assert result.findings == []
    assert result.details
    return result.skip_reason


def found(result: CheckResult) -> list[tuple[str, CheckStatus]]:
    """Return the code and status of each finding of *result*."""
    return [(finding.code, finding.status) for finding in result.findings]


def write_files(root: Path, files: Mapping[str, str | bytes | None]) -> Path:
    """Write *files* under *root*, creating directories, and return *root*.

    A value of ``None`` deletes the file.
    """
    for relative, content in files.items():
        target = root / relative
        if content is None:
            target.unlink(missing_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            target.write_bytes(content)
        else:
            target.write_text(content, encoding="utf-8")
    return root
