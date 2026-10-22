# 0004 — Offline e-paper bundle and truthful source links

## Context

The e-paper must open locally without network, match the PDF, and still work when
served by the app. Source links and QR codes must be truthful: a printed code that
points to localhost or a dead placeholder is a defect. No public publication origin
was configured for this build.

## Decision

- Generate a plain folder: `epaper/index.html`, `epaper/text.html`, page JPEGs,
  `epaper/manifest.json`, linked to `../sample_issue.pdf`.
- Use **relative links only** so the folder can be copied and opened from disk.
- Serve it under `/editions/{id}/epaper/` and redirect `/editions/{id}/epaper`
  (no trailing slash) with 307 so relative links resolve inside the bundle.
- Serve `/editions/{id}/sample_issue.pdf` as a redirect to the canonical PDF route
  so the viewer’s **Print PDF** link works in both contexts.
- Default **QR policy** is `appendix`: print the source appendix and a QR only when
  a public URL was verified reachable. Configurable alternatives are
  `verified_only` and `none`.
- Never emit a QR for a loopback, private or unverified destination.
- Label the e-paper as a synthetic fixture edition in the demo.

## Alternatives considered

- **Single-page app with fetch**: breaks when opened from `file://`.
- **Absolute `/editions/...` links**: break when the folder is copied or archived.
- **Always print a QR**: dishonest without a verified public endpoint.

## Consequences

- The bundle works offline from disk and through the server.
- The viewer’s navigation, text edition, source panel and PDF link are functional.
- Source links that only exist as fixture message IDs are printed as
  “Fixture message — no public URL”, not disguised.
- A public-hosted future deployment must set and verify a real origin before QR
  output is enabled.

## Files

- `channel_press/publishing/service.py`
- `channel_press/publishing/templates/epaper.html.j2`
- `channel_press/publishing/qr.py`
- `channel_press/web/app.py`
- `tests/test_web.py`
