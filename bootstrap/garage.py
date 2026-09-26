"""Garage: cluster layout, buckets and access keys.

Every call goes through the container's own command line, `garage json-api`,
which speaks the v2 admin API over Garage's internal RPC and prints the same
JSON the HTTP endpoint would return. That way bootstrap needs no published
port: it works against `-f compose.yaml` alone, where the admin API, a
full-control interface, stays inside the compose network. The dev override
still publishes it on loopback for scripts/check-lfs.py. GARAGE_BINARY is the
single binary in dxflrs/garage, which is both the server and the
administration client.

A fresh Garage node stores nothing until a layout is applied, and answers its
own /health with 503 until then, so this is the first thing bootstrap does.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from bootstrap.compose import Compose, ComposeFailed

GARAGE_BINARY = "/garage"


class GarageError(Exception):
    """The Garage admin API refused something."""


@dataclass(frozen=True)
class AccessKey:
    access_key_id: str
    secret_access_key: str


class Garage:
    def __init__(self, compose: Compose, service: str) -> None:
        self._compose = compose
        self._service = service

    def _call(self, endpoint: str, payload: Any = None) -> Any:
        """Invoke one admin API endpoint and return its decoded answer.

        Payloads here are bucket and key names, never secrets, so passing one
        as an argument is safe. Garage writes its connection log to stderr and
        the answer to stdout, so only stdout is parsed.
        """
        arguments = [GARAGE_BINARY, "json-api", endpoint]
        if payload is not None:
            arguments.append(json.dumps(payload))
        try:
            output = self._compose.execute(self._service, *arguments)
        except ComposeFailed as failure:
            raise GarageError(f"{endpoint}: {failure}") from failure
        return json.loads(output) if output.strip() else None

    def is_answering(self) -> bool:
        try:
            self._call("GetClusterStatus")
        except GarageError:
            return False
        return True

    def ensure_layout(self, zone: str = "unicon") -> bool:
        """Give the single node a role. Returns True if this run applied it.

        Capacity is the free space Garage reports on its data partition. On a
        one-node cluster the number only decides how partitions are spread, and
        there is nowhere else for them to go.
        """
        layout = self._call("GetClusterLayout")
        if layout["roles"]:
            return False

        status = self._call("GetClusterStatus")
        nodes = status["nodes"]
        if len(nodes) != 1:
            raise GarageError(f"expected exactly one Garage node, found {len(nodes)}")
        node = nodes[0]

        self._call(
            "UpdateClusterLayout",
            {
                "roles": [
                    {
                        "id": node["id"],
                        "zone": zone,
                        "capacity": node["dataPartition"]["available"],
                        "tags": [],
                    }
                ]
            },
        )
        self._call("ApplyClusterLayout", {"version": layout["version"] + 1})
        return True

    def ensure_bucket(self, alias: str) -> tuple[str, bool]:
        """Returns the bucket id and whether this run created it."""
        for existing in self._call("ListBuckets"):
            if alias in existing["globalAliases"]:
                return str(existing["id"]), False
        created = self._call("CreateBucket", {"globalAlias": alias})
        return str(created["id"]), True

    def ensure_key(self, name: str) -> tuple[AccessKey, bool]:
        """Returns the key and whether this run created it.

        Garage lets an existing secret be read back, so a lost .env costs
        nothing here: the same key is recovered rather than replaced.
        """
        for existing in self._call("ListKeys"):
            if existing["name"] == name:
                info = self._call(
                    "GetKeyInfo", {"id": existing["id"], "showSecretKey": True}
                )
                return AccessKey(info["accessKeyId"], info["secretAccessKey"]), False
        created = self._call("CreateKey", {"name": name})
        return AccessKey(created["accessKeyId"], created["secretAccessKey"]), True

    def allow_read_write(self, bucket_id: str, access_key_id: str) -> None:
        self._call(
            "AllowBucketKey",
            {
                "bucketId": bucket_id,
                "accessKeyId": access_key_id,
                "permissions": {"read": True, "write": True, "owner": False},
            },
        )
