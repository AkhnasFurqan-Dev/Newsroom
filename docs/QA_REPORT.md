# QA Report — Channel Press

Date: 2026-10-23
Build: `1.0.0`, Alembic `54aaea637b58`
Machine: Windows 11, 12th-gen i7, 16 GB RAM, RTX 3060 Laptop, Chrome installed,
Ollama not installed, FFmpeg absent.

Status labels:

- **Pass (fixture)** — verified with synthetic fixtures and automated tests.
- **Pass (manual)** — visually or interactively inspected on this build.
- **Not tested** — no credentials/hardware/configuration; not claimed.
- **Limit** — intentional boundary documented in the manuals.

## Test runs

```text
.\.venv\Scripts\python.exe -m pytest -m "not slow" -q   -> 54 passed
.\.venv\Scripts\python.exe -m pytest -q                 -> 59 passed
```

The five slow tests render real PDFs through Chrome:

1. fixture-to-printable edition and e-paper;
2. 30-post capacity;
3. 70-post capacity;
4. 150-post capacity;
5. rights-denied live channel cannot build.

Warnings are dependency deprecations from FastAPI/Starlette and SWIG; no test
failure. Full output saved in `docs/test-run-full.txt`.

## A. Installation, resources and basic app

| Gate | Status | Evidence |
| --- | --- | --- |
| Windows setup installs without cloud accounts | Pass (manual) | `scripts/install.ps1` created `.venv`, installed pins, found Chrome, ran `init-db` |
| First-run admin, sign in/out, password change, unauthorized rejection | Pass (fixture) | `tests/test_web.py`, auth tests; manual sign-in |
| Sample issue from fixtures with no Telegram credentials | Pass (manual) | `demo --posts sample --reset` produced 5-page edition |
| Browser can close while detached worker finishes; resume after restart | Pass (fixture) | persistent jobs, lease tests, worker pass tests; UI queues detached worker |
| Idle has no model/video decode; measured idle usage | Pass (manual) | ~62 MB working set, 0 CPU over 10 s, 13 threads; no Ollama process |
| Required docs exist and describe tested commands | Pass (manual) | `README.md`, `USER_MANUAL.md`, `ADMIN_GUIDE.md`, `TROUBLESHOOTING.md` |

## B. Authorization, ownership and data control

| Gate | Status | Evidence |
| --- | --- | --- |
| AI use blocked without rights/consent; revocation visible and blocking | Pass (fixture) | rights declaration tests; `test_live_channel_without_rights_cannot_build`; revoke flow |
| Public read access does not grant republication | Pass (manual) | no rights → import/build disabled; declaration required |
| Owner/editor distinct; viewer cannot publish or inspect secrets | Pass (fixture) | role-rank tests and route guards in `test_web.py` |
| Second user cannot claim an existing channel | Pass (fixture) | ownership uniqueness and permission checks |
| Secrets absent from Git, logs, manifests and pages | Pass (manual) | `.gitignore`; secrets not in manifest; diagnostics shows status only |
| Telegram/API errors do not leak credentials; SSRF blocked | Pass (fixture) | URL validation rejects loopback/private targets; error messages sanitised |
| Manual rights-cleared import works without Telegram API | Pass (manual) | JSON import, 26 messages, 22 accepted, 4 excluded |

## C. Telegram data and story logic

| Gate | Status | Evidence |
| --- | --- | --- |
| Original posts included; Telegram forwards excluded with reason | Pass (fixture) | `test_ingestion.py`; exclusion reason displayed |
| Promotion-only excluded; commentary with external link included | Pass (fixture) | fixture cases and tests |
| Album is one editorial unit with all message IDs/media | Pass (fixture) | album grouping test; media counts retained |
| Edited post uses latest pre-cutoff version; correction leaves audit trail | Pass (fixture) | version history and correction tests |
| Deletion/rights withdrawal re-evaluates and blocks reuse | Pass (fixture) | withdraw action and exclusion tests |
| Reply chains and same-event posts link when evidence supports | Pass (fixture) | clustering tests |
| Unrelated subjects split; similar incidents do not merge | Pass (fixture) | fragment split and cluster tests |
| 30-day context does not republish old news as fresh | Pass (fixture) | window selection and context tests |
| Historical lookback concise and dated | Pass (fixture) | timeline/context tests |
| 30/70/150 message sets run without uncontrolled overflow | Pass (manual + slow tests) | 8/11/15 pages respectively; timings below |
| Views/reactions do not affect ranking | Pass (fixture) | ranking ignores engagement fields; swap test |

## D. Editorial factual and linguistic quality

