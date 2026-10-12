"""Command-line interface for pycmdcheck."""

import html
import io
import json
import logging
import re
import sys
import textwrap
from pathlib import Path
from typing import Any

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from pycmdcheck import __version__, _skill, discovery
from pycmdcheck.config import _load_config_file, is_check_enabled, load_config
from pycmdcheck.discovery import list_available_checks
from pycmdcheck.fileio import plain_text
from pycmdcheck.profiles import DEPRECATED_CHECKS, PROFILES
from pycmdcheck.pyproject_reader import get_dict, get_tool_table
from pycmdcheck.results import CheckResult, CheckStatus, Report
from pycmdcheck.runner import RUNNER_HINTS, RUNNER_RULES, hints_of, run_checks

logger = logging.getLogger(__name__)

console = Console()

EXIT_FINDINGS = 1
EXIT_INTERNAL_ERROR = 3

JSON_SCHEMA_VERSION = 1

# The --fail-on levels, from most to least severe.
_SEVERITY = ("error", "warning", "note")

_NO_CHECKS = "No checks are registered. Is pycmdcheck installed correctly?"

# What a report puts before a hint.
_HINT_LABEL = "Suggestion:"

# Characters that Markdown reads as markup. A backslash makes each one literal.
_MARKDOWN_ESCAPES = str.maketrans({char: f"\\{char}" for char in "\\`*_[]<>|~#&"})

# GitHub turns "@name" into a mention, ":word:" into an emoji and a plain web
# address into a link, inside an HTML block as well, and a backslash does not
# stop any of them. A zero-width space after the "@", the first colon, or
# inside the start of the address keeps the text as it was written.
_ZERO_WIDTH_SPACE = "\u200b"
_EMOJI_COLON_RE = re.compile(r":(?=[\w+-]+:)")
_WWW_RE = re.compile(r"(?i)\bwww\.")

# What would start a block of its own at the start of a list item: a list
# marker ("-", "+", "1." or "1)") or a line of hyphens, which is a rule. An
# asterisk is not listed because _MARKDOWN_ESCAPES has already made it literal.
_LIST_MARKER_RE = re.compile(r"^(-(?= |[- ]*$)|\+(?= |$)|\d+[.)](?= |$))")

# Status markers for consoles that cannot encode the usual glyphs.
_ASCII_SYMBOLS: dict[CheckStatus, str] = {
    CheckStatus.OK: "+",
    CheckStatus.NOTE: "i",
    CheckStatus.WARNING: "!",
    CheckStatus.ERROR: "x",
    CheckStatus.SKIPPED: "-",
}

_EPILOG = """\b
Exit codes:
  0  No check reached the --fail-on status (always 0 with --exit-zero)
  1  A check returned the --fail-on status or a more severe one
  2  Usage error, such as an unknown check name or a path that is not a directory
  3  pycmdcheck itself failed
"""


