# Telegram-to-Newspaper: Autonomous Build Specification

**Status:** Implementation contract for an AI coding agent  
**Target:** Windows-first, local-first, scheduled, primarily hands-off newspaper/e-paper production application  
**Product working name:** Channel Press (working internal name only; branding is configurable)  
**Audience:** OpenCode or another autonomous coding agent  
**Companion files:** `AGENTS.md`, `START_HERE.md`, `ACCEPTANCE_TESTS.md`  
**Priority:** User requirements in this document supersede implementation conveniences. Refer to `AGENTS.md` for execution discipline.

> **Mission.** Build and test a working application that ingests authorized **original** posts from one Telegram news channel; groups posts by real-world story; reconciles corrections; writes and fact-checks English newspaper articles with carefully attributed claims; curates source images and meaningful stills from videos; lays out a variable-length, professional print newspaper; and exports print-ready PDF and locally viewable e-paper editions according to configurable schedules. Provide a usable, secure administrative interface, reliable retry/resume logic, and thorough documentation. Build the system, not only mockups or a specification.

## 0. Non-negotiable operational and legal guardrails

1. **No paid dependencies.** The default implementation must function with local, freely usable software/models and without a paid AI API, subscription, or hosted server. Internet is still needed for authorized Telegram access and optional verification. Never silently introduce charges, trial quotas requiring billing, or paid-only licensing. Optional no-cost APIs can be added as fallbacks but must never be necessary for basic operation.
2. **Source authorization and rights.** Telegram platform access is not equivalent to permission to use content for AI. The Telegram content-licensing and API terms restrict AI-related processing and provide narrow consent exceptions. The application must not scrape arbitrary public channels or bypass API restrictions. Require an affirmative, revocable, auditable declaration of authority/rights and, where required, individual informed consent of relevant contributors before enabling AI processing. Do not assume that channel-admin status automatically satisfies all rights or consent requirements. Implement a usable **licensed direct-submission/import** path for channel-owner-supplied content when API terms or permissions prevent direct automated ingestion. Never proceed with prohibited ingestion. Explain constraints during setup. Do not use channel data to train or fine-tune models. Respect copyright, licenses, attribution, deletion/consent withdrawal and media rights. For third-party source content, excerpts and citations only as permitted; no wholesale copying.
3. **Editorial accuracy over output volume.** No invented facts, fabricated sourcing, invented photographs, altered factual scenes, artificial eyewitness quotations, fake maps, speculative casualty counts presented as certain, or generic filler. Unknown remains unknown. Distinguish allegations, claims, confirmations, corrections, analyses and editorials. A story may be omitted if supporting evidence is inadequate.
4. **Human oversight for elevated risk.** Sensitive/high-impact claims, disputed evidence, graphic imagery, potentially identifying victims, operationally sensitive details or consequential allegations must be visibly flagged and queued for editorial review by policy. The scheduled workflow can finish generating a draft; do not silently publish high-risk claims without the configured approval. For non-risk stories, owner may enable unattended publication.
5. **At least one real, reproducible, end-to-end path.** The final app must run on Windows with a fixture dataset without Telegram secrets, generate a multi-page newspaper PDF, create an e-paper preview, produce source/reference links, and pass automated tests. Real Telegram mode becomes functional when authorized credentials and rights are supplied. Clearly distinguish fixture/demo mode from live mode.
6. **No all-day model loading.** Application must remain light while idle. Heavy workers/models launch at a scheduled/manual job and unload afterward; user can continue ordinary browsing and media playback.
7. **Single channel in the initial UI.** Architect channel/publication IDs and access control for eventual multiple channels, but do not implement a public multi-tenant SaaS, broad source aggregation, advertising, subscriptions or a full public website now.
8. **The coding agent must build autonomously.** Ask the user only for genuinely unavailable secrets, authorization/consent, identity/branding preferences needed for *live publishing*, or an unrecoverable hardware/environment blocker. Use safe defaults, fixture data and clear instructions for everything else. Never fabricate authorization or claim a live integration was tested when it was not.

**Reference material to verify against the latest official version while implementing:**
- Telegram API terms: https://core.telegram.org/api/terms
- Telegram content licensing / AI: https://telegram.org/tos/content-licensing
- Telegram bot developer terms: https://telegram.org/tos/bot-developers
- Telegram history method (user accounts only): https://core.telegram.org/method/messages.getHistory
- Telegram Bot API: https://core.telegram.org/bots/api
- Paged.js: https://pagedjs.org/en/documentation/
- Ollama Windows: https://github.com/ollama/ollama/blob/main/docs/windows.mdx
- Cloudflare Workers free limits: https://developers.cloudflare.com/workers/platform/limits/
- Oracle Cloud Always Free: https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm

## 1. Exact product boundaries

### 1.1 Audience and editorial coverage

- Purpose: produce a professional, source-transparent roundup for an owner-managed Telegram channel covering **military, counterterrorism, geopolitics, war reporting, regional/domestic news, commentary, opinion and analysis**.
- Make each issue intelligible to someone who has not read the channel that day, while explicitly acknowledging that it is **a roundup based primarily on that channel**; include channel attribution, follow/subscribe link and a discreet footer/colophon.
- Primary evidence originates in the channel. External sources are *secondary*: short excerpts, supporting legitimacy, context, cross-checks, citations. Do not let web content overwhelm the channel or silently replace its reporting.
- English output; translate posts in other languages into fluent English while retaining original names and meaning. Future Urdu output is a documented extension, not a blocking deliverable.
- Style: credible contemporary newspaper/magazine hybrid; restrained, substantial, serious, human-sounding writing. No AI-sounding inflation or padding.

