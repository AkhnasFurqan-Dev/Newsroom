# Internal Verification Notes

**Status:** internal record, not a user manual  
**Product:** Newsroom 1.0.0 for Windows  
**Documentation version:** 1.0, 31 October 2026  
**Prepared by:** documentation pass against the packaged application and installer

This file records what was verified, how it was verified, what was changed and what remains unverified. It is deliberately excluded from the combined user handbook.

---

## 1. Artefacts inspected

| Artefact | Size | SHA-256 |
|---|---:|---|
| `dist\installer\NewsroomSetup.exe` | 288,395,624 bytes | `8A62D13BB488719FDD25345C27E83E2461F88F49B0D40624C7602C5CEF32058C` |
| `dist\release-candidate\Newsroom\Newsroom.exe` | 19,309,645 bytes | `563291C6ACC7817F4DC370C1CD6B25AD8CAEE17AB9E323B593B35785AEC7064B` |

Other sources used:

- `packaging\Newsroom.iss` — per-user install, upgrade prompt, WebView2 bootstrap, legacy shortcut cleanup.
- `docs\WINDOWS_RELEASE_REPORT.md` — packaging, installer and rebrand evidence.
- `README.md` and the application templates — exact labels and status strings.
- The running packaged application against a disposable profile.

Version reported by the product: **1.0.0**.

---

## 2. Test environment

- The packaged `Newsroom.exe` was launched against a disposable profile at `E:\newsroom - Copy\.build\doc-demo`.
- The live `%LOCALAPPDATA%\ChannelPress` data was not modified.
- The disposable profile was populated with the application's synthetic fixture demonstration.
- The demo database is `E:\newsroom - Copy\.build\doc-demo\data\channel_press.db`.
- The UI was inspected through the application's local service. The session used local port `62489`; no external service was exposed.
- Ollama was started from an existing local installation for inspection only. No model was downloaded and no model setting was changed.
- The only profile edit was the owner display name, changed from `Demo Editor` to `Newsroom Editor` for legible screenshots. No application code was changed.

The demo owner password is not recorded in this repository or in any manual.

---

## 3. What was verified

### 3.1 Application shell and first run

- Single-window desktop shell; no console window.
- First-run **Set up Newsroom** page and sign-in page.
- Header newspaper selector, **Switch**, date/time zone, display name and role, full navigation and footer.
- Navigation labels: **Dashboard**, **Sources**, **Stories**, **Articles**, **Build**, **Editions**, **Schedules**, **Jobs**, **AI Models**, **Connections**, **Media cache**, **Branding**, **Appearance**, **Users**, **Audit**, **Diagnostics**.
- Close dialog with **Continue in background**, **Pause safely and exit** and **Keep open**.

### 3.2 Source and import path

- Source desk with fixture channel, rights status, counts and **Open**.
- **Register an authorized channel** fields and the rights declaration behaviour.
- Rights declaration table and **Revoke**.
- Source detail: reporting window, access note, collection windows, import form, imports table with SHA-256, schedules and recent stories.
- JSON import limits (40 MB), the optional referenced-media selection and the untrusted-data warning.
- Per-post decisions and withdrawal reasons.
- Telegram connection page: credentials **Missing**, login controls, cutoff window and history.
- Live Telegram credentials were not configured. Live login/retrieval was therefore not tested.

### 3.3 Story and article workflow

- Story list filters and columns.
- Story detail: claim ledger, source fragments, research context, chronology and story state.
- Article list filters and columns.
- Article editor: headline, deck, standfirst, body, byline, section, genre, locks, save, undo and re-run audit.
- Versions, sources used, review items, audit, style lint, independent quality pass, status decision, media and external context.
- **Approve item** / **Reject item**, article status values and **Publication held** notice.
- The distinction between the article list status filter labels and the status menu labels was checked against the templates.

### 3.4 Build, jobs and editions

- **Build** fields, cutoff rule (`>= start`, `< end`) and duplicate-window reuse.
- A real fixture build through **Queue build** produced job **#1** and edition **#2** in **Awaiting review**.
- Jobs list and job detail: status, stage, progress, steps, result, actions and timing.
- Detached worker behaviour as described by the application.
- Editions list and edition workspace.
- Edition #2 showed **Release held** blockers for elevated risk, unverified claims and quality items.
- Published edition #1: status **PUBLISHED**, **Immutable release**, 5 pages, buttons **Open preview PDF**, **Published PDF** and **E-paper**.
- **Open working version 2** and the immutable-version explanation were verified in the interface and template.
- Edition quality measurements: page count, warnings, average/lowest page fill, underfull pages, PDF bytes and PDF SHA.
- The published PDF was verified as 5 pages, 792 × 1224 pt (11 × 17 in), with selectable text.
- The e-paper opened with page navigation, a text edition and a source appendix.

