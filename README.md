# BoardSage

A board game rules assistant that lets you query rulebooks in plain English, powered by Claude AI.

## Features

- Ask rules questions in natural language — no need to skim through pages of rulebooks
- Searches official rulebooks, FAQs, and errata, and cites the exact source
- Automatically downloads and indexes new games on demand
- BGG and Reddit community forum search as fallback for edge cases not covered by official documents
- Platform-agnostic engine — currently serves Discord and WeChat Mini Program, extensible to other chat platforms

## Project Structure

```
boardsage/
├── core/
│   ├── engine.py               # RulesEngine — platform-agnostic AI loop and tools
│   ├── manager.py              # KnowledgeManager — list/remove/refresh game knowledge
│   ├── utils.py                # Shared utilities (e.g. normalize())
│   ├── bgg_fetch.py            # BGG forum fetch helpers
│   └── reddit_fetch.py         # Reddit thread fetch helpers
├── platforms/
│   ├── discord/
│   │   └── adapter.py          # Discord adapter (DiscordAdapter class + main())
│   └── wechat/
│       └── adapter.py          # WeChat Mini Program adapter (WeChatAdapter class + main())
├── management/
│   └── cli/
│       └── adapter.py          # Terminal management CLI (boardsage list/info/remove/refresh)
├── discord-bot/
│   ├── bot.py                  # Entry-point shim (adds repo root to sys.path, calls main())
│   └── requirements.txt
├── wechat-backend/
│   └── backend.py              # Entry-point shim for the WeChat aiohttp HTTP server
├── assets/{game_slug}/         # Rulebook PDFs + pre-extracted .txt files
├── knowledge/{game_slug}/bgg/  # BGG forum cache (index.json + threads/*.json)
├── knowledge/{game_slug}/reddit/ # Reddit thread cache (index.json + threads/*.json)
├── tests/                      # Unit and integration tests
└── docs/.workflow/              # PRDs, RFCs, ADRs, QE reports, sign-offs
```

## How It Works

1. User message arrives via a chat platform (Discord or WeChat Mini Program)
2. `RulesEngine` runs a tool-use loop with Claude:
   - `identify_game_from_web` — DDG web search to resolve the game name when absent from query
   - `search_rulebook` — keyword search over `assets/{game}/` text files
   - `add_game` — auto-download PDF rulebooks and extract text (if game is unknown)
   - `search_bgg_forums` — fetch and cache BGG threads as fallback
   - `search_reddit` — fetch and cache Reddit threads as additional fallback
3. Claude generates a cited plain-English answer
4. The platform adapter posts the response back

## Adding a New Game

Games are added **automatically** — just ask the bot about a game it doesn't know yet. It will search for the rulebook PDF online, download it, extract the text, and index it.

To add a game manually:

1. Create a folder under `assets/{game-name}/`
2. Drop in the PDF rulebook, FAQ, and errata files
3. Extract text: for each PDF, run `pdftotext yourfile.pdf yourfile.txt` (or use the `pdf` skill in Claude Code)
4. The bot will pick it up automatically on the next query

## Discord Bot

The bot responds when @mentioned in any channel it has access to. It uses Claude's tool-use loop to search rulebook files before answering.

### Setup

```bash
cd discord-bot
uv venv .venv
uv pip install -r requirements.txt --python .venv/bin/python
```

### Running

```bash
DISCORD_TOKEN=your_token ANTHROPIC_API_KEY=your_key .venv/bin/python bot.py
```

Or store keys in `.secret/discord_token` and `.secret/claude`:

```bash
DISCORD_TOKEN=$(cat ../.secret/discord_token) ANTHROPIC_API_KEY=$(cat ../.secret/claude) .venv/bin/python bot.py
```

You can also run the adapter directly from the repo root:

```bash
DISCORD_TOKEN=your_token ANTHROPIC_API_KEY=your_key python -m platforms.discord.adapter
```

### Usage

Mention the bot with a rules question:

> @BoardSage In Grimcoven, how does gambling work?

The bot shows live status updates as it searches and generates the answer.

## Adding a New Chat Platform

Create an adapter under `platforms/{platform}/adapter.py`:

1. Import `RulesEngine` from `core.engine`
2. Map platform events to `engine.ask(messages, status_callback)`
3. Post the returned answer back to the platform

See `platforms/discord/adapter.py` as a reference (~135 lines).

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (package manager)
- Anthropic API key
- Discord bot token (for the Discord bot)
