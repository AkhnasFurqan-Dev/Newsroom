# 0008 — Immutable per-newspaper print themes

**Status:** accepted
**Date:** 2026-10-27

## Context

The first print pipeline used one global `print.css` identity. That is wrong for a
product that hosts several newspapers on one computer: changing the accent for
one paper would change every paper, and a later palette edit could silently
change the appearance of an already published PDF. The owner also needs real
font choice, not a CSS override, and a preview that reflects the saved design.

## Decision

A theme is stored as an immutable revision per publication. `theme_revisions`
holds the palette, seven font roles, print metrics and optional section accents
as JSON. `is_current` marks the revision used for the next draft and preview.
Saving never edits an existing revision's design fields; it clears the previous
current marker and inserts the next revision number.

`editions.theme_revision_id` plus `editions.theme_snapshot_json` pin the exact
design used for a render. Publishing pins before `mark_published` and
`pin_edition_theme` refuses a published row, so the archive stays immutable even
if the publication profile later changes. Unpinned drafts resolve the current
revision. Draft previews store `preview_theme_hash`; changing the theme marks
unpublished previews stale.

The compiled theme is CSS variables plus `@font-face` declarations. The same
compiled CSS is injected into `print.html`, the specimen proof and the e-paper's
`assets/theme.css`. Charts, maps, captions and QR labels accept the same palette
and font stacks. QR modules are forced black on white regardless of the palette,
because a decorative QR is a broken QR.

Fonts are either discovered installed faces or licensed imports. Only `.ttf`,
`.otf` and `.ttc` files up to 12 MB with valid outline tables and basic Latin
coverage are accepted. Imports are content-addressed under `data/fonts` and
require a licence acknowledgement. Missing weights/styles produce a substitution
warning instead of synthetic bold. A font referenced by any saved revision cannot
be deleted, and CLI backups include the font library.

## Consequences

- Editing a theme cannot rewrite a published edition, its e-paper or its PDF.
- Each newspaper can look distinct without a global stylesheet fork.
- The e-paper and PDF share one palette and one set of role stacks; screen-only
  CSS cannot drift from print.
- A restore must include `fonts/`; older backups without it leave imported faces
  missing until they are re-imported by content hash.
- Proofs are cached by theme hash and a manual `PROOF_VERSION`; bumping the proof
  layout intentionally invalidates old files without touching editions.
- Font licensing remains the owner's responsibility. The app records the
  acknowledgement but cannot verify the grant.

## Verification

`tests/test_theming.py` covers normalization, contrast rejection, presets,
revision immutability, edition pinning, compiled CSS and font resolution.
`tests/test_appearance_web.py` covers form saves, validation, uploads, deletion
protection, editor-only access, path-safe proof serving and a real Playwright
specimen render (1056 × 1632). The fixture-to-PDF end-to-end test asserts the
manifest theme block, pinned revision/snapshot, e-paper `theme.css` and bundled
font files. The live database was backed up and migrated to
`d8a4b1c2e345`; row counts were preserved.
