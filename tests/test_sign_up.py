"""SIGN_UP_OPEN is the one switch for people making their own accounts, and
UNICON_SESSION_HARD_TTL the one length of a session: bootstrap derives
Forgejo's settings and the backend's from them, so neither pair can disagree.
It also refuses a cap on live streams the proxy would not start on.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bootstrap import main
from bootstrap.images import Manifest

DIGEST = "@sha256:" + "0" * 64
MANIFEST = Manifest(
    path=Path("images.json"),
    images={
        name: f"ghcr.io/uniconhq/{name}{DIGEST}"
        for name in ("harness", "clone", "socket-filter")
    },
    primitives=(),
)
BASE = {
    "UNICON_DB_PASSWORD": "x",
    "FORGEJO_PUBLIC_URL": "https://forge.example.org",
    "UNICON_SESSION_HARD_TTL": "2592000",
    "MAIL_SMTP_ADDR": "smtp.example.org",
}


@pytest.mark.parametrize(
    ("switch", "development", "is_open"),
    [
        ("", False, False),
        ("", True, True),
        ("true", False, True),
        ("false", False, False),
        ("false", True, False),
    ],
)
def test_both_switches_follow_the_one(
    switch: str, development: bool, is_open: bool
) -> None:
    values = {**BASE, "SIGN_UP_OPEN": switch}

    main._derive_values(values, MANIFEST, development=development)

    assert values["UNICON_FORGE_REGISTRATION_OPEN"] == str(is_open).lower()
    assert values["FORGEJO_DISABLE_REGISTRATION"] == str(not is_open).lower()


def test_a_derived_switch_set_by_hand_is_overwritten() -> None:
    values = {
        **BASE,
        "SIGN_UP_OPEN": "false",
        "UNICON_FORGE_REGISTRATION_OPEN": "true",
        "FORGEJO_DISABLE_REGISTRATION": "false",
    }

    main._derive_values(values, MANIFEST, development=False)

    assert values["UNICON_FORGE_REGISTRATION_OPEN"] == "false"
    assert values["FORGEJO_DISABLE_REGISTRATION"] == "true"


def test_a_switch_that_is_not_true_or_false_is_refused() -> None:
    with pytest.raises(ValueError, match="SIGN_UP_OPEN is 'yes'"):
        main._derive_values(
            {**BASE, "SIGN_UP_OPEN": "yes"}, MANIFEST, development=False
        )


@pytest.mark.parametrize(
    ("seconds", "hours"),
    [("2592000", "720"), ("3600", "1"), ("3601", "2"), ("60", "1")],
)
def test_the_refresh_token_outlives_the_session(seconds: str, hours: str) -> None:
    values = {**BASE, "UNICON_SESSION_HARD_TTL": seconds}

    main._derive_values(values, MANIFEST, development=False)

    assert values["FORGEJO_REFRESH_TOKEN_HOURS"] == hours


@pytest.mark.parametrize("seconds", ["", "0", "-5", "30d"])
def test_a_session_length_that_is_not_seconds_is_refused(seconds: str) -> None:
    with pytest.raises(ValueError, match="UNICON_SESSION_HARD_TTL"):
        main._derive_values(
            {**BASE, "UNICON_SESSION_HARD_TTL": seconds}, MANIFEST, development=False
        )


@pytest.mark.parametrize(
    "key", ["UNICON_LIVE_STREAMS", "UNICON_LIVE_STREAMS_PER_ADDRESS"]
)
@pytest.mark.parametrize("cap", ["0", "abc", "-1", "65536", "1.5"])
def test_a_cap_on_live_streams_nginx_would_not_start_on_is_refused(
    key: str, cap: str
) -> None:
    with pytest.raises(ValueError, match=key):
        main._derive_values({**BASE, key: cap}, MANIFEST, development=False)


@pytest.mark.parametrize("cap", ["", "1", "3000", "65535"])
def test_a_cap_on_live_streams_nginx_takes_passes(cap: str) -> None:
    main._derive_values(
        {**BASE, "UNICON_LIVE_STREAMS": cap, "UNICON_LIVE_STREAMS_PER_ADDRESS": cap},
        MANIFEST,
        development=False,
    )