@click.command(context_settings={"help_option_names": ["-h", "--help"]}, epilog=_EPILOG)
@click.argument(
    "path", default=".", type=click.Path(exists=True, file_okay=False, dir_okay=True)
)
@click.option(
    "-c",
    "--check",
    metavar="NAME",
    multiple=True,
    help=(
        "Run only the named checks. Separate the names with commas or repeat "
        "the option."
    ),
)
@click.option(
    "-s",
    "--skip",
    metavar="NAME",
    multiple=True,
    help="Skip the named checks. Separate the names with commas or repeat the option.",
)
@click.option(
    "--fail-on",
    multiple=True,
    type=click.Choice(_SEVERITY),
    help=(
        "Exit with code 1 when a check returns this status or a more severe "
        "one. The order is note, warning, error, so warning also fails on "
        "errors and note fails on all three. If the option is repeated, the "
        "least severe status counts. Default: the fail_on of the --config "
        "file when one is given, else the fail_on list in [tool.pycmdcheck], "
        "or error."
    ),
)
@click.option(
    "--exit-zero",
    "exit_zero",
    is_flag=True,
    help=(
        "Always exit 0 regardless of check results (report status via output "
        "or --json only). The exit code then reflects whether pycmdcheck ran, "
        "not what it found. Useful for CI that parses the results itself."
    ),
)
@click.option(
    "--list",
    "list_checks",
    is_flag=True,
    help="List available checks and exit.",
)
@click.option(
    "-v",
    "--verbose",
    is_flag=True,
    help="Show the details of each check and a suggestion for each finding.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output results as JSON. The same as --format json.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(["text", "json", "markdown"]),
    default=None,
    help=(
        "Report format: text (the default), json, or markdown for a pull "
        "request or issue comment."
    ),
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, writable=True, allow_dash=True),
    default=None,
    metavar="FILE",
    help=(
        "Write the report or listing to FILE as UTF-8, in the chosen format, "
        "and print nothing on standard output. The directory of FILE must "
        "exist. A FILE of - means standard output."
    ),
)
@click.option(
    "--no-parallel",
    is_flag=True,
    help="Run checks sequentially instead of in parallel.",
)
@click.option(
    "-x",
    "--fail-fast",
    "fail_fast",
    is_flag=True,
    help=(
        "Stop after the first check that returns an error. Checks that are "
        "already running finish, and the rest are reported as skipped."
    ),
)
@click.option(
    "--profile",
    type=click.Choice(list(PROFILES)),
    default=None,
    help=(
        "Use a predefined check profile. The name default is a deprecated "
        "alias of core."
    ),
)
@click.option(
    "--config",
    "config_file",
    type=click.Path(exists=True, dir_okay=False),
    default=None,
    metavar="FILE",
    help=(
        "Take the profile, the checks and their options from FILE, a TOML "
        "file kept outside the directory that is checked. The file names a "
        "profile and can take checks out of it. The [tool.pycmdcheck] table "
        "of the checked directory is then not read. Cannot be used with "
        "--profile."
    ),
)
@click.option(
    "--list-profiles",
    "list_profiles_flag",
    is_flag=True,
    help="List the profiles, with their checks and the options they set, and exit.",
)
@click.option(
    "--list-rules",
    "list_rules_flag",
    is_flag=True,
    help=(
        "List the rules of every check, with their codes and a suggestion for "
        "each, and exit."
    ),
)
@click.option(
    "--install-skill",
    "install_skill",
    # The value can be left out. No default is given, because click 8.3
    # takes a default to mean that the option always needs its value.
    is_flag=False,
    flag_value=_skill.DEFAULT_DIRECTORY,
    type=str,
    metavar="[DIR]",
    help=(
        "Copy the Agent Skill of this version of pycmdcheck, which tells an "
        "AI assistant how to run it, to DIR/pycmdcheck and exit. The last "
        "part of DIR must be named skills. Without DIR it is .claude/skills "
        "in the current directory."
    ),
)
@click.option("--debug", is_flag=True, help="Enable debug logging output.")
@click.version_option(package_name="pycmdcheck", prog_name="pycmdcheck")
def main(
    path: str,
    check: tuple[str, ...],
    skip: tuple[str, ...],
    fail_on: tuple[str, ...],
    exit_zero: bool,
    list_checks: bool,
    verbose: bool,
    json_output: bool,
    output_format: str | None,
    output: str | None,
    no_parallel: bool,
    fail_fast: bool,
    profile: str | None,
    config_file: str | None,
    list_profiles_flag: bool,
    list_rules_flag: bool,
    install_skill: str | None,
    debug: bool,
) -> None:
    """Check Python package quality.

    PATH is the directory to check (default: current directory).

    Examples:

        pycmdcheck                    # Check current directory

        pycmdcheck /path/to/package   # Check specific directory

        pycmdcheck --check tests,linting  # Run only these two

        pycmdcheck --skip typing      # Skip type checking

        pycmdcheck --fail-on warning  # Fail on warnings as well as errors

        pycmdcheck --format markdown --output report.md  # Report for a comment
    """
    _make_streams_safe()

    if install_skill is not None:
        # Like a listing, this runs no check. It also reads no settings, so
        # it comes before everything else.
        try:
            click.echo(_skill.install(install_skill))
        except ValueError as exc:
            raise click.UsageError(str(exc)) from exc
        return

    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s: %(message)s",
        )

    if json_output and output_format not in (None, "json"):
        raise click.UsageError(
            "--json means --format json, so it cannot be used with "
            f"--format {output_format}."
        )
    output_format = "json" if json_output else output_format or "text"
    output = _output_file(output)

    # A config file is read before anything else, so a mistake in it costs
    # no run and is reported for a listing as well.
    file_checks: list[str] | None = None
    file_config: dict[str, Any] = {}
    if config_file is not None:
        if profile is not None:
            raise click.UsageError(
                "--config cannot be used with --profile. The config file "
                "names the profile."
            )
        installed = discovery.discover_checks()
        if not installed:
            # The file is not at fault, and would be blamed for every name.
            raise click.UsageError(_NO_CHECKS)
        try:
            profile, file_checks, file_config = _load_config_file(
                Path(config_file), installed
            )
        except ValueError as exc:
            raise click.UsageError(f"Config file {config_file}: {exc}") from exc

    # Text is printed as the run goes. JSON, Markdown and anything meant for
    # --output are collected here and delivered once the run is over.
    buffer = io.StringIO()
    con = Console(file=buffer, force_terminal=False) if output is not None else console

    if list_profiles_flag or list_checks:
        if output_format == "json" and list_profiles_flag:
            buffer.write(json.dumps(_list_profiles(), indent=2) + "\n")
        elif output_format == "json":
            buffer.write(_json_listing(list_available_checks()))
        elif list_profiles_flag:
            _show_profiles(con)
        else:
            _show_available_checks(con)
        _deliver(buffer.getvalue(), output)
        return

    if list_rules_flag:
        if output_format == "json":
            buffer.write(json.dumps(_list_rules(), indent=2) + "\n")
        else:
            _show_rules(con)
        _deliver(buffer.getvalue(), output)
        return

    # Validate check names
    available = discovery.discover_checks()
    if not available:
        raise click.UsageError(_NO_CHECKS)
    check = _names(check, "--check")
    skip = _names(skip, "--skip")
    invalid = sorted(set(check) - set(available))
    if invalid:
        raise click.UsageError(
            f"Unknown check(s): {', '.join(invalid)}. "
            f"Available checks: {', '.join(available)}"
        )
    # A misspelled name would leave in the check it was meant to take out.
    invalid = sorted(set(skip) - set(available))
    if invalid:
        raise click.UsageError(
            f"Unknown check(s) given to --skip: {', '.join(invalid)}. "
            f"Available checks: {', '.join(available)}"
        )

    if file_checks is not None:
        # A run from a file never holds a check from outside the file.
        outside = sorted(set(check) - set(file_checks))
        if outside:
            raise click.UsageError(
                f"Check(s) not run by the config file: {', '.join(outside)}. "
                f"It runs {', '.join(file_checks)}"
            )

    package_path = Path(path).resolve()
    # The checks the run starts from, worked out as run_checks() does.
    selected = set(check or file_checks or ())
    if not selected and profile:
        selected = set(PROFILES[profile].checks)
    elif not selected:
        # Only here does the project have a say in which checks run.
        written = get_dict(get_tool_table(package_path, "pycmdcheck"), "checks")
        selected = {
            name
            for name in set(available) - DEPRECATED_CHECKS
            if is_check_enabled({"checks": written}, name)
        }
    # A run that holds no check is a mistake, as a config file that leaves
    # none is, and not an empty report that looks like a pass. A project that
    # turns every check off made a choice, and no --skip is at fault then.
    if skip and selected and not selected - set(skip):
        raise click.UsageError("--skip leaves no check to run.")

    label = _profile_label(profile, bool(check), config_file)

    try:
        if output_format == "text":
            con.print(_header(package_path, label), soft_wrap=True)

        # With a config file the settings of the checked project are not read.
        config = file_config if config_file else load_config(package_path)
        effective_fail_on = _resolve_fail_on(fail_on, config)

        def run() -> Report:
            return run_checks(
                package_path=package_path,
                checks=list(check) or file_checks,
                skip=list(skip),
                config=config,
                parallel=not no_parallel,
                fail_fast=fail_fast,
                # The configuration from a file holds the profile already.
                profile=None if config_file else profile,
            )

        # A spinner only makes sense for text on a real terminal.
        spin = output_format == "text" and con is console
        if spin and console.is_terminal and sys.stdout.isatty():
            with console.status("[bold]Running checks...[/bold]"):
                report = run()
        else:
            report = run()
        failed = report.failed_on(effective_fail_on)

        if output_format == "json":
            document = {
                **report.to_dict(),
                "schema_version": JSON_SCHEMA_VERSION,
                "pycmdcheck_version": __version__,
                "profile": profile,
                "config_file": config_file,
                "fail_on": effective_fail_on,
                "failed": failed,
                "requested_checks": list(check) or None,
                "skipped_checks": list(skip),
            }
            buffer.write(json.dumps(document, indent=2) + "\n")
        elif output_format == "markdown":
            markdown = _render_markdown(report, verbose, effective_fail_on, label)
            buffer.write(markdown + "\n")
        else:
            _output_rich(report, verbose, con, fail_on=effective_fail_on)
    except Exception as exc:
        # Last resort: one line on stderr and an exit code of its own, so a
        # caller can tell this from a report of findings. With JSON or
        # Markdown nothing has been written to stdout at this point.
        logger.debug("Internal error", exc_info=True)
        click.echo(f"pycmdcheck: internal error: {type(exc).__name__}: {exc}", err=True)
        sys.exit(EXIT_INTERNAL_ERROR)

    _deliver(buffer.getvalue(), output)

    # With --exit-zero, findings never change the exit code (the caller is
    # expected to read the results), so a non-zero exit then reflects only
    # that pycmdcheck itself could not run.
    if failed and not exit_zero:
        sys.exit(EXIT_FINDINGS)


