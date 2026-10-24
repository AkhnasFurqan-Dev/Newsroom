# Channel Press — concurrency recovery report (2026-10-24)

**Checkout verified:** `E:\newsroom - Copy` (this workspace). The owner's
`E:\newsroom` tree, database, media, editions and Telegram session were not
modified. Repetitive and destructive verification ran only against disposable
databases under `data/` (gitignored).

**Scope:** `CONCURRENCY_RECOVERY_CONTRACT.md`, the evidence in
`CONCURRENCY_FAILURE_EVIDENCE.md`, the gates in
`CONCURRENCY_ACCEPTANCE_MATRIX.md`, and the controlled restart / preflight
requirements in `START_CONCURRENCY_RECOVERY.md`. No module was rebuilt and no
editorial or layout behaviour was redesigned.

## 1. Result in one paragraph

The app database no longer loses interactive writes to a long-lived writer: write
transactions are committed before Telegram, model, media and PDF work, a bounded
retry handles genuinely transient `SQLITE_BUSY`, and an exhausted retry returns a
friendly `503` instead of a raw `500`. Telegram now has exactly one session owner
per action across threads **and** processes, releases the Telethon session on
success, timeout and exception, and never contacts Telegram to render a page. An
edition that finds its window already being collected now **defers** instead of
failing, with one durable job per window. A real two-process run (web server +
detached worker + dashboard writes) completed with 128 concurrent dashboard
requests, worst latency 16 ms and no `5xx`, while the job moved
`scheduled → running → awaiting_review` and produced a 7-page tabloid PDF. Live
Telegram collection is **BLOCKED in this checkout** (no session file; see §7) and
is not claimed as verified.

## 2. Root causes

### 2.1 Application SQLite write contention (observed + code-proven)

**Observed.** The historical logs show `sqlite3.OperationalError: database is
locked` at `db.commit()` in `POST /schedules/{id}` and `POST /jobs/{id}/cancel`,
with the first blocked statement being the `audit_events` insert. That only proves
which write arrived second; it does not identify the holder. This report does not
pretend otherwise.

**Code-proven holders.** The application database has one writer at a time. The
following paths held, or could hold, that write transaction across non-database
work:

- `channel_press/collection/service.py` — `collect_window` performed retrieval
  while the caller's session still had uncommitted job/claim state; the page
  callback then opened a second session, so the retrieval's checkpoint write
  competed with the caller's open transaction (and with `busy_timeout=15000` the
  second writer waited up to 15 s before failing).
- `channel_press/pipeline.py` — the pipeline ran story building, model drafting
  and PDF rendering between flushes. Instrumentation added in this repair measured
  a single **7,343 ms** write transaction in the fixture build (`transaction_end
  duration_ms=7343`, warning level), which is longer than any interactive request
  can wait.
- `channel_press/editorial/drafting.py`, `channel_press/layout/engine.py`,
  `channel_press/media/service.py` — each continued a dirty session into a model
  call, a Playwright render or a Telegram media download.
- `channel_press/web/app.py` — several handlers performed Telegram work while the
  request session held pending writes (Build, source Telegram actions).

**Fix.** Commit before every external call; persist page checkpoints in short,
own-session transactions; `busy_timeout=1000`; `retry_busy()` replays only
caller-declared idempotent units in a fresh transaction (3 attempts, 100/200 ms);
`OperationalError` on a busy database becomes `503` with `Retry-After` and
"your change was not saved". Audit logging, ownership checks and CSRF were not
weakened.

### 2.2 Telethon session-file contention (code-proven)

**Cause.** Every process constructed its own `TelegramService`/Telethon client
against the same `data/telegram.session` SQLite file. The web process and the
detached worker could therefore both own the file; nothing serialised them, and
timeouts could leave the client (and its SQLite handles) open, which is the
`telethon/sessions/sqlite.py:process_entities` lock in the evidence.

**Fix.** One serialised gateway (`TelegramService._operation`) with three gates:

1. an asyncio lock inside the owning event-loop thread;
2. a cross-process `FileLease` on `<session>.owner.lock`, taken **before**
   Telethon opens the SQLite file (a second process receives
   "Telegram session currently busy in another process" and never touches it);
3. a bounded `asyncio.wait_for`, after which the transport is disconnected, the
   Telethon session is closed and the lease released in `finally`.

`TelegramService.close()` cancels child tasks, awaits them, disconnects, then stops
and closes the loop; it is called on web shutdown, worker exit and CLI probes.
`status(probe=False)` no longer touches the network at all — it returns the cached
last-known result or an explicit "not tested" state. The explicit *Test
connection* action performs a bounded preflight.

### 2.3 Window coordination and duplicate collection (observed + code-proven)

