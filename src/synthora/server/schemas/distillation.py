from pydantic import BaseModel, ConfigDict, Field


class Distillation(BaseModel):
    """What the Distiller produces for one Source.

    The field descriptions are sent to Claude as part of the JSON schema,
    so they double as instructions for each field.
    """

    model_config = ConfigDict(frozen=True)

    tldr: str = Field(
        description="The single most useful idea of the source, in one sentence of at most 100 words"
    )

    key_ideas: list[str] = Field(
        description="3 to 7 key ideas, one sentence each, most important first."
    )

    notable_quotes: list[str] = Field(
        description="Up to 3 short passages copied word for word from the source. Empty if none stand out."
    )
