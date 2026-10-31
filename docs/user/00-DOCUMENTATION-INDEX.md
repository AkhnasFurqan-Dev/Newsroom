# Newsroom — Documentation Index

**Product:** Newsroom 1.0.0 for Windows  
**Build verified:** packaged `Newsroom.exe` and `NewsroomSetup.exe` of 31 October 2026  
**Documentation version:** 1.0, 31 October 2026  
**Publisher:** Newsroom documentation set

This is the navigation hub for the Newsroom user documentation. The manuals describe the installed Windows product, not its source-code development. Where a behaviour could not be verified, it is marked as unverified in the manual and recorded in the internal verification notes.

---

## 1. Which manual do I need?

| If you want to… | Read |
|---|---|
| Install Newsroom and produce a first edition | **Quick Start** |
| Look up a screen, field or button | **User Manual** |
| Decide what to publish, review evidence or release an issue | **Editorial & Publishing Guide** |
| Back up, update, restore or uninstall | **Installation & Maintenance** |
| Fix a problem | **Troubleshooting & FAQ** |

### Reader paths

- **New installation:** Quick Start → Installation & Maintenance when needed → User Manual.
- **Daily issue production:** Editorial & Publishing Guide → User Manual for controls.
- **Something broke:** Troubleshooting & FAQ → Installation & Maintenance for safe recovery.

---

## 2. The document set

| Document | What it covers | Markdown | PDF |
|---|---|---|---|
| **Documentation Index** | This map, versions and scope | [00-DOCUMENTATION-INDEX.md](00-DOCUMENTATION-INDEX.md) | [PDF](pdf/Newsroom-Documentation-Index.pdf) |
| **Quick Start** | Install, first run, AI readiness, source, branding, first build, preview, release and e-paper | [01-QUICK-START.md](01-QUICK-START.md) | [PDF](pdf/Newsroom-Quick-Start.pdf) |
| **User Manual** | Every verified screen and control, from Dashboard to Audit | [02-USER-MANUAL.md](02-USER-MANUAL.md) | [PDF](pdf/Newsroom-User-Manual.pdf) |
| **Editorial & Publishing Guide** | Lifecycle, evidence, AI limits, human review, media, layout, release, corrections, QR and e-paper | [03-EDITORIAL-AND-PUBLISHING-GUIDE.md](03-EDITORIAL-AND-PUBLISHING-GUIDE.md) | [PDF](pdf/Newsroom-Editorial-and-Publishing-Guide.pdf) |
| **Installation & Maintenance** | Installer, components, locations, update, backup, restore, uninstall, disk, background operation, network and privacy | [04-INSTALLATION-AND-MAINTENANCE.md](04-INSTALLATION-AND-MAINTENANCE.md) | [PDF](pdf/Newsroom-Installation-and-Maintenance.pdf) |
| **Troubleshooting & FAQ** | Symptom → cause → safe steps → escalation | [05-TROUBLESHOOTING-AND-FAQ.md](05-TROUBLESHOOTING-AND-FAQ.md) | [PDF](pdf/Newsroom-Troubleshooting-and-FAQ.pdf) |
| **Combined handbook** | All six documents in one bound volume | — | [PDF](pdf/Newsroom-User-Handbook.pdf) |
| **Documentation README** | File map, screenshot credits and regeneration notes | [README.md](README.md) | — |

The PDFs are generated from the Markdown files. If a PDF and its Markdown disagree, the Markdown is the source of truth.

---

## 3. Verified scope

The following were exercised or directly inspected for this documentation:

- the packaged `Newsroom.exe` running against a disposable demonstration profile;
- first-run setup, sign-in, Dashboard, Sources, Stories, Articles, Build, Editions, Jobs, Schedules, AI Models, Connections, Media cache, Branding, Appearance, Users, Audit and Diagnostics screens;
- a real fixture build that produced job #1 and edition #2 in **Awaiting review**;
- a previously published five-page synthetic edition, its **Published PDF** and its **E-paper**;
- the edition workspace, article editor, evidence record, review items and release controls;
- the installer configuration, install paths, upgrade prompt and legacy shortcut cleanup;
- the Windows release report and the packaged file inventory.

### 3.1 Not verified

These are described only as far as the interface or configuration supports them, and are marked unverified where relevant:

- live Telegram login and retrieval (API id/hash were not configured);
- a clean-machine WebView2 bootstrap;
- unattended scheduled builds while the application is closed;
- AI model download, inference and the local model test end to end;
- automatic external research providers;
- font import and print-proof rendering end to end;
- media cache deletion;
- backup restore and a full update/uninstall cycle;
- the complete multi-user permission matrix;
- public QR targets and public source-list hosting;
- PDF composition certification beyond visual inspection, text selection and page geometry.

The internal record is in [VERIFICATION-NOTES.md](VERIFICATION-NOTES.md).

---

## 4. Conventions used in these manuals

- **Bold** marks a UI label, button, field or exact status, for example **Queue build** or **Awaiting review**.
- `Monospace` marks a path, file name, model name or literal value.
- **Figure N.** captions describe real screenshots from a disposable demonstration profile.
- All demonstration content is synthetic. The app labels it **Synthetic demonstration edition**.
- The Newsroom name and logo identify the software and these manuals. They do not belong inside a customer newspaper's masthead.

---

## 5. Product identity

| Item | Value |
|---|---|
| Product name | **Newsroom** |
| Version | 1.0.0 |
| Platform | Windows 10 or Windows 11, 64-bit |
| Installer | `NewsroomSetup.exe` |
| Default install folder | `%LOCALAPPDATA%\Programs\Newsroom` |
| Default data folder | `%LOCALAPPDATA%\ChannelPress` |
| Published output | `%LOCALAPPDATA%\ChannelPress\output` |
| Logs | `%LOCALAPPDATA%\ChannelPress\logs` |
| Official palette | Ink `#20272B`, Paper `#F3F0E8`, Oxblood `#913E36`, Slate `#758089` |

Newsroom was formerly distributed as **Channel Press**. The data folder name and some internal identifiers retain the old name for compatibility; they are not the current product name.

---

## 6. Getting help

If a procedure does not match your screen:

1. Note the exact screen name and the label you see.
2. Note the product version from **Diagnostics → About Newsroom**.
3. If a job failed, note its number, stage and error text.
4. Read the matching section of **Troubleshooting & FAQ**.
5. Before sending logs, remove personal data, phone numbers, API credentials, session contents and any unpublished reporting.

Do not delete the database, the data folder or the Telegram session file as a first response. Most problems are recoverable without data loss.
