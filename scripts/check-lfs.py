"""Prove that a large file survives the round trip through Forgejo LFS.

Unicon commits contestant submissions with the same contents API call whether
they are a hundred bytes or half a gigabyte, and Forgejo turns anything matching
a filter=lfs rule in .gitattributes into an LFS object. The bytes then have to
land in Garage, not on the Forgejo container disk.

Run it from the deploy directory with the stack up:

    uv run scripts/check-lfs.py            # 500 MB
    uv run scripts/check-lfs.py --size 64  # smaller, for a quick check

On failure it halves the size and tries again, so the output always names a
ceiling that works. `--exact` turns that off and fails on the size asked for,
which is what CI wants: there the point is that one named size still works,
not that some smaller one does. The setting to look at first is Forgejo's
[server] LFS_MAX_FILE_SIZE. The proxy routes none of Forgejo's API, so this
script reaches it on the loopback port compose.dev.yaml publishes, the way it
reaches Garage's admin API; it is a check for a person at a keyboard, not part
of a deployment.

The request body is streamed in pieces of CHUNK_BYTES. Three raw bytes become
four base64 characters, so chunking on a multiple of three lets the encoded
pieces be concatenated without re-encoding anything. LONG_TIMEOUT is generous
but finite: a push that has made no progress in ten minutes is stuck, and a
script that hangs forever tells nobody anything.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import random
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx

from bootstrap import envfile

ORG = "unicon-checks"
REPO = "lfs-check"
BLOB_PATH = "blob.bin"
LFS_BUCKET = "forgejo-lfs"

CHUNK_BYTES = 3 * 1024 * 1024
LONG_TIMEOUT = httpx.Timeout(600.0)


class CheckFailed(Exception):
    """The round trip did not work at this size."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size", type=int, default=500, help="megabytes, default 500")
    parser.add_argument("--directory", type=Path, default=Path.cwd())
    parser.add_argument(
        "--exact", action="store_true", help="fail on that size instead of halving"
    )
    parser.add_argument(
        "--garage-admin-url",
        default="http://localhost:3903",
        help="Garage admin API as seen from this machine",
    )
    parser.add_argument(
        "--forge-url",
        default="http://localhost:3300",
        help="Forgejo as seen from this machine: the loopback port of compose.dev.yaml",
    )
    options = parser.parse_args(argv)

    values = envfile.load(options.directory / ".env")
    if not values.get("UNICON_FORGE_ADMIN_TOKEN"):
        print("no UNICON_FORGE_ADMIN_TOKEN in .env; run `uv run bootstrap` first")
        return 1

    forgejo = httpx.Client(
        base_url=f"{options.forge_url.rstrip('/')}/api/v1",
        headers={"Authorization": f"token {values['UNICON_FORGE_ADMIN_TOKEN']}"},
        timeout=LONG_TIMEOUT,
    )
    garage = httpx.Client(
        base_url=options.garage_admin_url,
        headers={"Authorization": f"Bearer {values['GARAGE_ADMIN_TOKEN']}"},
        timeout=30.0,
    )

    prepare_repository(forgejo)

    size_mb = options.size
    while size_mb >= 1:
        print(f"\n== {size_mb} MB")
        try:
            round_trip(forgejo, garage, size_mb)
        except (CheckFailed, httpx.HTTPError) as failure:
            print(f"   FAILED: {failure}")
            if options.exact:
                return 1
            size_mb //= 2
            continue
        print(f"\nlargest size that worked: {size_mb} MB")
        return 0

    print("\nnothing worked, not even 1 MB")
    return 1


def prepare_repository(forgejo: httpx.Client) -> None:
    if forgejo.get(f"/orgs/{ORG}").status_code == 404:
        expect(forgejo.post("/orgs", json={"username": ORG, "visibility": "private"}))
        print(f"created org {ORG}")
    if forgejo.get(f"/repos/{ORG}/{REPO}").status_code == 404:
        expect(
            forgejo.post(
                f"/orgs/{ORG}/repos",
                json={"name": REPO, "private": True, "auto_init": True},
            )
        )
        print(f"created repo {ORG}/{REPO}")
    write_file(
        forgejo,
        ".gitattributes",
        b"*.bin filter=lfs diff=lfs merge=lfs -text\n",
        "LFS rule for .bin",
    )


