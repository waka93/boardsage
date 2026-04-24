import json
import re
import urllib.parse
import urllib.request
from collections.abc import Callable
from pathlib import Path

import anthropic

from core import bgg_fetch, reddit_fetch
from core.utils import normalize

REPO_ROOT = Path(__file__).resolve().parent.parent

KNOWLEDGE_BASE = REPO_ROOT / "assets"
CACHE_BASE = REPO_ROOT / "knowledge"

SYSTEM_PROMPT = """You are a board game rules expert assistant in a Discord server.

When users ask about board game rules:
1. Always search the official rulebook first using search_rulebook.
2. If search_rulebook returns "No rulebook data found" (game not in knowledge base), call add_game to automatically download and set it up, then call search_rulebook again.
3. If search_rulebook returns "No matches" (game exists but nothing found), fall back to BOTH search_bgg_forums AND search_reddit as parallel community sources.
4. Cite your source (file + page for rulebook, thread title + URL for BGG/Reddit). Always indicate whether an answer comes from an official rulebook, BGG, or Reddit.
5. If all sources come up empty, say so honestly.

Never guess at rules. Always note when an answer comes from community discussion rather than official documents.

When the user's message does not mention a specific board game by name:
1. Call identify_game_from_web with the key terms from their question.
2. If the result lists exactly one candidate, proceed using that game — do not ask the user to confirm.
3. If the result lists multiple candidates, reply asking the user to pick one (show the numbered list).
4. If the result says no game was identified, ask the user which game they mean.
5. Once the game is known (from web search or user reply), use it for all subsequent tool calls in this conversation."""

TOOLS = [
    {
        "name": "search_rulebook",
        "description": (
            "Search the local rulebook, FAQ, and errata text files for a board game. "
            "Returns matching lines with surrounding context. Use this first for any rules question."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "Board game name (e.g. 'Grimcoven')."},
                "query": {"type": "string", "description": "Keyword or phrase to search for (case-insensitive)."},
            },
            "required": ["game", "query"],
        },
    },
    {
        "name": "add_game",
        "description": (
            "Download and set up a new board game's rulebook from BoardGameGeek. "
            "Call this when search_rulebook returns 'No rulebook data found' for a game. "
            "Creates the folder structure, downloads PDFs, and extracts text automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "Board game name to add (e.g. 'Wingspan')."},
            },
            "required": ["game"],
        },
    },
    {
        "name": "search_bgg_forums",
        "description": (
            "Search BoardGameGeek community forum threads for a board game. "
            "Use this as a fallback when search_rulebook finds no relevant results. "
            "Fetches and caches BGG threads locally."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "Board game name (e.g. 'Grimcoven')."},
                "query": {"type": "string", "description": "Keywords to search for in thread titles and posts."},
            },
            "required": ["game", "query"],
        },
    },
    {
        "name": "search_reddit",
        "description": (
            "Search Reddit board game communities for rules discussions. "
            "Searches both game-specific subreddits and r/boardgames. "
            "Use this alongside search_bgg_forums as a fallback when search_rulebook finds no relevant results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "game": {"type": "string", "description": "Board game name (e.g. 'Grimcoven')."},
                "query": {"type": "string", "description": "Keywords to search for in thread titles and posts."},
            },
            "required": ["game", "query"],
        },
    },
    {
        "name": "identify_game_from_web",
        "description": (
            "Search the web to identify which board game a user is asking about, "
            "when no game name is present in their question. "
            "Pass the key entities or terms from the user's message as the query. "
            "Returns a ranked list of candidate board games, or a not-found message."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The entity, character, item, or mechanic the user mentioned "
                        "(e.g. 'Death Jester weapon expansion'). Do not include generic words like 'board game'."
                    ),
                },
            },
            "required": ["query"],
        },
    },
]


_STRIP_TITLE_SUFFIX = re.compile(
    r"\s*[-|–]\s*(BoardGameGeek|Board Game Geek|BGG|Wikipedia|Amazon|eBay).*$",
    re.IGNORECASE,
)
_GAME_SIGNAL = re.compile(
    r"board\s*game|tabletop|miniatures\s*game|card\s*game|dice\s*game|wargame",
    re.IGNORECASE,
)


def _extract_game_candidates(titles: list[str]) -> list[str]:
    high: list[str] = []
    normal: list[str] = []
    seen: set[str] = set()
    for title in titles:
        cleaned = _STRIP_TITLE_SUFFIX.sub("", title).strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        if _GAME_SIGNAL.search(cleaned):
            high.append(cleaned)
        else:
            normal.append(cleaned)
    return (high + normal)[:4]


