# RFC: Platform Decoupling

**Status:** Proposed
**Author:** Architect
**Date:** 2026-04-22
**PRD:** docs/workflow/prds/platform-decoupling.md

## Summary

Extract the `RulesEngine` (tool-use loop, rulebook search, BGG fallback, conversation history management) from `discord-bot/bot.py` into `core/engine.py` as a standalone, platform-agnostic class. Relocate all Discord-specific I/O to `platforms/discord/adapter.py`. Keep `discord-bot/bot.py` as a one-line entry-point shim for backwards compatibility.

## Background

`discord-bot/bot.py` fuses Discord event handling with the AI rules engine in a single 415-line file. The PRD identifies this as the blocker for adding new chat platforms: any new integration would require forking the file and duplicating ~300 lines of engine logic. The refactor splits responsibilities cleanly so each platform adapter is a thin shell around a shared engine.

## Design

### High-Level Architecture

```
┌─────────────────────────────────────────────────┐
│                    platforms/                    │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  discord/adapter.py                      │   │
│  │  - discord.Client, intents               │   │
│  │  - on_message event handler              │   │
│  │  - per-channel history (dict[int, list]) │   │
│  │  - chunked reply sending (2000-char cap) │   │
│  │  - status message editing                │   │
│  └───────────────┬──────────────────────────┘   │
│                  │ engine.ask(messages, cb)       │
└──────────────────┼──────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────┐
│                    core/                         │
│                                                  │
│  ┌──────────────────────────────────────────┐   │
│  │  engine.py  —  RulesEngine               │   │
│  │  - Anthropic client (injected or default)│   │
│  │  - SYSTEM_PROMPT, TOOLS definitions      │   │
│  │  - tool-use loop (ask method)            │   │
│  │  - search_rulebook()                     │   │
│  │  - add_game()                            │   │
│  │  - search_bgg_forums()                   │   │
│  │  - helper: normalize(), find_game_folder()│  │
│  └──────────────────────────────────────────┘   │
└─────────────────────────────────────────────────┘

discord-bot/bot.py  →  thin shim: `python -m platforms.discord.adapter`
```

### Components

| Component                       | Responsibility                                                                 | File / Module                    |
|---------------------------------|--------------------------------------------------------------------------------|----------------------------------|
| `RulesEngine`                   | Platform-agnostic AI loop; tool dispatch; rulebook/BGG search                 | `core/engine.py`                 |
| `DiscordAdapter`                | Discord client setup; message events; history cap; chunked reply; status edits | `platforms/discord/adapter.py`   |
| Entry-point shim                | Import and run `DiscordAdapter` for backwards compatibility                    | `discord-bot/bot.py` (rewritten) |
| Core package marker             | Make `core` importable as a package                                           | `core/__init__.py`               |
| Platforms package markers       | Make `platforms` and `platforms.discord` importable as packages               | `platforms/__init__.py`, `platforms/discord/__init__.py` |

### Data Models

```python
# core/engine.py

# Message format — unchanged from current bot.py; matches Anthropic API shape
Message = dict  # {"role": "user"|"assistant", "content": str | list}

# Tool result — unchanged
ToolResult = dict  # {"type": "tool_result", "tool_use_id": str, "content": str}

# Status callback signature
StatusCallback = Callable[[str], None] | None
```

No new dataclasses are introduced; the engine uses the same dict-based message format already required by the Anthropic SDK.

### API / Interface Contract

#### `core/engine.py`

```python
REPO_ROOT: Path  # Path(__file__).parent.parent — anchors all asset paths

class RulesEngine:
    def __init__(
        self,
        anthropic_client=None,              # injected for testing; defaults to real client
        knowledge_base: Path | None = None, # defaults to REPO_ROOT / "assets"
        bgg_cache_base: Path | None = None, # defaults to REPO_ROOT / "knowledge"
    ) -> None: ...

    def ask(
        self,
        messages: list[dict],               # conversation so far (mutated internally per turn)
        status_callback: Callable[[str], None] | None = None,
    ) -> str: ...                           # plain-text answer from Claude

    # Private helpers (not part of public API, but must exist for the tool dispatch):
    def _search_rulebook(self, game: str, query: str) -> str: ...
    def _add_game(self, game: str, status: Callable) -> str: ...
    def _search_bgg_forums(self, game: str, query: str) -> str: ...
    def _search_cached_threads(self, cache_dir: str, keywords: re.Pattern) -> str: ...
```

`ask()` must:
1. Loop until `stop_reason != "tool_use"`.
2. Append assistant content and tool results to `messages` in-place (same pattern as current `run_with_tools`).
3. Call `status_callback(text)` whenever the current code calls `status(text)`, if the callback is not None.
4. Return the first `block.text` from the final response, or `"(No response)"`.

#### `platforms/discord/adapter.py`

```python
MAX_HISTORY: int = 20
DISCORD_CHUNK: int = 2000

class DiscordAdapter:
    def __init__(self, engine: RulesEngine, discord_token: str) -> None: ...
    def run(self) -> None: ...             # blocks; calls client.run(token)

# Module-level entry point:
def main() -> None:
    engine = RulesEngine()
    adapter = DiscordAdapter(engine, os.environ["DISCORD_TOKEN"])
    adapter.run()

if __name__ == "__main__":
    main()
```

