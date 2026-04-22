import os
import re
import sys
import urllib.parse
import discord
import anthropic
from collections import defaultdict
from pathlib import Path

# Import bgg_fetch from the skill scripts
sys.path.insert(0, str(Path(__file__).parent.parent / ".claude/skills/boardgame-forum-search/scripts"))
import bgg_fetch

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

KNOWLEDGE_BASE = Path(__file__).parent.parent / "assets"  # game folders live here
BGG_CACHE_BASE = Path(__file__).parent.parent / "knowledge"
MAX_HISTORY = 20

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

history: dict[int, list] = defaultdict(list)

SYSTEM_PROMPT = """You are a board game rules expert assistant in a Discord server.

When users ask about board game rules:
1. Always search the official rulebook first using search_rulebook.
2. If search_rulebook returns "No rulebook data found" (game not in knowledge base), call add_game to automatically download and set it up, then call search_rulebook again.
3. If search_rulebook returns "No matches" (game exists but nothing found), fall back to search_bgg_forums.
4. Cite your source (file + page for rulebook, thread title + URL for BGG).
5. If all sources come up empty, say so honestly.

Never guess at rules. Always note when an answer comes from community discussion rather than official documents."""

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
]


def normalize(name: str) -> str:
    return re.sub(r"[\s\W_]+", "", name).lower()


def find_game_folder(game: str) -> Path | None:
    norm = normalize(game)
    for folder in KNOWLEDGE_BASE.iterdir():
        if not folder.is_dir():
            continue
        if normalize(folder.name) == norm or norm in normalize(folder.name):
            return folder
    return None


def search_rulebook(game: str, query: str) -> str:
    folder = find_game_folder(game)
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


def search_bgg_forums(game: str, query: str) -> str:
    game_slug = normalize(game)
    cache_dir = str(BGG_CACHE_BASE / game_slug / "bgg")

    # 1. Check local cache first
    keywords = re.compile("|".join(re.escape(w) for w in query.lower().split()), re.IGNORECASE)
    cached_results = _search_cached_threads(cache_dir, keywords)
    if cached_results:
        return cached_results

    # 2. Look up BGG game ID
    game_info = bgg_fetch.lookup_game(game)
    bgg_id = game_info.get("bgg_id")
    if not bgg_id:
        return f"Could not find '{game}' on BoardGameGeek."

    # 3. Get forums, find the Rules forum
    forums = bgg_fetch.get_forums(bgg_id)
    rules_forum = next(
        (v for k, v in forums.items() if "rule" in k.lower() or "general" in k.lower()),
        None,
    )
    if not rules_forum:
        return "Could not find a rules forum for this game on BGG."

    # 4. Search thread titles for keywords
    threads = bgg_fetch.get_forum_threads(rules_forum["forumid"])
    relevant = [t for t in threads if keywords.search(t["subject"])][:3]
    if not relevant:
        # broaden: just take the most recent threads
        relevant = threads[:3]

    if not relevant:
        return "No relevant BGG threads found."

    # 5. Fetch and cache each thread, collect posts
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


def _search_cached_threads(cache_dir: str, keywords: re.Pattern) -> str:
    threads_dir = Path(cache_dir) / "threads"
    if not threads_dir.exists():
        return ""
    results = []
    for f in threads_dir.glob("*.json"):
        import json
        data = json.loads(f.read_text())
        matching_posts = [p["body"] for p in data.get("posts", []) if keywords.search(p.get("body", ""))]
        if matching_posts:
            url = f"https://boardgamegeek.com/thread/{data['thread_id']}"
            results.append(f"**{data['subject']}** ({url})\n\n" + "\n\n---\n\n".join(matching_posts[:3]))
    return "\n\n====\n\n".join(results[:3])


def extract_pdf_text(pdf_path: Path) -> str:
    import pypdf
    reader = pypdf.PdfReader(str(pdf_path))
    parts = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"--- Page {i} ---\n{text}")
    return "\n\n".join(parts)


def find_rulebook_pdf_urls(game: str) -> list[str]:
    """Search DuckDuckGo for direct PDF download URLs of the game's official rulebook."""
    import urllib.request as _req
    query = urllib.parse.quote(f"{game} rulebook PDF")
    url = f"https://html.duckduckgo.com/html/?q={query}"
    req = _req.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
        "Accept": "text/html",
    })
    try:
        with _req.urlopen(req, timeout=15) as resp:
            html = resp.read().decode(errors="replace")
        # DDG wraps result URLs in uddg= query params
        uddg = re.findall(r"uddg=(https?[^&\"<>\s]+)", html)
        decoded = [urllib.parse.unquote(u) for u in uddg]
        # Keep only direct PDF links, deduplicated
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


