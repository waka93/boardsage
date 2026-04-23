import asyncio
import os
from collections import defaultdict

import discord

from core.engine import RulesEngine

MAX_HISTORY = 20
DISCORD_CHUNK = 2000


class DiscordAdapter:
    def __init__(self, engine: RulesEngine, discord_token: str) -> None:
        self._engine = engine
        self._token = discord_token
        self._history: dict[int, list] = defaultdict(list)

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

        text = message.content.replace(f"<@{self._client.user.id}>", "").strip()
        if not text:
            await message.reply("Yes? Ask me a board game rules question!")
            return

        print(f"[{message.author}] {text}")
        channel_id = message.channel.id
        self._history[channel_id].append({"role": "user", "content": text})

        loop = asyncio.get_running_loop()
        status_msg = await message.reply("_Thinking..._")

        def update_status(status_text: str) -> None:
            asyncio.run_coroutine_threadsafe(
                status_msg.edit(content=f"_{status_text}_"), loop
            ).result()

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

        if len(reply) <= DISCORD_CHUNK:
            await status_msg.edit(content=reply)
        else:
            await status_msg.edit(content=reply[:DISCORD_CHUNK])
            for i in range(DISCORD_CHUNK, len(reply), DISCORD_CHUNK):
                await message.channel.send(reply[i:i + DISCORD_CHUNK])

    def run(self) -> None:
        self._client.run(self._token)


def main() -> None:
    engine = RulesEngine()
    adapter = DiscordAdapter(engine, os.environ["DISCORD_TOKEN"])
    adapter.run()


if __name__ == "__main__":
    main()