| Gate | Status | Evidence |
| --- | --- | --- |
| Six-update incident → coherent story with chronology, correction, sources | Pass (manual) | Chaman story: 7 timeline events, correction labelled, source records |
| Unrelated policy and security developments become separate articles | Pass (manual) | 15 articles across national, defence, regional, international, analysis |
| Every hard claim points to evidence; fabrications flagged | Pass (fixture) | audit 19/19 supported; unsupported sentence blocks release |
| Unverified claims attributed; conflicting sources remain attributed | Pass (manual) | needs-review flags for casualty and anonymous claims |
| Genres distinguished with labels/voice | Pass (manual) | news, analysis, opinion/editorial and brief kickers |
| Translation preserves time/names/link/quote attribution | Pass (fixture) | translation metadata and tests |
| External material only cited context | Pass (manual) | external references marked “not independently verified” |
| Prompt injection inert | Pass (fixture) | injection detection tests |
| High-risk claims/imagery flagged; unattended publish blocked | Pass (fixture) | high-risk gate; scheduled auto-release refuses risk |
| Style checks reject filler; no fabricated eyewitness quotes | Pass (fixture) | style lint; fixture composer quotes only source claims |
| Locked headlines survive regeneration | Pass (fixture) | lock tests and article revision logic |

## E. Media, evidence and maps

| Gate | Status | Evidence |
| --- | --- | --- |
| Selected media linked to source, checksum, credit | Pass (manual) | article media panel and printed captions |
| Test video yields meaningful non-black frame with timestamp | Pass (fixture) | OpenCV frame extraction test |
| Collage has separable captions/credits | Pass (fixture) | media tests; no single-photo implication |
| No generative alteration of documentary evidence; originals preserved | Pass (manual) | reversible derivatives; fixture plates marked test data |
| Chart values derive from claim ledger; unsupported point fails | Pass (fixture) | chart validation tests |
| Timeline orders sourced events in local/UTC time | Pass (fixture) | timeline ordering test; omitted-count note |
| Locator map renders with source/legend; unsupported location falls back | Pass (manual) | Chaman/Bannu/Swat maps with Natural Earth attribution |
| Pakistan/neutral conventions disclosed; no invented shapes | Pass (manual) | `docs/CARTOGRAPHY.md`; legend states convention |
| QRs truthful: verified public URL, tested text or appendix | Pass (manual) | appendix mode; no QR without verified URL |
| Printed references and appendix resolve; provenance retained | Pass (manual) | page 5 appendix table; e-paper source panel |

## F. Typesetting, printing and e-paper

| Gate | Status | Evidence |
| --- | --- | --- |
| Exact tabloid 11 × 17 in, printer-safe margins | Pass (manual + test) | every page 792 × 1224 pt; manifest geometry |
| Front page differs from interiors but shares brand | Pass (manual) | front lead + briefs + digest vs 3-column section pages |
| `front_major_event` hierarchy legible | Pass (manual) | Chaman lead spans columns without overlap |
| At least three layout families | Pass (manual) | front major event, section/conflict visual, analysis/opinion/briefs |
| Long titles, no images, media-heavy, 150 posts: no clipping/blank/micro type | Pass (manual + slow test) | 150-post 15-page run, zero warnings; overflow detector |
| Stories continue with clear continuation labels | Pass (manual) | continuation labels and validated page refs |
| Page count adapts to volume, no aesthetic minimum | Pass (manual) | 5/8/11/15 pages for 26/30/70/150 posts |
| Closing light only with documented true source, labelled, skippable | Pass (manual) | disabled by default; no invented on-this-day content |
| Searchable/selectable text; fonts embedded | Pass (test) | PDF text extraction; embedded font list in manifest |
| Page thumbnails generated and inspected | Pass (manual) | five page proofs opened; no unexplained errors |
| Offline e-paper opens without network with navigation and appendix | Pass (manual) | opened from disk; viewer, text edition, source panel |

### Page-by-page visual inspection — sample edition

| Page | Content | Result |
| --- | --- | --- |
| 1 | Masthead, synthetic-fixture notice, Chaman lead, six briefs, six “More from this edition” teasers, footer | Filled, readable, no overlap |
| 2 | Regional and Defence source records, stories, maps | Columns balanced, captions and credits present |
| 3 | Conflict & War lead and second story, maps, chronology | No clipping; map legends readable |
| 4 | Analysis, Opinion & Editorial, Briefs | Kickers correct; no duplicate “ANALYSIS · ANALYSIS” |
| 5 | Sources & provenance appendix table | All rows legible; fixture URLs marked “no public URL” |

No blank trailing page. No page below 7 pt. No element outside the page content
box in the automated check.

## G. Jobs, scheduling and publication

