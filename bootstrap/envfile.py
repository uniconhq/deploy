"""Reading and writing deploy/.env.

.env.example is the template and the only list of keys. Rendering walks it line
by line, so comments, ordering and the defaults that are checked in all survive,
and a key added to the example appears in the next generated .env without any
change here.
"""

from __future__ import annotations

import re
from pathlib import Path

_ASSIGNMENT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

# Marks the keys the template does not mention. A key the template lost, or one
# a person added to .env by hand, is carried across instead of dropped: the
# service that reads it would otherwise stop working on the next bootstrap run,
# and nothing would say why.
ADDED_OUTSIDE_TEMPLATE = "# Added outside the template"


class MissingTemplate(Exception):
    """.env.example is not where it should be."""


def load(path: Path) -> dict[str, str]:
    """Read an env file into a dict. Missing file means no values, not an error."""
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return values


def render(template: Path, values: dict[str, str]) -> str:
    """Fill the template with values, leaving everything else untouched."""
    if not template.exists():
        raise MissingTemplate(f"{template} is missing; it is the list of keys")
    lines: list[str] = []
    in_template: set[str] = set()
    for line in template.read_text(encoding="utf-8").splitlines():
        match = _ASSIGNMENT.match(line)
        if match:
            in_template.add(match.group(1))
        if match and match.group(1) in values:
            lines.append(f"{match.group(1)}={values[match.group(1)]}")
        else:
            lines.append(line)
    extra = sorted(key for key in values if key not in in_template)
    if extra:
        lines += ["", ADDED_OUTSIDE_TEMPLATE]
        lines += [f"{key}={values[key]}" for key in extra]
    return "\n".join(lines) + "\n"


def defaults(template: Path) -> dict[str, str]:
    """The values checked into the template, which are never secrets."""
    if not template.exists():
        raise MissingTemplate(f"{template} is missing; it is the list of keys")
    return load(template)


def write(path: Path, template: Path, values: dict[str, str]) -> None:
    """Write .env with LF endings, readable only by the person who ran this."""
    path.write_text(render(template, values), encoding="utf-8", newline="\n")
    path.chmod(0o600)
