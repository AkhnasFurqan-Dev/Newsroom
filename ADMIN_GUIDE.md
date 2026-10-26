# Channel Press — Administrator Guide

This guide covers installation, accounts, rights, secrets, models, schedules,
backup and security. Day-to-day editing is in [USER_MANUAL.md](USER_MANUAL.md).

## 1. Installation and upgrade

### Baseline

- Windows 10/11 with PowerShell.
- Python 3.12 (64-bit).
- Google Chrome. The print pipeline launches the installed Chrome channel through
  Playwright; Chromium bundled by Playwright is not required.
- ~2 GB free disk for the virtual environment plus space for media and editions.

### Install

```powershell
cd E:\newsroom
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

The script:

1. creates `.venv` if missing;
2. installs pinned dependencies with `pip install -e ".[test,video]"`;
3. copies `.env.example` to `.env` when absent;
4. checks for Chrome and warns if it is missing;
5. runs `init-db` to create or upgrade the SQLite schema.

Optional integrations are installed separately:

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[telegram]"   # live MTProto adapter
```

Local model support is not installed by this script. Install Ollama separately if
required (see §5).

### Upgrade

1. Stop the server and any worker task.
2. Back up first: `.\.venv\Scripts\python.exe -m channel_press.cli backup`.
3. Update the source and reinstall: `.\scripts\install.ps1`.
4. Start the server; `init-db` runs Alembic migrations on startup.
5. Run `diagnostics` and inspect the reported revision.

Dependencies are pinned in `pyproject.toml`. Review release notes before changing
pins, and test a fixture build after any upgrade.

## 2. Accounts and roles

The first account is created by **Set up Channel Press** or:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli setup --demo-channel
```

`setup` prompts for a password unless `CHANNEL_PRESS_SETUP_PASSWORD` is set. The
`--demo-channel` flag also creates the synthetic fixture desk.

### Role matrix

| Capability | Viewer | Operator | Editor | Owner |
| --- | --- | --- | --- | --- |
| Read accessible channels, stories, articles, editions | Yes | Yes | Yes | Yes |
| Queue builds and run jobs | — | Yes | Yes | Yes |
| Import content, edit articles, resolve flags | — | — | Yes | Yes |
| Release editions, manage schedules | — | — | Yes | Yes |
| Inspect and clear the media cache | — | — | Yes | Yes |
| Register channels, manage users, branding | — | — | — | Yes |
| Record or revoke rights declarations | — | — | — | Yes |
| Inspect diagnostics/audit | — | — | — | Yes |

Roles are ranked (`viewer` < `operator` < `editor` < `owner`). Channel-level
permissions can grant a user access to one channel without making them a global
editor. Publication membership (`PublicationEditor`) grants a role on one
newspaper without exposing any other paper. A user who can already open one of a
paper's channels keeps that access after an in-place upgrade, so a single-paper
installation does not lock its existing editors out. Owners always have access.

Every page, edition download and action resolves the working newspaper from the
request (query parameter, then the `cp_publication` cookie, then the first paper
the user may open). The server re-checks the resolved paper; a cookie or URL
parameter cannot widen access.

### Newspapers and sources

- An owner creates a newspaper under **Branding → Newspapers on this computer**.
  Deactivating a newspaper keeps its records and archive but removes it from the
  selector and from due-schedule expansion.
- A newspaper's source list is explicit. Newly registered channels are linked to
  the newspaper that was active when they were created; older channels with no
  link are treated as **unassigned** and remain buildable for compatibility.
- Source priority and weight are operational choices, not rights. Rights are
  still recorded per channel and checked for every non-demo source in a build.
- A schedule belongs to one newspaper. A due run expands that paper's enabled
  sources and records the source list on the job and edition. Removing a source
  later does not rewrite the edition's source record.

### Users page

Owners can:

- **Create user** with username, display name, role and initial password.
- **Change role** in the users table.
- **Deactivate** or reactivate an account.
- **Reset password** for another user.

The last active owner cannot be demoted or deactivated. Deactivating a user ends
their ability to sign in but preserves their audit history.

Self-service password changes are on **Account**. Passwords are hashed with
Argon2id; there is no password recovery by email. An owner resets a forgotten
password:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli set-password --username owner
```

