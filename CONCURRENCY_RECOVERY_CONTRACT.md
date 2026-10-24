# Channel Press — focused live concurrency repair contract

**Applies to:** new OpenCode session opened in existing `E:\newsroom` repository.  
**Scope:** application-DB write contention, Telethon session ownership, idempotent collection scheduling, resource cleanup and visible error/reporting.  
**Out of scope:** rebuilding newsroom, redesigning newspaper layouts, replacing models, introducing SaaS or paid hosting, implementing new editorial features.

## 1. Guardrails and preparation

- Inspect Git status, recent commits, current users/jobs/active source IDs, current `.env` file existence (NOT its values), session path and DB paths, service startup scripts and scheduler tasks.
- Do not overwrite or reveal `.env` or Telegram secrets. Do not remove/recreate/log out Telegram sessions, reset SQLite, run `demo --reset`, overwrite media or editions, rewrite Git history or modify publication rights declarations.
- **First establish a quiescent state** before any invasive DB migration/backup. Detect current web/worker Python process ownership and Task Scheduler triggers. If shutdown is needed, use graceful job stop/shutdown for known Channel Press processes; never blanket-kill `python.exe` or any unrelated program. Avoid interrupting a live in-progress transaction without proper recovery/confirmation.
- Arrange a verifiable, non-destructive backup of the app DB using SQLite's backup mechanism when safe; ensure WAL state is accounted for. For Telegram session, treat copying or manipulating a live session as sensitive and avoid unless the agent can demonstrate a safe, consented procedure.
- Use a disposable test DB and disposable/mock Telegram session implementation for deterministic concurrency reproductions. Live tests on the owner's authorized source must be small, read-only and supervised. Auto-release stays off.
- Reuse and honor existing `AGENTS.md`, security, channel-rights and exact `[start,end)` cutoff contract.

## 2. Investigation before code changes

Trace web request -> DB session -> queue insert -> detached process spawn -> job claim -> collection lock -> Telegram service -> persistence -> draft -> worker exit.

Instrument safely with request ID, job ID, channel ID, worker ID, PID, transaction begin/end/commit/rollback timing, lock/lease acquisition/release and session ownership lifecycle. Avoid logging message bodies or any auth/session secrets. Determine whether issues arise from:

- web/worker both writing Telethon's SQLite session file;
- reuse of Telegram service/client across event loops/threads or processes;
- SQLAlchemy transactions kept open across external API, media processing, sleeps or AI work;
- concurrent jobs for same source and window (including a `sync_window` and `edition`);
- stale lock or transaction state after crash/exception;
- abusive retries/excessive concurrent detached processes;
- secondary network failures.

Document **observed** versus **inferred** reasons before selecting a solution. No generic SQL pragma changes as a substitute for identifying contention.

## 3. Telegram connection/session ownership — mandatory

- Design one coherent session-ownership policy: preferably a single serialized Telegram I/O owner/service for an authenticated session, with the web app submitting requests to it; or another proven architecture that guarantees only one writer/client owns a Telethon SQLite session at a time.
- Do not keep competing web and worker Telethon clients writing the same `.session`. If brief separate ownership is chosen, ensure the old owner fully disconnected/closed and lock released before transfer, including process exit/crash handling.
- Bound async requests with meaningful, short UI timeouts. `GET /sources/{id}` must render reliably using cached last-known connection status and must not wait 45 seconds on Telegram. Provide an explicit asynchronous *Test connection* action.
- Ensure orderly Telethon disconnect on success, timeouts, exceptions and process shutdown. Await child tasks or cancel safely; close event loops only after cleanup. Test Windows network disruption and reconnect without pending-task warnings.
- Handle real `FloodWait`, rate-limits, invalid/expired authorization, channel access errors and networking separately from session SQLite locking. Don't mask failures with catch-all success paths.
- Never duplicate or serialize session credentials into a public socket, browser payload, logs or the generated edition.

## 4. Application DB write behavior — mandatory