def _make_streams_safe() -> None:
    """Keep output from failing on a console that is not UTF-8."""
    for stream in (sys.stdout, sys.stderr):
        encoding = (getattr(stream, "encoding", None) or "").lower()
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and "utf" not in encoding:
            reconfigure(errors="replace")


def _names(values: tuple[str, ...], option: str) -> tuple[str, ...]:
    """Return the check names given to --check or --skip, each of them once.

    A value holds one name, or several separated by commas. The white space
    around a name is ignored, and the order of first mention is kept.

    Raises:
        click.UsageError: If a value holds no name, or an empty name beside
            others. A script that builds an empty list would otherwise run
            every check.
    """
    names: list[str] = []
    for value in values:
        found = [name.strip() for name in value.split(",")]
        if not any(found):
            raise click.UsageError(f"{option} needs at least one check name.")
        if not all(found):
            # A space typed after a comma ends the value at the comma, and
            # the next name would be read as PATH.
            raise click.UsageError(
                f'{option} has an empty name in "{value}". Write the names '
                "with commas and no spaces, as in tests,linting."
            )
        names += found
    return tuple(dict.fromkeys(names))


def _resolve_fail_on(flag: tuple[str, ...], config: dict[str, Any]) -> list[str]:
    """Return the statuses that make the run exit with a failure.

    A status is a threshold: it brings every more severe status with it, and
    the least severe one that is named counts. The --fail-on option wins when
    it is given. Otherwise the fail_on list of the configuration is used,
    without its invalid values. That is the list of the --config file when
    one is given, and else the one from [tool.pycmdcheck]. The default is
    "error".

    Returns:
        The failing statuses, from most to least severe.
    """
    named: list[Any] = list(flag)
    if not named:
        configured = config.get("fail_on")
        named = configured if isinstance(configured, list) else []
    levels = [
        _SEVERITY.index(value)
        for value in named
        if isinstance(value, str) and value in _SEVERITY
    ]
    return list(_SEVERITY[: max(levels, default=0) + 1])


