"""Download one of a task's own files through the real proxy on the running
compose stack, as the organiser who made it, and not as anyone else.

The organiser creates an org, a contest and a task, replaces the input of
the task's test from its Tests card with bytes that are not text and past
what the page saves inline (256 KiB), so the file goes into the forge's
large-file store through the upload door, writes the task's statement and
opens the contest. The page's
own read of the file names the version it was read at, and the download
address is the one the organiser's page builds from it,
`/-/downloads/<org>/<contest>/<task>/versions/<commit>/<path>`. Through the
proxy, with the organiser's session, it answers the bytes as uploaded, not
the pointer the repository holds, as an attachment named after the file,
typed as bytes, kept by no cache, never sniffed and sandboxed, with nothing
of Forgejo's own headers; a typed file, the task's `task.yaml`, comes back
as its read holds it. An approved entrant of the contest, and someone with
no session, get no such file.

It runs against a stack that is already up with the `app` profile, and is
skipped unless `UNICON_E2E_URL` names the app; see `stack.py`.
"""

from __future__ import annotations

import base64
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from playwright.sync_api import APIResponse, Browser, Page, ViewportSize

from tests.e2e.stack import (
    APP,
    STATEMENT,
    approve,
    contest_settings,
    create,
    create_account,
    create_org,
    local,
    needs_stack,
    open_contest,
    register,
    replace_test,
    save_task,
    sign_in,
    submission_rate,
    write_statement,
)

pytestmark = [needs_stack]

# Every byte value, after a line a test's input could hold: a file that is
# not text, which the download must carry unchanged, and long enough that
# the page sends it through the upload door rather than inline.
INPUT = b"2 1\r\n" + bytes(range(256)) * 1100
FILE = "tests/main/1/input"
# What Forgejo's media endpoint answers with of its own that the door keeps
# from the browser: its cookie, and the names it gives the object and the
# headers its own pages' scripts read.
FORGES_OWN = (
    "set-cookie",
    "access-control-expose-headers",
    "x-forgejo-object-type",
    "x-gitea-object-type",
)


def _read(page: Page, org: str, path: str) -> dict[str, Any]:
    """The organiser's page's own read of one of the task's files."""
    read = page.request.get(
        f"{APP}/api/v1/orgs/{org}/contests/spring/tasks/sum/files/{path}"
    )
    assert read.status == 200, read.text()
    found: dict[str, Any] = read.json()
    assert isinstance(found["version"], str), found
    return found


def _address(org: str, version: str, path: str) -> str:
    """Where the organiser's page sends the browser for the file, each part
    encoded on its own (`taskDownloadHref` in the frontend)."""
    parts = [org, "spring", "sum", "versions", version, *path.split("/")]
    return f"{APP}/-/downloads/" + "/".join(quote(part, safe="") for part in parts)


def _download(page: Page, url: str) -> APIResponse:
    """`url` asked with the page's own cookies, as the browser asks it when
    the organiser follows the link, and nothing followed after it."""
    return page.request.get(url, max_redirects=0)


def _bytes_of(read: dict[str, Any]) -> bytes:
    if read["encoding"] == "base64":
        return base64.b64decode(read["content"])
    return str(read["content"]).encode()


def _headers(response: APIResponse, name: str) -> list[str]:
    return [h["value"] for h in response.headers_array if h["name"].lower() == name]


def test_an_organiser_downloads_a_tasks_file_and_an_entrant_does_not(
    browser: Browser,
) -> None:
    stamp = secrets.token_hex(3)
    organiser_name, entrant_name = f"e2e-o-{stamp}", f"e2e-c-{stamp}"
    org = f"e2e-org-{stamp}"
    organiser_password = create_account(organiser_name)
    entrant_password = create_account(entrant_name)

    viewport: ViewportSize = {"width": 1280, "height": 900}
    organiser = browser.new_context(viewport=viewport).new_page()
    entrant = browser.new_context(viewport=viewport).new_page()
    stranger = browser.new_context(viewport=viewport).new_page()
    for page in (organiser, entrant, stranger):
        page.set_default_timeout(30_000)

    sign_in(organiser, organiser_name, organiser_password)
    create_org(organiser, org)
    create(organiser, "spring", "Create contest", "New contest")
    create(organiser, "sum", "Create task", "New task")
    task_page = organiser.url
    submission_rate(organiser, 10, 60)
    save_task(organiser)
    organiser.goto(f"{task_page}?card=tests")
    replace_test(organiser, "main", "1", {"input": INPUT})
    save_task(organiser)

    stored = _read(organiser, org, FILE)
    assert stored["upload"] is not None, stored
    assert stored["upload"]["size"] == len(INPUT)
    url = _address(org, stored["version"], FILE)

    got = _download(organiser, url)

    assert got.status == 200, got.text()
    assert got.body() == INPUT
    assert _headers(got, "content-type") == ["application/octet-stream"]
    assert _headers(got, "content-disposition") == ['attachment; filename="input"']
    assert _headers(got, "cache-control") == ["private, no-store"]
    assert _headers(got, "x-content-type-options") == ["nosniff"]
    assert _headers(got, "x-frame-options") == ["DENY"]
    assert _headers(got, "content-security-policy") == ["default-src 'none'; sandbox"]
    assert _headers(got, "referrer-policy") == ["same-origin"]
    for forges_own in FORGES_OWN:
        assert _headers(got, forges_own) == [], forges_own

    typed = _read(organiser, org, "task.yaml")
    assert typed["upload"] is None, typed
    got = _download(organiser, _address(org, typed["version"], "task.yaml"))
    assert got.status == 200, got.text()
    assert got.body() == _bytes_of(typed)
    assert _headers(got, "content-disposition") == ['attachment; filename="task.yaml"']

    write_statement(organiser, task_page, STATEMENT)
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    contest_settings(
        organiser,
        org,
        "spring",
        Name=f"Spring {stamp}",
        Start=local(now - timedelta(hours=1)),
        End=local(now + timedelta(hours=3)),
    )
    open_contest(organiser, org, "spring")
    sign_in(entrant, entrant_name, entrant_password)
    register(entrant, org, "spring")
    approve(organiser, org, "spring", entrant_name)

    for outsider in (entrant, stranger):
        refused = _download(outsider, url)
        assert refused.status == 404, refused.status
        assert INPUT not in refused.body()