### Login protection

Failed logins are throttled (5 attempts per 15 minutes by default). Sessions
expire after 12 hours by default. Every mutating form carries a CSRF token, and
every route checks the session server-side.

## 3. Rights and consent

Rights are recorded per channel before any live content is imported or processed.

- **Grantor**, **authority basis** and **evidence reference** are mandatory for a
  new channel.
- Scopes are **Ingest**, **AI processing** and **Publication**. The default form
  selects all three; record only what the grantor actually authorized.
- Revocation records the reason, timestamp and actor. It blocks future imports,
  AI processing and builds for the channel.
- Public read access does not imply republication rights. The app does not bypass
  Telegram restrictions.

The fixture desk is synthetic and exempt. It must never be used to launder real
content into a demo edition.

### Live Telegram credentials

The application includes a Telethon user-session adapter for authorized history
access. It requires:

- `CHANNEL_PRESS_TELEGRAM_API_ID` and `CHANNEL_PRESS_TELEGRAM_API_HASH` from the
  owner's Telegram developer account (`.env`, never committed);
- `CHANNEL_PRESS_TELEGRAM_SESSION_PATH` under the ignored data directory
  (default `./data/telegram.session`);
- the optional Telethon package (`pip install -e ".[telegram]"`);
- a recorded rights declaration before any content is processed.

The flow is implemented in the app, not a detached script:

- **Sources → Telegram connection** saves the handle and resolves it to a stable
  numeric channel id with **Test source connection**;
- the phone login, code and two-step password are accepted only in that local
  form and are held in memory for the login window, never written to `.env`, the
  database, logs or the session file;
- **Import last 30 days (context only)** stores context without committing a
  daily window;
- **Sync window** collects exactly the effective `[start, end)` window;
- **Build** can collect the window first with **Collect this window from Telegram
  before building**;
- a session file behaves like a password. **Sign out** deletes it; revoke the
  session in Telegram if the device is lost.

**Live login and retrieval remain unverified in this build.** The fixture and
rights-cleared JSON import routes are the verified intake paths. The real target
channel (`The Pakistan News`) still needs the owner's API values, session login,
an active rights grant and one supervised collection/build. Do not report the live
path as working until that run is completed and its output inspected.

## 4. Secrets

| Secret | Location | Notes |
| --- | --- | --- |
| Session signing key | `data/secret.key` | Generated on first use, mode 600 where supported |
| Telegram API ID/hash | `.env` or environment | Never committed |
| Telegram session | `data/telegram.session` | Never committed; revoke at Telegram if lost |
| User passwords | SQLite, Argon2id hash | Not recoverable; reset instead |
| Backup archive | `data/backups/*.zip` | Contains the database, media and optionally the secret |

Rules:

- `.env`, `data/`, `output/` and session files are gitignored.
- Secrets are never rendered into HTML, PDFs, manifests or logs.
- `diagnostics`, preflight and the Telegram page report configured / missing /
  session-present only, never values. Tests assert the API id and hash do not
  appear in rendered pages or log output.
- Login codes and two-step passwords live only in process memory for the login
  window and are never written to disk.
- For a managed deployment set `CHANNEL_PRESS_SECRET_KEY` from a protected store
  and `CHANNEL_PRESS_COOKIE_SECURE=true` behind HTTPS. The default is a local
  HTTP server bound to `127.0.0.1`.

## 5. Local models

The default is deterministic and free. Non-fixture drafting can use Ollama:

```powershell
ollama pull qwen2.5:3b-instruct
ollama serve
```

Configuration (`.env`):

