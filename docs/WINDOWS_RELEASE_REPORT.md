# Windows production release report

**Contract:** `CHANNEL_PRESS_WINDOWS_PRODUCTION_RELEASE_PLAN_V2.md`, revision 2
**Evidence snapshot:** 31 October 2026
**Status:** PROD-00 / PROD-01 and targeted Qwen3.5 validation are documented.
PROD-02 renderer bundling and PROD-05 first-run/connection diagnostics have
working candidate implementations. The online per-user installer compiled and
passed an isolated install/update/uninstall smoke test on this development
machine. PROD-02 through PROD-05 and clean-install acceptance are not complete;
this is not a production-release certification.

## PROD-00 repository and machine inventory

- Baseline branch/commit: `main` at `0314a62`; worktree was clean before these
  release changes.
- Windows 11, build 26200; Python 3.12.4; NVIDIA RTX 3060 Laptop GPU, 6,144 MiB.
- The project pins direct Python requirements in `pyproject.toml`; there is no
  complete transitive lock file yet. The initial environment includes Python
  3.12.4, Ollama 0.40.10 and `qwen2.5:3b-instruct`. That legacy model and its
  `ModelRun` provenance are retained.
- No pywebview, PyInstaller, Inno Setup compiler, or packaged Chromium was
  present in the initial environment. The existing renderer can use locally
  installed Chrome/Edge and is not yet self-contained.
- Tracked-file screening found no `.env`, Telegram session, database, model
  weight, PDF, or archive artifacts. `.env`, data, output, models, sessions,
  logs and generated files are ignored. The local `.env` was not opened, copied,
  modified or reported.
- `packaging/sbom.spdx.json` was generated from this build environment by
  `scripts/build_sbom.py` and records 68 distributions. Packages without an
  unambiguous declared license remain `NOASSERTION` and require review. The
  installed PyMuPDF 1.25.1 is declared `AGPL-3.0-only`; pywebview is
  `BSD-3-Clause`.
- **Distribution license blocker:** the installed PyMuPDF metadata declares
  GNU AGPL v3. Do not redistribute that dependency in a closed-source installer
  until the owner selects and documents an applicable license, or the renderer is
  migrated to an appropriately licensed alternative. Other direct dependencies
  also require final license-text and notice review before distribution.
- The build currently uses `pyproject.toml` pins; reproducible transitive package
  locking, license bundle/notice generation, signed binaries, full offline
  installer, clean-machine verification, and the missing-WebView2 bootstrap path
  remain open.

## Qwen3.5 model pins verified

On 30 October 2026, the public Ollama registry manifests were fetched read-only.
The SHA-256 values below are over the exact registry manifest bytes; layer digests
and byte totals are stored in `channel_press/ai/model_manifest.json`. Each
manifest's license layer (digest
`sha256:50cbab8a892c5f2993b8c7351a99182507472def3b1374558308605d99b86b32`,
11,343 bytes) begins with the Apache License, Version 2.0 header.

| Profile | Fixed Ollama tag | Registry manifest SHA-256 | Download bytes | Initial hardware guidance |
|---|---|---|---:|---|
| Standard | `qwen3.5:4b-q4_K_M` | `sha256:d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04` | 3,324,173,757 | 6 GB VRAM / 8 GB RAM suggestion; measure actual inference |
| Enhanced | `qwen3.5:9b-q4_K_M` | `sha256:56671c2ab9385f9cfcb404638e32cd62d88e3501d44822208363c010179a3c90` | 6,550,825,373 | Prefer 12 GB VRAM / 16 GB RAM; explicit download confirmation |
| Compact | `qwen3.5:2b` | `sha256:0689d44085e06d165161a8a9a1731344278cfb5aade63a3c3dbdb48ab54b130a` | 2,683,396,637 | Optional hardware-dependent fallback; heavier tasks need review |

These are exact registry manifest pins, not local-inference quality claims. At
the initial inventory, no Qwen3.5 weights had been downloaded; the historical
Qwen2.5 3B model was the only one observed. Its model-run records remain
unchanged after the later authorized downloads.

## PROD-01 boundary

The desktop shell starts the existing FastAPI application on a random
`127.0.0.1` port inside a single pywebview/WebView2 window. **Continue in
background** hides the window while the API and work remain alive; **Pause safely
and exit** waits for model and worker checkpoints. A one-shot worker entrypoint
uses UTF-8 file output and no-console subprocess flags.

The PyInstaller onedir build completed for both executables:

| Executable | Size | PE subsystem |
|---|---:|---|
| `ChannelPress.exe` | 19,338,301 bytes | 2 (Windows GUI) |
| `ChannelPressWorker.exe` | 18,301,169 bytes | 2 (Windows GUI) |

Focused Python checks passed: 41 editorial/readiness/web/model/desktop/migration
tests, with one optional live test skipped by default; `compileall`; targeted
Ruff checks; and an Alembic upgrade test that
seeds a historical Qwen2.5 `ModelRun`, upgrades it, and verifies all prior fields
remain intact. The additive article reference uses `ON DELETE SET NULL`, so
clearing an unpublished article does not erase its provenance row.

