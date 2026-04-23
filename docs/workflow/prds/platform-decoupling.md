# PRD: Platform Decoupling

**Status:** Draft
**Author:** Product
**Date:** 2026-04-22
**Slug:** platform-decoupling

## Problem Statement

`discord-bot/bot.py` tightly couples Discord-specific I/O (intents, message events, reply API) with the core rules-engine logic (tool-use loop, rulebook search, BGG fallback, conversation history). Adding a second chat platform (Slack, web, CLI) requires duplicating or forking the entire bot file, making the codebase hard to maintain and extend.

## Goals

- The `RulesEngine` class is fully platform-agnostic: no Discord imports, no Discord types, no Discord-specific I/O.
- Adding a new chat platform requires creating only one new file (`platforms/{name}/adapter.py`) with no changes to the engine.
- The existing Discord bot behaviour is preserved exactly after refactoring (same answers, same history cap, same status updates, same chunked message splitting).
- The project can be started with `python -m platforms.discord.adapter` (or equivalent) without touching any engine code.

## Non-Goals (Out of Scope)

- Implementing any second platform (Slack, web, CLI) — the refactor only enables it.
- Changing the rules-engine logic, tool definitions, or AI model configuration.
- Adding tests for the existing engine tools (`search_rulebook`, `add_game`, `search_bgg_forums`).
- Packaging or distribution (pip install, Docker, etc.).

## User Stories

| ID   | As a…            | I want to…                                                        | So that…                                              |
|------|------------------|-------------------------------------------------------------------|-------------------------------------------------------|
| US-1 | Developer        | import `RulesEngine` from `core.engine` without touching Discord  | I can unit-test the engine in isolation                |
| US-2 | Developer        | add a new platform by creating one adapter file                   | I don't have to copy-paste engine logic               |
| US-3 | Discord user     | experience the same bot behaviour as before the refactor          | I notice no regression in answers, speed, or UX       |
| US-4 | Developer        | see a clear entry point per platform                              | I know exactly which file to run for each integration |

## Acceptance Criteria

| ID   | Criterion                                                                                      | Test approach                                                                 |
|------|-----------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------|
| AC-1 | `core/engine.py` exists and defines `RulesEngine`; it imports no Discord symbols               | Static import check; `import core.engine` succeeds in a Discord-free env      |
| AC-2 | `platforms/discord/adapter.py` exists and contains all Discord-specific code                   | File exists; all `discord.*` references live only in adapter                  |
| AC-3 | `RulesEngine.ask(messages, status_callback)` returns a plain-text string                       | Unit test: call with mock messages and a no-op status callback                |
| AC-4 | `RulesEngine` accepts an optional `anthropic_client` injection for testability                  | Unit test: pass a mock client; verify it is used                              |
| AC-5 | Discord adapter preserves `MAX_HISTORY=20` conversation-cap behaviour                          | Unit test or integration test against adapter logic                           |
| AC-6 | Discord adapter handles replies >2000 chars in a single message (embed), not multiple messages  | Unit test: simulate long reply from engine, verify single edit with embed     |
| AC-7 | Running `python discord-bot/bot.py` (or new entry point) still starts the bot without errors   | Manual smoke test / import-time check                                         |
| AC-8 | No engine logic remains in the adapter (tool dispatch, Anthropic API calls, search functions)  | Code review / grep: no `anthropic` import in adapter                          |
| AC-9 | Only one bot instance can run per Discord token; a second instance exits with a clear error     | Unit test: call `main()` twice with the same token; second call raises `SystemExit` |

## UX / Interaction Design

No user-facing interaction change. Discord users continue to @-mention the bot and receive the same styled responses. The refactor is internal only.

Developer UX (new layout):
```
boardsage/
├── core/
│   ├── __init__.py
│   └── engine.py          ← RulesEngine (platform-agnostic)
├── platforms/
│   ├── __init__.py
│   └── discord/
│       ├── __init__.py
│       └── adapter.py     ← Discord adapter (imports core.engine)
└── discord-bot/
    └── bot.py             ← kept for backwards compat or replaced by platforms entry point
```

`RulesEngine` public API:
```python
class RulesEngine:
    def __init__(self, anthropic_client=None): ...
    def ask(self, messages: list[dict], status_callback=None) -> str: ...
```

## Dependencies & Risks

| Item                          | Type         | Notes                                                                      |
|-------------------------------|--------------|----------------------------------------------------------------------------|
| `bgg_fetch` import path       | Risk         | Currently resolved via `sys.path.insert`; must survive relocation          |
| `KNOWLEDGE_BASE` / `BGG_CACHE_BASE` paths | Risk | Relative to `bot.py`; must be re-anchored to repo root after move    |
| `discord-bot/bot.py` entry point | Dependency | Existing deployment may reference this path; adapter must preserve it or be documented |
| `asyncio` / threading in adapter | Risk     | `run_with_tools` is sync; adapter calls it via `run_in_executor`; must stay thread-safe |

## Open Questions

- [ ] Should `discord-bot/bot.py` be kept as a thin shim that imports and runs the adapter, or deleted in favour of `python -m platforms.discord.adapter`?
- [ ] Should `KNOWLEDGE_BASE` and `BGG_CACHE_BASE` be moved to env vars or a config file as part of this refactor?

## Success Metrics

- Zero Discord-specific symbols (`discord.`, `discord.py` imports) remain in `core/engine.py` after the refactor.
- A developer can write a unit test that instantiates `RulesEngine` and calls `ask()` without a Discord token or a live Anthropic API key.
- Any future platform adapter (e.g., Slack) can be written in under 100 lines by copy-adapting the Discord adapter pattern.
