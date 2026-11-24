"""Turn a CaptureOutcome into the text the bot sends back in Telegram."""

from synthora.capture.pipeline import CaptureOutcome, Failed, Repeat, Succeeded
from synthora.capture.state import (
    LinkedDistilled,
    LinkedFailed,
    LinkedRead,
    LinkedSource,
)


def render(outcome: CaptureOutcome) -> str:
    """The reply for a Capture the owner sent."""
    match outcome:
        case Repeat():
            return f"🔁 already captured on {outcome.captured_on}\n\n{outcome.tldr}"
        case Failed():
            return f"{outcome.source_type or '?'}\n❌ {outcome.reason}"
        case Succeeded():
            return _succeeded(outcome)


def render_completed(outcome: Succeeded | Failed) -> str:
    """The reply when the owner's pasted text completes a Failed Capture (FR-22)."""
    if isinstance(outcome, Failed):
        return f"❌ {outcome.reason}"
    return f"✅ completed {outcome.source.url}\n\nTL;DR: {outcome.distillation.tldr}"


def _succeeded(outcome: Succeeded) -> str:
    source, d = outcome.source, outcome.distillation
    lines = [f"✅ {source.title or source.url}", "", f"TL;DR: {d.tldr}", ""]
    lines += [f"• {idea}" for idea in d.key_ideas]
    parts = ["\n".join(lines)]

    if outcome.linked:
        ok = sum(not isinstance(item, LinkedFailed) for item in outcome.linked)
        parts.append(f"🔗 Linked resources: {ok}/{len(outcome.linked)} read")
        parts += [_linked_line(item) for item in outcome.linked]
    parts += outcome.notes
    return "\n\n".join(parts)  # LiveMessage trims it to fit Telegram


def _linked_line(item: LinkedSource) -> str:
    """One short line per linked resource."""
    match item:
        case LinkedFailed():
            return f"❌ {item.url[:80]}: {item.error[:150]}"
        case LinkedDistilled():
            return f"✅ {item.source.title or item.url}\n   {item.distillation.tldr}"
        case LinkedRead():  # not reached: distill always runs after read_linked
            return f"✅ {item.source.title or item.url}"
