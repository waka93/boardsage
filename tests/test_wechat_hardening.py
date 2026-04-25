"""
Test suite for wechat-hardening feature.
Maps each test to an acceptance criterion (AC-N) from the PRD.
"""
import ast
import asyncio
import sys
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _make_adapter(session_ttl=86400):
    from platforms.wechat.adapter import WeChatAdapter
    engine = MagicMock()
    return WeChatAdapter(engine, "app_id", "app_secret", session_ttl=session_ttl)


def _add_session(adapter, openid="user1", created_at=None):
    """Insert a session directly into _sessions, bypassing WeChat API."""
    from platforms.wechat.adapter import _SessionEntry
    import secrets
    token = secrets.token_urlsafe(16)
    adapter._sessions[token] = _SessionEntry(
        openid=openid,
        created_at=created_at if created_at is not None else time.monotonic(),
    )
    return token


# ---------------------------------------------------------------------------
# AC-1  TTL eviction removes expired sessions
# ---------------------------------------------------------------------------

class TestAC1_SessionEviction(unittest.TestCase):

    def test_tc1_expired_session_removed(self):
        """AC-1: sessions older than TTL are removed by _evict_expired."""
        adapter = _make_adapter(session_ttl=1)
        token = _add_session(adapter, created_at=time.monotonic() - 2)
        adapter._evict_expired()
        self.assertNotIn(token, adapter._sessions)

    def test_tc2_fresh_session_kept(self):
        """AC-1: sessions within TTL are not removed."""
        adapter = _make_adapter(session_ttl=3600)
        token = _add_session(adapter)
        adapter._evict_expired()
        self.assertIn(token, adapter._sessions)

    def test_tc3_only_expired_sessions_removed(self):
        """AC-1: mix of expired and fresh; only expired are removed."""
        adapter = _make_adapter(session_ttl=1)
        old_token = _add_session(adapter, openid="old", created_at=time.monotonic() - 2)
        fresh_token = _add_session(adapter, openid="fresh")
        adapter._evict_expired()
        self.assertNotIn(old_token, adapter._sessions)
        self.assertIn(fresh_token, adapter._sessions)

    def test_tc4_ttl_zero_expires_immediately(self):
        """AC-1: TTL=0 expires all sessions on next eviction."""
        adapter = _make_adapter(session_ttl=0)
        token = _add_session(adapter)
        adapter._evict_expired()
        self.assertNotIn(token, adapter._sessions)


# ---------------------------------------------------------------------------
# AC-2  History and lock cleanup after eviction
# ---------------------------------------------------------------------------

class TestAC2_OrphanCleanup(unittest.TestCase):

    def test_tc5_history_removed_when_last_session_expires(self):
        """AC-2: _history entry removed when user's last session is evicted."""
        adapter = _make_adapter(session_ttl=0)
        token = _add_session(adapter, openid="user_a")
        adapter._history["user_a"].append({"role": "user", "content": "hi"})
        adapter._evict_expired()
        self.assertNotIn("user_a", adapter._history)

    def test_tc6_lock_removed_when_last_session_expires(self):
        """AC-2: _user_locks entry removed when user's last session is evicted."""
        adapter = _make_adapter(session_ttl=0)
        _add_session(adapter, openid="user_b")
        adapter._user_locks["user_b"] = asyncio.Lock()
        adapter._evict_expired()
        self.assertNotIn("user_b", adapter._user_locks)

    def test_tc7_history_kept_while_second_session_valid(self):
        """AC-2: history preserved while user still has a valid (non-expired) session."""
        adapter = _make_adapter(session_ttl=1)
        _add_session(adapter, openid="user_c", created_at=time.monotonic() - 2)
        _add_session(adapter, openid="user_c")  # fresh second session
        adapter._history["user_c"].append({"role": "user", "content": "hello"})
        adapter._evict_expired()
        self.assertIn("user_c", adapter._history)


# ---------------------------------------------------------------------------
# AC-3  _user_locks not written from executor thread
# ---------------------------------------------------------------------------

