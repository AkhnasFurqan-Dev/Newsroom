# Channel Press — mandatory concurrency verification matrix

Use a dedicated disposable database and controlled fake Telegram transport for deterministic tests. When safe, repeat relevant scenarios using the actual configured application and a small **authorized** channel history window; mark live results separately. No public publication.

| ID | Scenario | Expected evidence and behavior |
|---|---|---|
| C01 | Boot web and worker separately on Windows | Both run without blocking each other or leaking credentials; graceful shutdown works |
| C02 | Web GET `/sources/{id}` while Telegram unavailable for 60 s | Page responds promptly with last-known/degraded status; not 45-second hang or 500 |
| C03 | Worker fetch plus web Telegram Test Source | Only one authenticated Telethon session-file owner or demonstrably safe serialization; no sqlite locks |
| C04 | Two detached workers try the same source+window | Exactly one collector; second defers/waits; no duplicate message rows or editions |
| C05 | Active collector plus schedule save, branding save, job cancel, audit insert | All UI mutations succeed, or yield an explicit transient conflict after bounded retry; no raw 500 or lost audit |
| C06 | Force an app SQLite busy writer during an idempotent commit | Retry from fresh transaction; no duplicate audit, no permanent stall; bounded/friendly error if exhausted |
| C07 | Telegram connection/network drops during retrieval | Reason-coded retry, clean async disconnect, no stuck Telegram session DB or destroyed pending tasks |
| C08 | Worker crashes or is gracefully stopped mid-pagination | Checkpoint persists; next worker resumes without data loss, double rows, or skipped interval |
| C09 | Worker crashes while holding collection lease | Stale ownership eventually recoverable with safe process/heartbeat checks; active owner not stolen |
| C10 | Two clicks Queue Build for same interval | One edition/job identity or clearly deduplicated behavior; no second collector |
| C11 | Cancel queued vs running vs already finished job | Legitimate cancellation works; invalid state returns informative response; no SQLite lock/500 |
| C12 | Previous-to-current cutoff | `[start,end)` UTC: first included, last excluded; post exactly at end reserved next edition |
| C13 | 30-day backfill followed by daily issue | Context-only history does not flood daily edition or advance committed daily cutoff |
| C14 | Worker launcher prints INFO to stderr and exits 0 | PowerShell treats it as successful process; real nonzero failures remain observable |
| C15 | User refreshes Jobs/Source while worker active | Polling remains responsive; pages show accurate state and error details without secret leak |
| C16 | Existing fixture/e-paper end to end | Existing print/QC tests pass unchanged; no regressions from concurrency fixes |
| C17 | Real Telegram (if authorized and configured) | Small live history imported successfully; record window, counts, exclusion counts, real job status; no auto-publish |
| C18 | Real end-to-end draft (only if C17 passes and rights allow) | PDF + proof files generated, awaiting editorial review, sources traced; do not claim success if blocked |

## Execution order

1. Capture pre-change failure reproduction with trace/PID/lock diagnostics in safe test environment.
2. Make minimal scoped changes with tests for C01–C15.
3. Run existing suite and print tests C16.
4. Perform C17–C18 supervised when permissions allow. Otherwise label **BLOCKED — requires authorized live setup**, leaving fixtures passing.
5. Record all failed/passed/skipped cases with command, timestamp and artifact in `docs/CONCURRENCY_RECOVERY_REPORT.md`.

## Never do

Do not run a destructive demo reset on the user's existing DB; delete `.session`, DB/WAL/SHM, media or editions; commit `.env`; disable audit/security; suppress exceptions by marking jobs successful; publish live without approval; or report mock results as live verified.
