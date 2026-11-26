# Synthora

A personal learning system for an AI Engineer: links and images sent to a Telegram bot (which replaces Telegram Saved Messages as the single inbox) are turned into an immediate takeaway and woven into an Obsidian knowledge graph so knowledge compounds instead of being forgotten.

## Language

### Intake

**Capture**:
One thing to learn from (a link or an image) sent to the Synthora bot; a message with several links or an album of images yields several Captures. Old saved items are re-sent manually and are ordinary Captures.
_Avoid_: Save, bookmark, saved message, post

**Capture Comment**:
Free text the user sends alongside a link or image explaining why it was saved; kept with the Source Note.
_Avoid_: Caption, annotation

**Source**:
The content a Capture points at — the thing being learned from.
_Avoid_: Link, article, content, resource

**Source Type**:
The kind of Source, which determines how it is read: GitHub Repo, Web Article, Image, LinkedIn Post, or YouTube Video.
_Avoid_: Format, media type

**YouTube Video**:
A Source Type understood only from the video's title and description; the video itself is never watched or transcribed.

**LinkedIn Post**:
A Source Type where the post's own text (the author's commentary) is the Source, not any link it contains.

**Failed Capture**:
A Capture whose Source could not be read or processed; the user is told its status and it is not retried automatically. The user may complete it by replying with a screenshot or the pasted text.
_Avoid_: Error, dropped capture

**Linked Capture**:
A Capture created from a helpful resource link inside a saved LinkedIn post, rather than sent by the owner directly. It gets its own Source Note, which records the post it came from.
_Avoid_: Child capture, sub-capture

**Repeat Capture**:
A Capture whose URL exactly matches an earlier Capture's URL; it is not processed again — the bot returns the original capture date and TL;DR.
_Avoid_: Duplicate

### Agents

**Reader**:
The rule-based step (not an LLM agent) that turns a Capture into the Source's readable content by calling the one Extraction Server tool for its Source Type.
_Avoid_: Scraper, fetcher

**Extraction Server**:
The MCP server that hosts Synthora's read-only extractors (trafilatura, Defuddle, og-meta, GitHub, YouTube metadata) as tools, for the Reader and any other MCP client.
_Avoid_: Scraper server, MCP (on its own)

**Distiller**:
The agent that produces the TL;DR and the Source Note's body.
_Avoid_: Summarizer, writer

**Cartographer**:
The agent that decides which Nodes and Relations a Source adds to the Vault and writes them.
_Avoid_: Linker, writer agent

**Content Server**:
The MCP server of shared tools that the content agents (Distiller, Cartographer and future agents) call while crafting knowledge content.
_Avoid_: Tool server, MCP (on its own)

**Quizmaster** (v2):
The agent that writes Quizzes and grades the user's answers.
_Avoid_: Tutor, examiner

### Knowledge graph

**Vault**:
The Obsidian vault that holds Synthora's knowledge graph; starts empty.
_Avoid_: Notebook, workspace, knowledge base

**Node**:
A note in the Vault; always exactly one of Source Note, Concept, or Entity.
_Avoid_: Page, document

**Source Note**:
The Node representing one Source; exactly one per successful Capture.
_Avoid_: Article note, summary note

**Concept**:
A Node for an idea or technique (e.g. Retrieval-Augmented Generation, LoRA) that many Sources can touch.
_Avoid_: Topic, idea, technique, tag

**Entity**:
A Node for a named thing — person, organisation, model, tool, or paper (e.g. Anthropic, vLLM).
_Avoid_: Named entity, actor

**Alias**:
An alternative name (often an abbreviation) under which a Node is also found; a Node's title is always its canonical full name.
_Avoid_: Synonym, nickname

**Relation**:
A typed, directed edge between two Nodes drawn from a small fixed vocabulary (e.g. `is_a`, `part_of`, `uses`, `improves_on`, `contrasts_with`, `introduced_by`).
_Avoid_: Link, edge, connection

**Synthesis**:
The section of a Concept or Entity that states current understanding; agents may rewrite it as new Sources arrive, including over the user's own edits.
_Avoid_: Summary, overview

**Evidence**:
The append-only section of a Concept or Entity listing what each Source contributed, each entry linking to its Source Note; contradictions are flagged here.
_Avoid_: References, log

**My Notes**:
The section of any Node written only by the user; agents never modify it.
_Avoid_: Personal notes, comments

### Learning

**TL;DR**:
The short key-idea reply the bot sends back in Telegram immediately after a Capture, so learning happens at the moment of saving.
_Avoid_: Summary, digest, abstract

**Quiz** (v2):
A recall question about a Source sent in Telegram on a spaced schedule (1 day, 1 week, 1 month after Capture), answered by reply and graded by the Quizmaster.
_Avoid_: Test, flashcard, review