```text
CHANNEL_PRESS_OLLAMA_BASE_URL=http://127.0.0.1:11434
CHANNEL_PRESS_OLLAMA_MODEL=qwen2.5:3b-instruct
CHANNEL_PRESS_OLLAMA_KEEP_ALIVE=60s
CHANNEL_PRESS_OLLAMA_TIMEOUT_SECONDS=240
CHANNEL_PRESS_OLLAMA_MAX_CONCURRENCY=1
CHANNEL_PRESS_OLLAMA_UNLOAD_AFTER_JOB=true
```

All app settings use the `CHANNEL_PRESS_` prefix. The adapter:

- sends only the story evidence bundle and the versioned prompt;
- requires a strict schema and retries a limited number of times;
- extracts fenced JSON, truncates oversized input and matches the model name
  exactly;
- serialises model loads with a global gate (`ollama_max_concurrency=1`) and can
  unload after the job;
- falls back to the deterministic composer with a recorded reason when the model
  is unavailable or invalid;
- never treats model output as verified. A separate deterministic audit checks
  every fact sentence against the claim ledger.

### Choosing a model for 16 GB / RTX 3060 Laptop

The build machine has **6144 MiB** of VRAM, not 8 GB. Start with a 3–4B instruct
model quantized to Q4. `qwen2.5:3b-instruct` (1841 MB) is installed and verified.
Larger models may fit only at low quantization and short context; measure before
adopting. Keep `OLLAMA_KEEP_ALIVE=60s` so the model unloads between scheduled
runs.

Verified result on this machine: a 30-post edition with `qwen2.5:3b-instruct`
produced 17 stories, 17 articles and 8 pages in 57–62 s, with no fixture fallback
and a peak VRAM of 2213 MiB. A 70- or 150-post real-model run was not performed.

Measure with:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli demo --posts 30
.\.venv\Scripts\python.exe -m channel_press.cli diagnostics
nvidia-smi
```

Record wall time, peak RAM/VRAM and whether a fallback reason appears. `demo`
uses the fixture provider; the real-model proof is
`pytest -m live tests\test_ollama_live.py`.

## 6. Schedules and the Windows worker

The web UI manages schedule records. Actual wakeups come from Windows Task
Scheduler:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-task.ps1
```

This registers `ChannelPressWorker`, running `scripts\run-worker.ps1` every 15
minutes. The script performs one worker pass and exits. Logs go to
`data\logs\worker-YYYYMMDD.log`.

Design guarantees:

- **Deterministic job keys** — channel + kind + cutoff. Repeated triggers cannot
  create a second edition for the same cutoff.
- **Exact windows** — eligibility is `[previous committed cutoff, current
  cutoff)` in UTC, derived from the configured IANA timezone. A post at exactly
  the cutoff belongs to the next issue; a late worker start never moves it.
- **Watermark catch-up** — missed cutoffs are enqueued in order from the last
  committed cutoff, bounded by `max_catchup_windows` (7 by default).
- **Commit boundary** — the watermark advances only when a release is committed
  or a window is verified empty (`no_content`). Collection start, a failed model
  or a pending review does not advance it.
- **Checkpointed retrieval** — pages are staged as JSONL under
  `data/window-staging` and a retrieval checkpoint records the cursor. A crash on
  page 4 resumes without duplicating posts or losing the rest of the window.
- **Single worker gate** — every entry point (`worker --once`, the detached web
  launch, Task Scheduler) acquires one OS file lease in `data/worker.lock`. A
  second pass exits with code 2 and a message instead of competing.
- **Lease and stale-owner recovery** — jobs carry an owner, heartbeat and expiry.
  A crashed local worker is reclaimed only when its recorded PID is provably gone
  and its heartbeat is older than `worker_heartbeat_timeout_seconds`; a live owner
  is never stolen. A remote owner is reclaimed after its explicit lease expires.
- **One collector per window** — `edition_windows` ownership is a compare-and-set
  claim. An edition that finds the window claimed elsewhere is deferred as
  `waiting_for_collector` (about 30 s), not failed; a completed collection is
  reused instead of fetched twice.
