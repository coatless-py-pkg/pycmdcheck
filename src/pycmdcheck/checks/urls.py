"""URL reachability check.

Validates that URLs declared in package metadata are reachable.
"""

import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import MAX_DETAIL_LINES, MAX_URL_CHECKS, URL_TIMEOUT
from pycmdcheck.pyproject_reader import read_pyproject
from pycmdcheck.results import CheckResult, CheckStatus

logger = logging.getLogger(__name__)


class URLsCheck(BaseCheck):
    """Check that project URLs in metadata are reachable.

    Extracts URLs from ``[project.urls]`` in pyproject.toml and
    validates each with an HTTP HEAD request.

    Attributes:
        name: The check identifier ("urls").
        description: Human-readable description of this check.
    """

    name = "urls"
    description = "Validate project URLs are reachable"

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check URL reachability for project metadata URLs.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check.
                Supports ``timeout`` (int) to override URL_TIMEOUT.

        Returns:
            A CheckResult with:

            - OK status if all URLs are reachable
            - WARNING status if some URLs are unreachable
            - NOTE status if no URLs found in metadata
        """
        details: list[str] = []
        timeout = config.get("timeout", URL_TIMEOUT)

        pyproject = read_pyproject(package_path)
        if pyproject is None:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No pyproject.toml found",
                details=["Cannot check URLs without pyproject.toml"],
            )

        project = pyproject.get("project", {})
        urls = project.get("urls", {})

        if not urls:
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message="No project URLs defined",
                details=[
                    "Add [project.urls] to pyproject.toml",
                    'Example: Homepage = "https://github.com/user/project"',
                ],
            )

        unreachable: list[str] = []
        checked = 0

        for label, url in list(urls.items())[:MAX_URL_CHECKS]:
            checked += 1
            reachable, error = self._check_url(url, timeout)
            if reachable:
                details.append(f"{label}: {url} (OK)")
            else:
                unreachable.append(label)
                details.append(f"{label}: {url} (FAILED: {error})")

        if unreachable:
            return CheckResult(
                name=self.name,
                status=CheckStatus.WARNING,
                message=(f"{len(unreachable)} of {checked} URL(s) unreachable"),
                details=details[:MAX_DETAIL_LINES],
            )

        return CheckResult(
            name=self.name,
            status=CheckStatus.OK,
            message=f"All {checked} project URL(s) reachable",
            details=details[:MAX_DETAIL_LINES],
        )

    def _check_url(self, url: str, timeout: int) -> tuple[bool, str | None]:
        """Check if a URL is reachable via HTTP HEAD then GET.

        Returns:
            A tuple of (reachable, error_message).
        """
        for method in ("HEAD", "GET"):
            try:
                req = urllib.request.Request(url, method=method)
                req.add_header("User-Agent", "pycmdcheck/0.1")
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    if resp.status < 400:
                        return True, None
            except urllib.error.HTTPError as e:
                if method == "HEAD" and e.code == 405:
                    continue  # Try GET instead
                return False, f"HTTP {e.code}"
            except urllib.error.URLError as e:
                return False, str(e.reason)
            except (OSError, ValueError) as e:
                return False, str(e)

        return False, "All methods failed"
