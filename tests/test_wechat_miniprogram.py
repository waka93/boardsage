"""
QE test suite for wechat-miniprogram feature.
Maps each test to an acceptance criterion (AC-N) from the PRD.
"""
import ast
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import AioHTTPTestCase

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))

from core.engine import RulesEngine
from platforms.wechat.adapter import MAX_HISTORY, WECHAT_MSG_LIMIT, WeChatAdapter


# ---------------------------------------------------------------------------
# AC-6  Pure unit tests for _chunk_reply (no HTTP required)
# ---------------------------------------------------------------------------

class TestAC6ChunkReply(unittest.TestCase):

    def test_short_text_single_chunk_no_suffix(self):
        """TC-1 | AC-6: text ≤ WECHAT_MSG_LIMIT returns one chunk with no label."""
        result = WeChatAdapter._chunk_reply("short answer")
        self.assertEqual(result, ["short answer"])

    def test_exactly_at_limit_single_chunk(self):
        """TC-2 | AC-6: text of exactly WECHAT_MSG_LIMIT chars → single unlabelled chunk."""
        text = "x" * WECHAT_MSG_LIMIT
        result = WeChatAdapter._chunk_reply(text)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], text)

    def test_over_limit_produces_multiple_chunks(self):
        """TC-3 | AC-6: text > WECHAT_MSG_LIMIT → at least 2 chunks."""
        text = "x" * (WECHAT_MSG_LIMIT + 1)
        result = WeChatAdapter._chunk_reply(text)
        self.assertGreater(len(result), 1)

    def test_chunks_each_within_limit(self):
        """TC-4 | AC-6: every chunk (body + label) is ≤ WECHAT_MSG_LIMIT chars."""
        text = "x" * (WECHAT_MSG_LIMIT * 3)
        result = WeChatAdapter._chunk_reply(text)
        for chunk in result:
            self.assertLessEqual(len(chunk), WECHAT_MSG_LIMIT)

    def test_two_chunk_labels_correct(self):
        """TC-5 | AC-6: 2-chunk split has (1/2) and (2/2) labels."""
        # WECHAT_MSG_LIMIT + 1 chars produces exactly 2 chunks
        text = "x" * (WECHAT_MSG_LIMIT + 1)
        result = WeChatAdapter._chunk_reply(text)
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0].endswith(" (1/2)"))
        self.assertTrue(result[1].endswith(" (2/2)"))

    def test_content_preserved_across_chunks(self):
        """TC-6 | AC-6: rejoining chunk bodies (stripping labels) recovers original text."""
        text = "ab" * 2000  # 4000 chars, splits into multiple chunks
        result = WeChatAdapter._chunk_reply(text)
        self.assertGreater(len(result), 1)
        total = len(result)
        bodies = []
        for i, chunk in enumerate(result):
            suffix = f" ({i + 1}/{total})"
            self.assertTrue(chunk.endswith(suffix))
            bodies.append(chunk[: -len(suffix)])
        self.assertEqual("".join(bodies), text)


# ---------------------------------------------------------------------------
# AC-7  Import isolation — AST checks, no HTTP required
# ---------------------------------------------------------------------------

class TestAC7ImportIsolation(unittest.TestCase):

    def _adapter_imports(self) -> list[str]:
        src = (REPO_ROOT / "platforms" / "wechat" / "adapter.py").read_text()
        tree = ast.parse(src)
        names = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    names.append(node.module.split(".")[0])
        return names

    def test_tc7_no_discord_import_in_wechat_adapter(self):
        """TC-7 | AC-7: platforms/wechat/adapter.py must not import discord."""
        self.assertNotIn("discord", self._adapter_imports())

    def test_tc8_no_anthropic_import_in_wechat_adapter(self):
        """TC-8 | AC-7: platforms/wechat/adapter.py must not import anthropic."""
        self.assertNotIn("anthropic", self._adapter_imports())

    def test_tc9_core_engine_unchanged(self):
        """TC-9 | AC-7: core/engine.py contains no wechat-related symbols."""
        src = (REPO_ROOT / "core" / "engine.py").read_text()
        self.assertNotIn("wechat", src.lower())

    def test_tc10_adapter_file_exists(self):
        """TC-10 | AC-7: platforms/wechat/adapter.py exists."""
        self.assertTrue((REPO_ROOT / "platforms" / "wechat" / "adapter.py").exists())

    def test_tc11_init_file_exists(self):
        """TC-11 | AC-7: platforms/wechat/__init__.py exists."""
        self.assertTrue((REPO_ROOT / "platforms" / "wechat" / "__init__.py").exists())

    def test_tc12_backend_shim_exists(self):
        """TC-12 | AC-7: wechat-backend/backend.py exists and references the adapter."""
        shim = REPO_ROOT / "wechat-backend" / "backend.py"
        self.assertTrue(shim.exists())
        src = shim.read_text()
        self.assertIn("platforms.wechat.adapter", src)
        self.assertIn("main", src)


