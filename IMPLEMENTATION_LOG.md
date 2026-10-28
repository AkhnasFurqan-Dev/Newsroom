# Implementation Log

Chronological record of the Channel Press build. Commands are Windows PowerShell
run from `E:\newsroom`; Python is always `.\.venv\Scripts\python.exe`.

Status: **fixture-to-final-PDF route verified, and verified again with a real
local model.** Live Telegram remains unverified because the owner's API
credentials and an interactive login are required; it is recorded as a limit,
not claimed as a success.

## M15 — Explicit job controls and live worker heartbeat

- Separated job actions in the job page: **Retry failed job**, **Continue from
  checkpoint**, **Stop safely**, **Restart from beginning**, and confirmed
  **Cancel job**.
- Added cooperative `stop_requested` handling and recovery to `paused`, while
  preserving saved checkpoints for Continue.
- Added a separate worker heartbeat thread using its own database session. Long
  layout/model operations now refresh the heartbeat and emit a console detail
  approximately every 15 seconds, preventing false stale-job reports.
- Worker launcher keeps stdout attached and captures stderr under
  `data/logs/worker-err-*.log`.
- **Verification:** Ruff passed; scheduling/web/concurrency tests passed after
  fixing launcher stderr capture.

## M16 — Real TPN layout and provider enforcement

- Confirmed Job #5 is the real non-demo **The Pakistan News** channel:
  1,587 gathered source posts and 24 edition stories.
- Fixed the root layout hang: layout planning was attempting lazy Telegram
  media downloads. Missing media caused Telethon reconnects and could block
  layout indefinitely. Layout now uses already-downloaded media only; missing
  media leaves the real article text renderable without network I/O.
- Disabled silent fixture fallback for non-demo channels. Real-channel Ollama
  failures now surface as job errors; the fixture provider remains available
  only for demo/test flows.
- Added layout sub-stage progress, browser launch/page errors, diagnostic
  screenshot capture, browser timeout configuration, and explicit PDF/e-paper
  progress.
- **Real validation:** Job #5 rebuilt with the configured Ollama model
  `qwen2.5:3b-instruct`; all 24 article audits report `provider=ollama`.
  Layout pagination completed in approximately 5 seconds; the final v2 output
  contains 7 tabloid pages, a 1,331,881-byte PDF, and a local e-paper bundle.
  The edition is correctly left in `awaiting_review`.
- **Verification:** full pytest suite passed (171 tests); Ruff passed. Final
  real output:
  `output/the-pakistan-news/2026-10-23-manual-004-v2/`.

## Environment at build time

| Item | Value |
| --- | --- |
| OS | Windows 11 10.0.26200 |
| CPU / RAM | 12th-gen Intel i7 / 16 GB |
| GPU | NVIDIA RTX 3060 Laptop (6144 MiB VRAM), used only by local model drafting |
| Python | 3.12.4 in `.venv` |
| Browser | Google Chrome via Playwright `channel="chrome"` |
| Database | SQLite WAL, Alembic revision `b7c1f2d4e901` |
| Model | Ollama 0.40.9 at `E:\ollama`, `qwen2.5:3b-instruct` (Q4_K_M) |
| Video | FFmpeg absent; OpenCV 4.10 fallback used |

## M00 — Repository and contract

- Read `START_HERE.md`, `BUILD_SPECIFICATION.md`, `ACCEPTANCE_TESTS.md`,
  `AGENTS.md`.
- Created package layout, `pyproject.toml` with pinned dependencies,
  `.gitignore`, `.env.example`, fixture directory and scripts.
- Initialised Git. `data/`, `output/`, `.venv`, session files, PDFs and generated
  media excluded.
- **Verification:** `pip install -e ".[test,video]"`; `python -c "import
  channel_press"`.
- **Commit:** `chore: bootstrap repository, dependencies and fixtures`

## M01 — Schema, auth, roles

- 44-table SQLAlchemy model and one Alembic migration (`54aaea637b58`).
- Argon2id hashing, server-side sessions, CSRF tokens, login throttle, audit log.
- Roles `viewer < operator < editor < owner`; channel-level permissions,
  ownership records and last-owner protection.
- **Verification:** `cli init-db`; `alembic current`; auth/role tests.
- **Commit:** `feat(auth): schema, Argon2id sessions, roles and audit`

## M02 — Settings and consent

- `Settings` with env prefix `CHANNEL_PRESS_`, local data/output folders,
  `Asia/Karachi` default, generated `data/secret.key`.
- Rights declarations with grantor, authority basis, evidence reference and
  ingest/AI/publish scopes; revocation blocks future work.
- Branding/profile fields: masthead, city, timezone, accent, paper size, volume,
  section order, map profile, QR policy.
- **Verification:** rights denial tests; revoke flow; secrets absent from rendered
  HTML/manifest.
- **Commit:** `feat(config): consent, branding and local secrets`

## M03 — Source adapters and fixtures

- Owner JSON and Telegram Desktop JSON normalisation; flattened rich text; albums;
  edits/versions; replies; deletion; media copy.
- Exclusion reasons: Telegram forward, promotion-only, empty.
- Deterministic fixtures: 26-message sample plus 30/70/150-post sets, six media
  files, generator script.
- Isolated optional Telethon adapter documented but not exercised.
- Fixture media are procedural plates marked “NOT DOCUMENTARY EVIDENCE”.
- **Verification:** `pytest tests/test_ingestion.py`; sample import 26 messages →
  22 accepted, 4 excluded.
- **Defect fixed:** album dedupe and video-frame source linkage.
- **Commit:** `feat(ingestion): exports, fixtures, normalization and exclusions`

## M04 — Scheduling and jobs

- Deterministic job keys (channel + kind + cutoff), persistent jobs and steps,
  leases, single-worker file lock, watermark catch-up.
- Windows Task Scheduler integration: `install-task.ps1` → `run-worker.ps1` →
  one-shot `worker --once`.
- Scheduled builds stage as `awaiting_review`; auto-release only for clean,
  non-risk editions.
- Republish jobs create a new version.
- **Verification:** `tests/test_scheduling.py`; duplicate-trigger and restart
  tests; manual worker pass.
- **Commit:** `feat(scheduling): idempotent jobs, leases and catch-up`

## M05 — Extraction and story grouping

- Fragment splitting by topic, language detection, translation metadata.
- Duplicate/album/reply clustering, correction reconciliation, claim ledger,
  timeline, external references.
- Prompt-injection sentences detected and neutralised as evidence.
- **Verification:** 26 messages → 15 stories; contradiction and correction tests;
  unrelated subjects split.
- **Commit:** `feat(story): clustering, claims, corrections and chronology`

## M06 — Evidence and editorial drafting

- Versioned prompt templates for 15 tasks; Pydantic draft schema; bounded retries.
- Deterministic fixture composer and optional Ollama adapter with recorded
  fallback reason.
- Independent deterministic evidence audit: named entities, dates, numbers,
  quotes and citations checked against the ledger.
- Style lint for stock phrases and repeated openings.
- **Verification:** fixture build audit coverage 19/19 supported; unsupported
  sentence fails the gate; injection test inert.
- **Commit:** `feat(ai): versioned prompts, drafting and independent audit`

## M07 — Media desk

- Asset cache, ranking, reversible derivatives, video keyframes via OpenCV,
  collages with separate captions and credits.
- Source message, checksum and credit retained for every selected asset.
- **Verification:** media tests; video frame is non-black and timestamped;
  collage captions separable.
- **Commit:** `feat(media): ranking, derivatives, video frames and credits`

## M08 — Graphics and cartography

- Sourced charts from the claim ledger, chronological timelines, locator maps from
  bundled Natural Earth 1:50m countries and 1:10m disputed areas.
- Neutral, Pakistan and Egypt conventions with visible attribution/disclosure.
- Unsupported location falls back to a sourced text box, never invented geometry.
- Timeline capped with an explicit “first N of M” note; full chronology remains in
  the source record.
- **Verification:** map rendering tests; chart unit/scale validation; timeline
  ordering test.
- **Commit:** `feat(graphics): sourced charts, timelines and disclosed maps`

## M09 — Print engine

