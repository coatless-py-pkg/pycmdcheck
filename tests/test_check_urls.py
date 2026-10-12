"""Tests for urls check.

No test opens a socket or looks a name up. Most replace the ``open`` of the
opener the check builds. The tests of the private address rule keep the real
opener, its handlers and their connections, and replace what lies under them:
the name lookup, the function that opens the socket and the TLS handshake.
"""

import http.client
import io
import ipaddress
import re
import socket
import ssl
import urllib.error
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from pycmdcheck import __version__
from pycmdcheck.checks import urls as urls_module
from pycmdcheck.checks.urls import URLsCheck
from pycmdcheck.constants import URL_OVERALL_DEADLINE
from pycmdcheck.results import CheckResult, CheckStatus
from tests.helpers import codes, skipped

OPEN = "pycmdcheck.checks.urls.urllib.request.OpenerDirector.open"
ALLOW_PRIVATE = "PYCMDCHECK_ALLOW_PRIVATE_URLS"
NOT_CHECKED = "not checked (local or private address)"
PUBLIC_ADDRESS = "93.184.216.34"
LOOPBACK_ADDRESS = "127.0.0.1"
# What a resolver answers for hosts that are not an address written in full.
# The first three are short forms of 127.0.0.1 that getaddrinfo accepts.
RESOLVER_ANSWERS = {
    "127.1": ["127.0.0.1"],
    "0x7f.0.0.1": ["127.0.0.1"],
    "2130706433": ["127.0.0.1"],
    "example.org/.evil.example": ["127.0.0.1"],
}
UNKNOWN_HOST = urllib.error.URLError(socket.gaierror(socket.EAI_NONAME, "no name"))
# What an offline Linux machine answers: the lookup failed, the name may exist.
LOOKUP_FAILED = urllib.error.URLError(
    socket.gaierror(socket.EAI_AGAIN, "Temporary failure in name resolution")
)


class FakeResponse:
    """What urlopen returns for a request that did not fail."""

    def __init__(self, status: Any) -> None:
        self.status = status

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *exc_info: object) -> bool:
        return False


class FakeWeb:
    """Stand-in for the opener that answers by URL and records every request.

    An answer is an HTTP status, an exception to raise, or a dict that maps
    the request method to either. URLs without an answer get HTTP 200.
    """

    def __init__(self, answers: dict[str, Any] | None = None) -> None:
        self.answers = answers or {}
        self.calls: list[tuple[str, str]] = []
        self.timeouts: list[float] = []
        self.agents: list[str | None] = []

    def __call__(self, request: Any, timeout: float = 0) -> FakeResponse:
        url, method = request.full_url, request.get_method()
        self.calls.append((method, url))
        self.timeouts.append(timeout)
        self.agents.append(request.get_header("User-agent"))
        answer = self.answers.get(url, 200)
        if isinstance(answer, dict):
            answer = answer.get(method, 200)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int) and answer >= 400:
            headers = http.client.HTTPMessage()
            raise urllib.error.HTTPError(url, answer, "error", headers, None)
        return FakeResponse(answer)


def make_project(root: Path, urls: dict[str, str], project: str = "") -> Path:
    """Write a pyproject.toml whose [project.urls] table holds *urls*."""
    lines = [f'"{label}" = "{url}"' for label, url in urls.items()]
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "pkg"\nversion = "0.1.0"\n{project}\n'
        "[project.urls]\n" + "\n".join(lines) + "\n",
        encoding="utf-8",
    )
    return root


def check(
    root: Path, web: FakeWeb, config: dict[str, Any] | None = None
) -> CheckResult:
    with patch(OPEN, web):
        return URLsCheck().run(root, config or {})


class FakeSocket:
    """A connection to a server that answers one request with a canned reply."""

    def __init__(self, net: "FakeNet") -> None:
        self.net = net
        self.scheme = "http"
        self.reply = b""

    def setsockopt(self, *args: object) -> None:
        return None

    def sendall(self, data: bytes) -> None:
        """Note the URL that was asked for, from the request line and Host."""
        head = data.decode("ascii").split("\r\n")
        method, target = head[0].split()[:2]
        if method == "CONNECT":
            # A proxy is asked to pass a TLS connection on to a host.
            url = f"CONNECT {target}"
        elif "://" in target:
            # A proxy is sent the whole URL, a server only the path.
            url = target
        else:
            host = next(line[6:] for line in head if line.startswith("Host: "))
            url = f"{self.scheme}://{host}{target}"
        self.net.requested.append(url)
        location = self.net.redirects.get(url)
        status = "200 OK"
        if location:
            status = f"{self.net.redirect_status} Moved\r\nLocation: {location}"
        reply = f"HTTP/1.1 {status}\r\nContent-Length: 0\r\n\r\n"
        self.reply = reply.encode("ascii")

    def makefile(self, *args: object, **kwargs: object) -> io.BytesIO:
        return io.BytesIO(self.reply)

    def close(self) -> None:
        return None