def _output_file(output: str | None) -> str | None:
    """Return the file named by --output, or None for standard output.

    Called before any check runs, so that a file name that cannot be used
    costs no run. click has already refused a directory and an existing file
    that is not writable.

    Raises:
        click.UsageError: If the value is empty or its directory is missing.
    """
    if output is None or output == "-":
        return None
    if not output:
        raise click.UsageError("--output needs a file name. Use - for standard output.")
    parent = Path(output).parent
    if not parent.is_dir():
        raise click.UsageError(
            f"Cannot write the report to {output}: "
            f"the directory {parent} does not exist."
        )
    return output


def _deliver(text: str, output: str | None) -> None:
    """Print the collected report or listing, or write it to the --output file."""
    if output is None:
        sys.stdout.write(text)
        return
    try:
        Path(output).write_text(text, encoding="utf-8")
    except OSError as exc:
        raise click.UsageError(
            f"Cannot write the report to {output}: {exc.strerror or exc}"
        ) from exc


def _profile_label(
    profile: str | None, narrowed: bool, config_file: str | None = None
) -> str:
    """Say which profile a run used, for the line that names the package.

    A config file is named beside the profile, without its directory.
    """
    if not profile:
        return ""
    source = f"profile: {profile}"
    if config_file:
        source += f", config: {plain_text(Path(config_file).name)}"
    return f" (options from {source})" if narrowed else f" ({source})"


