# kb0 (Python client)

Native Python client for [**kb0**](https://github.com/vitorchristoval/kb0) — the knowledge base layer for AI agents. Markdown-first, git-backed, MCP-native.

This package hides the MCP plumbing and gives you a clean async `VaultClient`.

```bash
pip install kb0-mcp
```

> The PyPI package is `kb0-mcp`; you still `import kb0` in code.
>
> **Requires the kb0 server.** This is a client — it spawns the `kb0` binary as a
> subprocess. Install it once with Node: `npm install -g kb0-mcp`, then `kb0 init my-vault`.

## Quickstart

```python
import asyncio
from kb0 import VaultClient

async def main():
    async with VaultClient(vault="./my-vault", agent="my-bot") as kb:
        # write a note (author, id, timestamps set by the server)
        res = await kb.write(
            "notes/auth.md",
            title="Auth: we chose JWT",
            content="Short-lived access + refresh tokens. See [[notes/security.md]].",
            tags=["security"],
        )
        print("created", res["path"], "hash", res["hash"])

        # search (hybrid by default)
        hits = await kb.search("authentication design")
        for r in hits["results"]:
            print(r["score"], r["title"], r["path"])

        # read + update with optimistic locking
        note = await kb.read("notes/auth.md")
        await kb.update(
            "notes/auth.md",
            content=note["content"] + "\n\nUpdate: rotating keys monthly.",
            expected_hash=note["hash"],
        )

asyncio.run(main())
```

## Hosted vault (kb0 cloud)

Point the same client at a `kb0://` address with your API key and the vault
lives on the kb0 cloud — no local files, no `kb0` engine install, pure Python
over HTTPS. Same methods; every operation lands in your audit trail:

```python
async with VaultClient(
    vault="kb0://team-kb",
    agent="my-bot",
    api_key="kb0_live_...",   # create one in the kb0 dashboard (or set KB0_API_KEY)
) as kb:
    await kb.write("notes/idea.md", title="Idea", content="...")
    hits = await kb.search("idea")          # hosted keyword search
    graph = await kb.backlinks("notes/idea.md")
```

On a **local** vault, passing `api_key=` (or setting `KB0_API_KEY`) enables
content-free audit forwarding to the same dashboard.

## Configuration

`VaultClient` passes everything through to `kb0 serve`:

```python
VaultClient(
    vault="./my-vault",       # vault directory
    agent="my-bot",           # agent identity (provenance + ACL)
    openai_api_key="sk-...",  # optional — enables semantic search
    strict=False,             # require .vault-policy.yaml if True
    command="kb0",            # override the binary path if needed
    env={"KB0_EMBEDDING_MODEL": "text-embedding-3-large"},
    api_key="kb0_live_...",   # optional — audit forwarding / hosted vaults
    cloud_url=None,           # override the kb0 cloud base URL (kb0:// vaults)
)
```

## API

All methods are async and return plain dicts (the tool's structured output).

| Method | kb0 tool |
|---|---|
| `await kb.write(path, *, title, content, status="draft", tags=None)` | `vault.write` |
| `await kb.read(path)` | `vault.read` |
| `await kb.update(path, *, content, expected_hash, title=None, status=None, tags=None)` | `vault.update` |
| `await kb.delete(path)` | `vault.delete` |
| `await kb.search(query, *, mode="hybrid", ranking="rrf", limit=10, filters=None)` | `vault.search` |
| `await kb.list(*, prefix=None, tag=None, status=None, limit=50)` | `vault.list` |
| `await kb.recent(limit=10)` | `vault.recent` |
| `await kb.backlinks(path)` | `vault.backlinks` |
| `await kb.links(path)` | `vault.links` |
| `await kb.status()` | `vault.status` |

## Errors

Failures raise typed exceptions you can catch:

```python
from kb0 import KbConflictError, KbACLDeniedError, KbNotFoundError

try:
    await kb.update("notes/x.md", content="...", expected_hash=stale_hash)
except KbConflictError:
    note = await kb.read("notes/x.md")   # re-read, get the current hash, retry
```

`KbError` is the base class; `KbNotFoundError`, `KbConflictError`,
`KbValidationError`, and `KbACLDeniedError` are its subclasses.

## Why a subprocess?

kb0 is an MCP server. The Python client launches `kb0 serve` over stdio and speaks
MCP to it — the same protocol Claude Desktop uses. Your vault stays a local folder
of markdown under git; this client is just an ergonomic way for Python agents to
talk to it. See the [main repo](https://github.com/vitorchristoval/kb0) for the
architecture.

## Audit log

Every call you make through the client is recorded server-side to
`<vault>/.vault-index/kb0.log` as content-free JSON lines — the path read, the
query searched, and the paths a search returned, never the note bodies. Failed
and denied calls are logged too, so you can audit what an agent read and
searched, not just what it changed (git already covers writes).

## License

Apache 2.0.
