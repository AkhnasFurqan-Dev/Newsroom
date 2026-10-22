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

Optional local model support is not installed by this script. Install Ollama
separately if required.

### Upgrade

1. Stop the server and any worker task.
2. Back up first: `.\.venv\Scripts\python.exe -m channel_press.cli backup`.
3. Update the source and reinstall: `.\scripts\install.ps1`.
4. Start the server; `init-db` runs Alembic migrations on startup.
5. Run `diagnostics` and inspect the reported revision.

Dependencies are pinned in `pyproject.toml`. Review release notes before changing
pins, and test a fixture build after any upgrade.

## 2. Accounts and roles

The first account is created by **Set up the newsroom** or:

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
| Register channels, manage users, branding | — | — | — | Yes |
| Record or revoke rights declarations | — | — | — | Yes |
| Inspect diagnostics/audit | — | — | — | Yes |

Roles are ranked (`viewer` < `operator` < `editor` < `owner`). Channel-level
permissions can grant a user access to one channel without making them a global
editor. Owners always have access.

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

The repository includes an isolated, optional Telethon adapter for authorized
history access. It requires:

- `CHANNEL_PRESS_TELEGRAM_API_ID` and `CHANNEL_PRESS_TELEGRAM_API_HASH` from the
  owner's Telegram developer account;
- `CHANNEL_PRESS_TELEGRAM_SESSION_PATH` pointing outside Git (default
  `./data/telegram.session`);
- the optional Telethon package, which is not installed by the baseline script;
- a recorded rights declaration before any content is processed.

**This path was not tested in this build because no credentials or content rights
were supplied.** Do not represent it as verified until you have completed an
authorized end-to-end run. The verified intake route is the rights-cleared JSON
export.

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
- `diagnostics` reports presence/status only, never values.
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
```

All app settings use the `CHANNEL_PRESS_` prefix. The adapter:

- sends only the story evidence bundle and the versioned prompt;
- requires a strict schema and retries a limited number of times;
- falls back to the deterministic composer with a recorded reason when the model
  is unavailable or invalid;
- never treats model output as verified. A separate deterministic audit checks
  every fact sentence against the claim ledger.

### Choosing a model for 16 GB / RTX 3060 Laptop

Start with a 3–4B instruct model quantized to Q4. Larger models may fit in 8 GB
VRAM only at low quantization and short context; measure before adopting. Keep
`OLLAMA_KEEP_ALIVE=60s` so the model unloads between scheduled runs.

Measure with:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli demo --posts 30
.\.venv\Scripts\python.exe -m channel_press.cli diagnostics
```

Record wall time, peak RAM/VRAM and whether the fallback reason appears. **Ollama
was not installed on the machine used to build this repository**, so no real
local-model quality or speed result is claimed.

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
- **Watermark catch-up** — missed cutoffs are enqueued from the last processed
  cutoff.
- **Lease and file lock** — only one worker processes jobs; a stale lease is
  reclaimed after the configured window.
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
limit. `run-worker.ps1` keeps a daily log.

## 7. Manual overrides

- **Run the latest cutoff now** queues the current window immediately.
- **Enqueue missed cutoffs** catches up from the watermark.
- **Retry failed job** resumes a failed job transactionally.
- **Re-render as version N** rebuilds an edition from the same article set and
  keeps the previous version.
- `run-now --channel <slug>` does the same from the command line.
- `check-edition --edition-id N` prints status, page count, QA warnings, PDF path
  and any review blockers.

Do not edit `manifest.json`, `checksums.json` or files inside a published folder.
Create a new version instead.

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
PDF/e-paper history matters; they are reproducible from the database and media.

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
| 30 posts | 5.95 s | 17 articles, 8 pages |
| 70 posts | 7.97 s | 32 articles, 11 pages |
| 150 posts | 10.53 s | 49 articles, 15 pages |

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

- Live Telegram is untested without credentials and rights.
- Ollama is uninstalled and untested end-to-end on this machine.
- FFmpeg is absent; OpenCV handles video frames.
- Public hosting and QR landing pages are not configured.
- The SQLite + local-folder design targets one workstation, not concurrent
  multi-user scale.
