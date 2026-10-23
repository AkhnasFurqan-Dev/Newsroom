# Channel Press — Troubleshooting

Start with **Diagnostics** in the UI or:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli diagnostics
```

It reports the database revision, Chrome, model reachability, FFmpeg/OpenCV,
public URL and counts without exposing secrets. Also check `data\logs\` and the
**Jobs** page for the failing stage.

## The server will not start

**Port already in use**

```text
ERROR: [Errno 10048] error while attempting to bind on address ('127.0.0.1', 8765)
```

Find and stop the old server:

```powershell
Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
  Where-Object { $_.CommandLine -like '*channel_press.cli serve*' } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
```

Or start on another port: `... serve --port 8766`.

**Two servers running** — only one process can own the port. Kill duplicates; do
not run `serve` from both the system Python and `.venv`.

**Database migration failure** — run `init-db` and read the Alembic error. Back up
before restoring an older database. Never delete `channel_press.db-wal` while the
server is running.

## I cannot sign in

- Usernames are case-insensitive; passwords are case-sensitive.
- After 5 failed attempts in 15 minutes, wait for the window to pass.
- An owner can reset a password:

  ```powershell
  .\.venv\Scripts\python.exe -m channel_press.cli set-password --username owner
  ```

- If no owner exists, the app shows **Set up Channel Press**. If the database has
  users but you are locked out, reset with `set-password`; do not delete the
  database.

## The import is rejected or empty

| Message | Meaning | Action |
| --- | --- | --- |
| `A rights declaration is required` | No active grant for a live channel | Owner records the grant on the channel page |
| `Unsupported export format` | JSON is not Telegram Desktop or owner format | Export again from Telegram Desktop, or use the documented owner format |
| `Import exceeds 40 MB` | File too large | Split the export or raise `CHANNEL_PRESS_MAX_IMPORT_BYTES` deliberately |
| Accepted count is lower than raw | Forwards, promotion-only posts and empty messages are excluded by policy | Check the exclusion reason in **Source posts** |
| Media not shown | Files were not selected with the import | Re-import with **Referenced media files**, or copy files into `data\media` with the expected names |

Edited posts keep versions; the latest pre-cutoff version is used. A deleted or
withdrawn post is excluded from new builds.

## Rights or consent denial

The build stops with a rights error when:

- the channel has no active declaration;
- the declaration was revoked;
- the required scope (**Ingest**, **AI processing** or **Publication**) is missing.

Record a new declaration or restore the scope. Revocation is intentional and is
recorded in **Audit**. Do not work around it by renaming the channel or editing the
database.

## Telegram authentication and history

The live flow is implemented but has not been exercised with real credentials in
this build. Login and codes happen on **Sources → Open Telegram connection**, not
in chat or `.env`.

- `Telethon is not installed` — install the optional package in the venv:
  `.\.venv\Scripts\python.exe -m pip install -e ".[telegram]"`, then restart the
  server.
- `API ID/hash missing` — set `CHANNEL_PRESS_TELEGRAM_API_ID` and
  `CHANNEL_PRESS_TELEGRAM_API_HASH` in `.env`. Create them at
  <https://my.telegram.org> with the owner's account, then restart.
- `Login code did not arrive` — confirm the phone number is in international
  format and that the Telegram app is reachable. Use **Cancel login** and start
  again after the expiry shown on the page; do not request codes repeatedly.
- `Two-step password` — the password is accepted only in the local form and is
  not stored. A wrong password does not delete the pending login; retry within the
  login window.
- `Session password needed` / `unauthorized` — the local session expired or was
  revoked. Sign in again on the connection page. Never commit
  `data/telegram.session`.
- `FloodWait` — Telegram has rate-limited the account. Wait the stated period; do
  not retry in a loop. The retrieval checkpoint resumes from the last staged page.
- `Channel private or not accessible` — the account must be a member with the
  rights the owner granted. Public read access is not republication permission.
  Record a rights declaration before retrying.
- `Could not find the input entity` — save the exact `@username` or `t.me` URL on
  the connection page and use **Test source connection** after joining the channel
  with the authorized account.
- **Signed in but Sync window does nothing** — check that a daily schedule exists
  (the window preview comes from it), that the channel is not the fixture desk,
  and that the rights declaration is active.

If live access remains blocked, use the rights-cleared JSON import. It is the
verified path and needs no Telegram credentials.

## Ollama or model problems

**Diagnostics says Ollama unavailable (ConnectTimeout)**

- Ollama is not running or not installed. Start `ollama serve`, or continue with
  the deterministic composer. The fixture demo never needs a model.
- Check `CHANNEL_PRESS_OLLAMA_BASE_URL`; the default is
  `http://127.0.0.1:11434`.