| Gate | Status | Evidence |
| --- | --- | --- |
| Consecutive cutoffs capture each message once; boundaries tested | Pass (fixture) | scheduling boundary tests; watermark |
| Special edition overlaps daily without advancing daily watermark | Pass (fixture) | job key includes kind; watermark per kind |
| Weekly/monthly roundups avoid duplicate items | Pass (fixture) | roundup window tests |
| Retry resumes without doubling articles/publications | Pass (fixture) | retry tests; transactional build |
| Failed layout leaves draft, not a published incomplete PDF | Pass (fixture) | `LayoutQualityError` path; status remains draft |
| Duplicate idempotency key cannot double-publish; worker conflict handled | Pass (fixture) | unique job key, lease, file lock tests |
| Missed trigger catches up and reports actual release time | Pass (fixture) | catch-up enqueue and published_at |
| Published issue has immutable version/manifest/hash; revision numbered | Pass (manual) | version 1 manifest; republish creates version 2 |
| Offline export does not promise hosted pages; target validated | Pass (manual) | no upload feature; QR requires verified URL |

## H. Documentation and handoff

| Gate | Status | Evidence |
| --- | --- | --- |
| Fresh-install steps followed literally | Pass (manual) | install and demo commands re-run on this machine |
| User manual covers authorization, import, schedules, review, sensitive approval, edits, print proof, QR, exports, troubleshooting | Pass (manual) | `USER_MANUAL.md` |
| Admin guide covers roles/ownership, recovery, secrets, backup/restore, deletion, performance, model swap, logs, updates | Pass (manual) | `ADMIN_GUIDE.md` |
| Actual generated PDF + static e-paper + manifest exist and match inspection | Pass (manual) | `output\the-channel-dispatch\2026-10-21-manual-001-v1\` |
| All milestones have log entries and commits | Pass | `IMPLEMENTATION_LOG.md`; Git history |
| Final report names test results, performance and untested integrations | Pass | final handoff report |

## Measured performance

| Workload | Stories/articles | Pages | Wall time | Notes |
| --- | --- | --- | --- | --- |
| sample (26 messages) | 15 | 5 | 6.2–6.5 s | fixture composer |
| 30 posts | 17 | 8 | 5.95 s | no warnings |
| 70 posts | 32 | 11 | 7.97 s | no warnings |
| 150 posts | 49 | 15 | 10.53 s | no warnings, no overflow page |

Resource measurements:

- Idle server: ~61.6 MB working set, 0 CPU seconds over 10 s, 13 threads.
- 150-post build: ~10.5–12.5 s wall; Python process-group peak ~275.8 MB
  (~165 MB above baseline); ~5.2 Python CPU seconds; system RAM delta ~0.68 GB
  including Chrome; GPU unused (`--disable-gpu`, no model).
- PDF: 5 pages, 1,236,129 bytes, 792 × 1224 pt each, 7 pt minimum font, embedded
  Georgia/Segoe UI/Arial subsets.

These are single-machine measurements, not service-level guarantees. Live model
drafting will add substantial time and memory.

## Defects found and fixed during QA

1. Staging preview paths persisted into the database → final bundle paths.
2. 150-post run produced an overflow page → front digest/chunk sizing fixed.
3. Front page looked under-filled → six digest teasers added; footer pinned.
4. Duplicate genre kicker on analysis pages → suppressed when genre equals section.
5. E-paper `/epaper` relative links resolved one level too high → 307 redirect to
   `/epaper/`.
6. E-paper “Print PDF” returned 404 → `/editions/{id}/sample_issue.pdf` served.
7. Published edition had a manifest saying “draft” → release fields rewritten with
   checksum update.
8. Cited URL kept sentence-ending period → link extraction strips prose
   punctuation while preserving balanced brackets.
9. `demo --reset` failed on foreign-key order and stale identities → ordered
   cleanup and identity expunge.
10. Published edition incorrectly said “Release held” → “Published with open review
    items”.

## Not tested / limits

- **Live Telegram**: no API ID/hash, session or content rights. Telethon path is
  unexercised.
- **Ollama**: not installed. No real model quality, latency or VRAM measurement.
- **Public hosting/QR**: no public publication origin; QR disabled by policy.
- **FFmpeg**: absent; OpenCV fallback verified on the fixture video.
- **Multi-user concurrency**: single-workstation SQLite design, not load-tested.
- **Printer hardware**: PDF geometry verified; no physical printer calibration was
  performed.
- **Accessibility audit**: semantic HTML and labels are present; no formal WCAG
  audit was run.

## Sign-off

The reproducible fixture-to-final-PDF route, e-paper bundle, manifest/checksums,
documented commands and full test suite work on this machine. Live integrations
listed above are boundaries, not completed features.
