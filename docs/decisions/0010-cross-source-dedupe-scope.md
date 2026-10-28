# 0010 — Deduplicate across channels only

**Status:** accepted
**Date:** 2026-10-27

## Context

`merge_cross_source_stories` was added with multi-source newspapers so two
authorised channels reporting the same event produce one printed report with
attribution to both. The matcher compared section and a conservative title
token overlap but did not compare the source channel.

A single channel's posts are already clustered inside that channel by
`build_stories`, which keeps distinct incidents apart. Running the cross-source
matcher over the same channel afterwards merged reports whose generic titles
share vocabulary but whose place or subject differs, for example
"Editorial: Local services in Sialkot…" and "Editorial: Local services in
Gilgit…", or separate road reopenings in different districts. On the 150-post
fixture set this reduced 28 clustered stories to 8 printed reports, produced a
thinner paper, and folded unrelated local reports into one article.

## Decision

`merge_cross_source_stories` only considers a candidate when
`candidate.channel_id != story.channel_id`. Within-channel deduplication stays
the responsibility of `build_stories`. Cross-channel reports of the same event
are still merged, weighted by source priority and weight, with all evidence moved
to the kept story and both channels recorded in `article_sources`.

## Consequences

- A 150-post fixture build prints 28 stories again instead of 8, and the 30- and
  70-post sets print 17 and 27. Page counts grow accordingly (7, 8 and 9 pages
  with the current packing).
- Two channels that publish near-identical generic titles for genuinely
  different local events could still be merged. The matcher is deliberately
  conservative (three shared significant words plus strong containment), and an
  editor can split a story or override the edition.
- The function name and docstring now match its behaviour.

## Verification

`tests/test_publications.py::test_cross_source_merge_never_folds_two_reports_from_the_same_channel`
holds two same-channel editorials with near-identical titles and asserts both
survive with no merge. The existing weighted cross-channel merge test still
passes. The fixture capacity tests and the end-to-end edition test were re-run
after the change, and the regenerated edition PDF was inspected page by page.