def _header(package_path: Path, label: str) -> Text:
    """Build the first line of terminal output."""
    # A Text object is printed as it is, so the path is never read as markup.
    return Text.assemble(
        "\n", ("Checking package:", "bold"), f" {package_path}{label}\n"
    )


def _list_profiles() -> list[dict[str, Any]]:
    """Return every profile with its checks and the options it sets.

    pycmdcheck.profiles.list_profiles() is left as it is, because it returns
    (name, description) pairs and its callers unpack them.
    """
    return [
        {
            "name": profile.name,
            "description": profile.description,
            "checks": sorted(profile.checks),
            "options": profile.config_overrides,
        }
        for profile in PROFILES.values()
    ]


def _show_profiles(con: Console | None = None) -> None:
    """Display each profile with its checks and the options it sets."""
    con = con or console

    table = Table(title="Available Profiles")
    table.add_column("Name", style="cyan", no_wrap=True)
    table.add_column("Description, checks and options")

    for profile in _list_profiles():
        table.add_row(Text(profile["name"]), Text(profile["description"]))
        table.add_row("", Text(f"Runs {', '.join(profile['checks'])}"))
        for check, options in profile["options"].items():
            for option, value in options.items():
                # json.dumps writes true, 80 and "ruff" as a TOML file does.
                setting = f"{check}.{option} = {json.dumps(value)}"
                table.add_row("", Text(f"Sets {setting}"))
        table.add_section()

    con.print(table)


def _show_available_checks(con: Console | None = None) -> None:
    """Display list of available checks."""
    con = con or console
    checks = list_available_checks()

    table = Table(title="Available Checks")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for name, description in checks:
        table.add_row(Text(name), Text(description))

    con.print(table)


def _list_rules() -> list[dict[str, str | None]]:
    """Return every rule of every registered check, sorted by code.

    A check written before there were rules has none and adds nothing, and
    a table that is not a dict counts as none. A rule with no hint has None
    for it. The runner's own rules are listed under the check name
    "pycmdcheck".
    """
    tables = [
        (name, getattr(check_class, "rules", None), hints_of(check_class))
        for name, check_class in discovery.discover_checks().items()
    ]
    rules = [
        {
            "check": name,
            "code": code,
            "description": str(description),
            "hint": hints.get(code),
        }
        for name, descriptions, hints in [
            *tables,
            ("pycmdcheck", RUNNER_RULES, RUNNER_HINTS),
        ]
        if isinstance(descriptions, dict)
        for code, description in descriptions.items()
    ]
    return sorted(rules, key=lambda rule: str(rule["code"]))


def _show_rules(con: Console | None = None) -> None:
    """Display the rules of every registered check.

    A rule is a few lines and not the row of a table, so that its hint has
    the full width and the names in it stay whole.
    """
    con = con or console
    con.print(Text("Rules", style="bold"))
    for rule in _list_rules():
        con.print()
        con.print(Text.assemble((rule["code"] or "", "cyan"), f" ({rule['check']})"))
        _print_indented(con, rule["description"] or "")
        if rule["hint"]:
            _print_indented(con, rule["hint"], _HINT_LABEL)


