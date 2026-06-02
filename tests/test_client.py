"""Unit tests for VaultClient — uses a fake MCP session, no subprocess needed."""

import unittest
from types import SimpleNamespace

from kb0 import (
    KbACLDeniedError,
    KbConflictError,
    KbNotFoundError,
    VaultClient,
)
from kb0.errors import error_from_text


def fake_result(structured=None, text=None, is_error=False):
    content = [SimpleNamespace(text=text)] if text is not None else []
    return SimpleNamespace(structuredContent=structured, content=content, isError=is_error)


class FakeSession:
    """Records tool calls and returns a queued result per call."""

    def __init__(self):
        self.calls = []
        self.results = []

    def queue(self, result):
        self.results.append(result)

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.results.pop(0)


def connected_client():
    client = VaultClient(vault="/tmp/vault", agent="test-agent")
    client._session = FakeSession()
    return client


class WriteTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_sends_expected_arguments(self):
        kb = connected_client()
        kb._session.queue(fake_result(structured={"path": "n.md", "hash": "a" * 64, "id": "uuid"}))

        out = await kb.write("n.md", title="N", content="body", tags=["x"])

        name, args = kb._session.calls[0]
        self.assertEqual(name, "vault.write")
        self.assertEqual(args["path"], "n.md")
        self.assertEqual(args["title"], "N")
        self.assertEqual(args["tags"], ["x"])
        self.assertEqual(out["id"], "uuid")

    async def test_write_defaults_status_and_tags(self):
        kb = connected_client()
        kb._session.queue(fake_result(structured={}))
        await kb.write("n.md", title="N", content="c")
        _, args = kb._session.calls[0]
        self.assertEqual(args["status"], "draft")
        self.assertEqual(args["tags"], [])


class UpdateTests(unittest.IsolatedAsyncioTestCase):
    async def test_update_maps_expected_hash_to_camelcase(self):
        kb = connected_client()
        kb._session.queue(fake_result(structured={"path": "n.md", "hash": "b" * 64}))
        await kb.update("n.md", content="v2", expected_hash="a" * 64)
        _, args = kb._session.calls[0]
        self.assertEqual(args["expectedHash"], "a" * 64)
        self.assertNotIn("expected_hash", args)

    async def test_update_omits_unset_optional_fields(self):
        kb = connected_client()
        kb._session.queue(fake_result(structured={}))
        await kb.update("n.md", content="v2", expected_hash="h")
        _, args = kb._session.calls[0]
        self.assertNotIn("title", args)
        self.assertNotIn("status", args)


class SearchListTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_passes_mode_and_limit(self):
        kb = connected_client()
        kb._session.queue(fake_result(structured={"results": [], "warnings": []}))
        await kb.search("query", mode="keyword", limit=5)
        _, args = kb._session.calls[0]
        self.assertEqual(args["mode"], "keyword")
        self.assertEqual(args["limit"], 5)

    async def test_list_omits_unset_filters(self):
        kb = connected_client()
        kb._session.queue(fake_result(structured={"notes": [], "total": 0}))
        await kb.list(tag="work")
        _, args = kb._session.calls[0]
        self.assertEqual(args["tag"], "work")
        self.assertNotIn("prefix", args)


class ErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_not_found_raises_typed_error(self):
        kb = connected_client()
        kb._session.queue(fake_result(text="Not found: `ghost.md`", is_error=True))
        with self.assertRaises(KbNotFoundError):
            await kb.read("ghost.md")

    async def test_conflict_raises_typed_error(self):
        kb = connected_client()
        kb._session.queue(fake_result(text="Conflict at `n.md`: ...", is_error=True))
        with self.assertRaises(KbConflictError):
            await kb.update("n.md", content="x", expected_hash="wrong")

    async def test_acl_denied_raises_typed_error(self):
        kb = connected_client()
        kb._session.queue(fake_result(text="Permission denied: not allowed", is_error=True))
        with self.assertRaises(KbACLDeniedError):
            await kb.write("secret.md", title="T", content="c")

    async def test_call_without_connection_raises(self):
        kb = VaultClient(vault="/tmp/v", agent="a")
        with self.assertRaises(RuntimeError):
            await kb.read("x.md")


class UnwrapTests(unittest.IsolatedAsyncioTestCase):
    async def test_falls_back_to_json_text_when_no_structured(self):
        kb = connected_client()
        kb._session.queue(fake_result(text='{"ok": true}'))
        out = await kb.status()
        self.assertEqual(out, {"ok": True})


class ErrorMappingTests(unittest.TestCase):
    def test_prefixes_map_to_codes(self):
        self.assertIsInstance(error_from_text("Not found: x"), KbNotFoundError)
        self.assertIsInstance(error_from_text("Conflict at x"), KbConflictError)
        self.assertIsInstance(error_from_text("Permission denied: x"), KbACLDeniedError)


if __name__ == "__main__":
    unittest.main()