### 1.2 Included in v1

- Source connection for **one** authorized Telegram channel, manual licensed import and 30-day initial backfill where permitted; optional editor-supplied Telegram post links for relevant context.
- Original-post filtering, albums/media groups, edits and deletions, explicit reply chains, chronological processing, duplicate/correction reconciliation.
- Local story memory with a *short* context window for older stories; related-post clustering and post splitting when multiple unrelated stories appear in one post.
- Fixed primary sections with adjustable secondary sections, editorial relevance ranking **without using views/reactions**.
- Structured local or free fallback LLM pipeline, evidence traceability, guarded external reference lookup, attribution/fact-check states, human review queue.
- Images, video thumbnail/frame selection, collages, captions, annotated evidence boxes, source-aware charts, sequence/timeline graphics and accurate maps when evidence supports them.
- Configurable map worldview/territorial convention per edition; disclosed contested boundaries; no unverified geospatial information.
- Modern tabloid print PDF, variable number of pages, 3–4-column article pages, flexible front page, category-based layout families, internal e-paper preview, offline export bundle.
- Per-headline QR strategies linking to Telegram or an evidence-link landing page where one is actually hosted; source citations for media and claims.
- Configurable daily, selected weekday, weekly, monthly roundup, manual and special edition schedules; independent cutoff and release settings; missed-job recovery.
- Login, password hashing, roles/permissions for the one channel and its editions; ownership and authorization records, protected secrets, audit log.
- Export: print PDF, e-paper HTML/page previews, individual page images if feasible and local output directory; optional pluggable cloud destination requiring explicit setup (no promise of remote site).
- Self-contained sample data, automated tests, log-based diagnostics, README, end-user manual, admin documentation and setup scripts.

### 1.3 Explicitly defer

- Multi-channel merging, public self-registration, paid tiers, public news website/SEO/CMS, mobile apps, real-time breaking-news push automation, always-running Telegram monitoring as a requirement, print distribution integration, Urdu editions, custom AI training, proprietary cloud dependence.
- Do **not** secretly implement scope creep. Design interfaces cleanly for later additions.

## 2. Recommended implementation stack (choose only tested substitutes)

**Local-first web app:** Python 3.12+ backend with FastAPI, SQLAlchemy 2 + Alembic migrations, Pydantic settings, SQLite in WAL mode for initial deployment, Jinja2 templates + HTMX and carefully written vanilla JavaScript for a lightweight responsive admin UI. CSS may use a small build-time tool, but avoid framework-heavy SPAs and avoid TypeScript unless a convincing blocker exists. SQLite storage can be replaced with PostgreSQL later via repositories/migrations.

**Background execution:** Explicit persisted job state machine, bounded task queue, Windows Task Scheduler-triggered worker command and one-shot CLI commands. APScheduler may coordinate while server is running, but **must not be the only scheduler**, as the UI server may be stopped. Jobs wake the processing engine, execute, release memory and exit. Manual `run now` works independently.

**Telegram:** Isolate an adapter for official bot/new-post updates and an authenticated MTProto user-client history adapter (e.g. Telethon) **only when authorized and allowed by current Telegram terms**. Bot API cannot perform the same history backfill as a user account. An import adapter for rights-cleared Telegram export JSON/direct owner-supplied post data is mandatory, not just a dead fallback. Configure the app with either method after checking usage rights. Implement reconnecting, polite rate limiting, `FLOOD_WAIT` handling and no scraping of unrelated channels.

**AI inference:** Ollama on Windows or llama.cpp as local open-weight model serving. Build a provider abstraction compatible with local completion/embedding/vision models and **optional** zero-cost remote providers. Never hardwire one free API. Pick an actually tested quantized model that fits the detected RTX 3060 Laptop GPU VRAM (often 6 GB; detect rather than assume) and 16 GB RAM, prioritizing 3B–4B-class models for baseline. Larger models are optional and tested before deployment. Use 1 LLM request at a time by default, bounded context and chunking, lightweight embeddings and deterministic algorithms wherever possible. Record model name/version, prompt/template version, inputs hash and output validation.

**Media:** Pillow/OpenCV/FFmpeg for metadata, scaling, smart crop, contact sheets and frame sampling. Prefer salience/scene change plus quality heuristics; optional compact vision model; do not require heavy video-language inference for each frame. Generate QR with an offline library. Graphics/maps using SVG/vector techniques, charts from verified numerical claims and licensed cartography assets.

**Print typesetting:** Semantic HTML/CSS print composition with **Paged.js plus headless Chromium/Playwright** as the baseline; use named pages, page-specific layouts and deterministic PDF export. Prototype and visually test real sample layouts before committing. If Paged.js cannot meet concrete acceptance tests, substitute a proven custom pagination algorithm on top of HTML/CSS and PDF printing, document the measured reason, and retain the same outputs. Do not rely on WeasyPrint's multi-column page behavior untested; its docs note relevant limitations. Use embedded/packaged open-license print fonts without handing font binaries into version control if licensing forbids it. Keep true vector text in PDFs.

