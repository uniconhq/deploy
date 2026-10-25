"""The proxy logs no credential a request carries in its query. Each route
whose query holds one is logged by the access log as its method and path
alone, through the map `$logged_request`, and is reached through a location
whose error log takes only what is critical, since nginx's error log names
the request line, query and all, when an upstream fails. Read from the
config as nginx reads it.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

CONFIG = (Path(__file__).resolve().parent.parent / "proxy" / "nginx.conf").read_text()
CRITICAL_ONLY = "error_log /var/log/nginx/error.log crit;"

# Each route, a request to it, and the location nginx picks for it.
ROUTES = [
    ("/api/v1/auth/callback", "location = /api/v1/auth/callback"),
    ("/unicon-results/runs/1/log", "location ^~ /unicon-results/"),
    ("/forgejo-lfs/lfs/ab/cd", "location ^~ /forgejo-lfs/"),
    ("/login/oauth/authorize", "location ^~ /login/oauth/"),
    ("/user/activate", "location ^~ /user/activate"),
    ("/user/recover_account", "location ^~ /user/recover_account"),
]


def logged_without_query(path: str) -> bool:
    """Whether `$logged_request` gives `path` as its method and path."""
    block = CONFIG[CONFIG.index("map $uri $logged_request {") :]
    block = block[: block.index("\n}")]
    for line in block.splitlines()[1:]:
        found = re.match(r'\s+(\S+)\s+"\$request_method \$uri";', line)
        if found is None:
            continue
        key = found.group(1)
        if (key.startswith("~") and re.match(key[1:], path)) or key == path:
            return True
    return False


def location(header: str) -> str:
    start = CONFIG.index(header + " {")
    return CONFIG[start : CONFIG.index("}", start)]


@pytest.mark.parametrize(("path", "header"), ROUTES)
def test_a_credential_in_a_query_reaches_no_log(path: str, header: str) -> None:
    assert logged_without_query(path), path
    assert CRITICAL_ONLY in location(header), header


def test_the_rest_of_the_api_logs_its_errors_in_full() -> None:
    assert "    location /api/ {" in CONFIG
    assert CRITICAL_ONLY not in location("    location /api/")
