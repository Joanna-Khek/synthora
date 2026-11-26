from datetime import date

import pytest
import yaml
from pydantic import ValidationError

from synthora.obsidian.notes import (
    MY_NOTES,
    EvidenceEntry,
    NodeNote,
    SourceNote,
    safe_note_name,
    name_key,
    split_frontmatter,
    join_frontmatter,
)
from synthora.domain import SourceType


def split(markdown: str) -> tuple[dict, str]:
    """Frontmatter (parsed by a real YAML parser) and body."""
    _, frontmatter, body = markdown.split("---\n", 2)
    return yaml.safe_load(frontmatter), body


def source(**overrides) -> SourceNote:
    fields = dict(
        title="Use the saw, fear the saw",
        source_type=SourceType.WEB_ARTICLE,
        url="https://stephango.com/saw?ref=x#top",
        captured=date(2026, 11, 16),
        tldr="Powerful tools help and hurt.",
        key_ideas=["Respect the tool.", "Fear keeps you careful."],
        relations={
            "uses": ["Retrieval-Augmented Generation"],
            "introduced_by": ["Steph Ango"],
        },
    )
    return SourceNote(**(fields | overrides))


def test_unquoted_wikilinks_are_not_links_in_yaml():
    # Why FR-19b exists: without quotes, YAML reads [[RAG]] as a nested list.
    assert yaml.safe_load("uses:\n  - [[RAG]]") == {"uses": [[["RAG"]]]}


def test_relations_parse_as_lists_of_wikilink_strings():
    props, _ = split(source().render())
    assert props["uses"] == ["[[Retrieval-Augmented Generation]]"]
    assert props["introduced_by"] == ["[[Steph Ango]]"]


def test_source_properties_survive_a_round_trip():
    props, _ = split(
        source(saved_via=safe_note_name("Best 12 repos: for AI code")).render()
    )
    assert props["type"] == "source"
    assert props["source_type"] == "web_article"
    assert props["url"] == "https://stephango.com/saw?ref=x#top"  # '?' and '#' survive
    assert props["captured"] == date(2026, 11, 16)  # a real date, not a string
    assert props["saved_via"] == "[[Best 12 repos for AI code]]"
    assert props["aliases"] == []


def test_sections_are_in_order_and_my_notes_is_last():
    _, body = split(source(comment="for the RAG talk").render())
    headings = [line for line in body.splitlines() if line.startswith("## ")]
    assert headings == [
        "## TL;DR",
        "## Why I saved this",
        "## Key ideas",
        "## Related",
        MY_NOTES,
    ]


def test_no_comment_means_no_why_section():
    assert "## Why I saved this" not in source().render()


def test_quotes_stay_separate_blockquotes():
    _, body = split(source(notable_quotes=["First.", "Second\nline two."]).render())
    assert "> First.\n\n> Second\n> line two." in body


def test_unknown_relation_is_rejected():
    with pytest.raises(ValidationError):
        source(relations={"related_to": ["RAG"]})


@pytest.mark.parametrize("bad", ["[[RAG]]", "RAG|alias", "a/b", "RAG: intro"])
def test_link_syntax_or_illegal_characters_in_names_are_rejected(bad):
    with pytest.raises(ValidationError):
        source(relations={"uses": [bad]})


@pytest.mark.parametrize(
    "title, expected",
    [
        ("RAG: a primer?", "RAG a primer"),
        ("C/C++ <tips>", "C C++ tips"),
        ("GPT-4.5", "GPT-4.5"),  # dots are fine: paths always include .md
        ("ends with dots...", "ends with dots"),
        ("???", "Untitled"),
    ],
)
def test_safe_note_name(title, expected):
    assert safe_note_name(title) == expected


def test_node_note_with_evidence():
    node = NodeNote(
        title="Retrieval-Augmented Generation",
        type="concept",
        synthesis="Give the model retrieved text to ground its answer.",
        aliases=["RAG"],
        relations={"is_a": ["Information Retrieval"], "part_of": []},
        evidence=[
            EvidenceEntry(
                day=date(2026, 11, 16),
                source="Use the saw, fear the saw",
                text="Mentions RAG\nin passing.",
                contradicts=True,
            ),
        ],
    )
    props, body = split(node.render())
    assert props == {
        "type": "concept",
        "aliases": ["RAG"],
        "is_a": ["[[Information Retrieval]]"],
    }
    assert (
        "- 2026-11-16 · [[Use the saw, fear the saw]]: "
        "⚠️ **Contradicts earlier evidence.** Mentions RAG in passing."
    ) in body
    assert body.rstrip().endswith(MY_NOTES)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("Embeddings", "Embedding"),
        ("embedding", "Embedding"),
        ("Retrieval-Augmented Generation", "retrieval augmented generation"),
        ("Retrieval_Augmented  Generation", "Retrieval-Augmented Generation"),
        ("Approximate Nearest Neighbour Search", "Approximate Nearest Neighbor Search"),
        ("LLMs", "LLM"),
        ("Vector Databases", "Vector Database"),
        ("Search Queries", "Search Query"),
        ("Tokenisation", "Tokenization"),
        ("Tokenization", "tokenisation"),
        ("Bayes' Theorem", "Bayes Theorem"),
    ],
)
def test_same_node_same_key(a, b):
    assert name_key(a) == name_key(b)


@pytest.mark.parametrize(
    ("a", "b"),
    [
        (
            "Vector DB",
            "Vector Database",
        ),  # an abbreviation: the agent's call, via aliases
        ("Attention", "Self-Attention"),
        ("GPT-4", "GPT-4o"),
        ("Claude 3", "Claude 4"),
    ],
)
def test_different_names_keep_different_keys(a, b):
    assert name_key(a) != name_key(b)


@pytest.mark.parametrize("word", ["Analysis", "Class", "Corpus", "Hour"])
def test_words_that_only_look_plural_or_british_are_left_alone(word):
    assert name_key(word) == word.casefold()


def test_keys_only_need_to_match_each_other_not_look_right():
    # 'bias' -> 'bia' is fine: a key is compared, never shown. Excluding '-as'
    # would break 'Ideas' -> 'Idea'. What matters is that no OTHER name collides.
    assert name_key("Bias") == name_key("bias")
    assert name_key("Ideas") == name_key("Idea")


def test_frontmatter_round_trip_keeps_properties_and_body():
    text = NodeNote(
        title="Alpha",
        type="concept",
        synthesis="S.",
        aliases=["A"],
        relations={"uses": ["Beta"]},
    ).render()
    props, body = split_frontmatter(text)
    assert props["uses"] == ["[[Beta]]"] and props["aliases"] == ["A"]
    assert join_frontmatter(props, body) == text


@pytest.mark.parametrize(
    "text", ["no frontmatter", "---\nnever closed", "---\n- a list\n---\nbody"]
)
def test_split_frontmatter_rejects_malformed_notes(text):
    with pytest.raises(ValueError):
        split_frontmatter(text)
