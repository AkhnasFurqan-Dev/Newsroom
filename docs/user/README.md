# Newsroom User Documentation

This folder contains the user-facing documentation for **Newsroom 1.0.0 for Windows**. The Markdown files are the source of truth. The PDFs are generated from them with the approved Newsroom visual identity.

All screenshots come from a disposable demonstration profile and contain synthetic test data. No live newspaper, Telegram session or personal account is shown.

## Start here

- **[Documentation Index](00-DOCUMENTATION-INDEX.md)** — which manual to read and what was verified.
- **[Quick Start](01-QUICK-START.md)** — install, first run and a first edition preview.
- **[User Manual](02-USER-MANUAL.md)** — the full control reference.
- **[Editorial & Publishing Guide](03-EDITORIAL-AND-PUBLISHING-GUIDE.md)** — review, release and corrections.
- **[Installation & Maintenance](04-INSTALLATION-AND-MAINTENANCE.md)** — install, update, backup, restore and privacy.
- **[Troubleshooting & FAQ](05-TROUBLESHOOTING-AND-FAQ.md)** — symptoms and safe recovery.

## PDFs

| File | Contents |
|---|---|
| [`pdf/Newsroom-Documentation-Index.pdf`](pdf/Newsroom-Documentation-Index.pdf) | Navigation hub and verified scope |
| [`pdf/Newsroom-Quick-Start.pdf`](pdf/Newsroom-Quick-Start.pdf) | Installation to first edition |
| [`pdf/Newsroom-User-Manual.pdf`](pdf/Newsroom-User-Manual.pdf) | Complete control reference |
| [`pdf/Newsroom-Editorial-and-Publishing-Guide.pdf`](pdf/Newsroom-Editorial-and-Publishing-Guide.pdf) | Editorial lifecycle and release |
| [`pdf/Newsroom-Installation-and-Maintenance.pdf`](pdf/Newsroom-Installation-and-Maintenance.pdf) | Owner and maintenance guide |
| [`pdf/Newsroom-Troubleshooting-and-FAQ.pdf`](pdf/Newsroom-Troubleshooting-and-FAQ.pdf) | Recovery and FAQ |
| [`pdf/Newsroom-User-Handbook.pdf`](pdf/Newsroom-User-Handbook.pdf) | All six documents in one volume |

## File map

```text
docs/user/
├── 00-DOCUMENTATION-INDEX.md
├── 01-QUICK-START.md
├── 02-USER-MANUAL.md
├── 03-EDITORIAL-AND-PUBLISHING-GUIDE.md
├── 04-INSTALLATION-AND-MAINTENANCE.md
├── 05-TROUBLESHOOTING-AND-FAQ.md
├── README.md
├── VERIFICATION-NOTES.md
├── images/
│   ├── ai-models.png
│   ├── appearance.png
│   ├── article-editor.png
│   ├── branding.png
│   ├── build-edition.png
│   ├── close-newsroom.png
│   ├── dashboard.png
│   ├── edition-review-held.png
│   ├── edition-workspace.png
│   ├── epaper-viewer.png
│   ├── jobs-after-build.png
│   ├── newsroom-logo-horizontal.svg
│   ├── newsroom-symbol.svg
│   ├── published-front-page.png
│   ├── schedules.png
│   ├── sign-in.png
│   ├── source-desk.png
│   └── story-evidence.png
└── pdf/
    └── (generated PDFs)
```

An early `job-progress.png` capture was excluded because its elapsed time dated the manual and it added no unique information.

## Screenshot provenance

| Image | Screen | Notes |
|---|---|---|
| `sign-in.png` | Sign-in | Disposable demo account; no credentials visible |
| `dashboard.png` | Dashboard | Synthetic fixture newspaper and review items |
| `source-desk.png` | Sources | Fixture channel; rights record uses repository-owner text |
| `story-evidence.png` | Story detail | Synthetic claims and source fragments |
| `article-editor.png` | Article editor | Synthetic opinion article |
| `build-edition.png` | Build | Fixture window and synthetic edition name |
| `jobs-after-build.png` | Jobs | Verified fixture build job |
| `edition-review-held.png` | Edition workspace | Verified held edition and blockers |
| `edition-workspace.png` | Edition workspace | Published synthetic edition #1 |
| `epaper-viewer.png` | E-paper | Published synthetic edition |
| `published-front-page.png` | Published PDF | Synthetic newspaper's own masthead |
| `branding.png` | Branding & edition policy | Synthetic publication profile |
| `appearance.png` | Appearance | Default theme |
| `schedules.png` | Schedules | Disabled fixture schedule |
| `ai-models.png` | AI Models | Models not downloaded in the demo profile |
| `close-newsroom.png` | Close dialog | Background/pause choices |

The two SVG files are the approved Newsroom horizontal logo and symbol, copied from `assets/brand/newsroom/`. They are used on PDF covers and section furniture, never inside a newspaper masthead.

## Brand rules used in the PDFs

- Ink `#20272B` and Paper `#F3F0E8` carry most of the layout.
- Oxblood `#913E36` is used sparingly for accents and section identifiers.
- Slate `#758089` is used for secondary text and rules.
- The horizontal wordmark is placed on the cover as artwork. It is not retyped.
- No gradients, neon, 3D, glass or generic AI imagery.
- Body text uses a system serif/sans pairing; no unlicensed font is embedded.

## Regenerating the PDFs

The PDFs were generated with Python, the `markdown` package, Playwright Chromium and PyMuPDF. From the repository root:

```powershell
# One-time tool installation into a local build folder
python -m pip install --target .build/pdfdeps markdown

# Generate HTML from Markdown, print to PDF with Playwright Chromium,
# then add page numbers and bookmarks with PyMuPDF.
python .build/render-docs.py
```

The rendering script lives in the build folder and is not part of the shipped application. It does not modify application data.

## Editorial and privacy notes

- Demonstration content is synthetic and labelled as such. It must never be presented as real reporting.
- No Telegram API hash, login code, session token, password or Windows user name appears in the manuals or images.
- The demo profile's owner display name was changed from `Demo Editor` to `Newsroom Editor` for legibility. No application code or live data was changed.
- The published synthetic edition uses its own masthead. The Newsroom logo is not printed inside it.

## Version

Documentation version 1.0, 31 October 2026. Verified against Newsroom 1.0.0 (packaged build of 31 October 2026).

See [VERIFICATION-NOTES.md](VERIFICATION-NOTES.md) for the internal checklist and the list of unverified behaviours.
