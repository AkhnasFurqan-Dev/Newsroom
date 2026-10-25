# 0007 — Reclassify stored posts before story building

**Status:** accepted
**Date:** 2026-10-25

## Context

Post classification is deterministic and versioned, but imported rows keep the
version that created them. Story building reads only `inclusion_status`, so a post
stored as included by an older ruleset bypassed newer exclusions even though the
current classifier would reject it. The owner's stored archive contained 1,597
`legacy` rows out of 1,661.

Rewriting historical rows only at display time would leave the build unsafe. A
database migration cannot call the classifier because classification depends on
application code, media kinds and source rights. A one-off repair script would be
easy to forget after the next classifier upgrade.

## Decision

`channel_press.ingestion.service.reclassify_posts` re-runs the current classifier
over stored rows. The pipeline calls it before `clear_editorial_drafts` and
`build_stories` on every non-resumed build, limited to the edition window. The
whole channel is walked in chronological order so repeated-copy detection sees
earlier accepted posts outside the window; only in-window stale rows are changed.

For each stale row the pass:

- recomputes classification, reason, language and moderation status;
- keeps an explicit `editor_override` instead of the classifier result;
- labels a later identical text as `repeated_copy`;
- creates missing fragments for a post that is newly includable;
- records `source.reclassify` in the audit log.

`channel-press classify-posts --channel <slug>` exposes the same pass for older
archives, with optional window bounds and `--force`.

Empty text is never used for repeated-copy matching: media-only posts would
otherwise all share the hash of an empty string and be discarded as duplicates.

## Consequences

- A normal build repairs legacy rows in its window without operator action.
- The pass is additive and idempotent: current rows are skipped unless forced.
- Editor decisions, original text, source links, rights and media provenance are
  preserved; only classification fields change.
- The pass scans all channel posts for duplicate ordering. This is cheap for the
  expected archive size (thousands of rows) and runs inside the existing job.
- If a future classifier becomes much more expensive, the seed query should be
  narrowed and benchmarked before keeping the full scan.

## Verification

Fixture tests cover legacy backfill, editor-override survival, idempotency,
repeated copies, media-only posts and the pipeline call order. The owner database
was backed up, then the CLI reclassified 1,661 posts: 1,213 included, 448 excluded,
307 forwarded duplicates; a second pass reported 1,661 current and 0 changed.