**Dev/runtime packaging:** `pyproject.toml` with pinned versions/lock file, reproducible setup via PowerShell scripts, Windows Task Scheduler registration/removal scripts, Playwright browser setup, preflight diagnostics and a demo dataset. Optional Docker Compose for later hosting is supplementary, not the main Windows installation path.

**Architecture choice principles:** simple modular monolith; no needless microservices, no Redis requirement, no always-on Celery broker, no expensive cloud products, no implicit user data exfiltration.

## 3. System workflow and lifecycle

**Workflow:**

`source authorization → fetch permitted window → normalize + validate → aggregate albums/edit histories → content extraction/translation → story segmentation → semantic grouping/reply relationships → correction + conflict reconciliation → evidence ledger → optional external validation → editorial prioritization → article drafting → factual QA / risk gating → media selection → page planning → typography/pagination → technical QC → preview/approval rules → release/export → checkpoint + archive`

- Job schedule has `collection_start`, `cutoff_at`, `build_not_before`, `target_release_at`, `timezone`, and missed-run policy. Default timezone `Asia/Karachi`, configurable. Cutoff is an exclusive or inclusive boundary **specified consistently** in UTC storage and local UI; never lose or duplicate posts at midnight/DST/timezone changes.
- Normal edition consumes eligible source posts from **previous completed daily cutoff to current cutoff**, not merely posts 'since last successful fetch'; retries must not create gaps. Special editions use explicitly chosen intervals and **do not move the daily watermark** unless editor opts in. Weekly/monthly roundups reuse stored unique source posts and can deliberately overlap earlier editions, labeling the date range.
- Startup first-run: wizard → authorization → source validation → optional 30-day backfill (text-first) → design defaults → fixture rehearsal → optional live rehearsal → activate schedule. Backfill context is not automatically 'today's news'.
- Pull-only by default at build window (no constant Telegram connection). Provide optional short scheduled lightweight sync for better deletion/edit capture, but default remains no all-day processing. A later sync can reconcile source edits to saved posts; published issues remain immutable unless formally corrected/reissued.
- If laptop is off/asleep at deadline, Task Scheduler must invoke catch-up at next available time where supported; **never pretend to publish at a past time**. Show the real release timestamp and job status. One worker only per source/edition interval, with locks and stale-lock recovery.
- Persist intermediate stage artifacts/checkpoints. Retry transient faults with exponential backoff and reason-coded statuses. Provide skip-and-flag capability for a single bad media file without discarding the whole edition. Avoid incomplete automatic publication.
- Stop/abort/resume is supported. UI can be closed without killing a safely launched independent worker; test it. At idle, heavy models are unloaded; configure model keepalive to expire promptly.

## 4. Source ingestion: precise semantics

### 4.1 Allowed vs excluded posts

**Include:** directly posted channel messages/captions/text and media, even where the author has paraphrased/copied elsewhere, subject to content rights and authorization; directly authored commentary referencing external material; messages with genuine links that are relevant citations.

**Exclude by default:** Telegram-native forwarded posts (detect `forward_origin` / service-specific flags robustly); messages consisting only of promotions, channel advertisements, 'go see this other post/channel' links, empty/no-news housekeeping and service events; imported items without confirmed rights. A substantive post that includes a cross-link **must not** be excluded solely because it has a link. Treat external hyperlinks as sources, not automatically as article text.

Track each item's channel ID, immutable message ID, stable URL if available, posted/edited timestamps, caption/text, reply-to ID, album/media-group ID, forward metadata, language, media descriptors, full raw metadata hash, deletion/tombstone state, rights provenance and filtering reason. No Telegram reactions/views used for ranking.

### 4.2 Album and video handling

- Telegram media-group members are individual source objects with potentially shared text/caption; form one logical editorial post unit while preserving all message IDs and individual media links.
- Capture photo/video preview and metadata; lazily download full-resolution assets for likely published stories only. Confirm relevant source media may be reused. Preserve checksum and original, create derivative crops separately.
- Extract candidate video frames at scene boundaries and salient segments, reject blurry/black/duplicate frames. Optionally use subtitles/speech transcripts if explicitly enabled and resource budget allows; do not require transcription to produce the edition. Each frame records source video, timestamp and alteration log.
- Reject manipulated or synthetically generated imagery that lacks appropriate labeling; never fabricate replacement 'news photos'. Do not infer an image's location/date from its appearance without evidence.

### 4.3 Corrections, updates, deletions

- Treat later updates and explicitly linked replies as related evidence. Telegram reply-to is a strong graph edge; related entities/event/time/location may form additional edges.
- Store edit versions where law/consent permits; prefer latest verified text for prepublication work. Post-deletion respect platform/rights obligations, flag article for editorial reconsideration and avoid resurrecting revoked content.
- Distinguish 'new information', 'correction', 'contradiction', 'withdrawal', 'duplicate' and 'background'. A corrected claim supersedes an earlier one in narration but may be documented in a corrections note where editorially warranted.
- Once released, an issue stays versioned. Later corrections produce an erratum, corrected reissue or next-edition update with visible version and time; never silently rewrite a public historical issue.

### 4.4 Context expansion

- Initial backfill default: **30 days**, configurable. It builds lightweight text/evidence/story context; historical content does not automatically create 30 days of newspapers.
- An editor may paste Telegram post URL(s) as explicit context. Validate source permissions, fetch only accessible/authorized items, mark as context, and preserve source link. If unavailable, prompt for rights-cleared text/media import instead of guessing.
- Search stored story history by event identity/reply/thread/entities and text similarity, retrieving a concise relevant context summary; older data must be cited as dated context, not today's development.

