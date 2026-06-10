"""Async Python client for a kb0 vault over MCP (stdio)."""

from __future__ import annotations

import json
import os
from typing import Any

from .errors import error_from_text


def _text_of(result: Any) -> str:
    parts: list[str] = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(text)
    return "\n".join(parts)


def _unwrap(result: Any) -> Any:
    """Turn an MCP CallToolResult into a plain dict, or raise a typed KbError."""
    if getattr(result, "isError", False):
        raise error_from_text(_text_of(result))

    structured = getattr(result, "structuredContent", None)
    if structured is not None:
        return structured

    text = _text_of(result)
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return {"text": text}


class VaultClient:
    """A thin client over the kb0 MCP server.

    For a local vault it spawns ``kb0 serve`` as a subprocess and exposes the
    10 vault tools as native async methods. For a hosted vault — a ``kb0://``
    address — it talks REST to the kb0 cloud instead: no subprocess, no local
    files, just your API key. Use as an async context manager::

        async with VaultClient(vault="./my-vault", agent="my-bot") as kb:
            await kb.write("notes/idea.md", title="Idea", content="...")
            hits = await kb.search("auth design")

        async with VaultClient(
            vault="kb0://team-kb", agent="my-bot", api_key="kb0_live_..."
        ) as kb:
            ...  # same methods, hosted on the kb0 cloud
    """

    def __init__(
        self,
        vault: str,
        agent: str,
        *,
        command: str = "kb0",
        env: dict[str, str] | None = None,
        openai_api_key: str | None = None,
        api_key: str | None = None,
        ingest_url: str | None = None,
        cloud_url: str | None = None,
        strict: bool = False,
        _transport: Any = None,
    ) -> None:
        self.vault = str(vault)
        self.agent = agent
        self.command = command
        self._extra_env = env
        self._openai_api_key = openai_api_key
        # kb0 cloud API key: enables audit forwarding on a local vault, and is
        # the credential for a hosted (kb0://) vault. Falls back to KB0_API_KEY.
        self._api_key = api_key
        self._ingest_url = ingest_url
        self._cloud_url = cloud_url
        self._strict = strict
        self._transport = _transport  # test seam for the hosted HTTP client
        self._session: Any = None
        self._stack: Any = None
        self._remote: Any = None

    @property
    def _is_hosted(self) -> bool:
        return self.vault.startswith("kb0://")

    async def __aenter__(self) -> "VaultClient":
        if self._is_hosted:
            from .remote import DEFAULT_CLOUD_URL, RemoteVault

            api_key = self._api_key or os.environ.get("KB0_API_KEY")
            if not api_key:
                raise ValueError(
                    "A hosted (kb0://) vault requires an api_key — create one in the "
                    "kb0 dashboard and pass api_key=... or set KB0_API_KEY."
                )
            cloud_url = self._cloud_url or os.environ.get("KB0_CLOUD_URL") or DEFAULT_CLOUD_URL
            self._remote = RemoteVault(cloud_url, api_key, self.agent, transport=self._transport)
            return self

        # Imported lazily so the package can be imported (and unit-tested with a
        # mock session) without the mcp SDK installed.
        from contextlib import AsyncExitStack

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = dict(os.environ)
        if self._extra_env:
            env.update(self._extra_env)
        if self._openai_api_key:
            env["OPENAI_API_KEY"] = self._openai_api_key
        if self._api_key:
            env["KB0_API_KEY"] = self._api_key
        if self._ingest_url:
            env["KB0_INGEST_URL"] = self._ingest_url

        args = ["serve", "--agent", self.agent, "--vault", self.vault]
        if self._strict:
            args.append("--strict")

        params = StdioServerParameters(command=self.command, args=args, env=env)

        self._stack = AsyncExitStack()
        read, write = await self._stack.enter_async_context(stdio_client(params))
        session = await self._stack.enter_async_context(ClientSession(read, write))
        await session.initialize()
        self._session = session
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        if self._remote is not None:
            await self._remote.aclose()
        self._remote = None
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self._session = None

    async def _call(self, name: str, arguments: dict[str, Any]) -> Any:
        if self._session is None:
            raise RuntimeError("VaultClient is not connected — use 'async with VaultClient(...)'")
        result = await self._session.call_tool(name, arguments)
        return _unwrap(result)

    # ── tools ──────────────────────────────────────────────────────────────────

    async def write(
        self,
        path: str,
        *,
        title: str,
        content: str,
        status: str = "draft",
        tags: list[str] | None = None,
    ) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.write(path, title=title, content=content, status=status, tags=tags)
        return await self._call(
            "vault.write",
            {"path": path, "title": title, "content": content, "status": status, "tags": tags or []},
        )

    async def read(self, path: str) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.read(path)
        return await self._call("vault.read", {"path": path})

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
        if self._remote is not None:
            return await self._remote.update(
                path, content=content, expected_hash=expected_hash, title=title, status=status, tags=tags
            )
        args: dict[str, Any] = {"path": path, "content": content, "expectedHash": expected_hash}
        if title is not None:
            args["title"] = title
        if status is not None:
            args["status"] = status
        if tags is not None:
            args["tags"] = tags
        return await self._call("vault.update", args)

    async def delete(self, path: str) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.delete(path)
        return await self._call("vault.delete", {"path": path})

    async def search(
        self,
        query: str,
        *,
        mode: str = "hybrid",
        ranking: str = "rrf",
        limit: int = 10,
        filters: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.search(query, mode=mode, ranking=ranking, limit=limit, filters=filters)
        args: dict[str, Any] = {"query": query, "mode": mode, "ranking": ranking, "limit": limit}
        if filters is not None:
            args["filters"] = filters
        return await self._call("vault.search", args)

    async def list(
        self,
        *,
        prefix: str | None = None,
        tag: str | None = None,
        status: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.list(prefix=prefix, tag=tag, status=status, limit=limit)
        args: dict[str, Any] = {"limit": limit}
        if prefix is not None:
            args["prefix"] = prefix
        if tag is not None:
            args["tag"] = tag
        if status is not None:
            args["status"] = status
        return await self._call("vault.list", args)

    async def recent(self, limit: int = 10) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.recent(limit)
        return await self._call("vault.recent", {"limit": limit})

    async def backlinks(self, path: str) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.backlinks(path)
        return await self._call("vault.backlinks", {"path": path})

    async def links(self, path: str) -> dict[str, Any]:
        if self._remote is not None:
            return await self._remote.links(path)
        return await self._call("vault.links", {"path": path})

    async def status(self) -> dict[str, Any]:
        if self._remote is not None:
            from . import __version__

            return await self._remote.status(self.vault, __version__)
        return await self._call("vault.status", {})
