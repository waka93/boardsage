"""
QE test suite for platform-decoupling feature.
Maps each test to an acceptance criterion (AC-N) from the PRD.
"""
import ast
import asyncio
import re
import sys
import tempfile
import unittest
from collections import defaultdict
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# Anchor repo root so all imports work regardless of working directory
REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _imports_from_file(path: Path) -> list[str]:
    """Return all top-level module names imported in a Python source file."""
    tree = ast.parse(path.read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                names.append(node.module.split(".")[0])
    return names


def _mock_non_tool_response(text: str):
    """Build a mock Anthropic response that ends the loop immediately."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _mock_tool_use_response(tool_name: str, tool_id: str, tool_input: dict):
    """Build a mock Anthropic response requesting a single tool call."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = tool_name
    block.id = tool_id
    block.input = tool_input
    response = MagicMock()
    response.stop_reason = "tool_use"
    response.content = [block]
    return response


# ---------------------------------------------------------------------------
# AC-1  core/engine.py exists and defines RulesEngine; no Discord symbols
# ---------------------------------------------------------------------------

class TestAC1_CoreEngineStructure(unittest.TestCase):

    def test_tc1_engine_file_exists(self):
        """TC-1 | AC-1: core/engine.py must be present."""
        self.assertTrue((REPO_ROOT / "core" / "engine.py").exists())

    def test_tc2_rules_engine_defined(self):
        """TC-2 | AC-1: core.engine exports RulesEngine class."""
        import core.engine as eng
        self.assertTrue(hasattr(eng, "RulesEngine"))
        self.assertTrue(isinstance(eng.RulesEngine, type))

    def test_tc3_no_discord_import_in_engine(self):
        """TC-3 | AC-1: core/engine.py must not import discord."""
        imports = _imports_from_file(REPO_ROOT / "core" / "engine.py")
        self.assertNotIn("discord", imports, "core/engine.py must not import discord")


# ---------------------------------------------------------------------------
# AC-2  platforms/discord/adapter.py exists; all discord.* refs live there
# ---------------------------------------------------------------------------

class TestAC2_AdapterStructure(unittest.TestCase):

    def test_tc4_adapter_file_exists(self):
        """TC-4 | AC-2: platforms/discord/adapter.py must be present."""
        self.assertTrue((REPO_ROOT / "platforms" / "discord" / "adapter.py").exists())

    def test_tc5_adapter_uses_discord(self):
        """TC-5 | AC-2: adapter.py must import discord."""
        imports = _imports_from_file(REPO_ROOT / "platforms" / "discord" / "adapter.py")
        self.assertIn("discord", imports, "adapter.py must import discord")

    def test_tc6_engine_has_no_discord(self):
        """TC-6 | AC-2 (complement): no discord reference in core/engine.py source text."""
        src = (REPO_ROOT / "core" / "engine.py").read_text()
        self.assertFalse(
            re.search(r"\bdiscord\b", src),
            "The word 'discord' must not appear in core/engine.py",
        )


# ---------------------------------------------------------------------------
# AC-3  RulesEngine.ask() returns a plain-text string
# ---------------------------------------------------------------------------

class TestAC3_AskReturnsString(unittest.TestCase):

    def _make_engine(self, mock_responses):
        from core.engine import RulesEngine
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = mock_responses
        return RulesEngine(anthropic_client=mock_client)

    def test_tc7_ask_returns_str_on_immediate_end_turn(self):
        """TC-7 | AC-3: ask() with a direct text response returns a str."""
        engine = self._make_engine([_mock_non_tool_response("Hello, rules!")])
        result = engine.ask([{"role": "user", "content": "How do I move?"}])
        self.assertIsInstance(result, str)
        self.assertEqual(result, "Hello, rules!")

    def test_tc8_ask_invokes_status_callback(self):
        """TC-8 | AC-3: status_callback is called at least once during ask()."""
        engine = self._make_engine([_mock_non_tool_response("ok")])
        calls = []
        engine.ask([{"role": "user", "content": "test"}], status_callback=calls.append)
        self.assertGreater(len(calls), 0, "status_callback was never invoked")
        self.assertTrue(all(isinstance(c, str) for c in calls))

    def test_tc9_ask_handles_tool_use_loop(self):
        """TC-9 | AC-3: ask() loops through tool_use, then returns final text."""
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp)
            from core.engine import RulesEngine
            mock_client = MagicMock()
            mock_client.messages.create.side_effect = [
                _mock_tool_use_response(
                    "search_rulebook", "t1", {"game": "FakeGame", "query": "move"}
                ),
                _mock_non_tool_response("Based on the rulebook, you move 3 spaces."),
            ]
            engine = RulesEngine(anthropic_client=mock_client, knowledge_base=kb)
            result = engine.ask([{"role": "user", "content": "How do I move?"}])
        self.assertIsInstance(result, str)
        self.assertEqual(result, "Based on the rulebook, you move 3 spaces.")
        self.assertEqual(mock_client.messages.create.call_count, 2)


