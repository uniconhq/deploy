"""The bootstrap run, in the order the pieces need each other.

Garage first, because Forgejo needs an S3 key in its environment before it
starts or it would store the first LFS objects on local disk and only move to
Garage after a restart. Forgejo next, because Woodpecker will not start without
the OAuth client that only a Forgejo administrator can create. Woodpecker last,
then the proxy, which holds no bootstrap state of its own and is started so
that one command leaves the stack answering on its public URL.

Every step checks before it creates, and .env is rewritten after each one, so an
interrupted run can be resumed by running it again.

Four Garage buckets and no other: FORGEJO_LFS_BUCKET for Forgejo, and the three
in UNICON_BUCKETS for the backend, where unicon-uploads holds what a browser
uploads before a submit, unicon-results the grading logs, and unicon-exports is
reserved.

FORGEJO_DATA_VOLUME is the Docker name of the Forgejo volume: the compose
project name from compose.yaml, then the volume name. It stands for every
volume in the check for a lost .env, because it is the one a lost .env makes
unreadable.

GENERATORS lists every key .env holds that is generated here rather than
discovered from a service. A key already carrying a value is left alone.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from bootstrap import envfile, postgres, secret_values
from bootstrap.compose import Compose, ComposeFailed
from bootstrap.forgejo import Forgejo, ForgejoError, OAuthApplication
from bootstrap.garage import Garage, GarageError
from bootstrap.readiness import NotReady, wait_for, wait_for_http
from bootstrap.summary import Summary
from bootstrap.woodpecker import Woodpecker, WoodpeckerError

BACKEND_ACCOUNT = "unicon-backend"
CI_ACCOUNT = "unicon-ci"

FORGEJO_LFS_BUCKET = "forgejo-lfs"
UNICON_BUCKETS = (
    "unicon-uploads",
    "unicon-results",
    "unicon-exports",
)

FORGEJO_DATA_VOLUME = "unicon_forgejo-data"

GENERATORS: dict[str, Callable[[], str]] = {
    "POSTGRES_SUPERUSER_PASSWORD": secret_values.password,
    "FORGEJO_DB_PASSWORD": secret_values.password,
    "WOODPECKER_DB_PASSWORD": secret_values.password,
    "UNICON_DB_PASSWORD": secret_values.password,
    "FORGEJO_SECRET_KEY": secret_values.forgejo_secret_key,
    "FORGEJO_INTERNAL_TOKEN": secret_values.opaque_token,
    "FORGEJO_OAUTH2_JWT_SECRET": secret_values.forgejo_jwt_secret,
    "FORGEJO_LFS_JWT_SECRET": secret_values.forgejo_jwt_secret,
    "FORGEJO_BACKEND_PASSWORD": secret_values.password,
    "FORGEJO_CI_PASSWORD": secret_values.password,
    "WOODPECKER_GRPC_SECRET": secret_values.opaque_token,
    "GARAGE_RPC_SECRET": secret_values.garage_rpc_secret,
    "GARAGE_ADMIN_TOKEN": secret_values.opaque_token,
    "UNICON_SESSION_SIGNING_KEY": secret_values.key_32_bytes,
    "UNICON_TOKEN_ENCRYPTION_KEY": secret_values.key_32_bytes,
}


class LostEnvironment(Exception):
    """.env is gone while the volumes it unlocks are still on the machine."""


def main(argv: list[str] | None = None) -> int:
    options = _parse_arguments(argv)
    template = options.directory / ".env.example"
    env_path = options.directory / ".env"
    compose = Compose(options.directory, options.file)

    summary = Summary()
    values: dict[str, str] = {}

    try:
        _refuse_to_regenerate_over_existing_data(compose, env_path)
        previous = envfile.load(env_path)
        values = _starting_values(template, previous)

        _generate_missing_secrets(values, summary)
        _derive_values(values)
        envfile.write(env_path, template, values)

        _start_storage(compose, values, summary)
        envfile.write(env_path, template, values)

        _start_forgejo(compose, values, summary)
        envfile.write(env_path, template, values)

        _start_woodpecker(compose, values, summary)
        compose.up("proxy")
        envfile.write(env_path, template, values)

        _restart_backend_if_values_changed(compose, previous, values)
    except (
        ComposeFailed,
        ForgejoError,
        GarageError,
        LostEnvironment,
        NotReady,
        WoodpeckerError,
        ValueError,
        OSError,
    ) as failure:
        print(f"bootstrap failed: {failure}")
        return 1

    print(f"\nwrote {env_path}")
    for line in summary.lines():
        print(line)
    print(f"\napp      {values['UNICON_PUBLIC_URL']}")
    print(f"forgejo  {values['FORGEJO_PUBLIC_URL']}")
    print(f"ci       {values['WOODPECKER_PUBLIC_URL']}")
    return 0


def _parse_arguments(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="bootstrap",
        description="Prepare a Unicon stack and write deploy/.env.",
    )
    parser.add_argument(
        "-f",
        "--file",
        action="append",
        default=None,
        help="compose file, repeatable. Default: compose.yaml and compose.dev.yaml",
    )
    parser.add_argument(
        "--directory",
        type=Path,
        default=Path.cwd(),
        help="the deploy directory. Default: the working directory",
    )
    options = parser.parse_args(argv)
    if options.file is None:
        options.file = ["compose.yaml", "compose.dev.yaml"]
    return options


def _refuse_to_regenerate_over_existing_data(compose: Compose, env_path: Path) -> None:
    """Stop rather than generate a second set of secrets for data that exists.

    Fresh secrets over a surviving volume are not a fresh start. Forgejo's
    SECRET_KEY decrypts what is already in its database and a new one cannot
    read any of it; the run would look like it succeeded and leave an instance
    whose stored OAuth secrets and two-factor seeds are gone.
    """
    if env_path.exists() or not compose.volume_exists(FORGEJO_DATA_VOLUME):
        return
    raise LostEnvironment(
        f"{env_path} is missing but the volume {FORGEJO_DATA_VOLUME} is still "
        "here. That volume holds data only the secrets in that file can read, "
        "and those secrets cannot be generated again. Either put a backed-up "
        ".env back and run this again, or remove the stack's volumes with "
        "docker compose down -v and start from empty."
    )


def _starting_values(template: Path, previous: dict[str, str]) -> dict[str, str]:
    """Template defaults, overridden by anything a previous run already wrote."""
    values = {k: v for k, v in envfile.defaults(template).items() if v}
    values.update({k: v for k, v in previous.items() if v})
    return values


def _derive_values(values: dict[str, str]) -> None:
    """Values that are a restatement of another one, recomputed every run.

    They are written with the generated secrets rather than at the end, so an
    interrupted run still leaves a .env the services can start from.

    FORGEJO_DOMAIN follows FORGEJO_PUBLIC_URL, because Forgejo puts DOMAIN in
    clone URLs and mail, and a value set twice drifts. UNICON_FORGE_PUBLIC_URL
    is a copy of the same URL, because the backend sends people to the Forgejo
    their browser uses: two keys for one URL is two chances to disagree, and
    the symptom of disagreeing is a login redirect to a host the browser cannot
    resolve.
    """
    values["UNICON_DATABASE_URL"] = (
        f"postgresql+psycopg://unicon:{values['UNICON_DB_PASSWORD']}"
        f"@postgres:5432/unicon"
    )
    public_url = values["FORGEJO_PUBLIC_URL"]
    host = urlparse(public_url).hostname
    if not host:
        raise ValueError(f"FORGEJO_PUBLIC_URL has no host: {public_url}")
    values["FORGEJO_DOMAIN"] = host
    values["UNICON_FORGE_PUBLIC_URL"] = public_url


def _generate_missing_secrets(values: dict[str, str], summary: Summary) -> None:
    for key, generate in GENERATORS.items():
        if values.get(key):
            summary.record(f"secret {key}", created=False)
            continue
        values[key] = generate()
        summary.record(f"secret {key}", created=True)


def _start_storage(compose: Compose, values: dict[str, str], summary: Summary) -> None:
    print("starting postgres and garage")
    compose.up("postgres", "garage")
    wait_for("postgres", lambda: compose.is_healthy("postgres"))
    postgres.sync_role_passwords(
        compose,
        "postgres",
        {
            "forgejo": values["FORGEJO_DB_PASSWORD"],
            "woodpecker": values["WOODPECKER_DB_PASSWORD"],
            "unicon": values["UNICON_DB_PASSWORD"],
        },
    )

    garage = Garage(compose, "garage")
    wait_for("garage", garage.is_answering)
    summary.record("garage cluster layout", garage.ensure_layout())

    bucket_ids: dict[str, str] = {}
    for name in (FORGEJO_LFS_BUCKET, *UNICON_BUCKETS):
        bucket_id, created = garage.ensure_bucket(name)
        bucket_ids[name] = bucket_id
        summary.record(f"garage bucket {name}", created)

    forgejo_key, created = garage.ensure_key("forgejo-lfs")
    summary.record("garage key forgejo-lfs", created)
    garage.allow_read_write(bucket_ids[FORGEJO_LFS_BUCKET], forgejo_key.access_key_id)
    values["FORGEJO_S3_ACCESS_KEY"] = forgejo_key.access_key_id
    values["FORGEJO_S3_SECRET_KEY"] = forgejo_key.secret_access_key

    backend_key, created = garage.ensure_key("unicon-backend")
    summary.record("garage key unicon-backend", created)
    for name in UNICON_BUCKETS:
        garage.allow_read_write(bucket_ids[name], backend_key.access_key_id)
    values["UNICON_S3_ACCESS_KEY"] = backend_key.access_key_id
    values["UNICON_S3_SECRET_KEY"] = backend_key.secret_access_key


def _start_forgejo(compose: Compose, values: dict[str, str], summary: Summary) -> None:
    print("starting forgejo")
    compose.up("forgejo")
    public_url = values["FORGEJO_PUBLIC_URL"]
    wait_for_http("forgejo", f"{public_url}/api/healthz", timeout_seconds=300.0)

    forgejo = Forgejo(public_url, compose, "forgejo")
    summary.record(
        f"forgejo account {BACKEND_ACCOUNT}",
        forgejo.ensure_user(
            BACKEND_ACCOUNT,
            values["FORGEJO_BACKEND_PASSWORD"],
            f"{BACKEND_ACCOUNT}@unicon.invalid",
            admin=True,
        ),
    )
    summary.record(
        f"forgejo account {CI_ACCOUNT}",
        forgejo.ensure_user(
            CI_ACCOUNT,
            values["FORGEJO_CI_PASSWORD"],
            f"{CI_ACCOUNT}@unicon.invalid",
            admin=False,
        ),
    )

    if forgejo.token_is_valid(values.get("UNICON_FORGE_ADMIN_TOKEN", "")):
        summary.record("forgejo provisioning token", created=False)
    else:
        values["UNICON_FORGE_ADMIN_TOKEN"] = forgejo.mint_access_token(
            BACKEND_ACCOUNT, values["FORGEJO_BACKEND_PASSWORD"]
        )
        summary.record("forgejo provisioning token", created=True)

    unicon_app, created = forgejo.ensure_oauth_application(
        BACKEND_ACCOUNT,
        values["FORGEJO_BACKEND_PASSWORD"],
        name="Unicon",
        redirect_uri=f"{values['UNICON_PUBLIC_URL']}/api/v1/auth/callback",
        known=_known_application(
            values.get("UNICON_FORGE_OAUTH_CLIENT_ID"),
            values.get("UNICON_FORGE_OAUTH_CLIENT_SECRET"),
        ),
    )
    values["UNICON_FORGE_OAUTH_CLIENT_ID"] = unicon_app.client_id
    values["UNICON_FORGE_OAUTH_CLIENT_SECRET"] = unicon_app.client_secret
    summary.record("forgejo oauth application Unicon", created)

    woodpecker_app, created = forgejo.ensure_oauth_application(
        BACKEND_ACCOUNT,
        values["FORGEJO_BACKEND_PASSWORD"],
        name="Woodpecker",
        redirect_uri=f"{values['WOODPECKER_PUBLIC_URL']}/authorize",
        known=_known_application(
            values.get("WOODPECKER_FORGEJO_CLIENT"),
            values.get("WOODPECKER_FORGEJO_SECRET"),
        ),
    )
    values["WOODPECKER_FORGEJO_CLIENT"] = woodpecker_app.client_id
    values["WOODPECKER_FORGEJO_SECRET"] = woodpecker_app.client_secret
    summary.record("forgejo oauth application Woodpecker", created)


def _start_woodpecker(
    compose: Compose, values: dict[str, str], summary: Summary
) -> None:
    print("starting woodpecker")
    compose.up("woodpecker-server")

    woodpecker = Woodpecker(
        values["WOODPECKER_PUBLIC_URL"], values["FORGEJO_PUBLIC_URL"]
    )
    wait_for("woodpecker", woodpecker.is_answering)

    if woodpecker.token_is_valid(values.get("UNICON_WOODPECKER_TOKEN", "")):
        summary.record("woodpecker api token", created=False)
        return
    values["UNICON_WOODPECKER_TOKEN"] = woodpecker.mint_token(
        CI_ACCOUNT, values["FORGEJO_CI_PASSWORD"]
    )
    summary.record("woodpecker api token", created=True)


def _restart_backend_if_values_changed(
    compose: Compose, previous: dict[str, str], values: dict[str, str]
) -> None:
    """Hand the backend the values this run changed under it.

    Compose passes the UNICON_* keys in as environment variables and a running
    container never re-reads them, so a re-minted token would sit in .env while
    the backend kept presenting the revoked one. Woodpecker needs no such step:
    everything it reads is written before this run starts it.
    """
    changed = sorted(
        key
        for key, value in values.items()
        if key.startswith("UNICON_") and previous.get(key) != value
    )
    if not changed:
        return
    if not compose.container_exists("backend"):
        print("backend is not running; it reads the new values when it starts")
        return
    compose.up("backend")
    print(f"recreated backend for: {', '.join(changed)}")


def _known_application(
    client_id: str | None, client_secret: str | None
) -> OAuthApplication | None:
    if client_id and client_secret:
        return OAuthApplication(client_id, client_secret)
    return None