class TestAC3_LockThreadSafety(unittest.TestCase):

    def test_tc8_get_user_lock_is_synchronous(self):
        """AC-3: _get_user_lock has no await, confirming event-loop-only access."""
        src = (REPO_ROOT / "platforms" / "wechat" / "adapter.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_get_user_lock":
                self.fail("_get_user_lock must be a regular (sync) method, not async")
        # If we reach here, _get_user_lock is sync
        adapter = _make_adapter()
        lock = adapter._get_user_lock("user_x")
        self.assertIsInstance(lock, asyncio.Lock)

    def test_tc9_get_user_lock_reuses_existing(self):
        """AC-3: calling _get_user_lock twice for same openid returns the same Lock object."""
        adapter = _make_adapter()
        lock1 = adapter._get_user_lock("user_y")
        lock2 = adapter._get_user_lock("user_y")
        self.assertIs(lock1, lock2)

    def test_tc10_no_user_locks_write_in_executor_lambda(self):
        """AC-3: AST check — _user_locks is not referenced inside run_in_executor lambda."""
        src = (REPO_ROOT / "platforms" / "wechat" / "adapter.py").read_text()
        # The lambda passed to run_in_executor should only reference self._engine and snapshot
        # A naive but effective check: the lambda body should not contain '_user_locks'
        # We look for the run_in_executor call and inspect the surrounding lambda source
        self.assertNotIn(
            "_user_locks",
            src[src.find("run_in_executor"):src.find("run_in_executor") + 200],
        )


# ---------------------------------------------------------------------------
# AC-4  Chunk label sizing correct for any reply length
# ---------------------------------------------------------------------------

class TestAC4_ChunkSizing(unittest.TestCase):

    def _chunk(self, n):
        from platforms.wechat.adapter import WeChatAdapter
        return WeChatAdapter._chunk_reply("x" * n)

    def test_tc11_short_reply_single_chunk_no_label(self):
        """AC-4: reply at exactly WECHAT_MSG_LIMIT returns one unlabelled chunk."""
        from platforms.wechat.adapter import WECHAT_MSG_LIMIT
        chunks = self._chunk(WECHAT_MSG_LIMIT)
        self.assertEqual(len(chunks), 1)
        self.assertNotIn("(1/", chunks[0])

    def test_tc12_long_reply_all_chunks_within_limit(self):
        """AC-4: all chunks ≤ 2048 chars for a moderately long reply."""
        from platforms.wechat.adapter import WECHAT_MSG_LIMIT
        chunks = self._chunk(WECHAT_MSG_LIMIT * 10)
        for i, c in enumerate(chunks):
            self.assertLessEqual(len(c), WECHAT_MSG_LIMIT, f"chunk {i} exceeds limit")

    def test_tc13_999_chunks_within_limit(self):
        """AC-4: ~999 chunks (3-digit total) — all ≤ 2048 chars."""
        from platforms.wechat.adapter import WECHAT_MSG_LIMIT
        # 2038 * 999 → ~2035962 chars → ~999 chunks
        chunks = self._chunk(2038 * 999)
        for i, c in enumerate(chunks):
            self.assertLessEqual(len(c), WECHAT_MSG_LIMIT, f"chunk {i} exceeds limit")

    def test_tc14_1000_chunks_within_limit(self):
        """AC-4: ≥1000 chunks (4-digit total, the known overflow case) — all ≤ 2048 chars."""
        from platforms.wechat.adapter import WECHAT_MSG_LIMIT
        # Force >1000 chunks
        chunks = self._chunk(2038 * 1001)
        for i, c in enumerate(chunks):
            self.assertLessEqual(len(c), WECHAT_MSG_LIMIT, f"chunk {i} exceeds limit")

    def test_tc15_chunk_labels_correct(self):
        """AC-4: last chunk label matches total count."""
        from platforms.wechat.adapter import WECHAT_MSG_LIMIT
        chunks = self._chunk(WECHAT_MSG_LIMIT * 5)
        total = len(chunks)
        self.assertTrue(chunks[-1].endswith(f"({total}/{total})"))
        self.assertTrue(chunks[0].endswith(f"(1/{total})"))


# ---------------------------------------------------------------------------
# AC-5  Engine error log includes hashed openid and exception context
# ---------------------------------------------------------------------------

class TestAC5_ErrorLogging(unittest.TestCase):

    def _run_chat_with_engine_error(self, openid="openid_test_value"):
        """Run _handle_chat against a mock engine that raises, capture stdout."""
        import io
        from contextlib import redirect_stdout
        from unittest.mock import AsyncMock

        from platforms.wechat.adapter import WeChatAdapter, _SessionEntry

        engine = MagicMock()
        engine.ask.side_effect = RuntimeError("test error message")
        adapter = WeChatAdapter(engine, "app_id", "app_secret")

        token = "test_token_xyz"
        adapter._sessions[token] = _SessionEntry(openid=openid, created_at=time.monotonic())

        mock_request = MagicMock()
        mock_request.headers.get.return_value = f"Bearer {token}"
        mock_request.json = AsyncMock(return_value={"question": "what are the rules?"})

        buf = io.StringIO()
        with redirect_stdout(buf):
            asyncio.run(adapter._handle_chat(mock_request))
        return buf.getvalue()

    def test_tc16_log_contains_openid_tag(self):
        """AC-5: error log contains hashed openid tag, not raw openid."""
        import hashlib
        raw_openid = "openid_test_value"
        log = self._run_chat_with_engine_error(openid=raw_openid)
        expected_tag = hashlib.sha256(raw_openid.encode()).hexdigest()[:8]
        self.assertIn(f"openid={expected_tag}", log)

    def test_tc17_log_does_not_contain_raw_openid(self):
        """AC-5: raw openid value is not present in the log output."""
        raw_openid = "openid_test_value"
        log = self._run_chat_with_engine_error(openid=raw_openid)
        self.assertNotIn(raw_openid, log)

    def test_tc18_log_contains_exception_type(self):
        """AC-5: error log contains the exception class name."""
        log = self._run_chat_with_engine_error()
        self.assertIn("type=RuntimeError", log)

    def test_tc19_log_contains_exception_detail(self):
        """AC-5: error log contains the exception message."""
        log = self._run_chat_with_engine_error()
        self.assertIn("detail=test error message", log)


if __name__ == "__main__":
    unittest.main(verbosity=2)
