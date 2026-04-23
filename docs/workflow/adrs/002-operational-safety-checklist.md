# ADR-002: Operational Safety Requirements for Platform Adapters

**Status:** Accepted
**Date:** 2026-04-23

**Context:**

The platform-decoupling RFC established the pattern for adding new chat platform adapters (`platforms/{name}/adapter.py`). However, the RFC focused exclusively on structural separation (engine vs. adapter) and omitted operational concerns. The duplicate-response incident (see ADR-001) demonstrated that a structurally correct adapter can still fail in production if it lacks operational safeguards.

This ADR establishes a mandatory checklist that every platform adapter must address before deployment.

**Decision:**

All platform adapters (current: Discord; future: Slack, web, CLI, etc.) must implement or explicitly document exemptions for each of the following five operational safety requirements:

### 1. Singleton Enforcement

The adapter's entry point must prevent multiple instances from processing messages for the same identity/token concurrently.

- **Discord:** `fcntl.flock` on a token-keyed lock file (ADR-001).
- **Slack:** Same pattern, keyed on the Slack bot token or app ID.
- **CLI:** Not applicable (stdin is inherently single-reader). Document the exemption.

### 2. Message Deduplication

The adapter must tolerate receiving the same inbound message more than once without producing duplicate responses. Sources of duplicates vary by platform:

- **Discord:** Gateway RESUME replays previously-dispatched events after a reconnect.
- **Slack:** Webhook retries (Slack re-sends if the endpoint doesn't ACK within 3 seconds).
- **Web/HTTP:** Client retries on network timeout.

Implementation: maintain a bounded set of recently-processed message IDs (e.g., `collections.deque(maxlen=1000)`) and skip any message whose ID is already present.

### 3. Response Delivery Guarantees

The adapter must handle the platform's message-size limits and formatting constraints without silently truncating or dropping content.

- **Discord:** 2000-char message limit; long responses use embeds (4096-char description limit) with truncation indicator.
- **Slack:** 40,000-char message limit; blocks API for rich formatting.
- **CLI:** No limit; print to stdout.

### 4. Rate Limit Handling

The adapter must not flood the platform API. Specific concerns:

- **Status update edits:** Debounce to at most one edit per N seconds (Discord adapter uses 2-second intervals). Without debouncing, a tool-use loop with 10+ tool calls will hit Discord's edit rate limit.
- **Platform API rate limits:** Handle HTTP 429 responses with backoff. discord.py handles this internally; other SDKs may not.

### 5. Graceful Shutdown and Crash Recovery

The adapter must not leave stale state that blocks restart after a crash.

- **Process locks:** Use OS-level mechanisms that auto-release on process exit (`flock`, not PID files).
- **Pending status messages:** Accept that a crash may leave a "Thinking..." message in the channel. Do not attempt complex recovery; users can re-ask.
- **Conversation history:** Stored in-memory; lost on crash. This is acceptable for the current architecture. If persistence is added later, ensure atomic writes.

**Consequences:**

- Every new adapter PR must include a section in its design doc (or commit message) addressing all five items, either with an implementation or a documented exemption.
- Code review for adapter PRs should verify this checklist is covered.
- This ADR does not mandate specific implementations for future platforms -- it mandates that the concerns are addressed.

**Alternatives Considered:**

| Alternative | Why Rejected |
|---|---|
| **Encode these requirements in an abstract base class** | Premature abstraction with only one adapter. The checklist approach is lighter and equally enforceable via code review. Revisit if/when a third adapter is added. |
| **Build all safeguards into `core/engine.py`** | Violates the platform-decoupling principle. Singleton enforcement and message dedup are platform-specific (token format, message ID shape, API constraints differ). |
| **Leave operational concerns to each adapter author's judgment** | This is what the original RFC did, and it caused a production incident. Explicit requirements prevent recurrence. |