class FakeNet:
    """Name lookup, sockets and TLS for the real urllib opener, with no network.

    *addresses* maps a host name to the addresses it resolves to, or to the
    error its lookup raises. Any other name gets a public address, and an
    address written out resolves to itself. A name in *rebinding* gets a
    public address the first time it is looked up and a loopback address
    every time after that. An address in *refusing* refuses the connection.
    *redirects* maps a URL to the URL it redirects to, which it does with
    the HTTP status in *redirect_status*. Every other request is answered
    with HTTP 200.

    What happened is recorded in *lookups* (the names that were looked up),
    *dialled* (the addresses that were connected to), *requested* (the URLs
    that were asked for) and *verified* (the names whose certificate a TLS
    handshake was told to check).
    """

    def __init__(self) -> None:
        self.addresses: dict[str, Any] = {}
        self.rebinding: set[str] = set()
        self.refusing: set[str] = set()
        self.redirects: dict[str, str] = {}
        self.redirect_status = 302
        self.lookups: list[str] = []
        self.dialled: list[str] = []
        self.requested: list[str] = []
        self.verified: list[str | None] = []

    def getaddrinfo(self, host: str, *args: Any, **kwargs: Any) -> list[Any]:
        try:
            found: Any = [str(ipaddress.ip_address(host))]
        except ValueError:
            self.lookups.append(host)
            found = self.addresses.get(host, [PUBLIC_ADDRESS])
            if host in self.rebinding and self.lookups.count(host) > 1:
                found = [LOOPBACK_ADDRESS]
        if isinstance(found, BaseException):
            raise found
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (a, 0)) for a in found]

    def connect(self, address: tuple[str, int], *args: Any, **kwargs: Any) -> Any:
        """Stand in for socket.create_connection, which looks a name up itself."""
        dialled = self.getaddrinfo(address[0])[0][4][0]
        self.dialled.append(dialled)
        if dialled in self.refusing:
            raise ConnectionRefusedError(f"{dialled} refused the connection")
        return FakeSocket(self)

    def wrap(self, sock: FakeSocket, server_hostname: str | None = None) -> Any:
        """Stand in for the TLS handshake of ssl.SSLContext.wrap_socket."""
        self.verified.append(server_hostname)
        sock.scheme = "https"
        return sock

    def gethostbyname(self, host: str) -> str:
        """Stand in for the lookup urllib's ftp handler makes before it connects."""
        self.dialled.append(host)
        raise OSError("no ftp in the tests")


@pytest.fixture
def net(monkeypatch: pytest.MonkeyPatch) -> FakeNet:
    """Put a FakeNet under the real opener, with the private address rule on."""
    fake = FakeNet()
    monkeypatch.delenv(ALLOW_PRIVATE, raising=False)
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    monkeypatch.setattr(socket, "gethostbyname", fake.gethostbyname)
    monkeypatch.setattr(socket, "create_connection", fake.connect)
    monkeypatch.setattr(ssl.SSLContext, "wrap_socket", fake.wrap)
    return fake


