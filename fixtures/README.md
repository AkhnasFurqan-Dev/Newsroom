# Demonstration fixtures

Everything under `fixtures/` is synthetic test data created for Channel Press.

- `telegram_export_sample.json` — a deterministic owner-import dataset. Names,
  places, events, quotations and figures are invented for software testing. It
  is **not** a report of real events and must not be published as news.
- `posts_30.json`, `posts_70.json`, `posts_150.json` — deterministic volume sets
  for performance and pagination tests.
- `media/` — procedural test plates and a synthetic test video, explicitly
  labelled **NOT DOCUMENTARY EVIDENCE**. They contain no real people or events.

The fixture channel is marked `is_demo` and its edition footer states that it is
a demonstration edition. Generated fixture editions are not suitable for public
distribution.

The map layers in `data/geodata/` are from Natural Earth (public domain):
<https://www.naturalearthdata.com/>. See `CARTOGRAPHY.md` for the exact files,
version notes and boundary-convention disclosure.