def add_game(game: str, status) -> str:
    import urllib.request as _req
    game_slug = normalize(game)
    assets_dir = KNOWLEDGE_BASE / game_slug
    knowledge_dir = BGG_CACHE_BASE / game_slug / "bgg"
    assets_dir.mkdir(parents=True, exist_ok=True)
    knowledge_dir.mkdir(parents=True, exist_ok=True)

    # 1. Look up BGG ID (needed for forum search later)
    status(f"Looking up **{game}** on BoardGameGeek...")
    game_info = bgg_fetch.lookup_game(game)
    bgg_id = game_info.get("bgg_id")
    if bgg_id:
        print(f"  [add_game] BGG ID: {bgg_id}")
    else:
        print(f"  [add_game] BGG ID not found, continuing with PDF search")

    # 2. Find direct PDF URLs via DDG
    status(f"Searching for **{game}** rulebook PDFs...")
    pdf_urls = find_rulebook_pdf_urls(game)
    print(f"  [add_game] found {len(pdf_urls)} PDF URLs: {pdf_urls}")

    if not pdf_urls:
        msg = f"Could not find downloadable rulebook PDFs for **{game}**."
        if not bgg_id:
            msg += f" Also couldn't find it on BGG. Please add PDFs manually to `assets/{game_slug}/`."
        else:
            msg += f" BGG ID is {bgg_id} — you can browse https://boardgamegeek.com/boardgame/{bgg_id} to download the rulebook manually into `assets/{game_slug}/`."
        return msg

    # 3. Download and extract each PDF
    downloaded = []
    for i, pdf_url in enumerate(pdf_urls[:2], 1):
        filename = re.sub(r"[^\w\-.]", "_", pdf_url.split("/")[-1].split("?")[0]) or f"rulebook_{i}"
        if not filename.endswith(".pdf"):
            filename += ".pdf"
        pdf_path = assets_dir / filename
        txt_path = assets_dir / filename.replace(".pdf", ".txt")

        status(f"Downloading rulebook {i}/{min(2, len(pdf_urls))}...")
        try:
            req = _req.Request(pdf_url, headers={"User-Agent": "Mozilla/5.0"})
            with _req.urlopen(req, timeout=30) as resp:
                content = resp.read()
            pdf_path.write_bytes(content)
            print(f"  [add_game] downloaded {pdf_path} ({len(content)} bytes)")
        except Exception as e:
            print(f"  [add_game] download failed {pdf_url}: {e}")
            continue

        status(f"Extracting text from rulebook {i}...")
        try:
            text = extract_pdf_text(pdf_path)
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
        f"**{game}** is ready! Extracted {len(downloaded)} rulebook file(s): {', '.join(downloaded)}. "
        f"Searching now..."
    )


def run_with_tools(messages: list, update_status=None) -> str:
    def status(text: str):
        print(f"  [status] {text}")
        if update_status:
            update_status(text)

    while True:
        status("Thinking...")
        response = anthropic_client.messages.create(
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
                    result = search_rulebook(game, query)
                elif block.name == "add_game":
                    game = block.input["game"]
                    print(f"  [tool] add_game(game={game!r})")
                    status(f"Setting up **{game}** for the first time...")
                    result = add_game(game, status)
                elif block.name == "search_bgg_forums":
                    game = block.input["game"]
                    query = block.input["query"]
                    print(f"  [tool] search_bgg_forums(game={game!r}, query={query!r})")
                    status(f"Searching BGG forums for **{game}** — *{query}*...")
                    result = search_bgg_forums(game, query)
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


@client.event
async def on_ready():
    print(f"Logged in as {client.user}")


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if client.user not in message.mentions:
        return

    text = message.content.replace(f"<@{client.user.id}>", "").strip()
    if not text:
        await message.reply("Yes? Ask me a board game rules question!")
        return

    print(f"[{message.author}] {text}")
    channel_id = message.channel.id
    history[channel_id].append({"role": "user", "content": text})

    import asyncio
    loop = asyncio.get_running_loop()
    status_msg = await message.reply("_Thinking..._")

    def update_status(text: str):
        asyncio.run_coroutine_threadsafe(
            status_msg.edit(content=f"_{text}_"), loop
        ).result()

    try:
        reply = await loop.run_in_executor(
            None, lambda: run_with_tools(list(history[channel_id]), update_status)
        )
    except Exception as e:
        print(f"Error: {e}")
        await status_msg.edit(content=f"Sorry, something went wrong: {e}")
        return

    history[channel_id].append({"role": "assistant", "content": reply})

    if len(history[channel_id]) > MAX_HISTORY * 2:
        history[channel_id] = history[channel_id][-MAX_HISTORY * 2:]

    if len(reply) <= 2000:
        await status_msg.edit(content=reply)
    else:
        await status_msg.edit(content=reply[:2000])
        for i in range(2000, len(reply), 2000):
            await message.channel.send(reply[i:i+2000])


client.run(DISCORD_TOKEN)
