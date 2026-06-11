"""The remote backend behind a ``kb0://`` vault.

Talks REST to the kb0 cloud's agent self-routes (``/v1/vault/*``) with the API
key, and maps the cloud's note shape onto the same dict shapes the local MCP
tools return — so callers use the identical VaultClient methods whether the
vault is local or hosted. Mirrors the TypeScript client's ``remote.ts``.
"""

from __future__ import annotations

import uuid
from typing import Any
from urllib.parse import quote

import httpx

from .errors import (
    KbACLDeniedError,
    KbConflictError,
    KbError,
    KbNotFoundError,
    KbValidationError,
)

DEFAULT_CLOUD_URL = "https://kb0-api-production.up.railway.app"


def _str(value: Any, fallback: str = "") -> str:
    return value if isinstance(value, str) else fallback


def _str_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [v for v in value if isinstance(v, str)]
    return []


def _raise_for(resp: httpx.Response) -> None:
    """Raise the typed KbError matching a non-2xx cloud response."""
    try:
        body = resp.json()
    except ValueError:
        body = {}
    message = body.get("message") or body.get("error") or f"cloud responded {resp.status_code}"
    if resp.status_code == 404:
        raise KbNotFoundError(message)
    if resp.status_code == 409:
        raise KbConflictError(message)
    if resp.status_code in (401, 403):
        raise KbACLDeniedError(message)
    if resp.status_code == 400:
        raise KbValidationError(message)
    raise KbError(message)


class RemoteVault:
    """REST client for a hosted (``kb0://``) vault. Internal to VaultClient."""

    def __init__(
        self,
        cloud_url: str,
        api_key: str,
        agent: str,
        *,
        vault_name: str = "",
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._base = cloud_url.rstrip("/")
        self._agent = agent
        headers = {
            "authorization": f"Bearer {api_key}",
            # stamps the agent identity on hosted-vault audit events
            "x-kb0-agent": agent,
        }
        if vault_name:
            # kb0://<name> routing — addresses one of the tenant's named vaults
            headers["x-kb0-vault"] = vault_name
        self._http = httpx.AsyncClient(
            base_url=self._base,
            headers=headers,
            timeout=30.0,
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    def _note_url(self, path: str) -> str:
        encoded = "/".join(quote(seg, safe="") for seg in path.split("/"))
        return f"/v1/vault/notes/{encoded}"

    async def _tree(self) -> list[dict[str, Any]]:
        resp = await self._http.get("/v1/vault/tree")
        if resp.status_code >= 400:
            _raise_for(resp)
        entries = resp.json().get("entries")
        return entries if isinstance(entries, list) else []

    def _to_read(self, note: dict[str, Any]) -> dict[str, Any]:
        fm = note.get("frontmatter") or {}
        updated_at = _str(note.get("updatedAt"))
        return {
            "path": note.get("path"),
            "hash": note.get("hash"),
            "content": note.get("content"),
            "frontmatter": {
                **fm,
                "id": _str(fm.get("id")),
                "title": _str(fm.get("title"), _str(note.get("title"))),
                "author": _str(fm.get("author")),
                "status": _str(fm.get("status"), "draft"),
                "tags": _str_list(fm.get("tags")),
                "created": _str(fm.get("created"), updated_at),
                "updated": _str(fm.get("updated"), updated_at),
            },
        }

    # ── tools (same shapes as the local MCP tools) ─────────────────────────────

    async def read(self, path: str) -> dict[str, Any]:
        resp = await self._http.get(self._note_url(path))
        if resp.status_code >= 400:
            _raise_for(resp)
        return self._to_read(resp.json())

    async def write(
        self,
        path: str,
        *,
        title: str,
        content: str,
        status: str = "draft",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        note_id = str(uuid.uuid4())
        resp = await self._http.put(
            self._note_url(path),
            json={
                "title": title,
                "content": content,
                "frontmatter": {
                    "id": note_id,
                    "author": self._agent,
                    "status": status,
                    "tags": tags or [],
                },
            },
        )
        if resp.status_code >= 400:
            _raise_for(resp)
        note = resp.json()
        fm = note.get("frontmatter") or {}
        return {"path": note.get("path"), "hash": note.get("hash"), "id": _str(fm.get("id"), note_id)}

    async def update(
        self,
        path: str,
        *,
        content: str,
        expected_hash: str,
        title: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"content": content}
        if title is not None:
            body["title"] = title
        frontmatter: dict[str, Any] = {}
        if status is not None:
            frontmatter["status"] = status
        if tags is not None:
            frontmatter["tags"] = tags
        if frontmatter:
            body["frontmatter"] = frontmatter

        resp = await self._http.put(
            self._note_url(path), json=body, headers={"if-match": expected_hash}
        )
        if resp.status_code >= 400:
            _raise_for(resp)
        note = resp.json()
        return {"path": note.get("path"), "hash": note.get("hash")}

    async def delete(self, path: str) -> dict[str, Any]:
        resp = await self._http.delete(self._note_url(path))
        if resp.status_code >= 400 and resp.status_code != 204:
            _raise_for(resp)
        return {"path": path}

    async def search(
        self,
        query: str,
        *,
        mode: str = "hybrid",  # accepted for API parity; hosted search is keyword today
        ranking: str = "rrf",
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        del mode, ranking  # hosted search has a single mode for now
        params: dict[str, Any] = {"q": query, "limit": limit}
        if filters:
            if isinstance(filters.get("status"), str):
                params["status"] = filters["status"]
            tags = _str_list(filters.get("tags"))
            if tags:
                params["tag"] = tags[0]
        resp = await self._http.get("/v1/vault/search", params=params)
        if resp.status_code >= 400:
            _raise_for(resp)
        return resp.json()

    async def list(
        self,
        *,
        prefix: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        entries = await self._tree()
        if prefix:
            entries = [e for e in entries if _str(e.get("path")).startswith(prefix)]
        if status:
            entries = [e for e in entries if e.get("status") == status]
        if tag:
            entries = [e for e in entries if tag in _str_list(e.get("tags"))]
        notes = [
            {
                "id": "",
                "path": e.get("path"),
                "title": e.get("title"),
                "status": _str(e.get("status"), "draft"),
                "tags": _str_list(e.get("tags")),
            }
            for e in entries[:limit]
        ]
        return {"notes": notes, "total": len(entries)}

    async def recent(self, limit: int = 10) -> dict[str, Any]:
        entries = await self._tree()  # already newest-first
        return {
            "notes": [
                {
                    "path": e.get("path"),
                    "title": e.get("title"),
                    "updated": e.get("updatedAt"),
                    "status": _str(e.get("status"), "draft"),
                }
                for e in entries[:limit]
            ]
        }

    async def links(self, path: str) -> dict[str, Any]:
        resp = await self._http.get("/v1/vault/links", params={"path": path})
        if resp.status_code >= 400:
            _raise_for(resp)
        return resp.json()

    async def backlinks(self, path: str) -> dict[str, Any]:
        resp = await self._http.get("/v1/vault/backlinks", params={"path": path})
        if resp.status_code >= 400:
            _raise_for(resp)
        return resp.json()

    async def status(self, vault: str, version: str) -> dict[str, Any]:
        entries = await self._tree()
        return {
            "vault": vault,
            "agent": self._agent,
            "version": version,
            "notes": len(entries),
            "stale_embeddings": 0,
            "embedding_model": "hosted (kb0 cloud)",
            "policy_mode": "enforced",
            "policy_file": False,
            "log_file": "(hosted)",
        }
