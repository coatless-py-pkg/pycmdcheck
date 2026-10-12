"""Keep the hand-written lists in the documentation in step with the code.

The names of the checks, the profiles, the command line options, the rule
codes, the skip reasons and the keys of the JSON report are written out by
hand in the README and on the documentation pages. So are the options that a
profile sets, the options that a ``--config`` file may set and the hint of
each rule. These tests compare sets of names, and for the options of a profile
their values, so the wording around them can change freely. Three things are
fixed. The row of a profile names a check in front of its options, as in
"`docs` gets". The row of a rule is under the heading of its check. It ends
with the hint of its code, word for word, and may mark parts of it as code.
The tests that read ``docs/`` are skipped when that directory is absent, as
it is in a source distribution.
"""

import ast
import inspect
import json
import re
import sys
import tomllib
from collections.abc import Collection, Iterable, Iterator
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from pycmdcheck import discovery, runner, subprocess_runner
from pycmdcheck.cli import main
from pycmdcheck.config import _FILE_OPTIONS
from pycmdcheck.profiles import ALL_CHECKS, DEPRECATED_CHECKS, PROFILES
from pycmdcheck.results import SKIP_REASONS

# Every name a built-in check is registered under.
REGISTERED = ALL_CHECKS | DEPRECATED_CHECKS

# The first cell of a table row, such as "| `metadata` | ...".
_FIRST_CELL_RE = re.compile(r"^\|\s*`(\w+)`\s*\|", re.MULTILINE)
# A section heading for one check, such as "### metadata".
_CHECK_HEADING_RE = re.compile(r"^### (\w+)$", re.MULTILINE)
# An API reference entry, such as "        - checks.metadata.MetadataCheck".
_REFERENCE_ENTRY_RE = re.compile(r"^\s*-\s+(checks\.\w+\.\w+)\s*$", re.MULTILINE)
# The start of a section of the API reference, such as "    - title: Profiles".
_REFERENCE_SECTION_RE = re.compile(r"^\s*- title: ", re.MULTILINE)
# A row of the status table, such as "| `ci` | ok, note |".
_STATUS_ROW_RE = re.compile(r"^\|\s*`(\w+)`\s*\|\s*([a-z, ]+?)\s*\|$", re.MULTILINE)
# A row of a rule table, such as "| `ci.missing` | note | No CI ... |".
_RULE_ROW_RE = re.compile(
    r"^\|\s*`(\w+\.[a-z0-9-]+)`\s*\|\s*([a-z, ]+?)\s*\|", re.MULTILINE
)
# The same row with its last cell, which holds the hint of the code:
# "| `ci.missing` | note | No CI ... | Add a GitHub Actions workflow ... |".
_HINT_ROW_RE = re.compile(
    r"^\|\s*`(\w+\.[a-z0-9-]+)`\s*\|[^|\n]*\|[^|\n]*\|\s*([^|\n]*?)\s*\|$",
    re.MULTILINE,
)
# A second-level heading, which starts a section of a page.
_SECTION_RE = re.compile(r"^## ", re.MULTILINE)
# A row of the skip reason table, which ends with the checks that give the
# reason: "| `tool-missing` | The tool ... | `build`, `tests` |".
_REASON_ROW_RE = re.compile(
    r"^\|\s*`([a-z-]+)`\s*\|[^|\n]*\|([^|\n]*)\|$", re.MULTILINE
)
# A code written out on a page: the value of a "code" key in a JSON example,
# or a name with a hyphen in backticks, such as `docs.missing-readme`.
_WRITTEN_CODE_RE = re.compile(r'"code": "([\w.-]+)"|`(\w+\.[a-z0-9]+(?:-[a-z0-9]+)+)`')
# The number of built-in checks, as in "all 19 built-in checks".
_CHECK_COUNT_RE = re.compile(r"(\d+) built-in checks?\b")
# A name in backticks.
_NAME_RE = re.compile(r"`(\w+)`")
# The first cell of a row of the option table, such as "| `-c, --check NAME` |".
_OPTION_CELL_RE = re.compile(r"^\|\s*`(-[^`]*)`\s*\|", re.MULTILINE)
# An option with its value, as the row of a profile writes it: `strict = true`.
_SETTING_RE = re.compile(r"`(\w+) = ([^`]+)`")
# The check whose options follow, as that row names it: "`typing` gets".
_GETS_RE = re.compile(r"`(\w+)` gets")
# A row of two cells that starts with a name, such as "| `urls` | `timeout` |".
_TWO_CELL_ROW_RE = re.compile(r"^\|\s*`(\w+)`\s*\|([^|\n]*)\|$", re.MULTILINE)
# A JSON example, from the line that opens it to the line that closes it.
_JSON_BLOCK_RE = re.compile(r"^```json\n(.*?)^```$", re.MULTILINE | re.DOTALL)

