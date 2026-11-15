"""The platform org and its package store, which bootstrap makes before the
primitives. These run bootstrap's steps against the in-memory Forgejo of
forgejo_fake.py.
"""

from __future__ import annotations

from pathlib import Path

from bootstrap import main
from bootstrap.images import Manifest
from bootstrap.summary import Summary
from tests.forgejo_fake import FakeForgejo

DEPLOY = Path(__file__).resolve().parent.parent
TOKEN = "provisioning-token"
NO_PRIMITIVES = Manifest(
    path=DEPLOY / "images.local.json",
    images={"step-helper": "localhost:5000/uniconhq/step-helper@sha256:" + "0" * 64},
    primitives=(),
)


def _seed(forge: FakeForgejo) -> None:
    values = {"UNICON_FORGE_ADMIN_TOKEN": TOKEN}
    main._seed_platform(forge, NO_PRIMITIVES, values, Summary(), False)


def test_the_platform_org_is_public_and_made_by_the_platform_account() -> None:
    forge = FakeForgejo()

    _seed(forge)

    assert forge.body_of("POST", "/orgs") == {
        "username": "unicon",
        "description": "Unicon's own primitives",
        "visibility": "public",
        "repo_admin_change_team_access": False,
    }
    assert forge.body_of("POST", "/orgs/unicon/repos") == {
        "name": "packages",
        "private": False,
        "default_branch": "main",
        "auto_init": False,
    }


def test_an_existing_private_package_store_is_made_public() -> None:
    """A job fetches the pieces from it with no credential."""
    forge = FakeForgejo()
    store = forge.seed("unicon/packages")
    store.private = True

    _seed(forge)

    assert store.private is False
    assert forge.body_of("PATCH", "/repos/unicon/packages") == {"private": False}
