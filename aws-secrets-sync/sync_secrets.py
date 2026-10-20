#!/usr/bin/env python3
"""Bootstrap AWS strings into OpenHands organization or personal secrets.

Dry run by default. Known existing values are refused; no deletion. Checkpoints
record API acknowledgments, not verification of runtime delivery or secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import UUID

import boto3
import requests
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


class SyncError(Exception):
    """A safe message that never includes remote response bodies or values."""


def load_config(path: Path) -> dict:
    config = json.loads(path.read_text())
    required = {
        "base_url",
        "org_id",
        "mappings",
    }
    if not isinstance(config, dict) or not (
        required <= set(config) <= required | {"scope", "expected_user_id"}
    ):
        raise SyncError("Config requires origin, org, mappings, and known options.")
    scope = config.get("scope", "organization")
    if scope not in ("organization", "personal"):
        raise SyncError("scope must be organization or personal.")
    if scope == "personal" and "expected_user_id" not in config:
        raise SyncError("Personal scope requires expected_user_id.")
    if "expected_user_id" in config:
        if not isinstance(config["expected_user_id"], str):
            raise SyncError("expected_user_id must be a user UUID.")
        config["expected_user_id"] = str(UUID(config["expected_user_id"]))
    if not isinstance(config["base_url"], str) or not isinstance(config["org_id"], str):
        raise SyncError("base_url and org_id must be strings.")
    url = urlsplit(config["base_url"])
    if (
        url.scheme != "https"
        or not url.hostname
        or url.username
        or url.password
        or url.query
        or url.fragment
        or url.path not in ("", "/")
    ):
        raise SyncError("base_url must be an HTTPS origin without credentials.")
    config["base_url"] = config["base_url"].rstrip("/")
    config["org_id"] = str(UUID(config["org_id"]))
    mappings = config["mappings"]
    if not isinstance(mappings, list) or not mappings:
        raise SyncError("Provide at least one explicit mapping.")
    names = set()
    for mapping in mappings:
        if not isinstance(mapping, dict) or not (
            {"secret_arn", "name"}
            <= set(mapping)
            <= {"secret_arn", "name", "json_field"}
        ):
            raise SyncError("Mapping requires secret_arn, name, optional json_field.")
        arn = mapping["secret_arn"]
        if not isinstance(arn, str) or not re.fullmatch(
            r"arn:aws(?:-us-gov|-cn)?:secretsmanager:[a-z0-9-]+:\d{12}:secret:.+",
            arn,
        ):
            raise SyncError("Use a complete Secrets Manager ARN for each mapping.")
        name = mapping["name"]
        if not isinstance(name, str) or not re.fullmatch(
            r"[A-Za-z_][A-Za-z0-9_]*", name
        ):
            raise SyncError("Destination names must be valid environment names.")
        if name in names:
            raise SyncError("Each destination name must appear only once.")
        names.add(name)
        if "json_field" in mapping and (
            not isinstance(mapping["json_field"], str) or not mapping["json_field"]
        ):
            raise SyncError("json_field must be a nonempty top-level field name.")
    return config


def mapping_key(config: dict, mapping: dict) -> str:
    # Preserve existing organization checkpoints. Personal state is per user.
    payload = [config["base_url"], config["org_id"], mapping]
    if config.get("scope") == "personal":
        payload.extend(["personal", config["expected_user_id"]])
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


class Journal:
    """Single-writer local state; pending writes require manual reconciliation."""

    def __init__(self, path: Path):
        # The private directory also protects SQLite journal/lock sidecar files.
        path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.parent.stat().st_mode & 0o077:
            raise SyncError("State directory must be private (chmod 700).")
        if path.is_symlink():
            raise SyncError("State file must not be a symlink.")
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(path, timeout=1)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS writes "
            "(mapping TEXT PRIMARY KEY, version TEXT NOT NULL, status TEXT NOT NULL)"
        )

    def get(self, key: str):
        return self.db.execute(
            "SELECT version, status FROM writes WHERE mapping=?", (key,)
        ).fetchone()

    def record(self, key: str, version: str, status: str):
        self.db.execute(
            "INSERT OR REPLACE INTO writes VALUES (?, ?, ?)", (key, version, status)
        )
        self.db.commit()

    def close(self):
        self.db.close()


class OpenHands:
    def __init__(self, config: dict, token: str):
        self.origin = config["base_url"]
        self.org_id = config["org_id"]
        self.expected_user_id = config.get("expected_user_id")
        self.url = (
            config["base_url"] + "/api/organizations/" + config["org_id"] + "/secrets"
        )
        self.session = requests.Session()
        self.session.headers.update(
            {"X-Access-Token": token, "X-Org-Id": config["org_id"]}
        )

    def metadata(self, url: str) -> dict:
        for attempt in range(3):
            try:
                response = self.session.get(
                    url, timeout=(10, 30), allow_redirects=False
                )
            except requests.RequestException:
                if attempt == 2:
                    raise SyncError("OpenHands metadata read failed.") from None
                time.sleep(2**attempt)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2**attempt)
                continue
            if response.status_code != 200:
                raise SyncError(f"OpenHands metadata HTTP {response.status_code}.")
            try:
                page = response.json()
                if not isinstance(page, dict):
                    raise SyncError("Unexpected OpenHands metadata response.")
                return page
            except (ValueError, KeyError, TypeError):
                raise SyncError("Unexpected OpenHands metadata response.") from None
        raise SyncError("OpenHands metadata read exhausted retries.")

    def verify_identity(self):
        # Effective org_id alone does not prove a key is organization-bound.
        identity = self.metadata(self.origin + "/api/keys/current")
        if (
            identity.get("bound_org_id") != self.org_id
            or identity.get("org_id") != self.org_id
        ):
            raise SyncError("Use an API key bound to the configured organization.")
        if self.expected_user_id and identity.get("user_id") != self.expected_user_id:
            raise SyncError("API key user does not match expected_user_id.")

    def names(self) -> set[str]:
        self.verify_identity()
        page = self.metadata(self.url)
        # The tested endpoint returns a single page and has no page input.
        if page.get("next_page_id"):
            raise SyncError("Paginated listing needs a qualified API adapter.")
        items = page.get("items")
        if not isinstance(items, list) or any(
            not isinstance(item, dict)
            or not isinstance(item.get("name"), str)
            or not item["name"]
            for item in items
        ):
            raise SyncError("Unexpected OpenHands metadata response.")
        names = {item["name"] for item in items}
        if len(names) != len(items):
            raise SyncError("Duplicate destination metadata; reconciliation required.")
        return names

    def create_payload(self, name: str, value: str) -> dict:
        return {"name": name, "value": value}

    def create(self, name: str, value: str):
        # Never retry a write without an idempotency contract. A timeout/409
        # cannot prove whether this writer created the value now in the store.
        try:
            response = self.session.post(
                self.url,
                json=self.create_payload(name, value),
                timeout=(10, 30),
                allow_redirects=False,
            )
        except requests.RequestException:
            raise SyncError(
                "Create outcome uncertain; reconcile pending state."
            ) from None
        if response.status_code != 201:
            raise SyncError(
                f"Create HTTP {response.status_code}; reconcile pending state."
            )


class PersonalOpenHands(OpenHands):
    """One authenticated user per invocation; never impersonates another user.

    Personal POST is an upsert. An exclusive writer is required to prevent
    overwriting a same-name value created after our metadata snapshot.
    """

    def __init__(self, config: dict, token: str):
        super().__init__(config, token)
        if not self.expected_user_id:
            raise SyncError("Personal scope requires expected_user_id.")
        self.url = self.origin + "/api/v1/secrets"
        self.mapping_names = {item["name"] for item in config["mappings"]}

    def names(self) -> set[str]:
        self.verify_identity()
        names = set()
        seen = set()
        shared_names = set()
        query: dict[str, str | int] = {"limit": 100}
        for _ in range(100):
            page = self.metadata(self.url + "/search?" + urlencode(query))
            items = page.get("items")
            if not isinstance(items, list):
                raise SyncError("Unexpected personal metadata response.")
            for item in items:
                if (
                    not isinstance(item, dict)
                    or not isinstance(item.get("name"), str)
                    or not item["name"]
                    or item.get("scope") not in ("personal", "organization")
                ):
                    raise SyncError("Unexpected personal metadata response.")
                # Include shared names too: conservatively refuse shadowing.
                if item["name"] in names:
                    raise SyncError("Duplicate destination metadata.")
                names.add(item["name"])
                if item["scope"] == "organization":
                    shared_names.add(item["name"])
            cursor = page.get("next_page_id")
            if cursor is None:
                if self.mapping_names & shared_names:
                    raise SyncError("Mapped name collides with a shared secret.")
                return names
            if not isinstance(cursor, str) or not cursor or cursor in seen:
                raise SyncError("Invalid/repeated personal metadata cursor.")
            seen.add(cursor)
            query["page_id"] = cursor
        raise SyncError("Personal metadata page limit exceeded.")

    def create_payload(self, name: str, value: str) -> dict:
        return {"name": name, "value": value, "scope": "personal"}


def destination_for(config: dict, token: str) -> OpenHands:
    """Extension point for customer-owned destination adapters."""
    if config.get("scope", "organization") == "personal":
        return PersonalOpenHands(config, token)
    return OpenHands(config, token)


def current_version(client, arn: str) -> str:
    metadata = client.describe_secret(SecretId=arn)
    if metadata.get("ARN") != arn or metadata.get("DeletedDate"):
        raise SyncError("Source identity mismatch or scheduled deletion.")
    versions = [
        version
        for version, stages in metadata.get("VersionIdsToStages", {}).items()
        if "AWSCURRENT" in stages
    ]
    if len(versions) != 1:
        raise SyncError("Source must have exactly one AWSCURRENT version.")
    return versions[0]


def read_value(client, mapping: dict, version: str) -> str:
    result = client.get_secret_value(
        SecretId=mapping["secret_arn"], VersionId=version, VersionStage="AWSCURRENT"
    )
    if (
        result.get("ARN") != mapping["secret_arn"]
        or result.get("VersionId") != version
        or "AWSCURRENT" not in result.get("VersionStages", [])
    ):
        raise SyncError("Source identity/version mismatch.")
    value = result.get("SecretString")
    if not isinstance(value, str):
        raise SyncError("Only SecretString is supported; binary source rejected.")
    if "json_field" in mapping:
        try:
            value = json.loads(value)[mapping["json_field"]]
        except (ValueError, KeyError, TypeError):
            raise SyncError(
                "Missing/invalid JSON field; destination unchanged."
            ) from None
    if not isinstance(value, str) or not value:
        raise SyncError("Mapped value must be a nonempty string.")
    return value


def sync(config: dict, source_for_region, destination, journal: Journal, apply=False):
    names = destination.names()
    failures = 0
    for index, mapping in enumerate(config["mappings"], start=1):
        try:
            key = mapping_key(config, mapping)
            previous = journal.get(key)
            if previous and previous[1] == "pending":
                raise SyncError("Pending write; operator reconciliation required.")
            source = source_for_region(mapping["secret_arn"].split(":")[3])
            version = current_version(source, mapping["secret_arn"])
            if mapping["name"] in names:
                if previous == (version, "applied"):
                    print(f"mapping {index}: checkpoint unchanged (not value-verified)")
                    continue
                raise SyncError("Existing destination; rotation/adoption unsupported.")
            if not apply:
                print(f"mapping {index}: would create (value not read)")
                continue
            value = read_value(source, mapping, version)
            journal.record(key, version, "pending")
            destination.create(mapping["name"], value)
            journal.record(key, version, "applied")
            del value
            names.add(mapping["name"])
            print(f"mapping {index}: create acknowledged")
        except (BotoCoreError, ClientError):
            failures += 1
            print(
                f"mapping {index}: AWS read failed; destination unchanged",
                file=sys.stderr,
            )
        except SyncError as exc:
            failures += 1
            print(f"mapping {index}: {exc}", file=sys.stderr)
    return 1 if failures else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument(
        "--state", type=Path, default=Path(".sync-state/journal.sqlite")
    )
    parser.add_argument("--profile", help="Optional AWS named profile")
    parser.add_argument(
        "--apply", action="store_true", help="Create missing destinations"
    )
    args = parser.parse_args()
    journal = None
    lock_fd = None
    try:
        # Local flock protects concurrent runs, including separate SQLite commits.
        # Run on Linux/macOS with local state; distributed workers need a lease.
        import fcntl

        config = load_config(args.config)
        token = os.environ.get("OH_API_KEY")
        if not token:
            raise SyncError("Set OH_API_KEY to an organization-bound API key.")
        journal = Journal(args.state)
        lock_fd = os.open(str(args.state) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SyncError("Another sync process holds this journal lock.") from None
        session = boto3.Session(profile_name=args.profile)
        clients = {}

        def source_for_region(region):
            if region not in clients:
                clients[region] = session.client(
                    "secretsmanager",
                    region_name=region,
                    config=Config(
                        retries={"mode": "standard", "total_max_attempts": 3},
                        connect_timeout=10,
                        read_timeout=30,
                    ),
                )
            return clients[region]

        return sync(
            config,
            source_for_region,
            destination_for(config, token),
            journal,
            args.apply,
        )
    except SyncError as exc:
        print(str(exc), file=sys.stderr)
    except (OSError, ValueError, TypeError, KeyError, sqlite3.Error, BotoCoreError):
        # Do not echo arbitrary local/remote messages that could contain values.
        print("Configuration, credentials, or local state error.", file=sys.stderr)
    finally:
        if journal:
            journal.close()
        if lock_fd is not None:
            os.close(lock_fd)
    return 1


if __name__ == "__main__":
    sys.exit(main())