_BASE_CLASSES = {"checks.base.Check", "checks.base.BaseCheck"}

# The rules whose status no single call in the source settles, because the
# code or the status reaches Finding() through a variable. Every other status
# is read from the source by _rule_statuses(). A test keeps this table to
# exactly the codes that need it.
_STATUS_SET_ELSEWHERE = {
    # community.py pairs its two codes with NOTE in a loop.
    "community.missing-contributing": "note",
    "community.missing-code-of-conduct": "note",
    # docs.py builds these two codes from the README topics, as notes.
    "docs.missing-installation": "note",
    "docs.missing-usage": "note",
    # license.py hands these to LicenseCheck._warning().
    "license.missing": "warning",
    "license.unreadable": "warning",
    "license.too-short": "warning",
    # python_versions.py hands these to PythonVersionsCheck._note().
    "python_versions.allows-eol": "note",
    "python_versions.invalid-requires-python": "note",
    "python_versions.missing-requires-python": "note",
    # runner.py hands these to _check_failed(), which builds an ERROR.
    "pycmdcheck.check-raised": "error",
    "pycmdcheck.check-exited": "error",
    "pycmdcheck.invalid-result": "error",
}


@pytest.fixture
def docs_dir(repo_root: Path) -> Path:
    """Return ``docs/``, or skip the test when the tree has none."""
    docs = repo_root / "docs"
    if not docs.is_dir():
        pytest.skip("needs docs/, which a source distribution does not ship")
    return docs


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _check_classes(names: Iterable[str]) -> dict[str, type]:
    """Return the class of each of the built-in checks *names*, by check name."""
    checks = discovery.discover_checks()
    return {name: checks[name] for name in sorted(names)}


def _class_path(cls: type) -> str:
    """Return a class as the API reference names it."""
    return f"checks.{cls.__module__.rpartition('.')[2]}.{cls.__name__}"


def _module_source(cls: type) -> str:
    return inspect.getsource(sys.modules[cls.__module__])


def _all_codes() -> set[str]:
    """Return every rule code: those of the checks and the runner's own."""
    classes = _check_classes(REGISTERED).values()
    return {code for cls in classes for code in cls.rules} | set(runner.RUNNER_RULES)


# ── What the source says ──────────────────────────────────────────────


def _status_names(nodes: Iterable[ast.AST]) -> set[str]:
    """Return the statuses that *nodes* name as ``CheckStatus.NAME``."""
    return {
        node.attr.lower()
        for node in nodes
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id == "CheckStatus"
    }


def _calls(source: str) -> Iterator[tuple[str, list[ast.expr]]]:
    """Yield the name and the arguments of every call in *source*.

    The name is written as in the source for a plain name and for one
    attribute of a name, such as ``Finding`` and ``CheckResult.skip``.
    """
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "id", "")
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            name = f"{func.value.id}.{func.attr}"
        yield name, [*node.args, *(keyword.value for keyword in node.keywords)]


def _rule_statuses(source: str, codes: Collection[str]) -> dict[str, set[str]]:
    """Return the statuses of the *codes* that one call in *source* settles.

    Such a call is handed the code and a ``CheckStatus`` side by side, as in
    ``Finding("ci.missing", "No CI configuration found", CheckStatus.NOTE)``.
    A ``Finding`` that is given no status is a note.
    """
    found: dict[str, set[str]] = {}
    for name, arguments in _calls(source):
        statuses = _status_names(arguments)
        if not statuses and name == "Finding":
            statuses = {"note"}
        for argument in arguments:
            if isinstance(argument, ast.Constant) and argument.value in codes:
                found.setdefault(argument.value, set()).update(statuses)
    return {code: statuses for code, statuses in found.items() if statuses}