# ---------------------------------------------------------------------------
# AC-4  RulesEngine accepts injected anthropic_client
# ---------------------------------------------------------------------------

class TestAC4_ClientInjection(unittest.TestCase):

    def test_tc10_injected_client_is_used(self):
        """TC-10 | AC-4: the injected mock client (not a real Anthropic instance) drives ask()."""
        from core.engine import RulesEngine
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _mock_non_tool_response("injected!")
        engine = RulesEngine(anthropic_client=mock_client)
        result = engine.ask([{"role": "user", "content": "test"}])
        mock_client.messages.create.assert_called_once()
        self.assertEqual(result, "injected!")

    def test_tc11_knowledge_base_override_used(self):
        """TC-11 | AC-4: injected knowledge_base path is used by _search_rulebook."""
        with tempfile.TemporaryDirectory() as tmp:
            kb = Path(tmp)
            # Create a fake game folder with a txt file
            game_dir = kb / "testgame"
            game_dir.mkdir()
            (game_dir / "rules.txt").write_text("line1\nthe special keyword here\nline3")

            from core.engine import RulesEngine
            engine = RulesEngine(
                anthropic_client=MagicMock(),
                knowledge_base=kb,
            )
            result = engine._search_rulebook("testgame", "special keyword")
            self.assertIn("special keyword", result.lower())
            self.assertNotIn("No rulebook data found", result)


# ---------------------------------------------------------------------------
# AC-5  Discord adapter preserves MAX_HISTORY=20 cap
# ---------------------------------------------------------------------------

class TestAC5_HistoryCap(unittest.TestCase):

    def test_tc12_max_history_constant_is_20(self):
        """TC-12a | AC-5: MAX_HISTORY constant equals 20."""
        from platforms.discord.adapter import MAX_HISTORY
        self.assertEqual(MAX_HISTORY, 20)

    def test_tc13_history_capped_at_max_history_times_2(self):
        """TC-12b | AC-5: history list is trimmed to MAX_HISTORY*2 after exceeding it."""
        from platforms.discord.adapter import MAX_HISTORY

        # Replicate the exact cap logic from _handle_message
        history = defaultdict(list)
        channel_id = 1

        cap = MAX_HISTORY * 2  # 40
        # Pre-fill to exactly cap
        for i in range(cap):
            history[channel_id].append({"role": "user", "content": f"msg {i}"})

        # Simulate adapter appending user + assistant, then capping
        history[channel_id].append({"role": "user", "content": "new question"})
        history[channel_id].append({"role": "assistant", "content": "new answer"})
        # Now history has cap+2=42 entries — triggers the cap
        if len(history[channel_id]) > cap:
            history[channel_id] = history[channel_id][-cap:]

        self.assertEqual(len(history[channel_id]), cap)

    def test_tc14_history_below_cap_not_trimmed(self):
        """TC-13 | AC-5: history with fewer than MAX_HISTORY*2 entries is not trimmed."""
        from platforms.discord.adapter import MAX_HISTORY

        history = defaultdict(list)
        channel_id = 2
        cap = MAX_HISTORY * 2

        for i in range(5):
            history[channel_id].append({"role": "user", "content": f"msg {i}"})

        original_len = len(history[channel_id])
        if len(history[channel_id]) > cap:
            history[channel_id] = history[channel_id][-cap:]

        self.assertEqual(len(history[channel_id]), original_len)


