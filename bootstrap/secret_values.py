"""The random values the stack runs on, in the shape each consumer expects.

Forgejo would generate its own four secrets and write them into its app.ini,
but compose overwrites that file on every start, so Forgejo would get new ones
every restart: stored OAuth client secrets would stop decrypting and every
issued token would stop working. They are generated here instead and passed in
as environment variables.
"""

import base64
import secrets
import string

_ALPHANUMERIC = string.ascii_letters + string.digits


def password() -> str:
    """A password for a service account or a database role.

    URL-safe, because several of these end up inside connection strings and
    percent-encoding a password by hand is a step somebody will forget.
    """
    return secrets.token_urlsafe(24)


def opaque_token() -> str:
    """An opaque shared secret that is only ever compared for equality.

    Used for the Woodpecker agent secret, the Garage admin token and Forgejo's
    INTERNAL_TOKEN, which Forgejo compares literally against the header its own
    git hooks send.
    """
    return secrets.token_hex(32)


def forgejo_secret_key() -> str:
    """Forgejo's [security] SECRET_KEY: 64 alphanumeric characters.

    It encrypts what Forgejo stores in its database, including OAuth client
    secrets and two-factor seeds, so it must never change under a live
    instance.
    """
    return "".join(secrets.choice(_ALPHANUMERIC) for _ in range(64))


def forgejo_jwt_secret() -> str:
    """A Forgejo JWT secret: 32 random bytes, base64url with no padding.

    Forgejo decodes these with Go's RawURLEncoding and insists on exactly 32
    bytes, so padding would be rejected.
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).rstrip(b"=").decode()


def key_32_bytes() -> str:
    """A 32-byte key for the backend, base64url with padding.

    Padded, so that Python's base64.urlsafe_b64decode reads it back without the
    caller having to restore the padding first.
    """
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def garage_rpc_secret() -> str:
    """Garage's rpc_secret: 32 bytes as hex, which is the only form it takes."""
    return secrets.token_hex(32)