- **Cancel and resume** — cancelling a running job records a request and stops at a
  checkpoint; a request whose worker died is finalised as `canceled`, never left as
  a zombie. `paused` jobs resume from **Retry / resume job**.
- **Telegram session ownership** — the web app and worker never open the Telethon
  `.session` concurrently. A serialised gateway takes an OS lease on
  `<session>.owner.lock` before Telethon touches the file, disconnects and closes
  the session after every bounded operation, and reports
  "Telegram session currently busy" instead of a lock error. Page rendering never
  contacts Telegram; use **Test source connection** or **Check Connections**.
- **Database contention** — application writes are short and never span Telegram,
  model, media or PDF work. A transient `SQLITE_BUSY` is retried three times in a
  fresh transaction; if it persists the UI returns `503` with `Retry-After` and
  "your change was not saved", and the audit trail is intact.
- **Review policy** — scheduled builds stage as `awaiting_review`. Auto-release
  happens only when the schedule permits it and the edition has no blocking flags
  or high-risk articles.
- **Capacity honesty** — a job that cannot finish before release is not silently
  published late as on time. Review the job duration and move the cutoff earlier
  if needed.

Useful commands:

```powershell
Start-ScheduledTask -TaskName ChannelPressWorker
Get-ScheduledTask -TaskName ChannelPressWorker
Get-ScheduledTaskInfo -TaskName ChannelPressWorker
Unregister-ScheduledTask -TaskName ChannelPressWorker -Confirm:$false
```

`install-task.ps1` uses `MultipleInstances IgnoreNew` and a 2-hour execution
limit. `run-worker.ps1` keeps a daily log. It launches `python.exe` with separate
stdout/stderr files and exits the real process code, so Alembic `INFO` lines on
stderr are never mistaken for a failure and a genuine nonzero exit is preserved.

### Service lifecycle and health (Windows)

```powershell
# Configured vs connected: services, database, Telegram cache, worker, Ollama/model
powershell -ExecutionPolicy Bypass -File .\scripts\service-health.ps1

# Also validate configuration/storage and run one bounded read-only Telegram check
powershell -ExecutionPolicy Bypass -File .\scripts\service-health.ps1 -Validate -Probe

# Ask every managed service to pause at a checkpoint, then wait for exit
powershell -ExecutionPolicy Bypass -File .\scripts\stop-services.ps1

# Validate → stop → resume → model/health → detached web start → readiness poll
powershell -ExecutionPolicy Bypass -File .\scripts\restart-services.ps1
```

Stop is cooperative: it creates `data/services.stop`, and the web watcher and the
worker finish the current bounded step, release leases and exit. A stop request is
not cleared automatically; `restart-services.ps1` (or
`python -m channel_press.operations resume`) clears it. Never delete the database,
WAL/SHM files, `.session`, jobs or checkpoints to "fix" a lock.

Managed services register in `data/services/<pid>.json` under an OS lease on
`<pid>.lock`. `python -m channel_press.operations owners` lists only live owners.
On this repository's Windows venv, one `python.exe` invocation shows two PIDs (a
launcher stub plus the base interpreter); leases record the real PID, so judge by
`owners`, not by process count. No kill is issued by these scripts; if a managed
service is genuinely unresponsive, stop its recorded PID only after confirming it
is Channel Press.

## 7. Manual overrides

- **Run the latest cutoff now** queues the current window immediately.
- **Enqueue missed cutoffs** catches up from the watermark.
- **Retry failed job** resumes a failed job transactionally.
- **Re-render as version N** rebuilds an edition from the same article set and
  keeps the previous version.
- `run-now --channel <slug>` does the same from the command line.
- `check-edition --edition-id N` prints status, page count, QA warnings, PDF path
  and any review blockers.
