# Windows production release report

**Contract:** `CHANNEL_PRESS_WINDOWS_PRODUCTION_RELEASE_PLAN_V2.md`, revision 2
**Evidence snapshot:** 31 October 2026
**Status:** PROD-00 inventory documented; PROD-01 desktop prototype and
targeted Qwen3.5 validation completed. This is not an installer or clean-install
certification.

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
  locking, license bundle/notice generation, signed binaries, installer,
  WebView2 prerequisite handling, and clean-machine verification remain open.

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
  for inference. At the time of the check only 1,695 MiB of 16,088 MiB system
  RAM was free, and this profile recommends 12 GB VRAM; the machine has 6 GB.

The two smoke latencies are single-request observations, not quality,
throughput, or peak-memory certification. The Enhanced model still needs
inference validation on hardware with adequate memory.

Not verified: a packaged window launch/restore on this machine, the Windows 10
clean-machine workflow, WebView2 prerequisite handling, a setup/repair/uninstall
installer, bundled Playwright Chromium/Paged.js, silent Ollama runtime
supervision, Enhanced inference, and real-channel end-to-end behavior. These are
release blockers; do not describe this prototype as production-ready. The AGPL
PyMuPDF distribution license needs an owner-approved licensing choice or
renderer replacement before closed-source redistribution.