- Tabloid 11 × 17 in, Paged.js 0.4.3 vendored inline, Chrome print rendering.
- Distinct front page, 3–4 column interiors, continuation labels and folios.
- Automated geometry QA: exact page size, text presence, embedded fonts, blank
  pages, element overflow, minimum font size.
- **Verification:** PDF is exactly 792 × 1224 pt; all five pages inspected as
  rendered images; front page filled with digest.
- **Defects fixed:** overflow page at 150 posts; duplicate genre kickers
  (“ANALYSIS · ANALYSIS”); front footer pinned to page bottom.
- **Commit:** `feat(layout): tabloid composition, PDF render and geometry QA`

## M10 — Edition planner

- Story selection and ranking, section pagination, chunking, copyfit, continuation
  page references validated after Paged.js.
- Front briefs, digest teasers, source records for short sections, source appendix
  and QR policy.
- Light closing material only when a documented true source exists.
- **Verification:** variable page counts 5/8/11/15 for sample/30/70/150; no blank
  pages; appendix truthful.
- **Commit:** `feat(planner): edition plan, continuation and source appendix`

## M11 — Dashboard and page studio

- FastAPI app with 56+ routes: setup/login, dashboard, source desk, rights, import,
  stories, articles, editing, flags, build, editions, page studio, schedules,
  jobs, branding, users, audit, diagnostics.
- Server-side role checks and CSRF on every mutation.
- Detached worker launch from the web build; job polling.
- **Verification:** `tests/test_web.py`; manual smoke of all primary pages.
- **Commit:** `feat(web): newsroom dashboard, review and page studio`

## M12 — Publication and export

- Atomic staging → final folder; manifest with page geometry, hashes, sources and
  article map; checksums; edition assets; immutable versions.
- Offline e-paper bundle: page JPEGs, text edition, source appendix, relative
  links, `Print PDF`.
- **Verification:** sample edition 5 pages, manifest/checksums verified; e-paper
  opens from disk and through the server.
- **Defects fixed:** preview paths pointed at staging; `/epaper` relative links
  resolved one level too high; `/sample_issue.pdf` link returned 404; manifest said
  “draft” after release; cited URL retained sentence punctuation.
- **Commit:** `feat(publishing): atomic release, manifest and e-paper bundle`

## M13 — End-to-end validation and tuning

- `tests/test_edition_e2e.py`: sample, 30/70/150 capacity and rights-denial.
- Every PDF page rendered and inspected for blank/overflow geometry.
- Measured capacity and idle resources.
- **Results:**

  | Run | Stories/articles | Pages | Wall time |
  | --- | --- | --- | --- |
  | sample (26) | 15 | 5 | 6.2–6.5 s |
  | 30 | 17 | 8 | 5.95 s |
  | 70 | 32 | 11 | 7.97 s |
  | 150 | 49 | 15 | 10.53 s |

  Idle server: ~62 MB working set, 0 CPU seconds over 10 s, 13 threads.
  150-post build: ~276 MB peak process-group working set, ~5.2 s Python CPU,
  ~0.68 GB system RAM delta including Chrome, GPU unused.
- **Verification:** `pytest -q` → **59 passed**.
- **Lint:** `ruff check channel_press tests` → all checks passed.
- **Commit:** `test(e2e): PDF geometry, capacity and rights gates`

## M14 — Packaging and documentation

- `install.ps1`, `start.ps1`, `run-worker.ps1`, `install-task.ps1`,
  `uninstall-task.ps1`.
- `README.md`, `USER_MANUAL.md`, `ADMIN_GUIDE.md`, `TROUBLESHOOTING.md`, this log,
  `docs/QA_REPORT.md`, decision records and cartography notes.
- Real screenshots captured from the running app and the generated edition.
- One-command demo: `demo --posts sample --reset`.
- **Verification:** documented commands re-run; full suite re-run after final
  fixes; demo rebuilt clean with zero QA warnings.
- **Commit:** `docs: readme, manuals, QA report and decision records`

## Git history

The application was built in one continuous session, so the repository history
groups finished subsystems rather than replaying every intermediate save. Each
milestone has a commit:

| Milestone | Commit | Subject |
| --- | --- | --- |
| M00 | `905d61a` | chore: bootstrap repository, dependencies and build contract |
| M01 | `4dc581d` | feat(auth): schema, Argon2id sessions, roles and audit |
| M02 | `39e678d` | feat(config): consent, branding and local secrets |
| M03 | `fd6bd06` | feat(ingestion): exports, fixtures, normalization and exclusions |
| M04 | `c43280d` | feat(scheduling): idempotent jobs, leases and catch-up |
| M05 | `6ebfd96` | feat(story): clustering, claims, corrections and chronology |
| M06 | `2cf1518` | feat(ai): versioned prompts, drafting and independent audit |
| M07 | `29fd752` | feat(media): ranking, derivatives, video frames and credits |
| M08 | `8fa714d` | feat(graphics): sourced charts, timelines and disclosed maps |
| M09 | `96212df` | feat(layout): tabloid composition, PDF render and geometry QA |
| M10 | `36282e8` | feat(planner): edition plan, continuation and source appendix |
| M11 | `2436fca` | feat(web): newsroom dashboard, review and page studio |
| M12 | `7c8ce25` | feat(publishing): atomic release, manifest and e-paper bundle |
| M13 | `6efc139` | test(e2e): PDF geometry, capacity and rights gates |
| M14 | `2798c10` | docs: readme, manuals, QA evidence and decision records |

## Final demo artifact

```text
output\the-channel-dispatch\2026-10-21-manual-001-v1\
  sample_issue.pdf      1,236,129 bytes, 5 pages, 792 × 1224 pt
  print.html            728 KB
  edition.html          728 KB
  pages\page-01..05.png full-page proofs
  epaper\index.html     offline viewer
  epaper\text.html      text edition and appendix
  manifest.json         status published, zero warnings
  checksums.json        all four hashes verified
```

Front page: lead “Four soldiers wounded in Chaman border bombing as clearance
operation continues”, six front briefs and six digest teasers. Interior: regional
and defence with source records, conflict with a source record, analysis/opinion/
briefs, and a sourced appendix. Every page is 11 × 17 in, text-searchable and free
of blank or overflow pages.

## Open risks and limits

1. **Live Telegram** — no credentials or content rights; Telethon path untested.
2. **Ollama** — not installed; real local-model latency and quality unknown.
3. **Public hosting/QR** — no verified public destination; appendix mode is
   intentional and truthful.
4. **Single-workstation scale** — SQLite and a file-locked worker target one desk.
5. **Chrome dependency** — a Chrome update can alter pagination; the slow suite
   catches geometry regressions.
6. **Fixture content** — synthetic; not news and not for publication.

## Next step

For a live deployment: record the owner's rights declaration, configure authorized
Telegram credentials, run one supervised import and build, compare its claims and
page proofs against the fixture baseline, then enable the schedule. Do not enable
auto-release before that supervised run.

## Post-M14 live completion (L00–L09)

Owner instruction: `POST_M14_LIVE_COMPLETION.md`. Phase evidence lives in
`docs/POST_M14_CHECKLIST.md`; live-only status is in
`docs/POST_M14_READINESS_REPORT.md`.

### L00 — Baseline audit

- Re-read the contract and re-ran the suite before changing anything: 59 passed.
- Recorded the real environment: GPU is **6144 MiB**, not the "8 GB" written
  earlier in this log. Ollama, Telethon and FFmpeg were absent at baseline.
- **Commit:** included in the L08 docs commit.

### L01 — Secure configuration and preflight

- `channel_press/config.py` gained Telegram, Ollama, collection and worker
  settings, all read from `CHANNEL_PRESS_*` environment variables.
- `.env` holds empty placeholders only and stays untracked; `LIVE_ENV_PLACEHOLDERS.env.template`
  documents every key without values.
- `channel_press/preflight.py` reports readiness with redaction: it never prints
  an API hash, phone number, session path or model prompt.
- **Verification:** `pytest tests/test_preflight.py`; `cli diagnostics`.
- **Commit:** `4cab3f6` — `feat(config): secure live settings and redacted preflight`

