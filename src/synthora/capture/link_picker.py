from langchain_core.runnables import Runnable
from pydantic import BaseModel, Field

from synthora.capture.links import PostLink, number_links
from synthora.domain import ExtractedSource
from synthora.prompts import picker as picker_prompt


class LinkPicks(BaseModel):
    """What the LLM returns. Field descriptions are sent to Claude as instructions."""

    reasoning: str = Field(
        description="One or two sentences: what the post is about and which links serve that topic."
    )
    link_ids: list[int] = Field(
        description="The [link N] numbers of the helpful resources, in the order they appear. Empty if none."
    )


def make_picker_model(chat) -> Runnable:
    return chat.with_structured_output(LinkPicks, method="json_schema")


def build_picker_messages(numbered_text: str) -> list[tuple[str, str]]:
    text = numbered_text.replace("</post>", "</ post>")
    return [("system", picker_prompt.PROMPT), ("human", f"<post>\n{text}\n</post>")]


async def pick_links(
    model: Runnable, post: ExtractedSource
) -> tuple[list[PostLink], list[int]]:
    """Ask the LLM. Returns all links found, plus the numbers it picked (NOT yet validated)."""
    numbered_text, links = number_links(post.text)
    if not links:
        return [], []  # no links: skip the LLM call entirely
    picks: LinkPicks = await model.ainvoke(build_picker_messages(numbered_text))
    return links, picks.link_ids


def keep_valid_picks(
    link_ids: list[int], links: list[PostLink], ceiling: int
) -> list[PostLink]:
    """The code-side guard: only real link numbers, no duplicates, at most `ceiling`."""
    by_id = {link.id: link for link in links}
    chosen: list[PostLink] = []
    for link_id in link_ids:
        link = by_id.get(link_id)
        if link is not None and link not in chosen:
            chosen.append(link)
    return chosen[:ceiling]
