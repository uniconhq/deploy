"""Forgejo's mailer is on exactly when .env names a mail server and always
encrypts what it sends, and open
sign-up without one is refused, since Forgejo would then confirm nobody's
address and a contest's email pattern would let anyone in.
"""

from __future__ import annotations

import pytest

from bootstrap import main

BASE = {
    "UNICON_DB_PASSWORD": "x",
    "FORGEJO_PUBLIC_URL": "https://forge.example.org",
}


def test_the_mailer_follows_the_mail_server() -> None:
    without = dict(BASE)
    with_server = {
        **BASE,
        "MAIL_SMTP_ADDR": "smtp.example.org",
        "MAIL_ENABLED": "false",
    }

    main._derive_values(without)
    main._derive_values(with_server)

    assert without["MAIL_ENABLED"] == "false"
    assert with_server["MAIL_ENABLED"] == "true"


@pytest.mark.parametrize(
    ("port", "protocol"),
    [("465", "smtps"), ("587", "smtp+starttls"), ("25", "smtp+starttls")],
)
def test_mail_is_always_encrypted(port: str, protocol: str) -> None:
    values = {**BASE, "MAIL_SMTP_ADDR": "smtp.example.org", "MAIL_SMTP_PORT": port}

    main._derive_values(values)

    assert values["MAIL_PROTOCOL"] == protocol


def test_open_sign_up_without_a_mail_server_is_refused() -> None:
    with pytest.raises(ValueError, match="MAIL_SMTP_ADDR is empty"):
        main._derive_values({**BASE, "UNICON_FORGE_REGISTRATION_OPEN": "true"})

    main._derive_values(
        {
            **BASE,
            "UNICON_FORGE_REGISTRATION_OPEN": "true",
            "MAIL_SMTP_ADDR": "smtp.example.org",
        }
    )
    main._derive_values({**BASE, "UNICON_FORGE_REGISTRATION_OPEN": "false"})