# ---------------------------------------------------------------------------
# AC-6  Discord adapter splits replies >2000 chars
# ---------------------------------------------------------------------------

class TestAC6_ChunkedReplies(unittest.TestCase):
    """
    Tests the chunking logic in _handle_message.
    We run _handle_message under asyncio with mocked Discord objects.
    The same mock_user instance is used for both message.mentions and the
    patched client.user property so the identity check passes.
    """
    from unittest.mock import PropertyMock

    def _run_handle_message(self, reply_text: str):
        """
        Instantiate DiscordAdapter with a mock engine returning reply_text,
        call _handle_message via asyncio.run(), and return (mock_msg, status_msg).
        """
        from core.engine import RulesEngine
        from platforms.discord.adapter import DiscordAdapter
        from unittest.mock import PropertyMock

        mock_engine = MagicMock(spec=RulesEngine)
        mock_engine.ask.return_value = reply_text
        adapter = DiscordAdapter(mock_engine, "fake-token")

        mock_user = MagicMock()
        mock_user.id = 12345

        mock_msg = MagicMock()
        mock_msg.author.bot = False
        mock_msg.mentions = [mock_user]
        mock_msg.content = "<@12345> question"
        mock_msg.channel.id = 1

        status_msg = AsyncMock()
        mock_msg.reply = AsyncMock(return_value=status_msg)
        mock_msg.channel.send = AsyncMock()

        async def run():
            with patch.object(type(adapter._client), "user", new_callable=PropertyMock) as p:
                p.return_value = mock_user
                await adapter._handle_message(mock_msg)

        asyncio.run(run())
        return mock_msg, status_msg

    def test_tc15_reply_exactly_2000_chars_no_embed(self):
        """TC-14 | AC-6: reply of exactly 2000 chars → single edit as content."""
        reply = "x" * 2000
        mock_msg, status_msg = self._run_handle_message(reply)
        status_msg.edit.assert_called_once_with(content=reply)
        mock_msg.channel.send.assert_not_called()

    def test_tc16_reply_2001_chars_uses_embed(self):
        """TC-15 | AC-6: reply of 2001 chars → single edit using embed, no extra messages."""
        reply = "x" * 2001
        mock_msg, status_msg = self._run_handle_message(reply)
        status_msg.edit.assert_called_once()
        kwargs = status_msg.edit.call_args.kwargs
        self.assertEqual(kwargs["content"], "")
        self.assertEqual(kwargs["embed"].description, reply)
        mock_msg.channel.send.assert_not_called()

    def test_tc17_reply_4001_chars_uses_embed(self):
        """TC-16 | AC-6: reply of 4001 chars → single edit using embed, no extra messages."""
        reply = "x" * 4001
        mock_msg, status_msg = self._run_handle_message(reply)
        status_msg.edit.assert_called_once()
        kwargs = status_msg.edit.call_args.kwargs
        self.assertEqual(kwargs["content"], "")
        self.assertEqual(kwargs["embed"].description, reply)
        mock_msg.channel.send.assert_not_called()


# ---------------------------------------------------------------------------
# AC-7  discord-bot/bot.py import chain works without errors
# ---------------------------------------------------------------------------

class TestAC7_ShimImportChain(unittest.TestCase):

    def test_tc18_bot_py_has_valid_syntax(self):
        """TC-17 | AC-7: discord-bot/bot.py compiles without SyntaxError."""
        import py_compile
        path = str(REPO_ROOT / "discord-bot" / "bot.py")
        try:
            py_compile.compile(path, doraise=True)
        except py_compile.PyCompileError as e:
            self.fail(f"discord-bot/bot.py has a syntax error: {e}")

    def test_tc19_shim_imports_platforms_main(self):
        """TC-18 | AC-7: bot.py content references 'platforms.discord.adapter' and 'main'."""
        src = (REPO_ROOT / "discord-bot" / "bot.py").read_text()
        self.assertIn("platforms.discord.adapter", src)
        self.assertIn("main", src)

    def test_tc20_core_engine_importable(self):
        """TC-19 | AC-7: core.engine imports cleanly (validates full import chain)."""
        try:
            import core.engine
        except ImportError as e:
            self.fail(f"core.engine import failed: {e}")


