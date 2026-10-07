"""The bootstrap run, in the order the pieces need each other.

Garage first, because Forgejo needs an S3 key in its environment before it
starts or it would store the first LFS objects on local disk and only move to
Garage after a restart. Forgejo next, because Woodpecker will not start without
the OAuth client that only a Forgejo administrator can create. The proxy next,
because Forgejo's sign-in pages are reached through it, and minting the
Woodpecker token signs in there. Woodpecker last.

Every step checks before it creates, and .env is rewritten after each one, so an
interrupted run can be resumed by running it again.

After the accounts and the OAuth applications, the forge is seeded with the
platform org and what is in it: the three primitives, each version the image
manifest lists mirrored from its repository with the manifest's image digest
written in (primitives.py, images.py), and the built-in workflow
unicon/classic at each of its versions, which wire them together
(workflows.py). A task's first save has to find a workflow and its primitives
the organiser can read, or nothing can be published, so a workflow version
that uses a primitive version the manifest does not pin is refused before
anything is written. Every version goes to its own tag, and a tag already at
the forge is never edited: every version is compared with the forge before
any is seeded, and one that differs stops the run there, naming each. The
manifest also gives the harness, clone and socket filter images, which go into
.env for the services that run them.

On a development stack, the one whose compose files include compose.dev.yaml,
bootstrap also enrols the one CI agent that overlay can run and writes its
token to .env, with the group that owns the Docker socket on this machine,
which the overlay's socket filter joins, and deletes any other agent that
would take a grading run and has gone silent. Only there does --rewrite rewrite
the built-in versions in place instead of leaving them.

Two Garage buckets and no other: FORGEJO_LFS_BUCKET for Forgejo, which holds
every file a person uploads, and RESULTS_BUCKET for the backend, which holds
the grading logs. Nothing a browser sends goes into a bucket the platform
signs for: it goes through the upload door into Forgejo's own.

GENERATORS lists every key .env holds that is generated here rather than
discovered from a service. A key already carrying a value is left alone.

RETIRED lists keys an older .env may hold that nothing reads: the addresses
of the services on the compose network, the bucket names, the platform
account and the database URL, which compose.yaml writes itself, and the
settings whose value the backend works out on its own. A run drops them
rather than carrying them across, so nothing in .env looks like a setting
that is not one.

UNLOCKS names, for each generated secret that data on disk depends on, the
volume that data lives in: Forgejo's four secrets decrypt and sign what is in
forgejo-data, the backend's token key decrypts the credentials in
postgres-data, and Garage's RPC secret is what the node in garage-meta was
formed with. A run that finds one of those keys missing while its volume is
still on the machine stops, because a fresh value would not be a fresh start:
it would leave data nothing can read. The other generated values are
re-applied on every run or only invalidate what can be made again, so a
missing one is simply generated. Volume names are the compose project name
from compose.yaml, then the volume name.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

import httpx

from bootstrap import envfile, images, postgres, primitives, secret_values, workflows
from bootstrap.compose import Compose, ComposeFailed
from bootstrap.forgejo import Forgejo, ForgejoError, OAuthApplication
from bootstrap.garage import Garage, GarageError
from bootstrap.images import Manifest
from bootstrap.platform_repos import (
    PLATFORM_ORG,
    Outcome,
    PlatformRepos,
    Standing,
    publish,
    standing,
)
from bootstrap.readiness import NotReady, wait_for
from bootstrap.summary import Summary
from bootstrap.woodpecker import Woodpecker, WoodpeckerError

BACKEND_ACCOUNT = "unicon-backend"
CI_ACCOUNT = "unicon-ci"

FORGEJO_LFS_BUCKET = "forgejo-lfs"
RESULTS_BUCKET = "unicon-results"
UNICON_BUCKETS = (RESULTS_BUCKET,)

COMPOSE_PROJECT = "unicon"
DEVELOPMENT_OVERLAY = "compose.dev.yaml"
DEV_AGENT = "laptop"
GRADING_LABEL = ("pool", "platform")
SOCKET_PROBE_IMAGE = "busybox:1.37.0"
LIVE_STREAMS_CEILING = 65535
"""The most connections nginx's `limit_conn` takes for a cap."""

GRADING_SERVICES = ("socket-filter", "woodpecker-agent")