### L02 — Telegram user session, channel resolution

- `channel_press/telegram/transport.py` owns the Telethon client on a dedicated
  gateway thread; `service.py` implements the login state machine, channel
  resolution, pagination, FloodWait retry and media download.
- The session file defaults to `data/telegram.session`, is ignored by Git and is
  never logged.
- Web routes and `telegram.html.j2` expose connect / code / password / resolve
  steps without echoing secrets.
- **Verification:** `pytest tests/test_telegram_live.py` (marked `live`, skipped
  without credentials). **Live login is not verified** — it needs the owner.
- **Commit:** `29dc7a4` — `feat(telegram): local user session and channel resolution`

### L03 — Exact cutoff-to-cutoff collection

- Window semantics are exactly `[previous_committed_cutoff_utc, current_cutoff_utc)`.
- New tables `edition_windows` and `retrieval_checkpoints` (migration
  `b7c1f2d4e901`). The watermark advances only on a committed/approved release or
  a verified zero-content window.
- Collection stages per-page JSONL under `data/window-staging`, resumes from the
  checkpoint cursor, merges by message id, and deletes staging only on success.
- Telegram-native forwards and link-only promotions are excluded; posts are
  stored as untrusted data.
- **Verification:** `pytest tests/test_collection.py tests/test_cutoff_windows.py`.
- **Commit:** `4691145` — `feat(collection): exact cutoff windows, checkpoints and watermark`

### L04 — Real local AI

- Ollama installed at `E:\ollama` (0.40.9); models under `data/ollama-models`;
  `qwen2.5:3b-instruct` pulled. Server bound to `127.0.0.1:11434`.
- `OllamaDraftProvider` hardened: one global gate (no concurrent model loads),
  fenced-JSON extraction, input truncation, exact model-name match, `unload()`
  after drafting, `num_ctx` 4096 / `num_predict` 1800.
- Prompts versioned; `news_article` is now `news-article-v3` and forbids adding a
  country, province or region the source does not name.
- **Verification:** `pytest tests/test_ollama_live.py -m live`. A 30-post edition
  produced 17 stories / 17 articles / 8 pages in **57 s**, provider `ollama`, zero
  fixture fallbacks, peak VRAM **2213 MiB**.
- **Commit:** `d02167c` — `feat(ai): hardened Ollama drafting for real local models`

### L05 — Editorial desk and publication fidelity

- Lazy live-media download: imports store metadata only; bytes are fetched when a
  story actually reaches layout.
- Draft cleanup drops model-echoed `message N … timestamp` plumbing, `[S#]/[C#]`
  markers, bare ISO dates, duplicate lead/standfirst/deck, repeated sentences and
  truncated tails.
- Layout rebuilt for density: two reports per row in multi-story sections, wide
  2.6:1 locator bands with attribution moved to the figure caption, and
  single-report sections set as feature pages. The same 17-story edition fell from
  15 half-empty pages to **8 full pages**.
- All 8 pages were rendered to PNG and inspected: no clipping, overlap, blank
  pages or text below 7 pt; map captions no longer sit on coastlines.
- **Verification:** `pytest -q` (125 passed), `ruff check` clean.
- **Commit:** `49991fe` — `fix(layout): two-up rows, lazy media and caption-safe maps`

### L06 — Windows scheduling and recovery

- `scripts/install-task.ps1` registers a per-user Task Scheduler job that calls
  `scripts/run-worker.ps1` every 15 minutes; the worker is short-lived and
  file-locked, so no process or model stays resident.
- **Verification:** registered `ChannelPressWorkerTest`, started it, observed
  `LastTaskResult 0` and a clean `data/logs/worker-YYYYMMDD.log`
  (`enqueued=0 processed=0 … exit 0`), then unregistered the test task. The run
  also applied migration `b7c1f2d4e901` to the real `data/channel_press.db`
  without touching existing rows (26 posts, 1 edition, 1 user, 15 articles).
- **Commit:** `14b2e48` — `chore(scheduling): readable worker logs and verified task run`

### L07 — Security

- Argon2id, CSRF, session expiry, login throttle, audit log and role checks were
  re-verified; preflight and diagnostics redact secrets; `.env`, sessions, model
  weights, PDFs and generated media stay out of Git.
- **Verification:** `pytest tests/test_security_time.py tests/test_preflight.py`.

### L08 — Documentation

- README, USER_MANUAL, ADMIN_GUIDE and TROUBLESHOOTING updated for live setup,
  collection windows, review flags, local models and scheduling.
- Added `docs/POST_M14_READINESS_REPORT.md` with separate `fixture`, `local AI`,
  `Telegram live`, `supervised end-to-end` and `automatic scheduled publication`
  statuses.
- **Commit:** `docs: post-M14 live setup, collection and readiness evidence`

### L09 — Supervised live acceptance

- **Blocked on the owner:** `CHANNEL_PRESS_TELEGRAM_API_ID`/`_API_HASH`, an
  interactive user login, the source-rights declaration, and approval to publish.
- Everything independent of those secrets is built and tested; see
  `docs/POST_M14_READINESS_REPORT.md` for the exact remaining steps.

### Post-M14 verification snapshot (2026-10-23)

- `ruff check channel_press tests` → `All checks passed!` (exit 0).
- `pytest --collect-only -q` → **125 tests collected**.
- `pytest -q -p no:warnings` → exit 0.
- Real Ollama 30-post proof → 17 stories, 17 articles, 8 pages, 57–62 s, peak
  VRAM 2213 MiB, `quality.warnings == []`; all 8 page proofs inspected.
- Migration `b7c1f2d4e901` applied to the real database without data loss.
- Windows task test run → `LastTaskResult 0`, clean worker log.
- 14 of 17 real-model articles were held as `needs_review` for model-added
  contextual proper nouns. The proof edition therefore stayed `awaiting_review`
  and was not published; an editor must clear the flags first.
- Live Telegram, a supervised live edition and automatic scheduled publication
  remain unverified. Auto-release is off.

### Queue Build 500 and layout QA (2026-10-23)

**Root cause.** `POST /build` read the cutoff clock from
`profile.cutoff_local`, but `PublicationProfile` has no such column. After
`enqueue_manual_job` had flushed a job row, the attribute access raised
`AttributeError: 'PublicationProfile' object has no attribute 'cutoff_local'` in
`channel_press/web/app.py` (`build_submit`, line 1513 before the fix). The request
rolled back and returned a bare HTTP 500: no job, no edition, no audit row and no
traceback on disk. The cutoff belongs to the channel's daily
`EditionSchedule.cutoff_time` (fallback `CHANNEL_PRESS_CUTOFF_LOCAL_TIME`).

**Changes**

- Build route and page: cutoff comes from the channel schedule; the default channel
  is the first non-demo channel; a live channel with a schedule previews
  `[previous_cutoff, current_cutoff)`, a live channel without a schedule uses the
  last complete cutoff window, and the fixture channel keeps its data-derived
  window.
- Manual jobs use a deterministic key
  (`manual:<channel>:<kind>:<start>:<end>`): runnable/completed/awaiting-review
  jobs are reused, failed/cancelled jobs are re-armed in place, and a window that
  already has an edition raises `DuplicateWindowError` shown as a form message, not
  a 500. Manual jobs are review-only (`requires_review=True`, no auto-publish),
  `max_attempts=2`, create an `EditionWindowRecord`, pass the optional title and
  reject windows longer than 31 days.
- Logging: new `channel_press/logging_setup.py` writes JSON lines to
  `data/logs/web-YYYYMMDD.log` / `worker-YYYYMMDD.log`; a global 500 page shows a
  safe reference id and the log path; the worker records `job_failed` with a full
  traceback and `[at file.py:N in func]`. Alembic's `fileConfig` was disabling the
  already-created `channel_press.*` loggers
  (`disable_existing_loggers=True`), so worker failures never reached disk; fixed
  with `disable_existing_loggers=False`, a namespace re-enable in
  `configure_logging`, and configuring logging after `upgrade_database()` in
  `serve`/`worker`. Deterministic `PipelineError`/`PublicationError` failures are
  terminal; only unexpected exceptions retry.
