import subprocess

import pytest

from synthora.obsidian.vault import ObsidianError, Vault
from synthora.obsidian.sections import MyNotesViolation, replace_section

NOTE = "## Synthesis\nold\n\n## My Notes\nmine\n"


def _param(args: list[str], key: str) -> str:
    return next(a.split("=", 1)[1] for a in args if a.startswith(key + "="))


def fake_cli(monkeypatch, tmp_path, outputs: dict[str, str]) -> list[list[str]]:
    """Replace the real CLI. Canned stdout per command; every call is recorded.

    By default `create` reports success and `file` mirrors the disk, like a fully indexed vault.
    """
    outputs.setdefault("vault", f"name\ttest\npath\t{tmp_path}\n")
    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(args[2:])
        command = args[2]
        if command in outputs:
            stdout = outputs[command]
        elif command == "create":
            stdout = f"Created: {_param(args, 'path')}\n"
        elif command == "file":
            path = _param(args, "path")
            found = (tmp_path / path).exists()
            stdout = (
                f"path\t{path}\n" if found else f'Error: File "{path}" not found.\n'
            )
        else:
            stdout = ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout, stderr="")

    monkeypatch.setattr(
        "synthora.obsidian.vault.shutil.which", lambda name: "obsidian.com"
    )
    monkeypatch.setattr("synthora.obsidian.vault.subprocess.run", run)
    return calls


def test_error_output_raises_even_with_exit_0(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"read": 'Error: File "x.md" not found.\n'})
    with pytest.raises(ObsidianError, match="not found"):
        Vault("test", tmp_path).read("x.md")


def test_unknown_vault_raises(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"vault": "Vault not found.\n"})
    with pytest.raises(ObsidianError, match="Vault not found"):
        Vault("test", tmp_path)


def test_vault_with_the_same_name_elsewhere_is_refused(monkeypatch, tmp_path):
    fake_cli(
        monkeypatch, tmp_path, {"vault": "name\ttest\npath\tH:\\My Drive\\Synthora\n"}
    )
    with pytest.raises(ObsidianError, match="share this name"):
        Vault("test", tmp_path)


def test_small_body_goes_through_the_cli_with_escaped_newlines(monkeypatch, tmp_path):
    calls = fake_cli(monkeypatch, tmp_path, {})
    Vault("test", tmp_path).create("Sources/a.md", "line 1\nline 2")
    assert calls[-1] == ["create", "path=Sources/a.md", "content=line 1\\nline 2"]


def test_body_with_a_backslash_is_written_directly(monkeypatch, tmp_path):
    calls = fake_cli(monkeypatch, tmp_path, {})
    Vault("test", tmp_path).create("Sources/a.md", r"see C:\Users")
    assert (tmp_path / "Sources" / "a.md").read_text(
        encoding="utf-8"
    ) == r"see C:\Users"
    assert not any(c[0] == "create" for c in calls)  # bypassed the CLI's create


def test_large_body_is_written_directly(monkeypatch, tmp_path):
    calls = fake_cli(monkeypatch, tmp_path, {})
    Vault("test", tmp_path).create("Sources/big.md", "x" * 5000)
    assert (tmp_path / "Sources" / "big.md").stat().st_size == 5000
    assert not any(c[0] == "create" for c in calls)  # bypassed the CLI's create


def test_create_never_overwrites(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {})
    (tmp_path / "a.md").write_text("mine", encoding="utf-8")
    with pytest.raises(FileExistsError):
        Vault("test", tmp_path).create("a.md", "new")


@pytest.mark.parametrize("path", ["../escape.md", "Sources/../../escape.md"])
def test_paths_outside_the_vault_are_refused(monkeypatch, tmp_path, path):
    fake_cli(monkeypatch, tmp_path, {})
    with pytest.raises(ObsidianError, match="outside"):
        Vault("test", tmp_path).create(path, "x")


def test_create_refuses_a_silent_rename(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"create": "Created: Sources/a 1.md\n"})
    with pytest.raises(ObsidianError, match="a 1.md"):
        Vault("test", tmp_path).create("Sources/a.md", "x")


