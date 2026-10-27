"""Safe fetching and provenance for editor-requested web research."""

from __future__ import annotations

import hashlib
import ipaddress
import re
import socket
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit, urlunsplit
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
from urllib.robotparser import RobotFileParser

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..models import ExternalReference, StoryCluster

RESEARCH_PROVIDER = "research:v1"
USER_AGENT = "ChannelPressResearch/1.0 (+editorial context; honours robots.txt)"
MAX_URL_LENGTH = 2000
MAX_BYTES = 2_000_000
MAX_ROBOTS_BYTES = 64_000
MAX_REDIRECTS = 3
TIMEOUT_SECONDS = 8.0
MAX_PER_STORY = 5
_REDIRECT_CODES = {301, 302, 303, 307, 308}
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt",
    "figcaption", "figure", "footer", "h1", "h2", "h3", "h4", "h5", "h6", "header",
    "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "td", "th",
    "tr", "ul",
}
_SKIP_TAGS = {"script", "style", "noscript", "template", "svg", "iframe"}


class ResearchError(Exception):
    """A URL was rejected or could not be fetched within the safety bounds."""


@dataclass
class HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes
    url: str = ""

    def header(self, name: str) -> str:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return ""


@dataclass
class ResearchOutcome:
    url: str
    status: int
    title: str
    text: str
    excerpt: str
    content_sha256: str
    terms_note: str


Transport = Callable[..., HttpResponse]
Resolver = Callable[[str], list[str]]


def _default_resolver(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise ResearchError(f"Host could not be resolved: {host}") from exc
    return sorted({info[4][0] for info in infos})


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        # Returning None makes urllib raise HTTPError for the 3xx response, so
        # this module can validate every redirect target itself.
        return None


def _default_transport(url: str, *, timeout: float, max_bytes: int) -> HttpResponse:
    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.1",
        },
        method="GET",
    )
    opener = build_opener(_NoRedirect())
    try:
        with opener.open(request, timeout=timeout) as response:
            body = response.read(max_bytes + 1)
            return HttpResponse(
                status=response.status,
                headers=dict(response.headers),
                body=body,
                url=response.geturl(),
            )
    except HTTPError as exc:
        try:
            body = exc.read(max_bytes + 1)
        except OSError:
            body = b""
        return HttpResponse(
            status=exc.code,
            headers=dict(exc.headers or {}),
            body=body,
            url=url,
        )
    except (URLError, OSError, ValueError) as exc:
        raise ResearchError(f"Could not fetch the page: {type(exc).__name__}") from exc


def normalise_url(raw: str) -> str:
    """Validate scheme/credentials/length and return a canonical absolute URL."""
    value = (raw or "").strip()
    if not value:
        raise ResearchError("Enter a public http(s) URL.")
    if len(value) > MAX_URL_LENGTH:
        raise ResearchError("The URL is too long.")
    parsed = urlsplit(value)
    scheme = parsed.scheme.lower()
    if scheme not in {"http", "https"}:
        raise ResearchError("Only http and https URLs are supported.")
    if parsed.username or parsed.password:
        raise ResearchError("URLs containing credentials are not allowed.")
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ResearchError("The URL has no host.")
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        raise ResearchError("Local and loopback hosts are not allowed.")
    if not host.isascii():
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError as exc:
            raise ResearchError("The URL host is not valid.") from exc
    try:
        literal = ipaddress.ip_address(host.split("%")[0])
    except ValueError:
        literal = None
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.ipv4_mapped:
            literal = literal.ipv4_mapped
        if not literal.is_global or literal.is_multicast:
            raise ResearchError("Private, loopback or reserved network addresses are not allowed.")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ResearchError("The URL port is not valid.") from exc
    if port is not None and not (1 <= port <= 65535):
        raise ResearchError("The URL port is not valid.")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    path = parsed.path or "/"
    return urlunsplit((scheme, netloc, path, parsed.query, ""))


def _assert_public_address(url: str, resolver: Resolver) -> list[str]:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").strip()
    if not host:
        raise ResearchError("The URL has no host.")
    try:
        literal = ipaddress.ip_address(host.split("%")[0])
        addresses = [str(literal)]
    except ValueError:
        addresses = resolver(host)
    if not addresses:
        raise ResearchError("Host did not resolve to any address.")
    checked: list[str] = []
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split("%")[0])
        except ValueError as exc:
            raise ResearchError("Host resolved to an invalid address.") from exc
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            address = address.ipv4_mapped
        if not address.is_global or address.is_multicast:
            raise ResearchError("Private, loopback or reserved network addresses are not allowed.")
        checked.append(str(address))
    return checked


def _robots_permission(
    url: str,
    *,
    transport: Transport,
    timeout: float,
) -> tuple[bool, str]:
    parsed = urlsplit(url)
    robots_url = urlunsplit((parsed.scheme, parsed.netloc, "/robots.txt", "", ""))
    try:
        response = transport(robots_url, timeout=timeout, max_bytes=MAX_ROBOTS_BYTES)
    except ResearchError:
        return False, "robots.txt could not be read, so the page was not fetched"
    if response.status >= 500:
        return False, "robots.txt returned a server error, so the page was not fetched"
    if response.status >= 400:
        return True, "robots.txt is absent; no site rule blocks this page"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    parser.parse(response.body[:MAX_ROBOTS_BYTES].decode("utf-8", errors="replace").splitlines())
    if parser.can_fetch(USER_AGENT, url):
        return True, "robots.txt permits this page"
    return False, "robots.txt disallows this page for automated agents"


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs) -> None:  # noqa: ANN001
        tag = tag.lower()
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
            return
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _SKIP_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
            return
        if tag in _BLOCK_TAGS:
            self.text_parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
        else:
            self.text_parts.append(data)