## 5. Editorial pipeline and data contracts

**Do not put 70–150 raw posts and media into a single prompt.** This is not economical or reliable on a laptop. Use stage-specific structured outputs validated with schemas.

1. **Normalize:** deterministic filtering, URL classification, language detection, transliteration as needed, entities/locations/dates extraction and post-level preliminary topic tags. Reject no-news content based on traceable rules + lightweight classifier.
2. **Split:** one post can contain several unrelated story fragments; fragments retain precise `(post_id, excerpt/span)` provenance. Correlated subjects may remain within one coherent story.
3. **Cluster:** fuse explicit Telegram replies, same incident references, semantic embeddings, proper names, locations and temporal proximity. Do not merge unrelated stories merely because they share 'Pakistan', 'military', 'border', etc. Make a relationship graph and human-adjustable merge/split actions.
4. **Reconcile:** create a chronology and claim ledger: `claim`, `claim_type`, `source_id`, `source_span`, `timestamp`, `certainty`, `verification_status`, `correction_of`, `contradictions`, `external_support_ids`.
5. **Validate:** perform restrained lookup of official or credible external sources when necessary for legitimacy/context. Never copy full external articles. Record title, publisher, link, access time and short excerpt/summary. If external research is unavailable, do not pretend it was completed.
6. **Classify:** primary sections `Front Page`, `National & Internal`, `Regional`, `International & Geopolitics`, `Defence & Security`, `Conflict & War`, `Analysis`, `Opinion & Editorial`, `Briefs`, with configuration for subcategories and section ordering. A story can carry multiple topic tags but has one main print placement plus related-story references.
7. **Prioritize:** event magnitude, public consequence, independent significance, novelty, reliable evidence, developments and proximity to audience; NOT view counts/reactions. Front-page threshold can override normal template, e.g. a major outbreak of war. Do not over-sensationalize.
8. **Draft:** output title, deck/subhead, standfirst (if appropriate), lead, body paragraphs, optional key facts/chronology/timeline/chart spec, related-story IDs, image candidates, captions, source footnotes, quote attribution, genre and length target. Use fluent English. Avoid repetitive transitions, invented quotes, nonessential 'in a significant development', 'underscoring', 'amid growing tensions', etc.
9. **QA:** independent structured audit checks every hard claim against cited source spans, flags invented named entities/dates/figures, missing uncertainty, inconsistencies, unsupported maps/charts and temporal leaks. Flag rather than conceal contradictions. Article with unresolved elevated-risk issue cannot automatically reach final publication.
10. **Editorial controls:** reviewers can approve, reject, edit, lock text, split/merge story, change section/priority, replace photo, edit caption, hold article, pin front-page story or force a new section. Manual edits must not be overwritten during regen unless editor explicitly permits it.

### 5.1 Editorial genres and attribution

- **News:** factual, attributed, restrained. A claim by an official is still a claim unless separately substantiated; an official statement can be accurately reported as a statement.
- **Analysis:** draws reasoned inferences from identified evidence, labels interpretation, shows uncertainty and meaningful alternative explanations.
- **Opinion / editorial:** explicitly labeled, author/source clearly identified, distinct from ordinary news. AI may draft an analytical synthesis with evidence but must never invent a human byline or assert unstated opinions as the channel's editorial stance.
- **Editor's note:** concise explanatory aside to disclose uncertainty, correction, sourcing limitation, or publication policy, not a mechanism to launder speculation.
- **War/CT:** avoid glorification, dehumanizing language, exposure of vulnerable individuals, and publication of actionable tactical details not essential for legitimate reporting. Review high-risk details and graphic content. Do not treat propaganda claims as ground truth.

### 5.2 Evidence rules

Maintain a small explicit source-category system: `channel_original`, `primary_official`, `independent_reporting`, `direct_witness`, `unverified_social`, and `editor_supplied_context`, with editable trust/confirmation handling (not a spurious numerical truth score). Every substantive claim maps to at least one original source or duly labeled external support. The article may synthesize **without quoting every post verbatim**. Every selected photograph, video still and map input has a provenance link. Captions must not claim facts absent from the ledger. For a claim without support, remove it, qualify it prominently, or move it to human review; never make up a citation.

### 5.3 Output quality assessment

Implement deterministic style lints + factual audit + length/coverage rubric, with metrics stored per edition: eligible post count, included post count, excluded reason distribution, story count, duplicate collapse, unsupported-claim count, citation coverage, flagged stories, generated pages, overflow/errors and editor actions. Scores are diagnostic, not fictional external endorsements.

## 6. Layout and newspaper art direction

### 6.1 Visual identity

The app must look like a serious professional newspaper management tool, not an AI demo. **No neon gradients, glassmorphism, excessive rounded cards, glowing borders, synthetic-looking icon overload, 'breaking' badges everywhere or generic promotional language.** Editorial print visual identity: disciplined grid, black/charcoal ink, off-white paper, optional subdued deep red/navy accent, coherent editorial serif + functional sans-serif, balanced whitespace, thin rules and purposeful typographic hierarchy. Maintain print contrast.

Publication identity is configurable from UI: publication name, owner/channel handle, masthead/logo, short slogan, footer text, publication city, timezone, contact/follow URL, issue number/date/volume conventions, accent palette, licensed fonts, section labels, audience country/cartographic convention, language, print size and export preferences. Provide a thoughtful non-branded default only for fixtures.

