import os
import re
import discord
import anthropic
from collections import defaultdict
from pathlib import Path

DISCORD_TOKEN = os.environ["DISCORD_TOKEN"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

KNOWLEDGE_BASE = Path(__file__).parent.parent / "assets"  # game folders live here
MAX_HISTORY = 20

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
anthropic_client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

history: dict[int, list] = defaultdict(list)

SYSTEM_PROMPT = """You are a board game rules expert assistant in a Discord server.
When users ask about board game rules, mechanics, or rulings, use the search_rulebook tool \
to find relevant passages from official rulebooks, FAQs, and errata.
Always cite the source file and page number when answering rules questions.
If the rulebook is silent or ambiguous, say so clearly."""

TOOLS = [
    {
        "name": "search_rulebook",
        "description": (
            "Search the local rulebook, FAQ, and errata text files for a board game. "
            "Returns matching lines with surrounding context. Always use this for rules questions."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "The board game name (e.g. 'Grimcoven'). Matched to a local folder.",
                },
                "query": {
                    "type": "string",
                    "description": "Keyword or phrase to search for (case-insensitive).",
                },
            },
            "required": ["game", "query"],
        },
    }
]


def find_game_folder(game: str) -> Path | None:
    normalized = re.sub(r"[\s\W_]+", "", game).lower()
    for folder in KNOWLEDGE_BASE.iterdir():
        if not folder.is_dir():
            continue
        folder_norm = re.sub(r"[\s\W_]+", "", folder.name).lower()
        if folder_norm == normalized or normalized in folder_norm:
            return folder
    return None


def search_rulebook(game: str, query: str) -> str:
    folder = find_game_folder(game)
    if not folder:
        return f"No rulebook data found for '{game}'."

    pattern = re.compile(re.escape(query), re.IGNORECASE)
    results = []
    context_lines = 4

    for txt_file in sorted(folder.glob("*.txt")):
        lines = txt_file.read_text(errors="replace").splitlines()
        for i, line in enumerate(lines):
            if pattern.search(line):
                start = max(0, i - context_lines)
                end = min(len(lines), i + context_lines + 1)
                snippet = "\n".join(lines[start:end])
                results.append(f"[{txt_file.name}, around line {i + 1}]\n{snippet}")

    if not results:
        return f"No matches for '{query}' in {folder.name} rulebook files."

    return "\n\n---\n\n".join(results[:5])  # cap at 5 snippets


def run_with_tools(messages: list, update_status=None) -> str:
    """Agentic loop: call Claude, execute tool calls, repeat until final answer."""
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
                if block.type == "tool_use":
                    game = block.input["game"]
                    query = block.input["query"]
                    print(f"  [tool] search_rulebook(game={game!r}, query={query!r})")
                    status(f"Searching **{game}** rulebook for *{query}*...")
                    result = search_rulebook(game, query)
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

    await status_msg.edit(content=reply)
    history[channel_id].append({"role": "assistant", "content": reply})

    if len(history[channel_id]) > MAX_HISTORY * 2:
        history[channel_id] = history[channel_id][-MAX_HISTORY * 2:]

    if len(reply) <= 2000:
        await message.reply(reply)
    else:
        for i in range(0, len(reply), 2000):
            await message.channel.send(reply[i:i+2000])


client.run(DISCORD_TOKEN)
