# Production refinement report (V2)

**Repository:** `E:\newsroom - Copy`  
**Date:** 30 October 2026  
**Scope:** P01–P14 of the production editorial and publishing refinement brief, applied to the existing Phases A–F code. No phase was rebuilt and no published V1 artifact was rewritten.

## 1. Commits

| Commit | Area | What changed |
| --- | --- | --- |
| `6c0449e` | Print cleanup | Removed the printed appendix, “About this edition”, evidence box, source record, claim counters, raw message IDs, default chronology, footer boilerplate and unverified QR placeholders. Added per-story verified QR resolution, clean headline/deck fields, neutral edition names, conservative chronology and digital `sources/<public_id>/` evidence bundles. |
| `2026c3f` | Editorial filtering and prose | Added the `channel_administration` classification and override, stripped Telegram broadcast furniture at draft and render time, repaired stacked attribution (“reported noted”), suppressed near-duplicate sentences/paragraphs, and added verbatim-copy/repeated-opening quality notes. |
| `17a7c3b` | Discovery, health and edition identity | Added pluggable Google/Brave/GDELT/publisher-RSS discovery with bounded queries, ranking, SSRF-safe fetching and review policy; replaced the raw Check Connections JSON with readable rows; added per-newspaper research/chronology/QR controls and editor-defined edition names. |
| `c87f720` | Layout | Promoted a second and third reported story below the lead on thin days, kept the rail for briefs, changed paired story rows from CSS grid to flex so Chromium cannot leave a headline on one page and its body on the next, and added lower-front page references. |
| `(this report)` | Documentation and final UI cleanup | Removed remaining hero kickers/ledes, updated the archive naming, README/manual/admin/troubleshooting and this report. |

The pre-existing published artifact
`output/the-pakistan-news/2026-10-21-manual-011-v1` was not modified.

## 2. Before and after

### Reader-facing print

**Before (edition 13, Batch 1 baseline render):** 3 pages. The paper carried an
appendix-like provenance trail, the posting-frequency notice had been promoted
into an article, attribution read “according to a recent message from the channel
reported” and “The channel reported noted”, several paragraphs repeated the same
background, and the front page had a large empty lower half.

**After (live re-draft proof):** 2 pages, no appendix/About/source-record/claim
counters/message IDs, no `@ThePakistanNews` or emoji, no `REPORTED` stamps, and no
`reported noted`. The posting notice is classified `channel_administration` and is
excluded before clustering. The front page carries the lead plus two lower-front
reports, five rail/digest items and one compact QR per story. Measured page fill is
**0.777** and **0.840**, with `underfull_pages: []`, no overflow and no orphaned
section head.

Rendered page images from the live proof:

- `C:\Users\Akhnas Furqa\AppData\Local\Temp\opencode\cp-refine-live4-pages\page-01.png`
- `C:\Users\Akhnas Furqa\AppData\Local\Temp\opencode\cp-refine-live4-pages\page-02.png`

PDF: `C:\Users\Akhnas Furqa\AppData\Local\Temp\opencode\cp-refine-live\the-pakistan-news\2026-10-29-manual-013-v1\sample_issue.pdf`

The proof was generated from a **copy** of the real database, so the live database
and published archive were untouched. It is a draft proof, not a released issue.

### Live local-model proof

Ollama (`qwen2.5:3b-instruct`) was started on this machine and a fresh draft was
generated for the 28 October window:

- 6 stories / 6 articles in **29.4 s** wall time.
- 5 articles were held `needs_review`; 8 review flags were recorded.
- All six article bodies avoided the previous stacked-verb and emoji defects.
- Remaining factual weaknesses (for example an invented alliance founding year or
  extra background) were caught by the independent audit as unsupported claims and
  held for human review; they were not silently published.

This is a real local-model run on authorized channel data. It is not evidence that
a particular larger model will never hallucinate, and it is not a Telegram network
test.

### Automatic research

- Provider selection works without credentials: the keyless publisher-RSS adapter
  was selected automatically and its status is shown on the story page.
- A live smoke test used a current Dawn headline and URL. Discovery constructed
  fact-anchored queries, fetched the real article through the existing SSRF,
  robots.txt, redirect and size checks, and stored one candidate with HTTP 200 and
  an extracted article paragraph. The candidate remained `candidate`, not
  established fact.
- Google Programmable Search and Brave adapters parse their documented JSON
  payloads in tests but had no live credentials here.
- GDELT responded **HTTP 429** (“one request every five seconds”) on this network
  during verification. The adapter throttles and is a deliberate provider choice;
  it is not claimed as a successful live search.