**Observed.** Job #4 failed with "Another worker is already collecting this
window. It will not be built twice." The edition treated a legitimate concurrent
claim as a hard publication failure.

**Cause and fix.**

- `sync_window`/`collect_context` jobs used random UUID keys, so repeated clicks
  could create several jobs for one window. They now use the stable
  `{mode}:{window_key}` key, and a failed/cancelled row is re-armed instead of
  silently returning a dead job.
- Manual builds keep one deterministic key per channel/kind/interval and now also
  reuse `running` and `cancel_requested` rows instead of re-arming a live job.
- `EditionWindowRecord` ownership is a compare-and-set claim with owner, lease
  expiry and heartbeat. An edition whose window is owned elsewhere raises
  `CollectionBusy`, which the worker converts to `scheduled` +
  `waiting_for_collector`, defers 30 s, rolls the attempt back and releases its
  lease — no destructive failure, no duplicate posts.
- A completed collection is reused (`CollectionStats.complete`) instead of being
  re-fetched by the edition.
- Stale-lease recovery only reclaims when the recorded **local** process is
  provably gone (Windows `OpenProcess`, POSIX `kill(0)`) and its heartbeat is
  older than `worker_heartbeat_timeout_seconds`; a live owner is never stolen.
  Remote owners are only reclaimed after their explicit lease expires.
- A `cancel_requested` job whose worker died is finalised as `canceled`; it can no
  longer become a zombie. `paused` jobs are resumable from the job page.

### 2.4 Web status blocking on Telegram (observed + code-proven)

**Cause.** `GET /sources/{id}` called `telegram_service.status(probe=False)`,
which still submitted a real connection probe. With an unreachable network this
waited for the transport timeout (≈45 s).

**Fix.** Non-probe status is memory-only and cannot start a loop, socket or
session. A regression test calls it ten times with a saved session and asserts
`_loop is None` and `< 0.2 s` total. In the multi-process proof, `/sources/1`
answered in 32 ms with no Telegram session present.

### 2.5 Worker launcher stderr (observed + code-proven)

`scripts/run-worker.ps1` piped native `python.exe` stderr through PowerShell, so
Alembic `INFO` lines surfaced as a `NativeCommandError`. The launcher now uses
`Start-Process` with separate stdout/stderr files, appends them to the daily logs,
and exits the **real** process code. A test runs the real script with a probe
program that writes `INFO` to stderr and exits 0 and 7; both codes propagate and
the INFO text is retained.

### 2.6 Two defects found while verifying the restart path

1. `release_expired_locks` referenced `owner_process_alive` without importing it
   (ruff `F821`). Any stale `running` job would have raised `NameError` on the
   next worker pass. Fixed and covered by the crashed-lease test.
2. `scripts/restart-services.ps1` could never succeed: its final ownership check
   counted the freshly started **web** service as a lingering worker. It now
   counts `kind = worker` only. It also started the web through PowerShell, which
   made the server inherit the caller's stdout pipe: any caller capturing the
   script's output hung forever even though the restart had succeeded. The web is
   now started by `python -m channel_press.operations start-web`, which uses
   `DETACHED_PROCESS` with file stdio. The script returns in ~15 s.

### 2.7 Not a defect: two PIDs per `.venv` Python on this host

This checkout's `.venv\Scripts\python.exe` is a launcher that re-executes the base
interpreter: one invocation shows a venv-path parent and a base-interpreter child
with the same command line (verified directly). Process accounting therefore
records the **real** PID in leases and in `data/services/*.json`; the stub holds no
lock. Do not "fix" this by killing the extra PID — it is one logical process.

## 3. What changed (focused)

| Area | Files |
| --- | --- |
| DB writes and retry | `channel_press/db.py` (`busy_timeout=1000`, transaction timing, `is_busy`, `retry_busy`) |
| Cross-process leases | `channel_press/locking.py` (new: `FileLease`, `ResourceBusy`, `owner_process_alive`) |
| Collection | `channel_press/collection/service.py` (commit before I/O, page-level checkpoint sessions, lease heartbeat, failed-window release, incomplete-retrieval error) |
| Scheduling | `channel_press/scheduling/service.py` (stable keys, re-arm, `CollectionBusy` deferral, stale/zombie recovery, `worker_once` single gate) |
| Pipeline | `channel_press/pipeline.py` (resume an existing edition, stage story IDs, commit between model calls) |
| Drafting / layout / media | commit before provider, renderer and download calls |
| Telegram | `channel_press/telegram/service.py`, `transport.py` (single owner, bounded preflight, disconnect/close, clean shutdown, cached status) |
| Web | `channel_press/web/app.py` (Build preflight, sync/context preflight, 503 handler, `waiting_for_collector`, cancel-requested, System Health + restart endpoints) |
| UI | `dashboard.html.j2` (System Health), `job.html.j2` (lease/error detail), `static/js/health.js` |
| Operations | `channel_press/operations.py` (new: stop/resume/owners/validate/health/probe/start-web, managed-service registry, web shutdown watcher) |
| CLI | `cli.py` (worker gate, clean Telegram close, managed serve) |
| Scripts | `run-worker.ps1`, `stop-services.ps1`, `restart-services.ps1`, `service-health.ps1` |
| Tests | `tests/test_concurrency_recovery.py` (29 tests), `tests/concurrency_proof/` (repeatable multi-process driver) |