class RulesEngine:
    def __init__(
        self,
        anthropic_client=None,
        knowledge_base: Path | None = None,
        cache_base: Path | None = None,
    ) -> None:
        self._client = anthropic_client or anthropic.Anthropic()
        self._knowledge_base = knowledge_base or KNOWLEDGE_BASE
        self._cache_base = cache_base or CACHE_BASE

    def ask(
        self,
        messages: list[dict],
        status_callback: Callable[[str], None] | None = None,
    ) -> str:
        def status(text: str) -> None:
            print(f"  [status] {text}")
            if status_callback:
                status_callback(text)

        while True:
            status("Thinking...")
            response = self._client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=1024,
                system=SYSTEM_PROMPT,
                tools=TOOLS,
                messages=messages,
            )

            if response.stop_reason == "tool_use":
                messages.append({"role": "assistant", "content": response.content})
                tool_results = []
                for block in response.content:
                    if block.type != "tool_use":
                        continue
                    if block.name == "search_rulebook":
                        game = block.input["game"]
                        query = block.input["query"]
                        print(f"  [tool] search_rulebook(game={game!r}, query={query!r})")
                        status(f"Searching **{game}** rulebook for *{query}*...")
                        result = self._search_rulebook(game, query)
                    elif block.name == "add_game":
                        game = block.input["game"]
                        print(f"  [tool] add_game(game={game!r})")
                        status(f"Setting up **{game}** for the first time...")
                        result = self._add_game(game, status)
                    elif block.name == "search_bgg_forums":
                        game = block.input["game"]
                        query = block.input["query"]
                        print(f"  [tool] search_bgg_forums(game={game!r}, query={query!r})")
                        status(f"Searching BGG forums for **{game}** — *{query}*...")
                        result = self._search_bgg_forums(game, query)
                    elif block.name == "search_reddit":
                        game = block.input["game"]
                        query = block.input["query"]
                        print(f"  [tool] search_reddit(game={game!r}, query={query!r})")
                        status(f"Searching Reddit for **{game}** — *{query}*...")
                        result = self._search_reddit(game, query)
                    elif block.name == "identify_game_from_web":
                        query = block.input["query"]
                        print(f"  [tool] identify_game_from_web(query={query!r})")
                        status(f"Searching web to identify game for *{query}*...")
                        result = self._identify_game_from_web(query)
                    else:
                        result = f"Unknown tool: {block.name}"
                    print(f"  [tool] {len(result)} chars returned")
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": result,
                    })
                messages.append({"role": "user", "content": tool_results})
                status("Generating answer...")
            else:
                for block in response.content:
                    if hasattr(block, "text"):
                        return block.text
                return "(No response)"

    def _find_game_folder(self, game: str) -> Path | None:
        norm = normalize(game)
        for folder in self._knowledge_base.iterdir():
            if not folder.is_dir():
                continue
            if normalize(folder.name) == norm or norm in normalize(folder.name):
                return folder
        return None

    def _search_rulebook(self, game: str, query: str) -> str:
        folder = self._find_game_folder(game)
        if not folder:
            return f"No rulebook data found for '{game}'."

        pattern = re.compile(re.escape(query), re.IGNORECASE)
        results = []

        for txt_file in sorted(folder.glob("*.txt")):
            lines = txt_file.read_text(errors="replace").splitlines()
            for i, line in enumerate(lines):
                if pattern.search(line):
                    start = max(0, i - 4)
                    end = min(len(lines), i + 5)
                    snippet = "\n".join(lines[start:end])
                    results.append(f"[{txt_file.name}, around line {i + 1}]\n{snippet}")

        if not results:
            return f"No matches for '{query}' in {folder.name} rulebook files."
        return "\n\n---\n\n".join(results[:5])

    def _search_bgg_forums(self, game: str, query: str) -> str:
        game_slug = normalize(game)
        cache_dir = str(self._cache_base / game_slug / "bgg")

        keywords = re.compile("|".join(re.escape(w) for w in query.lower().split()), re.IGNORECASE)
        cached_results = self._search_cached_threads(cache_dir, keywords)
        if cached_results:
            return cached_results

        game_info = bgg_fetch.lookup_game(game)
        bgg_id = game_info.get("bgg_id")
        if not bgg_id:
            return f"Could not find '{game}' on BoardGameGeek."

        forums = bgg_fetch.get_forums(bgg_id)
        rules_forum = next(
            (v for k, v in forums.items() if "rule" in k.lower() or "general" in k.lower()),
            None,
        )
        if not rules_forum:
            return "Could not find a rules forum for this game on BGG."

        threads = bgg_fetch.get_forum_threads(rules_forum["forumid"])
        relevant = [t for t in threads if keywords.search(t["subject"])][:3]
        if not relevant:
            relevant = threads[:3]

        if not relevant:
            return "No relevant BGG threads found."

        parts = []
        for t in relevant:
            tid = t["thread_id"]
            stale = bgg_fetch.check_stale(tid, cache_dir).get("stale", True)
            if stale:
                bgg_fetch.fetch_thread(tid, cache_dir)
                bgg_fetch.update_index(cache_dir, tid, t["subject"], t["numposts"])

            data = bgg_fetch.read_cached_thread(tid, cache_dir)
            if not data:
                continue

            url = f"https://boardgamegeek.com/thread/{tid}"
            post_texts = [p["body"] for p in data.get("posts", [])[:5]]
            parts.append(f"**{data['subject']}** ({url})\n\n" + "\n\n---\n\n".join(post_texts))

        return "\n\n====\n\n".join(parts) if parts else "No thread content retrieved."

    def _search_cached_threads(self, cache_dir: str, keywords: re.Pattern) -> str:
        threads_dir = Path(cache_dir) / "threads"
        if not threads_dir.exists():
            return ""
        results = []
        for f in threads_dir.glob("*.json"):
            data = json.loads(f.read_text())
            matching_posts = [
                p["body"] for p in data.get("posts", []) if keywords.search(p.get("body", ""))
            ]
            if matching_posts:
                url = f"https://boardgamegeek.com/thread/{data['thread_id']}"
                results.append(
                    f"**{data['subject']}** ({url})\n\n" + "\n\n---\n\n".join(matching_posts[:3])
                )
        return "\n\n====\n\n".join(results[:3])

    def _search_reddit(self, game: str, query: str) -> str:
        game_slug = normalize(game)
        cache_dir = str(self._cache_base / game_slug / "reddit")

        keywords = re.compile("|".join(re.escape(w) for w in query.lower().split()), re.IGNORECASE)
        cached_results = self._search_cached_reddit_threads(cache_dir, keywords)
        if cached_results:
            return cached_results

        all_threads = []
        seen_ids = set()

        game_sub = re.sub(r"[\s\W_]+", "", game)
        for subreddit, search_query in [("boardgames", f"{game} {query}"), (game_sub, query)]:
            results = reddit_fetch.search_subreddit(subreddit, search_query)
            for t in results:
                if t["thread_id"] not in seen_ids:
                    seen_ids.add(t["thread_id"])
                    all_threads.append(t)

        relevant = [t for t in all_threads if keywords.search(t["subject"])][:3]
        if not relevant:
            relevant = all_threads[:3]

        if not relevant:
            return "No relevant Reddit threads found."

        parts = []
        for t in relevant:
            tid = t["thread_id"]
            sub = t["subreddit"]
            reddit_fetch.fetch_thread(tid, sub, cache_dir)
            reddit_fetch.update_index(cache_dir, tid, t["subject"], sub, t["num_comments"])

            data = reddit_fetch.read_cached_thread(tid, cache_dir)
            if not data:
                continue

            url = data.get("url", f"https://www.reddit.com/r/{sub}/comments/{tid}/")
            post_texts = [p["body"] for p in data.get("posts", [])[:5]]
            parts.append(f"**{data['subject']}** (r/{sub}) ({url})\n\n" + "\n\n---\n\n".join(post_texts))

        return "\n\n====\n\n".join(parts) if parts else "No Reddit thread content retrieved."

    def _identify_game_from_web(self, query: str) -> str:
        search_query = f"{query} board game"
        url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(search_query)}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "text/html",
        })
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                html = resp.read().decode(errors="replace")
        except Exception:
            return "Web search unavailable."

        raw_titles = re.findall(
            r'class="result__title"[^>]*>.*?<a[^>]*>(.*?)</a>', html, re.DOTALL
        )
        cleaned = [re.sub(r"<[^>]+>", "", t).strip() for t in raw_titles]
        candidates = _extract_game_candidates(cleaned)

        if not candidates:
            return "No board game identified from web search."
        lines = "\n".join(f"{i + 1}. {g}" for i, g in enumerate(candidates))
        return f"Candidate board games:\n{lines}"

    def _search_cached_reddit_threads(self, cache_dir: str, keywords: re.Pattern) -> str:
        threads_dir = Path(cache_dir) / "threads"
        if not threads_dir.exists():
            return ""
        results = []
        for f in threads_dir.glob("*.json"):
            data = json.loads(f.read_text())
            matching_posts = [
                p["body"] for p in data.get("posts", []) if keywords.search(p.get("body", ""))
            ]
            if matching_posts:
                sub = data.get("subreddit", "boardgames")
                url = data.get("url", f"https://www.reddit.com/r/{sub}/comments/{data['thread_id']}/")
                results.append(
                    f"**{data['subject']}** (r/{sub}) ({url})\n\n" + "\n\n---\n\n".join(matching_posts[:3])
                )
        return "\n\n====\n\n".join(results[:3])

    @staticmethod
    def _extract_pdf_text(pdf_path: Path) -> str:
        import pypdf
        reader = pypdf.PdfReader(str(pdf_path))
        parts = []
        for i, page in enumerate(reader.pages, 1):
            text = page.extract_text() or ""
            if text.strip():
                parts.append(f"--- Page {i} ---\n{text}")
        return "\n\n".join(parts)

    @staticmethod
    def _find_rulebook_pdf_urls(game: str) -> list[str]:
        query = urllib.parse.quote(f"{game} rulebook PDF")
        url = f"https://html.duckduckgo.com/html/?q={query}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
            "Accept": "text/html",
        })
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                html = resp.read().decode(errors="replace")
            uddg = re.findall(r"uddg=(https?[^&\"<>\s]+)", html)
            decoded = [urllib.parse.unquote(u) for u in uddg]
            seen = set()
            direct = []
            for u in decoded:
                if u.lower().endswith(".pdf") and u not in seen:
                    seen.add(u)
                    direct.append(u)
            return direct[:5]
        except Exception as e:
            print(f"  [add_game] DDG PDF search failed: {e}")
            return []

    def _add_game(self, game: str, status: Callable) -> str:
        game_slug = normalize(game)
        assets_dir = self._knowledge_base / game_slug
        knowledge_dir = self._cache_base / game_slug / "bgg"
        assets_dir.mkdir(parents=True, exist_ok=True)
        knowledge_dir.mkdir(parents=True, exist_ok=True)

        status(f"Looking up **{game}** on BoardGameGeek...")
        game_info = bgg_fetch.lookup_game(game)
        bgg_id = game_info.get("bgg_id")
        if bgg_id:
            print(f"  [add_game] BGG ID: {bgg_id}")
        else:
            print(f"  [add_game] BGG ID not found, continuing with PDF search")

        status(f"Searching for **{game}** rulebook PDFs...")
        pdf_urls = self._find_rulebook_pdf_urls(game)
        print(f"  [add_game] found {len(pdf_urls)} PDF URLs: {pdf_urls}")

        if not pdf_urls:
            msg = f"Could not find downloadable rulebook PDFs for **{game}**."
            if not bgg_id:
                msg += f" Also couldn't find it on BGG. Please add PDFs manually to `assets/{game_slug}/`."
            else:
                msg += (
                    f" BGG ID is {bgg_id} — you can browse "
                    f"https://boardgamegeek.com/boardgame/{bgg_id} to download the rulebook "
                    f"manually into `assets/{game_slug}/`."
                )
            return msg

        downloaded = []
        for i, pdf_url in enumerate(pdf_urls[:2], 1):
            filename = re.sub(r"[^\w\-.]", "_", pdf_url.split("/")[-1].split("?")[0]) or f"rulebook_{i}"
            if not filename.endswith(".pdf"):
                filename += ".pdf"
            pdf_path = assets_dir / filename
            txt_path = assets_dir / filename.replace(".pdf", ".txt")

            status(f"Downloading rulebook {i}/{min(2, len(pdf_urls))}...")
            try:
                req = urllib.request.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    content = resp.read()
                pdf_path.write_bytes(content)
                print(f"  [add_game] downloaded {pdf_path} ({len(content)} bytes)")
            except Exception as e:
                print(f"  [add_game] download failed {pdf_url}: {e}")
                continue

            status(f"Extracting text from rulebook {i}...")
            try:
                text = self._extract_pdf_text(pdf_path)
                txt_path.write_text(text, encoding="utf-8")
                downloaded.append(filename)
                print(f"  [add_game] extracted {txt_path} ({len(text)} chars)")
            except Exception as e:
                print(f"  [add_game] extraction failed {pdf_path}: {e}")
                pdf_path.unlink(missing_ok=True)
                continue

        if not downloaded:
            return (
                f"Found PDF URLs for **{game}** but all downloads failed. "
                f"Please add the rulebook PDF manually to `assets/{game_slug}/`."
            )

        return (
            f"**{game}** is ready! Extracted {len(downloaded)} rulebook file(s): "
            f"{', '.join(downloaded)}. Searching now..."
        )
