"""Command-line interface for pycmdcheck."""

import json
import logging
import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich.text import Text

from pycmdcheck.config import load_config
from pycmdcheck.discovery import list_available_checks
from pycmdcheck.profiles import get_profile, list_profiles
from pycmdcheck.results import CheckStatus, Report
from pycmdcheck.runner import run_checks

console = Console()


@click.command()
@click.argument("path", default=".", type=click.Path(exists=True))
@click.option(
    "-c",
    "--check",
    multiple=True,
    help="Run only specific check(s). Can be specified multiple times.",
)
@click.option(
    "-s",
    "--skip",
    multiple=True,
    help="Skip specific check(s). Can be specified multiple times.",
)
@click.option(
    "--fail-on",
    multiple=True,
    type=click.Choice(["error", "warning", "note"]),
    default=["error"],
    help="Exit with non-zero code on these statuses. Default: error",
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
    help="Show detailed output for each check.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Output results as JSON.",
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
    help="Stop after first check failure.",
)
@click.option(
    "--profile",
    type=click.Choice(["minimal", "default", "pyopensci", "strict"]),
    default=None,
    help="Use a predefined check profile.",
)
@click.option(
    "--list-profiles",
    "list_profiles_flag",
    is_flag=True,
    help="List available profiles and exit.",
)
@click.option("--debug", is_flag=True, help="Enable debug logging output.")
@click.version_option(package_name="pycmdcheck")
def main(
    path: str,
    check: tuple[str, ...],
    skip: tuple[str, ...],
    fail_on: tuple[str, ...],
    list_checks: bool,
    verbose: bool,
    json_output: bool,
    no_parallel: bool,
    fail_fast: bool,
    profile: str | None,
    list_profiles_flag: bool,
    debug: bool,
) -> None:
    """Check Python package quality.

    PATH is the directory to check (default: current directory).

    Examples:

        pycmdcheck                    # Check current directory

        pycmdcheck /path/to/package   # Check specific directory

        pycmdcheck -c tests -c linting  # Run only tests and linting

        pycmdcheck -s typing          # Skip type checking

        pycmdcheck --fail-on error --fail-on warning  # Fail on warnings too
    """
    if debug:
        logging.basicConfig(
            level=logging.DEBUG,
            format="%(name)s: %(message)s",
        )

    if list_profiles_flag:
        _show_profiles()
        return

    if list_checks:
        _show_available_checks()
        return

    # Resolve profile
    profile_config: dict[str, dict[str, object]] = {}
    if profile:
        prof = get_profile(profile)
        if prof:
            profile_config = prof.config_overrides
            if not check:
                # Use profile's check set (converted to tuple for consistency)
                check = tuple(sorted(prof.checks))

    # Validate check names
    if check:
        from pycmdcheck.discovery import discover_checks

        available = set(discover_checks().keys())
        invalid = set(check) - available
        if invalid:
            console.print(
                f"[bold red]Unknown check(s): {', '.join(sorted(invalid))}[/bold red]"
            )
            console.print(f"Available checks: {', '.join(sorted(available))}")
            sys.exit(2)

    package_path = Path(path).resolve()

    if not json_output:
        profile_label = f" (profile: {profile})" if profile else ""
        console.print(
            f"\n[bold]Checking package:[/bold] {package_path}{profile_label}\n"
        )

    # Load config from pyproject.toml and merge profile overrides
    merged_config = load_config(package_path)
    if profile_config:
        checks_section = merged_config.setdefault("checks", {})
        for check_name, overrides in profile_config.items():
            existing = checks_section.get(check_name, {})
            if isinstance(existing, bool):
                existing = {"enabled": existing}
            existing.update(overrides)
            checks_section[check_name] = existing

    # Run checks
    if not json_output and console.is_terminal:
        with console.status("[bold]Running checks...[/bold]"):
            report = run_checks(
                package_path=package_path,
                checks=list(check) if check else None,
                skip=list(skip),
                config=merged_config,
                parallel=not no_parallel,
                fail_fast=fail_fast,
            )
    else:
        report = run_checks(
            package_path=package_path,
            checks=list(check) if check else None,
            skip=list(skip),
            config=merged_config,
            parallel=not no_parallel,
            fail_fast=fail_fast,
        )

    # Output results
    if json_output:
        _output_json(report)
    else:
        _output_rich(report, verbose)

    # Determine exit code
    if report.failed_on(list(fail_on)):
        sys.exit(1)


def _show_profiles(con: Console | None = None) -> None:
    """Display list of available profiles."""
    con = con or console
    profiles = list_profiles()

    table = Table(title="Available Profiles")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for name, description in profiles:
        table.add_row(name, description)

    con.print(table)


def _show_available_checks(con: Console | None = None) -> None:
    """Display list of available checks."""
    con = con or console
    checks = list_available_checks()

    table = Table(title="Available Checks")
    table.add_column("Name", style="cyan")
    table.add_column("Description")

    for name, description in checks:
        table.add_row(name, description)

    con.print(table)


def _output_json(report: Report) -> None:
    """Output report as JSON."""
    print(json.dumps(report.to_dict(), indent=2))


def _output_rich(report: Report, verbose: bool, con: Console | None = None) -> None:
    """Output report with rich formatting."""
    con = con or console
    if not report.results:
        con.print("[yellow]No checks were run.[/yellow]")
        return

    # Create results table
    table = Table(show_header=True, header_style="bold")
    table.add_column("Status", width=3, justify="center")
    table.add_column("Check", style="cyan")
    table.add_column("Message")
    if verbose:
        table.add_column("Time", justify="right")

    for result in report.results:
        status_text = Text(result.status.symbol)
        status_text.stylize(result.status.color)

        if verbose:
            table.add_row(
                status_text, result.name, result.message, f"{result.duration:.2f}s"
            )
        else:
            table.add_row(status_text, result.name, result.message)

    con.print(table)

    # Show details if verbose
    if verbose:
        for result in report.results:
            if result.details:
                con.print(f"\n[bold]{result.name}[/bold] details:")
                for detail in result.details:
                    con.print(f"  {detail}")

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

    if report.passed:
        con.print("\n[bold green]All checks passed![/bold green]")
    else:
        con.print("\n[bold red]Some checks failed.[/bold red]")


if __name__ == "__main__":
    main()