def test_create_refuses_a_note_only_the_index_still_knows(monkeypatch, tmp_path):
    # the probe's bug: deleted from disk, but Obsidian hasn't noticed yet
    fake_cli(monkeypatch, tmp_path, {"file": "path\tSources/a.md\n"})
    with pytest.raises(FileExistsError):
        Vault("test", tmp_path).create("Sources/a.md", "x")


def test_search_with_no_matches_is_an_empty_list(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"search": "No matches found.\n"})
    assert Vault("test", tmp_path).search("zzz") == []


def test_search_returns_paths(monkeypatch, tmp_path):
    fake_cli(
        monkeypatch, tmp_path, {"search": '["Sources/Post.md","Concepts/RAG.md"]\n'}
    )
    assert Vault("test", tmp_path).search("RAG") == [
        "Sources/Post.md",
        "Concepts/RAG.md",
    ]


def test_aliases_map_to_their_note(monkeypatch, tmp_path):
    out = "Retrieval-Augmented Generation\tConcepts/RAG.md\nRAG\tConcepts/RAG.md\n"
    fake_cli(monkeypatch, tmp_path, {"aliases": out})
    assert Vault("test", tmp_path).aliases() == {
        "Retrieval-Augmented Generation": "Concepts/RAG.md",
        "RAG": "Concepts/RAG.md",
    }


def test_no_backlinks_is_an_empty_list(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"backlinks": "No backlinks found.\n"})
    assert Vault("test", tmp_path).backlinks("Concepts/RAG.md") == []


def test_backlinks_returns_paths(monkeypatch, tmp_path):
    fake_cli(
        monkeypatch,
        tmp_path,
        {"backlinks": '[\n  {\n    "file": "Sources/Post.md"\n  }\n]\n'},
    )
    assert Vault("test", tmp_path).backlinks("Concepts/RAG.md") == ["Sources/Post.md"]


def test_links_returns_paths_or_nothing(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"links": "No links found.\n"})
    assert Vault("test", tmp_path).links("Concepts/RAG.md") == []


def test_list_property_is_sent_as_a_json_array(monkeypatch, tmp_path):
    calls = fake_cli(monkeypatch, tmp_path, {"property:set": 'Set uses: ["[[RAG]]"]\n'})
    Vault("test", tmp_path).set_property("Sources/a.md", "uses", ["[[RAG]]"])
    assert calls[-1] == [
        "property:set",
        "path=Sources/a.md",
        "name=uses",
        'value=["[[RAG]]"]',
        "type=list",
    ]


def test_property_set_failure_is_detected(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {"property:set": "something unexpected\n"})
    with pytest.raises(ObsidianError):
        Vault("test", tmp_path).set_property("Sources/a.md", "type", "source")


def test_edit_writes_a_safe_change(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {})
    (tmp_path / "n.md").write_text(NOTE, encoding="utf-8")
    Vault("test", tmp_path).edit(
        "n.md", lambda t: replace_section(t, "## Synthesis", "new")
    )
    assert (tmp_path / "n.md").read_text(
        encoding="utf-8"
    ) == "## Synthesis\nnew\n\n## My Notes\nmine\n"


def test_edit_refuses_to_touch_my_notes(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {})
    (tmp_path / "n.md").write_text(NOTE, encoding="utf-8")
    with pytest.raises(MyNotesViolation):
        Vault("test", tmp_path).edit("n.md", lambda t: t + "agent scribble\n")
    assert (tmp_path / "n.md").read_text(encoding="utf-8") == NOTE  # nothing written


def test_edit_refuses_if_the_note_changed_meanwhile(monkeypatch, tmp_path):
    fake_cli(monkeypatch, tmp_path, {})
    path = tmp_path / "n.md"
    path.write_text(NOTE, encoding="utf-8")

    def you_type_while_the_agent_works(text: str) -> str:
        path.write_text(text.replace("old", "your edit"), encoding="utf-8")
        return replace_section(text, "## Synthesis", "new")

    with pytest.raises(ObsidianError, match="changed"):
        Vault("test", tmp_path).edit("n.md", you_type_while_the_agent_works)
    assert "your edit" in path.read_text(encoding="utf-8")  # your edit survived