class TestURLsCheck:
    """Tests for URLsCheck."""

    def test_no_pyproject(self, empty_dir: Path) -> None:
        """A missing pyproject.toml is reported by the metadata check alone."""
        result = check(empty_dir, FakeWeb())
        assert skipped(result) == "prerequisite-failed"
        assert result.message == "No pyproject.toml found"
        assert result.details == [
            "Cannot check URLs without pyproject.toml",
            "The metadata check reports the missing file",
        ]

    def test_no_project_table(self, tmp_path: Path) -> None:
        """A missing [project] table is reported by the metadata check alone."""
        (tmp_path / "pyproject.toml").write_text("[tool.black]\nline-length = 88\n")
        result = check(tmp_path, FakeWeb())
        assert skipped(result) == "prerequisite-failed"
        assert result.message == "No [project] table"
        assert result.details == [
            "Cannot check URLs without a [project] table",
            "The metadata check reports the missing [project] table",
        ]

    @pytest.mark.parametrize(
        ("pyproject", "message", "reason"),
        [
            (
                '[project]\nname = "mypkg"\n',
                "No project URLs defined",
                "not-applicable",
            ),
            ('project = "hello"\n', "No [project] table", "prerequisite-failed"),
            (
                '[project]\nname = "p"\ndynamic = ["urls"]\n',
                "Project URLs are dynamic",
                "not-applicable",
            ),
            # The metadata check reports a urls value that is not a table.
            (
                '[project]\nname = "p"\nurls = ["https://example.org"]\n',
                "Project URLs are not a table of strings",
                "prerequisite-failed",
            ),
        ],
    )
    def test_nothing_to_check_is_skipped(
        self, tmp_path: Path, pyproject: str, message: str, reason: str
    ) -> None:
        """Absent, dynamic or unusable URL metadata is skipped, never an error."""
        (tmp_path / "pyproject.toml").write_text(pyproject)
        web = FakeWeb()
        result = check(tmp_path, web)
        assert (skipped(result), result.message) == (reason, message)
        assert result.details
        assert web.calls == []

    def test_value_that_is_not_a_string_is_a_note(self, tmp_path: Path) -> None:
        """Only this check reads the values of the table, so it reports them."""
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "p"\n[project.urls]\nH = "https://e.org"\nN = 5\n'
        )
        web = FakeWeb()
        result = check(tmp_path, web)
        assert result.status == CheckStatus.NOTE
        assert result.message == "Project URLs are not a table of strings"
        assert codes(result) == ["urls.invalid-value"]
        # What to do is the hint of the rule, so no detail line says it.
        assert result.details == []
        assert web.calls == []

    def test_unparseable_pyproject_is_skipped(self, tmp_path: Path) -> None:
        """A broken pyproject.toml is reported by the metadata check alone."""
        (tmp_path / "pyproject.toml").write_text("[project\n")
        result = check(tmp_path, FakeWeb())
        assert skipped(result) == "prerequisite-failed"
        assert result.message == "pyproject.toml could not be read"
        assert result.details == ["The metadata check reports the reason"]

    def test_all_urls_reachable(self, temp_package: Path) -> None:
        """All URLs reachable -> OK."""
        web = FakeWeb()
        result = check(temp_package, web)
        assert result.status == CheckStatus.OK
        assert codes(result) == []
        assert result.message == "All 1 project URL(s) reachable"
        assert result.details == ["Homepage: https://example.com (OK)"]
        assert web.calls == [("HEAD", "https://example.com")]
        assert web.agents == [f"pycmdcheck/{__version__}"]

    def test_suite_stub_answers_when_nothing_is_patched(
        self, temp_package: Path
    ) -> None:
        """The urllib stub in conftest.py covers the opener the check uses."""
        result = URLsCheck().run(temp_package, {})
        assert result.status == CheckStatus.OK
        assert result.details == ["Homepage: https://example.com (OK)"]

    @pytest.mark.parametrize(
        ("answer", "reason"),
        [
            (404, "HTTP 404"),
            (410, "HTTP 410"),
            (UNKNOWN_HOST, "host name not found"),
        ],
    )
    def test_dead_url_warns(self, tmp_path: Path, answer: Any, reason: str) -> None:
        """Only 404, 410 and an unknown host count as unreachable."""
        url = "https://gone.example/page"
        result = check(make_project(tmp_path, {"Docs": url}), FakeWeb({url: answer}))
        assert result.status == CheckStatus.WARNING
        assert result.message == "1 of 1 URL(s) unreachable"
        assert codes(result) == ["urls.unreachable"]
        assert result.findings[0].message == "1 of 1 URL(s) unreachable"
        assert result.details == [f"Docs: {url} (FAILED: {reason})"]

    @pytest.mark.parametrize(
        ("answer", "reason"),
        [
            (401, "HTTP 401"),
            (403, "HTTP 403"),
            (429, "HTTP 429"),
            (500, "HTTP 500"),
            (None, "no HTTP status"),
            (TimeoutError("timed out"), "timed out"),
            (LOOKUP_FAILED, "[Errno"),
            (urllib.error.URLError(TimeoutError("timed out")), "timed out"),
            (
                urllib.error.URLError(ConnectionRefusedError(61, "Connection refused")),
                "[Errno 61] Connection refused",
            ),
            (
                http.client.InvalidURL("URL can't contain control characters"),
                "URL can't",
            ),
        ],
    )
    def test_unverifiable_url_is_a_note(
        self, tmp_path: Path, answer: Any, reason: str
    ) -> None:
        """A server that refuses, fails or times out says nothing about the link."""
        url = "https://doi.org/10.1002/jcc.26486"
        result = check(make_project(tmp_path, {"Paper": url}), FakeWeb({url: answer}))
        assert result.status == CheckStatus.NOTE
        assert result.message == "1 of 1 URL(s) could not be verified"
        assert codes(result) == ["urls.unverified"]
        assert result.findings[0].message == "1 of 1 URL(s) could not be verified"
        assert len(result.details) == 1
        assert result.details[0].startswith(f"Paper: {url} (could not verify: {reason}")

    @pytest.mark.parametrize("head_status", [403, 405, 501])
    def test_get_is_tried_when_head_is_refused(
        self, tmp_path: Path, head_status: int
    ) -> None:
        """A server that rejects HEAD is asked again with GET."""
        url = "https://example.org/docs"
        web = FakeWeb({url: {"HEAD": head_status, "GET": 200}})
        result = check(make_project(tmp_path, {"Docs": url}), web)
        assert result.status == CheckStatus.OK
        assert web.calls == [("HEAD", url), ("GET", url)]

    @pytest.mark.parametrize(
        "failure",
        [UNKNOWN_HOST, LOOKUP_FAILED, TimeoutError("timed out")],
        ids=["unknown host", "lookup failed", "timed out"],
    )
    def test_network_unavailable_is_skipped(
        self, tmp_path: Path, failure: Exception
    ) -> None:
        """Two hosts that both fail before any HTTP response mean no network."""
        urls = {"Home": "https://a.example/", "Docs": "https://b.example/"}
        web = FakeWeb(dict.fromkeys(urls.values(), failure))
        result = check(make_project(tmp_path, urls), web)
        assert skipped(result) == "network-unavailable"
        assert result.message == "Network unavailable, project URLs not checked"
        assert result.details[0] == "No response from any of the 2 hosts tried"

    def test_one_dead_host_is_not_a_network_failure(self, tmp_path: Path) -> None:
        """Failures on a single host are reported, not skipped."""
        urls = {"Home": "https://a.example/", "Docs": "https://a.example/docs"}
        web = FakeWeb(dict.fromkeys(urls.values(), UNKNOWN_HOST))
        result = check(make_project(tmp_path, urls), web)
        assert result.status == CheckStatus.WARNING
        assert result.message == "2 of 2 URL(s) unreachable"
        assert codes(result) == ["urls.unreachable"]

    def test_host_that_answered_head_is_not_a_network_failure(
        self, tmp_path: Path
    ) -> None:
        """Hosts that refused HEAD have answered, whatever GET does next."""
        urls = {"Home": "https://a.example/", "Docs": "https://b.example/"}
        answer = {"HEAD": 403, "GET": TimeoutError("timed out")}
        web = FakeWeb(dict.fromkeys(urls.values(), answer))
        result = check(make_project(tmp_path, urls), web)
        assert result.status == CheckStatus.NOTE
        assert result.message == "2 of 2 URL(s) could not be verified"
        assert codes(result) == ["urls.unverified"]

    def test_only_http_and_https_are_requested(self, tmp_path: Path) -> None:
        """file:, mailto: and malformed URLs are named and never opened."""
        urls = {
            "Local": "file:///etc/hosts",
            "Email": "mailto:someone@example.org",
            "Bad": "https://",
            "Bracket": "http://[::1",
            "Home": "HTTPS://example.org",
        }
        web = FakeWeb()
        result = check(make_project(tmp_path, urls), web)
        assert result.status == CheckStatus.OK
        assert web.calls == [("HEAD", "HTTPS://example.org")]
        assert result.details == [
            "Home: HTTPS://example.org (OK)",
            "4 URL(s) not checked, not http or https: Local, Email, Bad, Bracket",
        ]

    def test_no_http_urls_is_skipped(self, tmp_path: Path) -> None:
        """A table with nothing to request is skipped."""
        urls = {"Email": "mailto:someone@example.org"}
        web = FakeWeb()
        result = check(make_project(tmp_path, urls), web)
        assert skipped(result) == "not-applicable"
        assert result.message == "No http or https project URLs to check"
        assert result.details == ["1 URL(s) not checked, not http or https: Email"]
        assert web.calls == []

    @pytest.mark.parametrize(
        ("config", "expected"),
        [
            ({}, 10),
            ({"timeout": 600}, 30),
            ({"timeout": 0}, 1),
            ({"timeout": "fast"}, 10),
        ],
    )
    def test_timeout_is_clamped(
        self, temp_package: Path, config: dict[str, Any], expected: int
    ) -> None:
        """The configured timeout stays between 1 and 30 seconds."""
        web = FakeWeb()
        assert check(temp_package, web, config).status == CheckStatus.OK
        assert web.timeouts == [expected]

    def test_overall_deadline_stops_the_run(self, tmp_path: Path) -> None:
        """URLs left when the time budget is spent are reported as not checked."""
        urls = {f"U{i}": f"https://example.org/{i}" for i in range(3)}
        web = FakeWeb()
        clock = "pycmdcheck.checks.urls.time.monotonic"
        late = URL_OVERALL_DEADLINE + 1.0
        with patch(clock, side_effect=lambda: late if web.calls else 0.0):
            result = check(make_project(tmp_path, urls), web)
        assert result.status == CheckStatus.OK
        assert result.message == "All 1 project URL(s) reachable"
        assert result.details[-1] == (
            f"2 more URL(s) not checked (time limit of {URL_OVERALL_DEADLINE} seconds)"
        )

    def test_failures_come_first_and_the_cap_is_reported(self, tmp_path: Path) -> None:
        """With 25 URLs, 20 are requested and the failing one is never cut."""
        urls = {f"U{i:02}": f"https://example.org/{i:02}" for i in range(25)}
        web = FakeWeb({"https://example.org/15": 404, "https://example.org/16": 403})
        result = check(make_project(tmp_path, urls), web)
        assert result.status == CheckStatus.WARNING
        assert result.message == "1 of 20 URL(s) unreachable"
        # One finding for each rule, each with its own status. The runner adds
        # the hints.
        assert [finding.to_dict() for finding in result.findings] == [
            {
                "code": "urls.unreachable",
                "status": "warning",
                "message": "1 of 20 URL(s) unreachable",
                "hint": None,
            },
            {
                "code": "urls.unverified",
                "status": "note",
                "message": "1 of 20 URL(s) could not be verified",
                "hint": None,
            },
        ]
        assert codes(result) == ["urls.unreachable", "urls.unverified"]
        assert len({url for _method, url in web.calls}) == 20
        assert result.details[:2] == [
            "U15: https://example.org/15 (FAILED: HTTP 404)",
            "U16: https://example.org/16 (could not verify: HTTP 403)",
        ]
        assert result.details[-2:] == [
            "... and 10 more reachable URL(s)",
            "5 more URL(s) not checked (limit 20)",
        ]
        assert len(result.details) == 12

    def test_same_url_is_requested_once(self, tmp_path: Path) -> None:
        """One URL under several labels is one request."""
        urls = dict.fromkeys(
            ("Homepage", "Source", "Repository"), "https://example.org"
        )
        web = FakeWeb()
        result = check(make_project(tmp_path, urls), web)
        assert result.message == "All 1 project URL(s) reachable"
        assert web.calls == [("HEAD", "https://example.org")]

    def test_poetry_urls_are_checked(self, tmp_path: Path) -> None:
        """Legacy Poetry homepage and repository keys are project URLs."""
        (tmp_path / "pyproject.toml").write_text(
            '[tool.poetry]\nname = "p"\nhomepage = "https://example.org"\n'
            '[tool.poetry.urls]\nTracker = "https://example.org/issues"\n'
        )
        web = FakeWeb()
        result = check(tmp_path, web)
        assert result.message == "All 2 project URL(s) reachable"
        assert [url for _method, url in web.calls] == [
            "https://example.org",
            "https://example.org/issues",
        ]

    @pytest.mark.parametrize(
        ("code", "before", "said"),
        [
            (
                "urls.unreachable",
                '"https://gone.example/"',
                "Correct each failed address, usually under [project.urls] in",
            ),
            (
                "urls.unverified",
                '"https://refused.example/"',
                "and correct it in pyproject.toml if it does not load there.",
            ),
            (
                "urls.invalid-value",
                "5",
                "Write each value in [project.urls] of pyproject.toml as a quoted",
            ),
        ],
    )
    def test_following_the_hint_clears_the_code(
        self, tmp_path: Path, code: str, before: str, said: str
    ) -> None:
        """Each rule stops once its entry is a quoted address that answers."""
        assert said in URLsCheck.hints[code]
        # The entry written is the example that the hint for a bad value shows.
        shown = re.search(
            r'such as Homepage = (".+")\.$', URLsCheck.hints["urls.invalid-value"]
        )
        assert shown is not None
        web = FakeWeb({"https://gone.example/": 404, "https://refused.example/": 403})
        found = []
        for value in (before, shown[1]):
            (tmp_path / "pyproject.toml").write_text(
                f'[project]\nname = "p"\n[project.urls]\nHomepage = {value}\n'
            )
            found.append(codes(check(tmp_path, web)))
        assert found == [[code], []]


