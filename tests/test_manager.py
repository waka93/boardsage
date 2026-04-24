"""
Test suite for management-ui feature.
Maps each test to an acceptance criterion (AC-N) from the PRD.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_fixture(tmp: Path) -> tuple[Path, Path]:
    assets = tmp / "assets"
    knowledge = tmp / "knowledge"

    (assets / "grimcoven").mkdir(parents=True)
    (assets / "grimcoven" / "rulebook.txt").write_text("rules content")
    (assets / "grimcoven" / "faq.txt").write_text("faq content")

    bgg_dir = knowledge / "grimcoven" / "bgg"
    bgg_dir.mkdir(parents=True)
    (bgg_dir / "index.json").write_text(json.dumps({
        "123": {"subject": "Thread 1", "numposts": 5, "url": "https://boardgamegeek.com/thread/123",
                "last_fetched": "2026-04-20T10:00:00+00:00"},
        "456": {"subject": "Thread 2", "numposts": 3, "url": "https://boardgamegeek.com/thread/456",
                "last_fetched": "2026-04-20T11:00:00+00:00"},
    }))
    (bgg_dir / "threads").mkdir()

    reddit_dir = knowledge / "grimcoven" / "reddit"
    reddit_dir.mkdir(parents=True)
    (reddit_dir / "index.json").write_text(json.dumps({
        "abc123": {"subject": "Reddit Thread", "subreddit": "boardgames", "num_comments": 10,
                   "url": "https://reddit.com/r/boardgames/comments/abc123/",
                   "last_fetched": "2026-04-18T10:00:00+00:00"},
    }))
    (reddit_dir / "threads").mkdir()

    (knowledge / "arkhamhorror" / "bgg").mkdir(parents=True)
    (knowledge / "arkhamhorror" / "reddit").mkdir(parents=True)

    return assets, knowledge


def _bgg_update_index_side_effect(cache_dir, tid, subject, numposts):
    index_path = Path(cache_dir) / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[str(tid)] = {"subject": subject, "numposts": numposts, "url": "...",
                       "last_fetched": "2026-04-24T00:00:00+00:00"}
    index_path.write_text(json.dumps(index))


def _reddit_update_index_side_effect(cache_dir, tid, subject, subreddit, num_comments):
    index_path = Path(cache_dir) / "index.json"
    index_path.parent.mkdir(parents=True, exist_ok=True)
    index = json.loads(index_path.read_text()) if index_path.exists() else {}
    index[str(tid)] = {"subject": subject, "subreddit": subreddit, "num_comments": num_comments,
                       "url": "...", "last_fetched": "2026-04-24T00:00:00+00:00"}
    index_path.write_text(json.dumps(index))


# ---------------------------------------------------------------------------
# AC-1  list_games returns all games with correct stats
# ---------------------------------------------------------------------------

class TestListGames(unittest.TestCase):

    def test_tc1_list_shows_all_games(self):
        """AC-1: list_games returns GameInfo for all games in assets/ and knowledge/."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            slugs = [g.slug for g in mgr.list_games()]
            self.assertIn("grimcoven", slugs)
            self.assertIn("arkhamhorror", slugs)

    def test_tc2_knowledge_only_game_included(self):
        """AC-1: games in knowledge/ but not assets/ still appear (e.g. arkhamhorror)."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            arkham = next((g for g in mgr.list_games() if g.slug == "arkhamhorror"), None)
            self.assertIsNotNone(arkham)
            self.assertEqual(arkham.rulebook_files, [])
            self.assertEqual(arkham.bgg_thread_count, 0)

    def test_tc3_correct_counts(self):
        """AC-1: GameInfo has correct rulebook_files count and BGG/Reddit thread counts."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            g = next(x for x in mgr.list_games() if x.slug == "grimcoven")
            self.assertEqual(len(g.rulebook_files), 2)
            self.assertEqual(g.bgg_thread_count, 2)
            self.assertEqual(g.reddit_thread_count, 1)

    def test_tc4_last_synced_populated(self):
        """AC-1: bgg_last_synced is max last_fetched across index entries."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            g = next(x for x in mgr.list_games() if x.slug == "grimcoven")
            self.assertEqual(g.bgg_last_synced, "2026-04-20T11:00:00+00:00")
            self.assertEqual(g.reddit_last_synced, "2026-04-18T10:00:00+00:00")

    def test_tc5_no_games_returns_empty(self):
        """AC-1: empty assets/ and knowledge/ returns empty list."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets = Path(tmp) / "assets"
            knowledge = Path(tmp) / "knowledge"
            assets.mkdir()
            knowledge.mkdir()
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            self.assertEqual(mgr.list_games(), [])


# ---------------------------------------------------------------------------
# AC-10  get_game with normalize() matching
# ---------------------------------------------------------------------------

