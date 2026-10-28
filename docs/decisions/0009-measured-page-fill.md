# 0009 — Measure page fill and stop forcing empty section pages

**Status:** accepted
**Date:** 2026-10-27

## Context

Phase C visual inspection found three composition problems in the real fixture
PDF: clipped body text on the Conflict & War page when a chronology followed the
story, repeated chronology rows, and Analysis/Opinion pages that were more than
80% blank because every section forced a new page. The old planner also paired
any two one-page reports into a half-page row without checking whether the pair
fit, which could leave a blank page behind an overflowing row.

## Decision

The planner estimates each report's load from its real body words, headline,
standfirst and any media/map/chart/timeline weight. Two reports are paired only
for `national`, `regional`, `international` and `defence`, and only when each is
under `ROW_SINGLE_BUDGET` and the pair is under `ROW_WORD_BUDGET`. A tall media
row stays full width.

A section gets a fresh page only when its real load reaches
`SECTION_BREAK_MIN_LOAD`; thin sections flow after the previous one. Opinion
copy always opens a page, because a short editorial is otherwise split after a
line or two at the foot of a section page. Briefs and evidence follow the
preceding content. No process invents filler copy to raise a fill ratio.

The browser's `postprocess.js` measures each page from real leaf text, figure and
table bottoms rather than from the page box. `inspect_pdf` turns that into
`quality.layout`: per-page fill, underfull pages, average/lowest fill, trailing
whitespace and a `measured` flag. Underfull pages are editorial diagnostics.
Blank pages, overflow and a section heading with no reports remain proof
failures.

## Consequences

- The fixture edition changed from 7 pages with four underfull pages (57%, 20%,
  21%, 38%) and an orphan-head warning to 5 pages with no underfull page and no
  proof warning. A fresh 2026-10-28 render after the cross-source dedupe fix
  measured average fill 0.783; page 4 (Opinion, Briefs and evidence) ends at
  0.626 because the fixture copy runs out, and no filler was invented to raise it.
- Page count is content-dependent; tests assert a minimum, not an exact count.
- Trimming the window or changing section order may still leave a legitimate
  thin page. The editor sees the number and decides; the app does not pad it.
- The load estimate is deliberately conservative for row pairing. A pair that
  might overflow is kept full width rather than risking a blank page.
- Opinion opening its own page can leave a section page shorter than before. That
  is preferred to a split editorial and is visible in the fill report.

## Verification

`tests/test_layout.py` covers chunking, row pairing, tall-media rejection,
opinion page separation, PDF fill summary and orphan-head warnings. The fixture
end-to-end test asserts real measured fill, underfull consistency and page count.
All five fixture pages were rendered to PNG and inspected at full size; the
previous one-line opinion split is gone.
