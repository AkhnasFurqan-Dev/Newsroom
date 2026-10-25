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