class TestGetGame(unittest.TestCase):

    def _mgr(self, tmp):
        from core.manager import KnowledgeManager
        assets, knowledge = _make_fixture(Path(tmp))
        return KnowledgeManager(knowledge_base=assets, cache_base=knowledge)

    def test_tc6_exact_slug(self):
        """AC-10: get_game('grimcoven') resolves correctly."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNotNone(self._mgr(tmp).get_game("grimcoven"))

    def test_tc7_mixed_case_and_spaces(self):
        """AC-10: 'Grim Coven' and 'GRIMCOVEN' both resolve to grimcoven."""
        with tempfile.TemporaryDirectory() as tmp:
            mgr = self._mgr(tmp)
            self.assertIsNotNone(mgr.get_game("Grim Coven"))
            self.assertIsNotNone(mgr.get_game("GRIMCOVEN"))

    def test_tc8_unknown_returns_none(self):
        """AC-4 / AC-8: get_game with an unknown name returns None."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(self._mgr(tmp).get_game("totally_unknown_xyz"))


# ---------------------------------------------------------------------------
# AC-2 / AC-3 / AC-4  remove_game
# ---------------------------------------------------------------------------

class TestRemoveGame(unittest.TestCase):

    def test_tc9_remove_deletes_both_dirs(self):
        """AC-2: remove_game deletes assets/<slug>/ and knowledge/<slug>/."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            mgr.remove_game("grimcoven")
            self.assertFalse((assets / "grimcoven").exists())
            self.assertFalse((knowledge / "grimcoven").exists())

    def test_tc10_remove_unknown_raises_value_error(self):
        """AC-4: remove_game raises ValueError for unknown game."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            with self.assertRaises(ValueError):
                mgr.remove_game("totally_unknown_xyz")

    def test_tc11_remove_knowledge_only_succeeds(self):
        """AC-2: remove_game works when only knowledge/ dir exists (no assets/)."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            mgr.remove_game("arkhamhorror")
            self.assertFalse((knowledge / "arkhamhorror").exists())


# ---------------------------------------------------------------------------
# AC-10  KnowledgeManager importable without CLI/platform deps
# ---------------------------------------------------------------------------

class TestImportability(unittest.TestCase):

    def test_tc12_importable_standalone(self):
        """AC-10: core.manager exports KnowledgeManager, GameInfo, RefreshResult."""
        import importlib
        module = importlib.import_module("core.manager")
        self.assertTrue(hasattr(module, "KnowledgeManager"))
        self.assertTrue(hasattr(module, "GameInfo"))
        self.assertTrue(hasattr(module, "RefreshResult"))

    def test_tc13_no_discord_in_manager(self):
        """AC-10: core/manager.py must not import discord or anthropic."""
        import ast
        src = (REPO_ROOT / "core" / "manager.py").read_text()
        tree = ast.parse(src)
        top_imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                top_imports.extend(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    top_imports.append(node.module.split(".")[0])
        self.assertNotIn("discord", top_imports)
        self.assertNotIn("anthropic", top_imports)


# ---------------------------------------------------------------------------
# AC-5  refresh_bgg
# ---------------------------------------------------------------------------

class TestRefreshBGG(unittest.TestCase):

    def test_tc14_refresh_bgg_calls_fetch_for_stale_threads(self):
        """AC-5: refresh_bgg re-fetches stale threads and returns RefreshResult."""
        from core.manager import KnowledgeManager, RefreshResult
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)

            with patch("core.manager.bgg_fetch") as mock_bgg:
                mock_bgg.lookup_game.return_value = {"bgg_id": 999}
                mock_bgg.get_forums.return_value = {
                    "Rules": {"forumid": 111, "numposts": 5, "numthreads": 2}
                }
                mock_bgg.get_forum_threads.return_value = [
                    {"thread_id": 123, "subject": "Thread 1", "numposts": 5},
                    {"thread_id": 789, "subject": "New Thread", "numposts": 1},
                ]
                mock_bgg.check_stale.return_value = {"stale": True}
                mock_bgg.fetch_thread.return_value = {"fetched": True}
                mock_bgg.update_index.side_effect = _bgg_update_index_side_effect

                result = mgr.refresh_bgg("grimcoven")

            self.assertIsInstance(result, RefreshResult)
            self.assertEqual(result.source, "bgg")
            self.assertEqual(result.slug, "grimcoven")
            self.assertEqual(result.threads_before, 2)
            self.assertEqual(mock_bgg.fetch_thread.call_count, 2)

    def test_tc15_refresh_bgg_skips_fresh_threads(self):
        """AC-5: refresh_bgg does not re-fetch threads that are not stale."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)

            with patch("core.manager.bgg_fetch") as mock_bgg:
                mock_bgg.lookup_game.return_value = {"bgg_id": 999}
                mock_bgg.get_forums.return_value = {"Rules": {"forumid": 111}}
                mock_bgg.get_forum_threads.return_value = [
                    {"thread_id": 123, "subject": "Thread 1", "numposts": 5},
                ]
                mock_bgg.check_stale.return_value = {"stale": False}

                mgr.refresh_bgg("grimcoven")

            mock_bgg.fetch_thread.assert_not_called()

    def test_tc16_refresh_bgg_not_found_raises(self):
        """AC-8: refresh_bgg raises ValueError for unknown game."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            with self.assertRaises(ValueError):
                mgr.refresh_bgg("totally_unknown_xyz")

    def test_tc17_refresh_bgg_bgg_lookup_failure(self):
        """AC-5: refresh_bgg returns error result when BGG lookup fails."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)

            with patch("core.manager.bgg_fetch") as mock_bgg:
                mock_bgg.lookup_game.return_value = {"bgg_id": None}
                result = mgr.refresh_bgg("grimcoven")

            self.assertGreater(len(result.errors), 0)
            self.assertIn("BoardGameGeek", result.errors[0])
            self.assertEqual(result.threads_after, result.threads_before)