- `classify-posts --channel <slug>` re-runs the deterministic post classifier over
  stored rows without building an edition. The pipeline does this automatically for
  the build window; the command is useful after a classifier upgrade or for older
  imported archives. Add `--force` to recheck rows already at the current version.
  Editor overrides are preserved and the pass is recorded as `source.reclassify` in
  the audit log.

Do not edit `manifest.json`, `checksums.json` or files inside a published folder.
Create a new version instead.

### Media lifecycle and cache

Live source media is downloaded only when an edition build needs it, before
layout, under all of these gates:

- an active rights grant covering ingest, AI processing and publication;
- a resolved channel handle and configured Telegram credentials;
- a documentary/relevant asset with usable metadata;
- a per-file size below `CHANNEL_PRESS_TELEGRAM_MAX_MEDIA_MB` (25 MB default;
  video uses `CHANNEL_PRESS_TELEGRAM_MAX_VIDEO_MB`, 120 MB default);
- at most `CHANNEL_PRESS_MEDIA_MAX_ASSETS_PER_STORY` assets per story (3 default);
- at most `CHANNEL_PRESS_MEDIA_DOWNLOAD_ATTEMPTS` attempts per file (2 default).

| Setting | Default | Purpose |
| --- | --- | --- |
| `CHANNEL_PRESS_TELEGRAM_DOWNLOAD_MEDIA` | `true` | Master switch for live media downloads |
| `CHANNEL_PRESS_TELEGRAM_MAX_MEDIA_MB` | `25` | Per-photo/document limit |
| `CHANNEL_PRESS_TELEGRAM_MAX_VIDEO_MB` | `120` | Per-video limit |
| `CHANNEL_PRESS_MEDIA_MAX_ASSETS_PER_STORY` | `3` | Bounded download count |
| `CHANNEL_PRESS_MEDIA_DOWNLOAD_ATTEMPTS` | `2` | Retry budget before marking failed |
| `CHANNEL_PRESS_MEDIA_RETENTION_HOURS` | `72` | New-file protection from cache clearing |

Layout and PDF rendering never contact Telegram; they use local files only. A
missing or failed download leaves the article text renderable.

Downloaded source media is edition working material. It is removed only after the
PDF, e-paper bundle, manifest and checksums are written, verified and the release
is committed. Owner-supplied files imported from disk are a source library, not
edition working material, and are not removed by publication cleanup.

Cleanup is idempotent and skips anything still needed by another draft,
awaiting-review, failed or running edition, an editor lock, an active job, the
retention window, or a published edition with an incomplete bundle. Provenance
rows, captions, credits, checksums, rights links and audit events stay after file
deletion. A cleanup failure is recorded as `pending` in the manifest and an audit
event; it never turns a successful publication into a failed one.

Inspect before deleting:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli media-cache --dry-run
.\.venv\Scripts\python.exe -m channel_press.cli media-cache --clear-unused --confirm
.\.venv\Scripts\python.exe -m channel_press.cli media-cache --finalize-edition <edition-id>
```

The **Media cache** page is restricted to editors, requires CSRF, and audits each
clear as `media.cache_clear`. The CLI refuses deletion without `--confirm`.
Because publication cleanup can remove downloaded media, a backup taken after
release may contain provenance but not every original download. Back up before a
release if the raw source files must be retained indefinitely.

## 8. Backup and restore

### Backup

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli backup
```

Writes `data\backups\channel-press-<timestamp>.zip` containing:

- a consistent SQLite snapshot (`channel_press.db`);
- `secret.key` unless `--without-secret`;
- `media/` unless `--without-media`;
- `backup-manifest.json` with schema, revision, counts and included components.

Published `output/` folders are **not** included. Archive them separately if the
PDF/e-paper history matters. They are reproducible from the database and retained
media only while that media still exists: post-publication cleanup deliberately
removes downloaded source files, so a released edition is the durable record.

Options:

```powershell
... backup --out D:\backups\newsroom.zip
... backup --without-media
... backup --without-secret
```

### Restore