- Audit all transactional boundaries and concurrent worker access. Database operations should be short and deliberately scoped; fetch network data outside write transactions; use staged batches or a local queue to persist results in bounded commits.
- Reconcile SQLAlchemy and SQLite config: correct paths, WAL mode where appropriate, `busy_timeout` on all app DB connections, proper connection lifecycle, rollback-on-exception and closure, concurrency-safe session usage. WAL permits readers with one writer; it does **not** make multiple concurrent writers safe.
- Add bounded retry/backoff for *genuinely transient* `SQLITE_BUSY` after rollback and new transaction, especially idempotent commits. Never retry non-idempotent writes blindly or hide repeated failure.
- Web actions like schedule update, job cancellation, branding save and audit events must not fail merely because a healthy collection worker is processing. Return a safe, actionable non-500 status if the DB is temporarily unavailable after the bounded attempt.
- Preserve audit integrity and ownership checks. Do not solve the problem by disabling audit logging or access controls.

## 5. Workers, window locks, progress and retries

- Fix race conditions between `sync_window` and `edition` for the same channel/date range. An edition may reuse already ingested posts/await a collector rather than launching another competing sync of the exact window.
- Model lock ownership explicitly: session/DB transaction atomic claim, worker ID and process identity, heartbeat where necessary, lease TTL, graceful release, stale owner detection/recovery, no premature lock stealing. A second worker must wait/defer/requeue without being counted as a destructive processing failure.
- Guarantee one active collector per stable source/window; avoid accidental duplicate worker launches from repeated clicks, browser refresh, Task Scheduler and retry mechanisms.
- Persist intermediate retrieval checkpoint separately from last successfully committed edition cutoff. Do not advance cutoff while the build is failed/incomplete. Preserve special/weekly/daily independence and half-open interval semantics.
- Ensure retries of the same interval are idempotent: `(stable_channel_id,message_id)` uniqueness, media/hash deduplication, no duplicate editions, correct transaction rollback and clear job states.
- Cancel/stop/resume should be safe at each state, with clear statuses. No indefinite zombie `running` job; no silent deletion of failed jobs.
- Do not auto-release or publish during these repairs. An owner must approve first real edition.

## 6. Windows worker launcher and observability

- Fix `scripts/run-worker.ps1` stderr handling so Alembic INFO lines do not masquerade as fatal `NativeCommandError`; respect true exit code and retain stderr from genuine failures. Avoid throwing away native stderr by redirecting it to nowhere.
- Ensure detached workers do not outlive required shutdown policy unpredictably and have appropriate logging independent of the browser; job details should show meaningful state and last error ID.
- Keep log formats source-safe and redact secrets, session file contents, phone numbers, codes and protected Telegram content. Unexpected exceptions get traces in private logs, not raw HTML to browsers.
- Surface a `session busy / active collector / waiting for worker / retry scheduled / network disconnected` state rather than a generic 500 or misleading success message.

## 7. Test, integration and documentation obligations

Execute the accompanying `CONCURRENCY_ACCEPTANCE_MATRIX.md` comprehensively. Existing suite + lint must remain green. Add real regression tests for database write contention, cross-worker session ownership, job claim/release, timed-out GET, interrupt/resume and worker stderr.

In `docs/CONCURRENCY_RECOVERY_REPORT.md`, report:

- exact root causes and proof, paths/functions, before/after state;
- committed code and migrations (if unavoidable), no data loss;
- test commands, counts and runtime outcomes (fixture versus live clearly labeled);
- explicit authorized Telegram live-validation status, cutoff window, message counts, job outcomes and manuscript/PDF proof only if actually achieved;
- steps for starting/stopping and diagnosing web/worker safely;
- any unresolved operational risk and user action needed.

Update only the relevant existing `README.md`, `USER_MANUAL.md`, `ADMIN_GUIDE.md`, `TROUBLESHOOTING.md`, and `IMPLEMENTATION_LOG.md`. Do not overwrite earlier implementation history. Commit focused changes by subsystem after testing, and give the owner a short final checklist.

## Definition of done

A web user can queue one authorized cutoff edition while a Telegram collection is active; concurrently view source detail, save a schedule, save branding, inspect jobs and cancel a cancellable job without SQLite `database is locked`, indefinite wait or raw HTTP 500. The worker makes deterministic forward progress or enters an honest `waiting/retry/blocked` state, respects source rights and cutoff boundaries, and can resume after a crash without duplicates. Telethon session-file writer contention and unclosed pending tasks are eliminated in verified tests. A supervised first-live test is a separate required checkpoint; never claim it passed if live access was not available.
