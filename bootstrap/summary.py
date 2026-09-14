"""What the run did, for the last few lines it prints.

Kept apart from the steps so that no step is tempted to print a secret: this
only ever holds the name of a thing, never its value.
"""

from __future__ import annotations


class Summary:
    def __init__(self) -> None:
        self._created: list[str] = []
        self._already_present: list[str] = []

    def record(self, what: str, created: bool) -> None:
        (self._created if created else self._already_present).append(what)

    def lines(self) -> list[str]:
        lines = [
            f"created: {len(self._created)}, already present: "
            f"{len(self._already_present)}"
        ]
        lines += [f"  created  {what}" for what in self._created]
        lines += [f"  present  {what}" for what in self._already_present]
        return lines
