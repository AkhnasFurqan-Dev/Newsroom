"""Build tiny deterministic TrueType fonts for tests.

The generated file is a real outline font with a minimal cmap, so it exercises
the same fontTools validation and embedding path as an editor's upload without
depending on fonts installed on the test machine.
"""

from __future__ import annotations

from io import BytesIO


def _glyph(char: str):
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    pen = TTGlyphPen(None)
    if char == " ":
        return pen.glyph()
    left, bottom, right, top = 40, 0, 520, 700
    pen.moveTo((left, bottom))
    pen.lineTo((right, bottom))
    pen.lineTo((right, top))
    pen.lineTo((left, top))
    pen.closePath()
    return pen.glyph()


def make_font(
    *,
    family: str = "Test Proof Serif",
    style: str = "Regular",
    weight: int = 400,
    characters: str = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 .,%$",
) -> bytes:
    from fontTools.fontBuilder import FontBuilder

    unique = sorted(set(characters))
    glyph_names = [".notdef"] + [f"uni{ord(char):04X}" for char in unique]
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_names)
    builder.setupCharacterMap({ord(char): f"uni{ord(char):04X}" for char in unique})
    builder.setupGlyf({name: _glyph("A" if name != ".notdef" else "") for name in glyph_names})
    builder.setupHorizontalMetrics({name: (560, 0) for name in glyph_names})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable(
        {
            "familyName": family,
            "styleName": style,
            "fullName": f"{family} {style}",
            "psName": f"{family.replace(' ', '')}-{style}".replace(" ", ""),
        }
    )
    builder.setupOS2(
        sTypoAscender=800,
        sTypoDescender=-200,
        usWinAscent=800,
        usWinDescent=200,
        usWeightClass=weight,
        fsSelection=0x01 if "italic" in style.casefold() else 0x40,
    )
    builder.setupPost()
    buffer = BytesIO()
    builder.save(buffer)
    return buffer.getvalue()
