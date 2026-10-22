# 0005 — Deterministic jobs with one short-lived worker

## Context

The target is one Windows laptop used as a newsroom desk, not a server cluster.
Scheduling must survive sleep, missed triggers and duplicate Task Scheduler runs,
without keeping a model or a long-running queue daemon in memory.

## Decision

- Windows Task Scheduler runs `scripts/run-worker.ps1` every 15 minutes; the script
  performs one `worker --once` pass and exits.
- Jobs have a deterministic key of **channel + edition kind + cutoff**, enforced by
  a unique constraint, so repeated triggers cannot create a second edition.
- A watermark per `(channel, kind)` records the last processed cutoff; missed
  cutoffs are enqueued on catch-up.
- A job is claimed with a renewable lease and a process-level file lock guarantees
  only one worker processes jobs at a time.
- Builds are transactional and restage from source on retry; a failure cannot
  leave a half-published edition.
- Scheduled builds stage as `awaiting_review`. Auto-release requires the schedule
  policy, no blockers and no high-risk articles.
- UTC is stored in SQLite; the named timezone is used for UI, schedule math and
  display.

## Alternatives considered

- **Celery/Redis**: extra services and memory for a single-user desktop.
- **Always-on Python scheduler**: idle resource use and fragile after reboot.
- **In-process background threads**: lost on server restart and hard to audit.

## Consequences

- Idle resource use is minimal; a missed run is caught up rather than lost.
- Job duration is visible; if a 150-post build cannot finish before release, the
  operator must move the cutoff or reduce the window rather than publish late.
- A stale lease from a killed worker is reclaimed after the configured window.
- Single-workstation SQLite concurrency is a documented scale limit.

## Files

- `channel_press/scheduling/service.py`
- `channel_press/cli.py`
- `scripts/run-worker.ps1`
- `scripts/install-task.ps1`
- `tests/test_scheduling.py`