- Layout: removed the conservative section-height page packing that produced
  near-empty pages; non-standalone sections flow and Paged.js packs the actual
  rows, while standalone `analysis`/`opinion`/`briefs` still force a page. The
  section record is unbreakable (`break-inside: avoid`). Layout QA skips Paged.js
  anonymous split wrappers (false 3016 px overflow) and reports tag, class, text
  excerpt and `+Npx` for real overflow.
- Templates: the build page explains the window source and what a duplicate
  re-submit does; the job page shows the worker log path and a `"job_id"` hint.

**Verification**

- `ruff check channel_press tests` → `All checks passed!` (exit 0).
- `pytest -p no:warnings` → **141 passed** in ~3.5 min (16 new tests in
  `tests/test_build_queue.py`, including duplicate-window protection).
- Supervised HTTP run against an exact copy of the live database (`uvicorn`
  127.0.0.1:8799, real Ollama, stored posts, media downloads off, throwaway
  session path): login succeeded; `GET /build` selected channel 2
  (`@ThePakistanNews`) with `2026-10-22T05:00 → 2026-10-23T05:00`;
  `POST /build` returned `303 /jobs/4`. The detached worker completed: job 4
  `awaiting_review`, edition 2 `awaiting_review`, `published_at: None`; a 7-page
  792 × 1224 pt PDF, manifest `demo_edition: false`, `quality.warnings == []`, and
  4610–9186 characters on every page (no blank page). Output folder, e-paper
  bundle, manifest and checksums exist. No real Telegram session was used, the
  real database was not written, and nothing was published.
- Real database untouched: revision `b7c1f2d4e901`; 26 posts on channel 1 and
  1587 on channel 2; 15 articles; 1 edition; 1 user; 2 rights grants; 828
  metadata-only media rows.

