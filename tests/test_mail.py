"""Forgejo's mailer is on exactly when .env names a mail server and always
encrypts what it sends, and open
sign-up without one is refused, since Forgejo would then confirm nobody's
address and a contest's email pattern would let anyone in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml  # type: ignore[import-untyped]

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
    "MAIL_FROM": "Unicon <u@example.org>",
    "UNICON_DB_PASSWORD": "x",
    "FORGEJO_PUBLIC_URL": "https://forge.example.org",
    "UNICON_SESSION_HARD_TTL": "2592000",
}


def test_the_mailer_follows_the_mail_server() -> None:
    without = dict(BASE)
    with_server = {
        **BASE,
        "MAIL_SMTP_ADDR": "smtp.example.org",
        "MAIL_ENABLED": "false",
    }

    main._derive_values(without, MANIFEST, development=False)
    main._derive_values(with_server, MANIFEST, development=False)

    assert without["MAIL_ENABLED"] == "false"
    assert with_server["MAIL_ENABLED"] == "true"


@pytest.mark.parametrize(
    ("port", "protocol"),
    [("465", "smtps"), ("587", "smtp+starttls"), ("25", "smtp+starttls")],
)
def test_mail_is_always_encrypted(port: str, protocol: str) -> None:
    values = {**BASE, "MAIL_SMTP_ADDR": "smtp.example.org", "MAIL_SMTP_PORT": port}

    main._derive_values(values, MANIFEST, development=False)

    assert values["MAIL_PROTOCOL"] == protocol


def test_open_sign_up_without_a_mail_server_is_refused() -> None:
    with pytest.raises(ValueError, match="MAIL_SMTP_ADDR is empty"):
        main._derive_values(
            {**BASE, "SIGN_UP_OPEN": "true"}, MANIFEST, development=False
        )

    main._derive_values(
        {**BASE, "SIGN_UP_OPEN": "true", "MAIL_SMTP_ADDR": "smtp.example.org"},
        MANIFEST,
        development=False,
    )
    main._derive_values({**BASE, "SIGN_UP_OPEN": "false"}, MANIFEST, development=False)


def test_a_development_stack_signs_up_through_mailpit() -> None:
    values = {**BASE, "SIGN_UP_OPEN": "true"}

    main._derive_values(values, MANIFEST, development=True)

    assert values["FORGEJO_DISABLE_REGISTRATION"] == "false"


def test_the_backend_mails_through_the_server_forgejo_does() -> None:
    compose = yaml.safe_load(Path("compose.yaml").read_text(encoding="utf-8"))
    backend = compose["services"]["backend"]["environment"]
    forgejo = compose["services"]["forgejo"]["environment"]

    pairs = {
        "UNICON_MAIL_SMTP_ADDR": "FORGEJO__mailer__SMTP_ADDR",
        "UNICON_MAIL_SMTP_PORT": "FORGEJO__mailer__SMTP_PORT",
        "UNICON_MAIL_PROTOCOL": "FORGEJO__mailer__PROTOCOL",
        "UNICON_MAIL_SMTP_USER": "FORGEJO__mailer__USER",
        "UNICON_MAIL_SMTP_PASSWORD": "FORGEJO__mailer__PASSWD",
        "UNICON_MAIL_FROM": "FORGEJO__mailer__FROM",
    }
    for ours, theirs in pairs.items():
        assert backend[ours] == forgejo[theirs], ours


def test_a_mail_server_needs_a_sender_and_a_port() -> None:
    main.refuse_mail_without_sender({})
    main.refuse_mail_without_sender(
        {"MAIL_SMTP_ADDR": "smtp.example.org", "MAIL_FROM": "u@x.org"}
    )
    with pytest.raises(ValueError, match="MAIL_FROM is empty"):
        main.refuse_mail_without_sender(
            {"MAIL_SMTP_ADDR": "smtp.example.org", "MAIL_FROM": " "}
        )
    with pytest.raises(ValueError, match="not a port number"):
        main.refuse_mail_without_sender(
            {
                "MAIL_SMTP_ADDR": "smtp.example.org",
                "MAIL_FROM": "u@x.org",
                "MAIL_SMTP_PORT": "x",
            }
        )
