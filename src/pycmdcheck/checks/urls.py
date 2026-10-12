"""URL reachability check.

Validates that URLs declared in package metadata are reachable.
"""

import functools
import http.client
import ipaddress
import logging
import os
import socket
import time
import urllib.error
import urllib.parse
import urllib.request
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import IO, Any

from pycmdcheck.checks.base import BaseCheck
from pycmdcheck.constants import (
    MAX_DETAIL_LINES,
    MAX_URL_CHECKS,
    URL_OVERALL_DEADLINE,
    URL_TIMEOUT,
    URL_TIMEOUT_MAX,
    URL_TIMEOUT_MIN,
)
from pycmdcheck.fileio import plain_text
from pycmdcheck.pyproject_reader import (
    get_effective_project_table,
    get_number,
    get_str_dict,
    is_dynamic,
    pyproject_status,
)
from pycmdcheck.results import CheckResult, CheckStatus, Finding, most_severe

logger = logging.getLogger(__name__)

try:
    _USER_AGENT = f"pycmdcheck/{version('pycmdcheck')}"
except PackageNotFoundError:  # a source tree that is not installed
    _USER_AGENT = "pycmdcheck"

# Lookup errors that mean the name does not exist. EAI_AGAIN (offline) is not one.
_NO_HOST = (socket.EAI_NONAME, getattr(socket, "EAI_NODATA", socket.EAI_NONAME))

_Address = ipaddress.IPv4Address | ipaddress.IPv6Address


def _http_host(url: str) -> str | None:
    """Return the host of an http or https URL, or None for anything else."""
    try:
        parts = urllib.parse.urlsplit(url)
        return parts.hostname if parts.scheme in ("http", "https") else None
    except ValueError:
        return None


def _unverified(error: object) -> str:
    """Word the reason a request said nothing about the URL."""
    return f"could not verify: {plain_text(str(error) or type(error).__name__)}"


def _hosts(url: str) -> set[str]:
    """Return the host of *url* as it is written and as urllib connects to it.

    urllib decodes the percent escapes of a host before it connects, so the
    host written in a URL is not always the name that gets looked up.

    Raises:
        ValueError: The URL has a host that cannot be read.
        http.client.InvalidURL: The URL has a host that urllib refuses too.
    """
    # Read from the URL again, because a proxy replaces the host of a request.
    request = urllib.request.Request(url)
    # The class that opens the connection splits the port off the host.
    dialled = http.client.HTTPConnection(request.host or "").host
    return {urllib.parse.urlsplit(url).hostname or "", dialled}


def _is_local_name(host: str) -> bool:
    """Return whether *host* is empty or a name of the local machine.

    An empty host counts, because a connection to it can reach the local
    machine.
    """
    return not host or host.rstrip(".").lower() == "localhost"


def _addresses(host: str, port: int | None = None) -> list[_Address]:
    """Look *host* up once and return its addresses, each one once.

    Raises:
        OSError: The lookup failed.
    """
    found = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return list(dict.fromkeys(ipaddress.ip_address(info[4][0]) for info in found))


def _not_public(address: _Address) -> bool:
    """Return whether *address* is anything but a public address."""
    return (
        address.is_loopback
        or address.is_private
        or address.is_link_local
        or address.is_reserved
        or address.is_multicast
        or address.is_unspecified
        or not address.is_global
    )


def _is_private(host: str) -> bool:
    """Return whether *host* is the local machine or resolves to a non-public address.

    A name that does not resolve is not private here, so its request goes
    ahead and fails the way it always did.
    """
    if _is_local_name(host):
        return True
    try:
        return any(map(_not_public, _addresses(host)))
    except OSError:
        return False


class _PrivateAddress(Exception):
    """A request was refused because its host is local or private."""


def _private_allowed() -> bool:
    """Return whether the rule against local and private hosts is turned off.

    Only the environment variable PYCMDCHECK_ALLOW_PRIVATE_URLS=1 does that.
    The checked project cannot, because its files are untrusted input.
    """
    return os.environ.get("PYCMDCHECK_ALLOW_PRIVATE_URLS") == "1"


def _refuse_private(url: str) -> None:
    """Raise _PrivateAddress if the host of *url* is local or private.

    This looks the host up only to test it. It serves the requests whose
    connection is opened elsewhere, with a lookup of its own: http or https
    through a proxy.
    """
    if not _private_allowed() and any(map(_is_private, _hosts(url))):
        raise _PrivateAddress(url)