def _print_indented(con: Console, text: str, label: str = "") -> None:
    """Print a line of a section two spaces in, wrapped to the console width.

    The rest of a long line starts two spaces further in, so that it cannot
    be taken for a new line. A line that has an indent of its own keeps it,
    and a line break in the text starts a new line. The label goes before
    the text, in bold.

    The text is wrapped here and not by Rich, which would start the rest of
    a long line at the left margin and cut a word that is longer than the
    line. Such a word, a path for one, is left whole here, and so is a name
    with hyphens in it.
    """
    # A plugin may give a detail that is not text, such as a path.
    text = f"{label} {text}" if label else str(text)
    wrapped = []
    for line in text.splitlines():
        body = line.lstrip(" ")
        indent = " " * (2 + len(line) - len(body))
        wrapped.append(
            textwrap.fill(
                body,
                con.width,
                initial_indent=indent,
                subsequent_indent=f"{indent}  ",
                break_long_words=False,
                break_on_hyphens=False,
            )
        )
    shown = Text("\n".join(wrapped))
    if label:
        # The label opens the first line, after its two spaces.
        shown.stylize("bold", 2, 2 + len(label))
    con.print(shown, soft_wrap=True)


def _json_listing(items: list[tuple[str, str]]) -> str:
    """Format a list of (name, description) pairs as a JSON array."""
    listing = [
        {"name": name, "description": description} for name, description in items
    ]
    return json.dumps(listing, indent=2) + "\n"


def _defuse(text: str) -> str:
    """Stop GitHub from reading text as a mention, an emoji code, or a link."""
    text = text.replace("://", f":{_ZERO_WIDTH_SPACE}//")
    text = _WWW_RE.sub(lambda m: f"{m[0][:-1]}{_ZERO_WIDTH_SPACE}.", text)
    text = _EMOJI_COLON_RE.sub(f":{_ZERO_WIDTH_SPACE}", text)
    return text.replace("@", f"@{_ZERO_WIDTH_SPACE}")


def _markdown(text: str) -> str:
    """Put text from a check on one line and make its Markdown literal."""
    return _defuse(" ".join(text.split())).translate(_MARKDOWN_ESCAPES)


def _markdown_item(text: str) -> str:
    """Format a detail line as a list item that holds nothing but its text."""
    # "- 1. text" would be a list inside the list, and "- 1\. text" is not.
    # The backslash goes before the last character of the marker.
    escaped = _LIST_MARKER_RE.sub(lambda m: f"{m[1][:-1]}\\{m[1][-1]}", _markdown(text))
    return f"- {escaped}"


def _hints(result: CheckResult) -> list[str]:
    """Return the distinct hints of a result's findings, in finding order."""
    return list(dict.fromkeys(f.hint for f in result.findings if f.hint))


def _render_markdown(
    report: Report, verbose: bool, fail_on: list[str], label: str
) -> str:
    """Render the report as Markdown for a pull request or issue comment.

    Text that comes from checks, the directory name and the profile label
    are escaped, so they cannot break the table, add markup of their own, or
    mention anyone.

    Args:
        report: The report to render.
        verbose: Whether to show the details of every result, not only of
            those that are not ok.
        fail_on: The failing statuses, as returned by _resolve_fail_on().
        label: The profile label from _profile_label().

    Returns:
        The Markdown text, without a final line break.
    """
    directory = Path(report.package_path).name or report.package_path
    lines = [
        "## pycmdcheck report",
        "",
        f"Checked {_markdown(directory + label)} with pycmdcheck {__version__}.",
        "",
    ]
    if not report.results:
        return "\n".join([*lines, "No checks were run."])

    lines += ["| Status | Check | Message |", "| --- | --- | --- |"]
    lines += [
        f"| {r.status.value} | {_markdown(r.name)} | {_markdown(r.message)} |"
        for r in report.results
    ]
    counts = report.count_by_status()
    found = ", ".join(f"{counts[s]} {s.value}" for s in CheckStatus if counts[s])
    lines += ["", f"**{found}. {_closing_line(report, fail_on)[0]}**"]

    for result in report.results:
        hints = _hints(result)
        if (result.details or hints) and (
            verbose or result.status is not CheckStatus.OK
        ):
            # The summary sits in an HTML block, where Markdown is not read.
            summary = html.escape(_defuse(f"{result.name}: {result.status.value}"))
            lines += [
                "",
                "<details>",
                f"<summary>{summary}</summary>",
                "",
                *(_markdown_item(detail) for detail in result.details),
                *(f"- **{_HINT_LABEL}** {_markdown(hint)}" for hint in hints),
                "",
                "</details>",
            ]
    return "\n".join(lines)


