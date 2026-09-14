"""Reading hidden fields out of an HTML form.

Only used for the Woodpecker token dance, which has to drive two pages that
exist for browsers and have no API: Forgejo's sign-in form and its OAuth consent
screen. Nothing else in Unicon scrapes HTML.
"""

from __future__ import annotations

from html.parser import HTMLParser


class _HiddenInputCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.fields: dict[str, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "input":
            return
        attributes = dict(attrs)
        if attributes.get("type") != "hidden":
            return
        name = attributes.get("name")
        if name:
            self.fields[name] = attributes.get("value") or ""


def hidden_inputs(html: str) -> dict[str, str]:
    collector = _HiddenInputCollector()
    collector.feed(html)
    return collector.fields
