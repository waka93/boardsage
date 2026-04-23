# QE Report: Platform Decoupling

**Status:** PASSED
**Date:** 2026-04-22
**Feature slug:** platform-decoupling

## Test Results

| Test ID | AC    | Description                                                          | Result |
|---------|-------|----------------------------------------------------------------------|--------|
| TC-1    | AC-1  | `core/engine.py` file exists                                         | PASS   |
| TC-2    | AC-1  | `core.engine` exports `RulesEngine` class                            | PASS   |
| TC-3    | AC-1  | `core/engine.py` does not import `discord` (AST check)              | PASS   |
| TC-4    | AC-2  | `platforms/discord/adapter.py` file exists                           | PASS   |
| TC-5    | AC-2  | `adapter.py` imports `discord`                                       | PASS   |
| TC-6    | AC-2  | Word `discord` does not appear anywhere in `core/engine.py` source   | PASS   |
| TC-7    | AC-3  | `ask()` with immediate end_turn response returns a `str`             | PASS   |
| TC-8    | AC-3  | `status_callback` is invoked at least once during `ask()`            | PASS   |
| TC-9    | AC-3  | `ask()` loops through one `tool_use` turn then returns final text    | PASS   |
| TC-10   | AC-4  | Injected mock `anthropic_client` is the one that `ask()` calls       | PASS   |
| TC-11   | AC-4  | Injected `knowledge_base` path is used by `_search_rulebook`         | PASS   |
| TC-12a  | AC-5  | `MAX_HISTORY` constant equals 20                                     | PASS   |
| TC-12b  | AC-5  | History list trimmed to `MAX_HISTORY * 2` after exceeding cap        | PASS   |
| TC-13   | AC-5  | History with fewer than `MAX_HISTORY * 2` entries is not trimmed     | PASS   |
| TC-14   | AC-6  | Reply of exactly 2000 chars → single `status_msg.edit`, no `send`   | PASS   |
| TC-15   | AC-6  | Reply of 2001 chars → `edit(first 2000)` + one `channel.send("x")`  | PASS   |
| TC-16   | AC-6  | Reply of 4001 chars → `edit(first 2000)` + two `channel.send` calls | PASS   |
| TC-17   | AC-7  | `discord-bot/bot.py` compiles without `SyntaxError`                  | PASS   |
| TC-18   | AC-7  | `bot.py` references `platforms.discord.adapter` and `main`          | PASS   |
| TC-19   | AC-7  | `core.engine` imports cleanly (full import chain validated)          | PASS   |
| TC-20   | AC-8  | `adapter.py` does not import `anthropic`                             | PASS   |
| TC-21   | AC-8  | `adapter.py` does not call `messages.create`                         | PASS   |
| TC-22   | AC-8  | Tool functions (`search_rulebook`, `add_game`, etc.) not defined in adapter | PASS   |
| TC-23   | AC-8  | `SYSTEM_PROMPT` not defined in `adapter.py`                          | PASS   |

## Notes

- TC-14/15/16 (AC-6) required an async test harness using `asyncio.run()` and `PropertyMock` to patch `discord.Client.user`. A `DeprecationWarning` for `audioop` from `discord/player.py` was observed — this is a discord.py library warning, not a project issue.
- AC-7 was verified via syntax compile + content check. The "start the bot without errors" part (actually connecting to Discord) is `MANUAL-PENDING` and requires a live `DISCORD_TOKEN`.

## AC Coverage

| AC   | Criterion                                                        | Test(s)                     |
|------|------------------------------------------------------------------|-----------------------------|
| AC-1 | `core/engine.py` exists with `RulesEngine`; no Discord imports  | TC-1, TC-2, TC-3            |
| AC-2 | `platforms/discord/adapter.py` exists; Discord refs only there  | TC-4, TC-5, TC-6            |
| AC-3 | `ask()` returns plain-text string; status callback fires        | TC-7, TC-8, TC-9            |
| AC-4 | `RulesEngine` accepts injected `anthropic_client` and `knowledge_base` | TC-10, TC-11         |
| AC-5 | History capped at `MAX_HISTORY * 2 = 40`                        | TC-12a, TC-12b, TC-13       |
| AC-6 | Replies >2000 chars split into chunks                           | TC-14, TC-15, TC-16         |
| AC-7 | Entry point imports and syntax valid; live start is MANUAL      | TC-17, TC-18, TC-19         |
| AC-8 | No engine logic in adapter                                      | TC-20, TC-21, TC-22, TC-23  |

## Summary

All 24 automated tests across all 8 acceptance criteria pass. One sub-criterion of AC-7 (live Discord bot startup) is `MANUAL-PENDING` and requires a real `DISCORD_TOKEN` to verify. No bugs found.

Ready for product sign-off.
