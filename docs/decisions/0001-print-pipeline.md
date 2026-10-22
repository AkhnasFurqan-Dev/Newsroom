# 0001 — Paged.js and installed Chrome for the print pipeline

## Context

The newspaper needs true paginated print composition: running headers, folios,
column balancing, continuation pages and a vector PDF at exact tabloid size
(11 × 17 in). A browser “print to PDF” button is not enough because the build must
detect overflow and blank pages before release.

## Decision

- Use Paged.js 0.4.3, vendored inline into the print document, to paginate the
  Jinja print template.
- Render with Playwright 1.49.1 using the **installed Google Chrome channel**
  (`channel="chrome"`), not a downloaded Chromium.
- After Paged.js settles, run `postprocess.js` to validate every element rectangle
  against `.pagedjs_page_content`, confirm the page count is stable and expose
  `window.__pagedReady`.
- Inspect the resulting PDF with PyMuPDF: page count, exact size in points, text
  characters, embedded fonts, images, links, minimum font size and blank pages.
- Fail the release on any unexplained QA warning (`LayoutQualityError`).

## Alternatives considered

- **WeasyPrint**: good CSS paged media support but weaker for the required
  multi-column composition and continuation labels; also adds native dependencies.
- **LaTeX**: excellent typography, but a separate toolchain and harder integration
  with the Jinja/HTML editorial templates.
- **Bundled Chromium**: avoids requiring Chrome, but a second 150 MB browser to
  maintain and update; Chrome is already present on the target machine.

## Consequences

- Chrome updates can change pagination. The slow end-to-end suite renders real
  PDFs and catches geometry regressions; pin/verify after Chrome updates.
- The pipeline is local, free and Windows-native.
- The automated geometry check is deliberately conservative. A warning blocks
  release unless an owner explicitly overrides in code and documents why.
- Verified output: 792 × 1224 pt on every page, five-page demo, no overflow or
  blank pages, embedded font subsets.

## Files

- `channel_press/layout/engine.py`
- `channel_press/layout/templates/print.html.j2`
- `channel_press/web/static/css/print.css`
- `channel_press/web/static/js/postprocess.js`
- `tests/test_edition_e2e.py`
