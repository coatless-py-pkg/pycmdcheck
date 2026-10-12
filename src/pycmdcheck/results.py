"""Result types for pycmdcheck."""

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(Enum):
    """Status of a check result.

    Each check returns one of these statuses to indicate
    whether the check passed, failed, or encountered issues.

    Attributes:
        OK: Check passed successfully.
        NOTE: Something the author of the checked package can improve.
        WARNING: Potential issue that should be reviewed.
        ERROR: Check failed.
        SKIPPED: The check did not look at anything, for example because the
            tool it needs is not installed. The result's skip_reason says why.

    Examples:
        >>> status = CheckStatus.OK
        >>> status.symbol
        '✓'
        >>> status.color
        'green'
        >>> str(status)
        'ok'
    """

    OK = "ok"
    NOTE = "note"
    WARNING = "warning"
    ERROR = "error"
    SKIPPED = "skipped"

    def __str__(self) -> str:
        """Return the string value of the status.

        Returns:
            The status value as a lowercase string.
        """
        return self.value

    @property
    def symbol(self) -> str:
        """Return a visual symbol for the status.

        Returns:
            A unicode symbol representing the status:
            ✓ for OK, ℹ for NOTE, ⚠ for WARNING, ✗ for ERROR, ○ for SKIPPED.

        Examples:
            >>> CheckStatus.OK.symbol
            '✓'
            >>> CheckStatus.ERROR.symbol
            '✗'
        """
        symbols = {
            CheckStatus.OK: "✓",
            CheckStatus.NOTE: "ℹ",
            CheckStatus.WARNING: "⚠",
            CheckStatus.ERROR: "✗",
            CheckStatus.SKIPPED: "○",
        }
        return symbols[self]

    @property
    def color(self) -> str:
        """Return a Rich library color name for the status.

        Returns:
            A color name compatible with the Rich library:
            green, blue, yellow, red, or dim.

        Examples:
            >>> CheckStatus.OK.color
            'green'
            >>> CheckStatus.ERROR.color
            'red'
        """
        colors = {
            CheckStatus.OK: "green",
            CheckStatus.NOTE: "blue",
            CheckStatus.WARNING: "yellow",
            CheckStatus.ERROR: "red",
            CheckStatus.SKIPPED: "dim",
        }
        return colors[self]


# Why a check was skipped. A SKIPPED result carries one of these as its
# skip_reason. The set is closed, so a program that reads the JSON report can
# rely on it.
SKIP_REASONS: frozenset[str] = frozenset(
    {
        # The tool the check needs is not installed.
        "tool-missing",
        # The project has not adopted the tool (linting, typing, formatting).
        "not-configured",
        # There is nothing for this check to look at in this project.
        "not-applicable",
        # The check cannot work because something another check reports is
        # missing or unreadable, such as pyproject.toml.
        "prerequisite-failed",
        # The check needs the network and could reach no host at all.
        "network-unavailable",
        # The check could not import the code it needed to look at.
        "import-failed",
        # The check could not parse the code it needed to look at.
        "parse-failed",
        # The runner stopped before this check.
        "fail-fast",
        # The check has been merged into another.
        "deprecated",
    }
)

# The statuses a finding can carry, from most to least severe.
_SEVERITY: tuple[CheckStatus, ...] = (
    CheckStatus.ERROR,
    CheckStatus.WARNING,
    CheckStatus.NOTE,
)


@dataclass(frozen=True)
class Finding:
    """One thing a check found, for programs that read the report.

    Attributes:
        code: Stable identifier of the rule, "<check name>.<rule>" in lower
            case with hyphens inside the rule part, such as "changelog.missing".
        message: One line of plain text that says what was found.
        status: How severe the finding is: NOTE, WARNING or ERROR.
        hint: One sentence that says what to do about the finding. A check
            sets it when the advice depends on what was found. When it is
            None, the runner fills it from the `hints` of the check.

    Examples:
        >>> finding = Finding("changelog.missing", "No changelog found")
        >>> finding.status
        <CheckStatus.NOTE: 'note'>
        >>> finding.to_dict()  # doctest: +NORMALIZE_WHITESPACE
        {'code': 'changelog.missing', 'status': 'note',
         'message': 'No changelog found', 'hint': None}
    """

    code: str
    message: str
    status: CheckStatus = CheckStatus.NOTE
    hint: str | None = None

    def to_dict(self) -> dict[str, str | None]:
        """Convert to dictionary for JSON serialization.

        Returns:
            A dictionary with keys: code, status, message, hint.
        """
        return {
            "code": self.code,
            "status": self.status.value,
            "message": self.message,
            "hint": self.hint,
        }