def _status_symbol(status: CheckStatus, encoding: str) -> str:
    """Return the status glyph, or an ASCII marker if it cannot be encoded."""
    try:
        status.symbol.encode(encoding)
    except (UnicodeEncodeError, LookupError):
        return _ASCII_SYMBOLS[status]
    return status.symbol


def _count(number: int, status: CheckStatus) -> str:
    """Format a count such as "2 warnings"."""
    plural = "" if number == 1 or status == CheckStatus.SKIPPED else "s"
    return f"{number} {status.value}{plural}"


# Skip reasons that say nothing was left unchecked that could have been checked.
_BENIGN_SKIP_REASONS = frozenset({"not-applicable", "deprecated"})


def _closing_line(report: Report, fail_on: list[str]) -> tuple[str, str]:
    """Return the closing line and its style.

    The line follows the same rule as the exit code, so it stays true under
    --exit-zero and at any --fail-on level. The causes of a failure are
    listed from most to least severe. An error is always a cause, also when
    *fail_on* does not name it, so a report with an error never passes. A
    check skipped because the project gave it nothing to look at does not
    keep the report from passing.
    """
    counts = report.count_by_status()
    failing = [
        status
        for status in map(CheckStatus, _SEVERITY)
        if counts[status] and (status.value in fail_on or status is CheckStatus.ERROR)
    ]
    if failing:
        causes = ", ".join(_count(counts[s], s) for s in failing)
        return f"Failed: {causes}.", "bold red"

    # A check that had nothing to look at is no caveat. One that could not
    # look (a missing tool, a missing prerequisite, no network, a failed
    # import, a file that could not be parsed) is. When no check looked at
    # anything, every skipped check counts.
    looked = counts[CheckStatus.OK] + counts[CheckStatus.NOTE] > 0
    skipped = sum(
        1
        for result in report.results
        if result.status is CheckStatus.SKIPPED
        and not (looked and result.skip_reason in _BENIGN_SKIP_REASONS)
    )
    notable = {CheckStatus.WARNING: counts[CheckStatus.WARNING]}
    notable[CheckStatus.SKIPPED] = skipped
    found = ", ".join(_count(n, status) for status, n in notable.items() if n)
    if not found:
        return "All checks passed!", "bold green"
    return f"No errors ({found}).", "bold yellow"


def _output_rich(
    report: Report,
    verbose: bool,
    con: Console | None = None,
    fail_on: list[str] | None = None,
) -> None:
    """Output report with rich formatting.

    Text that comes from checks (names, messages, details) is printed as
    plain text, so brackets in it are never read as Rich markup.
    """
    con = con or console
    fail_on = ["error"] if fail_on is None else fail_on
    if not report.results:
        con.print("[yellow]No checks were run.[/yellow]")
        return

    # Create results table
    table = Table(show_header=True, header_style="bold")
    table.add_column("", width=3, justify="center")
    table.add_column("Check", style="cyan", no_wrap=True)
    table.add_column("Message", overflow="fold")
    if verbose:
        table.add_column("Time", justify="right", no_wrap=True)

    for result in report.results:
        status_text = Text(_status_symbol(result.status, con.encoding))
        status_text.stylize(result.status.color)
        row = [status_text, Text(result.name), Text(result.message)]
        if verbose:
            row.append(Text(f"{result.duration:.2f}s"))
        table.add_row(*row)

    con.print(table)

    # Show details if verbose
    if verbose:
        for result in report.results:
            hints = _hints(result)
            if result.details or hints:
                con.print()
                con.print(Text.assemble((result.name, "bold"), " details:"))
                for detail in result.details:
                    _print_indented(con, detail)
                for hint in hints:
                    _print_indented(con, hint, _HINT_LABEL)

    # Summary
    con.print()
    counts = report.count_by_status()
    summary_parts: list[str] = []

    for status in CheckStatus:
        count = counts[status]
        if count > 0:
            colored = f"[{status.color}]{count} {status.value}[/{status.color}]"
            summary_parts.append(colored)

    con.print(" | ".join(summary_parts))

    line, style = _closing_line(report, fail_on)
    con.print()
    con.print(Text(line, style=style))
    if not verbose and any(_hints(result) for result in report.results):
        con.print("Run again with --verbose to see a suggestion for each finding.")


if __name__ == "__main__":
    main()
