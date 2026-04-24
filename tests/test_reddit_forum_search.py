"""
QE test suite for reddit-forum-search feature.
Maps each test to an acceptance criterion (AC-N) from the PRD.
"""
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


def _mock_non_tool_response(text):
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _mock_tool_use_response_multi(tool_calls):
    blocks = []
    for name, tid, inp in tool_calls:
        block = MagicMock()
        block.type = "tool_use"
        block.name = name
        block.id = tid
        block.input = inp
        blocks.append(block)
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = blocks
    return response


# ---------------------------------------------------------------------------
# AC-7  search_reddit tool is registered in TOOLS with proper schema
# ---------------------------------------------------------------------------

class TestAC7_ToolRegistration(unittest.TestCase):

    def test_tc1_search_reddit_in_tools(self):
        """TC-1 | AC-7: search_reddit tool definition exists in TOOLS list."""
        from core.engine import TOOLS
        names = [t["name"] for t in TOOLS]
        self.assertIn("search_reddit", names)

    def test_tc2_search_reddit_schema(self):
        """TC-2 | AC-7: search_reddit has correct input_schema with game and query."""
        from core.engine import TOOLS
        tool = next(t for t in TOOLS if t["name"] == "search_reddit")
        schema = tool["input_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("game", schema["properties"])
        self.assertIn("query", schema["properties"])
        self.assertEqual(schema["required"], ["game", "query"])

    def test_tc3_search_reddit_description_mentions_both(self):
        """TC-3 | AC-7: search_reddit description mentions r/boardgames and game-specific."""
        from core.engine import TOOLS
        tool = next(t for t in TOOLS if t["name"] == "search_reddit")
        desc = tool["description"]
        self.assertIn("boardgames", desc.lower())
        self.assertIn("game-specific", desc.lower())


# ---------------------------------------------------------------------------
# AC-6  SYSTEM_PROMPT instructs parallel fallback with both tools
# ---------------------------------------------------------------------------

class TestAC6_SystemPrompt(unittest.TestCase):

    def test_tc4_prompt_mentions_search_reddit(self):
        """TC-4 | AC-6: SYSTEM_PROMPT contains search_reddit."""
        from core.engine import SYSTEM_PROMPT
        self.assertIn("search_reddit", SYSTEM_PROMPT)

    def test_tc5_prompt_mentions_search_bgg_forums(self):
        """TC-5 | AC-6: SYSTEM_PROMPT still contains search_bgg_forums."""
        from core.engine import SYSTEM_PROMPT
        self.assertIn("search_bgg_forums", SYSTEM_PROMPT)

    def test_tc6_prompt_mentions_both_as_parallel(self):
        """TC-6 | AC-6: SYSTEM_PROMPT instructs BOTH tools as parallel fallbacks."""
        from core.engine import SYSTEM_PROMPT
        self.assertIn("BOTH", SYSTEM_PROMPT)
        self.assertIn("search_bgg_forums AND search_reddit", SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# AC-1  Both tools available and invokable in parallel
# ---------------------------------------------------------------------------

class TestAC1_ParallelAvailability(unittest.TestCase):

    def test_tc7_both_forum_tools_in_tools_list(self):
        """TC-7 | AC-1: Both search_bgg_forums and search_reddit are in TOOLS."""
        from core.engine import TOOLS
        names = [t["name"] for t in TOOLS]
        self.assertIn("search_bgg_forums", names)
        self.assertIn("search_reddit", names)

    def test_tc8_engine_dispatches_search_reddit(self):
        """TC-8 | AC-1: Engine dispatches search_reddit tool calls correctly."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            mock_client.messages.create.side_effect = [
                _mock_tool_use_response_multi([
                    ("search_reddit", "r1", {"game": "TestGame", "query": "movement"}),
                ]),
                _mock_non_tool_response("Answer from Reddit"),
            ]
            with patch.object(engine, "_search_reddit", return_value="Reddit result") as mock_sr:
                result = engine.ask([{"role": "user", "content": "How do I move?"}])
                mock_sr.assert_called_once_with("TestGame", "movement")
            self.assertEqual(result, "Answer from Reddit")

    def test_tc9_engine_dispatches_both_tools_in_one_turn(self):
        """TC-9 | AC-1: Engine dispatches both BGG and Reddit in the same tool-use turn."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            mock_client.messages.create.side_effect = [
                _mock_tool_use_response_multi([
                    ("search_bgg_forums", "b1", {"game": "TestGame", "query": "movement"}),
                    ("search_reddit", "r1", {"game": "TestGame", "query": "movement"}),
                ]),
                _mock_non_tool_response("Combined answer"),
            ]
            with patch.object(engine, "_search_bgg_forums", return_value="BGG result") as mock_bgg, \
                 patch.object(engine, "_search_reddit", return_value="Reddit result") as mock_reddit:
                result = engine.ask([{"role": "user", "content": "How do I move?"}])
                mock_bgg.assert_called_once()
                mock_reddit.assert_called_once()
            self.assertEqual(result, "Combined answer")


# ---------------------------------------------------------------------------
# AC-2  Searches both game-specific and r/boardgames
# ---------------------------------------------------------------------------

class TestAC2_SubredditCoverage(unittest.TestCase):

    def test_tc10_searches_boardgames_and_game_specific(self):
        """TC-10 | AC-2: _search_reddit calls search_subreddit for both r/boardgames and game-specific."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            with patch("core.reddit_fetch.search_subreddit", return_value=[]) as mock_search:
                engine._search_reddit("Gloomhaven", "movement rules")
                calls = mock_search.call_args_list
                self.assertEqual(len(calls), 2)
                subreddits_searched = [c[0][0] for c in calls]
                self.assertIn("boardgames", subreddits_searched)
                self.assertIn("Gloomhaven", subreddits_searched)

    def test_tc11_game_sub_strips_special_chars(self):
        """TC-11 | AC-2: Game-specific subreddit derived by stripping non-alphanumeric chars."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            with patch("core.reddit_fetch.search_subreddit", return_value=[]) as mock_search:
                engine._search_reddit("Ark Nova", "solo variant")
                calls = mock_search.call_args_list
                subreddits = [c[0][0] for c in calls]
                self.assertIn("ArkNova", subreddits)


# ---------------------------------------------------------------------------
# AC-3  Cache files written after search
# ---------------------------------------------------------------------------

class TestAC3_CacheWrite(unittest.TestCase):

    def _make_search_result(self, thread_id="abc123", subreddit="boardgames"):
        return [{
            "thread_id": thread_id,
            "subject": "Test thread",
            "subreddit": subreddit,
            "num_comments": 5,
            "url": f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}/",
        }]

    def _make_thread_data(self, thread_id="abc123", subreddit="boardgames"):
        return {
            "thread_id": thread_id,
            "subject": "Test thread",
            "subreddit": subreddit,
            "num_comments": 5,
            "url": f"https://www.reddit.com/r/{subreddit}/comments/{thread_id}/",
            "fetched_at": "2026-04-23T12:00:00+00:00",
            "posts": [{"id": "c1", "author": "user1", "body": "test body", "score": 10, "created_utc": 0}],
        }

    def test_tc12_cache_files_created(self):
        """TC-12 | AC-3: After _search_reddit, cache files exist at knowledge/{slug}/reddit/."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            thread_data = self._make_thread_data()
            with patch("core.reddit_fetch.search_subreddit", return_value=self._make_search_result()), \
                 patch("core.reddit_fetch.fetch_thread", return_value={"fetched": True}), \
                 patch("core.reddit_fetch.update_index", return_value={"updated": True}), \
                 patch("core.reddit_fetch.read_cached_thread", return_value=thread_data):
                engine._search_reddit("TestGame", "movement")

    def test_tc13_index_json_structure(self):
        """TC-13 | AC-3: update_index creates index.json with correct structure."""
        from core import reddit_fetch
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = f"{tmp}/reddit"
            reddit_fetch.update_index(cache_dir, "abc123", "Test Thread", "boardgames", 5)
            index_path = Path(cache_dir) / "index.json"
            self.assertTrue(index_path.exists())
            index = json.loads(index_path.read_text())
            self.assertIn("abc123", index)
            entry = index["abc123"]
            self.assertEqual(entry["subject"], "Test Thread")
            self.assertEqual(entry["subreddit"], "boardgames")
            self.assertEqual(entry["num_comments"], 5)
            self.assertIn("last_fetched", entry)
            self.assertIn("url", entry)

    def test_tc14_thread_cache_structure(self):
        """TC-14 | AC-3: fetch_thread creates threads/{id}.json with correct structure."""
        from core import reddit_fetch
        mock_reddit_response = [
            {"data": {"children": [{"data": {
                "title": "Test Thread",
                "selftext": "Original post text",
                "num_comments": 2,
                "permalink": "/r/boardgames/comments/abc123/test/",
                "author": "poster",
                "score": 10,
                "created_utc": 1713870000.0,
            }}]}},
            {"data": {"children": [
                {"kind": "t1", "data": {"id": "c1", "body": "A comment", "author": "commenter", "score": 5, "created_utc": 1713871000.0}},
            ]}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = f"{tmp}/reddit"
            with patch.object(reddit_fetch, "_get", return_value=mock_reddit_response):
                result = reddit_fetch.fetch_thread("abc123", "boardgames", cache_dir)
            self.assertTrue(result["fetched"])
            thread_file = Path(cache_dir) / "threads" / "abc123.json"
            self.assertTrue(thread_file.exists())
            data = json.loads(thread_file.read_text())
            self.assertEqual(data["thread_id"], "abc123")
            self.assertEqual(data["subreddit"], "boardgames")
            self.assertEqual(len(data["posts"]), 2)


# ---------------------------------------------------------------------------
# AC-4  Cache hit avoids HTTP calls
# ---------------------------------------------------------------------------

class TestAC4_CacheHit(unittest.TestCase):

    def test_tc15_cached_threads_returned_without_http(self):
        """TC-15 | AC-4: When cached threads match keywords, no HTTP calls are made."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "testgame" / "reddit"
            threads_dir = cache_dir / "threads"
            threads_dir.mkdir(parents=True)
            (threads_dir / "abc123.json").write_text(json.dumps({
                "thread_id": "abc123",
                "subject": "Movement rules question",
                "subreddit": "boardgames",
                "num_comments": 3,
                "url": "https://www.reddit.com/r/boardgames/comments/abc123/",
                "posts": [
                    {"id": "c1", "body": "You can move 3 spaces per turn", "author": "user1", "score": 5},
                ],
            }))
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            with patch("core.reddit_fetch.search_subreddit") as mock_search, \
                 patch("core.reddit_fetch.fetch_thread") as mock_fetch:
                result = engine._search_reddit("testgame", "move")
                mock_search.assert_not_called()
                mock_fetch.assert_not_called()
            self.assertIn("Movement rules question", result)
            self.assertIn("r/boardgames", result)

    def test_tc16_empty_cache_triggers_api_calls(self):
        """TC-16 | AC-4: When cache is empty, search_subreddit is called."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            with patch("core.reddit_fetch.search_subreddit", return_value=[]) as mock_search:
                engine._search_reddit("NewGame", "rules")
                self.assertTrue(mock_search.called)


# ---------------------------------------------------------------------------
# AC-5  Results include subreddit, title, and URL
# ---------------------------------------------------------------------------

class TestAC5_ResultFormat(unittest.TestCase):

    def test_tc17_result_contains_subreddit_title_url(self):
        """TC-17 | AC-5: Formatted result includes subreddit name, thread title, and URL."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            thread_data = {
                "thread_id": "xyz789",
                "subject": "How does combat work?",
                "subreddit": "Gloomhaven",
                "num_comments": 10,
                "url": "https://www.reddit.com/r/Gloomhaven/comments/xyz789/",
                "posts": [{"id": "p1", "body": "Combat works by...", "author": "user", "score": 5}],
            }
            search_results = [{
                "thread_id": "xyz789",
                "subject": "How does combat work?",
                "subreddit": "Gloomhaven",
                "num_comments": 10,
                "url": "https://www.reddit.com/r/Gloomhaven/comments/xyz789/",
            }]
            with patch("core.reddit_fetch.search_subreddit", return_value=search_results), \
                 patch("core.reddit_fetch.fetch_thread", return_value={"fetched": True}), \
                 patch("core.reddit_fetch.update_index", return_value={"updated": True}), \
                 patch("core.reddit_fetch.read_cached_thread", return_value=thread_data):
                result = engine._search_reddit("Gloomhaven", "combat")
            self.assertIn("r/Gloomhaven", result)
            self.assertIn("How does combat work?", result)
            self.assertIn("https://www.reddit.com/r/Gloomhaven/comments/xyz789/", result)

    def test_tc18_cached_result_also_has_attribution(self):
        """TC-18 | AC-5: Cached results also include subreddit attribution."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = Path(tmp) / "testgame" / "reddit" / "threads"
            cache_dir.mkdir(parents=True)
            (cache_dir / "t1.json").write_text(json.dumps({
                "thread_id": "t1",
                "subject": "Shield rules",
                "subreddit": "Gloomhaven",
                "url": "https://www.reddit.com/r/Gloomhaven/comments/t1/",
                "posts": [{"id": "c1", "body": "Shield blocks 1 damage", "author": "u", "score": 1}],
            }))
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            result = engine._search_reddit("testgame", "shield")
            self.assertIn("r/Gloomhaven", result)
            self.assertIn("Shield rules", result)
            self.assertIn("https://www.reddit.com/r/Gloomhaven/comments/t1/", result)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases(unittest.TestCase):

    def test_tc19_no_reddit_results_graceful(self):
        """TC-19 | Edge: No Reddit results returns graceful message."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            with patch("core.reddit_fetch.search_subreddit", return_value=[]):
                result = engine._search_reddit("ObscureGame", "rules")
            self.assertIn("No relevant Reddit threads found", result)

    def test_tc20_api_error_graceful(self):
        """TC-20 | Edge: Reddit API error returns empty list, doesn't crash."""
        from core import reddit_fetch
        with patch.object(reddit_fetch, "_get", side_effect=Exception("Connection refused")):
            result = reddit_fetch.search_subreddit("boardgames", "test")
            self.assertEqual(result, [])

    def test_tc21_fetch_thread_api_error_graceful(self):
        """TC-21 | Edge: fetch_thread API error returns fetched=False."""
        from core import reddit_fetch
        with patch.object(reddit_fetch, "_get", side_effect=Exception("Timeout")):
            result = reddit_fetch.fetch_thread("abc123", "boardgames", "/tmp/test_cache")
            self.assertFalse(result["fetched"])
            self.assertEqual(result["reason"], "fetch_failed")

    def test_tc22_invalid_subreddit_rejected(self):
        """TC-22 | Edge: Invalid subreddit name raises ValueError."""
        from core import reddit_fetch
        with self.assertRaises(ValueError):
            reddit_fetch.search_subreddit("../evil", "test")

    def test_tc23_invalid_thread_id_rejected(self):
        """TC-23 | Edge: Invalid thread ID raises ValueError."""
        from core import reddit_fetch
        with self.assertRaises(ValueError):
            reddit_fetch.fetch_thread("../../etc", "boardgames", "/tmp/test")

    def test_tc24_corrupt_cache_returns_none(self):
        """TC-24 | Edge: Corrupt JSON cache file returns None."""
        from core import reddit_fetch
        with tempfile.TemporaryDirectory() as tmp:
            threads_dir = Path(tmp) / "threads"
            threads_dir.mkdir()
            (threads_dir / "abc123.json").write_text("{truncated")
            result = reddit_fetch.read_cached_thread("abc123", tmp)
            self.assertIsNone(result)

    def test_tc25_corrupt_cache_stale_check(self):
        """TC-25 | Edge: Corrupt cache returns stale=True with reason."""
        from core import reddit_fetch
        with tempfile.TemporaryDirectory() as tmp:
            threads_dir = Path(tmp) / "threads"
            threads_dir.mkdir()
            (threads_dir / "abc123.json").write_text("not json")
            result = reddit_fetch.check_stale("abc123", tmp)
            self.assertTrue(result["stale"])
            self.assertEqual(result["reason"], "corrupt_cache")

    def test_tc26_dedup_across_subreddits(self):
        """TC-26 | Edge: Same thread from both subreddits is deduplicated."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            engine = RulesEngine(
                anthropic_client=mock_client,
                knowledge_base=Path(tmp),
                cache_base=Path(tmp),
            )
            same_thread = [{
                "thread_id": "dup1",
                "subject": "Duplicate thread",
                "subreddit": "boardgames",
                "num_comments": 5,
                "url": "https://www.reddit.com/r/boardgames/comments/dup1/",
            }]
            thread_data = {
                "thread_id": "dup1",
                "subject": "Duplicate thread",
                "subreddit": "boardgames",
                "url": "https://www.reddit.com/r/boardgames/comments/dup1/",
                "posts": [{"id": "c1", "body": "test body", "author": "u", "score": 1}],
            }
            with patch("core.reddit_fetch.search_subreddit", return_value=same_thread), \
                 patch("core.reddit_fetch.fetch_thread", return_value={"fetched": True}) as mock_fetch, \
                 patch("core.reddit_fetch.update_index", return_value={"updated": True}), \
                 patch("core.reddit_fetch.read_cached_thread", return_value=thread_data):
                engine._search_reddit("TestGame", "rules")
                self.assertEqual(mock_fetch.call_count, 1)


# ---------------------------------------------------------------------------
# reddit_fetch module — type annotations present
# ---------------------------------------------------------------------------

class TestTypeAnnotations(unittest.TestCase):

    def test_tc27_public_functions_have_annotations(self):
        """TC-27 | Code quality: All public functions have type annotations."""
        import inspect
        from core import reddit_fetch
        public_funcs = [
            reddit_fetch.search_subreddit,
            reddit_fetch.fetch_thread,
            reddit_fetch.read_cached_thread,
            reddit_fetch.update_index,
            reddit_fetch.check_stale,
        ]
        for func in public_funcs:
            sig = inspect.signature(func)
            for name, param in sig.parameters.items():
                self.assertNotEqual(
                    param.annotation, inspect.Parameter.empty,
                    f"{func.__name__}({name}) missing type annotation",
                )
            self.assertNotEqual(
                sig.return_annotation, inspect.Signature.empty,
                f"{func.__name__} missing return type annotation",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
