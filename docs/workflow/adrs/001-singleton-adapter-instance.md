# ADR-001: Singleton Adapter Instance

**Status:** Accepted
**Date:** 2026-04-23

**Context:**

After the platform-decoupling refactor (RFC: `docs/workflow/rfcs/platform-decoupling.md`), a production incident occurred where two bot processes ran simultaneously with the same `DISCORD_TOKEN`. Both processes connected to the Discord gateway, both received every incoming message, and both independently called the LLM and posted a response. Users saw two different answers to the same question.

The original RFC had no "Operational Considerations" section and did not address process exclusion, assuming the deployment environment would never run duplicate instances. This assumption was wrong.

**Decision:**

Enforce single-instance-per-token at the process level using `fcntl.flock` with a non-blocking exclusive lock on a token-keyed file.

Implementation (in `platforms/discord/adapter.py :: main()`):

1. Hash the first 8 hex chars of `md5(DISCORD_TOKEN)` to produce a lock ID.
2. Open `/tmp/boardsage-{lock_id}.lock` for writing.
3. Attempt `fcntl.flock(fd, LOCK_EX | LOCK_NB)`.
4. If the lock fails (`OSError`), exit immediately with a clear error message.
5. If acquired, write the PID and proceed to start the adapter.

The lock is automatically released when the process exits (including crashes and `SIGKILL`), because the OS closes the file descriptor.

**Consequences:**

- **Positive:** Duplicate-instance incidents are impossible for a single host. The mechanism is zero-dependency (no Redis, no database), works on all POSIX systems, and self-heals on crash (the kernel releases `flock` on process exit).
- **Negative:** This does NOT prevent two instances on different hosts (e.g., two VMs, two containers). If the project moves to multi-host deployment, a distributed lock (Redis, database row, or leader election) will be required. This is an accepted tradeoff: BoardSage currently runs on a single host, and adding distributed coordination would be premature.
- **Negative:** The lock file lives in `/tmp`, which is cleared on reboot on some systems. This is benign -- a stale lock file with no holding process does not block a new instance (flock is process-scoped, not file-content-scoped).

**Alternatives Considered:**

| Alternative | Why Rejected |
|---|---|
| **PID file check** (`kill -0 $pid`) | Race condition: process A reads PID file, process B starts and overwrites it, process A checks the new PID and incorrectly concludes it's itself. `flock` is atomic and race-free. |
| **Redis / external lock** | Adds an infrastructure dependency for a single-host project. Appropriate if/when BoardSage moves to multi-instance deployment; overkill today. |
| **Database advisory lock** | BoardSage has no database. Adding one solely for process exclusion is disproportionate. |
| **Discord-side dedup (check recent bot messages before posting)** | Treats the symptom, not the cause. Both instances still consume LLM tokens and API quota. Also introduces a race window between the check and the post. |
| **Message-level dedup only (no process lock)** | Insufficient. Two processes would still both call the LLM for every message, doubling API cost, even if only one posts the reply. The process lock prevents wasted work. |
