from __future__ import annotations

from pathlib import Path

import fitz

from channel_press.layout.engine import (
    build_page_plan,
    chunk_paragraphs,
    inspect_pdf,
)
from channel_press.layout.planner import EditionPlan, StoryPackage
from channel_press.models import Article, PublicationProfile, StoryCluster


def _package(identifier: int, paragraphs: list[str], *, section="national", genre="news") -> StoryPackage:
    article = Article(id=identifier, story_id=identifier, title=f"Story {identifier}", body_text="\n\n".join(paragraphs))
    story = StoryCluster(id=identifier, canonical_title=f"Story {identifier}", section=section, genre=genre)
    return StoryPackage(
        article=article,
        story=story,
        section=section,
        genre=genre,
        body_paragraphs=paragraphs,
        headline_size="major" if identifier == 1 else "section",
    )


def test_chunking_preserves_every_paragraph_and_marks_continuations():
    paragraphs = [f"Paragraph {index} " + " ".join(["word"] * 90) for index in range(8)]
    package = _package(1, paragraphs)
    chunks = chunk_paragraphs(package, max_words=250, first_max_words=180)
    assert len(chunks) > 2
    assert [chunk.index for chunk in chunks] == list(range(1, len(chunks) + 1))
    assert all(chunk.count == len(chunks) for chunk in chunks)
    assert [paragraph for chunk in chunks for paragraph in chunk.paragraphs] == paragraphs
    assert sum(len(" ".join(chunk.paragraphs).split()) for chunk in chunks) == sum(
        len(paragraph.split()) for paragraph in paragraphs
    )


def test_short_tail_is_merged_into_the_previous_chunk():
    paragraphs = [
        " ".join(["alpha"] * 200),
        " ".join(["beta"] * 200),
        " ".join(["gamma"] * 12),
    ]
    chunks = chunk_paragraphs(_package(1, paragraphs), max_words=205)
    assert len(chunks) == 2
    assert "gamma" in " ".join(chunks[-1].paragraphs)
    assert "beta" in " ".join(chunks[-1].paragraphs)


def test_page_plan_keeps_lead_continuation_and_section_order():
    lead = _package(1, [" ".join(["lead"] * 200), " ".join(["lead"] * 220)])
    second = _package(2, [" ".join(["regional"] * 120)], section="regional")
    profile = PublicationProfile(name="Test", timezone="Asia/Karachi")
    plan = EditionPlan(
        lead=lead,
        front_briefs=[],
        sections=[("national", [lead]), ("regional", [second])],
        packages=[lead, second],
        profile=profile,
        appendix_entries=[],
    )
    page_plan = build_page_plan(plan)
    assert page_plan.lead is lead
    assert len(page_plan.lead_chunks) >= 2
    assert page_plan.lead_chunks[0].index == 1
    assert "national" in page_plan.section_chunks
    continuation = page_plan.section_chunks["national"][0]
    assert continuation.index == 2
    assert continuation.package is lead
    assert page_plan.sections[0][0] == "national"
    assert any(section == "regional" for section, _ in page_plan.sections)
    assert page_plan.templates["front"] in {"front_major_event", "front_standard"}


def test_short_stories_are_paired_into_rows_that_fill_the_page():
    lead = _package(1, [" ".join(["lead"] * 120)])
    first = _package(2, [" ".join(["alpha"] * 180)], section="national")
    second = _package(3, [" ".join(["beta"] * 160)], section="national")
    third = _package(4, [" ".join(["gamma"] * 140)], section="national")
    profile = PublicationProfile(name="Test", timezone="Asia/Karachi")
    plan = EditionPlan(
        lead=lead,
        front_briefs=[],
        sections=[("national", [first, second, third])],
        packages=[lead, first, second, third],
        profile=profile,
        appendix_entries=[],
    )
    page_plan = build_page_plan(plan)
    rows = [[chunk.package.article.id for chunk in row] for row in page_plan.section_rows["national"]]
    assert rows == [[2, 3], [4]]


