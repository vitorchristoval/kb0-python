"""Unit tests for the hosted (kb0://) mode — httpx.MockTransport, no network."""

import json
import unittest

import httpx

from kb0 import (
    KbACLDeniedError,
    KbConflictError,
    KbNotFoundError,
    VaultClient,
)

CLOUD = "https://cloud.test"


def record_and_respond(calls, responder):
    """A MockTransport handler that records each request and delegates."""

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return responder(request)

    return httpx.MockTransport(handler)


def json_response(body, status=200):
    return httpx.Response(status, json=body)


def hosted(transport) -> VaultClient:
    return VaultClient(
        vault="kb0://team-kb",
        agent="bot",
        api_key="kb0_live_x",
        cloud_url=CLOUD,
        _transport=transport,
    )


NOTE = {
    "path": "notes/a.md",
    "title": "A",
    "content": "hello",
    "hash": "h1",
    "frontmatter": {"title": "A", "created": "2026-01-01T00:00:00Z"},
    "updatedAt": "2026-01-02T00:00:00Z",
}

TREE = {
    "entries": [
        {"path": "notes/a.md", "title": "A", "status": "draft", "tags": ["x"], "updatedAt": "2026-01-03T00:00:00Z"},
        {"path": "archive/b.md", "title": "B", "status": "reviewed", "tags": [], "updatedAt": "2026-01-02T00:00:00Z"},
    ]
}


class HostedModeTests(unittest.IsolatedAsyncioTestCase):
    async def test_requires_api_key(self):
        import os

        old = os.environ.pop("KB0_API_KEY", None)
        try:
            with self.assertRaises(ValueError):
                async with VaultClient(vault="kb0://team-kb", agent="bot"):
                    pass
        finally:
            if old is not None:
                os.environ["KB0_API_KEY"] = old

    async def test_sends_bearer_and_agent_headers(self):
        calls = []
        async with hosted(record_and_respond(calls, lambda r: json_response(TREE))) as kb:
            await kb.recent()
        req = calls[0]
        self.assertEqual(req.url, f"{CLOUD}/v1/vault/tree")
        self.assertEqual(req.headers["authorization"], "Bearer kb0_live_x")
        self.assertEqual(req.headers["x-kb0-agent"], "bot")
        # the kb0://team-kb name routes to the named vault server-side
        self.assertEqual(req.headers["x-kb0-vault"], "team-kb")

    async def test_write_puts_note_and_maps_output(self):
        calls = []

        def respond(request):
            body = json.loads(request.content)
            note = dict(NOTE)
            note["frontmatter"] = body["frontmatter"]
            return json_response(note, 201)

        async with hosted(record_and_respond(calls, respond)) as kb:
            out = await kb.write("notes/a.md", title="A", content="hello", tags=["x"])

        req = calls[0]
        self.assertEqual(req.method, "PUT")
        self.assertEqual(str(req.url), f"{CLOUD}/v1/vault/notes/notes/a.md")
        body = json.loads(req.content)
        self.assertEqual(body["title"], "A")
        self.assertEqual(body["frontmatter"]["author"], "bot")
        self.assertEqual(body["frontmatter"]["tags"], ["x"])
        self.assertEqual(out["path"], "notes/a.md")
        self.assertEqual(out["hash"], "h1")
        self.assertTrue(out["id"])

    async def test_read_fills_frontmatter_defaults(self):
        async with hosted(httpx.MockTransport(lambda r: json_response(NOTE))) as kb:
            out = await kb.read("notes/a.md")
        self.assertEqual(out["content"], "hello")
        self.assertEqual(out["frontmatter"]["status"], "draft")
        self.assertEqual(out["frontmatter"]["tags"], [])
        self.assertEqual(out["frontmatter"]["updated"], "2026-01-02T00:00:00Z")

    async def test_update_sends_if_match(self):
        calls = []
        async with hosted(record_and_respond(calls, lambda r: json_response(NOTE))) as kb:
            await kb.update("notes/a.md", content="v2", expected_hash="h1", status="reviewed")
        req = calls[0]
        self.assertEqual(req.headers["if-match"], "h1")
        self.assertEqual(json.loads(req.content)["frontmatter"]["status"], "reviewed")

    async def test_search_builds_query_params(self):
        calls = []
        async with hosted(
            record_and_respond(calls, lambda r: json_response({"results": [], "warnings": []}))
        ) as kb:
            await kb.search("jwt", limit=5, filters={"status": "draft", "tags": ["auth"]})
        url = calls[0].url
        self.assertEqual(url.path, "/v1/vault/search")
        self.assertEqual(url.params["q"], "jwt")
        self.assertEqual(url.params["limit"], "5")
        self.assertEqual(url.params["status"], "draft")
        self.assertEqual(url.params["tag"], "auth")

    async def test_list_filters_client_side(self):
        async with hosted(httpx.MockTransport(lambda r: json_response(TREE))) as kb:
            out = await kb.list(prefix="notes/")
        self.assertEqual(out["total"], 1)
        self.assertEqual(out["notes"][0]["path"], "notes/a.md")
        self.assertEqual(out["notes"][0]["tags"], ["x"])

    async def test_links_and_backlinks_pass_through(self):
        def respond(request):
            if request.url.path == "/v1/vault/links":
                return json_response({"path": "notes/a.md", "links": [{"path": "notes/b.md", "title": "B"}]})
            return json_response({"path": "notes/b.md", "backlinks": [{"path": "notes/a.md", "title": "A"}]})

        async with hosted(httpx.MockTransport(respond)) as kb:
            links = await kb.links("notes/a.md")
            backlinks = await kb.backlinks("notes/b.md")
        self.assertEqual(links["links"][0]["path"], "notes/b.md")
        self.assertEqual(backlinks["backlinks"][0]["title"], "A")

    async def test_status_reports_hosted_engine(self):
        async with hosted(httpx.MockTransport(lambda r: json_response(TREE))) as kb:
            st = await kb.status()
        self.assertEqual(st["notes"], 2)
        self.assertEqual(st["embedding_model"], "hosted (kb0 cloud)")
        self.assertEqual(st["vault"], "kb0://team-kb")

    async def test_error_statuses_map_to_typed_errors(self):
        def respond_404(_):
            return json_response({"error": "not_found", "message": "no note"}, 404)

        async with hosted(httpx.MockTransport(respond_404)) as kb:
            with self.assertRaises(KbNotFoundError):
                await kb.read("missing.md")

        def respond_409(_):
            return json_response({"error": "stale"}, 409)

        async with hosted(httpx.MockTransport(respond_409)) as kb:
            with self.assertRaises(KbConflictError):
                await kb.update("a.md", content="x", expected_hash="old")

        def respond_403(_):
            return json_response({"error": "missing scope"}, 403)

        async with hosted(httpx.MockTransport(respond_403)) as kb:
            with self.assertRaises(KbACLDeniedError):
                await kb.write("a.md", title="A", content="x")


if __name__ == "__main__":
    unittest.main()
