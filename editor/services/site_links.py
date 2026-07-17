from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from html.parser import HTMLParser
import re
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from django.core.cache import cache
from django.core.exceptions import ValidationError

from editor.config import SiteConfig
from editor.navigation import normalize_page_url


MAX_HOMEPAGE_BYTES = 2 * 1024 * 1024
SUGGESTION_CACHE_SECONDS = 300
_SPACE = re.compile(r"\s+")
_GROUP_CLASSES = {"menubox", "dropdown", "menu-item-has-children", "has-submenu"}
_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr",
}


class SiteLinkError(Exception):
    pass


@dataclass(frozen=True)
class LinkSuggestion:
    label: str
    url: str

    def as_json(self) -> dict[str, str]:
        return asdict(self)


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None = None):
        self.tag = tag
        self.attrs = attrs
        self.parent = parent
        self.children: list[_Node | str] = []


class _TreeParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag.lower(), {key.lower(): value or "" for key, value in attrs}, self.stack[-1])
        self.stack[-1].children.append(node)
        if node.tag not in _VOID_TAGS:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack[-1].tag == tag.lower():
            self.stack.pop()

    def handle_endtag(self, tag):
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def _descendants(node: _Node):
    for child in node.children:
        if isinstance(child, _Node):
            yield child
            yield from _descendants(child)


def _anchors(node: _Node) -> list[_Node]:
    return [item for item in _descendants(node) if item.tag == "a"]


def _text(node: _Node) -> str:
    parts: list[str] = []
    for child in node.children:
        if isinstance(child, str):
            parts.append(child)
        elif child.tag not in {"script", "style", "noscript"}:
            parts.append(_text(child))
    visible = _SPACE.sub(" ", " ".join(parts)).strip()
    if visible:
        return visible
    for candidate in (node.attrs.get("aria-label"), node.attrs.get("title")):
        if candidate and candidate.strip():
            return _SPACE.sub(" ", candidate).strip()
    for child in _descendants(node):
        for candidate in (child.attrs.get("aria-label"), child.attrs.get("title"), child.attrs.get("alt")):
            if candidate and candidate.strip():
                return _SPACE.sub(" ", candidate).strip()
    return ""


def _group_for(anchor: _Node) -> _Node | None:
    ancestor = anchor.parent
    while ancestor and ancestor.tag != "document":
        classes = {item.casefold() for item in ancestor.attrs.get("class", "").split()}
        if ancestor.tag in {"li", "details"} or classes.intersection(_GROUP_CLASSES):
            if len(_anchors(ancestor)) > 1:
                return ancestor
        ancestor = ancestor.parent
    return None


def _normalized_href(href: str, homepage_url: str, allowed_hosts: frozenset[str]) -> str | None:
    href = href.strip()
    if not href or href.startswith("#") or href.lower().startswith(("javascript:", "mailto:", "tel:")):
        return None
    # The legacy Jerozolima carousel contains a query without its leading question mark.
    if re.match(r"^[\w%.-]+=", href) and not href.startswith(("/", "?")):
        href = f"?{href}"
    absolute = urljoin(homepage_url, href)
    try:
        return normalize_page_url(absolute, allowed_hosts)
    except ValidationError:
        return None


def extract_link_suggestions(
    html: str,
    homepage_url: str,
    allowed_hosts: frozenset[str],
) -> list[LinkSuggestion]:
    parser = _TreeParser()
    parser.feed(html)
    parser.close()
    try:
        normalized_homepage = normalize_page_url(homepage_url, allowed_hosts)
    except ValidationError as exc:
        raise SiteLinkError("Skonfigurowany adres strony głównej jest nieprawidłowy.") from exc
    suggestions = [LinkSuggestion(label="Strona główna", url=normalized_homepage)]
    seen_urls = {normalized_homepage}

    for anchor in _anchors(parser.root):
        url = _normalized_href(anchor.attrs.get("href", ""), homepage_url, allowed_hosts)
        if not url or url in seen_urls:
            continue
        label = _text(anchor)
        if not label:
            continue
        label = label[:160]
        group = _group_for(anchor)
        if group:
            group_anchors = _anchors(group)
            parent_anchor = group_anchors[0] if group_anchors else None
            if parent_anchor is not None and parent_anchor is not anchor:
                parent_label = _text(parent_anchor)[:80]
                if parent_label and parent_label.casefold() != label.casefold():
                    label = f"{parent_label} → {label}"[:240]
        seen_urls.add(url)
        suggestions.append(LinkSuggestion(label=label, url=url))
        if len(suggestions) >= 150:
            break
    return suggestions


class _SameSiteRedirectHandler(HTTPRedirectHandler):
    def __init__(self, allowed_hosts: frozenset[str]):
        self.allowed_hosts = allowed_hosts

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if (urlsplit(newurl).hostname or "").lower() not in self.allowed_hosts:
            raise SiteLinkError("Strona główna przekierowała poza skonfigurowaną domenę.")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_homepage(config: SiteConfig) -> str:
    request = Request(
        config.homepage_url,
        headers={"User-Agent": "PHPVibe link suggestions/1.0", "Accept": "text/html,application/xhtml+xml"},
    )
    opener = build_opener(_SameSiteRedirectHandler(config.allowed_hosts))
    try:
        with opener.open(request, timeout=8) as response:
            final_host = (urlsplit(response.geturl()).hostname or "").lower()
            if final_host not in config.allowed_hosts:
                raise SiteLinkError("Odpowiedź strony głównej pochodzi z nieskonfigurowanej domeny.")
            content = response.read(MAX_HOMEPAGE_BYTES + 1)
            if len(content) > MAX_HOMEPAGE_BYTES:
                raise SiteLinkError("Strona główna jest zbyt duża, aby utworzyć podpowiedzi.")
            charset = response.headers.get_content_charset() or "utf-8"
    except SiteLinkError:
        raise
    except (HTTPError, URLError, OSError, ValueError) as exc:
        raise SiteLinkError(f"Nie udało się pobrać strony głównej: {exc}") from exc
    return content.decode(charset, errors="replace")


def get_site_link_suggestions(config: SiteConfig) -> list[LinkSuggestion]:
    version = sha256(config.homepage_url.encode()).hexdigest()[:16]
    cache_key = f"phpvibe:site-links:{config.key}:{version}"
    cached = cache.get(cache_key)
    if cached is not None:
        return [LinkSuggestion(**item) for item in cached]
    suggestions = extract_link_suggestions(_download_homepage(config), config.homepage_url, config.allowed_hosts)
    cache.set(cache_key, [item.as_json() for item in suggestions], SUGGESTION_CACHE_SECONDS)
    return suggestions
