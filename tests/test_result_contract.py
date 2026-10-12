"""Every built-in check keeps to the result contract.

One case for each check on each of five small projects. Select the cases of
one check with its name and a hyphen in brackets: ``-k "[license-"``. No tool
runs and nothing is fetched: every tool looks missing, no process can start,
and conftest keeps the tests off the network.
"""

import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from pycmdcheck import discovery
from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.profiles import ALL_CHECKS, DEPRECATED_CHECKS
from pycmdcheck.results import (
    SKIP_REASONS,
    CheckResult,
    CheckStatus,
    Finding,
    most_severe,
)
from pycmdcheck.runner import RUNNER_HINTS, RUNNER_RULES, fill_hints

# The modules of the checks that run an external tool.
TOOL_MODULES = ("tests", "doctests", "linting", "formatting", "typing", "build")
SETUP_PY = "from setuptools import setup\n\nsetup(name='mypackage', version='0.1.0')\n"
TOOLS_ONLY = """\
[build-system]
requires = ["setuptools>=61"]
build-backend = "setuptools.build_meta"

[tool.black]
line-length = 88
"""
# How make_package builds each project.
PROJECTS: dict[str, Callable[[Callable[..., Path]], Path]] = {
    "healthy": lambda make: make(),
    "empty": lambda make: make(
        layout="none", pyproject=False, readme=False, license=False, tests=False
    ),
    "setup-py": lambda make: make(
        layout="flat", pyproject=False, files={"setup.py": SETUP_PY}
    ),
    "invalid-toml": lambda make: make(pyproject="[project\nname ="),
    # A pyproject.toml that only configures tools, and no setup.py or setup.cfg.
    "no-project-table": lambda make: make(pyproject=TOOLS_ONLY),
}
CASES = [
    pytest.param(name, project, id=f"{name}-{project}")
    for name in sorted(ALL_CHECKS | DEPRECATED_CHECKS)
    for project in PROJECTS
]


def one_line(text: str) -> bool:
    """Say whether *text* is one line that holds more than white space."""
    return bool(text.strip()) and text.splitlines() == [text]


def broken_clauses(result: CheckResult, name: str, rules: dict[str, str]) -> list[str]:
    """Return the clauses of the contract that a result of check *name* breaks."""
    status, findings, reason = result.status, result.findings, result.skip_reason
    skipped = status is CheckStatus.SKIPPED
    broken = []
    if skipped and reason not in SKIP_REASONS:
        broken.append(f"SKIPPED needs a skip_reason from SKIP_REASONS, not {reason!r}")
    if not skipped and reason is not None:
        broken.append(f"only SKIPPED carries a skip_reason, here {reason!r}")
    if skipped or status is CheckStatus.OK:
        if findings:
            broken.append(f"{status.name} must carry no findings")
        return broken
    if not findings:
        return [*broken, f"{status.name} needs at least one finding"]
    known = {code for code in rules if code.startswith(f"{name}.")}
    if unknown := sorted({finding.code for finding in findings} - known):
        broken.append(f"not a '{name}.<rule>' key of the class's rules: {unknown}")
    if status is not (expected := most_severe(findings)):
        broken.append(f"status must be most_severe(findings), {expected.name}")
    if untidy := sorted({f.code for f in findings if not one_line(f.message)}):
        broken.append(f"a finding message must be one line of text: {untidy}")
    return broken


def hint_faults(hint: str) -> list[str]:
    """Return what is wrong with the form of a hint."""
    faults = {
        "is not one line": not one_line(hint),
        "does not start with an upper case letter": not hint[:1].isupper(),
        "does not end with a full stop": not hint.endswith("."),
        "is longer than 160 characters": len(hint) > 160,
        "has an em dash": "\u2014" in hint,
        'starts with "Consider"': hint.startswith("Consider"),
        'says "please"': "please" in hint.lower(),
        "is more than one sentence": bool(re.search(r"[.!?;] +[A-Z]|;", hint)),
    }
    return [fault for fault, found in faults.items() if found]


def rules_and_hints(name: str) -> tuple[dict[str, str], dict[str, str]]:
    """Return the rules and the hints of check *name*, or of the runner."""
    if name == "pycmdcheck":
        return RUNNER_RULES, RUNNER_HINTS
    check_class = discovery.discover_checks()[name]
    return check_class.rules, getattr(check_class, "hints", {})


