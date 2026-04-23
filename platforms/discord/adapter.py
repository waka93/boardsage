import asyncio
import fcntl
import hashlib
import os
import sys
import time
from collections import defaultdict, deque

import discord

from core.engine import RulesEngine

MAX_HISTORY = 20
DISCORD_MSG_LIMIT = 2000
_EMBED_DESC_LIMIT = 4096
_DEDUP_CACHE_SIZE = 1000
_STATUS_EDIT_INTERVAL = 2.0
_MAX_VISIBLE_STEPS = 15

_TRANSIENT_STATUSES = frozenset({"Thinking...", "Generating answer..."})


class DiscordAdapter:
    def __init__(self, engine: RulesEngine, discord_token: str) -> None:
        self._engine = engine
        self._token = discord_token
        self._history: dict[int, list] = defaultdict(list)
        self._processed: deque = deque(maxlen=_DEDUP_CACHE_SIZE)

        intents = discord.Intents.default()
        intents.message_content = True
        self._client = discord.Client(intents=intents)

        @self._client.event
        async def on_ready():
            print(f"Logged in as {self._client.user}")

        @self._client.event
        async def on_message(message: discord.Message):
            await self._handle_message(message)

    async def _handle_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if self._client.user not in message.mentions:
            return
        if message.id in self._processed:
            return
        self._processed.append(message.id)

        text = message.content.replace(f"<@{self._client.user.id}>", "").strip()
        if not text:
            await message.reply("Yes? Ask me a board game rules question!")
            return

        print(f"[{message.author}] {text}")
        channel_id = message.channel.id
        self._history[channel_id].append({"role": "user", "content": text})

        loop = asyncio.get_running_loop()
        status_msg = await message.reply("_Thinking..._")

        steps: list[str] = []
        last_edit = [0.0]

        def update_status(status_text: str) -> None:
            if status_text not in _TRANSIENT_STATUSES:
                steps.append(status_text)

            visible = steps[-_MAX_VISIBLE_STEPS:]
            lines = [f"> {s}" for s in visible]
            if len(steps) > _MAX_VISIBLE_STEPS:
                lines.insert(0, f"_...and {len(steps) - _MAX_VISIBLE_STEPS} earlier steps_")
            if status_text in _TRANSIENT_STATUSES:
                lines.append(f"\n_{status_text}_")
            content = "\n".join(lines) or f"_{status_text}_"

            now = time.monotonic()
            if now - last_edit[0] < _STATUS_EDIT_INTERVAL:
                return
            last_edit[0] = now
            try:
                asyncio.run_coroutine_threadsafe(
                    status_msg.edit(content=content), loop
                ).result(timeout=5)
            except Exception:
                pass

        try:
            reply = await loop.run_in_executor(
                None, lambda: self._engine.ask(list(self._history[channel_id]), update_status)
            )
        except Exception as e:
            print(f"Error: {e}")
            await status_msg.edit(content=f"Sorry, something went wrong: {e}")
            return

        self._history[channel_id].append({"role": "assistant", "content": reply})

        if len(self._history[channel_id]) > MAX_HISTORY * 2:
            self._history[channel_id] = self._history[channel_id][-MAX_HISTORY * 2:]

        if len(reply) <= DISCORD_MSG_LIMIT:
            await status_msg.edit(content=reply)
        else:
            desc = reply if len(reply) <= _EMBED_DESC_LIMIT else reply[:_EMBED_DESC_LIMIT - 20] + "\n\n_(truncated)_"
            await status_msg.edit(content="", embed=discord.Embed(description=desc))

    def run(self) -> None:
        self._client.run(self._token)


def main() -> None:
    token = os.environ["DISCORD_TOKEN"]

    lock_id = hashlib.md5(token.encode()).hexdigest()[:8]
    lock_file = open(f"/tmp/boardsage-{lock_id}.lock", "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        sys.exit("ERROR: Another BoardSage instance is already running.")
    lock_file.write(str(os.getpid()))
    lock_file.flush()

    engine = RulesEngine()
    adapter = DiscordAdapter(engine, token)
    adapter.run()


if __name__ == "__main__":
    main()