def round_trip(forgejo: httpx.Client, garage: httpx.Client, size_mb: int) -> None:
    size = size_mb * 1024 * 1024
    before = bucket_bytes(garage)

    started = time.monotonic()
    pushed = push_blob(forgejo, size)
    push_seconds = time.monotonic() - started
    print(
        f"   push  {size / 1e6:7.1f} MB in {push_seconds:6.1f}s"
        f"  ({size / 1e6 / push_seconds:6.1f} MB/s)"
    )

    started = time.monotonic()
    read_digest, read_size = read_blob(forgejo)
    read_seconds = time.monotonic() - started
    print(
        f"   read  {read_size / 1e6:7.1f} MB in {read_seconds:6.1f}s"
        f"  ({read_size / 1e6 / read_seconds:6.1f} MB/s)"
    )

    if read_size != size:
        raise CheckFailed(f"read back {read_size} bytes, pushed {size}")
    if read_digest != pushed:
        raise CheckFailed("sha256 of what came back does not match what went in")
    print(f"   sha256 matches: {pushed[:16]}...")

    after = bucket_bytes(garage)
    if after - before < size:
        raise CheckFailed(
            f"garage bucket {LFS_BUCKET} grew by {after - before} bytes, "
            f"expected at least {size}; LFS is not using Garage"
        )
    print(f"   garage bucket {LFS_BUCKET} grew by {(after - before) / 1e6:.1f} MB")


class StreamedBlob:
    """A contents-API request body carrying `size` random bytes.

    The body is produced a chunk at a time. At 500 MB the base64 alone is
    667 MB, and building that as one string would need it several times over in
    memory before a single byte left the machine. The sha256 is accumulated on
    the way past, so it is available once the request has been sent.

    The random source is seeded from the clock, not from the size. LFS stores
    an object once per digest, so a fixed seed would make the second run of
    this script push bytes Garage already has: the bucket would not grow, the
    growth check would fail, and the script would report a ceiling half the
    size of the one that works.
    """

    def __init__(self, payload: dict[str, Any], size: int) -> None:
        self._payload = payload
        self._size = size
        self._seed = time.time_ns()
        self._digest = hashlib.sha256()

    def __iter__(self) -> Iterator[bytes]:
        opening = json.dumps(self._payload)[:-1]
        separator = ", " if len(opening) > 1 else ""
        yield f'{opening}{separator}"content": "'.encode()
        source = random.Random(self._seed)
        remaining = self._size
        while remaining > 0:
            block = source.randbytes(min(CHUNK_BYTES, remaining))
            remaining -= len(block)
            self._digest.update(block)
            yield base64.b64encode(block)
        yield b'"}'

    @property
    def sha256(self) -> str:
        return self._digest.hexdigest()


def push_blob(forgejo: httpx.Client, size: int) -> str:
    """Commit `size` random bytes at BLOB_PATH and return their sha256."""
    existing = forgejo.get(f"/repos/{ORG}/{REPO}/contents/{BLOB_PATH}")
    payload: dict[str, Any] = {"message": f"blob of {size} bytes"}
    if existing.status_code == 200:
        payload["sha"] = existing.json()["sha"]
    body = StreamedBlob(payload, size)

    expect(
        forgejo.request(
            "PUT" if "sha" in payload else "POST",
            f"/repos/{ORG}/{REPO}/contents/{BLOB_PATH}",
            headers={"Content-Type": "application/json"},
            content=iter(body),
        )
    )
    return body.sha256


def read_blob(forgejo: httpx.Client) -> tuple[str, int]:
    """Read BLOB_PATH back through /media, which serves LFS content whole."""
    digest = hashlib.sha256()
    size = 0
    with forgejo.stream("GET", f"/repos/{ORG}/{REPO}/media/{BLOB_PATH}") as response:
        if response.status_code != 200:
            response.read()
            raise CheckFailed(f"/media returned {response.status_code}")
        for block in response.iter_bytes(1024 * 1024):
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def bucket_bytes(garage: httpx.Client) -> int:
    response = garage.get("/v2/GetBucketInfo", params={"globalAlias": LFS_BUCKET})
    if response.status_code != 200:
        raise CheckFailed(f"garage bucket {LFS_BUCKET} is missing")
    return int(response.json()["bytes"])


def write_file(forgejo: httpx.Client, path: str, content: bytes, message: str) -> None:
    if forgejo.get(f"/repos/{ORG}/{REPO}/contents/{path}").status_code == 200:
        return
    expect(
        forgejo.post(
            f"/repos/{ORG}/{REPO}/contents/{path}",
            json={
                "message": message,
                "content": base64.b64encode(content).decode(),
            },
        )
    )
    print(f"committed {path}")


def expect(response: httpx.Response) -> None:
    if response.status_code >= 400:
        raise CheckFailed(
            f"{response.request.method} {response.request.url.path} -> "
            f"{response.status_code} {response.text[:300]}"
        )


if __name__ == "__main__":
    raise SystemExit(main())
