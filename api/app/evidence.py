"""Private evidence storage access used to verify completed uploads."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol

import httpx

EVIDENCE_BUCKET = "evidence"
ALLOWED_MIME_TYPES = frozenset({"image/jpeg", "image/png", "image/webp"})
MAX_ATTACHMENT_BYTES = 5 * 1024 * 1024
UPLOAD_WINDOW_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class StoredObject:
    size_bytes: int
    sha256: str
    mime_type: str | None


class EvidenceStore(Protocol):
    """Read a stored object so its length and checksum can be verified."""

    async def fetch(self, storage_key: str) -> StoredObject | None:
        """Return the object's size/hash or ``None`` when it does not exist."""


class SupabaseEvidenceStore:
    """Fetch objects from Supabase Storage with the server-only secret key."""

    def __init__(
        self, *, supabase_url: str, secret_key: str, bucket: str = EVIDENCE_BUCKET
    ) -> None:
        self._base = supabase_url.rstrip("/")
        self._secret_key = secret_key
        self._bucket = bucket

    async def fetch(self, storage_key: str) -> StoredObject | None:
        url = f"{self._base}/storage/v1/object/{self._bucket}/{storage_key}"
        headers = {
            "apikey": self._secret_key,
            "Authorization": f"Bearer {self._secret_key}",
        }
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(url, headers=headers)
        if response.status_code in (400, 404):
            return None
        response.raise_for_status()
        content = response.content
        return StoredObject(
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
            mime_type=response.headers.get("content-type"),
        )


class UnavailableEvidenceStore:
    """Used when no server storage credential is configured."""

    async def fetch(self, storage_key: str) -> StoredObject | None:
        del storage_key
        raise RuntimeError("evidence store not configured")


def build_evidence_store(
    *, supabase_url: str | None, secret_key: str | None
) -> EvidenceStore:
    if supabase_url and secret_key:
        return SupabaseEvidenceStore(supabase_url=supabase_url, secret_key=secret_key)
    return UnavailableEvidenceStore()