def _extract(html: str) -> tuple[str, str]:
    parser = _Extractor()
    try:
        parser.feed(html)
    except Exception:  # malformed markup must not fail the whole request
        pass
    text = " ".join(" ".join(parser.text_parts).split())
    title = " ".join(" ".join(parser.title_parts).split())
    if not title:
        title = text[:120]
    return title[:300], text


def fetch_document(
    url: str,
    *,
    transport: Transport | None = None,
    resolver: Resolver | None = None,
    timeout: float = TIMEOUT_SECONDS,
    max_bytes: int = MAX_BYTES,
    max_redirects: int = MAX_REDIRECTS,
) -> ResearchOutcome:
    """Fetch one public HTML page within the safety bounds."""
    transport = transport or _default_transport
    resolver = resolver or _default_resolver
    current = normalise_url(url)
    visited: set[str] = set()
    for _hop in range(max_redirects + 1):
        if current in visited:
            raise ResearchError("The redirect chain loops.")
        visited.add(current)
        _assert_public_address(current, resolver)
        allowed, reason = _robots_permission(current, transport=transport, timeout=timeout)
        if not allowed:
            raise ResearchError(reason)
        response = transport(current, timeout=timeout, max_bytes=max_bytes)
        if response.status in _REDIRECT_CODES:
            location = response.header("location")
            if not location:
                raise ResearchError("A redirect response had no target.")
            current = normalise_url(urljoin(current, location))
            continue
        if response.status >= 400:
            raise ResearchError(f"The page returned HTTP {response.status}.")
        if response.status < 200 or response.status >= 300:
            raise ResearchError(f"Unexpected HTTP {response.status}.")
        if len(response.body) > max_bytes:
            raise ResearchError("The page exceeds the research size limit.")
        content_type = response.header("content-type").lower()
        if not any(kind in content_type for kind in _HTML_TYPES):
            raise ResearchError("Only HTML pages can be used as research context.")
        charset = "utf-8"
        match = re.search(r"charset=([\w\-]+)", content_type)
        if match:
            charset = match.group(1)
        try:
            html = response.body.decode(charset, errors="replace")
        except LookupError:
            html = response.body.decode("utf-8", errors="replace")
        title, text = _extract(html)
        if len(text) < 80:
            raise ResearchError("The page has too little readable text to cite.")
        final_url = current
        digest = hashlib.sha256(response.body).hexdigest()
        terms = f"{reason}; editorial context only, not claim evidence."
        return ResearchOutcome(
            url=final_url,
            status=response.status,
            title=title,
            text=text,
            excerpt=text[:700],
            content_sha256=digest,
            terms_note=terms[:300],
        )
    raise ResearchError("Too many redirects.")


def _existing_reference(db: Session, story_id: int, outcome: ResearchOutcome) -> ExternalReference | None:
    existing = db.scalar(
        select(ExternalReference).where(
            ExternalReference.story_id == story_id,
            ExternalReference.url == outcome.url,
        )
    )
    if existing is not None:
        return existing
    if outcome.content_sha256:
        return db.scalar(
            select(ExternalReference).where(
                ExternalReference.story_id == story_id,
                ExternalReference.content_sha256 == outcome.content_sha256,
            )
        )
    return None


def research_reference(
    db: Session,
    story: StoryCluster,
    url: str,
    *,
    transport: Transport | None = None,
    resolver: Resolver | None = None,
    max_per_story: int = MAX_PER_STORY,
) -> tuple[ExternalReference, bool]:
    """Fetch *url* and store a candidate reference for *story*.

    Returns ``(reference, created)``. Duplicate URLs and identical page content
    resolve to the existing row instead of adding noise for the editor.
    """
    outcome = fetch_document(url, transport=transport, resolver=resolver)
    existing = _existing_reference(db, story.id, outcome)
    if existing is not None:
        return existing, False
    current = db.scalar(
        select(func.count())
        .select_from(ExternalReference)
        .where(
            ExternalReference.story_id == story.id,
            ExternalReference.provider.like("research:%"),
        )
    ) or 0
    if current >= max_per_story:
        raise ResearchError(
            f"This story already has {current} research candidates; accept or reject one before adding more."
        )
    reference = ExternalReference(
        story_id=story.id,
        url=outcome.url[:1000],
        publisher=urlsplit(outcome.url).hostname or "",
        title=outcome.title,
        excerpt=outcome.excerpt,
        provider=RESEARCH_PROVIDER,
        reachable=True,
        review_status="candidate",
        http_status=outcome.status,
        content_sha256=outcome.content_sha256,
        terms_note=outcome.terms_note,
    )
    db.add(reference)
    db.flush()
    return reference, True


def accept_reference(db: Session, reference: ExternalReference) -> ExternalReference:
    reference.review_status = "accepted"
    db.flush()
    return reference


def reject_reference(db: Session, reference: ExternalReference) -> ExternalReference:
    reference.review_status = "rejected"
    db.flush()
    return reference
