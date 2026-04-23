# QE Report: Platform Decoupling

**Status:** PASSED
**Date:** 2026-04-23
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
| TC-14   | AC-6  | Reply of exactly 2000 chars → single `status_msg.edit(content=...)`, no `send` | PASS   |
| TC-15   | AC-6  | Reply of 2001 chars → single `edit(content="", embed=...)` using embed | PASS   |
| TC-16   | AC-6  | Reply of 4001 chars → single `edit(content="", embed=...)` using embed | PASS   |
| TC-17   | AC-7  | `discord-bot/bot.py` compiles without `SyntaxError`                  | PASS   |
| TC-18   | AC-7  | `bot.py` references `platforms.discord.adapter` and `main`          | PASS   |
| TC-19   | AC-7  | `core.engine` imports cleanly (full import chain validated)          | PASS   |
| TC-20   | AC-8  | `adapter.py` does not import `anthropic`                             | PASS   |
| TC-21   | AC-8  | `adapter.py` does not call `messages.create`                         | PASS   |
| TC-22   | AC-8  | Tool functions (`search_rulebook`, `add_game`, etc.) not defined in adapter | PASS   |
| TC-23   | AC-8  | `SYSTEM_PROMPT` not defined in `adapter.py`                          | PASS   |
| TC-24   | AC-6  | Reply >4096 chars → embed description truncated with `_(truncated)_` indicator, length within limit | PASS   |
| TC-25   | AC-9  | `main()` acquires `LOCK_EX | LOCK_NB` via `fcntl.flock`            | PASS   |
| TC-26   | AC-9  | `main()` calls `sys.exit` with error message when lock is already held | PASS   |
| TC-27   | Dedup | Same message ID processed only once (RESUME replay protection)       | PASS   |
| TC-28   | Dedup | Different message IDs are processed independently                    | PASS   |

## Notes

- TC-14/15/16/24 (AC-6): The adapter now uses `discord.Embed` for replies >2000 chars instead of multi-message chunking. TC-15 and TC-16 were updated to verify `edit(content="", embed=...)` behavior rather than the old `edit(first 2000)` + `channel.send()` chunking logic. TC-24 is a new test covering the >4096 char truncation path, verifying the embed description is capped at `_EMBED_DESC_LIMIT` with a `_(truncated)_` suffix.
- TC-25/26 (AC-9): New tests verifying the process lock mechanism. TC-25 confirms `main()` calls `fcntl.flock` with `LOCK_EX | LOCK_NB`. TC-26 confirms that when the lock is already held (flock raises `OSError`), `main()` exits with an error message containing "Another BoardSage instance is already running".
- AC-7 was verified via syntax compile + content check. The "start the bot without errors" part (actually connecting to Discord) is `MANUAL-PENDING` and requires a live `DISCORD_TOKEN`.
- A `DeprecationWarning` for `audioop` from `discord/player.py` was observed — this is a discord.py library warning, not a project issue.
- A `ResourceWarning` for an unclosed lock file was observed during testing — this is expected because the mock patches `fcntl` but the test still opens a real file via `open()` in `main()`. The lock file is cleaned up by the OS; no production impact.

## AC Coverage

| AC   | Criterion                                                        | Test(s)                     |
|------|------------------------------------------------------------------|-----------------------------|
| AC-1 | `core/engine.py` exists with `RulesEngine`; no Discord imports  | TC-1, TC-2, TC-3            |
| AC-2 | `platforms/discord/adapter.py` exists; Discord refs only there  | TC-4, TC-5, TC-6            |
| AC-3 | `ask()` returns plain-text string; status callback fires        | TC-7, TC-8, TC-9            |
| AC-4 | `RulesEngine` accepts injected `anthropic_client` and `knowledge_base` | TC-10, TC-11         |
| AC-5 | History capped at `MAX_HISTORY * 2 = 40`                        | TC-12a, TC-12b, TC-13       |
| AC-6 | Replies >2000 chars use embed; >4096 chars truncated            | TC-14, TC-15, TC-16, TC-24  |
| AC-7 | Entry point imports and syntax valid; live start is MANUAL      | TC-17, TC-18, TC-19         |
| AC-8 | No engine logic in adapter                                      | TC-20, TC-21, TC-22, TC-23  |
| AC-9 | Only one bot instance per Discord token (process lock)          | TC-25, TC-26                |

## Summary

All 29 automated tests across all 9 acceptance criteria pass. Two new test cases were added in this run:

1. **TC-24 (AC-6):** Covers the >4096 char embed truncation path that was identified as a test coverage gap during code review. Verifies the `_(truncated)_` suffix is appended and the total embed description length stays within `_EMBED_DESC_LIMIT`.
2. **TC-25 and TC-26 (AC-9):** Cover the new singleton-instance process lock mechanism. TC-25 verifies `fcntl.flock` is called with `LOCK_EX | LOCK_NB`. TC-26 verifies that when the lock is already held, `main()` exits with a clear error message.

TC-15 and TC-16 descriptions were updated to reflect the new embed-based behavior (previously described chunking via `channel.send()`).

One sub-criterion of AC-7 (live Discord bot startup) remains `MANUAL-PENDING` and requires a real `DISCORD_TOKEN`. No bugs found.

Ready for product sign-off.
