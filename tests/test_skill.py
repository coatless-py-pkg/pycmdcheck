"""Keep the Agent Skill in step with the program.

``skills/pycmdcheck/SKILL.md`` tells an AI assistant how to use pycmdcheck, so
a name in it that the program does not have sends the assistant wrong. These
tests compare names. The wording around them can change. Six things are fixed.
Every ``--word`` in the skill is an option of pycmdcheck. A name that follows
``--profile``, ``--check`` or ``--skip`` is a profile or a check. A plain name
in backticks, such as ``skip_reason``, is a check, a profile, a status or a
key of the JSON report. A name with hyphens in backticks is a skip reason. A
name with a dot in backticks, such as ``license.missing``, is a rule code or
one of the file names in ``_FILES``. The JSON example is the result of the
license check for an empty directory, word for word, without the keys an
assistant need not read. The first test keeps the frontmatter within the
format and the body short.

The last two tests read the files in ``.claude-plugin/``. They make the
repository a Claude Code plugin that holds the skill, and a marketplace that
lists that one plugin. The names in them are those of the skill. The version
and the two web addresses are those of the package.
"""

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import click
import pytest
from click.testing import CliRunner

from pycmdcheck.cli import main
from pycmdcheck.profiles import ALL_CHECKS, DEPRECATED_CHECKS, PROFILES
from pycmdcheck.results import SKIP_REASONS

SKILL = Path("skills", "pycmdcheck", "SKILL.md")
PLUGIN = Path(".claude-plugin", "plugin.json")
MARKETPLACE = Path(".claude-plugin", "marketplace.json")
CHECKS = ALL_CHECKS | DEPRECATED_CHECKS
# The file names the skill writes in backticks. They are not rule codes.
_FILES = {"py.typed"}

# A long option, such as "--exit-zero".
_OPTION_RE = re.compile(r"(?<![\w-])--[a-z][a-z-]*")
# The names that follow an option in a command, as in "--check tests,build".
_VALUE_RE = re.compile(r"--(profile|check|skip) ([a-z_,]+)")
# A plain name in backticks, such as `skip_reason` or `triage`.
_WORD_RE = re.compile(r"`([a-z0-9_]+)`")
# A name with hyphens in backticks, such as `tool-missing`.
_REASON_RE = re.compile(r"`([a-z]+(?:-[a-z]+)+)`")
# A name with a dot in backticks, such as `license.missing`.
_CODE_RE = re.compile(r"`(\w+\.[\w-]+)`")
# A JSON example, from the line that opens it to the line that closes it.
_JSON_BLOCK_RE = re.compile(r"^```json\n(.*?)^```$", re.MULTILINE | re.DOTALL)


@pytest.fixture
def skill(repo_root: Path) -> str:
    """Return the text of the skill, or skip the test when the tree has none."""
    if not (repo_root / SKILL).is_file():
        pytest.skip("needs skills/")
    return (repo_root / SKILL).read_text(encoding="utf-8")


@pytest.fixture
def plugin(repo_root: Path) -> tuple[str, str]:
    """Return the text of the plugin manifest and of the marketplace file.

    The test is skipped when the tree has no ``.claude-plugin/``. A tree that
    has the directory must hold both files, so a missing one fails the test.
    """
    if not (repo_root / PLUGIN.parent).is_dir():
        pytest.skip("needs .claude-plugin/, which a source distribution does not ship")
    paths = (repo_root / PLUGIN, repo_root / MARKETPLACE)
    manifest, marketplace = (path.read_text(encoding="utf-8") for path in paths)
    return manifest, marketplace


def _json(*arguments: str) -> Any:
    """Return what pycmdcheck prints for a listing or for a check that reads files."""
    return json.loads(CliRunner().invoke(main, [*arguments, "--json"]).stdout)


