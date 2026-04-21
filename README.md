# BoardSage

A board game encyclopedia that lets you query rulebooks in plain English, powered by Claude AI.

## Features

- Ask rules questions in natural language — no need to skim through pages of rulebooks
- Searches official rulebooks, FAQs, and errata, and cites the exact source
- Discord bot interface for easy access during game sessions
- BGG community forum search as fallback for edge cases not covered by official documents

## Project Structure

```
boardsage/
├── assets/                  # Rulebook data, one folder per game
│   └── grimcoven/           # PDFs + pre-extracted .txt files
├── discord-bot/             # Discord bot that answers rules questions
│   ├── bot.py
│   └── requirements.txt
├── knowledge/               # BGG forum cache, one folder per game slug
│   └── {game}/bgg/
│       ├── index.json
│       └── threads/
└── .claude/skills/          # Claude Code skills
    ├── boardgame-rules/     # Searches local rulebook files
    └── boardgame-forum-search/  # Searches BGG community threads
```

## Adding a New Game

1. Create a folder under `assets/{game-name}/`
2. Drop in the PDF rulebook, FAQ, and errata files
3. Extract text: for each PDF, run `pdftotext yourfile.pdf yourfile.txt` (or use the `pdf` skill in Claude Code)
4. The Discord bot and Claude Code skills will pick it up automatically

## Discord Bot

The bot responds when @mentioned in any channel it has access to. It uses Claude's tool use to search rulebook files before answering.

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

### Usage

Mention the bot with a rules question:

> @BoardSage In Grimcoven, how does gambling work?

The bot shows live status updates as it searches and generates the answer.

## Claude Code Skills

Two skills are available within Claude Code for interactive rules lookups:

- **`boardgame-rules`** — searches local rulebook `.txt` files, cites page numbers
- **`boardgame-forum-search`** — searches BGG community threads when official docs are silent

## Requirements

- Python 3.12+
- [uv](https://github.com/astral-sh/uv) (package manager)
- Anthropic API key
- Discord bot token (for the Discord bot)
