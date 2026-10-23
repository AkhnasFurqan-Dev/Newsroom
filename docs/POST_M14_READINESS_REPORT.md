# Post-M14 Readiness Report

**Project:** Channel Press, `E:\newsroom`
**Report date:** 23 October 2026
**Code baseline:** `a81a739` (`fix(layout): fit two-up section rows on one tabloid page`), on top of the L00–L06 history ending at `14b2e48` and the M00–M14 history ending at `8fde22e`.
**Machine:** Windows 11, 12th-gen Intel i7, 16 GB RAM, NVIDIA RTX 3060 Laptop GPU with **6144 MiB** VRAM, Google Chrome installed, Python 3.12.4 in `.venv`.

This report separates what was actually executed from what still depends on the owner. A fixture pass is not evidence of a live Telegram pass, and a local-model pass over synthetic posts is not evidence of live reporting.

## 1. Status summary

| Track | Status | Meaning |
| --- | --- | --- |
| `fixture` | **PASS** | Synthetic posts → stories → articles → 11 × 17 in PDF → e-paper, with geometry and visual page checks. |
| `local AI` | **PASS (30 posts)** | Real Ollama `qwen2.5:3b-instruct` drafted 17 articles from synthetic posts; no fixture fallback; 8 pages inspected. 70/150-post real-model runs were not performed. |
| `Telegram live` | **BLOCKED — owner setup required** | Login, handle resolution, windowed retrieval and resume are implemented and fixture-tested, but no API values, user login or rights declaration were supplied. |
| `supervised end-to-end` | **BLOCKED — depends on live** | Cannot collect a real authorized window or publish a real edition without the live source. |
| `automatic scheduled publication` | **NOT ENABLED** | The Windows task and worker were verified, but auto-release is off and no public release was approved. |

No live functionality is claimed as verified in this report.

## 2. What changed in L00–L09

| Phase | Commit | Scope |
| --- | --- | --- |
| L00 | baseline `8fde22e` | Audit and `docs/POST_M14_CHECKLIST.md`. |
| L01 | `4cab3f6` | `channel_press/config.py`, `channel_press/preflight.py`, `.env` placeholders, `LIVE_ENV_PLACEHOLDERS.env.template`, preflight tests. |
| L02 | `29dc7a4` | `channel_press/telegram/` transport and service, Telegram connection page, source/diagnostics links, live tests. |
| L03 | `4691145` | `channel_press/collection/`, `edition_windows` + `retrieval_checkpoints` migration `b7c1f2d4e901`, scheduling integration, build-page collection, window tests. |
| L04 | `d02167c` | Hardened Ollama provider, prompt v3, real-model proof test. |
| L05 | `49991fe`, `a81a739` | Lazy live media, two-up section rows, caption-safe locator maps, draft cleanup, page-proof tests; row spacing tightened so a four-story national section fits one page. |
| L06 | `14b2e48` | UTF-8 worker log path verified with a real Task Scheduler run. |
| L07 | covered by L01/L02 tests | Secret redaction, CSRF, session and role checks re-verified. |
| L08 | docs commit after this report | README, USER_MANUAL, ADMIN_GUIDE, TROUBLESHOOTING, this report, checklist and log. |
| L09 | **blocked** | Owner secrets, interactive login, rights consent and publication approval. |

## 3. Commands and observed results

```powershell
.\.venv\Scripts\ruff.exe check channel_press tests
# All checks passed! exit=0

.\.venv\Scripts\python.exe -m pytest --collect-only -q -p no:warnings
# 125 tests collected

.\.venv\Scripts\python.exe -m pytest -q -p no:warnings
# exit=0 (full suite, including slow PDF renders)

.\.venv\Scripts\python.exe -m channel_press.cli diagnostics
# Revision b7c1f2d4e901; Chrome present; Ollama reachable; OpenCV 4.10.0;
# FFmpeg absent (OpenCV fallback); public URL not configured
```

Database after migration (real `data/channel_press.db`, not reset):

| Item | Count / value |
| --- | --- |
| Alembic revision | `b7c1f2d4e901` |
| Users | 1 |
| Channels | 1 |
| Source posts | 26 |
| Stories | 15 |
| Articles | 15 |
| Editions | 1 |
| Rights grants | 1 |
| Edition windows | 0 (no live window collected yet) |

The migration added tables and a `window_key` column; existing rows were preserved.

## 4. Test matrix

| Area | Evidence | Outcome |
| --- | --- | --- |
| Cutoff boundaries, DST policy, catch-up | `tests/test_cutoff_windows.py` | Pass |
| Collection staging, resume, duplicate windows, context backfill, watermark | `tests/test_collection.py` | Pass |
| Preflight and secret redaction | `tests/test_preflight.py` | Pass |
| Telegram transport, handle parsing, login state machine, window fetch | `tests/test_telegram_live.py` (fixture-backed) | Pass; live login skipped |
| Ollama provider schema, fencing, exact model match | `tests/test_ollama_live.py` | Pass |
| Real 30-post local-model edition | `tests/test_ollama_live.py::test_real_local_model_builds_a_thirty_post_edition` | Pass, 8 pages |
| Lazy media download and map attribution | `tests/test_live_media.py`, `tests/test_media_graphics.py` | Pass |
| Draft cleanup and standfirst/date handling | `tests/test_editorial.py` | Pass |
| PDF geometry, e-paper, checksums | `tests/test_edition_e2e.py` | Pass |
| Security, CSRF, sessions, roles | `tests/test_security_time.py` | Pass |

