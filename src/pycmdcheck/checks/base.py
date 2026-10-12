"""Base check protocol and abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Protocol, runtime_checkable

from pycmdcheck.results import CheckResult


@runtime_checkable
class Check(Protocol):
    """Protocol defining the interface for all checks.

    This protocol defines what a check must implement to be usable
    by pycmdcheck. Checks can be implemented by either:

    1. Inheriting from BaseCheck (recommended for most cases)
    2. Implementing this Protocol directly (for structural subtyping)

    Two more attributes are optional, and BaseCheck documents both. `rules`
    maps every code the check can emit to a one-line description. `hints`
    maps the same codes to one sentence that says what to do about a finding.

    Attributes:
        name: Unique identifier for the check (e.g., "metadata", "tests").
        description: Human-readable description of what the check does.

    Examples:
        Implement a check using the protocol directly:

        >>> class MyCheck:
        ...     name = "my_check"
        ...     description = "My custom check"
        ...
        ...     def run(self, package_path, config):
        ...         return CheckResult(
        ...             name=self.name,
        ...             status=CheckStatus.OK,
        ...             message="Check passed",
        ...         )
        >>> isinstance(MyCheck(), Check)
        True
    """

    name: str
    description: str

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Execute the check and return result.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check, loaded from
                pyproject.toml [tool.pycmdcheck.checks.<name>] section.

        Returns:
            A CheckResult containing the status, message, and optional details.
        """
        ...


class BaseCheck(ABC):
    """Abstract base class for implementing checks.

    This is the recommended way to implement custom checks. Subclasses
    must override the `name`, `description` attributes and implement
    the `run` method.

    A result says what the check found twice: in its message and details,
    which people read, and in its findings, which programs read. A Finding
    has a code, one line of text and a status. A code is
    "<check name>.<rule>" in lower case with hyphens inside the rule part,
    such as "copyright.missing-header". It is a stable public identifier, so
    name it for what the rule means, and list it in `rules`.

    A hint is one sentence that says what to do about a finding, such as
    "Add a copyright line to the top of the file.". Give one for each code
    in `hints`, and pycmdcheck puts it on every finding with that code that
    has none of its own. Pass `hint=` to a Finding when the advice depends
    on what was found. A check with no hints still works, and its findings
    carry None.

    Every built-in check keeps to this contract:

    - OK: no findings and no skip reason.
    - NOTE, WARNING or ERROR: at least one finding, every code is a key of
      `rules`, the status is `most_severe(findings)`, and no skip reason.
    - SKIPPED: no findings, and a skip reason from `SKIP_REASONS`. Build it
      with `CheckResult.skip()`.

    SKIPPED is the status whenever a check did not look at anything: its
    tool is not installed ("tool-missing"), the project has not adopted the
    tool ("not-configured"), the project has nothing of the kind the check
    looks at ("not-applicable"), or something that another check reports is
    missing or unreadable, such as pyproject.toml ("prerequisite-failed").
    pycmdcheck.results.SKIP_REASONS holds the full set of reasons.
    A NOTE always means something the author of the checked package can
    improve. A real shortcoming keeps its status: a package with no tests
    is a WARNING from the test_suite check, not a row of skipped checks.

    A plugin should follow the contract but does not have to. A result with
    no findings is still accepted.

    Attributes:
        name: Unique identifier for the check. Override in subclass.
        description: Human-readable description. Override in subclass.
        rules: Every code the check can emit, mapped to a one-line
            description of the rule. `pycmdcheck --list-rules` prints them.
        hints: The same codes, each mapped to one sentence that says what to
            do about a finding. `pycmdcheck --verbose` prints it.

    Examples:
        Create a custom check:

        >>> from pycmdcheck.checks.base import BaseCheck
        >>> from pycmdcheck.results import (
        ...     CheckResult,
        ...     CheckStatus,
        ...     Finding,
        ...     most_severe,
        ... )
        >>>
        >>> class CopyrightCheck(BaseCheck):
        ...     name = "copyright"
        ...     description = "Check for copyright headers"
        ...     rules = {"copyright.missing-header": "A file has no copyright header"}
        ...     hints = {
        ...         "copyright.missing-header": "Add a line with Copyright to the file."
        ...     }
        ...
        ...     def run(self, package_path, config):
        ...         sources = sorted(package_path.rglob("*.py"))
        ...         if not sources:
        ...             return CheckResult.skip(
        ...                 self.name, "No Python files found", "not-applicable"
        ...             )
        ...         findings = [
        ...             Finding(
        ...                 "copyright.missing-header",
        ...                 f"{path.name} has no copyright header",
        ...                 CheckStatus.WARNING,
        ...             )
        ...             for path in sources
        ...             if "Copyright" not in path.read_text(encoding="utf-8")
        ...         ]
        ...         return CheckResult(
        ...             name=self.name,
        ...             status=most_severe(findings),
        ...             message=f"{len(findings)} file(s) without a copyright header",
        ...             details=[finding.message for finding in findings],
        ...             findings=findings,
        ...         )

        Register via entry points in pyproject.toml:

        ```toml
        [project.entry-points."pycmdcheck.checks"]
        copyright = "my_package.checks:CopyrightCheck"
        ```
    """

    name: str = "base"
    description: str = "Base check"
    rules: ClassVar[dict[str, str]] = {}
    hints: ClassVar[dict[str, str]] = {}

    @abstractmethod
    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Execute the check and return result.

        Subclasses must implement this method to perform the actual check.

        Args:
            package_path: Path to the package directory to check. This is
                always an absolute, resolved path.
            config: Configuration dictionary for this check. Contains any
                options specified in pyproject.toml under
                [tool.pycmdcheck.checks.<name>]. Common keys include
                "enabled" (bool) and check-specific options.

        Returns:
            A CheckResult with:
            - name: Should match self.name
            - status: One of CheckStatus values (OK, WARNING, ERROR, etc.)
            - message: Brief description of the result
            - details: List of short lines that say what the check looked
              for and where, and what it found. What would fix it belongs
              in a hint and not here.
            - findings: One Finding for each thing the check found. See the
              contract in the class docstring.

        Examples:
            >>> def run(self, package_path, config):
            ...     findings = self._find_issues(package_path)
            ...     if findings:
            ...         return CheckResult(
            ...             name=self.name,
            ...             status=most_severe(findings),
            ...             message=f"Found {len(findings)} issues",
            ...             details=[finding.message for finding in findings],
            ...             findings=findings,
            ...         )
            ...     return CheckResult(
            ...         name=self.name,
            ...         status=CheckStatus.OK,
            ...         message="No issues found",
            ...     )
        """
        ...

    def __repr__(self) -> str:
        """Return string representation of the check.

        Returns:
            A string in the format "<ClassName: check_name>".
        """
        return f"<{self.__class__.__name__}: {self.name}>"