# ---------------------------------------------------------------------------
# HTTP integration tests (AC-1 through AC-5, AC-8)
# ---------------------------------------------------------------------------

class TestWeChatAdapterHTTP(AioHTTPTestCase):

    async def get_application(self):
        self.mock_engine = MagicMock(spec=RulesEngine)
        self.mock_engine.ask.return_value = "Cited answer [Source: Rulebook p.1]"
        self.adapter = WeChatAdapter(self.mock_engine, "test_app_id", "test_secret")
        return self.adapter.app

    async def _create_session(self, openid: str = "test_openid") -> str:
        with patch.object(self.adapter, "_exchange_code", new=AsyncMock(return_value=openid)):
            resp = await self.client.post("/wechat/session", json={"code": "wx_code"})
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        return data["session_token"]

    # AC-1: Session stability

    async def test_tc13_session_returns_token(self):
        """TC-13 | AC-1: POST /wechat/session returns a non-empty session_token."""
        token = await self._create_session()
        self.assertIsInstance(token, str)
        self.assertGreater(len(token), 16)

    async def test_tc14_same_openid_yields_different_tokens(self):
        """TC-14 | AC-1: two logins with same openid produce different session tokens."""
        with patch.object(self.adapter, "_exchange_code", new=AsyncMock(return_value="same_openid")):
            r1 = await self.client.post("/wechat/session", json={"code": "code_a"})
            r2 = await self.client.post("/wechat/session", json={"code": "code_b"})
        t1 = (await r1.json())["session_token"]
        t2 = (await r2.json())["session_token"]
        self.assertNotEqual(t1, t2)

    async def test_tc15_missing_code_returns_400(self):
        """TC-15 | AC-1: POST /wechat/session without 'code' → 400."""
        resp = await self.client.post("/wechat/session", json={})
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertIn("code", data["error"])

    async def test_tc16_wechat_error_returns_401(self):
        """TC-16 | AC-1: WeChat code2session failure → 401 with descriptive error."""
        with patch.object(
            self.adapter, "_exchange_code", new=AsyncMock(side_effect=ValueError("invalid code"))
        ):
            resp = await self.client.post("/wechat/session", json={"code": "bad"})
        self.assertEqual(resp.status, 401)
        data = await resp.json()
        self.assertIn("WeChat login failed", data["error"])

    # AC-2: Cited answer returned

    async def test_tc17_chat_returns_cited_answer(self):
        """TC-17 | AC-2: /wechat/chat returns chunks containing the engine's cited reply."""
        token = await self._create_session()
        resp = await self.client.post(
            "/wechat/chat",
            json={"question": "How do I move?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status, 200)
        data = await resp.json()
        self.assertIn("chunks", data)
        self.assertIsInstance(data["chunks"], list)
        self.assertGreater(len(data["chunks"]), 0)
        full = "".join(data["chunks"])
        self.assertIn("[Source:", full)

    # AC-3: Follow-up uses history

    async def test_tc18_followup_includes_prior_turn_in_history(self):
        """TC-18 | AC-3: second engine.ask() call receives history with prior user+assistant turns."""
        token = await self._create_session()
        headers = {"Authorization": f"Bearer {token}"}

        await self.client.post("/wechat/chat", json={"question": "First question"}, headers=headers)
        await self.client.post("/wechat/chat", json={"question": "Follow-up"}, headers=headers)

        self.assertEqual(self.mock_engine.ask.call_count, 2)
        second_messages = self.mock_engine.ask.call_args_list[1][0][0]
        roles = [m["role"] for m in second_messages]
        self.assertIn("user", roles)
        self.assertIn("assistant", roles)
        self.assertGreaterEqual(len(second_messages), 3)  # user, assistant, user (follow-up)

    # AC-4: History isolation

    async def test_tc19_history_isolated_per_openid(self):
        """TC-19 | AC-4: two users have separate, non-overlapping histories."""
        token_a = await self._create_session(openid="user_alice")
        token_b = await self._create_session(openid="user_bob")

        await self.client.post(
            "/wechat/chat",
            json={"question": "Alice question"},
            headers={"Authorization": f"Bearer {token_a}"},
        )
        await self.client.post(
            "/wechat/chat",
            json={"question": "Bob question"},
            headers={"Authorization": f"Bearer {token_b}"},
        )

        history_a = self.adapter._history["user_alice"]
        history_b = self.adapter._history["user_bob"]
        self.assertEqual(len(history_a), 2)  # user + assistant
        self.assertEqual(len(history_b), 2)
        user_msgs_a = {m["content"] for m in history_a if m["role"] == "user"}
        user_msgs_b = {m["content"] for m in history_b if m["role"] == "user"}
        # Alice's question must not appear in Bob's history and vice versa
        self.assertIn("Alice question", user_msgs_a)
        self.assertIn("Bob question", user_msgs_b)
        self.assertNotIn("Alice question", user_msgs_b)
        self.assertNotIn("Bob question", user_msgs_a)

    # AC-5: Auto-add game passthrough

    async def test_tc20_engine_answer_passed_through(self):
        """TC-20 | AC-5: adapter returns whatever engine.ask() returns (auto-add transparent)."""
        self.mock_engine.ask.return_value = "Wingspan added! [Source: Wingspan Rulebook p.2]"
        token = await self._create_session()
        resp = await self.client.post(
            "/wechat/chat",
            json={"question": "How do birds work in Wingspan?"},
            headers={"Authorization": f"Bearer {token}"},
        )
        data = await resp.json()
        full = "".join(data["chunks"])
        self.assertIn("Wingspan added!", full)

    # AC-8: Duplicate session for same user

    async def test_tc21_two_sessions_same_openid_both_valid(self):
        """TC-21 | AC-8: two sessions for same openid both work; history keyed by openid."""
        token1 = await self._create_session(openid="shared_user")
        token2 = await self._create_session(openid="shared_user")
        self.assertNotEqual(token1, token2)

        for token in [token1, token2]:
            resp = await self.client.post(
                "/wechat/chat",
                json={"question": "test"},
                headers={"Authorization": f"Bearer {token}"},
            )
            self.assertEqual(resp.status, 200)

        # Both chat calls wrote to the same history (shared openid)
        self.assertEqual(len(self.adapter._history["shared_user"]), 4)

    # Auth and input validation edge cases

    async def test_tc22_missing_auth_header_returns_401(self):
        """TC-22: /wechat/chat without Authorization header → 401."""
        resp = await self.client.post("/wechat/chat", json={"question": "test"})
        self.assertEqual(resp.status, 401)

    async def test_tc23_invalid_token_returns_401(self):
        """TC-23: /wechat/chat with unknown token → 401."""
        resp = await self.client.post(
            "/wechat/chat",
            json={"question": "test"},
            headers={"Authorization": "Bearer not_a_real_token"},
        )
        self.assertEqual(resp.status, 401)

    async def test_tc24_missing_question_returns_400(self):
        """TC-24: /wechat/chat without 'question' field → 400."""
        token = await self._create_session()
        resp = await self.client.post(
            "/wechat/chat",
            json={},
            headers={"Authorization": f"Bearer {token}"},
        )
        self.assertEqual(resp.status, 400)
        data = await resp.json()
        self.assertIn("question", data["error"])

    async def test_tc25_history_capped_at_max_history_times_2(self):
        """TC-25: history trimmed to MAX_HISTORY*2 after exceeding the cap."""
        token = await self._create_session(openid="cap_user")
        headers = {"Authorization": f"Bearer {token}"}
        cap = MAX_HISTORY * 2

        for _ in range(cap + 2):
            await self.client.post("/wechat/chat", json={"question": "q"}, headers=headers)

        self.assertLessEqual(len(self.adapter._history["cap_user"]), cap)


if __name__ == "__main__":
    unittest.main(verbosity=2)
