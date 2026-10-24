# Failure evidence — 2026-10-23

This document records observations from user-supplied console and log files. It **does not** prove which process holds each lock. Reinspect the current repository, live processes, database connections and job records before attributing causality.

## Attached log sources in the support conversation

- `Pasted text.txt` — captured Uvicorn stdout/stderr and HTTP events.
- `web-20261023.log` — web exception JSONL.
- `worker-20261023.log` — worker exception JSONL.
- `worker-err-20261023.log` — PowerShell/native command stderr.
- `worker-web.log` — worker output, Telethon reconnect warnings and pending tasks.

All of these are **historical copies**. The agent must inspect the actual on-disk logs in `E:\newsroom\data\logs\` and any other configured worker output, rather than assuming the current process state is unchanged.

## A. Queue button is no longer the original failure

The web console showed:

```text
POST /build HTTP/1.1" 303 See Other
GET /jobs/4?msg=Build+queued.+Detached+worker+started... 200 OK
```

Meaning: the HTTP queue submission completed and Job #4 was created. This is **not** proof that the detached worker finished the edition. The previous `PublicationProfile.cutoff_local` defect should not be re-fixed or used as an explanation without fresh evidence.

## B. Main application database cannot always commit

- `POST /schedules/1` returned `500`: `channel_press/web/app.py`, `schedule_update`, line about 1895, at `db.commit()`; SQL insert into `audit_events`; underlying `sqlite3.OperationalError: database is locked`.
- `POST /jobs/2/cancel` returned `500`: `job_cancel`, line about 2114, same underlying SQLite lock during audit-event insert.
- Other dashboard updates were reported affected. Audit every code path that can hold long write transactions (scheduler, collection callbacks, web handlers, audit).

**Interpretation:** the app SQLite database experienced write contention, or a stalled long-lived transaction. A commit failing while inserting an audit event is not proof that the audit table caused the original lock. Locate the competing writer(s), when the write lock was acquired, and how long it was held.

## C. Telethon session DB independently experiences lock contention

The worker stack includes:

```text
telethon/sessions/sqlite.py:process_entities
sqlite3.OperationalError: database is locked
```

- Job #3 `sync_window` fails during Telethon `connect()` / `get_me()` / entity processing.
- Job #4 `edition` fails during channel `get_entity(username)` / entity processing.
- Web `GET /sources/2` invokes `telegram_service.status(probe=False)` and times out after approximately 45 seconds; an ordinary source-detail request should not block on Telegram networking.

**Interpretation:** likely contention over Telethon's own SQLite session file between client owners or unclosed clients. Prove who shares the file and why. This is logically distinct from the app DB lock; don't try to fix both by changing only SQLAlchemy settings.

## D. Window coordination blocks progress

Job #4 recorded repeated failures:

```text
PublicationError: Another worker is already collecting this window. It will not be built twice.
```

Investigate job creation, lock/lease acquisition and release, active/stale-lock determination, nested sync-window + edition collection, retries and crashes. Multiple workers must not duplicate collection, yet one valid draft should eventually progress or be explicitly deferred with meaningful state.

## E. Additional issues / secondary evidence

- `worker-web.log`: repeated `WinError 121` (semaphore timeout) / `WinError 1236` (network aborted), Telethon reconnect warnings, `Task was destroyed but it is pending!`, `coroutine ignored GeneratorExit`.
- `worker-err-20261023.log`: `scripts/run-worker.ps1` treats native `python.exe` Alembic INFO stderr output as a PowerShell `NativeCommandError`. Separate informational stderr from actual nonzero exit codes; do not suppress real failures.
- `POST /schedules/new` returned HTTP `422 Unprocessable Entity`. This can be validation rather than concurrency; independently inspect submitted fields. Handle gracefully and test if a genuine app defect.
- One later worker pass shows `enqueued=0 processed=0 ... failed=0`, which is **not** proof that prior jobs succeeded or an edition was produced.
- The log includes at least one successful Telegram channel resolution in the wider trace; Telegram API credentials are not categorically invalid. Do not automatically ask to reauthenticate.

## Root-cause questions the new agent must answer

1. Exactly which SQLite files exist, and which engine/session classes access each?
2. Which processes/PIDs own live Telegram connections and the Telethon `.session` file?
3. Are SQLAlchemy sessions/connections held across network calls, LLM inference, sleeps or PDF work?
4. Are multiple detached workers launched per UI action or by Windows Task Scheduler? Can they acquire conflicting job/window leases?
5. Are stale locks persisted after errors, and is the intended job queued, running, retrying, failed or awaiting review?
6. Does the web UI issue network-dependent calls merely to render GET pages?
7. Do network timeouts and shutdown paths always disconnect Telethon and terminate asyncio tasks cleanly?
8. Is an audit insert only the first blocked write, rather than the source of the hold?

Use the actual runtime and current repository as authoritative. Never claim the cause is solved based only on stack traces from the attachment.
