from langchain_core.runnables import Runnable
from pydantic import BaseModel, ConfigDict, Field

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


class LinkSelection(BaseModel):
    """The links to follow, already checked: real link numbers only, no duplicates."""

    model_config = ConfigDict(frozen=True)

    chosen: list[PostLink]  # in the order the LLM gave them, at most the ceiling
    hit_ceiling: bool  # there were more valid picks than the ceiling allowed


async def pick_links(
    model: Runnable, post: ExtractedSource, ceiling: int
) -> LinkSelection:
    """Ask the LLM which of the post's links are worth following, then check its answer."""
    numbered_text, links = number_links(post.text)
    if not links:  # no links: skip the LLM call entirely
        return LinkSelection(chosen=[], hit_ceiling=False)
    picks: LinkPicks = await model.ainvoke(build_picker_messages(numbered_text))
    return select_links(links, picks.link_ids, ceiling)


def select_links(
    links: list[PostLink], link_ids: list[int], ceiling: int
) -> LinkSelection:
    """The code-side guard on the LLM's answer: never trust its numbers as given."""
    by_id = {link.id: link for link in links}
    valid: list[PostLink] = []
    for link_id in link_ids:
        link = by_id.get(link_id)
        if link is not None and link not in valid:
            valid.append(link)
    return LinkSelection(chosen=valid[:ceiling], hit_ceiling=len(valid) > ceiling)
