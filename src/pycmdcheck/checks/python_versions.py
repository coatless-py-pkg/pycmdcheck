"""Python version support check.

Verifies that a package's ``requires-python`` specifier excludes
end-of-life Python versions.
"""

import configparser
import datetime
import re
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import PYTHON_EOL_VERSIONS
from pycmdcheck.pyproject_reader import get_effective_project_table, read_pyproject
from pycmdcheck.results import CheckResult, CheckStatus


class PythonVersionsCheck(BaseCheck):
    """Check that requires-python excludes end-of-life Python versions.

    Parses the ``requires-python`` field from pyproject.toml and checks
    whether it allows any Python versions that have reached end-of-life.

    Attributes:
        name: The check identifier ("python_versions").
        description: Human-readable description of this check.
    """

    name = "python_versions"
    description = "Check Python version support (EOL versions)"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check requires-python against EOL versions.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.

        Returns:
            A CheckResult with:

            - OK status if requires-python excludes all EOL versions
            - NOTE status if requires-python allows EOL versions
            - WARNING status if no requires-python specified
            - NOTE status if no pyproject.toml found
        """
        details: list[str] = []

        # Resolve a Python constraint from any declared source: PEP 621
        # [project], legacy Poetry [tool.poetry.dependencies].python, or
        # setup.cfg [options] python_requires.
        requires_python = self._get_requires_python(package_path)

        if not requires_python:
            no_pyproject = read_pyproject(package_path) is None
            no_setup_cfg = not (package_path / "setup.cfg").exists()
            if no_pyproject and no_setup_cfg:
                return CheckResult(
                    name=self.name,
                    status=CheckStatus.NOTE,
                    message="No pyproject.toml found",
                    details=["Cannot check requires-python without pyproject.toml"],
                )
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message="No requires-python specified",
                details=[
                    "Add requires-python to [project] in pyproject.toml",
                    'Example: requires-python = ">=3.10"',
                ],
            )

        details.append(f"requires-python: {requires_python}")

        # Determine which EOL versions are currently past EOL
        now = datetime.datetime.now(tz=datetime.timezone.utc)
        current_date = (now.year, now.month)

        eol_allowed: list[str] = []
        for version, eol_date in PYTHON_EOL_VERSIONS.items():
            if eol_date <= current_date:
                # This version is past EOL — check if requires-python allows it
                if self._specifier_allows(requires_python, version):
                    eol_allowed.append(version)
                    details.append(f"Python {version} is EOL but allowed")

        if eol_allowed:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message=(
                    f"requires-python allows EOL version(s): {', '.join(eol_allowed)}"
                ),
                details=details,
            )

        details.append("All allowed Python versions are supported")
        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message="Python version requirement is current",
            details=details,
        )

    def _get_requires_python(self, package_path: Path) -> str | None:
        """Return a declared Python constraint from any supported source."""
        project = get_effective_project_table(package_path)
        requires_python = project.get("requires-python")
        if isinstance(requires_python, str) and requires_python.strip():
            return requires_python
        return self._setup_cfg_python_requires(package_path)

    @staticmethod
    def _setup_cfg_python_requires(package_path: Path) -> str | None:
        """Read ``python_requires`` from ``setup.cfg`` ``[options]`` if present."""
        setup_cfg = package_path / "setup.cfg"
        if not setup_cfg.exists():
            return None
        parser = configparser.ConfigParser()
        try:
            parser.read(setup_cfg, encoding="utf-8")
        except (OSError, configparser.Error):
            return None
        if parser.has_option("options", "python_requires"):
            value = parser.get("options", "python_requires").strip()
            return value or None
        return None

    def _specifier_allows(self, specifier: str, version: str) -> bool:
        """Check if a PEP 440 specifier allows the given version.

        Prefers the accurate ``packaging`` library (a declared dependency).
        Falls back to a conservative hand parser only if ``packaging`` is
        somehow unavailable.
        """
        try:
            from packaging.specifiers import InvalidSpecifier, SpecifierSet

            try:
                return version in SpecifierSet(specifier, prereleases=True)
            except InvalidSpecifier:
                return False
        except ImportError:
            return self._fallback_allows(specifier, version)

    def _fallback_allows(self, specifier: str, version: str) -> bool:
        """Conservative fallback used only when ``packaging`` is missing.

        Handles comma-separated ``>=``, ``>``, ``<=``, ``<``, ``==`` (incl.
        ``==X.*``), ``!=`` and ``~=`` clauses. Returns ``False`` whenever the
        specifier cannot be confidently parsed, so an EOL version is never
        falsely reported as allowed.
        """
        target = self._version_tuple(version)
        clauses = [c.strip() for c in specifier.split(",") if c.strip()]
        if not clauses:
            return False
        for clause in clauses:
            match = re.match(r"(>=|<=|==|~=|!=|>|<)\s*(\d+(?:\.\d+)*)(\.\*)?$", clause)
            if not match:
                return False
            op, num, wildcard = match.group(1), match.group(2), match.group(3)
            bound = self._version_tuple(num)
            if op == ">=" and not target >= bound:
                return False
            if op == ">" and not target > bound:
                return False
            if op == "<=" and not target <= bound:
                return False
            if op == "<" and not target < bound:
                return False
            if op == "~=" and not target >= bound:
                return False
            if op == "==":
                if wildcard:
                    if target[: len(bound)] != bound:
                        return False
                elif target != bound:
                    return False
            if op == "!=" and target == bound:
                return False
        return True

    def _version_tuple(self, version: str) -> tuple[int, ...]:
        """Convert a version string to a comparable tuple."""
        return tuple(int(x) for x in version.split("."))