The edition detail page now serves the saved working preview at
`/editions/{id}/working.pdf`. The focused web test verifies access through the
edition helper, inline PDF headers in desktop mode, and same-window navigation
for local preview/PDF/e-paper links. The release executables were rebuilt from
the project environment with PyInstaller 6.11.1; both PE headers report GUI
subsystem 2. This verifies build artifacts, not successful native window
launch, WebView2 availability or clean-install behavior.

## Live Qwen3.5 validation

After explicit owner authorization, Ollama was upgraded in place from 0.40.10
to 0.40.13 with the official signed installer. The configured user model-store
setting and historical `qwen2.5:3b-instruct` model remained available. The
Standard, Enhanced and Compact weights were downloaded through the application
model manager; each local model digest matches its pinned registry manifest.
Combined registry layer size is 12,558,395,767 bytes.

- Compact `qwen3.5:2b`: synthetic local smoke passed in 42,608 ms.
- Standard `qwen3.5:4b-q4_K_M`: synthetic smoke passed in 21,578 ms, and the
  optional live test ran `draft_story` through the editorial cleanup, factual
  audit and provenance recording path successfully.
- Enhanced `qwen3.5:9b-q4_K_M`: downloaded and digest-verified, but not loaded
  for inference. The pre-load headroom check showed 3,534 MiB free of 16,088
  MiB system RAM; the final post-download snapshot showed 1,695 MiB free.
  This profile recommends 12 GB VRAM; the machine has 6 GB.

The two smoke latencies are single-request observations, not quality,
throughput, or peak-memory certification. The Enhanced model still needs
inference validation on hardware with adequate memory.

## PROD-02 / PROD-05 Windows packaging follow-up — 31 October 2026

- Built the final candidate at `dist/release-candidate-final` with PyInstaller
  6.11.1. It contains GUI-subsystem `ChannelPress.exe` (19,343,861 bytes),
  `ChannelPressWorker.exe` (18,303,773 bytes), Playwright 1.49.1's pinned
  Chromium 1148, Paged.js, `postprocess.js`, and the e-paper template.
- A disposable-profile packaged worker fixture run imported 26 synthetic posts,
  built a 5-page, 886,430-byte tabloid PDF and e-paper output, and left the
  edition in `awaiting_review`. All five pages were rendered for inspection:
  no blank page was found, but pages 2, 4 and 5 were underfilled (manifest
  average fill 0.538; page 4 especially sparse). This remains a composition
  issue; the PDF is not visually certified for release.
- Added the initial-owner handoff to **Connections**, with first-run links for
  AI Models, Branding, authorized Sources/import, Schedules and Build.
  Connection checks stay inside the app. The packaged WebView2 window and
  worker had no visible console; the desktop process used WebView2 as its
  browser child. A successful graceful "Pause safely and exit" choice remains
  unverified.
- Readiness regression check found that preflight compared Ollama's installed
  package size with the registry layer total; Ollama adds local metadata bytes.
  Changed the check to compare the exact pinned model digest and added a test
  for both the valid digest with a +176-byte size delta and a mismatched digest.
  Rebuilt the packaged candidate and installer with this fix included.
- A read-only query of the installed Ollama tags confirmed the actual Standard
  local digest equals the reviewed pin
  `d8b0f5e9760cd1682034f292d7ef72ec46f432149be0df7574bf2d6e92e38c04`. This
  diagnostics check did not load a model; Standard's separate smoke and
  editorial-path results are recorded above.
- Compiled `dist/installer/ChannelPressSetup.exe` (288,445,030 bytes) with
  Inno Setup 6.7.3. The build downloads Microsoft's WebView2 bootstrapper and
  verifies its Authenticode signature. The first install attempt failed because
  Inno's `FindFirst` does not match the wildcard-intermediate path used by the
  original runtime detector. Replaced that check with version-directory
  enumeration; recompilation succeeded. The final build uses Inno's
  non-deprecated `x64compatible` architecture identifier.
- On the current Windows 11 host, which already had WebView2 Runtime, the
  corrected installer passed a silent per-user install to an isolated `.build`
  target, a second in-place update, and uninstall. The app executable and
  uninstall registration were removed, while a separate test data sentinel
  remained intact. Subsequent rebuilt installers, including the final refreshed
  application bundle, also installed and uninstalled successfully; the final
  setup log confirms that the WebView2 bootstrapper was skipped because the
  existing runtime was detected. The WebView2-missing bootstrap path,
  interactive existing install confirmation, clean Windows 10/11 install,
  repair behavior and
  rollback were not verified.
- **Focused checks:** 30 tests passed across
  `tests/test_browser_runtime.py`, `tests/test_connections.py`,
  `tests/test_desktop.py`, `tests/test_preflight.py` and `tests/test_web.py`;
  Ruff passed for the changed Python files; both PowerShell build scripts
  parsed; the refreshed installer compiled and passed final install/uninstall.
  The full test suite was not run.
- **Still incomplete:** an offline installer, app-owned Ollama installation
  and supervision, model-weight packaging, clean-machine WebView2 flow,
  interactive update confirmation, code signing, license/notice review,
  Enhanced inference on sufficient hardware, real Telegram credentials and
  end-to-end publication. The AGPL PyMuPDF distribution license needs an
  owner-approved licensing choice or renderer replacement before
  closed-source redistribution. Do not describe this prototype as
  production-ready.