def test_tall_media_rows_are_not_forced_into_a_half_page_pair():
    lead = _package(1, [" ".join(["lead"] * 120)])
    first = _package(2, [" ".join(["alpha"] * 430)], section="national")
    second = _package(3, [" ".join(["beta"] * 430)], section="national")
    first.media_path = "first.jpg"
    first.map_result = object()
    second.media_path = "second.jpg"
    second.chart_svg = "<svg></svg>"
    profile = PublicationProfile(name="Test", timezone="Asia/Karachi")
    plan = EditionPlan(
        lead=lead,
        front_briefs=[],
        sections=[("national", [first, second])],
        packages=[lead, first, second],
        profile=profile,
        appendix_entries=[],
    )
    page_plan = build_page_plan(plan)
    assert "national" not in page_plan.section_rows
    assert "national" not in page_plan.pair_sections


def test_feature_and_conflict_sections_keep_their_own_measure():
    lead = _package(1, [" ".join(["lead"] * 120)])
    regional = _package(2, [" ".join(["regional"] * 120)], section="regional")
    analysis = _package(3, [" ".join(["analysis"] * 200)], section="analysis", genre="analysis")
    opinion = _package(4, [" ".join(["opinion"] * 200)], section="opinion", genre="opinion")
    profile = PublicationProfile(name="Test", timezone="Asia/Karachi")
    plan = EditionPlan(
        lead=lead,
        front_briefs=[],
        sections=[
            ("regional", [regional]),
            ("analysis", [analysis]),
            ("opinion", [opinion]),
        ],
        packages=[lead, regional, analysis, opinion],
        profile=profile,
        appendix_entries=[],
    )
    page_plan = build_page_plan(plan)
    assert "analysis" not in page_plan.section_rows
    assert "opinion" not in page_plan.section_rows
    # A short analysis can follow a section page, but editorial copy opens its
    # own page so a few lines are never stranded under another section.
    assert page_plan.section_break["analysis"] is False
    assert page_plan.section_break["opinion"] is True


def _write_pdf(path: Path, *, width=792, height=1224, text="A" * 120) -> Path:
    document = fitz.open()
    page = document.new_page(width=width, height=height)
    page.insert_text((72, 90), text, fontsize=9)
    document.save(path)
    document.close()
    return path


def test_pdf_inspection_checks_tabloid_size_fonts_and_blank_pages(tmp_path):
    good = _write_pdf(tmp_path / "good.pdf", text="Searchable tabloid proof text. " * 8)
    report = inspect_pdf(good)
    assert report["page_count"] == 1
    assert report["pages"][0]["width_in"] == 11
    assert report["pages"][0]["height_in"] == 17
    assert report["pages"][0]["text_chars"] > 40
    assert report["pages"][0]["min_font_pt"] >= 7
    assert report["warnings"] == []
    assert report["office_print_proof"] is True
    assert report["expected_size_pt"] == {"width": 792, "height": 1224}

    wrong_size = _write_pdf(tmp_path / "letter.pdf", width=612, height=792)
    report = inspect_pdf(wrong_size)
    assert any("expected 11 x 17" in warning for warning in report["warnings"])

    blank = _write_pdf(tmp_path / "blank.pdf", text="")
    report = inspect_pdf(blank)
    assert any("appears blank" in warning for warning in report["warnings"])


def test_pdf_inspection_summarises_real_page_fill_and_orphan_heads(tmp_path):
    path = _write_pdf(tmp_path / "fill.pdf", text="Searchable tabloid proof text. " * 8)
    meta = {
        "pages": [
            {
                "page": 1,
                "template": "front",
                "section": "front",
                "fill_ratio": 0.94,
                "used_height_px": 1500,
                "content_height_px": 1600,
                "trailing_whitespace_px": 100,
                "underfull": False,
                "orphan_section_head": False,
            },
            {
                "page": 2,
                "template": "national",
                "section": "national",
                "fill_ratio": 0.41,
                "used_height_px": 650,
                "content_height_px": 1600,
                "trailing_whitespace_px": 950,
                "underfull": True,
                "orphan_section_head": True,
            },
        ]
    }
    report = inspect_pdf(path, meta)
    layout = report["layout"]
    assert layout["measured"] is True
    assert layout["underfull_pages"] == [2]
    assert layout["underfull_count"] == 1
    assert layout["average_fill_ratio"] == 0.675
    assert layout["lowest_fill_ratio"] == 0.41
    assert layout["trailing_whitespace_px"] == 1050
    assert any("section heading with no reports" in warning for warning in report["warnings"])
