"""Base check protocol and abstract class."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pycmdcheck.results import CheckResult


@runtime_checkable
class Check(Protocol):
    """Protocol defining the interface for all checks.

    This protocol defines what a check must implement to be usable
    by pycmdcheck. Checks can be implemented by either:

    1. Inheriting from BaseCheck (recommended for most cases)
    2. Implementing this Protocol directly (for structural subtyping)

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

    Attributes:
        name: Unique identifier for the check. Override in subclass.
        description: Human-readable description. Override in subclass.

    Examples:
        Create a custom check:

        >>> from pycmdcheck.checks.base import BaseCheck
        >>> from pycmdcheck.results import CheckResult, CheckStatus
        >>>
        >>> class CopyrightCheck(BaseCheck):
        ...     name = "copyright"
        ...     description = "Check for copyright headers"
        ...
        ...     def run(self, package_path, config):
        ...         # Check logic here
        ...         return CheckResult(
        ...             name=self.name,
        ...             status=CheckStatus.OK,
        ...             message="All files have copyright headers",
        ...         )

        Register via entry points in pyproject.toml:

        ```toml
        [project.entry-points."pycmdcheck.checks"]
        copyright = "my_package.checks:CopyrightCheck"
        ```
    """

    name: str = "base"
    description: str = "Base check"

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
            - details: Optional list of specific issues found

        Examples:
            >>> def run(self, package_path, config):
            ...     issues = self._find_issues(package_path)
            ...     if issues:
            ...         return CheckResult(
            ...             name=self.name,
            ...             status=CheckStatus.WARNING,
            ...             message=f"Found {len(issues)} issues",
            ...             details=issues,
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