`on_message` handler responsibilities (unchanged logic, new home):
- Ignore bot messages and non-mentions.
- Strip mention from text; reply with `"Yes? Ask me a board game rules question!"` if empty.
- Append user turn to `history[channel_id]`.
- Send `"_Thinking..._"` status message.
- Call `engine.ask(list(history[channel_id]), update_status)` via `run_in_executor`.
- Append assistant turn to history; cap at `MAX_HISTORY * 2` entries.
- Edit status message with reply; send additional chunks for replies >2000 chars.

#### `discord-bot/bot.py` (shim — complete replacement)

```python
from platforms.discord.adapter import main
main()
```

### Key Algorithms / Logic

**Path anchoring (resolves the biggest risk from PRD):**

```python
# core/engine.py — line 1 of module scope
REPO_ROOT = Path(__file__).parent.parent  # boardsage/core/../  == boardsage/
KNOWLEDGE_BASE = REPO_ROOT / "assets"
BGG_CACHE_BASE = REPO_ROOT / "knowledge"
```

**bgg_fetch import (preserves current sys.path hack, anchored to REPO_ROOT):**

```python
# core/engine.py — module-level, before import bgg_fetch
import sys
_BGG_SCRIPTS = REPO_ROOT / ".claude" / "skills" / "boardgame-forum-search" / "scripts"
if str(_BGG_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_BGG_SCRIPTS))
import bgg_fetch
```

**Thread-safety of status callback in adapter:**

```python
# platforms/discord/adapter.py — inside on_message
loop = asyncio.get_running_loop()

def update_status(text: str) -> None:
    asyncio.run_coroutine_threadsafe(
        status_msg.edit(content=f"_{text}_"), loop
    ).result()  # blocks the executor thread until edit completes

reply = await loop.run_in_executor(
    None, lambda: self._engine.ask(list(history[channel_id]), update_status)
)
```

This is identical to the current pattern — no change in concurrency model.

## Alternatives Considered

| Option | Pros | Cons | Decision |
|--------|------|------|----------|
| Keep everything in `bot.py`, use inheritance for platform variants | No file moves; familiar layout | Inheritance is the wrong tool; still one file per platform | Rejected |
| Protocol / ABC for `PlatformAdapter` | Enforces contract for future adapters | Premature abstraction; only one adapter exists today | Rejected — add if/when a second platform is built |
| Move `bgg_fetch.py` into `core/` | Cleaner imports; no sys.path hack | Changes a skills file outside the feature scope; breaks the skill | Rejected — fix in a separate ticket |
| Config file / env vars for `KNOWLEDGE_BASE` / `BGG_CACHE_BASE` | More flexible for deployment | Out of scope per PRD non-goals | Rejected — engine accepts override via `__init__` params for testing |

## Security Considerations

- No new attack surface. All existing input boundaries (Discord message content, BGG API responses, PDF downloads) remain in the same functions; they move files only.
- `DISCORD_TOKEN` and `ANTHROPIC_API_KEY` stay in environment variables; neither is hardcoded or logged.
- The `sys.path.insert` for `bgg_fetch` is preserved as-is; no new exec/eval-style risk introduced.

## Performance Considerations

- `run_in_executor` keeps the Discord event loop non-blocking — no change from current behaviour.
- `RulesEngine.__init__` creates the `anthropic.Anthropic()` client once per process, same as today.
- No new I/O paths; no additional API calls; no caching changes.

## Testing Strategy

QE should validate the following:

**Structural (static/import):**
- `core/engine.py` imports successfully in an environment where `discord` is not installed.
- No `discord` symbol appears in `core/engine.py` (grep check).
- No `anthropic` import appears in `platforms/discord/adapter.py` (grep check).

**Unit — `RulesEngine.ask()`:**
- With a mock `anthropic_client` that returns a fixed non-tool response: `ask()` returns the expected text.
- With a mock client that returns one `tool_use` block followed by a final text response: `ask()` calls the correct tool function and returns the final text.
- Status callback is invoked at least once during a tool-use turn.
- Injected `knowledge_base` path is used by `_search_rulebook` (not the default).

**Unit — `DiscordAdapter`:**
- `history` is capped at `MAX_HISTORY * 2` entries after exceeding the limit.
- A reply of exactly 2001 chars is split into two chunks.
- A reply of exactly 2000 chars is sent as a single edit (no second chunk).

**Integration (smoke):**
- `discord-bot/bot.py` can be imported without raising an exception (validates shim + engine import chain).

**Regression:**
- All existing `boardgame-rules` evals pass against the refactored engine (evals live in `.claude/skills/boardgame-rules/evals/`).

## Implementation Checklist

- [ ] Create `core/__init__.py` (empty)
- [ ] Create `core/engine.py`: move `RulesEngine` (all search functions, tool dispatch, SYSTEM_PROMPT, TOOLS, normalize/find_game_folder helpers) from `bot.py`; anchor `REPO_ROOT` to `Path(__file__).parent.parent`; add `bgg_fetch` sys.path setup
- [ ] Create `platforms/__init__.py` (empty)
- [ ] Create `platforms/discord/__init__.py` (empty)
- [ ] Create `platforms/discord/adapter.py`: move Discord client, `on_ready`, `on_message`, history dict, `MAX_HISTORY`, chunked send logic; import `RulesEngine` from `core.engine`
- [ ] Rewrite `discord-bot/bot.py` to a two-line shim: `from platforms.discord.adapter import main; main()`
- [ ] Verify all `Path` references in `core/engine.py` resolve correctly relative to `REPO_ROOT`
- [ ] Verify `bgg_fetch` import works from the new file location
- [ ] Run existing evals to confirm no regression