**Model loads but the build falls back**

Read the fallback reason on the article page or in the job result:

- schema validation failure after retries — the model did not return the required
  JSON; try a more instruction-following model or a smaller prompt;
- timeout — reduce context or choose a smaller quantized model;
- out of memory — see the next section.

**Out of VRAM / RAM**

- The build machine's GPU has **6144 MiB** of VRAM. Use a 3–4B Q4 instruct model;
  `qwen2.5:3b-instruct` peaked at 2213 MiB during the verified 30-post run.
- Lower `CHANNEL_PRESS_OLLAMA_NUM_CTX` (default 4096) and
  `CHANNEL_PRESS_OLLAMA_NUM_PREDICT` (default 1800) if the model spills.
- Keep `OLLAMA_KEEP_ALIVE=60s` and `OLLAMA_UNLOAD_AFTER_JOB=true` so the model
  leaves VRAM between scheduled runs.
- Do not run the model and a 150-post layout at the same time on 16 GB.

**Model is installed but the app still falls back**

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli diagnostics
```

Check that the configured name matches `ollama list` exactly and that the
Ollama server is on `127.0.0.1:11434`. A stale `OLLAMA_KEEP_ALIVE` or a model
still loading can time out; the adapter retries and then records the fallback
reason rather than silently claiming AI output.

A model fallback is not a publication failure. The deterministic composer still
passes the independent audit; the edition is labelled with its actual provider.

## A job is skipped, stuck or duplicated

**Skipped cutoff** — open **Schedules** and select **Enqueue missed cutoffs**. The
watermark records the last committed cutoff. Repeated triggers for the same cutoff
are ignored by design. Catch-up is bounded by `max_catchup_windows` (7 by
default); a longer gap is visible in the window history and must be reviewed, not
assumed published.

**Collection failed halfway** — the retrieval checkpoint and staged JSONL under
`data\window-staging` survive a crash. Retry the job; it resumes from the cursor
and merges by `(channel, message id)`, so posts are not duplicated. The watermark
does not advance on a failed collection.

**A window says `no_content`** — this is a verified empty window, not a skip. It
commits so the next cutoff starts in the right place. Check the source channel
before treating it as an error.

**Draft built but the watermark did not move** — expected. The watermark advances
only on a committed release or a verified empty window. Approve and release the
edition, or investigate the review flags. A pending review never discards the next
day's intake.

**Job stuck in a running stage** — check the heartbeat and lease owner on the job
page. A worker that died leaves a stale lease; it is reclaimed after the lease
window. Stop any orphaned worker, then retry. Two workers cannot claim the same
window.

**Two editions for one window** — this should not happen for scheduled jobs
because the job key is channel + kind + cutoff. If it does, record both edition
IDs and inspect **Audit** before deleting anything. A manual build with a different
window or kind is a legitimate separate edition.

**Worker does nothing** — run one pass manually and read the log:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run-worker.ps1
Get-Content .\data\logs\worker-$(Get-Date -Format yyyyMMdd).log -Tail 50
```

**Task Scheduler does not run** — check `Get-ScheduledTaskInfo
-TaskName ChannelPressWorker`, the “Start when available” setting and the working
directory. The task must point at the repository folder with `.venv` present.

## Duplicate or overlapping windows

The build selects posts with `posted_at >= start` and `< end`. The end instant is
excluded: a post at exactly the cutoff belongs to the next normal issue.
Overlapping manual windows can legitimately produce overlapping editions. For
scheduled jobs the watermark prevents this. If a special edition must overlap a
daily edition, keep the daily watermark separate (the job key includes the edition
kind). A manual or special edition does not advance the daily watermark.

When in doubt, compare the **Reporting window** and message IDs in each edition's
source appendix.

## Layout, CSS and PDF problems

**PDF is not 11 × 17 inches** — check the **Quality inspection** panel. Every page
must report 11.0 × 17.0 in (792 × 1224 pt). If not, confirm the publication profile
uses **Tabloid** and that Chrome is installed. The renderer refuses to claim a
successful print proof when page geometry is wrong.

**Text clipped, overlapping or running off the page** — the build should fail with
a layout quality error rather than publish. Inspect:

- the page proof images under `pages\`;
- the job error for the failing page;
- unusually long headlines, unbroken URLs or long proper names in the article.

Fix the copy or the chunking, then **Re-render as version N**. Do not reduce the
font below the configured minimum to force a fit.

**Blank trailing page** — a blank page is a defect. It usually means a forced page
break or an empty section container. Check the edition plan and re-render.

**Overflow warnings block release** — the publisher raises `LayoutQualityError`
when QA warnings exist. Only an owner should override with code-level
`allow_quality_warnings=True`, and the reason must be documented. The normal path
is to fix the layout.

**Fonts missing or substituted** — the pipeline uses installed Windows fonts
(Georgia, Segoe UI, Arial) and embeds them. If a font is missing, install it or
change the print CSS. Verify embedded fonts in the manifest after the change.

**Chrome failed to launch** — install Google Chrome, close a hung Chrome process,
and re-run. Playwright uses `channel="chrome"`, not a downloaded Chromium.

## E-paper and QR problems

**E-paper images broken when opened from disk** — open `epaper\index.html` from
inside the edition folder; the bundle uses relative paths. Do not move
`index.html` away from `pages\` or `sample_issue.pdf`.

**E-paper links broken when served** — use the trailing-slash URL
`/editions/<id>/epaper/`. The non-slash URL redirects there so relative links
resolve. **Print PDF** resolves to `/editions/<id>/pdf`.

**No QR code printed** — this is expected. The default policy prints a truthful
source appendix unless a public URL is configured and verified reachable. Setting a
localhost or placeholder URL will not produce a valid QR.

**QR points to the wrong place** — correct **Contact / public URL** or
`CHANNEL_PRESS_PUBLIC_BASE_URL`, rebuild, and verify the destination from a phone
on a different network before release. A QR that cannot be reached is a defect.

## Missing media

- Check the source post's media count and the **Media** panel on the article.
- Confirm the original file exists under `data\media\`.
- If a file was imported without its binary, re-import it with **Referenced media
  files**.
- A missing image should degrade to a text-only layout, not a broken frame. If the
  PDF shows an empty image box, record it as a layout defect.
- Video frames use OpenCV because FFmpeg is absent. A black frame means the
  timestamp selection failed; inspect the source video and re-render.

## Timezone and date confusion

- The database stores UTC; the UI and schedules use the named timezone
  (`Asia/Karachi` by default).
- A cutoff is an instant. “Daily 05:00” means 05:00 local on the scheduled day,
  converted to UTC for storage. The window is half-open:
  `[previous committed cutoff, current cutoff)`.
- A late worker start does not move the cutoff. If the laptop wakes at 11:00, the
  05:00 window is still the 05:00 window.
- If an edition includes yesterday's news, check the window fields and the
  schedule's timezone. Do not “fix” it by changing the cutoff after the fact; build
  the correct window and keep both records honest.
- DST: the default `Asia/Karachi` has no daylight saving. If you choose a zone
  with DST, verify a transition day in a test or a manual build; an ambiguous
  local clock time is resolved by the tested policy and should be documented
  before enabling scheduled release.

## Backup and restore problems

- **Restore refuses to run** — stop the server and worker, then add `--force`.
- **Archive is not a Channel Press backup** — the zip lacks
  `backup-manifest.json` or `channel_press.db`. Do not extract arbitrary archives
  over `data\`.
- **Restore succeeded but editions are missing** — backups include the database and
  media, not `output\`. Rebuild editions, or restore the output folder separately
  from your own archive.
- **Login fails after restore** — the restored database may contain different
  password hashes. Use the owner account from that backup or reset with
  `set-password`.
- A pre-restore safety backup is written to `data\backups\pre-restore-*.zip`.

## Tests fail

```powershell
.\.venv\Scripts\python.exe -m pytest -m "not slow" -q
.\.venv\Scripts\python.exe -m pytest -q
```

- The slow suite renders real PDFs and needs Chrome. A Chrome update can change
  pagination; inspect the failure before changing expected page counts.
- Tests run against a temporary database created in `tests\conftest.py`; they do
  not touch your newsroom database.
- If `playwright` cannot find Chrome, install Chrome or verify the executable path.
- Live-marked tests skip without credentials or a model:
  `pytest -m live tests\test_telegram_live.py` and
  `pytest -m live tests\test_ollama_live.py`. A skip is not a pass.
- `DeprecationWarning` output from FastAPI/Starlette is noise unless a test fails.

## Still stuck

Collect:

1. the exact command or UI action;
2. the job ID and failing stage;
3. the error text from **Jobs** or `data\logs`;
4. `diagnostics` output;
5. the edition ID and page number, and the page proof image.

Do not send `data\secret.key`, `.env`, `telegram.session` or raw private source
content in a bug report. Reproduce with the synthetic fixtures where possible.