### 6.2 Page format and templates

- Default **tabloid 11 x 17 in (279.4 x 431.8 mm)** portrait; optional A3 alternate. Explicit safe margins, embedded fonts, print-appropriate images and optional bleed/trim/crop marks. PDF must remain searchable with selectable vector text. Distinguish office-print-ready PDF (mandatory: correctly sized pages, no clipping, sufficient image resolution, embedded fonts where allowed) from print-shop PDF/X or CMYK prepress certification (only label supported when actually validated; otherwise clearly mark as optional/deferred).
- News/article pages typically **3 or 4 balanced columns**, chosen by legibility. Front page is **1–2 major visual zones** with headline hierarchy and supporting 3–4 column briefs; don't force it to behave like an inside page.
- Support named template families: `front_standard`, `front_major_event`, `national`, `regional`, `international`, `defence`, `conflict_visual`, `analysis_feature`, `opinion`, `briefs`, `chronology_explainer`, `closing_light`.
- Each family shares a design system but has different information density/image treatment. Major events may invoke a larger headline and multi-page special package with contextual timelines and sidebars; do not change brand fonts or randomly choose incompatible layouts.
- Flexible number of pages driven by useful content, not a fixed page count. Allow sensible article continuations with 'Continued on page X' / continuation heading where page references can be validated. Prefer not to split an article in a visually confusing place.
- Editorial utility content may fill remaining space **only from a verified, separately labeled, curated source**: `On this day`, region/history quiz, short explainer, fact box, or small cross-referenced brief. No invented anniversaries/trivia, no padding or repeating already printed material. Editor can disable supplementary content entirely.
- Proper image credits, alt text for digital edition, captions, no image stretching. No forced equal-height tiles with giant gaps; no stranded headlines, widows/orphans where avoidable, clipped text or overlapped components.

### 6.3 Deterministic layout algorithm

1. Determine stories/lengths, priority, sections and candidate illustrations.
2. Assign front-page lead and supporting stories, then section-specific story packages.
3. Select a template from explicit rules based on number of articles, visual assets and priority, not arbitrary per-run prompt creativity.
4. Generate structured content blocks (article, headline, standfirst, image, caption, sidebar, quote, timeline, chart, map, quiz, section heading, QR).
5. Estimate available page area and font metrics, lay out and paginate in browser; render and inspect final page geometry.
6. Fix overset using ranked strategies: change approved template variant → resize/relocate image within constraints → redistribute sidebars → create continuation page → shorten *redundancy only* via an editorial rewrite maintaining all facts. Never shrink type below configurable minimum or silently drop claims.
7. Render PDF and HTML e-paper using the same edition data/config snapshot; record page count, pixel preview paths, story placements and QA warnings.

Document and enforce font min/max, headline grades, line height, column gap, baseline rhythm, caption size, contrast and text-area coverage. Use snapshot/image regression tests against reference page specimens. Avoid calling the PDF 'print-ready' unless print settings and layout QA actually pass.

## 7. Multimedia, graphics and maps

### 7.1 News photography/video stills

- Rank permitted source photos on editorial relevance, visible subject, sharpness, original resolution, framing, appropriateness and source attribution, **not Telegram engagement**.
- Video-frame pipeline samples a bounded number of timestamps, identifies scene changes and image clarity, avoids transition/black/blurred/duplicate frames, and lets editor replace frame/timestamp. Add small `VIDEO STILL` label when appropriate.
- Basic corrections: rotation, exposure/contrast, sharpening, de-noising, proportional crop; keep the original and record adjustments. No content-generating 'enhancement' that invents details or misleading composites. Collages must preserve distinct image boundaries and corresponding sources/captions.
- Respect sensitive visual policies: victim privacy, distressing imagery, graphic-content review. Never automatically publish gruesome material merely because it is available.

### 7.2 Chart and timeline generation

- A chart needs typed source figures, units, date ranges, provenance and schema validation. If numerical sources disagree, disclose or omit chart. No hand-drawn invented trend lines, comparison totals, confidence percentages or fabricated maps. Timelines likewise use confirmed or attributed timestamps with timezone clarity.
- Visuals are rendered as SVG/vector where possible for crisp PDF use; media footprints and fonts are constrained to template grid.

### 7.3 Per-edition geographic worldview / `cartographic_profile`

This is a deliberate configurable **publisher map convention**, not a license to misrepresent evidence. Expose an edition-scoped `allegiance` or `audience_cartographic_convention` setting that selects a provenance-tracked `cartographic_profile`; the setting governs map presentation and jurisdictional convention, **not journalistic truthfulness or political allegiance of the reporting**. Provide country-based profiles (start with `neutral`, `pakistan`, `egypt`) that specify language/transliteration, approved administrative boundary data, disputed-boundary line conventions, required labels, source license and attribution. Profiles cannot silently overwrite geographic facts. Render disputed regions using source-backed, publisher-specified claimed boundaries with accurate legend/disclosure such as 'Boundary representation follows the publication's Pakistan cartographic convention; territorial status is disputed.' Where jurisdictional requirements apply, use reviewed, appropriately licensed official map layers. The user may configure their own approved profile, but must supply valid geographic sources and accept a prepublication preview. Unverified border geometry must **never** be generated by an LLM.