def _skip_reasons_named(source: str) -> set[str]:
    """Return the skip reasons that *source* writes out as strings."""
    return {
        node.value
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Constant) and node.value in SKIP_REASONS
    }


def _statuses_read_from_source() -> dict[str, set[str]]:
    """Return the statuses of every rule that the source settles.

    The result for a tool that could not run is built by a shared function,
    so its status counts for every check module that calls the function.
    """
    shared = subprocess_runner.could_not_run
    shared_statuses = _status_names(ast.walk(ast.parse(inspect.getsource(shared))))
    assert shared_statuses, "could_not_run no longer names a status"

    found = _rule_statuses(inspect.getsource(runner), runner.RUNNER_RULES)
    for name, cls in _check_classes(REGISTERED).items():
        source = _module_source(cls)
        found.update(_rule_statuses(source, cls.rules))
        if shared.__name__ in {called for called, _ in _calls(source)}:
            found[f"{name}.could-not-run"] = shared_statuses
    return found


def _statuses_in_code() -> dict[str, set[str]]:
    """Return the statuses of every rule code."""
    elsewhere = {code: {status} for code, status in _STATUS_SET_ELSEWHERE.items()}
    return {**elsewhere, **_statuses_read_from_source()}


def test_explicit_status_table_holds_what_the_source_cannot_settle() -> None:
    """A code is in the table if, and only if, no call gives its status."""
    unsettled = _all_codes() - set(_statuses_read_from_source())
    assert sorted(_STATUS_SET_ELSEWHERE) == sorted(unsettled)


# ── Checks and profiles ───────────────────────────────────────────────


def test_every_check_has_a_configuration_section(docs_dir: Path) -> None:
    headings = set(_CHECK_HEADING_RE.findall(_read(docs_dir / "configuration.qmd")))
    # Both ways round: a deprecated check keeps its section, with the note
    # that says what replaced it, and a removed check loses it.
    assert sorted(headings) == sorted(REGISTERED)


def test_api_reference_lists_every_check_class(docs_dir: Path) -> None:
    text = _read(docs_dir / "_quarto.yml")
    listed = set(_REFERENCE_ENTRY_RE.findall(text))
    registered = {_class_path(cls) for cls in _check_classes(REGISTERED).values()}
    # Both ways round: a class that no longer exists breaks the docs build.
    assert listed - _BASE_CLASSES == registered

    # The section of built-in checks holds none of the deprecated ones.
    sections = _REFERENCE_SECTION_RE.split(text)
    (built_in,) = (s for s in sections if s.startswith("Built-in Checks"))
    current = {_class_path(cls) for cls in _check_classes(ALL_CHECKS).values()}
    assert set(_REFERENCE_ENTRY_RE.findall(built_in)) == current


def test_readme_tables_name_every_check(repo_root: Path) -> None:
    readme = repo_root / "README.md"
    if not readme.is_file():
        pytest.skip("needs README.md")
    assert sorted(ALL_CHECKS - set(_FIRST_CELL_RE.findall(_read(readme)))) == []


def test_pages_give_the_number_of_built_in_checks(
    docs_dir: Path, repo_root: Path
) -> None:
    pages = [*docs_dir.glob("*.qmd"), docs_dir / "_quarto.yml", repo_root / "README.md"]
    wrong = [
        f"{page.name} says {count}"
        for page in pages
        if page.is_file()
        for count in _CHECK_COUNT_RE.findall(_read(page))
        if int(count) != len(ALL_CHECKS)
    ]
    assert sorted(wrong) == []


def test_cli_reference_names_every_profile(docs_dir: Path) -> None:
    named = set(_FIRST_CELL_RE.findall(_read(docs_dir / "cli.qmd")))
    assert sorted(set(PROFILES) - named) == []


