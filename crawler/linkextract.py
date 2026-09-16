"""
Cheap `<a href>` extraction for optional frontier growth (crawler-
spec.md #3). Deliberately not a general HTML parser: no DOM, no
attribute validation beyond what's needed to grab an href value.
`html.parser.HTMLParser` is the stdlib's SAX-style tokenizer -- exactly
enough for "give me every href on this page" without pulling in
BeautifulSoup/lxml for a fetcher, not a scraper (see crawler-spec.md's
"Explicitly out of scope").
"""
from html.parser import HTMLParser
from urllib.parse import urljoin


class _LinkExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag != "a":
            return
        for name, value in attrs:
            if name == "href" and value:
                self.hrefs.append(value)


def extract_links(base_url: str, html_text: str) -> list[str]:
    """Returns absolute URLs for every <a href> found, resolved against
    base_url. Malformed HTML is tolerated (HTMLParser is lenient by
    design); a hard parse error just yields whatever was found before
    the error, never raises -- link discovery is a bonus, not
    something that should crash a worker fetching legitimate content.
    """
    parser = _LinkExtractor()
    try:
        parser.feed(html_text)
    except Exception:
        pass
    return [urljoin(base_url, href) for href in parser.hrefs]
