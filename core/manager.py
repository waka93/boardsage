import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from core import bgg_fetch, reddit_fetch
from core.utils import normalize

REPO_ROOT = Path(__file__).resolve().parent.parent
KNOWLEDGE_BASE = REPO_ROOT / "assets"
CACHE_BASE = REPO_ROOT / "knowledge"


@dataclass
class GameInfo:
    slug: str
    rulebook_files: list[str]
    bgg_thread_count: int
    bgg_last_synced: str | None
    reddit_thread_count: int
    reddit_last_synced: str | None


@dataclass
class RefreshResult:
    source: str
    slug: str
    threads_before: int
    threads_after: int
    errors: list[str] = field(default_factory=list)


class KnowledgeManager:
    def __init__(
        self,
        knowledge_base: Path | None = None,
        cache_base: Path | None = None,
    ) -> None:
        self._knowledge_base = knowledge_base or KNOWLEDGE_BASE
        self._cache_base = cache_base or CACHE_BASE

    def _all_slugs(self) -> set[str]:
        slugs: set[str] = set()
        for base in (self._knowledge_base, self._cache_base):
            if base.exists():
                for d in base.iterdir():
                    if d.is_dir():
                        slugs.add(d.name)
        return slugs

    def _read_index(self, path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            return json.loads(path.read_text())
        except (json.JSONDecodeError, ValueError):
            return {}

    def _last_synced(self, index: dict) -> str | None:
        dates = [v.get("last_fetched") for v in index.values() if v.get("last_fetched")]
        return max(dates) if dates else None

    def _build_game_info(self, slug: str) -> GameInfo:
        assets_dir = self._knowledge_base / slug
        rulebook_files: list[str] = []
        if assets_dir.exists():
            rulebook_files = sorted(f.name for f in assets_dir.glob("*.txt"))

        bgg_index = self._read_index(self._cache_base / slug / "bgg" / "index.json")
        reddit_index = self._read_index(self._cache_base / slug / "reddit" / "index.json")

        return GameInfo(
            slug=slug,
            rulebook_files=rulebook_files,
            bgg_thread_count=len(bgg_index),
            bgg_last_synced=self._last_synced(bgg_index),
            reddit_thread_count=len(reddit_index),
            reddit_last_synced=self._last_synced(reddit_index),
        )

    def _resolve_slug(self, name: str) -> str | None:
        norm = normalize(name)
        for slug in self._all_slugs():
            if normalize(slug) == norm or norm in normalize(slug):
                return slug
        return None

    def list_games(self) -> list[GameInfo]:
        return [self._build_game_info(slug) for slug in sorted(self._all_slugs())]

    def get_game(self, name: str) -> GameInfo | None:
        slug = self._resolve_slug(name)
        return self._build_game_info(slug) if slug else None

    def remove_game(self, name: str) -> None:
        slug = self._resolve_slug(name)
        if slug is None:
            raise ValueError(f"Game not found: {name!r}")

        assets_dir = self._knowledge_base / slug
        cache_dir = self._cache_base / slug

        if assets_dir.exists():
            shutil.rmtree(assets_dir)
        if cache_dir.exists():
            shutil.rmtree(cache_dir)

    def refresh_bgg(
        self,
        name: str,
        progress: Callable[[str], None] | None = None,
    ) -> RefreshResult:
        slug = self._resolve_slug(name)
        if slug is None:
            raise ValueError(f"Game not found: {name!r}")

        def _p(msg: str) -> None:
            if progress:
                progress(msg)

        cache_dir = str(self._cache_base / slug / "bgg")
        index_path = self._cache_base / slug / "bgg" / "index.json"
        threads_before = len(self._read_index(index_path))
        errors: list[str] = []

        _p(f"Looking up {slug} on BoardGameGeek...")
        game_info = bgg_fetch.lookup_game(name)
        bgg_id = game_info.get("bgg_id")
        if not bgg_id:
            return RefreshResult(
                source="bgg",
                slug=slug,
                threads_before=threads_before,
                threads_after=threads_before,
                errors=["Could not find game on BoardGameGeek"],
            )

        _p("Fetching forums...")
        forums = bgg_fetch.get_forums(bgg_id)
        rules_forum = next(
            (v for k, v in forums.items() if "rule" in k.lower() or "general" in k.lower()),
            None,
        )
        if not rules_forum:
            return RefreshResult(
                source="bgg",
                slug=slug,
                threads_before=threads_before,
                threads_after=threads_before,
                errors=["No rules/general forum found on BGG"],
            )

        _p("Fetching thread list...")
        threads = bgg_fetch.get_forum_threads(rules_forum["forumid"])

        for i, t in enumerate(threads, 1):
            tid = t["thread_id"]
            _p(f"Checking thread {i}/{len(threads)}: {str(t['subject'])[:50]}...")
            try:
                stale = bgg_fetch.check_stale(tid, cache_dir).get("stale", True)
                if stale:
                    bgg_fetch.fetch_thread(tid, cache_dir)
                    bgg_fetch.update_index(cache_dir, tid, t["subject"], t["numposts"])
            except Exception as e:
                errors.append(f"Thread {tid}: {e}")

        threads_after = len(self._read_index(index_path))
        return RefreshResult(
            source="bgg",
            slug=slug,
            threads_before=threads_before,
            threads_after=threads_after,
            errors=errors,
        )

    def refresh_reddit(
        self,
        name: str,
        progress: Callable[[str], None] | None = None,
    ) -> RefreshResult:
        slug = self._resolve_slug(name)
        if slug is None:
            raise ValueError(f"Game not found: {name!r}")

        def _p(msg: str) -> None:
            if progress:
                progress(msg)

        cache_dir = str(self._cache_base / slug / "reddit")
        index_path = self._cache_base / slug / "reddit" / "index.json"
        threads_before = len(self._read_index(index_path))
        errors: list[str] = []

        game_sub = re.sub(r"[\s\W_]+", "", name)
        seen_ids: set[str] = set()

        for subreddit in ["boardgames", game_sub]:
            _p(f"Searching r/{subreddit}...")
            try:
                results = reddit_fetch.search_subreddit(subreddit, name, limit=5)
            except Exception as e:
                errors.append(f"Search r/{subreddit}: {e}")
                continue

            for t in results:
                tid = t["thread_id"]
                if tid in seen_ids:
                    continue
                seen_ids.add(tid)
                _p(f"Fetching thread: {str(t['subject'])[:50]}...")
                try:
                    reddit_fetch.fetch_thread(tid, subreddit, cache_dir)
                    reddit_fetch.update_index(
                        cache_dir, tid, t["subject"], subreddit, t["num_comments"]
                    )
                except Exception as e:
                    errors.append(f"Thread {tid}: {e}")

        threads_after = len(self._read_index(index_path))
        return RefreshResult(
            source="reddit",
            slug=slug,
            threads_before=threads_before,
            threads_after=threads_after,
            errors=errors,
        )
