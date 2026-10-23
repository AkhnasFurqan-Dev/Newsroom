# Post-M14 Verification Checklist (L00–L09)

Owner instruction: `POST_M14_LIVE_COMPLETION.md`. Every row below is filled from
commands actually run in this checkout, not from earlier reports.

Status vocabulary: `not started` · `working` · `blocked` · `independently verified`.

Baseline verified before any L-phase change (2026-10-23):

| Check | Command | Observed |
| --- | --- | --- |
| Full suite | `.\.venv\Scripts\python.exe -m pytest -q` | 125 tests collected, exit 0 (slow PDF renders included) |
| Lint | `.\.venv\Scripts\ruff.exe check channel_press tests` | `All checks passed!` |
| Database | existing `data/channel_press.db` preserved, now revision `b7c1f2d4e901` | not reset; 26 posts, 15 articles, 1 edition, 1 user |
| GPU | `nvidia-smi` | RTX 3060 Laptop, **6144 MiB** (earlier log said "8 GB" — corrected) |
| Ollama | `ollama --version`, `GET /api/tags` | 0.40.9; `qwen2.5:3b-instruct` 1841 MB installed |
| Telethon | `python -c "import telethon"` | 1.38.1 installed via `.[telegram]` |
| FFmpeg | `Get-Command ffmpeg` | absent; OpenCV 4.10.0 fallback stays |
| Chrome | `C:\Program Files\Google\Chrome\Application\chrome.exe` | present |

## Phase status

| Phase | Scope | Status | Evidence |
| --- | --- | --- | --- |
| L00 | Baseline audit, checklist | independently verified | this file, `docs/POST_M14_READINESS_REPORT.md` |
| L01 | Secure configuration, `.env` placeholders, preflight | independently verified | commit `4cab3f6`; `tests/test_preflight.py` |
| L02 | Telegram user login, channel resolution, live adapter | working — live login blocked | commit `29dc7a4`; fixture-backed `tests/test_telegram_live.py`; owner values needed |
| L03 | Cutoff-to-cutoff collection, windows, watermarks | independently verified (fixtures) | commit `4691145`; `tests/test_collection.py`, `tests/test_cutoff_windows.py` |
| L04 | Real local AI (Ollama) in the pipeline | independently verified (30 posts) | commit `d02167c`; `tests/test_ollama_live.py`; 8-page PDF inspected |
| L05 | Editorial desk, window preview, publication fidelity | independently verified | commits `49991fe`, `a81a739`; `tests/test_edition_e2e.py`; all 8 page proofs inspected |
| L06 | Windows scheduling and recovery | independently verified | commit `14b2e48`; `LastTaskResult 0`; clean worker log |
| L07 | Security, credential non-disclosure | independently verified | `tests/test_security_time.py`, `tests/test_preflight.py`; secret redaction assertions |
| L08 | Documentation update | independently verified | docs commit; README, USER_MANUAL, ADMIN_GUIDE, TROUBLESHOOTING, readiness report |
| L09 | Supervised live acceptance | blocked (owner values/consent) | see readiness report §8 |

Live-only statuses are reported separately in
`docs/POST_M14_READINESS_REPORT.md`: `fixture`, `local AI`, `Telegram live`,
`supervised end-to-end`, `automatic scheduled publication`. A fixture pass is
never evidence of a live pass.

## Blocking items for L09

1. Owner fills `CHANNEL_PRESS_TELEGRAM_API_ID` and
   `CHANNEL_PRESS_TELEGRAM_API_HASH` in `.env`.
2. Owner completes the interactive Telegram login in the local UI.
3. Owner records a rights declaration for the source channel.
4. Owner approves one supervised edition for release.

Everything independent of those four items is built, tested and committed.