### 3.5 Configuration and maintenance screens

- Branding: publication profile, paper size, source-code policy, evidence URL confirmation, research policy, section order, newspapers, sources, editors and map conventions.
- Appearance: print checks, palette presets, colour roles, font roles, type metrics, section accents, save as new revision, render print proof, reset, imported font library and revision history.
- Schedules: cutoff vs release, enable, days, review policy, save, **Run the latest cutoff now**, **Enqueue missed cutoffs** and add schedule.
- AI Models: three profiles, sizes, licences, pinned digests, hardware guidance, approval checkboxes, **Verify and download**, **Select profile** and **Test local model**.
- Connections and Diagnostics: component rows, status meanings, configuration values and data/privacy notes.
- Media cache: dry run, two clear controls and the protected set.
- Users: roles, create user, temporary password, disable and reset password.
- Account: change password, 12-hour session note and sign out.
- Audit trail columns and example actions.

### 3.6 Installer and package

- Per-user install to `%LOCALAPPDATA%\Programs\Newsroom`; `PrivilegesRequired=lowest`; 64-bit compatible.
- Start Menu shortcut always; desktop shortcut optional.
- WebView2 bootstrapper extracted and run silently only when the runtime is missing.
- Existing-installation prompt explicitly states that data, Telegram sessions, settings and editions are stored separately and will not be removed.
- Legacy `ChannelPress.exe`, `ChannelPressWorker.exe` and old shortcuts are deleted on install.
- Data folder remains `%LOCALAPPDATA%\ChannelPress`; output and logs subfolders confirmed from the running app.
- Installed size about 831 MB, from the release report.
- The uninstaller removes program files; the data folder is outside the install folder and is preserved by design.

---

## 4. What was not verified

These items are described only as far as the interface or configuration supports them. They must not be presented as tested promises.

| Item | Reason / status |
|---|---|
| Live Telegram login and retrieval | API id/hash not configured on the tested installation; no live account was used. |
| Clean-machine WebView2 bootstrap | WebView2 was already present; the bootstrap path was inspected in the installer, not run on a clean machine. |
| Unattended scheduled builds while the app is closed | The installer creates no Windows task; automatic closed-app execution was not tested and should be treated as unsupported. |
| AI model download, inference and local model test | No model was downloaded; the demo used the deterministic fixture composer. |
| Enhanced 9B model on adequate hardware | Not tested; hardware guidance is the vendor's suggestion. |
| Automatic external research providers | Not configured or exercised; only the editor-triggered publisher-feed state was observed. |
| Font import and print-proof rendering | Page and controls inspected; no font was imported and no proof was rendered end to end. |
| Media cache deletion | Dry run inspected; the destructive actions were not run. |
| Backup and restore | Procedure derived from confirmed paths and SQLite WAL behaviour; a full restore was not exercised. |
| Full update, repair and uninstall cycle | Installer logic and prompt inspected; a clean-machine cycle was not repeated. |
| Complete multi-user permission matrix | Roles are exposed; each role's full boundary was not tested. |
| Public QR targets and hosted source list | Not configured; the app correctly reported truthful source appendix mode. |
| PDF composition certification | Visual inspection, text selection and page geometry passed; no prepress certification was performed. |
| Distribution to print or web | Out of scope; Newsroom has no upload. |

Additional observations:

- The synthetic published edition reported underfull pages 2, 4 and 5 (average fill 58.0%, lowest 11.0%). This is reported by the app, not a proof failure, but it means the synthetic edition is not a model of a full commercial paper.
- The in-app Diagnostics, Media cache and Telegram pages contain some legacy developer wording and paths. The manuals do not present those as ordinary user procedures.
- The installer is not code-signed, so Windows SmartScreen may warn.

---

## 5. Screenshot handling

- Screenshots were captured from the running packaged application at the default desktop window size.
- The disposable profile contains only synthetic fixture content.
- No phone number, API hash, login code, session token, password or Windows user name appears in the selected images.
- The demo owner display name is `Newsroom Editor`. The newspaper is `The Channel Dispatch`; its content is invented.
- Images were cropped only to remove unused desktop area. UI elements were not rearranged or fabricated.
- The published newspaper page uses the newspaper's own masthead. The Newsroom logo does not appear inside it.
- `job-progress.png` was captured but excluded because its elapsed time dates the manual and it adds no unique information.

Selected images and their manuals:

| Image | Used in |
|---|---|
| `sign-in.png` | Quick Start |
| `dashboard.png` | Quick Start, User Manual |
| `source-desk.png` | Quick Start, User Manual |
| `story-evidence.png` | User Manual |
| `article-editor.png` | User Manual |
| `build-edition.png` | Quick Start, User Manual |
| `jobs-after-build.png` | Quick Start, User Manual |
| `edition-review-held.png` | Quick Start, User Manual |
| `edition-workspace.png` | Quick Start, User Manual |
| `epaper-viewer.png` | Quick Start, Editorial Guide |
| `published-front-page.png` | Quick Start, Editorial Guide |
| `branding.png` | User Manual |
| `appearance.png` | User Manual |
| `schedules.png` | User Manual |
| `ai-models.png` | Quick Start, User Manual |
| `close-newsroom.png` | Quick Start |