def test_frontmatter_follows_the_format(skill: str) -> None:
    """The limits are those of the Agent Skills specification, except the last one."""
    _, header, body = skill.split("---\n", 2)
    lines = (line.partition(": ") for line in header.splitlines())
    fields = {key: value for key, _, value in lines}
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", fields["name"])
    assert len(fields["name"]) <= 64 and fields["name"] == SKILL.parent.name
    assert 1 <= len(fields["description"]) <= 1024
    assert len(fields.get("compatibility", "")) <= 500
    # A value with a colon and a space in it would need quotes in YAML.
    assert [key for key, value in fields.items() if ": " in value] == []
    # The person approves each command, and "check my package" loads the skill.
    assert not {"allowed-tools", "disable-model-invocation"} & set(fields)
    assert "\N{EM DASH}" not in skill
    # The format allows 500 lines. This skill stays under 150, because an
    # assistant keeps the whole body in mind once the skill is loaded.
    assert len(body.splitlines()) < 150


def test_every_option_exists(skill: str) -> None:
    with click.Context(main, **main.context_settings) as context:
        options = {name for param in main.get_params(context) for name in param.opts}
    assert sorted(set(_OPTION_RE.findall(skill)) - options) == []


def test_every_profile_check_skip_reason_and_rule_code_exists(skill: str) -> None:
    for option, names in _VALUE_RE.findall(skill):
        known = set(PROFILES) if option == "profile" else CHECKS
        assert sorted(set(names.split(",")) - known) == [], f"--{option}"
    assert sorted(set(_REASON_RE.findall(skill)) - SKIP_REASONS) == []
    codes = {rule["code"] for rule in _json("--list-rules")}
    assert sorted(set(_CODE_RE.findall(skill)) - codes - _FILES) == []


def test_every_key_of_the_report_exists(skill: str, tmp_path: Path) -> None:
    """The example is compared word for word, so a key that is gone fails."""
    report = _json(str(tmp_path), "--check", "license")
    (result,) = report["results"]
    (example,) = (json.loads(block) for block in _JSON_BLOCK_RE.findall(skill))
    assert sorted(set(example) - set(result)) == []
    assert example == {key: result[key] for key in example}
    # The keys of the summary are the five statuses. The skill also writes
    # the name of the program in backticks.
    keys = {*report, *report["summary"], *result, *result["findings"][0]}
    known = keys | CHECKS | set(PROFILES) | {"pycmdcheck"}
    assert sorted(set(_WORD_RE.findall(skill)) - known) == []


def test_plugin_manifest_names_the_skill_and_the_version(
    plugin: tuple[str, str], repo_root: Path
) -> None:
    """Claude Code asks for a name only, and reads the version to find an update."""
    text, _ = plugin
    manifest = json.loads(text)
    assert manifest["name"] == SKILL.parent.name
    # An update reaches a person only when this version changes, so it
    # follows the version of the package.
    pyproject = (repo_root / "pyproject.toml").read_text(encoding="utf-8")
    project = tomllib.loads(pyproject)["project"]
    assert manifest["version"] == project["version"]
    # A homepage that is not a web address keeps the plugin from loading.
    assert manifest["homepage"] == project["urls"]["Documentation"]
    assert manifest["repository"] == project["urls"]["Repository"]
    assert "email" not in manifest["author"]
    # The values are searched, so an em dash that the file writes as an
    # escape is found too.
    assert "\N{EM DASH}" not in json.dumps(manifest, ensure_ascii=False)


def test_marketplace_lists_the_repository_as_its_one_plugin(
    plugin: tuple[str, str],
) -> None:
    """Claude Code asks for a name, an owner with a name and a list of plugins."""
    manifest, text = plugin
    marketplace = json.loads(text)
    assert {"name", "owner", "plugins"} <= set(marketplace)
    # A person types this name after the @, as in pycmdcheck@pycmdcheck.
    assert marketplace["name"] == SKILL.parent.name
    assert marketplace["owner"]["name"]
    assert "email" not in marketplace["owner"]
    # The plugin is the repository itself, and "./" is its root.
    (entry,) = marketplace["plugins"]
    assert entry["source"] == "./"
    # A person installs the plugin by the name of the entry.
    assert entry["name"] == json.loads(manifest)["name"]
    assert "\N{EM DASH}" not in json.dumps(marketplace, ensure_ascii=False)
