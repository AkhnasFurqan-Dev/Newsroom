# 0006 — Release status and manifest integrity

## Context

The manifest is written during layout, before an editor has necessarily released
the issue. In an early build a published edition still carried
`"status": "draft"` in `manifest.json`, which contradicts the edition record and
misleads anyone auditing the bundle.

## Decision

- Write the manifest with the status the build actually expects: `published` for a
  direct release, `awaiting_review` for a staged scheduled build.
- When a release happens later, rewrite only the manifest’s release fields
  (`edition.status`, `edition.published_at`) and update `checksums.json` for
  `manifest.json` in the same operation.
- Keep the original build-time evidence, page geometry, source map and article map
  unchanged.
- The manifest and checksums remain the authoritative record for that exact
  version folder. Editing files by hand is not supported; create a new version.

## Alternatives considered

- **Leave the manifest as “draft”**: factually wrong for a released edition.
- **Regenerate the whole manifest at release**: changes build evidence and risks
  divergence from the PDF.
- **Omit status**: loses useful release state.

## Consequences

- A published edition’s manifest agrees with the database and the PDF.
- Checksums stay valid after the status update.
- The e-paper bundle manifest is unchanged by this step; it describes the bundle,
  not the release workflow.

## Verification

- Slow end-to-end test asserts `manifest["edition"]["status"] == "published"` and a
  non-empty `published_at`.
- `checksums.json` was re-hashed for all four entries after the demo rebuild; all
  matched.

## Files

- `channel_press/publishing/service.py` (`record_manifest_release`)
- `channel_press/scheduling/service.py`
- `channel_press/web/app.py`
- `tests/test_edition_e2e.py`