1. Stop the server and disable the worker task.
2. Run:

   ```powershell
   .\.venv\Scripts\python.exe -m channel_press.cli restore --archive <zip> --force
   ```

Restore validates the manifest, takes a pre-restore safety backup, replaces the
database and media, and restores the secret if present. Restart the server and run
`diagnostics`.

Restoring an old database does not delete newer published folders. Remove stale
edition folders deliberately if the archive must be the exact state.

## 9. Export and public links

The baseline publishes locally:

- PDF, print HTML, page proofs, e-paper bundle, manifest and checksums under
  `output/<publication>/<edition folder>/`.

There is no public hosting, upload destination or cloud inference in the baseline.
The **Contact / public URL** and `CHANNEL_PRESS_PUBLIC_BASE_URL` are used only for
source QR codes, and only after a reachability check. With no verified public URL,
the edition prints an honest source appendix instead of a QR code. Never configure
a localhost or placeholder URL to make a QR appear.

If you later add hosting, validate the destination and update the release note and
manifest. Do not mark a release successful because an upload was attempted.

## 10. Security operations

- Keep the app bound to `127.0.0.1` unless you put it behind a TLS reverse proxy.
- Set `CHANNEL_PRESS_COOKIE_SECURE=true` when served over HTTPS.
- Review **Audit** for logins, imports, rights changes, edits, releases, role
  changes and worker actions.
- Review **Diagnostics** after upgrades and before a scheduled run.
- Delete accounts that leave the desk; do not share the owner account.
- Rotate the Telegram session if a device is lost, and revoke it in Telegram.
- Keep Windows, Chrome and Python patched. Test a fixture build after updating
  Playwright or Chrome, because the print pipeline depends on Chrome's rendering.
- Back up before upgrades and before bulk imports.

### Data deletion

- Withdraw a post to exclude it from future builds without destroying evidence.
- Revoke a rights declaration to stop future processing for a channel.
- To remove a channel entirely, back up first, then delete its rows and media
  through a controlled maintenance procedure; do not hand-delete SQLite rows while
  the server is running.

## 11. Performance and capacity

Baseline measurements (fixture composer, no model, Chrome rendering):

| Workload | Wall time | Output |
| --- | --- | --- |
| sample (26 messages) | ~6 s | 15 articles, 5 pages |
| 30 posts (fixture composer) | 5.95 s | 17 articles, 8 pages |
| 70 posts (fixture composer) | 7.97 s | 32 articles, 11 pages |
| 150 posts (fixture composer) | 10.53 s | 49 articles, 15 pages |
| 30 posts (real Ollama, `qwen2.5:3b-instruct`) | 57–62 s | 17 articles, 8 pages, peak VRAM 2213 MiB |

Peak process-group working set for the 150-post build was about 276 MB, with
about 0.68 GB system RAM delta including Chrome. The idle web server used about
62 MB and no measurable CPU over 10 seconds. GPU was unused.

Practical guidance:

- Keep the web server and worker on the same machine.
- Do not run a model and a large layout build simultaneously on 16 GB.
- Re-render only when needed; a re-render creates a new version and new files.
- Monitor `data/logs` and the **Jobs** page for duration growth.
- If a 150-post build approaches the cutoff-to-release gap, move the cutoff
  earlier or reduce the window. Publish on time or warn; never backdate.

## 12. Known administrative limits

- Live Telegram login and retrieval are implemented but unverified: they need the
  owner's API values, an interactive login and a rights declaration.
- The real-model proof covers 30 posts only. 70- and 150-post real-model runs,
  quality comparisons and sustained GPU thermals were not measured.
- The real-model audit held 14 of 17 articles for review in that proof because
  the model added contextual proper nouns. Review capacity is a real operational
  constraint, not a defect to bypass.
- FFmpeg is absent; OpenCV handles video frames.
- Public hosting and QR landing pages are not configured.
- The SQLite + local-folder design targets one workstation, not concurrent
  multi-user scale.
