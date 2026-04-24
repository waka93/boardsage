# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

BoardSage is a board game rules assistant that lets users query rulebooks in plain English, powered by Claude AI. Users interact via chat platforms (currently Discord and WeChat Mini Program). The bot searches official rulebooks first, falls back to BoardGameGeek and Reddit forums, and automatically adds new games on demand.

## Current Architecture

```
boardsage/
├── core/
│   └── engine.py           # RulesEngine — platform-agnostic AI loop, all tools
├── platforms/
│   ├── discord/
│   │   └── adapter.py      # Discord adapter (DiscordAdapter class + main())
│   └── wechat/
│       └── adapter.py      # WeChat Mini Program adapter (WeChatAdapter class + main())
├── discord-bot/
│   └── bot.py              # Entry-point shim: adds repo root to sys.path then calls main()
├── wechat-backend/
│   └── backend.py          # Entry-point shim for the WeChat aiohttp HTTP server
├── assets/{game_slug}/     # Extracted rulebook text files (.txt) and PDFs
├── knowledge/{game_slug}/bgg/     # BGG forum cache (index.json + threads/*.json)
├── knowledge/{game_slug}/reddit/  # Reddit thread cache (index.json + threads/*.json)
├── tests/                  # Unit/integration tests (unittest)
└── docs/.workflow/          # PRDs, RFCs, ADRs, QE reports, sign-offs
```

**Stack:** Python, discord.py, aiohttp, Anthropic SDK (`claude-sonnet-4-6`), pypdf  
**Rulebook search:** keyword grep over pre-extracted `.txt` files  
**Forum fallback:** BGG and Reddit APIs, cached locally under `knowledge/`  
**Context:** per-channel/per-user conversation history, capped at `MAX_HISTORY=20` turns

**Entry points:**
- `python discord-bot/bot.py` — starts the Discord bot (backwards-compat shim)
- `python -m platforms.discord.adapter` — direct module entry point (from repo root)
- `python wechat-backend/backend.py` — starts the WeChat Mini Program HTTP server
- `python -m platforms.wechat.adapter` — direct module entry point (from repo root)

## Core Data Flow

1. User message arrives via chat platform
2. `RulesEngine` runs a tool-use loop with Claude:
   - `identify_game_from_web` → DDG web search to resolve the game name when absent from query
   - `search_rulebook` → keyword search over `assets/{game}/` text files
   - `add_game` → auto-download PDF from DDG + extract text (if game unknown)
   - `search_bgg_forums` → fetch and cache BGG threads as fallback
   - `search_reddit` → fetch and cache Reddit threads as additional fallback
3. Claude generates a cited plain-English answer
4. Adapter posts the response back to the platform

## Adding a New Chat Platform

The platform-decoupling refactor (approved 2026-04-22) is complete. To add a new chat platform (e.g. Slack):
1. Create `platforms/slack/adapter.py` — import `RulesEngine` from `core.engine`, instantiate it, map platform events to `engine.ask()`.
2. No changes to `core/engine.py` required.
3. Reference `platforms/discord/adapter.py` as the pattern (~130 lines for the adapter class + entry point).

### Adapter safety patterns

New adapters should implement the safeguards applicable to their transport model:

- **Process lock** *(event-driven adapters, e.g. Discord):* Use `fcntl.flock` (or platform equivalent) in `main()` to prevent duplicate bot instances with the same credentials. The lock must be held for the process lifetime and released automatically on exit/crash. N/A for HTTP adapters — the OS refuses duplicate port binding.
- **Message dedup** *(event-driven adapters, e.g. Discord):* Track recently processed message IDs (e.g. `deque(maxlen=1000)`) and skip duplicates. The check-and-record must be atomic — no `await` between them. N/A for HTTP request/response adapters.
- **Long response handling:** Platform message limits vary. The Discord adapter uses `discord.Embed` for replies >2000 chars, with truncation at 4096 chars. The WeChat adapter splits replies into labelled chunks `(1/N)` at 2048 chars. New adapters should handle their platform's limits similarly (split, embed, or truncate with an indicator).

## Development Workflow

This project uses a 7-step pipeline for features:

1. `/product {slug}` — write PRD, save to `docs/.workflow/prds/{slug}.md`
2. `/architect {slug}` — write RFC, save to `docs/.workflow/rfcs/{slug}.md`
3. `/developer {slug}` — implement from RFC
4. `/technical-doc-writer` — audit and update all docs to match the new code
5. `/multi-review {slug}` — 3 independent reviewers (operational, security, structural) with consolidated verdict
6. `/quality-engineer {slug}` — run tests (including E2E hard gate), file bugs to `docs/.workflow/bugs/{slug}.md`
7. `/product {slug}` — final sign-off, save to `docs/.workflow/signoffs/{slug}.md`

## Claude API Usage

- Use `claude-sonnet-4-6` as default; escalate to `claude-opus-4-7` only for complex reasoning
- Enable prompt caching for rulebook context — rulebooks are large and reused across queries
- Stream responses where latency is user-facing
- Tool-use loop is the core interaction pattern; keep tools narrow and composable
