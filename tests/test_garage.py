"""The Garage client's grants, read from the admin API calls it makes."""

from __future__ import annotations

import json
from typing import Any

from bootstrap.garage import Garage


class Recording:
    """Stands in for compose: keeps every admin API call and answers none."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(self, service: str, *arguments: str) -> str:
        self.calls.append((arguments[2], json.loads(arguments[3])))
        return ""


def _set(calls: list[tuple[str, dict[str, Any]]], endpoint: str) -> set[str]:
    return {
        name
        for called, payload in calls
        if called == endpoint
        for name, named in payload["permissions"].items()
        if named
    }


def test_a_read_only_key_may_read_the_bucket_and_nothing_more() -> None:
    """The backend's key for forgejo-lfs: read granted, and write and owner
    taken away, since granting leaves what it does not name as it was."""
    compose = Recording()
    Garage(compose, "garage").allow_read_only("bucket", "key")  # type: ignore[arg-type]
    assert {payload["bucketId"] for _, payload in compose.calls} == {"bucket"}
    assert {payload["accessKeyId"] for _, payload in compose.calls} == {"key"}
    assert _set(compose.calls, "AllowBucketKey") == {"read"}
    assert _set(compose.calls, "DenyBucketKey") == {"write", "owner"}


def test_temporary_uploads_expire_and_unfinished_parts_are_aborted_after_one_day() -> (
    None
):
    compose = Recording()
    Garage(compose, "garage").expire_uploads("uploads")  # type: ignore[arg-type]
    assert compose.calls == [
        (
            "UpdateBucket",
            {
                "id": "uploads",
                "body": {
                    "lifecycleRules": [
                        {
                            "ID": "abandoned-uploads",
                            "Status": "Enabled",
                            "Filter": {"Prefix": ""},
                            "AbortIncompleteMultipartUpload": {
                                "DaysAfterInitiation": 1
                            },
                            "Expiration": {"Days": 1},
                        }
                    ]
                },
            },
        )
    ]
