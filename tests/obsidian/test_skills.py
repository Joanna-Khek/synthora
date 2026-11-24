import re

import pytest

from synthora.obsidian.skills import SKILLS_DIR, load_skill


def test_license_is_kept_next_to_the_skills():
    assert "MIT License" in (SKILLS_DIR / "LICENSE").read_text(encoding="utf-8")


def test_vendored_commit_is_a_full_sha():
    text = (SKILLS_DIR / "VENDORED.md").read_text(encoding="utf-8")
    assert re.search(r"Commit: [0-9a-f]{40}\b", text)  # a SHA, never a branch name


@pytest.mark.parametrize("name", ["obsidian-markdown", "obsidian-cli"])
def test_each_skill_loads(name):
    assert f"name: {name}" in load_skill(name)


def test_references_are_appended():
    plain = load_skill("obsidian-markdown")
    with_properties = load_skill("obsidian-markdown", references=("PROPERTIES.md",))
    assert with_properties.startswith(plain)
    assert len(with_properties) > len(plain)
