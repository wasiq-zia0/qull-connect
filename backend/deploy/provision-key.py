#!/usr/bin/env python3
"""Provision/revoke a user-scoped API key without printing its secret.

Run on the trusted server. Deliver the private --output file through an approved
secure channel, then remove that delivery copy. Never commit or email this file.
"""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def atomic_registry(path, data):
    previous = path.stat() if path.exists() else None
    handle, name = tempfile.mkstemp(prefix=".keys-", dir=path.parent)
    try:
        os.fchmod(handle, 0o640 if previous and previous.st_mode & 0o040 else 0o600)
        if previous:
            os.fchown(handle, previous.st_uid, previous.st_gid)
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--registry", type=Path, required=True)
    sub = parser.add_subparsers(dest="action", required=True)
    create = sub.add_parser("create")
    create.add_argument("--owner", required=True, help="Stable server-assigned user identifier")
    create.add_argument("--output", type=Path, required=True, help="New private file; secret is never printed")
    create.add_argument("--expires-at", help="Optional timezone-aware ISO 8601 timestamp")
    revoke = sub.add_parser("revoke")
    revoke.add_argument("--key-id", required=True, help="Public key identifier printed by create")
    args = parser.parse_args()
    if not args.registry.is_absolute():
        parser.error("Registry path must be absolute")
    if args.action == "create":
        if (not args.owner.strip() or args.owner.strip() != args.owner or len(args.owner) > 128
                or any(ord(char) < 32 for char in args.owner)):
            parser.error("Owner must be 1–128 characters, without surrounding whitespace/control characters")
        if args.expires_at:
            try:
                deadline = datetime.fromisoformat(args.expires_at.replace("Z", "+00:00"))
                if deadline.tzinfo is None or deadline <= datetime.now(timezone.utc):
                    raise ValueError()
            except ValueError:
                parser.error("Expiration must be a future ISO 8601 timestamp including its timezone")
    lock_fd = os.open(str(args.registry) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(lock_fd, "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = json.loads(args.registry.read_text()) if args.registry.exists() else {"version": 1, "keys": []}
        if data.get("version") != 1 or not isinstance(data.get("keys"), list):
            parser.error("Unsupported registry schema; refusing to replace it")
        if args.action == "create":
            secret = "qk_" + secrets.token_urlsafe(32)
            key_id = secrets.token_hex(8)
            entry = {"key_id": key_id, "sha256": hashlib.sha256(secret.encode()).hexdigest(),
                     "owner_id": args.owner, "created_at": datetime.now(timezone.utc).isoformat()}
            if args.expires_at:
                entry["expires_at"] = args.expires_at
            # O_EXCL prevents overwriting a credential, including through a symlink.
            out_fd = os.open(args.output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            try:
                with os.fdopen(out_fd, "w") as stream:
                    stream.write(secret + "\n")
                    stream.flush()
                    os.fsync(stream.fileno())
                data["keys"].append(entry)
                atomic_registry(args.registry, data)
            except BaseException:
                args.output.unlink(missing_ok=True)
                raise
            print(f"Created key {key_id}. Credential written to the private output file.")
        else:
            entries = [item for item in data["keys"] if item.get("key_id") == args.key_id]
            if len(entries) != 1:
                parser.error("Expected one matching key ID; registry unchanged")
            entries[0]["disabled"] = True
            atomic_registry(args.registry, data)
            print(f"Revoked key {args.key_id}. Revocation applies to new requests immediately.")


if __name__ == "__main__":
    main()
