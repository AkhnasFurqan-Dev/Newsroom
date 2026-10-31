# Newsroom — brand identity: The Folio Mark

*Many sources. One edition.*

## The mark
A ragged group of columns on the left hangs from a masthead rule. These are independent information streams, unequal and unresolved.
An Oxblood **spine** sits between them and one tall, justified **block** on the right, the finished edition. The rule feeds into
the block, so the whole composition reads left to right: collection, organisation, publication.
The geometry lives on a 4-unit module (96-unit master). The construction grid is not included in any asset.

## Files
| File | Use |
|---|---|
| `newsroom-logo-horizontal.svg` | Primary lockup: symbol + wordmark. Default for headers, documents, web. |
| `newsroom-logo-stacked.svg` | Square-ish spaces: splash screens, covers, social. |
| `newsroom-symbol.svg` | Symbol alone, 96×96 canvas. Use at 32 px and up; below that use the small app icon. |
| `newsroom-logo-reversed.svg` | Paper-on-transparent for **dark** backgrounds (Ink or darker). Spine is Slate (see below). |
| `newsroom-logo-monochrome.svg` | Pure black (#000000), single colour: print, fax, stamps, engraving. |
| `newsroom-logo-white.svg` | Pure white (#FFFFFF) equivalent, for photographs and arbitrary dark fields. *(extra)* |
| `newsroom-app-icon.svg` | Master application icon (48-unit grid, Paper tile). Used for 48 px and above. |
| `newsroom-app-icon-small.svg` | 16-unit pixel-grid optical variant. Used for 16 px and 32 px. |
| `newsroom-app-icon-24.svg` | 24-unit pixel-grid optical variant, so 24 px is crisp too. *(extra)* |
| `newsroom.ico` | Windows icon: 16, 24, 32, 48, 64, 128, 256 px. Wire this into the EXE, installer, shortcuts and window. |
| `newsroom-icon-256/512/1024.png` | Transparent-corner raster exports of the master icon. |
| `newsroom-identity-sheet.svg/.png` | Flat presentation of the whole identity. |
| `concepts/` | The three explored directions, the selected refinement, and the overview sheet. |
| `tools/` | `verify.py` (checks), `make_rasters.py` (SVG → PNG/ICO), `source/` (generator for all assets). |

## Colour
| Role | Hex | Notes |
|---|---|---|
| Primary Ink | `#20272B` | Symbol, wordmark, dark backgrounds |
| Paper | `#F3F0E8` | Icon tile, light backgrounds, reversed marks |
| Oxblood Accent | `#913E36` | **Spine only.** 6.3:1 on Paper. |
| Secondary Slate | `#758089` | Secondary text; the spine on dark backgrounds |

Oxblood on Ink is only 2.1:1, so on dark backgrounds the spine switches to Slate (3.8:1) rather than dimming out.
The icon uses a **Paper tile** (not an Ink tile) because an Ink tile disappears against dark taskbars.

## Usage
- **Clear space:** at least ⅓ of the symbol's height on every side.
- **Minimum size:** horizontal lockup ≥ 120 px wide; symbol ≥ 32 px (below that use the app icon variants).
- Never recolour the spine, add effects (shadow, gradient, glow), re-set the wordmark, or stretch the artwork.
- The app icon contains no text by design.

## Icon sizes (Windows)
16 and 32 px use `app-icon-small` (aligned to whole pixels), 24 px uses `app-icon-24`, 48 px and up use the master (also pixel-aligned at 48).
Entries 16–48 px are classic 32-bit BMP/DIB frames for maximum tool compatibility; 64, 128 and 256 px are PNG-compressed.

## Typography
Wordmark: **Bitstream Charter Bold**, converted to outlines (no font needed at runtime), tracking −6/1000 em, with small manual pair
adjustments at w–s and s–r. Bitstream Charter's licence permits free commercial use and redistribution, but have your own counsel confirm
before shipping the *font itself*. The logo files contain only outlines. If you later license Source Serif 4 (OFL), re-run `tools/source/build.py`
with that font.

## Verification
`python tools/verify.py` runs the checks (all passing at delivery): required files present, SVGs self-contained (no external references,
live text, images, styles or scripts), every file renders, wordmark outline has the 8 expected letters (11 contours), monochrome is only `#000000`,
contrast ratios, ICO structure and sizes, centred/uncropped frames, 16 px frame pixel-exact, PNG alpha.
Rasters are rendered from the SVG masters by `tools/svgmini.py` (a small, dependency-light renderer for the plain rects/paths used here).
The files were checked with Pillow and that renderer; they have **not** been opened in the Windows shell, a browser, or Visual Studio / `rc.exe`,
so give the ICO a quick check in your real build.