- For every map: title, region, scale/reference, source dataset/version, map date if relevant, units, status of claims, legend and proofreading check.
- First version may prioritize **accurate locator maps** rather than full GIS. If a map cannot be verified or suitable source data is not available, replace with a sourced text/location box instead of fictional output.
- Store cartographic profile as part of each edition snapshot so subsequent configuration changes do not retroactively change historical PDFs.

## 8. QR, links and evidence bundles

Headline QR strategy must **work on real paper** and not point to an unreachable localhost URL.

- A story with a single public accessible Telegram source: QR directly to that canonical post, with visible short source notation.
- Multi-post story: generate a structured evidence index listing all source Telegram permalinks, dates and relevant external references; link via QR to a **publicly reachable** lightweight HTML page **only if a configured static-hosting/publication target has successfully published it**. HTML evidence pages contain no secret/private links and must satisfy consent/rights.
- In offline/no-host mode: if a short source URL set can fit as plain text in a tested, readable QR, permit text-QR; otherwise QR targets the lead public Telegram post and visible printed `Sources: page N` refers to an evidence appendix in PDF/e-paper. Never encode an imaginary web address. Provide clickable sources in digital PDF and bundled offline HTML evidence page.
- Private-channel sources: detect inaccessible public permalinks; don't claim a public QR will work. Require publication-safe public source page or visible source note without QR, depending on policy.
- Each substantive evidence item, chart and photo has a caption/citation link or a numbered reference resolving to a listed source. Test scannability at chosen physical QR print size and contrast.

## 9. Edition scheduling, types and publishing

### 9.1 Edition types

`daily`, `weekly_roundup`, `monthly_roundup`, `special`, `manual`. Each has time window, timezone, section selection, profile, editorial policy, cutoffs and release destinations. Daily schedule default is disabled until owner opts in and authorizes source; initial suggested daily cutoff 05:00 and target release 08:00 local, both editable (no claim of guaranteed runtime). Validate that estimated build time fits or warn. Weekend/weekday selections supported. Special editions use manual start/end and can override front page, maintain own volume/issue designation and do not corrupt daily checkpoints.

### 9.2 Job state machine

`scheduled → source_validation → fetching → normalization → clustering → drafting → auditing → media → layout → quality_check → awaiting_review OR ready_to_publish → publishing → published`, with `failed_retryable`, `failed_terminal`, `canceled` and `resumable` transitions.

- Persist `job_id`, `idempotency_key`, interval, source revision hashes, config snapshots, stages, timing, retries, last exception, outputs and human actions.
- Atomic export: write to temp folder → PDF/text/layout validation → rename to final folder → upload/copy to configured target → save publication record → update checkpoint. Partial failure must not mark successful release.
- On failure, log a clear user-action message, run reasonable automatic retries and model fallback, never silently emit empty PDFs. Track model/provider fallback separately and alert in dashboard.
- Missed schedule due to PC off: catch-up using Windows scheduler when machine returns; distinguish scheduled/release timestamps; avoid double publishing.

### 9.3 Destinations

Mandatory: directory containing print PDF, e-paper HTML+assets, interactive per-page viewer, link appendix and metadata manifest (checksums, provenance, issue time, version). Optional: page PNG previews and a general upload adapter (e.g. destination folder/supported cloud storage configured and tested). Full public website and subscription delivery explicitly deferred. Deliver e-paper in a static bundle that can be hosted later without rewriting it.

## 10. Security, account/permission model and privacy

- First-run local admin setup; username/password login, strong password hash (Argon2id), session rotation, CSRF protection, secure cookie settings where TLS is used, brute-force/login throttling, backup/restore and audit logging.
- Roles: `owner` (source ownership + publication and user management), `editor` (articles, layout, approvals and designated editions), `viewer` (read-only preview/export), optional `operator` (job execution and diagnostics). Model explicit ACLs `user → channel → publication/edition` and default **deny**; users cannot assume control of a channel already owned by someone else; claims of ownership require admin-approved transfer and audit trail.
- v1 UI manages one source channel; schema still supports future `channel_id`, edition permission scopes and ownership uniqueness. No public self-signup. Admin creates/invites additional user accounts and manages their per-edition permissions.
- Store API IDs, hashes, provider keys, bot tokens, MTProto sessions and passwords securely (Windows DPAPI/keyring or encrypted secrets with an explicitly managed key), never plaintext in tracked files, logs, PDF, exported HTML or browser localStorage. Use `.env.example` with dummy values only. No secrets in URLs. Validate file upload paths and URL fetch targets (SSRF protection). External searches are rate-limited and allowlisted where practical.
- Do not send full Telegram content to remote AI providers by default. Optional external providers must require explicit data-sharing confirmation and disclose retention/privacy considerations. Restrict external citations to minimal permitted excerpts.
- Consent/rights panel: source owner identity, scope, accepted terms version, relevant contributor consent records where necessary, content permissions, revocation actions, audit history and retention settings. Deny activation without a valid legal/authorization basis; preserve test/demo mode. Rights withdrawal triggers a workflow to remove affected unpublished data and assess historical output obligations.
- Harden private multi-user data paths now, even though only one channel is configured. Every API call checks server-side permissions; UI hiding alone is not access control.

## 11. Resources and no-cost model strategy

Hardware target: 12th-gen Core i7 laptop, RTX 3060 Laptop GPU (auto-detect actual VRAM), 16 GB RAM. Nonblocking defaults:

