from html.parser import HTMLParser


class _MetaTagParser(HTMLParser):
    """Collects <meta property/name="..." content="..."> tags and the <title>.
    HTMLParser is event-based. parser.feed(html) will walk through the html once and calls
    the methods below as it meets things.
    """

    def __init__(self) -> None:
        super().__init__()
        self.meta: dict[str, str] = {}
        self.title = ""
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "meta":
            key = (a.get("property") or a.get("name") or "").lower()
            content = a.get("content")
            if key and content and key not in self.meta:  # first one wins
                self.meta[key] = content
        elif tag == "title":
            self._in_title = True

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False

    def handle_data(self, data):
        if self._in_title:
            self.title += data