def _connect_public(host: str, port: int, timeout: float | None) -> socket.socket:
    """Look *host* up once and connect to one of the addresses it has.

    The address that is tested is the address that is connected to. Were the
    name looked up a second time to connect, as http.client does, a name
    server could give a public address to the test and a private one to the
    connection.

    Raises:
        _PrivateAddress: The host is the local machine, or one of its
            addresses is not public.
        OSError: The lookup failed, or no address accepted the connection.
    """
    if _is_local_name(host):
        raise _PrivateAddress(host)
    addresses = _addresses(host, port)
    if any(map(_not_public, addresses)):
        raise _PrivateAddress(host)
    error = OSError(f"no address found for {host}")
    for address in addresses:
        try:
            # An address written out in full is not looked up again.
            return socket.create_connection((str(address), port), timeout)
        except OSError as exc:
            error = exc
    raise error


class _PublicHTTPConnection(http.client.HTTPConnection):
    """An HTTP connection that only goes to a public address.

    The Host header still carries the name, because only the socket is
    opened by address.
    """

    def connect(self) -> None:
        """Open the socket to an address of the host that passed the test."""
        self.sock = _connect_public(self.host, self.port, self.timeout)


class _PublicHTTPSConnection(http.client.HTTPSConnection, _PublicHTTPConnection):
    """An HTTPS connection that only goes to a public address.

    HTTPSConnection.connect() opens the socket through the connect() above
    and then starts TLS on it with the default context, which checks the
    certificate against the host name.
    """


def _connection_class(
    request: urllib.request.Request,
    plain: type[http.client.HTTPConnection],
    public: type[http.client.HTTPConnection],
) -> type[http.client.HTTPConnection]:
    """Choose the class that opens the connection for *request*.

    Raises:
        _PrivateAddress: The request goes through a proxy and the host of its
            URL is local or private.
    """
    if _private_allowed():
        return plain
    # urllib marks a request that goes through a proxy in one of these two
    # ways. The second is an https request that the proxy tunnels.
    if request.has_proxy() or getattr(request, "_tunnel_host", None):
        # The proxy looks the name up and connects, so the address cannot be
        # held to the one tested here. Such a request stays as it was: the
        # host is tested with a lookup of its own and the proxy is trusted.
        _refuse_private(request.full_url)
        return plain
    return public


class _PublicHTTPHandler(urllib.request.HTTPHandler):
    """Open http requests, the first and every redirect hop, to public hosts only."""

    def http_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        """Open *req* unless its host is local or private."""
        connection = _connection_class(
            req, http.client.HTTPConnection, _PublicHTTPConnection
        )
        return self.do_open(connection, req)


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    """Open https requests, the first and every redirect hop, to public hosts only."""

    def https_open(self, req: urllib.request.Request) -> http.client.HTTPResponse:
        """Open *req* unless its host is local or private."""
        connection = _connection_class(
            req, http.client.HTTPSConnection, _PublicHTTPSConnection
        )
        return self.do_open(connection, req)


class _LeftHTTP(Exception):
    """A redirect was refused because it leads to a URL that is not http or https."""