- **Idle:** no loaded LLM, no background embeddings, no persistent browser automation, minimal web UI, no continuous media decoding. Worker only runs when required.
- **Active default:** one model at a time, one video processing task, CPU thread limit configurable (default conservative), rate-limited IO, Windows-friendly process priority, batch processing and early-release of GPU resources. Low-resource / balanced / high-quality profiles configurable and measured; pause/yield processing to improve responsiveness.
- Pipeline relies on deterministic filtering, small embeddings model, narrow prompts, validated JSON schemas, caching and hierarchical summaries. Do not recompute unchanged old posts/claims/stories on every edition.
- Define `ModelProvider`, `EmbeddingProvider`, `VisionProvider`, `WebReferenceProvider` and separate task configurations; model routing chooses quality/cost/VRAM-appropriate option. Test a default quantized local model on actual hardware; **do not claim an unbenchmarked model guarantees excellent newspaper writing**. Support swap without style drift via stable prompts, output schemas, examples and AGENTS.md.
- During 150+ posts per day, prioritize important clusters; compute selective visual analysis only for selected stories. Keep run-time estimates honest and warn if scheduled deadline is infeasible.
- For possible **free remote operation**, investigate and document current limits for Oracle Cloud Always Free CPU VMs for light coordination, and static hosting such as Cloudflare Pages or GitHub Pages for publicly shareable evidence/edition bundles (subject to owner permission and platform terms). Cloudflare Workers Free has strict per-request CPU limits and is **not** a suitable full LLM/PDF processing machine. Zero-cost VM quota, regional capacity and hosting availability are not guaranteed; do not create a cloud account or enable billing automatically. Optional remote 'free tier' integrations are supplemental and explicitly labeled unstable/quotas subject to change; fallback to local. On cloud, offer documented no-cost *lightweight scheduler/static hosting* options but never promise free remote 150-post LLM/multimedia production. Provisioning requires user credentials and opt-in; the app's baseline runs without any cloud account.

## 12. UI views and interactions

1. **First-run setup:** install status/preflight (FFmpeg, Chromium, model, GPU, storage), local admin creation, channel authorization/consent, identity, print format, timezone/cutoff, editorial profile, dry run.
2. **Overview:** upcoming editions, last run, active state, source-post volume, coverage by section, pending reviews, warnings, storage and model availability. Show run-now, pause and open-last-edition.
3. **Source desk:** chronological accepted/rejected posts, source link, edit/reply/media grouping, filter reasons, historical import, paste context links, reprocess.
4. **Story desk:** clusters with source counts, linked reply edges, chronology, claim ledger, merge/split operations, section/importance, genre and title proposals.
5. **Editorial review:** newspaper article editor with source traceability and two-pane evidence, flagged uncertain/contradictory claims, approval/reject/hold, media pickers/caption control, explicit overrides.
6. **Page studio:** paginated print previews with named templates, readable zoom, left/right page navigation, page tree, overflow flags, section/story placements and action to regenerate layout *without rewriting approved articles*.
7. **Schedule & editions:** schedule forms, edition type, cutoff/release, special edition editor, history, versions and publish destinations.
8. **Branding & cartography:** masthead, country profile, map sources, legal legend, fonts/palette, graphics/print profiles, channel attribution and QR strategy.
9. **Users & permissions:** admin-only users/roles, channel ownership and publication access, logs.
10. **System/diagnostics:** model selection, CPU/GPU caps, provider connectivity, retry log, backups, export configuration and license/compliance state.

Clean typography, restrained colors and purposeful information density. Responsiveness is for managing on a normal desktop/laptop; no mobile-first compromise in print-studio tools. Provide keyboard-accessible controls and validation/error affordances. All UI labels must be functional; avoid fake buttons, placeholder charts, dead links or inexplicable 'coming soon' elements for v1 features.

## 13. Database and source-of-truth schema

Use migrations and explicit indexes/unique constraints. Proposed entities (adapt names but keep semantics):

- `users`, `roles`, `channel_ownership`, `channel_permissions`, `sessions`, `audit_events`
- `channels`, `source_connections`, `rights_grants`, `contributors_consent`, `source_imports`, `source_posts`, `post_versions`, `post_media`, `post_fragments`, `reply_edges`, `source_links`
- `story_clusters`, `story_relationships`, `story_post_fragments`, `story_timeline_events`, `story_claims`, `claim_evidence`, `external_references`, `story_context_links`
- `articles`, `article_versions`, `article_claim_links`, `article_media`, `review_flags`, `review_decisions`, `editorial_overrides`
- `publication_profiles`, `cartographic_profiles`, `edition_schedules`, `editions`, `edition_articles`, `edition_page_layouts`, `edition_assets`, `source_appendices`, `export_targets`, `publication_attempts`
- `jobs`, `job_steps`, `retry_attempts`, `model_runs`, `processing_cache`, `configuration_snapshots`

Use unique `(channel_id, telegram_message_id)`, stable fragment/source IDs, immutable article/edition revision numbers, file hash manifests and idempotent scheduled job identities. All timestamps stored UTC and displayed in publication timezone. Structure asset storage per source and edition, with garbage collection and backup restoration tested. Separate raw source content, extracted facts, editorial versions and published artifacts.

## 14. External research adapter and provenance

