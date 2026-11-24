"""Load vendored skills (FR-19a) as text for a system prompt."""

from synthora.config import ROOT

SKILLS_DIR = ROOT / "skills" / "obsidian"


def load_skill(name: str, *, references: tuple[str, ...] = ()) -> str:
    """A skill's SKILL.md, plus any of its reference files, joined for a prompt."""
    skill_dir = SKILLS_DIR / name
    parts = [(skill_dir / "SKILL.md").read_text(encoding="utf-8")]
    parts += [
        (skill_dir / "references" / ref).read_text(encoding="utf-8")
        for ref in references
    ]
    return "\n\n".join(parts)