- The RSS fallback is a feed reader, not a search engine. For the real channel
  stories in this window it found no closely matching current feed item and
  reported `no_candidates` rather than inventing corroboration.

### System health and edition identity

- `/system/check` now returns readable rows for Application, Database, Telegram
  account, Selected source channel, Local AI, Configured model, Edition worker and
  Source discovery. It shows reason, one recovery link and a live check time.
- Raw JSON/PIDs/paths are no longer shown in the normal browser action. The
  programmatic endpoint remains at `/system/check.json`.
- The build form no longer offers “Manual edition” as a title. It accepts an
  **Edition name** such as “Daily Edition”; the internal kind stays `manual` for
  workflow and watermark rules.
- Draft editions can be renamed in place; published versions keep their released
  name and require a new revision.

## 3. Tests run

Focused batches (during implementation):

- `pytest tests\test_editorial.py tests\test_editorial_quality.py tests\test_moderation_media_lifecycle.py tests\test_collection.py` — classification, attribution repair, duplicate suppression.
- `pytest tests\test_layout.py tests\test_edition_e2e.py tests\test_citations.py tests\test_publications.py` — print/QR/evidence and multi-source boundaries.
- `pytest tests\test_research.py tests\test_research_discovery.py` — bounded fetch, adapters, ranking, acceptance policy.
- `pytest tests\test_refinement_ui.py` — health view, edition naming, branding research fields.
- A combined focused run passed **130 tests**.

Full suite command:

```powershell
.\.venv\Scripts\python.exe -m pytest -o addopts="" -q
```

Result on 30 October 2026: **299 passed, 0 failed** in 280.9 s. This includes the
new research, health, edition-name and timeline tests. It does not convert the
separate live-provider observations below into a claim that Google/Brave or GDELT
answered successfully.

Migration check:

```powershell
.\.venv\Scripts\alembic.exe -c alembic.ini upgrade head
```

The new revision `b7f2c1d9a310` adds discovery metadata and the research acceptance
policy. It was applied to the real database after a backup-copy trial.

## 4. Acceptance matrix

| ID | Result |
| --- | --- |
| U01 | Readable server-rendered health rows; JSON retained only at `/system/check.json`. |
| U02 | Dashboard, branding, build, edition, archive, appearance, stories, jobs, audit, diagnostics and account page heroes removed or reduced; destructive/consent warnings retained. |
| N01 | Proof PDF has no About block, footer boilerplate or source appendix. |
| N02 | Demo editions still carry the synthetic fixture notice. |
| N03 | Automatic chronology is conservative; repeated bulletins do not create a timeline. |
| N04 | Forced/justified sequences remain available; printed copy has no `REPORTED` token. |
| N05 | Edition rename and build name verified in UI tests and preview template. |
| N06 | Proof stories have real `https://t.me/ThePakistanNews/<id>` targets; no localhost QR. |
| N07 | No verified target → no QR, with editor-only reason. |
| N08 | No source-record box, claim counter, message ID or appendix marker in print. |
| E01 | Posting-frequency notice excluded as `channel_administration`; override retained. |
| E02 | Short English/Urdu/Roman-Urdu alerts remain eligible. |
| E03 | Decorated source copy cleaned at draft and render time; evidence untouched. |
| E04 | Live re-draft has no `reported noted` or repeated background paragraphs. |
| R01 | Real Dawn article discovered, fetched and stored as a candidate (RSS provider). |
| R02 | Unsupported/contested claims held for review; no invented corroboration. |
| R03 | Provider unavailable/rate-limited → bounded failure, manual URL still works. |
| I01 | Existing revision/preview flow unchanged; preview refresh tested. |
| I02 | Published V1 untouched; V2 remains a separate revision. |

## 5. Known limits

- Google CSE and Brave Search were not tested with live credentials. The shipped
  automatic fallback is publisher RSS; GDELT was rate-limited during verification.
- RSS discovery is deliberately conservative and often returns no candidate for a
  synthetic/future channel story. That is reported honestly.
- The live proof used a 3B local model. Larger-model prose and hallucination rates
  will differ; the audit remains the release gate.
- Accepted research is used as attributed context and included in the audit
  corpus; it is not independent verification and is never labelled as such.
- The proof is a draft in the temp directory. No new edition was published and no
  historic PDF was rewritten.
- The live Telegram session and rights records exist on this machine, but this
  session did not perform a new Telegram collection; the proof reused stored posts.
- No public evidence-page host is configured. Printed codes therefore use real
  Telegram post URLs when available and print nothing otherwise.
- Direct `t.me` HTTP checks from this network timed out during the final review.
  The channel and message IDs are real and the QR payloads decode to those URLs,
  but phone reachability must be re-checked on a network that can open Telegram.