@pytest.mark.real_urlopen
class TestPrivateAddresses:
    """The check does not contact local or private hosts."""

    @pytest.mark.parametrize(
        "url",
        [
            "http://localhost:8000/",
            "http://127.0.0.1:8000/",
            "http://[::1]/",
            "http://10.0.0.5/",
            "https://192.168.1.1/admin",
            "http://169.254.169.254/latest/meta-data/",
            "http://0.0.0.0/",
            "http://224.0.0.1/",
            "http://240.0.0.1/",
            "http://100.64.0.1/",
        ],
    )
    def test_local_or_private_address_is_not_requested(
        self, tmp_path: Path, net: FakeNet, url: str
    ) -> None:
        """localhost and a loopback, private or other special address -> no request."""
        result = URLsCheck().run(make_project(tmp_path, {"Home": url}), {})
        assert (net.dialled, net.requested) == ([], [])
        assert skipped(result) == "not-applicable"
        assert result.message == "No public project URLs to check"
        assert result.details == [f"Home: {url} {NOT_CHECKED}"]

    @pytest.mark.parametrize(
        "addresses",
        [["10.1.2.3"], [PUBLIC_ADDRESS, LOOPBACK_ADDRESS], ["fe80::1%en0"]],
    )
    def test_name_that_resolves_to_a_private_address_is_not_requested(
        self, tmp_path: Path, net: FakeNet, addresses: list[str]
    ) -> None:
        """One private address among those a public name resolves to is enough."""
        url = "https://intranet.example/docs"
        net.addresses["intranet.example"] = addresses
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        assert net.lookups == ["intranet.example"]
        assert (net.dialled, net.requested) == ([], [])
        assert result.details == [f"Docs: {url} {NOT_CHECKED}"]

    @pytest.mark.parametrize(
        "target",
        [
            "http://169.254.169.254/latest/meta-data/",
            "https://intranet.example/",
            "https://mixed.example/",
        ],
    )
    def test_redirect_to_a_private_address_is_not_followed(
        self, tmp_path: Path, net: FakeNet, target: str
    ) -> None:
        """A public URL cannot bounce the check to a private address."""
        url = "https://example.org/docs"
        net.addresses["intranet.example"] = ["10.1.2.3"]
        net.addresses["mixed.example"] = [PUBLIC_ADDRESS, "10.1.2.3"]
        net.redirects[url] = target
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        assert (net.dialled, net.requested) == ([PUBLIC_ADDRESS], [url])
        assert skipped(result) == "not-applicable"
        assert result.details == [f"Docs: {url} {NOT_CHECKED}"]

    @pytest.mark.parametrize(
        ("status", "target"),
        [
            (301, "ftp://ftp.example.org/pub"),
            (302, "ftp://ftp.example.org/pub"),
            (303, "ftp://ftp.example.org/pub"),
            (307, "ftp://ftp.example.org/pub"),
            (308, "ftp://ftp.example.org/pub"),
            (302, "FTP://rebind.example/pub"),
            (302, "file:///etc/hosts"),
        ],
    )
    def test_redirect_that_leaves_http_is_not_followed(
        self, tmp_path: Path, net: FakeNet, status: int, target: str
    ) -> None:
        """Only http and https connections go to the address that was tested.

        urllib's ftp handler looks the host up again before it connects, so
        a name could answer with a public address first and a private one
        after. No redirect gets that far, whatever its status: its host is
        not even looked up.
        """
        url = "https://example.org/docs"
        net.rebinding.add("rebind.example")
        net.redirect_status = status
        net.redirects[url] = target
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        assert net.lookups == ["example.org"]
        assert (net.dialled, net.requested) == ([PUBLIC_ADDRESS], [url])
        assert result.status == CheckStatus.NOTE
        assert codes(result) == ["urls.unverified"]
        assert result.details == [
            f"Docs: {url} (could not verify: "
            "redirects to a URL that is not http or https)"
        ]

    def test_redirects_that_leave_http_are_not_a_network_failure(
        self, tmp_path: Path, net: FakeNet
    ) -> None:
        """A host that answered with a redirect has answered."""
        urls = {"Home": "http://a.example/", "Docs": "http://b.example/"}
        for url in urls.values():
            net.redirects[url] = "ftp://ftp.example.org/pub"
        result = URLsCheck().run(make_project(tmp_path, urls), {})
        assert result.status == CheckStatus.NOTE
        assert result.message == "2 of 2 URL(s) could not be verified"

    @pytest.mark.parametrize("scheme", ["http", "https"])
    @pytest.mark.parametrize("redirected", [False, True])
    def test_address_that_passed_the_test_is_the_one_connected_to(
        self, tmp_path: Path, net: FakeNet, scheme: str, redirected: bool
    ) -> None:
        """A name server cannot give a public address first and a private one after.

        The name is looked up once for each connection, and the connection
        goes to the address that lookup gave. The name is still what the
        request and the certificate check use.
        """
        target = f"{scheme}://rebind.example/docs"
        net.rebinding.add("rebind.example")
        url = "http://example.org/" if redirected else target
        net.redirects["http://example.org/"] = target
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        hops = ["example.org", "rebind.example"] if redirected else ["rebind.example"]
        assert net.lookups == hops
        assert net.dialled == [PUBLIC_ADDRESS] * len(hops)
        assert net.requested == ([url, target] if redirected else [target])
        assert net.verified == (["rebind.example"] if scheme == "https" else [])
        assert result.status == CheckStatus.OK

    def test_each_connection_tests_its_own_lookup(
        self, tmp_path: Path, net: FakeNet
    ) -> None:
        """A second connection to a name that has turned private is refused."""
        urls = {"Home": "http://rebind.example/", "Docs": "http://rebind.example/docs"}
        net.rebinding.add("rebind.example")
        result = URLsCheck().run(make_project(tmp_path, urls), {})
        assert net.lookups == ["rebind.example", "rebind.example"]
        assert net.dialled == [PUBLIC_ADDRESS]
        assert net.requested == ["http://rebind.example/"]
        assert result.details == [
            "Home: http://rebind.example/ (OK)",
            f"Docs: http://rebind.example/docs {NOT_CHECKED}",
        ]

    @pytest.mark.parametrize(("value", "requested"), [("1", True), ("0", False)])
    def test_environment_variable_turns_the_rule_off(
        self,
        tmp_path: Path,
        net: FakeNet,
        monkeypatch: pytest.MonkeyPatch,
        value: str,
        requested: bool,
    ) -> None:
        """PYCMDCHECK_ALLOW_PRIVATE_URLS=1, read when the check runs, allows them."""
        url = "http://127.0.0.1:8000/"
        monkeypatch.setenv(ALLOW_PRIVATE, value)
        result = URLsCheck().run(make_project(tmp_path, {"Home": url}), {})
        assert net.requested == ([url] if requested else [])
        assert (result.status == CheckStatus.OK) is requested

    def test_name_that_does_not_resolve_is_handled_as_before(
        self, tmp_path: Path, net: FakeNet
    ) -> None:
        """An unknown host is not private, and is reported as unreachable."""
        url = "https://gone.example/page"
        net.addresses["gone.example"] = socket.gaierror(socket.EAI_NONAME, "no name")
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        assert net.lookups == ["gone.example"]
        assert result.status == CheckStatus.WARNING
        assert codes(result) == ["urls.unreachable"]
        assert result.details == [f"Docs: {url} (FAILED: host name not found)"]

    @pytest.mark.parametrize("public", [True, False])
    def test_private_urls_are_listed_and_not_counted(
        self, tmp_path: Path, net: FakeNet, public: bool
    ) -> None:
        """A private URL is neither broken nor unverified, nor a network failure."""
        urls = {"Dev": "http://localhost:8000/", "Lab": "http://10.0.0.5/"}
        expected = [f"{label}: {url} {NOT_CHECKED}" for label, url in urls.items()]
        if public:
            urls = {"Home": "https://example.org/", **urls}
            expected.insert(0, "Home: https://example.org/ (OK)")
        result = URLsCheck().run(make_project(tmp_path, urls), {})
        if public:
            assert result.status == CheckStatus.OK
            assert result.message == "All 1 project URL(s) reachable"
        else:
            assert skipped(result) == "not-applicable"
            assert result.message == "No public project URLs to check"
        assert result.details == expected

    @pytest.mark.parametrize(
        "url",
        [
            "http://%31%32%37.0.0.1/",
            "http://%6c%6f%63%61%6c%68%6f%73%74:8000/",
            "http://localhost./",
            "http://127.1/",
            "http://0x7f.0.0.1/",
            "http://2130706433/",
            "http://[::ffff:127.0.0.1]/",
            "http://example.org%2F.evil.example/",
        ],
    )
    def test_no_spelling_of_a_local_host_is_dialled(
        self, tmp_path: Path, net: FakeNet, url: str
    ) -> None:
        """The host tested is the host urllib connects to, percent escapes decoded."""
        net.addresses.update(RESOLVER_ANSWERS)
        result = URLsCheck().run(make_project(tmp_path, {"Home": url}), {})
        assert net.dialled == []
        assert result.details == [f"Home: {url} {NOT_CHECKED}"]

    @pytest.mark.parametrize(
        "target",
        [
            "http://%31%30.0.0.5/admin",
            "http://%6c%6f%63%61%6c%68%6f%73%74/",
            "http://:8000/",
        ],
    )
    def test_redirect_to_a_disguised_private_host_is_not_dialled(
        self, tmp_path: Path, net: FakeNet, target: str
    ) -> None:
        """Escapes or a missing host do not hide a target."""
        url = "http://example.org/docs"
        net.redirects[url] = target
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        assert (net.dialled, net.requested) == ([PUBLIC_ADDRESS], [url])
        assert result.details == [f"Docs: {url} {NOT_CHECKED}"]

    def test_public_host_is_dialled(self, tmp_path: Path, net: FakeNet) -> None:
        """A public host, and the public host it redirects to, are connected to."""
        # The host is example.org with one letter written as a percent escape.
        url = "http://%65xample.org/docs"
        net.redirects["http://example.org/docs"] = "http://docs.example.org/"
        result = URLsCheck().run(make_project(tmp_path, {"Docs": url}), {})
        assert net.lookups == ["example.org", "docs.example.org"]
        assert net.dialled == [PUBLIC_ADDRESS, PUBLIC_ADDRESS]
        assert result.status == CheckStatus.OK

    def test_next_address_is_tried_when_one_refuses(
        self, tmp_path: Path, net: FakeNet
    ) -> None:
        """A name with two public addresses is reached through the one that accepts."""
        url = "http://example.org/"
        first = "93.184.216.35"
        net.addresses["example.org"] = [first, PUBLIC_ADDRESS]
        net.refusing.add(first)
        result = URLsCheck().run(make_project(tmp_path, {"Home": url}), {})
        assert net.lookups == ["example.org"]
        assert net.dialled == [first, PUBLIC_ADDRESS]
        assert (net.requested, result.status) == ([url], CheckStatus.OK)

    @pytest.mark.parametrize(
        ("variable", "proxy", "scheme", "asked"),
        [
            # The proxy is sent the URL over TLS.
            ("http_proxy", "https://10.0.0.1:3128", "http", []),
            # The proxy passes a TLS connection on to the host.
            (
                "https_proxy",
                "http://10.0.0.1:3128",
                "https",
                ["CONNECT example.org:443"],
            ),
        ],
    )
    def test_proxy_on_a_private_address_does_not_make_a_url_private(
        self,
        tmp_path: Path,
        net: FakeNet,
        monkeypatch: pytest.MonkeyPatch,
        variable: str,
        proxy: str,
        scheme: str,
        asked: list[str],
    ) -> None:
        """The rule is about the host of the URL, also once a proxy is set for it.

        The proxy looks the name up and connects, so the check can only test
        the name and hand the request over.
        """
        monkeypatch.setenv(variable, proxy)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("NO_PROXY", raising=False)
        # The opener reads the proxy settings when it is built.
        opener = urls_module._opener.__wrapped__()
        monkeypatch.setattr(urls_module, "_opener", lambda: opener)
        public, private = f"{scheme}://example.org/", f"{scheme}://intranet.example/"
        local = f"{scheme}://localhost/"
        net.addresses["intranet.example"] = ["10.1.2.3"]
        urls = {"Home": public, "Lab": private, "Dev": local}
        result = URLsCheck().run(make_project(tmp_path, urls), {})
        assert net.dialled == ["10.0.0.1"]
        assert net.requested == [*asked, public]
        assert result.status == CheckStatus.OK
        assert result.details == [
            f"Home: {public} (OK)",
            f"Lab: {private} {NOT_CHECKED}",
            f"Dev: {local} {NOT_CHECKED}",
        ]
