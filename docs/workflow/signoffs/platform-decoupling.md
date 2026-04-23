# Product Sign-off: Platform Decoupling

**Decision:** APPROVED
**Date:** 2026-04-22
**Feature slug:** platform-decoupling

## Artifacts Reviewed

- PRD: docs/workflow/prds/platform-decoupling.md
- RFC: docs/workflow/rfcs/platform-decoupling.md
- QE Report: docs/workflow/bugs/platform-decoupling.md
- Code Review: in-conversation architectural review (APPROVED, 3 MINOR issues — all resolved)

## Acceptance Criteria Coverage

| AC   | Criterion                                                              | QE Test(s)            | Status               |
|------|------------------------------------------------------------------------|-----------------------|----------------------|
| AC-1 | `core/engine.py` exists, defines `RulesEngine`, no Discord imports    | TC-1, TC-2, TC-3      | ✓ Verified           |
| AC-2 | `platforms/discord/adapter.py` exists; all `discord.*` refs there only | TC-4, TC-5, TC-6      | ✓ Verified           |
| AC-3 | `ask(messages, status_callback)` returns plain-text string             | TC-7, TC-8, TC-9      | ✓ Verified           |
| AC-4 | `RulesEngine` accepts injected `anthropic_client` for testability      | TC-10, TC-11          | ✓ Verified           |
| AC-5 | Adapter preserves `MAX_HISTORY=20` conversation-cap behaviour          | TC-12a, TC-12b, TC-13 | ✓ Verified           |
| AC-6 | Adapter splits replies >2000 chars into multiple messages              | TC-14, TC-15, TC-16   | ✓ Verified           |
| AC-7 | Entry point starts bot without errors                                  | TC-17, TC-18, TC-19   | ✓ Verified (import chain); live startup is a deploy-time verification |
| AC-8 | No engine logic (tool dispatch, API calls, search fns) in adapter      | TC-20–TC-23           | ✓ Verified           |

## Decision Rationale

The implementation fully solves the stated problem: `discord-bot/bot.py` is now a 7-line shim; `core/engine.py` contains zero Discord symbols (verified by AST and text grep); `platforms/discord/adapter.py` holds all Discord-specific I/O. All four PRD goals are met. Code review found 3 MINOR issues (unused `import os` in engine, unused imports in tests, platform-specific language in `SYSTEM_PROMPT`) — the first two were fixed immediately, the third is deferred as a follow-up when a second platform is added (changing it now would violate the PRD non-goal of not modifying engine logic). All 24 QE tests pass. AC-7's live bot startup is a deploy-time verification requiring credentials — the full import chain is verified by automated tests, which satisfies the PRD's stated test approach of "manual smoke test / import-time check."

## Ship Checklist

- [x] All acceptance criteria verified by QE (24/24 tests pass)
- [x] No open BLOCKER or HIGH bugs
- [x] Code review completed — APPROVED, all actionable issues resolved
- [x] Non-goals respected — no second platform, no engine changes, no packaging
- [x] Open questions from PRD resolved — shim approach confirmed, paths kept as computed constants

## Follow-up (not blocking ship)

- `SYSTEM_PROMPT` in `core/engine.py` says "in a Discord server" — make platform-aware when a second adapter is added