RETIRED = frozenset(
    {
        "UNICON_DATABASE_URL",
        "UNICON_FORGE_PUBLIC_URL",
        "UNICON_FORGE_INTERNAL_URL",
        "UNICON_INTERNAL_URL",
        "UNICON_FORGE_PLATFORM_ACCOUNT",
        "UNICON_WOODPECKER_URL",
        "UNICON_S3_ENDPOINT",
        "UNICON_S3_REGION",
        "UNICON_S3_UPLOADS_BUCKET",
        "UNICON_S3_RESULTS_BUCKET",
        "UNICON_COOKIE_SECURE",
        "UNICON_FORGE",
        "WOODPECKER_AGENT_SECRET",
    }
)

UNLOCKS: dict[str, str] = {
    "FORGEJO_SECRET_KEY": "forgejo-data",
    "FORGEJO_INTERNAL_TOKEN": "forgejo-data",
    "FORGEJO_OAUTH2_JWT_SECRET": "forgejo-data",
    "FORGEJO_LFS_JWT_SECRET": "forgejo-data",
    "UNICON_TOKEN_ENCRYPTION_KEY": "postgres-data",
    "GARAGE_RPC_SECRET": "garage-meta",
}

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
    """A secret is gone from .env while the volume it unlocks is still on the
    machine.
    """


class UnpinnedPrimitive(ValueError):
    """A built-in workflow version uses a primitive version the image
    manifest does not pin.
    """


class DifferingVersions(ValueError):
    """Versions at the forge differ from what is here, and a version is never
    edited without being told to.
    """