# ---------------------------------------------------------------------------
# AC-6  refresh_reddit
# ---------------------------------------------------------------------------

class TestRefreshReddit(unittest.TestCase):

    def test_tc18_refresh_reddit_fetches_threads(self):
        """AC-6: refresh_reddit searches subreddits and fetches threads."""
        from core.manager import KnowledgeManager, RefreshResult
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)

            with patch("core.manager.reddit_fetch") as mock_reddit:
                mock_reddit.search_subreddit.return_value = [
                    {"thread_id": "abc123", "subject": "Rules Q", "subreddit": "boardgames",
                     "num_comments": 5, "url": "https://reddit.com/r/boardgames/comments/abc123/"},
                    {"thread_id": "def456", "subject": "New Q", "subreddit": "boardgames",
                     "num_comments": 2, "url": "https://reddit.com/r/boardgames/comments/def456/"},
                ]
                mock_reddit.fetch_thread.return_value = {"fetched": True}
                mock_reddit.update_index.side_effect = _reddit_update_index_side_effect

                result = mgr.refresh_reddit("grimcoven")

            self.assertIsInstance(result, RefreshResult)
            self.assertEqual(result.source, "reddit")
            self.assertEqual(result.slug, "grimcoven")
            self.assertEqual(result.threads_before, 1)

    def test_tc19_refresh_reddit_deduplicates_thread_ids(self):
        """AC-6: same thread_id from two subreddits is only fetched once."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)

            with patch("core.manager.reddit_fetch") as mock_reddit:
                mock_reddit.search_subreddit.return_value = [
                    {"thread_id": "dup1", "subject": "Q", "subreddit": "boardgames",
                     "num_comments": 1, "url": "https://reddit.com/..."},
                ]
                mock_reddit.fetch_thread.return_value = {"fetched": True}
                mock_reddit.update_index.side_effect = _reddit_update_index_side_effect

                mgr.refresh_reddit("grimcoven")

            self.assertEqual(mock_reddit.fetch_thread.call_count, 1)

    def test_tc20_refresh_reddit_not_found_raises(self):
        """AC-8: refresh_reddit raises ValueError for unknown game."""
        from core.manager import KnowledgeManager
        with tempfile.TemporaryDirectory() as tmp:
            assets, knowledge = _make_fixture(Path(tmp))
            mgr = KnowledgeManager(knowledge_base=assets, cache_base=knowledge)
            with self.assertRaises(ValueError):
                mgr.refresh_reddit("totally_unknown_xyz")


# ---------------------------------------------------------------------------
# CLI subprocess tests
# ---------------------------------------------------------------------------

class TestCLISubprocess(unittest.TestCase):

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "management.cli"] + args,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    def test_tc21_list_exits_0_and_shows_slugs(self):
        """AC-1: list command exits 0 and shows known game slugs."""
        r = self._run(["list"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("grimcoven", r.stdout)

    def test_tc22_info_shows_game_details(self):
        """AC-9: info command exits 0 and shows BGG/Reddit thread headings."""
        r = self._run(["info", "grimcoven"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("grimcoven", r.stdout)
        self.assertIn("BGG threads", r.stdout)
        self.assertIn("Reddit threads", r.stdout)

    def test_tc23_remove_unknown_exits_1(self):
        """AC-4: remove with unknown game exits 1 with error on stderr."""
        r = self._run(["remove", "--yes", "totally_unknown_game_xyz999"])
        self.assertEqual(r.returncode, 1)
        self.assertIn("not found", r.stderr.lower())

    def test_tc24_info_unknown_exits_1(self):
        """AC-9: info with unknown game exits 1."""
        r = self._run(["info", "totally_unknown_game_xyz999"])
        self.assertEqual(r.returncode, 1)

    def test_tc25_help_exits_0(self):
        """CLI: --help exits 0."""
        r = self._run(["--help"])
        self.assertEqual(r.returncode, 0)

    def test_tc26_no_subcommand_exits_nonzero(self):
        """CLI: invocation with no subcommand exits non-zero."""
        r = self._run([])
        self.assertNotEqual(r.returncode, 0)

    def test_tc27_list_shows_rulebook_and_thread_columns(self):
        """AC-1: list output includes RULEBOOK FILES and thread count columns."""
        r = self._run(["list"])
        self.assertEqual(r.returncode, 0)
        self.assertIn("RULEBOOK FILES", r.stdout)
        self.assertIn("BGG THREADS", r.stdout)
        self.assertIn("REDDIT THREADS", r.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