def test_cli_reference_gives_the_checks_of_every_profile(docs_dir: Path) -> None:
    """A profile's row names its checks, gives their number, or does both."""
    text = _read(docs_dir / "cli.qmd")
    for name, profile in PROFILES.items():
        row = re.search(rf"^\|\s*`{name}`\s*\|([^|\n]*)\|", text, re.MULTILINE)
        assert row is not None, f"no row for the {name} profile"
        listed = set(_NAME_RE.findall(row[1])) & REGISTERED
        counts = re.findall(r"(\d+) (?:built-in )?checks", row[1])
        assert listed or counts, name
        assert not listed or listed == profile.checks, name
        assert {int(count) for count in counts} <= {len(profile.checks)}, name


def test_cli_reference_gives_the_options_of_every_profile(docs_dir: Path) -> None:
    """A profile's row gives each check the options the profile sets for it.

    Both ways round, and with the values. An option under the wrong check,
    a missing option, an extra one and a wrong value each fail.
    """
    text = _read(docs_dir / "cli.qmd")
    for name, profile in PROFILES.items():
        row = re.search(rf"^\|\s*`{name}`\s*\|[^|\n]*\|([^|\n]*)\|", text, re.MULTILINE)
        assert row is not None, f"no row for the {name} profile"
        # The cell has one part for each check, such as "`typing` gets
        # `tool = "mypy"` and `strict = true`."
        before, *parts = _GETS_RE.split(row[1])
        assert _SETTING_RE.findall(before) == [], f"{name}: an option of no check"
        documented = {
            (check, option, value)
            for check, options in zip(parts[::2], parts[1::2], strict=True)
            for option, value in _SETTING_RE.findall(options)
        }
        # The page writes a value as a TOML file does, which is how JSON
        # spells true, 80 and "ruff".
        in_code = {
            (check, option, json.dumps(value))
            for check, options in profile.config_overrides.items()
            for option, value in options.items()
        }
        assert documented == in_code, name


def test_cli_reference_lists_every_option(docs_dir: Path) -> None:
    """Both ways round: a new option needs a row, and a removed one loses it."""
    sections = _SECTION_RE.split(_read(docs_dir / "cli.qmd"))
    (section,) = (s for s in sections if s.startswith("Options"))
    # The option table comes before the first subsection. The subsections
    # have tables of their own, which name some of the options again.
    table = section.partition("\n### ")[0]
    documented = {
        word
        for cell in _OPTION_CELL_RE.findall(table)
        for word in cell.replace(",", " ").split()
        if word.startswith("-")
    }
    # The command's own settings give the help option its short name.
    with click.Context(main, **main.context_settings) as context:
        parameters = main.get_params(context)
    in_code = {
        name
        for parameter in parameters
        for name in parameter.opts
        if name.startswith("-")
    }
    assert documented == in_code


def test_configuration_page_lists_the_options_of_a_config_file(docs_dir: Path) -> None:
    """Both ways round, for every check that a ``--config`` file can configure."""
    sections = _SECTION_RE.split(_read(docs_dir / "configuration.qmd"))
    (section,) = (s for s in sections if s.startswith("A Settings File"))
    documented = {
        name: set(_NAME_RE.findall(options))
        for name, options in _TWO_CELL_ROW_RE.findall(section)
        # The other tables of the section start their rows with an option.
        if name in REGISTERED
    }
    assert documented == {name: set(options) for name, options in _FILE_OPTIONS.items()}


def test_cli_reference_names_every_json_key(docs_dir: Path, tmp_path: Path) -> None:
    """The key tables cover the report, each of its results and each finding."""
    # The license check only reads files, so nothing is started or fetched.
    # An empty directory has no license file, which gives one finding.
    result = CliRunner().invoke(main, [str(tmp_path), "-c", "license", "--json"])
    report = json.loads(result.stdout)
    (license_result,) = report["results"]
    named = set(_FIRST_CELL_RE.findall(_read(docs_dir / "cli.qmd")))
    assert sorted(set(report) - named) == []
    assert sorted(set(license_result) - named) == []
    assert sorted(set(license_result["findings"][0]) - named) == []


