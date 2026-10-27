"""Deterministic quality pass and persisted classification signals."""

from __future__ import annotations

import json

from channel_press.ai.providers import FixtureDraftProvider
from channel_press.editorial.clustering import build_stories
from channel_press.editorial.drafting import draft_story, reaudit_article
from channel_press.editorial.quality import QUALITY_VERSION, quality_review
from channel_press.models import PostFragment, ReviewFlag, SourcePost, StoryCluster
from tests.test_editorial import WINDOW


def _story_with_message(db, message_id: int) -> StoryCluster:
    post = db.query(SourcePost).filter_by(telegram_message_id=message_id).one()
    fragment = db.query(PostFragment).filter_by(post_id=post.id).first()
    assert fragment is not None and fragment.story_id is not None
    return db.get(StoryCluster, fragment.story_id)


def _long_news_body(*, extra: str = "") -> list[str]:
    return [
        "Security forces said on Monday that two people were killed and six injured when a blast struck a market in Quetta.",
        "The district administration said the wounded were taken to the civil hospital and that an investigation had begun.",
        "Police did not name any suspect and said the area had been cordoned off while officers collected evidence.",
        extra,
    ]


def test_news_opinion_leak_is_flagged_but_attributed_criticism_is_not():
    leak = quality_review(
        title="Quetta market blast kills two",
        deck="",
        standfirst="",
        body=_long_news_body(extra="The government's response was shameful and incompetent."),
        genre="news",
        publication_names=["The Pakistan News"],
        source_text="Security forces said a blast in Quetta killed two people.",
    )
    codes = {finding["code"] for finding in leak["findings"]}
    assert "opinion_leak" in codes
    assert leak["status"] in {"review", "block"}

    attributed = quality_review(
        title="Quetta market blast kills two",
        deck="",
        standfirst="",
        body=_long_news_body(
            extra='A traders association spokesman said the response was "shameful and incompetent".'
        ),
        genre="news",
        publication_names=["The Pakistan News"],
        source_text="Security forces said a blast in Quetta killed two people.",
    )
    assert "opinion_leak" not in {finding["code"] for finding in attributed["findings"]}


def test_quality_detects_repeated_wording_long_headline_and_invented_reporter():
    repeated = "Security forces reopened the main road after the blast on Monday morning."
    report = quality_review(
        title="A very long headline that keeps going well beyond any sensible tabloid column measure and keeps going",
        deck="",
        standfirst=repeated,
        body=[
            repeated,
            "The district administration said traffic had resumed.",
            repeated,
        ],
        genre="news",
        publication_names=["The Pakistan News"],
        source_text="Security forces reopened the road on Monday.",
    )
    codes = {finding["code"] for finding in report["findings"]}
    assert "long_headline" in codes
    assert "repeated_phrase" in codes
    assert "redundant_standfirst" in codes

    invented = quality_review(
        title="Road reopens after blast",
        deck="",
        standfirst="",
        body=_long_news_body(extra="A reporter for The Pakistan News visited the scene."),
        genre="news",
        publication_names=["The Pakistan News"],
        source_text="Security forces reopened the road.",
    )
    assert "attribution" in {finding["code"] for finding in invented["findings"]}


def test_quality_passes_a_plain_attributed_report():
    report = quality_review(
        title="Quetta market blast kills two",
        deck="",
        standfirst="",
        body=_long_news_body(),
        genre="news",
        publication_names=["The Pakistan News"],
        source_text="Security forces said a blast in Quetta killed two people.",
    )
    assert "opinion_leak" not in {finding["code"] for finding in report["findings"]}
    assert report["version"] == QUALITY_VERSION
    assert report["body_words"] > 40


def test_drafting_records_independent_quality_in_the_audit(db, demo_channel, sample_import):
    build_stories(db, demo_channel.id, *WINDOW)
    article = draft_story(db, _story_with_message(db, 101), FixtureDraftProvider())
    db.commit()
    audit = json.loads(article.audit_json)
    assert audit["quality"]["version"] == QUALITY_VERSION
    assert isinstance(audit["quality"]["findings"], list)
    assert audit["quality"]["status"] in {"pass", "review", "block"}


def test_reaudit_refreshes_a_single_open_quality_flag(db, demo_channel, sample_import):
    build_stories(db, demo_channel.id, *WINDOW)
    article = draft_story(db, _story_with_message(db, 102), FixtureDraftProvider())
    db.commit()
    body = _long_news_body(extra="The response was disgraceful and must stop.")
    article.body_json = json.dumps(body)
    article.body_text = "\n\n".join(body)
    article.title = "A deliberately overlong headline that exceeds the tabloid measure by a wide margin and keeps going"
    db.flush()

    audit = reaudit_article(db, article)
    db.commit()
    assert audit["quality"]["status"] == "review"
    assert "opinion_leak" in {finding["code"] for finding in audit["quality"]["findings"]}
    open_quality = db.query(ReviewFlag).filter_by(
        article_id=article.id, category="quality", status="open"
    ).all()
    assert len(open_quality) == 1

    # A second run replaces the flag instead of stacking duplicates.
    reaudit_article(db, article)
    db.commit()
    again = db.query(ReviewFlag).filter_by(
        article_id=article.id, category="quality", status="open"
    ).all()
    assert len(again) == 1


def test_classification_signals_are_persisted_with_a_reason(db, demo_channel, sample_import):
    post = db.query(SourcePost).filter_by(telegram_message_id=101).one()
    signals = json.loads(post.classification_signals_json or "[]")
    assert post.classification_reason
    assert signals
    kinds = {signal["kind"] for signal in signals}
    assert kinds & {"event_terms", "numbers", "media", "roman_urdu_terms"}


def test_article_with_no_body_is_blocked_by_the_quality_pass():
    report = quality_review(
        title="",
        deck="",
        standfirst="",
        body=[],
        genre="news",
        publication_names=[],
    )
    assert report["status"] == "block"
    assert {"empty_headline", "empty_body"} <= {finding["code"] for finding in report["findings"]}