---

## 6. Link and file checks

Checks performed after the manuals were written:

- all six required Markdown files exist in `docs/user/`;
- all relative Markdown links resolve to an existing file;
- all image references resolve to an existing file;
- no absolute local paths or personal paths are used as document links;
- no credentials or session material appear in the Markdown or images;
- each PDF exists, opens, has selectable text, page numbers and a table of contents;
- the combined handbook contains all six documents and its bookmarks resolve.

The exact commands and results are recorded in section 8 after generation.

---

## 7. Changes made outside `docs/user/`

- Created the disposable demo profile under `.build/doc-demo`.
- Created UI capture text and screenshots under `.build/ui-capture`.
- Created the PDF generation script and dependency folder under `.build/`.
- No application source, packaging file, installer, model, Telegram session or live data was changed.
- The working tree's only documentation change is the new `docs/user/` tree.

---

## 8. Generation and verification record

### 8.1 Renderer

- Python 3.12.4.
- `markdown` 3.11.2.
- Playwright 1.64.1 with bundled Chromium.
- PyMuPDF 2.0 (MuPDF 1.28.5).
- Source of truth: the eight Markdown files in this folder.
- PDFs: A4 portrait, 595.0 × 841.9 pt, with embedded images, selectable text, running footers and page numbers.
- The cover is rendered separately at full A4 bleed, then merged with the body.
- Chromium's generated outline is cleaned of duplicated heading labels and extended with the cover and contents entries.
- Printed contents entries are made clickable with PyMuPDF link annotations because Chromium's print path drops internal anchors.

### 8.2 Output inventory

| PDF | Pages | Internal links | Bookmarks |
|---|---:|---:|---:|
| `Newsroom-Documentation-Index.pdf` | 5 | 6 | 10 |
| `Newsroom-Quick-Start.pdf` | 18 | 12 | 27 |
| `Newsroom-User-Manual.pdf` | 32 | 20 | 93 |
| `Newsroom-Editorial-and-Publishing-Guide.pdf` | 17 | 13 | 60 |
| `Newsroom-Installation-and-Maintenance.pdf` | 12 | 13 | 40 |
| `Newsroom-Troubleshooting-and-FAQ.pdf` | 18 | 17 | 78 |
| `Newsroom-User-Handbook.pdf` | 96 | 87 | 304 |

### 8.3 Visual inspection

- **Covers:** inspected for the approved horizontal wordmark, correct geometry, clean Paper field, Oxblood rule and readable metadata. No stretched or recoloured logo.
- **Title and contents pages:** inspected. Contents are one page per standalone manual, with the metadata block on the same page. The handbook contents span three pages and list every document and its main sections.
- **Procedural pages:** inspected the installer, first-run and build pages. Ordered lists, nested bullets, code spans and tables render correctly. No raw Markdown or literal `**` remains in the PDFs.
- **Screenshot-heavy pages:** inspected the Dashboard, AI Models, Jobs, Build, article editor, edition workspace and e-paper pages. Figures are sharp at 100%, captions are visible, and no image is clipped or missing.
- **Tables:** inspected the symptom table, component tables and field tables. Header rows repeat across page breaks; no column is cut off.
- **End pages:** inspected the last page of each manual and the handbook. No orphan heading or blank final page.
- **Known layout note:** in the User Manual, section 6 deliberately starts a new page so its Build screenshot stays with the heading. The preceding page ends at a section boundary with white space.
- **Known content note:** the Jobs screenshot shows a mostly empty page because the fixture job list is short. This is the real UI, not a capture error.

### 8.4 Automated checks

- All six required Markdown files plus the README and verification notes exist.
- Every relative Markdown link resolves to an existing file.
- Every image reference resolves; all 16 PNGs in `images/` are used.
- No CJK characters, TODOs or missing-image warnings remain.
- No password, API hash, login code, session token or Windows user name appears in the manuals or selected images.
- All PDFs open, contain selectable text and have no replacement glyphs.
- All bookmarks point to valid pages.
- Contents links point to the matching section pages; sampled destinations were checked against the outline.
- File sizes: 207 KB to 4.9 MB; the handbook is 4.9 MB.

### 8.5 Repository state

- Documentation-only change under `docs/user/`.
- No application source, packaging file, installer, model, Telegram session or live data was changed.
- The `.build/` work folders are not part of the documentation commit.
- Documentation commit: a single commit on the current branch; the hash is reported in the completion summary supplied with the handoff.
