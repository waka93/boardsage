# Product Sign-off: Platform Decoupling

**Decision:** APPROVED WITH CONDITIONS
**Date:** 2026-04-22
**Feature slug:** platform-decoupling

## Artifacts Reviewed

- PRD: docs/workflow/prds/platform-decoupling.md
- RFC: docs/workflow/rfcs/platform-decoupling.md
- QE Report: docs/workflow/bugs/platform-decoupling.md
- Code: core/engine.py, platforms/discord/adapter.py, discord-bot/bot.py

## Acceptance Criteria Coverage

| AC   | Criterion                                                              | QE Test(s)            | Status               |
|------|------------------------------------------------------------------------|-----------------------|----------------------|
| AC-1 | `core/engine.py` exists, defines `RulesEngine`, no Discord imports    | TC-1, TC-2, TC-3      | ✓ Verified           |
| AC-2 | `platforms/discord/adapter.py` exists; all `discord.*` refs there only | TC-4, TC-5, TC-6      | ✓ Verified           |
| AC-3 | `ask(messages, status_callback)` returns plain-text string             | TC-7, TC-8, TC-9      | ✓ Verified           |
| AC-4 | `RulesEngine` accepts injected `anthropic_client` for testability      | TC-10, TC-11          | ✓ Verified           |
| AC-5 | Adapter preserves `MAX_HISTORY=20` conversation-cap behaviour          | TC-12a, TC-12b, TC-13 | ✓ Verified           |
| AC-6 | Adapter splits replies >2000 chars into multiple messages              | TC-14, TC-15, TC-16   | ✓ Verified           |
| AC-7 | Entry point starts bot without errors                                  | TC-17, TC-18, TC-19   | ⚠ MANUAL-PENDING     |
| AC-8 | No engine logic (tool dispatch, API calls, search fns) in adapter      | TC-20–TC-23           | ✓ Verified           |

## Decision Rationale

The implementation fully solves the stated problem: `discord-bot/bot.py` is now a 7-line shim; `core/engine.py` contains zero Discord symbols (verified by AST and text grep); `platforms/discord/adapter.py` holds all Discord-specific I/O. All four PRD goals are met — the engine is platform-agnostic, a new platform requires only one new file, existing Discord behaviour is preserved (history cap, chunked replies, status updates verified by 24 passing tests), and `python -m platforms.discord.adapter` is a valid entry point via the `if __name__ == "__main__": main()` guard. Non-goals were respected: no second platform was built, engine logic was not modified, no packaging changes were made. Both open questions from the PRD were resolved in the implementation (shim kept, paths remain as computed `Path` constants with `__init__` overrides for testing). The single condition is the MANUAL-PENDING live Discord startup test — the import chain is fully automated and passes, but a human with a real `DISCORD_TOKEN` must verify the bot actually connects and responds in Discord before declaring this fully shipped.

## Gaps

_(None — no BLOCKER or HIGH issues.)_

## Deferred Items

- **Live Discord startup smoke test** (AC-7, MANUAL-PENDING): Run `python discord-bot/bot.py` with a real `DISCORD_TOKEN` and `ANTHROPIC_API_KEY`, confirm the bot logs in and replies to a mention. This is a deployment-credential requirement that cannot be automated in the test suite, not a code defect. Acceptable to verify at first deployment.

## Ship Checklist

- [x] All acceptance criteria verified by QE (24/24 tests pass)
- [x] No open BLOCKER or HIGH bugs
- [ ] MANUAL-PENDING test outstanding — live bot startup must be verified by operator at deploy time
- [x] Non-goals respected — no second platform, no engine changes, no packaging
- [x] Open questions from PRD resolved — shim approach confirmed, paths kept as computed constants