No schema migration was required; all existing tables and rows are preserved.

## 4. Verification

### 4.1 Automated suite

```powershell
.\.venv\Scripts\python.exe -m ruff check channel_press tests
.\.venv\Scripts\python.exe -m pytest
```

- ruff: `All checks passed!`
- pytest: **168 passed, 2 skipped** (97 s). The two skips are
  `tests/test_ollama_live.py` because Ollama is not running at
  `http://127.0.0.1:11434` in this session; they are labelled, not hidden.
- The suite includes all pre-existing print/PDF/e-paper tests unchanged.

### 4.2 Multi-process proof (Windows, disposable database)

Command (from the repo root, with `CHANNEL_PRESS_*` env vars pointing at
`data/concurrency-proof`):

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli demo --posts sample --username owner
.\.venv\Scripts\python.exe tests\concurrency_proof\driver.py
```

Result (saved as `data/concurrency-proof/proof-run.json`, gitignored):

| Phase | Evidence |
| --- | --- |
| Web boot | `serve` answered `/login`; managed registry contained one `web` service |
| Idle reads | `/` 15 ms, `/sources/1` 32 ms, `/jobs` 15 ms, `/schedules` 15 ms |
| Duplicate Queue Build | two `POST /build` both returned job **#1**; exactly one job for the window |
| Real worker + dashboard | 128 requests during the worker run; statuses `200/303`; worst 16 ms; job statuses observed `scheduled`, `running`, `awaiting_review`; timeline 07:12:18 → 07:12:22; job finished `awaiting_review`, edition #2 |
| Edition | 7-page 792 × 1224 pt (11 × 17 in) PDF, page text 590–5360 chars per page, none blank |
| Active collection + desk | holder process owns job #2 + window lease; `GET /sources/1` 0 ms, schedule save 16 ms, branding save 0 ms, `/jobs` 0 ms |
| Cancel at checkpoint | `POST /jobs/2/cancel` 15 ms → `cancel_requested` → holder stops at page 1, job `canceled`, window lock released |
| Second worker | exits **2** with "Another Channel Press worker is already running." |
| Session ownership | while another process holds the session lease: `Telegram session currently busy in another process`; after release: `ready` (fake transport, no network) |
| Graceful stop | stop request → web exit 0, owners `{count: 0}` |
| Controlled restart | `restart-services.ps1` exit 0 in 14.6 s; web answered again; no worker owner |
| Data integrity | `PRAGMA integrity_check` = `ok`; posts 26 → 26; duplicate editions 0; audit 2 → 61 |

### 4.3 Acceptance matrix

| ID | Status | Evidence |
| --- | --- | --- |
| C01 | **PASS** | multi-process boot, graceful stop, restart script, owners = 0 |
| C02 | **PASS** (fixture) | status never connects (test); `/sources/1` 32 ms; no 45 s path remains |
| C03 | **PASS** (session lock) | cross-process lease tests + `session_while_owned` blocked / released ready |
| C04 | **PASS** | CAS claim test; `CollectionBusy` deferral test; second worker exit 2 |
| C05 | **PASS** | 128 concurrent requests while worker `running`; schedule/branding/audit all succeeded |
| C06 | **PASS** | `retry_busy` test (fresh transaction, one audit row); exhausted → `503` + `Retry-After` |
| C07 | **PASS** (fixture) | timeout disconnects and releases lease; network error after success releases owner |
| C08 | **PASS** | stop mid-pagination keeps checkpoint; resume fetches all 30 without duplicates |
| C09 | **PASS** | crashed owner reclaimed; live local owner not stolen; cancel zombie finalised |
| C10 | **PASS** | duplicate build, duplicate sync job, duplicate manual job all one row |
| C11 | **PASS** | queued/running/finished cancel states; running → checkpoint, finished rejected |
| C12 | **PASS** | existing half-open cutoff tests unchanged |
| C13 | **PASS** | existing context/backfill tests unchanged |
| C14 | **PASS** | real `run-worker.ps1` test: exit 0/7 preserved, INFO stderr retained |
| C15 | **PASS** | polling + `/api/jobs/{id}` during collection; no secret leak |
| C16 | **PASS** | full existing suite, including print/PDF/QC |
| C17 | **BLOCKED — requires authorized live setup** | no `data\telegram.session` in this checkout; see §7 |
| C18 | **BLOCKED (live half); fixture half PASS** | fixture edition + PDF above; live draft requires C17 |

## 5. Operating the repaired services

```powershell
# Health: configured vs tested
powershell -ExecutionPolicy Bypass -File .\scripts\service-health.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\service-health.ps1 -Validate -Probe