def test_report_examples_show_every_key(
    docs_dir: Path, repo_root: Path, tmp_path: Path
) -> None:
    """An example of a whole report has the keys of a real one, in its order."""
    result = CliRunner().invoke(main, [str(tmp_path), "-c", "license", "--json"])
    keys = list(json.loads(result.stdout))
    examples = [
        (page.name, list(json.loads(block)))
        for page in [*docs_dir.glob("*.qmd"), repo_root / "README.md"]
        if page.is_file()
        for block in _JSON_BLOCK_RE.findall(_read(page))
        # A whole report starts with the path of the package.
        if '"package_path"' in block
    ]
    assert examples, "no page shows a whole report"
    assert [name for name, shown in examples if shown != keys] == []


# ── Rule codes, statuses and skip reasons ─────────────────────────────


def test_rules_page_lists_every_code_with_its_status(docs_dir: Path) -> None:
    """Both ways round: no code is left out, and none is listed that is gone."""
    rows = _RULE_ROW_RE.findall(_read(docs_dir / "rules.qmd"))
    documented = {code: set(statuses.split(", ")) for code, statuses in rows}
    assert len(rows) == len(documented), "a code has more than one row"
    assert documented == _statuses_in_code()


def test_rules_page_puts_every_code_under_its_check(docs_dir: Path) -> None:
    """Every row of a rule is in the section of the check its code names."""
    text = _read(docs_dir / "rules.qmd")
    sections = _SECTION_RE.split(text)
    (section,) = (s for s in sections if s.startswith("Rules of Each Check"))
    # The parts are the text before the first heading, then a check name and
    # the text under its heading, in turn.
    _, *parts = _CHECK_HEADING_RE.split(section)
    placed = {
        code: heading
        for heading, body in zip(parts[::2], parts[1::2], strict=True)
        for code, _ in _RULE_ROW_RE.findall(body)
    }
    # Both ways round: a row outside these sections is in no section at all.
    rows = _RULE_ROW_RE.findall(text)
    assert placed == {code: code.partition(".")[0] for code, _ in rows}


def test_rules_page_gives_every_code_its_hint(docs_dir: Path) -> None:
    """The last cell of a rule's row holds the words of the hint in the code.

    The page writes option names, file names and table names as code, so the
    backticks are left out before the two are compared. The test cannot tell
    whether the right words are marked. It only counts the backticks, since
    one that is lost leaves a mark that never closes.
    """
    rows = _HINT_ROW_RE.findall(_read(docs_dir / "rules.qmd"))
    assert [code for code, hint in rows if hint.count("`") % 2] == []
    documented = {code: hint.replace("`", "") for code, hint in rows}
    hints = dict(runner.RUNNER_HINTS)
    for cls in _check_classes(REGISTERED).values():
        hints.update(cls.hints)
    assert documented == {code: hints.get(code, "") for code in _all_codes()}


def test_pages_name_no_code_that_does_not_exist(
    docs_dir: Path, repo_root: Path
) -> None:
    """A code in an example or in running text is a code of some check."""
    owners = REGISTERED | {"pycmdcheck"}
    known = _all_codes()
    unknown = [
        f"{page.name}: {code}"
        for page in [*docs_dir.glob("*.qmd"), repo_root / "README.md"]
        if page.is_file()
        for match in _WRITTEN_CODE_RE.findall(_read(page))
        for code in match
        # A name such as `copyright.missing-header` belongs to an example of
        # a custom check, not to a built-in one.
        if code.partition(".")[0] in owners and code not in known
    ]
    assert sorted(set(unknown)) == []


def test_status_table_matches_the_rules_of_each_check(docs_dir: Path) -> None:
    """The table lists, for each check, the statuses its results can have.

    Those are the statuses of its rules, OK if its module names that status
    or takes the status of a list of findings that may be empty, and SKIPPED
    if its module builds a skipped result.
    """
    rows = _STATUS_ROW_RE.findall(_read(docs_dir / "configuration.qmd"))
    documented = {name: set(statuses.split(", ")) for name, statuses in rows}

    rule_statuses = _statuses_in_code()
    in_code: dict[str, set[str]] = {}
    for name, cls in _check_classes(ALL_CHECKS).items():
        source = _module_source(cls)
        named = _status_names(ast.walk(ast.parse(source)))
        called = {called for called, _ in _calls(source)}
        statuses = {status for code in cls.rules for status in rule_statuses[code]}
        if "ok" in named or "most_severe" in called:
            statuses.add("ok")
        if "skipped" in named or "CheckResult.skip" in called:
            statuses.add("skipped")
        in_code[name] = statuses
    # A deprecated check is always skipped and has no row.
    assert documented == in_code