def most_severe(findings: Iterable[Finding]) -> CheckStatus:
    """Return the most severe status among findings.

    An error outranks a warning, which outranks a note. This is the status
    of a result that carries these findings.

    Args:
        findings: The findings of one check.

    Returns:
        ERROR, WARNING or NOTE, or OK when there are no findings.

    Examples:
        >>> most_severe(
        ...     [
        ...         Finding("changelog.missing", "No changelog found"),
        ...         Finding(
        ...             "metadata.missing-required",
        ...             "Missing required fields: version",
        ...             CheckStatus.ERROR,
        ...         ),
        ...     ]
        ... )
        <CheckStatus.ERROR: 'error'>
        >>> most_severe([])
        <CheckStatus.OK: 'ok'>
    """
    found = {finding.status for finding in findings}
    return next((status for status in _SEVERITY if status in found), CheckStatus.OK)


@dataclass
class CheckResult:
    """Result of a single check.

    Contains the outcome of running a check, including the status,
    a human-readable message, and optional details. People read the message
    and the details. Programs read the findings and the skip reason.

    Attributes:
        name: Name of the check that produced this result (e.g., "metadata").
        status: The status of the check (OK, WARNING, ERROR, etc.).
        message: Human-readable message describing the result.
        details: Optional list of detailed messages providing more context.
        duration: Time taken to run the check in seconds.
        findings: What the check found, one Finding for each. Empty when the
            status is OK or SKIPPED.
        skip_reason: One of SKIP_REASONS when the status is SKIPPED, and None
            otherwise.

    Examples:
        Create a successful check result:

        >>> result = CheckResult(
        ...     name="metadata",
        ...     status=CheckStatus.OK,
        ...     message="Package metadata is valid",
        ... )
        >>> result.status
        <CheckStatus.OK: 'ok'>

        Create a result with details:

        >>> result = CheckResult(
        ...     name="linting",
        ...     status=CheckStatus.WARNING,
        ...     message="Found 3 linting issues",
        ...     details=["Line 10: E501 line too long", "Line 20: W503 line break"],
        ... )
        >>> print(result)
        ⚠ linting: Found 3 linting issues
    """

    name: str
    status: CheckStatus
    message: str
    details: list[str] = field(default_factory=list)
    duration: float = 0.0
    findings: list[Finding] = field(default_factory=list)
    skip_reason: str | None = None

    @classmethod
    def skip(
        cls,
        name: str,
        message: str,
        reason: str,
        details: list[str] | tuple[str, ...] = (),
    ) -> "CheckResult":
        """Build the result of a check that did not look at anything.

        Args:
            name: Name of the check.
            message: Human-readable message that says why it was skipped.
            reason: One of SKIP_REASONS.
            details: Optional detail lines, as a list or a tuple. A bare
                string is not accepted by the type, since it would become
                one detail for each character.

        Returns:
            A SKIPPED result with that reason and no findings.

        Examples:
            >>> result = CheckResult.skip(
            ...     "typing", "No type checker configured", "not-configured"
            ... )
            >>> result.status, result.skip_reason
            (<CheckStatus.SKIPPED: 'skipped'>, 'not-configured')
        """
        return cls(
            name=name,
            status=CheckStatus.SKIPPED,
            message=message,
            details=list(details),
            skip_reason=reason,
        )

    def __str__(self) -> str:
        """Return a formatted string representation.

        Returns:
            A string in the format "symbol name: message".

        Examples:
            >>> result = CheckResult("test", CheckStatus.OK, "All tests passed")
            >>> str(result)
            '✓ test: All tests passed'
        """
        return f"{self.status.symbol} {self.name}: {self.message}"

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Returns:
            A dictionary with keys: name, status, message, details, duration,
            findings, skip_reason. The status is converted to its string
            value and each finding to a dictionary.

        Examples:
            >>> result = CheckResult("test", CheckStatus.OK, "Passed")
            >>> result.to_dict()  # doctest: +NORMALIZE_WHITESPACE
            {'name': 'test', 'status': 'ok', 'message': 'Passed',
             'details': [], 'duration': 0.0, 'findings': [],
             'skip_reason': None}
        """
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "duration": self.duration,
            "findings": [finding.to_dict() for finding in self.findings],
            "skip_reason": self.skip_reason,
        }


@dataclass
class Report:
    """Collection of check results for a package.

    A Report aggregates multiple CheckResult objects and provides
    methods for querying the overall status and summarizing results.

    Attributes:
        results: List of CheckResult objects from running checks.
        package_path: Path to the package that was checked.

    Examples:
        Create a report and add results:

        >>> report = Report(package_path="/path/to/package")
        >>> report.add(CheckResult("metadata", CheckStatus.OK, "Valid"))
        >>> report.add(CheckResult("tests", CheckStatus.OK, "Passed"))
        >>> report.passed
        True

        Check for specific failure conditions:

        >>> report.failed_on(["error"])
        False
        >>> report.failed_on(["error", "warning"])
        False
    """

    results: list[CheckResult] = field(default_factory=list)
    package_path: str = ""

    def add(self, result: CheckResult) -> None:
        """Add a check result to the report.

        Args:
            result: The CheckResult to add.

        Examples:
            >>> report = Report()
            >>> report.add(CheckResult("test", CheckStatus.OK, "Passed"))
            >>> len(report.results)
            1
        """
        self.results.append(result)

    @property
    def passed(self) -> bool:
        """Check if all checks passed (no errors).

        Returns:
            True if no check has ERROR status, False otherwise.

        Examples:
            >>> report = Report()
            >>> report.add(CheckResult("test", CheckStatus.OK, "Passed"))
            >>> report.passed
            True
            >>> report.add(CheckResult("lint", CheckStatus.ERROR, "Failed"))
            >>> report.passed
            False
        """
        return not any(r.status == CheckStatus.ERROR for r in self.results)

    @property
    def has_warnings(self) -> bool:
        """Check if any check has warnings.

        Returns:
            True if any check has WARNING status, False otherwise.

        Examples:
            >>> report = Report()
            >>> report.add(CheckResult("test", CheckStatus.OK, "Passed"))
            >>> report.has_warnings
            False
            >>> report.add(CheckResult("lint", CheckStatus.WARNING, "Issues"))
            >>> report.has_warnings
            True
        """
        return any(r.status == CheckStatus.WARNING for r in self.results)

    def count_by_status(self) -> dict[CheckStatus, int]:
        """Count results by status.

        Returns:
            A dictionary mapping each CheckStatus to the count of results
            with that status.

        Examples:
            >>> report = Report()
            >>> report.add(CheckResult("a", CheckStatus.OK, "OK"))
            >>> report.add(CheckResult("b", CheckStatus.OK, "OK"))
            >>> report.add(CheckResult("c", CheckStatus.WARNING, "Warn"))
            >>> counts = report.count_by_status()
            >>> counts[CheckStatus.OK]
            2
            >>> counts[CheckStatus.WARNING]
            1
        """
        counts: dict[CheckStatus, int] = dict.fromkeys(CheckStatus, 0)
        for result in self.results:
            counts[result.status] += 1
        return counts

    def failed_on(self, fail_on: list[str]) -> bool:
        """Check if any result matches the fail_on criteria.

        Args:
            fail_on: List of status values to consider as failures
                (e.g., ["error", "warning"]).

        Returns:
            True if any result has a status in the fail_on list.

        Raises:
            ValueError: If fail_on holds a value that is not a status.

        Examples:
            >>> report = Report()
            >>> report.add(CheckResult("test", CheckStatus.WARNING, "Warn"))
            >>> report.failed_on(["error"])
            False
            >>> report.failed_on(["error", "warning"])
            True
        """
        fail_statuses = {CheckStatus(s) for s in fail_on}
        return any(r.status in fail_statuses for r in self.results)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization.

        Returns:
            A dictionary containing:
            - package_path: The path that was checked
            - passed: Whether all checks passed
            - summary: Count of each status
            - results: List of result dictionaries

        Examples:
            >>> report = Report(package_path="/pkg")
            >>> report.add(CheckResult("test", CheckStatus.OK, "OK"))
            >>> data = report.to_dict()
            >>> data["passed"]
            True
            >>> data["summary"]["ok"]
            1
        """
        counts = self.count_by_status()
        return {
            "package_path": self.package_path,
            "passed": self.passed,
            "summary": {status.value: count for status, count in counts.items()},
            "results": [r.to_dict() for r in self.results],
        }
