# Implementation Log

Chronological record of the Channel Press build. Commands are Windows PowerShell
run from `E:\newsroom`; Python is always `.\.venv\Scripts\python.exe`.

Status: **fixture-to-final-PDF route verified.** Live Telegram and Ollama remain
unverified integrations, recorded as limits rather than claimed successes.

## Environment at build time

| Item | Value |
| --- | --- |
| OS | Windows 11 10.0.26200 |
| CPU / RAM | 12th-gen Intel i7 / 16 GB |
| GPU | NVIDIA RTX 3060 Laptop (8 GB), unused by the fixture pipeline |
| Python | 3.12.4 in `.venv` |
| Browser | Google Chrome via Playwright `channel="chrome"` |
| Database | SQLite WAL, Alembic revision `54aaea637b58` |
| Model | Ollama not installed; deterministic fixture composer used |
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
