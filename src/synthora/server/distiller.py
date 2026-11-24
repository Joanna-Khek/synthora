from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage
from langchain_core.runnables import Runnable
from loguru import logger

from synthora.server.schemas import Distillation, ExtractedSource
from synthora.config import LLMConfig
from synthora.prompts import distill as distill_prompt


class DistillError(Exception):
    """The Distiller could not produce a usable Distillation. The message says why."""


def make_distill_model(api_key: str, llm: LLMConfig) -> Runnable:
    chat = ChatAnthropic(  # pyright: ignore[reportCallIssue]  (pydantic aliases; runtime accepts these names)
        model=llm.model, api_key=api_key, max_tokens=llm.max_tokens, effort=llm.effort
    )

    return chat.with_structured_output(
        Distillation, method="json_schema", include_raw=True
    )


def build_messages(source: ExtractedSource) -> list[tuple[str, str]]:
    # A source containing "</source>" must not be able to close our tag early.
    text = source.text.replace("</source>", "</ source>")
    return [
        ("system", distill_prompt.PROMPT),
        (
            "human",
            (
                "<source>\n"
                f"<title>{source.title or ''}</title>\n"
                f"<author>{source.author or ''}</author>\n"
                f"<url>{source.url}</url>\n"
                f"<extracted_by>{source.tool}</extracted_by>\n"
                f"<text>\n{text}\n</text>\n"
                "</source>"
            ),
        ),
    ]


def _squash(text: str) -> str:
    return " ".join(text.split()).casefold()


def keep_real_quotes(quotes: list[str], source_text: str) -> list[str]:
    """Drop any 'quote' that does not actually appear in the source (whitespace and case ignored)."""
    haystack = _squash(source_text)
    return [q for q in quotes if _squash(q.strip(' "“”')) in haystack]


async def distill(model: Runnable, source: ExtractedSource) -> Distillation:
    result = await model.ainvoke(build_messages(source))
    raw: AIMessage = result["raw"]
    stop_reason = raw.response_metadata.get("stop_reason")
    logger.info(
        f"distilled {source.url}: stop={stop_reason} usage={raw.usage_metadata}"
    )

    if stop_reason == "refusal":
        raise DistillError("Claude declined to distill this source")
    if stop_reason == "max_tokens":
        raise DistillError("the answer was cut off (max_tokens reached)")
    if result["parsing_error"] is not None or result["parsed"] is None:
        raise DistillError(
            f"Claude's answer did not match the schema: {result['parsing_error']}"
        )

    distillation: Distillation = result["parsed"]
    real_quotes = keep_real_quotes(distillation.notable_quotes, source.text)
    if len(real_quotes) < len(distillation.notable_quotes):
        logger.warning(
            f"dropped {len(distillation.notable_quotes) - len(real_quotes)} made-up quote(s)"
        )
    return distillation.model_copy(update={"notable_quotes": real_quotes})