@pytest.fixture(autouse=True)
def _no_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make every tool look missing, and let no process start."""
    for module in TOOL_MODULES:
        # The build check asks for the version of its module instead, which
        # it cannot get either while no process starts.
        target = f"pycmdcheck.checks.{module}.tool_available"
        monkeypatch.setattr(target, lambda tool: False, raising=False)

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError("the result contract test starts no process")

    monkeypatch.setattr(subprocess, "Popen", refuse)


@pytest.mark.parametrize(("name", "project"), CASES)
def test_built_in_check_keeps_the_contract(
    name: str, project: str, make_package: Callable[..., Path]
) -> None:
    check_class = discovery.discover_checks()[name]
    result = check_class().run(PROJECTS[project](make_package), {})
    broken = broken_clauses(result, name, check_class.rules)
    assert not broken, (
        f"the {name} check on the {project} project returned "
        f"{result.status.name} ({result.message!r}): {'; '.join(broken)}"
    )
    # Every finding carries a hint once those of the class are filled in.
    fill_hints(result, getattr(check_class, "hints", {}))
    bare = sorted({finding.code for finding in result.findings if not finding.hint})
    assert not bare, (
        f"the {name} check on the {project} project returned findings "
        f"with no hint: {', '.join(bare)}"
    )
    # A hint that the check set itself has to keep the form too.
    untidy = [
        f"{finding.code} {fault}"
        for finding in result.findings
        for fault in hint_faults(finding.hint or "")
    ]
    assert not untidy, (
        f"hints of the {name} check on the {project} project: {'; '.join(untidy)}"
    )


@pytest.mark.parametrize(
    "name", sorted(ALL_CHECKS | DEPRECATED_CHECKS | {"pycmdcheck"})
)
def test_every_rule_has_a_well_formed_hint(name: str) -> None:
    rules, hints = rules_and_hints(name)
    missing = sorted(set(rules) - set(hints))
    assert not missing, f"the {name} check has no hint for: {', '.join(missing)}"
    extra = sorted(set(hints) - set(rules))
    assert not extra, f"the {name} check has a hint for no rule: {', '.join(extra)}"
    untidy = [
        f"{code} {fault}" for code, hint in hints.items() for fault in hint_faults(hint)
    ]
    assert not untidy, f"hints of the {name} check: {'; '.join(untidy)}"


def test_hint_form_is_checked() -> None:
    assert hint_faults("Add a LICENSE file to the project root.") == []
    assert hint_faults("consider this\nor that \u2014 " + "x" * 160) == [
        "is not one line",
        "does not start with an upper case letter",
        "does not end with a full stop",
        "is longer than 160 characters",
        "has an em dash",
    ]
    assert hint_faults("Consider a file. Please add it; then run again.") == [
        'starts with "Consider"',
        'says "please"',
        "is more than one sentence",
    ]
    assert hint_faults("Consider adding a file.") == ['starts with "Consider"']


def test_stub_check_that_follows_the_model_keeps_the_contract(tmp_path: Path) -> None:
    class NoticeCheck(BaseCheck):
        name = "notice"
        rules = {"notice.empty-file": "The NOTICE file is empty"}

        def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
            notice = package_path / "NOTICE"
            if not notice.exists():
                return CheckResult.skip(self.name, "No NOTICE file", "not-applicable")
            empty = Finding("notice.empty-file", "NOTICE is empty", CheckStatus.WARNING)
            findings = [] if notice.read_text(encoding="utf-8") else [empty]
            status = most_severe(findings)
            return CheckResult(self.name, status, "Read NOTICE", findings=findings)

    statuses = []
    for content in (None, "", "Copyright 2026"):
        if content is not None:
            (tmp_path / "NOTICE").write_text(content, encoding="utf-8")
        result = NoticeCheck().run(tmp_path, {})
        assert broken_clauses(result, "notice", NoticeCheck.rules) == []
        statuses.append(result.status)
    assert statuses == [CheckStatus.SKIPPED, CheckStatus.WARNING, CheckStatus.OK]
    # A result that leaves its finding out is told so.
    bare = CheckResult("notice", CheckStatus.WARNING, "NOTICE is empty")
    assert broken_clauses(bare, "notice", {}) == ["WARNING needs at least one finding"]
    # So is a finding whose message is not one line of text.
    for text in ("", " ", "first\nsecond", "ends with a break\n"):
        untidy = Finding("notice.empty-file", text)
        result = CheckResult("notice", CheckStatus.NOTE, "Read", findings=[untidy])
        assert broken_clauses(result, "notice", NoticeCheck.rules) == [
            "a finding message must be one line of text: ['notice.empty-file']"
        ]
