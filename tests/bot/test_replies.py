from synthora.bot.replies import render, render_completed
from synthora.capture.pipeline import Failed, Repeat, Succeeded
from synthora.domain import Distillation, ExtractedSource, SourceType

SOURCE = ExtractedSource(tool="fake", url="https://a.com", title="A title", text="x")
DISTILLATION = Distillation(
    tldr="The idea.", key_ideas=["One.", "Two."], notable_quotes=[]
)


def linked_ok(url: str) -> dict:
    return {
        "url": url,
        "source": SOURCE.model_copy(update={"url": url, "title": None}),
        "distillation": DISTILLATION,
    }


def test_success_shows_title_tldr_and_key_ideas():
    text = render(Succeeded(source=SOURCE, distillation=DISTILLATION))
    assert text.startswith("✅ A title")
    assert "TL;DR: The idea." in text
    assert "• One.\n• Two." in text
    assert "Linked resources" not in text


def test_linked_resources_are_counted_and_listed():
    linked = [linked_ok("https://b.com"), {"url": "https://c.com", "error": "HTTP 404"}]
    text = render(
        Succeeded(
            source=SOURCE,
            distillation=DISTILLATION,
            linked=linked,
            notes=["⚠️ stopped at the safety ceiling of 2 links"],
        )
    )
    assert "🔗 Linked resources: 1/2 read" in text
    assert "✅ https://b.com\n   The idea." in text
    assert "❌ https://c.com: HTTP 404" in text
    assert text.endswith("⚠️ stopped at the safety ceiling of 2 links")


def test_failure_shows_the_source_type_and_reason():
    text = render(
        Failed(reason="couldn't read it: HTTP 404", source_type=SourceType.WEB_ARTICLE)
    )
    assert text == "web_article\n❌ couldn't read it: HTTP 404"


def test_failure_before_classifying_shows_a_question_mark():
    assert render(Failed(reason="boom")).startswith("?\n")


def test_repeat_shows_the_first_capture_date_and_tldr():
    text = render(Repeat(captured_on="2026-11-20", tldr="The idea."))
    assert text == "🔁 already captured on 2026-11-20\n\nThe idea."


def test_completed_capture():
    text = render_completed(Succeeded(source=SOURCE, distillation=DISTILLATION))
    assert text == "✅ completed https://a.com\n\nTL;DR: The idea."
    assert render_completed(Failed(reason="it was already completed")) == (
        "❌ it was already completed"
    )