The app may follow hyperlinks already found in authorized posts (only accessible/rights-permitted pages); retrieve short relevant metadata/excerpts and retain publisher/title/URL/time, with per-domain limits and robots/terms respect. General open-web search is **optional** and may use a documented lawful non-paid method when configured; if no reliable mechanism is available, mark external verification unavailable and draft only supportable channel-derived claims. Avoid brittle search-engine scraping and unauthenticated unlimited requests. Never infer 'verified' solely from agreement between copied sources. External evidence cannot inflate a minor channel item into unsupported sensational news.

## 15. Completion plan: autonomous milestones and commits

The agent must follow these milestones in order, producing **meaningful atomic Git commits**, running tests at each gate and updating `IMPLEMENTATION_LOG.md`. Do not stop for user signoff between milestones. Only pause for required credentials/permissions; meanwhile continue fixture-first implementation.

**M00 — Repo and contract.** Inspect environment; create repository structure, `.gitignore`, AGENTS.md, plan/risks log, dependency manifest, dev/test commands, fixtures directory and running environment checks. Commit.

**M01 — Schema, auth, roles.** SQLite migrations, Argon2id login, first-run admin, permission enforcement, channel ownership uniqueness, audits, test coverage. Commit.

**M02 — Settings + consent.** Secrets management, rights/consent setup, branding/timezone/edition config, model settings, import eligibility. Commit.

**M03 — Source adapters and fixtures.** Compliant Telegram adapter where authorized, direct-export import, fake deterministic fixture adapter, message/version/media/reply normalization and forward filtering. Integration tests with mocked Telegram; no secret requirement for CI. Commit.

**M04 — Scheduling and jobs.** Windows scheduled wakeup, one-shot worker, cutoff watermark, special/week/month intervals, missed jobs and idempotent retry/resume. Test process restart. Commit.

**M05 — News extraction and grouping.** Language normalization, post splitting, embeddings/reply graph, duplicate and correction handling; story memory/backfill/context links; sample 30-day data tests. Commit.

**M06 — Evidence and editorial AI.** Claim ledger, local provider adapter, structured prompts, external support, attribution, genre handling, ranking and review gating. Implement fallback and deterministic fixture provider for testing. Commit.

**M07 — Media desk.** Asset cache, image ranking, video keyframe selection, QR generation, collation, source-based captions, provenance, safety checks. Commit.

**M08 — Graphics and cartography.** Verified chart/timeline components, one real tested locator map, configurable cartographic policy, invalid-map fallback. Commit.

**M09 — Print design engine.** Brand tokens, tabloid front and interior page templates, typography, pagination rules, vector PDF, print QA and layout snapshots. Commit.

**M10 — Edition planner.** Story selection, section pagination, multi-page continuation, copyfit, verified filler microfeatures, source appendices and QR destination policy. Commit.

**M11 — Dashboard and page studio.** Functional editorial UI, preflight, stories/articles/review, print preview, schedules, settings, roles, logs. Commit.

**M12 — Publication/export.** PDF, HTML bundle, page preview images, manifest, publication destinations, atomic release, history/versions and error recovery. Commit.

**M13 — End-to-end validation and tuning.** 30/70/150+ daily source fixture tests, 30-day backfill, stress/security/access tests, newspaper visual QA, Windows resource mode, genuine local-model trial, accessibility and no-network fallback. Fix defects and commit.

**M14 — Packaging and user docs.** One-command demo run, Windows setup scripts, Task Scheduler registration, `.env.example`, full README, `USER_MANUAL.md`, `ADMIN_GUIDE.md`, `TROUBLESHOOTING.md`, screenshots or genuine sample issue; final QA and commit.

The agent must finish all gates before saying 'done'. No dummy implementations passed off as production. For infeasible external provider/auth integration, leave a clearly labeled integration boundary with a fully working and documented owner-import path; enumerate what requires user secrets for live use. Hard constraints are not grounds to abandon the functioning fixture-to-newspaper pipeline.

## 16. Definition of done

All acceptance tests in `ACCEPTANCE_TESTS.md` pass; application opens on Windows; admin can initialize accounts, import rights-cleared posts, configure publication and trigger an issue; real content produces a coherent, legible PDF and e-paper bundle; post reconciliation and evidence provenance work; QR behavior is non-deceptive; scheduled resume/retry works; security checks hold; default operation needs no paid services; models unload at idle; manual and automated publishing honor approvals; documentation accurately describes every user interaction and limitation. Never claim print-ready or live-connected status without inspecting an actual rendered file / making a real test where authorized.

## 17. Deliverables that must exist in the finished repository

- Running, tested source code with reproducible Windows installer/setup scripts and optional Docker instructions.
- `AGENTS.md`, updated `README.md`, `USER_MANUAL.md`, `ADMIN_GUIDE.md`, `TROUBLESHOOTING.md`, `IMPLEMENTATION_LOG.md`, architecture notes and all database migrations.
- Versioned prompt/style profiles and local-model adapter configuration; dataset/test fixtures with no real user data.
- Actual sample newspaper PDF, HTML e-paper bundle, evidence/source appendix and sample issue manifest generated from fixtures; test screenshots and QA report.
- Passing unit/integration/E2E tests, security checks, print-layout overflow checks and recorded benchmark hardware settings (do not claim performance not measured).
- Git history with meaningful commits corresponding to milestones. Final assistant report lists implemented modules, how to start locally, what user needs to configure for Telegram and distribution, tested outputs, and any honest known limits.