def test_rules_page_lists_every_skip_reason_with_its_checks(docs_dir: Path) -> None:
    """Each reason is listed with the checks whose module writes it out."""
    sections = _SECTION_RE.split(_read(docs_dir / "rules.qmd"))
    (section,) = (s for s in sections if s.startswith("Skip Reasons"))
    rows = _REASON_ROW_RE.findall(section)
    documented = {reason: set(_NAME_RE.findall(checks)) for reason, checks in rows}

    in_code: dict[str, set[str]] = {reason: set() for reason in SKIP_REASONS}
    for name, cls in _check_classes(REGISTERED).items():
        for reason in _skip_reasons_named(_module_source(cls)):
            in_code[reason].add(name)
    assert documented == in_code


# ── The version of the release ────────────────────────────────────────

# A command pinned to one release, such as "uvx pycmdcheck@1.2.3".
_PINNED_RE = re.compile(r"pycmdcheck@(\d+\.\d+\.\d+)")
# The version in a sample of a JSON report.
_SAMPLE_VERSION_RE = re.compile(r'"pycmdcheck_version": "(\d+\.\d+\.\d+)"')


def test_pinned_versions_name_the_version_of_the_package(repo_root: Path) -> None:
    """A page that pins a version pins the one this tree would release.

    The bump settings in pyproject.toml rewrite these places, and this test
    fails for a place they missed.
    """
    pyproject = tomllib.loads(_read(repo_root / "pyproject.toml"))
    version = pyproject["project"]["version"]
    assert pyproject["tool"]["bumpversion"]["current_version"] == version
    pages = [repo_root / "README.md", *(repo_root / "docs").glob("*.qmd")]
    stale = [
        f"{page.name}: {found}"
        for page in pages
        if page.is_file()
        for pattern in (_PINNED_RE, _SAMPLE_VERSION_RE)
        for found in pattern.findall(_read(page))
        if found != version
    ]
    assert stale == []
    # The README shows one pinned command, so the settings have a line to keep.
    assert _PINNED_RE.search(_read(repo_root / "README.md"))


# A release heading of the changelog, such as "# pycmdcheck 1.2.3".
_RELEASE_HEADING_RE = re.compile(r"^# pycmdcheck (.+)$", re.MULTILINE)
_UNRELEASED = "(development version)"


def test_changelog_starts_with_this_version_or_unreleased_work(
    repo_root: Path,
) -> None:
    """The top of the changelog is this version, or the work after it.

    A bump renames the heading for unreleased work to the new version. Were
    that heading missing, the bump would leave the changelog alone and the
    release would have no section, so this test fails on the bump.
    """
    pyproject = tomllib.loads(_read(repo_root / "pyproject.toml"))
    version = pyproject["project"]["version"]
    headings = _RELEASE_HEADING_RE.findall(_read(repo_root / "CHANGELOG.md"))
    released = [heading for heading in headings if heading != _UNRELEASED]
    assert headings[0] in {version, _UNRELEASED}
    assert released[0] == version
    assert headings.count(_UNRELEASED) <= 1


def test_workflow_adds_the_heading_that_the_bump_renames(repo_root: Path) -> None:
    """The heading a release leaves behind is the one the bump looks for."""
    workflow = repo_root / ".github" / "workflows" / "development-version.yml"
    if not workflow.is_file():
        pytest.skip("needs .github/, which a source distribution does not ship")
    pyproject = tomllib.loads(_read(repo_root / "pyproject.toml"))
    renamed = [
        entry["search"]
        for entry in pyproject["tool"]["bumpversion"]["files"]
        if entry.get("filename") == "CHANGELOG.md"
    ]
    assert renamed == [f"# pycmdcheck {_UNRELEASED}"]
    assert f"heading='{renamed[0]}'" in _read(workflow)
