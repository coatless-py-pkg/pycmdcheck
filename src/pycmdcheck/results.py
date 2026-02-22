"""Result types for pycmdcheck."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CheckStatus(Enum):
    """Status of a check result.

    Each check returns one of these statuses to indicate
    whether the check passed, failed, or encountered issues.

    Attributes:
        OK: Check passed successfully.
        NOTE: Informational message, not a problem.
        WARNING: Potential issue that should be reviewed.
        ERROR: Check failed.
        SKIPPED: Check was skipped (e.g., required tool not installed).

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


@dataclass
class CheckResult:
    """Result of a single check.

    Contains the outcome of running a check, including the status,
    a human-readable message, and optional details.

    Attributes:
        name: Name of the check that produced this result (e.g., "metadata").
        status: The status of the check (OK, WARNING, ERROR, etc.).
        message: Human-readable message describing the result.
        details: Optional list of detailed messages providing more context.
        duration: Time taken to run the check in seconds.

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
            A dictionary with keys: name, status, message, details, duration.
            The status is converted to its string value.

        Examples:
            >>> result = CheckResult("test", CheckStatus.OK, "Passed")
            >>> result.to_dict()  # doctest: +NORMALIZE_WHITESPACE
            {'name': 'test', 'status': 'ok', 'message': 'Passed',
             'details': [], 'duration': 0.0}
        """
        return {
            "name": self.name,
            "status": self.status.value,
            "message": self.message,
            "details": self.details,
            "duration": self.duration,
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
