"""Revision-checked drafts, immutable proof metadata and content addressed images."""

from hashlib import sha256
from json import dumps, loads
from os import fsync, replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from datetime import datetime, timezone
from uuid import uuid4

from image_lab.validation import Conflict, Invalid

from .model import MAX_SAVED_PROOFS, identifier


def identity():
    return uuid4().hex[:24]


def stamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_bytes(value):
    return (dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()


def atomic_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, prefix="pending-", delete=False) as handle:
        scratch = Path(handle.name)
        try:
            handle.write(content)
            handle.flush()
            fsync(handle.fileno())
            replace(scratch, path)
        finally:
            scratch.unlink(missing_ok=True)


class Library:
    def __init__(self, root):
        self.root = Path(root)
        self.drafts = self.root / "drafts"
        self.proofs = self.root / "proofs"
        self.blobs = self.root / "blobs"
        self.guard = RLock()
        self.drafts.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def read(path):
        try:
            return loads(path.read_bytes())
        except FileNotFoundError as error:
            raise Invalid("This record is unavailable.") from error

    def draft(self, key):
        return self.read(self.drafts / (identifier(key) + ".json"))

    def list(self):
        drafts = (self.read(path) for path in self.drafts.glob("*.json"))
        return sorted(
            [{**{k: draft[k] for k in ("id", "title", "updated", "synthetic")}, "frame_count": len(draft["frames"])}
             for draft in drafts],
            key=lambda row: row["updated"], reverse=True,
        )

    def create(self, title, settings, frames=None, scan=None, synthetic=False, band=None):
        with self.guard:
            key, now = identity(), stamp()
            draft = {
                "id": key, "title": title, "created": now, "updated": now, "revision": 1,
                "frames": frames or [], "scan": scan, "proofs": [], "settings": settings,
                "band": band if band is not None else {"frame_count": 8, "captures": []},
                "synthetic": synthetic,
            }
            atomic_write(self.drafts / (key + ".json"), json_bytes(draft))
            return draft

    def change(self, key, expected, update):
        with self.guard:
            draft = self.draft(key)
            if type(expected) is not int or draft["revision"] != expected:
                raise Conflict("This drum changed. Reload it before saving.")
            update(draft)
            draft["revision"] += 1
            draft["updated"] = stamp()
            atomic_write(self.drafts / (key + ".json"), json_bytes(draft))
            return draft

    def blob(self, digest):
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
            raise Invalid("The stored image identity is invalid.")
        path = self.blobs / digest[:2] / digest
        if not path.is_file():
            raise Invalid("The stored image is unavailable.")
        return path

    def keep(self, data):
        digest = sha256(data).hexdigest()
        path = self.blobs / digest[:2] / digest
        if not path.is_file():
            atomic_write(path, data)
        return digest

    def proof(self, draft, proof_key):
        identifier(proof_key)
        if all(proof["id"] != proof_key for proof in draft["proofs"]):
            raise Invalid("This retained proof is outside the current drum.")
        return self.read(self.proofs / (proof_key + ".json"))

    def retain(self, key, revision, proof):
        row = {**proof, "id": identity(), "saved": stamp(), "source_revision": revision}
        path = self.proofs / (row["id"] + ".json")

        def save(draft):
            if len(draft["proofs"]) >= MAX_SAVED_PROOFS:
                raise Invalid(f"This drum already has {MAX_SAVED_PROOFS} saved proofs.")
            atomic_write(path, json_bytes(row))
            draft["proofs"].append({k: row[k] for k in ("id", "mode", "title", "saved")})

        try:
            updated = self.change(key, revision, save)
        except Exception:
            path.unlink(missing_ok=True)
            raise
        return updated, row