## 5. Local AI proof

- Provider: Ollama 0.40.9 at `127.0.0.1:11434`, model `qwen2.5:3b-instruct` (1841 MB).
- Input: synthetic 30-post fixture set. This is **not** Telegram live content.
- Output: 17 stories, 17 articles, 8 tabloid pages, provider `ollama`, no fixture fallbacks.
- Time: 57–62 s wall time. Peak VRAM: **2213 MiB** of 6144 MiB.
- Manifest: `quality.warnings == []`, `office_print_proof == true`, every page 11 × 17 in, minimum font 7.0 pt (7.1 pt on the appendix), no blank pages.
- Edition status after the run: `awaiting_review`. It was **not** auto-published.
- Artifact:

```text
C:\Users\Akhnas Furqa\AppData\Local\Temp\pytest-of-Akhnas Furqa\pytest-55\
  test_real_local_model_builds_a0\the-channel-dispatch\2026-10-22-manual-001-v1\
    edition.pdf / sample_issue.pdf   8 pages
    pages\page-01.png … page-08.png  page proofs
    manifest.json, checksums.json, print.html, edition.html, epaper\
```

All eight page proofs were rendered and inspected. No clipping, overlap, blank
page, broken QR or sub-7 pt body text was found. The national section packs all
four stories on page 2 after the two-up row fix. Sparse sections (international
p. 4, defence p. 5, opinion p. 7, appendix p. 8) carry section-end white space;
they are not blank pages.

**Review-gate observation:** 14 of the 17 articles were held as `needs_review`
because the model introduced contextual proper nouns that the source did not
state (for example a country name for a city). The deterministic audit caught
this; it is the intended safety behaviour. An editor must clear or correct those
flags before release. Prompt `news-article-v3`, which forbids adding unnamed
countries, provinces or regions, was the version used for this run; the gate
still fired on the remaining additions, so review capacity remains a real
operational constraint.

## 6. Scheduling proof

- `scripts/install-task.ps1` registered a per-user task (`ChannelPressWorkerTest`)
  that runs `scripts/run-worker.ps1` every 15 minutes.
- A manual `Start-ScheduledTask` run returned `LastTaskResult 0`.
- The daily log `data/logs/worker-YYYYMMDD.log` showed a clean pass
  (`enqueued=0 processed=0 … exit 0`); stderr is routed to
  `worker-err-YYYYMMDD.log`.
- The test task was unregistered afterwards. The production task name remains
  `ChannelPressWorker` and is **not** installed by default.
- No process or model stays resident between runs.

## 7. Security and secrets

- `.env` is untracked and contains empty placeholders only; the owner fills it.
- `data/telegram.session`, `data/secret.key`, databases, media and output are
  gitignored.
- Preflight and diagnostics report configured / missing / session-present only.
  Tests assert that API values and session strings do not appear in rendered HTML
  or logs.
- Login codes and two-step passwords are held in memory for the login window and
  never persisted.
- Argon2id, CSRF, session expiry, login throttling, role checks and audit logging
  remain in place.

## 8. Exact owner-only steps remaining

1. Edit `E:\newsroom\.env` and set:
   - `CHANNEL_PRESS_TELEGRAM_API_ID`
   - `CHANNEL_PRESS_TELEGRAM_API_HASH`

   Do not send these values in chat.
2. Install the adapter if not already installed:
   `.\\.venv\\Scripts\\python.exe -m pip install -e ".[telegram]"`, then restart
   the server.
3. In **Sources**, register the channel and record an active rights declaration
   covering the scopes you actually hold.
4. Open **Sources → Open Telegram connection**, save the `@handle`, select
   **Test source connection**, and complete the phone/code/two-step login in the
   local form.
5. Run **Import last 30 days (context only)**.
6. Create a daily schedule and confirm the **Effective window** and committed
   watermark. The first live edition needs an explicit baseline.
7. Run **Sync window**, then build with **Collect this window from Telegram before
   building**.
8. Review the flagged articles, inspect every page proof, and approve or reject.
9. Only after that, decide whether to enable **Auto-release** or install the
   production Windows task. Publication approval is separate from API access.

## 9. Known limits and residual risks

- Live Telegram authentication and retrieval are unverified; every live claim in
  the UI remains conditional until step 8 above is completed.
- Real-model testing covers 30 posts only. Capacity for 70/150 live posts, long
  videos and sustained GPU load is not measured.
- The strict audit produces a high review workload with a 3B model. That is a
  quality control, not a bug; plan editor time accordingly.
- Opinion and appendix pages can carry white space when the source set is small.
- FFmpeg is absent; OpenCV handles video frames.
- No public hosting, upload destination or QR landing page is configured.
- The SQLite + local-folder design targets one workstation, not concurrent
  multi-user scale.
- The fixture demo must never be presented as live reporting. Its manifest and
  front page say so.
