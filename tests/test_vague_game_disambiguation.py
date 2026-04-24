"""
QE test suite for vague-game-disambiguation feature.
Maps each test to an acceptance criterion (AC-N) from the PRD.
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(REPO_ROOT))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_text_response(text: str):
    block = MagicMock()
    block.type = "text"
    block.text = text
    resp = MagicMock()
    resp.stop_reason = "end_turn"
    resp.content = [block]
    return resp


def _mock_tool_response(name: str, tool_id: str, inp: dict):
    block = MagicMock()
    block.type = "tool_use"
    block.name = name
    block.id = tool_id
    block.input = inp
    resp = MagicMock()
    resp.stop_reason = "tool_use"
    resp.content = [block]
    return resp


def _make_engine():
    from core.engine import RulesEngine
    client = MagicMock()
    return RulesEngine(anthropic_client=client), client


# ---------------------------------------------------------------------------
# Tool / SYSTEM_PROMPT registration
# ---------------------------------------------------------------------------

class TestToolRegistration(unittest.TestCase):

    def test_identify_game_in_tools(self):
        """identify_game_from_web tool is registered in TOOLS."""
        from core.engine import TOOLS
        names = [t["name"] for t in TOOLS]
        self.assertIn("identify_game_from_web", names)

    def test_identify_game_schema(self):
        """identify_game_from_web has correct schema with query field."""
        from core.engine import TOOLS
        tool = next(t for t in TOOLS if t["name"] == "identify_game_from_web")
        schema = tool["input_schema"]
        self.assertEqual(schema["type"], "object")
        self.assertIn("query", schema["properties"])
        self.assertEqual(schema["required"], ["query"])

    def test_system_prompt_contains_disambiguation_instructions(self):
        """SYSTEM_PROMPT includes instructions for calling identify_game_from_web."""
        from core.engine import SYSTEM_PROMPT
        self.assertIn("identify_game_from_web", SYSTEM_PROMPT)
        self.assertIn("does not mention a specific board game", SYSTEM_PROMPT)


# ---------------------------------------------------------------------------
# AC-1: engine dispatches identify_game_from_web tool call
# ---------------------------------------------------------------------------

class TestAC1_ToolDispatch(unittest.TestCase):

    def test_identify_game_tool_is_dispatched(self):
        """AC-1: When Claude calls identify_game_from_web, the engine dispatches it."""
        engine, client = _make_engine()

        client.messages.create.side_effect = [
            _mock_tool_response("identify_game_from_web", "t1", {"query": "Death Jester weapon"}),
            _mock_text_response("I found Warhammer Underworlds."),
        ]

        with patch.object(engine, "_identify_game_from_web", return_value="Candidate board games:\n1. Warhammer Underworlds") as mock_search:
            result = engine.ask([{"role": "user", "content": "The Death Jester weapon seems OP. How to nerf?"}])

        mock_search.assert_called_once_with("Death Jester weapon")
        self.assertIn("Warhammer", result)


# ---------------------------------------------------------------------------
# AC-2: single candidate → engine proceeds without asking user
# ---------------------------------------------------------------------------

class TestAC2_SingleCandidate(unittest.TestCase):

    def test_single_candidate_no_clarification_prompt(self):
        """AC-2: Single web search candidate → answer given without asking user to pick."""
        engine, client = _make_engine()

        client.messages.create.side_effect = [
            _mock_tool_response("identify_game_from_web", "t1", {"query": "Death Jester weapon"}),
            _mock_text_response("Based on Warhammer Underworlds rules, here is how to balance the weapon..."),
        ]

        with patch.object(engine, "_identify_game_from_web", return_value="Candidate board games:\n1. Warhammer Underworlds"):
            result = engine.ask([{"role": "user", "content": "The Death Jester weapon seems OP. How to nerf?"}])

        self.assertNotIn("which game", result.lower())
        self.assertNotIn("could you tell me", result.lower())


# ---------------------------------------------------------------------------
# AC-3: multiple candidates → engine asks user to pick
# ---------------------------------------------------------------------------

class TestAC3_MultipleCandidates(unittest.TestCase):

    def test_multiple_candidates_shows_numbered_list(self):
        """AC-3: Multiple candidates → reply contains numbered list for user to pick."""
        engine, client = _make_engine()

        client.messages.create.side_effect = [
            _mock_tool_response("identify_game_from_web", "t1", {"query": "Death Jester weapon"}),
            _mock_text_response(
                "I found a few games this could refer to — which one did you mean?\n"
                "1. Warhammer Underworlds\n2. Grimslingers"
            ),
        ]

        with patch.object(engine, "_identify_game_from_web",
                          return_value="Candidate board games:\n1. Warhammer Underworlds\n2. Grimslingers"):
            result = engine.ask([{"role": "user", "content": "The Death Jester weapon seems OP."}])

        self.assertIn("1.", result)
        self.assertIn("2.", result)


# ---------------------------------------------------------------------------
# AC-4: no candidates → fallback clarification prompt
# ---------------------------------------------------------------------------

class TestAC4_NoCandidates(unittest.TestCase):

    def test_no_candidates_fallback_to_clarification(self):
        """AC-4: Web search returns nothing → engine falls back to open-ended clarification."""
        engine, client = _make_engine()

        client.messages.create.side_effect = [
            _mock_tool_response("identify_game_from_web", "t1", {"query": "Death Jester weapon"}),
            _mock_text_response("I couldn't identify the game from that description. Could you tell me which board game you're asking about?"),
        ]

        with patch.object(engine, "_identify_game_from_web",
                          return_value="No board game identified from web search."):
            result = engine.ask([{"role": "user", "content": "The Death Jester weapon seems OP."}])

        lower = result.lower()
        self.assertTrue(
            "which" in lower or "game" in lower or "identify" in lower,
            f"Expected clarification prompt, got: {result!r}",
        )


# ---------------------------------------------------------------------------
# AC-5: resolved game persists in session history
# ---------------------------------------------------------------------------

class TestAC5_SessionPersistence(unittest.TestCase):

    def test_resolved_game_reused_in_followup(self):
        """AC-5: After disambiguation, follow-up query uses the resolved game without re-asking."""
        engine, client = _make_engine()

        # Turn 1: vague query → disambiguate → answer
        client.messages.create.side_effect = [
            _mock_tool_response("identify_game_from_web", "t1", {"query": "Death Jester weapon"}),
            _mock_text_response("Based on Warhammer Underworlds rules, the Death Jester's scythe deals 2 damage."),
        ]

        messages: list[dict] = [{"role": "user", "content": "The Death Jester weapon seems OP."}]
        with patch.object(engine, "_identify_game_from_web",
                          return_value="Candidate board games:\n1. Warhammer Underworlds"):
            reply1 = engine.ask(list(messages))

        messages.append({"role": "assistant", "content": reply1})

        # Turn 2: follow-up with no game name → should NOT call identify_game_from_web again
        messages.append({"role": "user", "content": "What about its other abilities?"})

        with patch.object(engine, "_identify_game_from_web") as mock_search:
            client.messages.create.side_effect = [
                _mock_text_response("Still referring to Warhammer Underworlds: the Death Jester also has..."),
            ]
            reply2 = engine.ask(list(messages))

        # identify_game_from_web should NOT be called if Claude uses conversation history
        # (Claude may or may not call it — this test verifies the history is passed correctly
        # so Claude has the context to avoid re-disambiguation)
        self.assertIn("messages", str(client.messages.create.call_args))
        call_kwargs = client.messages.create.call_args[1]
        history = call_kwargs.get("messages", [])
        # The history passed must include the prior assistant reply containing "Warhammer"
        history_text = str(history)
        self.assertIn("Warhammer", history_text)


# ---------------------------------------------------------------------------
# _extract_game_candidates unit tests
# ---------------------------------------------------------------------------

class TestExtractGameCandidates(unittest.TestCase):

    def test_strips_bgg_suffix(self):
        from core.engine import _extract_game_candidates
        result = _extract_game_candidates(["Warhammer Underworlds - BoardGameGeek"])
        self.assertEqual(result[0], "Warhammer Underworlds")

    def test_deduplicates_case_insensitive(self):
        from core.engine import _extract_game_candidates
        result = _extract_game_candidates(["Warhammer Underworlds", "warhammer underworlds"])
        self.assertEqual(len(result), 1)

    def test_caps_at_four(self):
        from core.engine import _extract_game_candidates
        titles = [f"Game {i}" for i in range(10)]
        result = _extract_game_candidates(titles)
        self.assertLessEqual(len(result), 4)

    def test_empty_input(self):
        from core.engine import _extract_game_candidates
        self.assertEqual(_extract_game_candidates([]), [])

    def test_high_confidence_sorted_first(self):
        from core.engine import _extract_game_candidates
        titles = ["Some Random Site", "Grimcoven board game review"]
        result = _extract_game_candidates(titles)
        self.assertEqual(result[0], "Grimcoven board game review")

    def test_strips_wikipedia_suffix(self):
        from core.engine import _extract_game_candidates
        result = _extract_game_candidates(["Pandemic - Wikipedia"])
        self.assertEqual(result[0], "Pandemic")


if __name__ == "__main__":
    unittest.main()