class _HTTPRedirectsOnly(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to an http or https URL.

    urllib also follows one to an ftp URL, and its ftp handler looks the host
    up a second time before it connects. The address that was tested would
    then not be the address connected to.
    """

    def http_error_302(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: http.client.HTTPMessage,
    ) -> Any:
        """Follow the redirect unless it names a scheme other than http or https.

        Raises:
            _LeftHTTP: The redirect leads to such a URL.
        """
        # The headers urllib reads the target from. A target without a scheme
        # is relative to the URL of the request, which is http or https.
        target = headers.get("location", headers.get("uri", ""))
        if urllib.parse.urlsplit(target).scheme not in ("http", "https", ""):
            fp.close()
            raise _LeftHTTP(target)
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_308 = http_error_302


@functools.cache
def _opener() -> urllib.request.OpenerDirector:
    """Build, once, the opener that every request of the check goes through."""
    return urllib.request.build_opener(
        _PublicHTTPHandler, _PublicHTTPSHandler, _HTTPRedirectsOnly
    )


class URLsCheck(BaseCheck):
    """Check that project URLs in metadata are reachable.

    Reads the URLs from ``[project.urls]`` in pyproject.toml (or the legacy
    Poetry keys) and requests each http or https URL with HEAD, then GET.
    A URL on a local or private host is listed and not requested. A redirect
    is followed only to another http or https URL.

    Attributes:
        name: The check identifier ("urls").
        description: Human-readable description of this check.
        rules: The codes of the findings, each with what the rule means.
        hints: The same codes, each with one sentence that says what to do.
    """

    name = "urls"
    description = "Validate project URLs are reachable"
    rules = {
        "urls.unreachable": (
            "A project URL answers HTTP 404 or 410, or its host name does not exist"
        ),
        "urls.unverified": (
            "A project URL could not be verified, for example because of "
            "HTTP 403, a server error or a timeout"
        ),
        "urls.invalid-value": "A value in the [project.urls] table is not a string",
    }
    hints = {
        "urls.unreachable": (
            "Correct each failed address, usually under [project.urls] in "
            "pyproject.toml, or remove the entry if the page is gone."
        ),
        "urls.unverified": (
            "Open in a browser each address that could not be verified, and "
            "correct it in pyproject.toml if it does not load there."
        ),
        "urls.invalid-value": (
            "Write each value in [project.urls] of pyproject.toml as a quoted "
            'string, such as Homepage = "https://example.com".'
        ),
    }

    def run(self, package_path: Path, config: dict[str, Any]) -> CheckResult:
        """Check URL reachability for project metadata URLs.

        Args:
            package_path: Path to the package directory to check.
            config: Configuration dictionary for this check. Supports
                ``timeout`` (seconds per request, default URL_TIMEOUT), kept
                between URL_TIMEOUT_MIN and URL_TIMEOUT_MAX.

        Returns:
            A CheckResult with:

            - OK status if all URLs are reachable
            - WARNING status if a URL answers 404 or 410 or its host is unknown
            - NOTE status if a URL could not be verified (HTTP 403, a server
              error, a timeout), or a value of the URL table is not a string
            - SKIPPED status if no URL was requested. The reason is
              "not-applicable" when the project declares no URLs, declares
              them dynamically, or has none that is http or https and on a
              public host. It is "prerequisite-failed" when pyproject.toml is
              missing or unreadable, has no ``[project]`` table, or has a
              urls value that is not a table, which the metadata check
              reports. It is "network-unavailable" when no host answered.

            A URL on a local or private host is listed as not checked and
            counts as neither unreachable nor unverified.
        """
        low, high = URL_TIMEOUT_MIN, URL_TIMEOUT_MAX
        timeout = get_number(config, "timeout", URL_TIMEOUT, minimum=low, maximum=high)
        pyproject = pyproject_status(package_path)
        if pyproject == "unparseable":
            return CheckResult.skip(
                self.name,
                "pyproject.toml could not be read",
                "prerequisite-failed",
                ["The metadata check reports the reason"],
            )
        if pyproject == "missing":
            return CheckResult.skip(
                self.name,
                "No pyproject.toml found",
                "prerequisite-failed",
                [
                    "Cannot check URLs without pyproject.toml",
                    "The metadata check reports the missing file",
                ],
            )

        project = get_effective_project_table(package_path)
        if not project:
            return CheckResult.skip(
                self.name,
                "No [project] table",
                "prerequisite-failed",
                [
                    "Cannot check URLs without a [project] table",
                    "The metadata check reports the missing [project] table",
                ],
            )
        declared = project.get("urls")
        urls = get_str_dict(project, "urls")
        if declared is not None and (
            not isinstance(declared, dict) or len(urls) < len(declared)
        ):
            message = "Project URLs are not a table of strings"
            if not isinstance(declared, dict):
                # The finding of the metadata check carries the hint.
                owner = "The metadata check reports the urls value that is not a table"
                return CheckResult.skip(
                    self.name, message, "prerequisite-failed", [owner]
                )
            # Only this check reads the values of the table.
            said = "A value in the [project.urls] table is not a string"
            return CheckResult(
                name=self.name,
                status=CheckStatus.NOTE,
                message=message,
                findings=[Finding("urls.invalid-value", said, CheckStatus.NOTE)],
            )
        if not urls and is_dynamic(project, "urls"):
            return CheckResult.skip(
                self.name,
                "Project URLs are dynamic",
                "not-applicable",
                ["The build backend supplies the URLs, so they cannot be checked"],
            )
        if not urls:
            return CheckResult.skip(
                self.name,
                "No project URLs defined",
                "not-applicable",
                [
                    "Add [project.urls] to pyproject.toml",
                    'Example: Homepage = "https://github.com/user/project"',
                ],
            )

        # One request per distinct http or https URL, under its first label.
        targets: dict[str, str] = {}
        other: list[str] = []
        for label, url in urls.items():
            if _http_host(url):
                targets.setdefault(url, label)
            else:
                other.append(label)

        deadline = time.monotonic() + URL_OVERALL_DEADLINE
        lines: dict[str, list[str]] = {"broken": [], "unverified": [], "ok": []}
        private: list[str] = []
        hosts: set[str | None] = set()
        answered = False
        checked = 0
        for url, label in list(targets.items())[:MAX_URL_CHECKS]:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            checked += 1
            state, reason, responded = self._check_url(
                url, min(timeout, max(remaining, URL_TIMEOUT_MIN))
            )
            shown = f"{plain_text(label)}: {plain_text(url)}"
            if state == "private":
                private.append(f"{shown} not checked ({reason})")
                continue
            answered = answered or responded
            hosts.add(_http_host(url))
            lines[state].append(f"{shown} ({reason})")
        broken, unverified, reachable = lines.values()
        requested = checked - len(private)

        notes: list[str] = [*private]
        if len(targets) > checked:
            limit = f"time limit of {URL_OVERALL_DEADLINE} seconds"
            if checked == MAX_URL_CHECKS:
                limit = f"limit {MAX_URL_CHECKS}"
            notes.append(f"{len(targets) - checked} more URL(s) not checked ({limit})")
        if other:
            names = plain_text(", ".join(other))
            notes.append(f"{len(other)} URL(s) not checked, not http or https: {names}")

        if not requested:
            message = "No http or https project URLs to check"
            if private:
                message = "No public project URLs to check"
            return CheckResult.skip(self.name, message, "not-applicable", notes)
        if not answered and len(hosts) > 1:
            # Silence from every host says the network is down, not the URLs.
            tried = f"No response from any of the {len(hosts)} hosts tried"
            return CheckResult.skip(
                self.name,
                "Network unavailable, project URLs not checked",
                "network-unavailable",
                [tried, (broken + unverified)[0]],
            )

        # Failures are never cut. Reachable URLs fill the rest of the limit.
        room = max(MAX_DETAIL_LINES - len(broken) - len(unverified), 0)
        details = broken + unverified + reachable[:room]
        if len(reachable) > room:
            details.append(f"... and {len(reachable) - room} more reachable URL(s)")
        details += notes
        # One finding for each rule. The most severe one gives the message.
        findings: list[Finding] = []
        if broken:
            said = f"{len(broken)} of {requested} URL(s) unreachable"
            findings.append(Finding("urls.unreachable", said, CheckStatus.WARNING))
        if unverified:
            said = f"{len(unverified)} of {requested} URL(s) could not be verified"
            findings.append(Finding("urls.unverified", said, CheckStatus.NOTE))
        message = f"All {requested} project URL(s) reachable"
        if findings:
            message = findings[0].message
        return CheckResult(
            name=self.name,
            status=most_severe(findings),
            message=message,
            details=details,
            findings=findings,
        )

    def _check_url(self, url: str, timeout: float) -> tuple[str, str, bool]:
        """Request a URL with HEAD, then with GET if HEAD does not succeed.

        Returns:
            ``(state, reason, answered)``. *state* is ``"ok"``, ``"broken"``
            (HTTP 404 or 410, or an unknown host), ``"unverified"`` (any
            other failure, and a redirect to a URL that is not http or
            https) or ``"private"`` (the URL, or one it redirects to, is on a
            local or private host and was not requested). *reason* is the
            text shown after the URL. *answered* tells whether a server
            replied at all.
        """
        status: object = None
        for method in ("HEAD", "GET"):
            try:
                request = urllib.request.Request(
                    url, method=method, headers={"User-Agent": _USER_AGENT}
                )
                with _opener().open(request, timeout=timeout) as response:
                    status = getattr(response, "status", None)
            except _PrivateAddress:
                return "private", "local or private address", False
            except _LeftHTTP:
                left = "redirects to a URL that is not http or https"
                return "unverified", _unverified(left), True
            except urllib.error.HTTPError as exc:
                status = exc.code
            except urllib.error.URLError as exc:
                error = exc.reason
                if isinstance(error, socket.gaierror) and error.errno in _NO_HOST:
                    return "broken", "FAILED: host name not found", False
                return "unverified", _unverified(error), isinstance(status, int)
            except (OSError, ValueError, http.client.HTTPException) as exc:
                return "unverified", _unverified(exc), isinstance(status, int)
            if isinstance(status, int) and status < 400:
                return "ok", "OK", True
        text = f"HTTP {status}" if isinstance(status, int) else "no HTTP status"
        if status in (404, 410):
            return "broken", f"FAILED: {text}", True
        return "unverified", _unverified(text), True