**Known configuration issue.** The live publication profile still carries the
seeded demo identity (`channel_handle='@fixture_newsroom'`, footer "... Demonstration
edition produced from synthetic fixtures."). The edition is correctly
`demo_edition: false` and built from the live channel, but the masthead/footer text
is stale. Update **Branding** (publication name, handle, footer) and re-render
before release; the draft is not published. Documented in `TROUBLESHOOTING.md`.

**Files:** `channel_press/web/app.py`, `channel_press/scheduling/service.py`,
`channel_press/scheduling/__init__.py`, `channel_press/logging_setup.py`,
`channel_press/cli.py`, `alembic/env.py`, `channel_press/layout/engine.py`,
`channel_press/layout/templates/postprocess.js`,
`channel_press/web/static/css/print.css`,
`channel_press/web/templates/build.html.j2`,
`channel_press/web/templates/job.html.j2`, `tests/test_build_queue.py`,
`tests/test_scheduling.py`, `TROUBLESHOOTING.md`, `IMPLEMENTATION_LOG.md`.

**Commits:** `2d00f22` (build, scheduling, logging and tests), `b5d44a5`
(layout flow and QA noise), plus the documentation commit that contains this
entry.

---

## Post-M14 corrective milestone — live concurrency recovery (2026-10-24)

**Trigger.** After the Queue Build repair, live use still produced
`sqlite3.OperationalError: database is locked` on schedule/branding/cancel
requests, Telethon session SQLite locks, a 45-second `/sources/{id}` render, and
"Another worker is already collecting this window". The focused contract and
evidence are `CONCURRENCY_RECOVERY_CONTRACT.md` and
`CONCURRENCY_FAILURE_EVIDENCE.md`; the owner-facing report is
`docs/CONCURRENCY_RECOVERY_REPORT.md`.

**Root causes fixed**

1. Application writes were held across retrieval, model, media and PDF work.
   Transactions now commit before external I/O; page checkpoints use short fresh
   sessions with a bounded busy retry; an exhausted retry is a friendly `503`.
2. Web and worker each opened the same Telethon SQLite session. A serialised
   gateway plus a cross-process `<session>.owner.lock` now guarantees one owner,
   closes the client after every bounded call, and releases on timeout/exception.
3. `status(probe=False)` connected to Telegram. It is now memory-only; only the
   explicit Test/Preflight action connects, within `telegram_preflight_timeout_seconds`.
4. `sync_window` jobs had random keys and an edition treated a window claim by
   another worker as a hard failure. One job per window, compare-and-set claims,
   and a `waiting_for_collector` deferral replace both behaviours.
5. `release_expired_locks` referenced an unimported symbol (would raise `NameError`
   on any stale running job). Imported and covered by tests.
6. `restart-services.ps1` counted the web app as a lingering worker and started it
   in a way that inherited the caller's stdout, hanging anyone capturing output.
   It now counts worker owners only and starts the web through
   `operations start-web` (`DETACHED_PROCESS`, file stdio).

**Verification**

- `ruff check channel_press tests` → `All checks passed!`
- `pytest` → **168 passed, 2 skipped** in ~96 s. The skips are the two
  `tests/test_ollama_live.py` cases because Ollama is not running here.
- 29 new regression tests in `tests/test_concurrency_recovery.py`: cached status,
  cross-process session ownership, timeout release, preflight failure paths,
  dirty-writer collection, busy retry, responsive pages, crashed-lease recovery,
  live-owner protection, cancel zombie, exclusive window claim, job re-arm,
  duplicate clicks, checkpoint resume, cancel states, launcher stderr/exit codes.
- Multi-process proof (`tests/concurrency_proof/driver.py`, disposable DB):
  128 dashboard requests during a real worker run, worst 16 ms, no 5xx; job
  `scheduled → running → awaiting_review`; 7-page 792 × 1224 pt PDF; a second
  worker exited 2; session lease blocked a second owner then passed after release;
  graceful stop left zero owners; controlled restart exited 0 in 14.6 s;
  `PRAGMA integrity_check` = `ok`, 26 posts before and after, no duplicate editions.
- Live Telegram: **not verified**. This checkout has API credentials but no
  `data\telegram.session` and no configured source handle, so no live message was
  requested and no live job was run. C17/C18 remain owner-supervised steps.

**Files:** `channel_press/db.py`, `channel_press/locking.py` (new),
`channel_press/operations.py` (new), `channel_press/collection/service.py`,
`channel_press/scheduling/service.py`, `channel_press/pipeline.py`,
`channel_press/editorial/drafting.py`, `channel_press/layout/engine.py`,
`channel_press/media/service.py`, `channel_press/telegram/service.py`,
`channel_press/telegram/transport.py`, `channel_press/web/app.py`,
`channel_press/web/templates/{dashboard,job}.html.j2`,
`channel_press/web/static/js/health.js`, `channel_press/cli.py`,
`channel_press/config.py`, `scripts/{run-worker,stop-services,restart-services,service-health}.ps1`,
`tests/test_concurrency_recovery.py`, `tests/concurrency_proof/`,
`docs/CONCURRENCY_RECOVERY_REPORT.md`, manuals.

**Commits:** `aded3e4` (DB writes, leases, window ownership),
`bbb5d4c` (Telegram session ownership, cached status, preflight, health UI),
`ddd0544` (service control scripts), plus the test/documentation commit.

**Next step:** owner completes the local Telegram login on the machine holding the
session, records rights, and runs one small supervised cutoff edition with
auto-release off; then mark C17/C18.

## Post-M14 startup and layout recovery fix (2026-10-24)

- `scripts/start.ps1` now runs the controlled restart, waits for the managed web
  service, starts one background worker pass for queued/stale jobs, and opens the
  local URL in Google Chrome (falling back to the default browser only when
  Chrome is not installed).
- The print renderer now selects installed Chrome, installed Edge, or bundled
  Playwright Chromium in that order. If no browser can launch, it reports the
  exact installation remedy instead of failing with an unexplained browser
  channel error.
- Updated `README.md` and `TROUBLESHOOTING.md` with the complete start/stop
  workflow, stale layout-job recovery, and worker log locations.
- **Verification:** `pytest tests\test_layout.py tests\test_edition_e2e.py
  tests\test_concurrency_recovery.py` — 39 passed; `ruff check channel_press
  tests` — passed. The real startup script reached
  `http://127.0.0.1:8765/` and launched the recovery worker. The existing live
  24-story job exceeded the interactive command timeout while rendering; the
  generated staged PDF was 5 pages at 792 × 1224 pt, but it was intentionally
  not promoted because the worker process was interrupted.

## Post-M14 job progress visibility (2026-10-24)

- Jobs now persist stage, percentage, detail, update time, elapsed seconds and
  estimated remaining seconds in the existing result JSON. Drafting reports
  story `N/M` progress instead of appearing idle during the model phase.
- The job detail page polls this record and displays a progress bar, live stage,
  detail, elapsed time and ETA. The jobs table also shows the latest percentage.
- Worker CLI output prints stage updates live. `scripts/start.ps1` opens the
  worker console visibly, while the durable JSON log remains under `data/logs`.
- **Verification:** focused web/scheduling/queue tests — 28 passed; `ruff check
  channel_press tests` — passed.

## Post-M14 moderation, language, attribution and media lifecycle (2026-10-25)

**Trigger.** A review of the partially implemented intake prompt found that basic
classification existed, but meaningless-post reasons, Urdu/Roman Urdu handling,
institutional attribution, bounded media preparation, cache clearing and
post-publication cleanup were incomplete, inconsistent or untested.

**Implemented**

1. **Classification and moderation.** `channel_press/editorial/moderation.py` is a
   deterministic, versioned classifier. It distinguishes event reports from bare
   numbers, greetings, prayers, slogans, emoji, promotions, link-only posts and
   unsupported media, and persists a specific reason. Short casualty, correction
   and emergency alerts remain eligible in English, Urdu script, Roman Urdu and
   mixed Urdu/English. Unrecognised scripts are retained as
   `language_needs_review`, never silently dropped.
2. **Profanity and abuse.** Plain and obfuscated forms are detected, including
   leetspeak, spaced letters, repeated characters and intra-word `@`. A report that
   quotes abuse is retained and flagged; abuse with no report is excluded. Threats,
   targeted abuse and hate/dehumanising language are routed to review.
   Sexual-violence reporting terms are treated as sensitive editorial material,
   not automatic profanity. The word lists are self-authored and versioned.
3. **Persistence and editor control.** Posts store classification, reason,
   classifier version, language, moderation status and classification time.
   Repeated copies get `repeated_copy`. The source page shows the result and offers
   audited editor decisions (include, exclude, context only, promotional,
   profanity/abuse); these survive re-imports and rebuilds. Before clustering,
   `run_pipeline` reclassifies any stored rows still at `legacy`/an older
   classifier version, creates missing fragments for newly includable posts and
   preserves editor overrides. `channel-press classify-posts --channel <slug>`
   exposes the same backfill for older databases, with optional
   `--window-start`/`--window-end` and `--force`.
4. **Attribution.** Byline and source disclosure are configurable. The publication
   is an institutional source, never an individual reporter or eyewitness. The
   evidence audit and prompts reject invented staff, scene reporting and unnamed
   “our correspondent” claims. Print and e-paper render the byline and roundup
   disclosure.
5. **Media preparation.** Live media is downloaded only with an active rights
   grant, credentials, a resolved handle, documentary/relevant metadata, size
   limits, a per-story asset cap and a retry budget. Downloads are relevance-ranked
   and bounded before layout. Layout and PDF rendering accept local files only and
   never call Telegram. Provenance records filename, bytes, checksum, attempts,
   download time, rights grant and errors.
6. **Cache and cleanup.** The media cache has dry-run, unused, derivative,
   edition-working and all-deletable modes. File deletion is confined to
   `data/media`; protected media includes draft/review/failed/running editions,
   editor locks, active jobs, the retention window and incomplete published
   bundles. Provenance is retained after file deletion. Publication cleanup runs
   only after a verified release, is idempotent, and records failures as `pending`
   without reversing publication. `finalize_media_cleanup` commits the release
   before deleting any file, because file deletion cannot be rolled back.
7. **Controls and visibility.** `/media-cache` is editor-only, CSRF-protected and
   audited. `media-cache` supports `--dry-run`, explicit modes, `--confirm` and
   `--finalize-edition`. Jobs report `classification`, `media_prepare`,
   `media_download`, `media_process` and `clean_media` in the browser and worker
   CLI.
8. **Migrations.** Added `c1d4e6f8a901` (moderation/media lifecycle),
   `d2e5f7a8b902` (attribution identity) and `e3f6a9c0d013` (classification time
   and media provenance).

**Verification**

- `.\.venv\Scripts\python.exe -m pytest tests/test_moderation_media_lifecycle.py -q -p no:warnings`
  → **32 passed**. Covers exclusions, short alerts, four language families,
  obfuscated profanity, threats, sexual-violence reporting, repeated copies,
  overrides, legacy-stored-post backfill before clustering, media-only posts not
  mistaken for copies, institutional attribution, rights-gated downloads, bounds,
  retries, local-only rendering, cache protection, path traversal,
  post-publication cleanup, idempotency and the protected web/CLI controls.
- `.\.venv\Scripts\python.exe -m pytest -p no:warnings` → **202 passed, 2 skipped
  in 104.92 s**. The skips are the two Ollama live tests because no model server
  was running.
- `.\.venv\Scripts\python.exe -m ruff check .` → **All checks passed!**
- Fresh-database migration check:
  `.\.venv\Scripts\python.exe -m alembic upgrade head` applied all five revisions
  and reported `e3f6a9c0d013 (head)`.
- The existing fixture-to-PDF e2e remains green and asserts the printed byline,
  source disclosure, `media_cleanup` manifest entry and e-paper text byline.

**Commit:** `85246dd` — `feat(moderation): classify stored posts and manage media
lifecycle`

**Fixture vs real channel**

The results above are fixture and owner-import evidence. **The Pakistan News was
not collected or published in this pass.** Live validation still requires the
owner's Telegram API values, completed local login, an active rights grant and one
supervised window/build. No fixture result is presented as live Telegram proof,
and no live attribution, profanity or media-cleanup claim is made.

The stored archive was checked separately. Before this change, 1,597 of the
channel's 1,661 stored posts still had `classification_version = "legacy"`, a blank
reason and no `classified_at`; story building only read `inclusion_status`, so those
rows bypassed the new rules. A transactional dry run of the new backfill produced
1,213 relevant, 307 duplicate, 115 low-information, 13 promotion, 7 profanity-only,
3 context-only, 2 unsupported-media and 1 empty post. That matched the earlier
independent read-only pass exactly once a defect was fixed: media-only posts with no
text must not share the hash of an empty string and be mistaken for repeated
copies. The owner database was backed up, then the CLI persisted the upgrade:

```text
classify-posts --channel regional-desk
1661 stored, 1661 reclassified, 1213 included, 448 excluded,
0 editor overrides, 0 repeated copies, 0 fragments created
```

This is stored-data validation, not live Telegram collection. Existing text,
message ids, source links, rights records, media metadata, story/article snapshots
and editor overrides were preserved; the pass is recorded as `source.reclassify`
in the audit log. While preparing the backup, a Windows defect was also fixed:
`backup` left a SQLite snapshot handle open, so `ZipFile` could not read it. The
connection now closes explicitly, and `tests/test_cli_backup.py` checks that the
archive is written and readable.

**Known limits**

- The classifier is rule-based and deliberately conservative; an unfamiliar slang
  or script variant may need an editor override. That decision is recorded.
- Live media can be skipped by policy or size. The text article still renders; the
  build does not fail solely because a download failed.
- Post-publication cleanup removes downloaded source media. The published PDF and
  e-paper bundle are the durable record; archived raw media must be backed up
  before release.
- Two skipped Ollama tests still require a local model run to mark live-model
  coverage complete.

**Files:** `channel_press/editorial/moderation.py` (new),
`channel_press/media/service.py`, `channel_press/publishing/service.py`,
`channel_press/ingestion/service.py`, `channel_press/ingestion/normalize.py`,
`channel_press/layout/planner.py`, `channel_press/layout/engine.py`,
`channel_press/layout/templates/print.html.j2`,
`channel_press/publishing/templates/epaper_text.html.j2`,
`channel_press/scheduling/service.py`, `channel_press/pipeline.py`,
`channel_press/config.py`, `channel_press/constants.py`, `channel_press/models.py`,
`channel_press/cli.py`, `channel_press/web/app.py`,
`channel_press/web/templates/{source,media_cache,base,branding}.html.j2`,
`channel_press/ai/prompts/*` and `VERSIONS.yaml`, migrations
`c1d4e6f8a901`/`d2e5f7a8b902`/`e3f6a9c0d013`,
`tests/test_moderation_media_lifecycle.py` (new), `tests/test_edition_e2e.py`,
`tests/test_ingestion.py`, `tests/test_live_media.py`, `.env.example`, README,
manuals and this log.

**Next step:** owner completes the local Telegram login, records rights for
`The Pakistan News`, runs one supervised cutoff with auto-release off, then
inspects the generated PDF/e-paper for language, attribution and media behavior
before enabling the schedule.

---

## Phase A — editorial integrity: revisions, previews and immutable versions

**Goal:** an editor's saved change must appear in the next rendered preview and
release, while an already published version stays byte-for-byte unchanged.

**What changed**

- `channel_press/editorial/revisions.py` (new) owns the content lifecycle:
  article snapshots and SHA-256 fingerprints, optimistic-concurrency saves
  (`expected_revision`), field-level revision diffs, editorial status changes,
  article ordering, frozen `EditionArticle` snapshots and working-version
  creation (`working_revision`).
- `EditionArticle.article_revision` and `snapshot_json` freeze the exact article
  content used by each published version. Later edits cannot alter it.
- `Edition.preview_*` tracks the private working preview: state
  (`missing`/`current`/`stale`/`failed`), content fingerprint, render timestamp
  and folder. Preview folders are `.preview-<edition_id>` and are never
  publication pointers.
- A published edition is never mutated into a new version. Saving, reordering or
  changing status opens `V(n+1)` as a draft via `working_revision`; release stays
  an explicit editor action.
- Release always re-renders from the latest saved revisions and then freezes
  snapshots, manifest article revisions, content hashes, PDF checksum, page set
  and timestamp. The preview folder is discarded after a successful release.
- The scheduler's `republish_edition` job now renders a working preview only and
  finishes as `awaiting_review`; it no longer auto-publishes.
- The edition page is a three-pane workspace: article list with revision/status
  indicators, the full article editor (headline, deck, standfirst, body, byline,
  section, genre, lead media and captions, status, order) and the rendered page
  preview with evidence, claims, external context, revision history, published
  snapshot hash and version family.
- Every article in an edition is editable, not only review-flagged articles.
- `workspace.js` adds dirty-form detection, an undo-unsaved button, a save-state
  badge and a `beforeunload` warning. Server-side concurrency still wins: a stale
  form is rejected and the saved revision is preserved.
- Excluded articles are omitted from composition; a held or “needs research”
  article blocks release until the editor resolves it. Rendering an edition
  whose every article is withheld fails with a clear message instead of emitting
  a blank paper.
- Media alternatives are ranked with eligibility flags. A non-documentary or
  promotional asset is refused for a real channel; the explicitly labelled demo
  channel may use its synthetic plates. Editors can replace the lead asset, edit
  caption/credit or remove it.

**Schema and migration**

`alembic/versions/f4b8c2d6e123_editorial_workspace.py` adds
`articles.byline`, five `editions.preview_*` columns and
`edition_articles.article_revision` / `snapshot_json`.
Before touching the owner database, a backup was written:
`data/backups/channel-press-20261025-111225.zip` (13.2 MB). The migration was
first applied to a copy of the real database, then to
`data/channel_press.db`; the copy retained 7 editions, 81 articles and 1,687
stored posts. Current revision: `f4b8c2d6e123`.

**Verification**

- `tests/test_editorial_workspace.py` (new, 8 tests) covers:
  edit → preview → release V2 → edit → V3; V1/V2 byte immutability and frozen
  snapshots; stale-form rejection; every-article editability; excluded composition
  and held release blocking; revision diffs; eligible media replacement; the
  Alembic column set; and the served workspace assets.
- `.\.venv\Scripts\python.exe -m pytest -q -p no:warnings` → **208 passed, 2
  skipped**. The skips remain the two live-Ollama tests because no model server is
  running.
- `.\.venv\Scripts\python.exe -m ruff check channel_press tests alembic` →
  **All checks passed!**
- Migration on a copy of the owner database upgraded
  `e3f6a9c0d013 → f4b8c2d6e123` and exposed the new columns without changing row
  counts.

**Fixture vs real channel**

All workspace tests use repository fixtures and the demo channel. They prove the
software path, not live Telegram access or publication. No live channel was
edited or released in this phase.

**Known limits at this checkpoint**

- Media alternatives are rendered from the local cache; downloads remain a
  separate, rights-gated preparation step.
- The workspace is server-rendered and intentionally does not autosave. An editor
  who ignores the unsaved-changes warning still loses unsubmitted text (the
  server never receives it).
- A quality-warning preview is allowed so editors can inspect a broken page;
  release remains strict.

---

## Phase B — media control and printed QR citations

**Goal:** stop printing long URL lists, give editors real control over the
pictures the paper prints, and never expose a private asset as a public link.

**Citations**

- `channel_press/publishing/qr.py` adds `citation_code()`, a self-contained
  0.85in QR image emitted as a PNG data URI. It accepts only a syntactically
  public HTTP(S) destination: loopback, `file:`, RFC1918 and placeholder
  addresses are rejected before encoding.
- The printed appendix now prints a compact QR plus a short scan label instead
  of a full URL. Where no public direct source post exists (fixture messages,
  unverified external pages) it prints an explicit "No public link" /
  "External link not printed" label rather than an invented or local address.
- Long inline source links were removed from the section records; those now say
  "public post (scan appendix)".
- `manifest.json` keeps the full digital provenance (`url`, `qr_target`,
  `qr_printed`, `qr_reason`, `message_id`, timestamps) while stripping the QR
  image bytes so JSON stays readable.

**Media manager**

- The edition workspace shows the current lead image, a captioned/credited
  editor override, a remove control, and ranked alternatives with preview
  thumbnails, dimensions, sensitivity and eligibility state.
- A supporting-media slot was added: explicit editor choice only, never
  auto-selected. Briefs and narrow rows never take a second picture.
- `apply_media_choice(role="supporting")` stores a separate derivative and
  caption/credit set; the renderer prints it as a smaller second figure.
- `GET /media/{id}/original` serves the exact stored source file as a private
  attachment to editors who can access the channel and, for a real channel,
  hold a current rights grant. Files outside the managed media cache are
  refused even if a database row points there.
- Non-documentary/promotional assets remain blocked for real channels. The
  synthetic demo channel may select its labelled fixture plates.

**Verification**

- `tests/test_citations.py` (5 tests): rejects loopback/private/file targets,
  round-trips a public Telegram URL through QR encode/decode, never invents a
  target for fixture or unverified entries, decodes the printed appendix QR
  from a 200dpi render and measures it in the 0.6–1.1in band, and proves a
  fixture edition prints no fake code.
- `tests/test_editorial_workspace.py` gained supporting-media add/remove and
  original-download tests, including refusal of a path outside the cache.
- `.\\.venv\\Scripts\\python.exe -m pytest -q -p no:warnings` → green
  (all tests; 2 live-Ollama skips).
- `.\\.venv\\Scripts\\python.exe -m ruff check channel_press tests alembic` →
  **All checks passed!**

**Fixture vs real channel**

QR tests use a synthetic `https://t.me/example_channel/101` target attached to
a fixture post and a demo edition. No real Telegram post was downloaded,
published or linked in this phase.

---

## Phase C — multiple newspapers and multiple authorized sources (2026-10-25)

**Goal:** let one installation run more than one newspaper, let each paper draw
on several authorized Telegram sources over one shared cutoff window, keep every
paper's data server-side isolated, and never lose a source silently.

**Publication identity**

- `PublicationProfile` gained `slug` and `is_active`; a real
  `publication_sources` table links authorized channels with `priority`,
  `weight` and `is_enabled`; `publication_editors` grants a newspaper role per
  user. Editions, schedules and jobs now record `publication_id`.
- `channel_press/publications.py` owns resolution and access:
  `resolve_publication` (query parameter, then `cp_publication` cookie, then the
  first paper the user may open), `can_access_publication`, source membership,
  unassigned-channel detection and per-edition source lookup.
- Every scoped page re-checks the paper on the server: dashboard counts,
  editions, stories, articles, schedules and jobs. Opening an edition from a
  paper the user cannot access returns 403. A user who could already open one of
  a paper's channels keeps access after an in-place upgrade, so an existing
  single-paper installation does not lock out its editors.
- The header selector and **Branding → Newspapers on this computer** create,
  switch and (owner only) deactivate papers and manage source links and editor
  membership. New sources are auto-linked to the active paper; channels with no
  link are treated as unassigned and remain buildable for compatibility.

**Multi-source pipeline**

- `PipelineRequest.channels` is authoritative when set; otherwise the legacy
  single-channel path is untouched.
- Each source is reclassified, clustered and cleared independently before the
  weighted cross-source merge. `merge_cross_source_stories` folds a duplicate
  report into the higher `importance × source weight` story, moves fragments,
  claims, external references, timeline events, relationships and review flags,
  and keeps the absorbed row as a `merged_into_id` audit pointer.
- `record_article_sources` stores which channels and how many distinct posts each
  article actually draws on. The article page shows that attribution; the printed
  appendix and manifest record the source channel per entry.
- Edition issue numbers count per publication and volume comes from the paper's
  profile. Source ids and `source_status` (`complete` / `partial`) are stored on
  the edition and shown in the archive and edition header.

**Scheduling and failure isolation**

- Manual builds can include every enabled source (`all_sources=1`) or one
  channel. Manual job keys and durable window keys are publication-scoped, so two
  papers sharing a primary channel cannot collide.
- A due schedule expands that schedule's newspaper enabled sources at enqueue
  time and records the list on the job. The worker collects each source with its
  own rights check, window record and failure boundary. A failed or empty source
  is reported per source and excluded from the pipeline; the resulting edition is
  marked `partial` instead of pretending every source was used. If every source
  fails, the job fails rather than publishing an empty edition.

**Migration and live data**

- `alembic/versions/b5d7e1a9c234_multi_publication.py` backfills the existing
  single-paper installation: all editions, schedules and jobs are linked to the
  default publication, existing channels become enabled sources, and new columns
  default without rewriting row counts.
- Backup before migration:
  `data/backups/channel-press-20261025-122846.zip` (13.2 MB).
- Live `data/channel_press.db` upgraded `f4b8c2d6e123 → b5d7e1a9c234` with
  counts preserved: 1 user, 2 channels, 1,687 posts, 81 stories, 81 articles,
  7 editions, 1 schedule, 8 jobs, 1 publication profile; 7 editions, 1 schedule
  and 8 jobs linked, 2 source links created.

**Verification**

- `tests/test_publications.py` (9 tests) covers publication creation/switching,
  non-member story/edition isolation, weighted cross-source merge and
  attribution, publication-scoped and legacy manual jobs, due-schedule source
  expansion, migration downgrade/upgrade backfill, publication resolution, and a
  full two-source fixture build. That last test imports the same synthetic wire
  report into two demo channels, runs the real pipeline, and asserts: one folded
  story, both channels on `ArticleSource`, both channel ids in the manifest,
  `source_status=complete`, an 8-page tabloid PDF with no blank page, both
  channel labels in the appendix, and no localhost/file URL in print.
- `.\\.venv\\Scripts\\python.exe -m pytest -q -p no:warnings` → **229 tests,
  0 failures, 0 errors, 2 skipped** (the two live-Ollama tests; no model server
  is running).
- `.\\.venv\\Scripts\\python.exe -m ruff check channel_press tests alembic` →
  **All checks passed!**
- `.\\.venv\\Scripts\\python.exe -m channel_press.cli init-db` → revision
  `b5d7e1a9c234`.

**Visual inspection finding (deferred to Phase E)**

The two-source edition produced a real 8-page PDF. Page 1 and the section
openings are composed and readable, and the appendix carries both channel
labels. Inspection also found three real layout defects that Phase E must fix
before the paper can be called print-ready:

1. body text is clipped mid-sentence at the bottom of columns on the Conflict &
   War page when the chronology block follows the story;
2. chronology rows repeat the same message twice (attributed and reported copy of
   one event);
3. Analysis and Opinion section openings leave more than 80% of the page blank
   because every section forces a new page.

These are recorded as defects, not accepted behaviour. Phase C's data and
attribution contract is working; Phase E owns page composition.

**Fixture vs real channel**

All Phase C tests use synthetic demo channels and fixture imports. The migration
was applied to the live local database, but no live Telegram account was used, no
rights grant was verified, and no real newspaper was published in this phase.

**Known limits at this checkpoint**

- The optional local model and live Telegram collection still require owner
  credentials, a completed login and a running Ollama server.
- A source that fails during a scheduled run produces a partial edition; it is
  visible but it is not automatically retried as a standalone source job.
- Per-source weight changes apply to the next build; an already published edition
  keeps its source record and manifest.

## Phase D — independent quality pass, classification signals and bounded research (2026-10-27)

Commit `d917642` (`feat(editorial): independent quality pass, classification signals and bounded research`).

- Post typing now records whether an included post is a report, correction,
  casualty/emergency alert, commentary or other signal, and the story/edition
  planner can use it without re-parsing free text. Editorial quality is checked
  by a separate deterministic pass instead of asking one model to approve its own
  article.
- Research is bounded and licence-aware. A candidate external reference is kept
  separate from accepted evidence; only an editor-accepted, reachable and
  validated URL can enter the source appendix. URL validation rejects literal
  loopback/private/link-local targets and redirects to them, and robots/legal
  notes are shown to the editor.
- The editorial workspace exposes candidate versus accepted evidence and the
  quality flags with provenance. Opinion/analysis copy is labelled as such in
  print and e-paper.
- Focused verification: `tests/test_editorial_quality.py` and
  `tests/test_research.py` (17 tests), `tests/test_editorial.py`,
  `tests/test_moderation_media_lifecycle.py`, `tests/test_publications.py`,
  `tests/test_layout.py` and `tests/test_editorial_workspace.py` all passed.
- Not verified in this phase: live external research against a real publisher,
  live Telegram collection and a real Ollama run. Fixture results are not claimed
  as live-network evidence.

## Print appearance and Phase E page composition (2026-10-27)

Commits `12d9544` (`feat(theme): per-newspaper print identity with immutable
revisions`) and `2393025` (`feat(layout): measured page fill, tighter packing and
theme-aware rendering`).

**Theme and fonts**

- Added `fonts.py` (installed-family discovery with a path/mtime cache, validated
  `.ttf/.otf/.ttc` import up to 12 MB, content-addressed storage under
  `data/fonts`, face resolution without synthetic bold, glyph coverage) and
  `theming.py` (palette, seven font roles, bounded metrics, presets, contrast and
  ink warnings, compiled CSS, immutable revisions, edition pinning).
- Added `FontAsset` and `ThemeRevision` plus `edition.theme_revision_id`,
  `theme_snapshot_json` and `preview_theme_hash`. Migration
  `d8a4b1c2e345_print_themes.py` is additive and was validated against a fresh
  temporary database and a copy of the live database before being applied.
- `print.css`, charts, maps, captions and QR labels take the compiled theme.
  QR modules stay black on white. The e-paper gets `assets/theme.css`, bundled
  font files and the same palette/typography; its manifest and the edition
  manifest record the theme hash, revision and fonts.
- Added the **Appearance** workspace: presets, HEX pickers, contrast checks,
  seven font roles, per-section accents, metrics, licence-confirmed uploads,
  import deletion protection and a real specimen proof rendered through Chrome
  from the same `print.css`.
- A released edition pins its theme before publish; a later theme edit never
  rewrites it. Draft/preview staleness compares the current theme hash.
- Current migration state of the live local database: `d8a4b1c2e345 (head)`.
  `diagnostics` after the upgrade still reports 1 user, 2 channels, 1,687 source
  posts, 81 stories, 81 articles, 7 editions, 8 jobs and 0 pending jobs. Backup
  before migration:
  `data/backups/channel-press-before-theme-20261027-190438.db`.

**Page composition**

- The planner only pairs two reports into a half-page row when the estimated
  load fits (`ROW_WORD_BUDGET=980`, `ROW_SINGLE_BUDGET=620`) and the section
  family is `national`, `regional`, `international` or `defence`.
- Thin sections flow after the previous section instead of forcing an almost
  empty page; a section gets a fresh page only at `SECTION_BREAK_MIN_LOAD=760`.
  Editorial/opinion copy opens its own page so a short piece is not split after
  one line at the foot of a section page.
- Browser post-processing measures fill from real leaf text/figure/table
  bottoms. `inspect_pdf` reports `quality.layout` with per-page fill, underfull
  pages, average/lowest fill, trailing whitespace and whether the measurement is
  real. Underfull pages are diagnostics; blank pages, overflow and orphaned
  section headings remain proof failures.
- Before/after on the synthetic fixture edition: 7 pages with four underfull
  pages (57%, 20%, 21%, 38%) and an orphan-head warning became 5 pages with
  average fill 0.791, no underfull page reported and no proof warning. Page 3
  (Conflict & War + Analysis) and page 4 (Opinion + Briefs + evidence) were
  inspected as full-page PNGs; the previous one-line opinion split is gone.
- Added backup coverage for imported fonts so a restore cannot orphan a theme
  revision. `cli backup` now includes `fonts/` and the manifest records
  `included_fonts`.

**Focused verification during this phase**

- `python -m pytest tests/test_theming.py tests/test_layout.py tests/test_web.py`
  → 29 passed.
- `python -m pytest tests/test_appearance_web.py` → 9 passed, including a real
  Playwright specimen proof (1056×1632 PNG) with the named palette swatch strip.
- `python -m pytest tests/test_cli_backup.py` → 2 passed (font archive test).
- `python -m pytest tests/test_edition_e2e.py::test_fixture_to_printable_edition_and_epaper`
  → passed with the new theme and page-fill assertions.

**Not claimed**

- No live Telegram or Ollama run in this phase; no real-network research.
- The specimen proof and PDF use the installed browser, but the proof is a
  labelled specimen, not a news article.
- Font embedding licensing remains the owner's responsibility; the app records
  the acknowledgement but cannot verify the licence.

## Phase F — end-to-end verification and defect repair (2026-10-28)

**Scope.** Verify the complete fixture-to-PDF/e-paper route after the retrospective
theme and page-fill work, repair what verification exposed, re-measure capacity and
refresh the user-facing documentation. No production table, migration, Telegram
session or archived edition was reset or rewritten.

**Defects found during verification and fixed**

1. **Same-channel stories were folded by the cross-source dedupe.** The
   `merge_cross_source_stories` matcher added for multi-source papers compared
   section and title tokens but not the channel. On the 150-post fixture set it
   reduced 28 clustered stories to 8 printed reports, merging unrelated local
   reports such as "Editorial: Local services in Sialkot…" with the Gilgit
   editorial. It now only considers candidates from another channel, leaving
   within-channel deduplication to `build_stories`. Regression test:
   `tests/test_publications.py::test_cross_source_merge_never_folds_two_reports_from_the_same_channel`.
   Recorded in `docs/decisions/0010-cross-source-dedupe-scope.md`. The 30-, 70-
   and 150-post sets now print 17, 27 and 28 stories instead of 17, 8 and 8.
   Earlier capacity rows in this log were recorded while this bug was active.
2. **Three-column body inside a half-width Conflict & War row.** The
   `.template-conflict_visual .story__body { columns: 3 }` rule overrode the row
   rule, so each 4.9 in story was set in three ~1.5 in columns with two or three
   words a line and stretched justification. The rule is now scoped to full-width
   conflict reports. A Playwright test loads the real `print.css` and asserts
   computed `column-count` of 2 for row members and 3 for a full-width report.
   Page 3 was re-rendered and inspected at full size.
3. **`cli demo` never marked setup complete.** The documented five-minute demo
   created the owner, publication profile and edition, but its dashboard kept
   showing "Setup is not complete". The command now records the setup flag, and
   `tests/test_cli_demo.py` asserts both the flag and the absence of the banner.
4. **fontTools was missing from the dependencies.** The Appearance screen
   therefore reported Georgia, Segoe UI and every other installed family as
   "not installed", and every upload was rejected. `fonttools==4.55.3` is now a
   core dependency; discovery on this machine returns 377 faces across 190
   families. The on-disk discovery cache is keyed by the parser version so a
   cache written without fontTools cannot survive installing it
   (`tests/test_theming.py::test_installed_font_cache_is_keyed_by_the_font_parser`).
   `diagnostics` and preflight now report the fontTools version. Appearance was
   rechecked in the browser: all seven roles resolve to installed faces, and the
   remaining notes are truthful Urdu-coverage notes, not missing-font warnings.

**Verified artifact**

- PDF: `output/the-pakistan-news/2026-10-21-manual-011-v1/sample_issue.pdf`
  (edition 12, issue 11, version 1, published 2026-10-28 09:40 UTC).
- E-paper: `output/the-pakistan-news/2026-10-21-manual-011-v1/epaper/index.html`
  with `assets/theme.css`, five page JPEGs, text edition and source appendix.
- Manifest: 5 pages, every page 792 × 1224 pt, 1,142,089 bytes, theme revision 1
  pinned, average measured fill 0.783, `underfull_pages: []`,
  `warnings: []`, `office_print_proof: true`.
- Pages 3 (Conflict & War + Analysis), 4 (Opinion & Editorial, Briefs, evidence)
  and 5 (Sources & provenance) were rendered to PNG and inspected at full size.
  The previous three-column garbling on page 3 is gone. Page 4 ends at fill 0.626
  because the fixture copy runs out; that is reported, not padded with invented
  filler.

**Capacity re-measured (cold CLI runs, 2026-10-28)**

| Posts | Stories/articles | Pages | Wall | Peak process group (with Chrome) |
| --- | --- | --- | --- | --- |
| 30 | 17 | 7 | 9.57 s | 869 MB |
| 70 | 27 | 8 | 10.81 s | 874 MB |
| 150 | 28 | 9 | 12.11 s | 861 MB |

Each run used a fresh data directory, so the wall time includes database creation,
fixture import, drafting, Chrome rendering and publication; the 150-post run spent
9.7 s inside the pipeline. Average fill was 0.659, 0.781 and 0.739 with underfull
pages reported as diagnostics (4–6, 3 and 7, 3/7/9) and no proof warnings. The
capacity fixtures repeat a small number of topics, so their pages genuinely end
shorter than the sample edition's.

**Full test suite**

- Command: `.\.venv\Scripts\python.exe -m pytest -o addopts="" -q`
- Result: **277 passed, 2 skipped, 0 failed** in 345 s, exit 0 (2026-10-28).
- The two skips are the live Ollama tests (`tests/test_ollama_live.py`); no model
  server was running. The run includes the slow sample/30/70/150 PDF builds, the
  Playwright specimen proof, the Playwright column test and the new CLI/font
  regressions.

**Documentation**

- `README.md`: theme and page-fill capabilities, corrected capacity table,
  current test count, demo paths.
- `ADMIN_GUIDE.md`: re-measured capacity, fontTools requirement and diagnostics.
- `USER_MANUAL.md`: Appearance screenshot and current procedures.
- `TROUBLESHOOTING.md`: font parser missing, stale discovery cache, upload and
  theme-cache recoveries.
- `docs/decisions/0008`, `0009`, `0010`; screenshots regenerated from the current
  build (dashboard, editions archive, edition detail, e-paper viewer, front page)
  and a new Appearance capture.

**Not claimed**

- No live Telegram collection and no real Ollama drafting ran in this phase; the
  two Ollama tests were skipped and no real-network research was performed.
- The specimen proof is a labelled specimen page, not a news article.
- Capacity times are measurements on this laptop, not throughput promises; peak
  memory includes every Chrome process started for the build.
- Font embedding licensing remains the owner's decision; the app records the
  acknowledgement but cannot verify the licence.
