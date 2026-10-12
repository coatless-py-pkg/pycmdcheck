"""Python version support check.

Verifies that a package's ``requires-python`` specifier excludes
end-of-life Python versions.
"""

import datetime
from pathlib import Path
from typing import Any

from packaging.specifiers import SpecifierSet

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.checks.metadata import legacy_metadata_files
from pycmdcheck.constants import EOL_GRACE_DAYS, PYTHON_EOL_DATES
from pycmdcheck.fileio import plain_text
from pycmdcheck.pyproject_reader import (
    SETUP_PY_DYNAMIC,
    get_effective_project_table,
    get_project_table,
    get_setup_cfg_value,
    get_setup_py_keywords,
    get_str,
    get_table,
    is_dynamic,
    parse_specifier_set,
    parse_version,
    project_metadata_source,
    pyproject_status,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe


def _today() -> datetime.date:
    """Return the current date in UTC. Tests replace this to freeze the date."""
    return datetime.datetime.now(tz=datetime.UTC).date()


def _eol_and_supported() -> tuple[list[str], list[str]]:
    """Return the Python versions to report as end-of-life, and the supported ones.

    Both lists are oldest first. Every version older than the first entry of
    ``PYTHON_EOL_DATES`` is end-of-life as well. The grace period only delays
    the report: a version inside it is in neither list, so it is never
    offered as supported.
    """
    today = _today()
    cutoff = today - datetime.timedelta(days=EOL_GRACE_DAYS)
    first_minor = int(next(iter(PYTHON_EOL_DATES)).split(".")[1])
    eol = ["2.7", *(f"3.{minor}" for minor in range(first_minor))]
    eol += [v for v, eol_date in PYTHON_EOL_DATES.items() if eol_date <= cutoff]
    supported = [v for v, eol_date in PYTHON_EOL_DATES.items() if eol_date > today]
    return eol, supported


def _allows(specifier: SpecifierSet, minor: str) -> bool:
    """Return whether *specifier* admits a release of *minor*.

    Probes X.Y, X.Y.99 and each release of that minor the specifier names,
    so that ``==3.9.7`` is seen to allow 3.9.
    """
    named = [clause.version for clause in specifier]
    probes = [minor, f"{minor}.99"] + [
        version
        for version in named
        if version.startswith(f"{minor}.") and parse_version(version) is not None
    ]
    return any(specifier.contains(version, prereleases=True) for version in probes)


def _example(place: str) -> str:
    """Return the end of a hint, which shows a constraint to write in *place*.

    The constraint starts at the oldest Python that is still supported. It is
    in quotation marks unless it goes into setup.cfg, which takes none.
    """
    supported = _eol_and_supported()[1]
    if not supported:
        return ""
    quote = "" if "setup.cfg" in place else '"'
    return f", for example {quote}>={supported[0]}{quote}"


class PythonVersionsCheck(BaseCheck):
    """Check that requires-python excludes end-of-life Python versions.

    Reads the Python constraint from pyproject.toml (``[project]`` or
    Poetry), setup.cfg or setup.py and checks whether it allows any Python
    version that has reached end-of-life. A requires-python that the
    ``[project]`` table lacks or misstates is left to the metadata check.

    Attributes:
        name: The check identifier ("python_versions").
        description: Human-readable description of this check.
        rules: The codes of the findings, each with what the rule means.
        hints: The same codes, each with what to do about the finding.
    """

    name = "python_versions"
    description = "Check Python version support (EOL versions)"
    rules = {
        "python_versions.allows-eol": (
            "The Python constraint allows a version that has reached end of life"
        ),
        "python_versions.invalid-requires-python": (
            "The Python constraint in a Poetry table, setup.cfg or setup.py is "
            "not a valid version specifier"
        ),
        "python_versions.missing-requires-python": (
            "No Python constraint is declared in a legacy Poetry table, setup.cfg "
            "or setup.py"
        ),
    }
    hints = {
        "python_versions.allows-eol": (
            "Raise the lowest version that the Python constraint allows to one "
            "that is still supported."
        ),
        "python_versions.invalid-requires-python": (
            "Write the Python constraint as a version specifier such as >=3.12."
        ),
        "python_versions.missing-requires-python": (
            "Add python to [tool.poetry.dependencies] in pyproject.toml, or "
            "python_requires to setup.py or [options] in setup.cfg."
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check requires-python against EOL versions.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if requires-python excludes all EOL versions
            - NOTE status if requires-python allows EOL versions, or if a
              constraint kept in a legacy Poetry table, setup.cfg or setup.py
              cannot be interpreted or is not specified
            - SKIPPED status if there is no value to check: requires-python is
              dynamic or computed in setup.py ("not-applicable"), or the
              metadata check reports why ("prerequisite-failed"). That is a
              pyproject.toml that is missing or cannot be read or has no
              ``[project]`` table, and a ``[project]`` table whose
              requires-python is missing or not a valid specifier.
        """
        # Resolve a Python constraint from any declared source: PEP 621
        # [project], Poetry [tool.poetry.dependencies].python, setup.cfg
        # [options] python_requires, or a literal python_requires in setup.py.
        requires_python, label = self._get_requires_python(package_path)
        if requires_python is None:
            return self._not_declared(package_path)

        shown = plain_text(requires_python)
        details = [f"{label}: {shown}"]
        specifier = parse_specifier_set(requires_python)
        if specifier is None:
            details.append(
                "Not a PEP 440 version specifier, so end-of-life versions "
                "were not checked"
            )
            message = "Could not interpret requires-python"
            if label == "requires-python":
                # The metadata check judges the values written in [project].
                details.append("The metadata check reports the invalid requires-python")
                return CheckResult.skip(
                    self.name, message, "prerequisite-failed", details
                )
            return self._note(
                "python_versions.invalid-requires-python",
                message,
                details,
                found=f"{label} is not a PEP 440 version specifier: {shown}",
                hint=f"Write {label} as a version specifier{_example(label)}.",
            )

        eol, supported = _eol_and_supported()
        eol_allowed = [version for version in eol if _allows(specifier, version)]
        if not eol_allowed:
            today = _today()
            grace = datetime.timedelta(days=EOL_GRACE_DAYS)
            recent = [
                (version, eol_date)
                for version, eol_date in PYTHON_EOL_DATES.items()
                if today - grace < eol_date <= today and _allows(specifier, version)
            ]
            for version, eol_date in recent:
                details.append(
                    f"Python {version} reached end-of-life on {eol_date.isoformat()} "
                    f"and will be reported here from {(eol_date + grace).isoformat()}"
                )
            if not recent:
                details.append("All allowed Python versions are supported")
            return CheckResult(
                name=self.name,
                status=CheckStatus.OK,
                message="Python version requirement is current",
                details=details,
            )

        # Summarise: ">=2.7" allows a dozen end-of-life versions.
        newest = eol_allowed[-1]
        summary = ", ".join(eol_allowed)
        if len(eol_allowed) > 3:
            summary = f"{eol_allowed[0]} to {newest} ({len(eol_allowed)} versions)"
        if newest in PYTHON_EOL_DATES:
            details.append(
                f"Python {newest} reached end of life on {PYTHON_EOL_DATES[newest]}"
            )
        # The hint names the oldest Python that is still supported.
        hint = None
        if supported:
            hint = f"Raise the lowest version that {label} allows to {supported[0]}."
        return self._note(
            "python_versions.allows-eol",
            f"requires-python allows EOL version(s): {summary}",
            details,
            hint=hint,
        )

    def _note(
        self,
        code: str,
        message: str,
        details: list[str],
        found: str = "",
        hint: str | None = None,
    ) -> CheckResult:
        """Build a NOTE with one finding, which says *found* or else *message*.

        A *hint* given here is used in place of the one in ``hints``.
        """
        findings = [Finding(code, found or message, CheckStatus.NOTE, hint)]
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=message,
            details=details,
            findings=findings,
        )

    @staticmethod
    def _get_requires_python(package_path: Path) -> tuple[str | None, str]:
        """Return a declared Python constraint and the name of its source."""
        project = get_effective_project_table(package_path)
        value = get_str(project, "requires-python", "").strip()
        if value:
            if "requires-python" in get_project_table(package_path):
                return value, "requires-python"
            # Poetry keeps the constraint among its dependencies.
            return value, "python in [tool.poetry.dependencies]"
        from_cfg = get_setup_cfg_value(package_path, "options", "python_requires")
        if from_cfg:
            return from_cfg, "python_requires in setup.cfg"
        keyword = get_setup_py_keywords(package_path).get("python_requires")
        if isinstance(keyword, str) and keyword.strip():
            return keyword.strip(), "python_requires in setup.py"
        return None, ""

    def _not_declared(self, package_path: Path) -> CheckResult:
        """Grade a project in which no Python constraint could be read."""
        pyproject = pyproject_status(package_path)
        source = project_metadata_source(package_path)
        legacy = legacy_metadata_files(package_path)
        # setup.py may set the value in a way that is not read without running it.
        keywords = get_setup_py_keywords(package_path)
        computed = keywords.get("python_requires") is SETUP_PY_DYNAMIC or (
            "python_requires" not in keywords and "**" in keywords
        )

        if is_dynamic(get_effective_project_table(package_path), "requires-python"):
            return CheckResult.skip(
                self.name,
                "requires-python is dynamic (set at build time)",
                "not-applicable",
                ["requires-python is listed in dynamic, so it was not checked"],
            )
        if pyproject == "unparseable":
            # The metadata check is the one that reports the broken file.
            return CheckResult.skip(
                self.name,
                "pyproject.toml could not be read",
                "prerequisite-failed",
                ["The metadata check reports the reason"],
            )
        if pyproject == "missing" and not legacy:
            return CheckResult.skip(
                self.name,
                "No pyproject.toml found",
                "prerequisite-failed",
                [
                    "Cannot check requires-python without pyproject.toml",
                    "The metadata check reports the missing file",
                ],
            )
        if source == "none" and computed:
            return CheckResult.skip(
                self.name,
                "Could not read python_requires from setup.py",
                "not-applicable",
                ["The value is computed when setup.py runs"],
            )

        message = "No requires-python specified"
        if source == "project":
            # The metadata check judges what the [project] table declares.
            return CheckResult.skip(
                self.name,
                message,
                "prerequisite-failed",
                ["The metadata check reports a missing or invalid requires-python"],
            )
        if source == "none" and not legacy:
            return CheckResult.skip(
                self.name,
                "No [project] table",
                "prerequisite-failed",
                [
                    "Cannot check requires-python without a [project] table",
                    "The metadata check reports the missing [project] table",
                ],
            )

        # What is left keeps its metadata in a legacy Poetry table, or in
        # setup.py and setup.cfg, so the finding and its hint name that place.
        key, place = "python", "[tool.poetry.dependencies] in pyproject.toml"
        if source == "none":
            key = "python_requires"
            place = " or ".join(legacy).replace("setup.cfg", "[options] in setup.cfg")
        found, verb = f"No {key} in {place}", f"Add {key} to"
        if key in get_table(package_path, "tool", "poetry", "dependencies"):
            # A constraint such as "*" is there, and allows every version.
            found, verb = f"{key} in {place} sets no lowest version", f"Change {key} in"
        return self._note(
            "python_versions.missing-requires-python",
            message,
            [],
            found=found,
            hint=f"{verb} {place}{_example(place)}.",
        )