def main(argv: list[str] | None = None) -> int:
    options = _parse_arguments(argv)
    template = options.directory / ".env.example"
    env_path = options.directory / ".env"
    compose = Compose(options.directory, options.file)
    development = is_development(options.file)

    summary = Summary()
    values: dict[str, str] = {}

    try:
        refuse_rewrite_outside_development(options.rewrite, development)
        manifest = images.load(images.choose(options.directory, options.images))
        print(f"images from {manifest.path.name}")
        refuse_unpinned_primitives(options.directory, manifest)

        previous = envfile.load(env_path)
        _refuse_to_regenerate_over_existing_data(compose, env_path, previous)
        values = _starting_values(template, previous)
        retired = sorted(RETIRED & previous.keys())
        if retired:
            print(f"dropped from .env, as nothing reads them: {', '.join(retired)}")

        _generate_missing_secrets(values, summary)
        _derive_values(values, manifest, development=development)
        if development:
            values["DOCKER_SOCKET_GID"] = compose.socket_group(SOCKET_PROBE_IMAGE)
        envfile.write(env_path, template, values)

        _start_storage(compose, values, summary)
        envfile.write(env_path, template, values)

        _start_forgejo(compose, values, summary)
        envfile.write(env_path, template, values)

        _seed_platform(
            compose, options.directory, manifest, values, summary, options.rewrite
        )
        envfile.write(env_path, template, values)

        compose.up("proxy")
        _start_woodpecker(compose, values, summary)
        if development:
            _enrol_dev_agent(values, summary)
            _pull_grading_images(compose, values, summary)
        envfile.write(env_path, template, values)

        recreate_changed(compose, "backend")
        if development:
            recreate_changed(compose, *GRADING_SERVICES)
    except (
        ComposeFailed,
        envfile.MissingTemplate,
        ForgejoError,
        GarageError,
        LostEnvironment,
        NotReady,
        WoodpeckerError,
        ValueError,
        OSError,
        httpx.HTTPError,
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
    parser.add_argument(
        "--images",
        type=Path,
        default=None,
        help=(
            f"the image manifest. Default: {images.LOCAL_MANIFEST} when a local "
            f"build wrote it, otherwise {images.RELEASE_MANIFEST}"
        ),
    )
    parser.add_argument(
        "--rewrite",
        action="store_true",
        help=(
            "development stacks only: rewrite each version of the built-in "
            "workflow and of each primitive in place to what is here now, "
            "instead of stopping at a version at the forge that differs"
        ),
    )
    options = parser.parse_args(argv)
    if options.file is None:
        options.file = ["compose.yaml", "compose.dev.yaml"]
    return options


def is_development(files: list[str]) -> bool:
    """Whether the compose files are a development stack's: the dev overlay
    is one of them.
    """
    return any(Path(file).name == DEVELOPMENT_OVERLAY for file in files)


def refuse_unpinned_primitives(directory: Path, manifest: Manifest) -> None:
    """Refuse a version of the built-in workflow whose steps use a primitive
    version the manifest does not pin, since the forge would refuse every
    task saved against that workflow version.
    """
    pinned = {
        f"{PLATFORM_ORG}/{release.name}@{release.version}"
        for release in manifest.primitives
    }
    name = workflows.CLASSIC
    unpinned = [
        f"workflows/{name}/{version}/ uses {used}"
        for version, files in workflows.seed_versions(directory, name).items()
        for used in workflows.primitives_used(files[workflows.DEFINITION])
        if used not in pinned
    ]
    if unpinned:
        raise UnpinnedPrimitive(
            f"{manifest.path.name} does not pin every primitive version the "
            f"built-in workflow uses, so nothing is seeded: "
            f"{'; '.join(unpinned)}. Pin each in the manifest; on a "
            "development machine, scripts/build-images.py builds them from "
            "the sibling checkouts into images.local.json."
        )


def refuse_rewrite_outside_development(rewrite: bool, development: bool) -> None:
    """A version is frozen. Rewriting one in place is allowed on a
    development stack, where nothing published outlives a reset, and nowhere
    else, whatever the flag says.
    """
    if rewrite and not development:
        raise ValueError(
            f"--rewrite rewrites published versions and is only for a "
            f"development stack, one started with {DEVELOPMENT_OVERLAY}. A "
            "changed definition on any other stack is a new version."
        )


def _refuse_to_regenerate_over_existing_data(
    compose: Compose, env_path: Path, previous: dict[str, str]
) -> None:
    """Stop rather than generate a secret again for data that exists.

    Fresh secrets over a surviving volume are not a fresh start. Forgejo's
    SECRET_KEY decrypts what is already in its database and a new one cannot
    read any of it; the run would look like it succeeded and leave an instance
    whose stored OAuth secrets and two-factor seeds are gone. The check is per
    key, so a .env that is present but has lost one line, or is empty, is
    caught the same as a .env that is gone.
    """
    lost = [key for key in UNLOCKS if not previous.get(key)]
    if not lost:
        return
    kept: dict[str, bool] = {}
    for key in lost:
        volume = UNLOCKS[key]
        if volume not in kept:
            kept[volume] = compose.volume_exists(f"{COMPOSE_PROJECT}_{volume}")
        if kept[volume]:
            raise LostEnvironment(
                f"{key} is missing from {env_path} but the volume "
                f"{COMPOSE_PROJECT}_{volume} is still here. That volume holds "
                "data only that secret can read, and the secret cannot be "
                "generated again. Either put a backed-up .env back and run "
                "this again, or remove the stack's volumes with "
                "docker compose down -v and start from empty."
            )


def _starting_values(template: Path, previous: dict[str, str]) -> dict[str, str]:
    """Template defaults, overridden by anything a previous run already wrote,
    except the RETIRED keys, which are dropped.
    """
    values = {k: v for k, v in envfile.defaults(template).items() if v}
    values.update({k: v for k, v in previous.items() if v and k not in RETIRED})
    return values


def _derive_values(
    values: dict[str, str], manifest: Manifest, *, development: bool
) -> None:
    """Values that are a restatement of another one, recomputed every run.

    They are written with the generated secrets rather than at the end, so an
    interrupted run still leaves a .env the services can start from.

    FORGEJO_DOMAIN follows FORGEJO_PUBLIC_URL, because Forgejo puts DOMAIN in
    clone URLs and mail, and a value set twice drifts. MAIL_ENABLED follows
    MAIL_SMTP_ADDR, so Forgejo's mailer is on exactly when there is a server
    to send through, and MAIL_PROTOCOL follows MAIL_SMTP_PORT, so the mail
    is always encrypted: TLS from the start on 465, STARTTLS required on any
    other port.

    SIGN_UP_OPEN is the one switch for people making their own accounts, and
    the two settings that carry it out follow from it:
    FORGEJO_DISABLE_REGISTRATION, which makes Forgejo take or refuse a
    sign-up, and UNICON_FORGE_REGISTRATION_OPEN, which decides whether the
    landing page offers Create account. Set apart, one could say open while
    the other said closed. FORGEJO_REFRESH_TOKEN_HOURS follows
    UNICON_SESSION_HARD_TTL for the same reason: a Unicon session that
    outlives the Forgejo refresh token behind it can no longer act for the
    person, so the two are one length of time, in Forgejo's unit.

    The three images are the image manifest's, so none of them can be set to
    something that is not there.
    """
    public_url = values["FORGEJO_PUBLIC_URL"]
    host = urlparse(public_url).hostname
    if not host:
        raise ValueError(f"FORGEJO_PUBLIC_URL has no host: {public_url}")
    values["FORGEJO_DOMAIN"] = host
    values["MAIL_ENABLED"] = "true" if values.get("MAIL_SMTP_ADDR") else "false"
    values["MAIL_PROTOCOL"] = (
        "smtps" if values.get("MAIL_SMTP_PORT") == "465" else "smtp+starttls"
    )
    values["UNICON_HARNESS_IMAGE"] = manifest.harness
    values["UNICON_CLONE_IMAGE"] = manifest.clone
    values["UNICON_FILTER_IMAGE"] = manifest.socket_filter
    open_sign_up = sign_up_open(values, development=development)
    values["UNICON_FORGE_REGISTRATION_OPEN"] = "true" if open_sign_up else "false"
    values["FORGEJO_DISABLE_REGISTRATION"] = "false" if open_sign_up else "true"
    values["FORGEJO_REFRESH_TOKEN_HOURS"] = refresh_token_hours(values)
    refuse_sign_up_without_mail(values, development=development)
    refuse_mail_without_sender(values)
    refuse_bad_stream_caps(values)


def sign_up_open(values: dict[str, str], *, development: bool) -> bool:
    """Whether SIGN_UP_OPEN opens sign-up. Empty means closed, except on a
    development stack, where the dev overlay sends every mail to Mailpit and
    sign-up is how a developer makes an account.
    """
    switch = values.get("SIGN_UP_OPEN", "")
    if switch not in ("", "true", "false"):
        raise ValueError(
            f"SIGN_UP_OPEN is {switch!r}. It is true, false, or empty for closed "
            "(open on a development stack)."
        )
    return switch == "true" or (switch == "" and development)


def refresh_token_hours(values: dict[str, str]) -> str:
    """UNICON_SESSION_HARD_TTL in whole hours, rounded up, so the refresh
    token never runs out before the session does.
    """
    given = values.get("UNICON_SESSION_HARD_TTL", "")
    seconds = int(given) if given.isdigit() else 0
    if seconds <= 0:
        raise ValueError(
            f"UNICON_SESSION_HARD_TTL is {given!r}. It is how long a session "
            "lives, in seconds, and has to be a whole number above zero."
        )
    return str(-(-seconds // 3600))


def refuse_sign_up_without_mail(values: dict[str, str], *, development: bool) -> None:
    """Stop when sign-up is open and there is no mail server. Forgejo turns
    its address confirmation off without a word when it cannot send mail, and
    a contest's email_pattern would then let in anyone who types an address.
    A development stack has Mailpit, which the dev overlay names itself.
    """
    if (
        values.get("UNICON_FORGE_REGISTRATION_OPEN") == "true"
        and not development
        and not values.get("MAIL_SMTP_ADDR")
    ):
        raise ValueError(
            "SIGN_UP_OPEN is true but MAIL_SMTP_ADDR is empty. People who sign "
            "themselves up confirm their address by mail, and without a mail "
            "server Forgejo would confirm nobody. Set the MAIL_* values in .env, "
            "or keep sign-up closed."
        )


def refuse_mail_without_sender(values: dict[str, str]) -> None:
    """Stop when a mail server is named without a sender or with a port that
    is not a number. The backend refuses to start on either, and with it
    every page, where Forgejo would only log it.
    """
    if not values.get("MAIL_SMTP_ADDR"):
        return
    if not values.get("MAIL_FROM", "").strip():
        raise ValueError(
            "MAIL_SMTP_ADDR is set but MAIL_FROM is empty. Name the sender mail "
            "goes out as, such as Unicon <no-reply@contests.example.org>."
        )
    port = values.get("MAIL_SMTP_PORT", "587").strip()
    if not port.isdigit() or not 0 < int(port) < 65536:
        raise ValueError(f"MAIL_SMTP_PORT is {port!r}, which is not a port number.")


def refuse_bad_stream_caps(values: dict[str, str]) -> None:
    """Stop when a cap on live streams is not a number nginx takes. The proxy
    refuses to start on one, and with it sign-in and every page; empty is
    the default compose fills in.
    """
    for key in ("UNICON_LIVE_STREAMS", "UNICON_LIVE_STREAMS_PER_ADDRESS"):
        given = values.get(key, "")
        if given and not (given.isdigit() and 1 <= int(given) <= LIVE_STREAMS_CEILING):
            raise ValueError(
                f"{key} is {given!r}. It is how many live streams the proxy "
                f"holds, a whole number from 1 to {LIVE_STREAMS_CEILING}, or empty "
                "for the default."
            )


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
    wait_for("forgejo", lambda: compose.is_healthy("forgejo"), timeout_seconds=300.0)

    forgejo = Forgejo(compose, "forgejo")
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
        summary.record("forgejo admin token", created=False)
    else:
        values["UNICON_FORGE_ADMIN_TOKEN"] = forgejo.mint_access_token(
            BACKEND_ACCOUNT, values["FORGEJO_BACKEND_PASSWORD"]
        )
        summary.record("forgejo admin token", created=True)

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


def _seed_platform(
    compose: Compose,
    directory: Path,
    manifest: Manifest,
    values: dict[str, str],
    summary: Summary,
    rewrite: bool,
) -> None:
    """The platform org, each version of the primitives the manifest pins and
    each version of the built-in workflow unicon/classic, in that order, since
    the workflow names the primitives. Then every image the primitives'
    versions name, which is the socket filter's list.

    Every version is compared with the forge first, so that any that differs
    stops the run before anything is seeded, unless `rewrite`.
    """
    repos = PlatformRepos(
        Forgejo(compose, "forgejo"), values["UNICON_FORGE_ADMIN_TOKEN"]
    )
    name = workflows.CLASSIC
    workflow = f"{PLATFORM_ORG}/{name}"
    workflow_repo = workflows.repository(name)
    mirrored = {
        release: primitives.version_files(release) for release in manifest.primitives
    }
    definitions = workflows.seed_versions(directory, name)
    if not rewrite:
        refuse_differing_versions(
            manifest,
            [
                f"{PLATFORM_ORG}/{release.name}@{release.version}, against what "
                f"{manifest.path.name} pins for it"
                for release, files in mirrored.items()
                if primitives.standing_of(repos, release, files) is Standing.DIFFERENT
            ]
            + [
                f"{workflow}@{version}, against workflows/{name}/{version}/"
                for version, files in definitions.items()
                if standing(repos, PLATFORM_ORG, workflow_repo, version, files)
                is Standing.DIFFERENT
            ],
        )

    summary.record(f"forgejo organisation {PLATFORM_ORG}", repos.ensure_platform_org())
    for primitive in manifest.primitive_names:
        repo = primitives.repository(primitive)
        summary.record(
            f"primitive {PLATFORM_ORG}/{primitive} repository",
            repos.ensure_repository(PLATFORM_ORG, repo),
        )
        for release in manifest.versions_of(primitive):
            outcome = primitives.mirror(
                repos, release, mirrored[release], rewrite=rewrite
            )
            _record_version(
                summary,
                f"primitive {PLATFORM_ORG}/{primitive}",
                release.version,
                outcome,
            )
        summary.record(
            f"primitive {PLATFORM_ORG}/{primitive} mark {primitives.PRIMITIVE_TOPIC}",
            repos.ensure_mark(PLATFORM_ORG, repo, primitives.PRIMITIVE_TOPIC),
        )

    summary.record(
        f"workflow {workflow} repository",
        repos.ensure_repository(PLATFORM_ORG, workflow_repo),
    )
    for version, files in definitions.items():
        outcome = publish(
            repos,
            PLATFORM_ORG,
            workflow_repo,
            version,
            files,
            message=f"Seed {workflow}@{version}",
            rewrite=rewrite,
        )
        _record_version(summary, f"workflow {workflow}", version, outcome)
    summary.record(
        f"workflow {workflow} mark {workflows.WORKFLOW_TOPIC}",
        repos.ensure_mark(PLATFORM_ORG, workflow_repo, workflows.WORKFLOW_TOPIC),
    )

    values["UNICON_FILTER_IMAGES"] = ",".join(
        primitives.images_at_forge(repos, manifest.primitive_names)
    )


def refuse_differing_versions(manifest: Manifest, differing: list[str]) -> None:
    """Stop the run, naming each version at the forge that differs from what
    is here.
    """
    if not differing:
        return
    raise DifferingVersions(
        "nothing is seeded, since these versions at the forge differ from "
        "what is here and a version is never edited:\n"
        + "".join(f"  {version}\n" for version in differing)
        + "A changed image or declaration of a primitive goes under a new "
        f"version in {manifest.path.name}, and a changed workflow definition "
        "in a new version folder under workflows/. On a development stack, "
        "uv run bootstrap --rewrite rewrites each version in place instead."
    )


def _record_version(
    summary: Summary, what: str, version: str, outcome: Outcome
) -> None:
    """One summary line for a version. A rewritten version counts as made and
    says so.
    """
    label = f"{what} version {version}"
    if outcome is Outcome.REWRITTEN:
        label += f" ({outcome.value})"
    summary.record(label, outcome in (Outcome.CREATED, Outcome.REWRITTEN))


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


def _enrol_dev_agent(values: dict[str, str], summary: Summary) -> None:
    """The one CI agent the dev overlay runs on this machine, enrolled at the
    CI as a global agent the way a platform machine is, with its token in
    .env for compose.dev.yaml to hand it. Every other agent that would take a
    grading run and has gone silent is deleted, so a development stack's
    gradings land on the laptop's agent and nowhere else.
    """
    woodpecker = Woodpecker(
        values["WOODPECKER_PUBLIC_URL"], values["FORGEJO_PUBLIC_URL"]
    )
    admin = values["UNICON_WOODPECKER_TOKEN"]
    token, created = woodpecker.ensure_agent(
        admin, DEV_AGENT, values.get("WOODPECKER_DEV_AGENT_TOKEN", "")
    )
    values["WOODPECKER_DEV_AGENT_TOKEN"] = token
    summary.record(f"woodpecker agent {DEV_AGENT}", created)

    others = woodpecker.remove_other_agents(admin, token, DEV_AGENT, GRADING_LABEL)
    for name in others.removed:
        print(f"removed woodpecker agent {name}, silent for over an hour")
    for name in others.reporting:
        print(
            f"note: woodpecker agent {name} also takes grading runs and is still "
            "reporting to the CI; it is left as it is"
        )
    for name in others.refused:
        print(f"note: the CI would not delete the silent woodpecker agent {name}")


def _pull_grading_images(
    compose: Compose, values: dict[str, str], summary: Summary
) -> None:
    """Put on this machine every image a grading step may run: the images the
    socket filter lets a step start from. The filter refuses a pull, so a
    grading machine has to hold them before a run needs one; on a platform
    machine the worker does this at enrolment, and on the laptop it is this
    step. The harness and clone images are the CI's own steps, which the agent
    pulls itself.
    """
    for reference in filter(None, values.get("UNICON_FILTER_IMAGES", "").split(",")):
        summary.record(
            f"grading image {reference.rpartition('/')[2]}",
            compose.ensure_image(reference),
        )


def recreate_changed(compose: Compose, *services: str) -> None:
    """Bring each running service up to the values now in .env.

    A running container keeps the environment it started with, so a re-minted
    token would sit in .env while the backend kept presenting the revoked one.
    `up` recreates a container when the configuration compose works out from
    the compose files and .env, interpolated values included, differs from
    the one the container was made with, and leaves it alone otherwise; so
    every running service gets an `up` and compose decides. A service that is
    not running is left as it is, since the dev overlay's agent and socket
    filter run only when someone started the `agent` profile; whatever starts
    it with `up` gives it the new values.
    """
    for service in services:
        if not compose.is_running(service):
            continue
        before = compose.container_id(service)
        compose.up(service)
        if compose.container_id(service) != before:
            print(f"recreated {service}, as its configuration changed")


def _known_application(
    client_id: str | None, client_secret: str | None
) -> OAuthApplication | None:
    if client_id and client_secret:
        return OAuthApplication(client_id, client_secret)
    return None