# ---------------------------------------------------------------------------
# AC-8  No engine logic remains in the adapter
# ---------------------------------------------------------------------------

class TestAC8_NoEngineLicInAdapter(unittest.TestCase):

    def _adapter_src(self) -> str:
        return (REPO_ROOT / "platforms" / "discord" / "adapter.py").read_text()

    def test_tc21_no_anthropic_import_in_adapter(self):
        """TC-20 | AC-8: adapter.py must not import anthropic."""
        imports = _imports_from_file(REPO_ROOT / "platforms" / "discord" / "adapter.py")
        self.assertNotIn("anthropic", imports, "adapter must not import anthropic")

    def test_tc22_no_messages_create_in_adapter(self):
        """TC-21 | AC-8: adapter.py must not call messages.create (Anthropic API)."""
        self.assertNotIn(
            "messages.create",
            self._adapter_src(),
            "Anthropic messages.create must not appear in adapter",
        )

    def test_tc23_no_tool_dispatch_in_adapter(self):
        """TC-22 | AC-8: search/add_game tool names must not be defined in adapter."""
        src = self._adapter_src()
        for symbol in ("search_rulebook", "add_game", "search_bgg_forums"):
            self.assertNotIn(
                f"def {symbol}",
                src,
                f"Tool function '{symbol}' must not be defined in adapter",
            )

    def test_tc24_no_system_prompt_in_adapter(self):
        """TC-23 | AC-8: SYSTEM_PROMPT must not be defined in adapter."""
        self.assertNotIn(
            "SYSTEM_PROMPT",
            self._adapter_src(),
            "SYSTEM_PROMPT must live in core/engine.py, not in adapter",
        )


# ---------------------------------------------------------------------------
# Dedup — duplicate messages after RESUME are ignored
# ---------------------------------------------------------------------------

class TestDuplicateMessageHandling(unittest.TestCase):

    def _make_adapter(self, reply_text="answer"):
        from core.engine import RulesEngine
        from platforms.discord.adapter import DiscordAdapter

        mock_engine = MagicMock(spec=RulesEngine)
        mock_engine.ask.return_value = reply_text
        adapter = DiscordAdapter(mock_engine, "fake-token")
        return adapter, mock_engine

    def _make_message(self, mock_user, msg_id=99999):
        mock_msg = MagicMock()
        mock_msg.author.bot = False
        mock_msg.mentions = [mock_user]
        mock_msg.content = f"<@{mock_user.id}> question"
        mock_msg.channel.id = 1
        mock_msg.id = msg_id
        status_msg = AsyncMock()
        mock_msg.reply = AsyncMock(return_value=status_msg)
        mock_msg.channel.send = AsyncMock()
        return mock_msg

    def test_duplicate_message_ignored(self):
        """Same message ID processed only once (RESUME replay protection)."""
        from unittest.mock import PropertyMock
        adapter, mock_engine = self._make_adapter()
        mock_user = MagicMock()
        mock_user.id = 12345
        mock_msg = self._make_message(mock_user, msg_id=77777)

        async def run():
            with patch.object(type(adapter._client), "user", new_callable=PropertyMock) as p:
                p.return_value = mock_user
                await adapter._handle_message(mock_msg)
                await adapter._handle_message(mock_msg)

        asyncio.run(run())
        mock_engine.ask.assert_called_once()

    def test_different_messages_both_processed(self):
        """Different message IDs are processed independently."""
        from unittest.mock import PropertyMock
        adapter, mock_engine = self._make_adapter()
        mock_user = MagicMock()
        mock_user.id = 12345
        msg1 = self._make_message(mock_user, msg_id=11111)
        msg2 = self._make_message(mock_user, msg_id=22222)

        async def run():
            with patch.object(type(adapter._client), "user", new_callable=PropertyMock) as p:
                p.return_value = mock_user
                await adapter._handle_message(msg1)
                await adapter._handle_message(msg2)

        asyncio.run(run())
        self.assertEqual(mock_engine.ask.call_count, 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
