# Channel Press

Channel Press turns an owner-authorized Telegram channel into a printable local
newspaper and an offline e-paper edition. It runs on a single Windows laptop,
keeps source evidence next to every sentence, and produces a real 11 × 17 inch
tabloid PDF rather than a web page styled as a newspaper.

The repository ships with a complete, credential-free demonstration built from
synthetic fixtures. Nothing in the demo pretends to be live Telegram reporting.

![Dashboard](docs/images/dashboard.png)

## What is implemented

- **Authorized intake.** Owner import of Telegram Desktop JSON or the owner-format
  export, albums, edits, replies, deletions and rights withdrawal. A live MTProto
  adapter exists but was not exercised without credentials; see
  [Limitations](#limitations).
- **Story memory.** Post splitting, duplicate clustering, reply linking,
  correction reconciliation, a claim ledger with chronology and a separate
  deterministic evidence audit.
- **Editorial drafting.** Version-controlled prompt templates, a deterministic
  fixture composer for reproducible tests, and an optional local Ollama provider.
  Every draft is audited against the claim ledger before it can be released.
- **Media and graphics.** Source-linked photos, reversible derivatives,
  OpenCV video frames, collages with credits, sourced charts, chronological
  timelines and locator maps from bundled Natural Earth data.
- **Print engine.** Paged.js pagination rendered through installed Chrome, a
  distinct tabloid front page, 3–4 column interiors, continuation labels, source
  records, a source appendix, embedded fonts and automated overflow/blank-page
  checks.
- **Newsroom UI.** Source desk, rights declarations, stories, article editing and
  review, edition page studio, schedules, jobs, branding, users, audit and
  diagnostics.
- **Scheduling and publication.** Deterministic job keys, watermark catch-up,
  leases and a single-worker file lock, atomic release, manifest and checksums,
  immutable numbered versions and a fully offline e-paper bundle.

![Edition detail](docs/images/edition-detail.png)

## Requirements

- Windows 10/11, Python 3.12.
- Google Chrome installed (the PDF pipeline launches the installed Chrome
  channel through Playwright). No cloud account or paid API is required.
- Optional: Ollama with a local instruct model for non-fixture drafting.
- No FFmpeg is required; OpenCV supplies video frame extraction.

## Install

```powershell
git clone <repository> E:\newsroom
cd E:\newsroom
powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1
```

The script creates `.venv`, installs pinned dependencies, copies `.env.example`
to `.env` if needed, checks for Chrome and initialises the SQLite database.

## Five-minute demo

```powershell
# 1. Import the synthetic fixtures and build a complete edition.
.\.venv\Scripts\python.exe -m channel_press.cli demo --posts sample --reset

# 2. Start the newsroom UI.
.\.venv\Scripts\python.exe -m channel_press.cli serve
```

Open <http://127.0.0.1:8765>. If no owner exists yet, `demo` creates one and
prints a generated password once. Use `set-password` to choose a new one:

```powershell
.\.venv\Scripts\python.exe -m channel_press.cli set-password --username owner
```

The build writes a real edition to:

```text
output\the-channel-dispatch\2026-10-21-manual-001-v1\
  sample_issue.pdf      5-page, 11 × 17 in, searchable PDF
  print.html            print composition
  edition.html          same composition for browser review
  pages\page-01..05.png page proofs
  epaper\index.html     offline e-paper viewer
  epaper\text.html      text edition and source appendix
  manifest.json         version, page geometry, hashes and provenance
  checksums.json        SHA-256 for the bundle files
```

![Front page](docs/images/front-page.png)

Open `epaper\index.html` directly from disk to confirm the offline bundle works
without the server. The in-app viewer lives at `/editions/1/epaper/`.

## Everyday commands

| Task | Command |
| --- | --- |
| Initialise or migrate the database | `python -m channel_press.cli init-db` |
| Create the first owner | `python -m channel_press.cli setup --demo-channel` |
| Build a demo edition | `python -m channel_press.cli demo --posts sample --reset` |
| Capacity fixtures | `python -m channel_press.cli demo --posts 30` (also `70`, `150`) |
| Import an authorized export | `python -m channel_press.cli import --channel <slug> --file <export.json> --media-root <folder>` |
| Build the latest window now | `python -m channel_press.cli run-now --channel <slug>` |
| Run one worker pass | `python -m channel_press.cli worker --once` |
| Start the UI | `python -m channel_press.cli serve` |
| Check an edition | `python -m channel_press.cli check-edition --edition-id 1` |
| Back up | `python -m channel_press.cli backup` |
| Restore (app stopped) | `python -m channel_press.cli restore --archive <zip>` |
| Environment report | `python -m channel_press.cli diagnostics` |

All Python commands use `.\.venv\Scripts\python.exe` on Windows.

Two helper scripts cover the automation path:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\install-task.ps1   # worker every 15 min
powershell -ExecutionPolicy Bypass -File .\scripts\uninstall-task.ps1
```

The task calls `run-worker.ps1`, which performs one short-lived worker pass and
exits. No model or server process stays resident while the desk is idle.

## How it works

```text
authorized export / fixture
        |
   ingestion  ->  normalization, exclusion reasons, versions, media
        |
   story      ->  fragments, clusters, claims, corrections, timeline
        |
   editorial  ->  ranking, review gates, article state
        |
   ai         ->  fixture or Ollama draft -> independent evidence audit
        |
   media/graphics -> photos, frames, charts, timelines, maps
        |
   layout     ->  plan pages -> Paged.js + Chrome -> PDF -> geometry QA
        |
   publishing ->  atomic folder + manifest + checksums + e-paper bundle
        |
   scheduling ->  deterministic jobs, watermark, lease, release policy
```

Key modules live under `channel_press/`:

| Package | Responsibility |
| --- | --- |
| `ingestion` | Export formats, normalization, exclusion, media, MTProto adapter |
| `editorial` | Story clustering, claims, correction, review rules |
| `ai` | Prompt templates, fixture/Ollama providers, audit, style lint |
| `media` | Asset cache, ranking, derivatives, frame extraction |
| `graphics` | Charts, timelines, cartography |
| `layout` | Edition plan, chunking, print HTML, PDF rendering, QA |
| `publishing` | Atomic release, manifest, checksums, e-paper |
| `scheduling` | Jobs, watermarks, leases, worker |
| `web` | FastAPI routes, Jinja templates, session/CSRF/role checks |

Data stays under `data/` (SQLite database, media originals and derivatives,
secret key, logs) and `output/` (published editions). Both are gitignored.

## Publication truthfulness

- The demonstrated edition is labelled **synthetic fixture edition — not for
  publication** on its front page and in its manifest.
- The printed footer says the paper is an editorial roundup prepared from the
  owner's authorized Telegram source, not independent newsroom reporting.
- Source appendix rows identify fixture messages explicitly. No QR code is printed
  unless a public destination was verified reachable.
- `public_base_url` is empty by default, so no localhost or placeholder QR is
  emitted. The default policy is an honest printed source appendix.

## Security and privacy

- Argon2id password hashing, server-side sessions, CSRF tokens on every mutating
  form, role checks on every route, login throttling and audit logging.
- Secrets live in `data/secret.key` or environment variables; they are never
  rendered into HTML, PDFs, manifests or logs.
- Imported JSON, captions and model output are treated as untrusted data.
  Prompt-injection sentences are detected and neutralised as evidence, not
  followed.
- External URL validation blocks loopback, link-local and private-network
  targets. Source data is not sent to any online service by default.

## Measured performance on the build machine

12th-gen Intel i7, 16 GB RAM, RTX 3060 Laptop GPU, Windows 11, Chrome installed,
no Ollama model running.

| Scenario | Result |
| --- | --- |
| Idle web server | ~62 MB working set, 0 CPU seconds over a 10 s sample, 13 threads |
| 30-post fixture build | 5.95 s, 17 stories/articles, 8 pages |
| 70-post fixture build | 7.97 s, 32 stories/articles, 11 pages |
| 150-post fixture build | 10.53 s wall (~5.2 s Python CPU), 49 stories/articles, 15 pages, ~276 MB peak process-group working set |
| PDF page size | exactly 792 × 1224 pt (11 × 17 in) on every page |
| GPU | not used for fixture composition or layout |

These are measurements, not throughput promises. A live Ollama model will be
substantially slower; run `diagnostics` and a single real build before relying on
a tight cutoff-to-release window.

## Tests

```powershell
# Fast unit/integration suite
.\.venv\Scripts\python.exe -m pytest -m "not slow" -q

# Full suite, including real PDF renders for sample/30/70/150 fixtures
.\.venv\Scripts\python.exe -m pytest -q

# Lint (dev extra)
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m ruff check channel_press tests
```

Last recorded run: **59 passed** (54 fast + 5 end-to-end). The slow suite renders
actual PDFs, checks exact page geometry, verifies searchable text, inspects every
page proof for blank/overflow defects and confirms the e-paper bundle and
checksums.

## Limitations

- **Live Telegram was not tested.** No credentials or content rights were
  supplied. The isolated Telethon adapter is optional and untested against a real
  account; the fixture and rights-cleared import paths are the verified routes.
- **Ollama was not installed on the build machine.** The adapter passes schema
  validation and falls back deterministically, but a real local-model run remains
  an unverified integration. Fixture output is clearly labelled and is never
  presented as live reporting.
- **No public hosting.** E-paper bundles are local folders; the app does not
  promise a public landing page. QR output is disabled unless a reachable public
  URL is configured.
- The demo uses synthetic names, places and figures. They exist to exercise the
  pipeline and must not be quoted as news.
- Maps are locator graphics from public-domain Natural Earth layers, not
  survey-grade or operational products. See `docs/CARTOGRAPHY.md`.

## Documentation

- [User manual](USER_MANUAL.md) — day-to-day editor procedures.
- [Admin guide](ADMIN_GUIDE.md) — users, rights, secrets, models, schedules,
  backup and security.
- [Troubleshooting](TROUBLESHOOTING.md) — recovery procedures.
- [Implementation log](IMPLEMENTATION_LOG.md) — milestone history and test results.
- [QA report](docs/QA_REPORT.md) — acceptance evidence, page inspection and
  measured limits.
- [Cartography](docs/CARTOGRAPHY.md) and [decision records](docs/decisions/).

## Licence and source rights

The application code is provided for the owner's use. Bundled Natural Earth data
is public domain; fonts come from the Windows installation and are embedded for
print. Telegram content is processed only with a recorded rights declaration.
The software does not grant republication rights and must not be used to bypass
Telegram's terms or a channel owner's consent.