# Graceful stop (waits; never kills unrelated processes)
powershell -ExecutionPolicy Bypass -File .\scripts\stop-services.ps1

# Controlled restart: validate → graceful stop → resume → model/health → web → readiness
powershell -ExecutionPolicy Bypass -File .\scripts\restart-services.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\restart-services.ps1 -ProbeTelegram
```

Stop semantics: `stop-services.ps1` creates `data/services.stop`; the web watcher
and the worker checkpoints observe it, finish the current bounded step, release
leases and exit. No kill is issued. The stop file remains until
`operations resume` (which the restart script runs). Never delete the database,
WAL/SHM files, session, jobs or checkpoints.

Diagnostics (read-only):

```powershell
.\.venv\Scripts\python.exe -m channel_press.operations validate
.\.venv\Scripts\python.exe -m channel_press.operations health
.\.venv\Scripts\python.exe -m channel_press.operations owners
```

## 6. Supervised first live edition (owner steps)

1. Confirm `.env` has the API id/hash and the publication's source handle, then
   start the app normally (`python -m channel_press.cli serve`).
2. In the dashboard, **Source desk → the channel → Test source connection**.
   Expected: **Telegram Ready: @handle (UTC timestamp)**. A failure is shown as a
   concrete reason (unreachable, authentication expired, channel not accessible,
   session busy, authorization missing, timed out).
3. Open **Build**, choose the exact cutoff window and enable live collection, then
   **Queue Build**. The backend repeats the bounded preflight; no job is created if
   it fails.
4. Watch the job page. `waiting_for_collector` means another collector owns that
   exact window; it retries in 30 s and is not a failure. `paused` means the stop
   policy interrupted a checkpoint; use **Retry / resume job**.
5. The edition lands in `awaiting_review`. Inspect the PDF and sources before any
   release. Automatic publication is still off.

## 7. Live Telegram status — BLOCKED (not claimed as verified)

- `CHANNEL_PRESS_TELEGRAM_API_ID` / `_API_HASH`: **configured** in this checkout.
- `data\telegram.session`: **absent** (0 bytes / missing). `settings.telegram_source_username` is empty.
- Therefore no authenticated account exists here and no channel can be resolved.
  No live message was fetched, no live job was run, and no live source counts are
  reported. The session-ownership behaviour was proven with the real ownership
  gate and a fake transport, which is **not** evidence of Telegram credentials
  working.

To unblock, the owner must complete the local login on the machine that holds the
session (`Source desk → Telegram → Sign in`) and record a rights declaration. Then
run one small, supervised window with auto-release off.

## 8. Residual risks

- **Historical lock holder** cannot be reconstructed from the attached logs. The
  repair removes the known long-transaction paths and bounds contention, and the
  multi-process proof exercises the corrected behaviour; it does not prove which
  old process held the lock on the owner's machine.
- **Live Telethon behaviour** (FloodWait, reconnect, Windows network loss with a
  real session) is unverified here. The timeout/ownership paths are tested.
- **Two web processes** can still be started manually on two ports; each registers
  itself. Only one can bind a port, and the restart script refuses to start when
  an unmanaged server answers. Do not run two on the same database expecting
  safety.
- **Stop file** persists after a stop until `resume`; a manually started server
  will print the paused message and exit. This is intentional.
- The `.venv` launcher stub means two PIDs per process; monitor by the real PID in
  `data/services/`, not by process count.

## 9. Owner checklist

- [x] Application DB contention bounded; interactive writes succeed under load.
- [x] One Telegram session owner across threads and processes; no page-time network call.
- [x] One collector per window; editions defer instead of failing.
- [x] Crash/stop/resume keeps checkpoints; no duplicate posts or editions.
- [x] Cancel and retry behave at every state; no zombie `cancel_requested`.
- [x] Real PowerShell launcher keeps stderr and exit codes.
- [x] Graceful stop, health check and controlled restart scripts verified on Windows.
- [x] Full suite, lint and fixture PDF verified.
- [ ] Supervised live Telegram collection and first live draft — requires the owner's session and rights.
