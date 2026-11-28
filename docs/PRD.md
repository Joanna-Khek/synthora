# Synthora — Project Requirements Document

**Status:** Draft v0.7 · **Date:** 2026-11-28 · **Owner:** Joanna Khek Cuina

Related: [CONTEXT.md](../CONTEXT.md) (glossary — terms in **bold** below are defined there) · [ADR 0004](adr/0004-rule-based-reader.md) (supersedes [ADR 0002](adr/0002-reader-agent-over-extraction-mcp-server.md)) · [ADR 0007](adr/0007-host-the-server-on-fly-io.md) · [ADR 0008](adr/0008-server-side-vault-with-obsidian-headless.md) (supersedes [ADR 0001](adr/0001-always-on-bot-with-laptop-side-obsidian-cli.md))

---

## 1. Overview

### Problem
As an AI Engineer, I save a steady stream of articles, repos, LinkedIn posts and infographics to Telegram Saved Messages, intending to revisit them. I rarely do. When I do, notes go into Notion as isolated pages, so nothing connects or compounds.

### Goal
The Synthora Telegram bot **replaces Saved Messages** as my single inbox. Every **Capture** sent to the bot should (1) teach me the key idea *immediately* via a **TL;DR** reply, and (2) be woven into an Obsidian knowledge graph where each new **Source** strengthens existing **Concepts** and **Entities** rather than becoming another orphaned note.

### Goals
- G1. Zero-effort capture: sending a link/image to the bot is the only required action, and the bot chat is the only place I save things (no more Saved Messages).
- G2. Instant learning: a TL;DR arrives in Telegram within ~1 minute of a Capture.
- G3. Compounding knowledge: every Source is linked into a typed graph of Concepts and Entities, with Concept understanding updated as Sources accumulate.
- G4. Own my data: the knowledge lives in a plain-Markdown, git-versioned Obsidian **Vault**.

### Non-goals (v1)
- Spaced-repetition quizzes (**Quizmaster** is deferred to v2).
- Migrating existing Notion notes.
- Multi-user support.

---

## 2. Scope

| In scope (v1) | Out of scope (v1) |
|---|---|
| Source Types: Web Article, GitHub Repo, Image, LinkedIn Post, YouTube Video (title + description only) | YouTube transcripts/audio, X/Twitter, podcasts, PDFs-as-files |
| Single user (owner's Telegram account only) | Multiple users / sharing |
| Instant TL;DR reply in Telegram | Quizzes, weekly reviews |
| Graph building in Obsidian (Source Notes, Concepts, Entities, Relations) | Notion import |
| Manual re-sending of old Saved Messages items to the bot as ordinary Captures | Automated bulk import of Telegram history |
| `/status` command | Logged-in LinkedIn scraping, paid scraping services |

---

## 3. Glossary (summary)

Full definitions live in `CONTEXT.md`. Key terms: **Capture**, **Source**, **Source Type**, **Failed Capture**, **Repeat Capture**, **TL;DR**, **Reader**, **Extraction Server**, **Distiller**, **Cartographer**, **Content Server**, **Quizmaster** (v2), **Vault**, **Node**, **Source Note**, **Concept**, **Entity**, **Alias**, **Relation**, **Synthesis**, **Evidence**, **My Notes**.

---

## 4. Functional requirements

### 4.1 Capture intake
- FR-1. The bot accepts Telegram messages containing URLs and/or images. Each URL and each image (including each image in an album) is a separate Capture.
- FR-1a. Any free text in the message is a **Capture Comment** and is saved in each resulting Source Note under "Why I saved this".
- FR-2. The bot only responds to the owner's Telegram user ID; all other senders are ignored.
- FR-3. The bot acknowledges receipt immediately (e.g. "Reading…") before processing.
- FR-4. The bot determines the Source Type with rules on the URL (host and path, e.g. `github.com/<owner>/<repo>`, `linkedin.com/posts/…`, `lnkd.in/p/…`, `youtube.com/watch`, `youtu.be/…`); anything else is a Web Article, and an image is an Image. No LLM is involved, and the result is final.

### 4.2 Reading (Reader + Extraction Server)
See ADR 0004.
- FR-4a. The **Reader** is rule-based, not an LLM agent. Through an MCP client, it calls exactly one tool on the Extraction Server for the Capture's Source Type, with no fallback to another tool:

  | Source Type | Tool | Backed by |
  |---|---|---|
  | Web Article | `extract_article` | trafilatura (FR-5) |
  | LinkedIn Post | `extract_with_defuddle` | Defuddle, Node CLI (FR-8) |
  | GitHub Repo | `read_github_repo` | GitHub REST API (FR-6) |
  | YouTube Video | `read_youtube_metadata` | YouTube Data API v3, `videos.list`, `part=snippet` (FR-8a) |
  | (link inside a post) | `resolve_link` | the page a `lnkd.in/…` short link points to (FR-8b) |
  | Image | vision model | not an extractor (FR-7) |

  Every extractor returns text plus metadata (title, author, published date, final URL after redirects), never raw HTML. If the extractor fails or finds no main text, the Capture becomes a Failed Capture whose message states the reason (FR-21).
- FR-4b. The **Extraction Server** is an MCP server (official `mcp` SDK) that hosts the extractors as read-only tools: the four above, plus `read_og_meta` (the page's `og:` preview tags, not used by the Reader). It holds the API keys and the shared HTTP client, so callers pass only a URL. Other MCP clients (MCP Inspector, Claude apps, future agents) can use the same tools.
- FR-4c. **Guardrails:** fetched content is untrusted data. Instructions inside a Source are never followed by the Distiller, the Cartographer or any other agent that reads Source text. All fetching happens inside the Extraction Server, through one HTTP client with the SSRF guard (NFR-3).
- FR-5. **Web Article:** extract main content with trafilatura. Fewer than ~200 characters of main text (e.g. only a cookie banner) counts as a failure.
- FR-6. **GitHub Repo:** read README, repo description, topics and file tree only (no source code).
- FR-7. **Image:** read the image with a vision-capable model; the image is stored as an attachment in the Vault. Images do not go through the extractors.
- FR-8. **LinkedIn Post:** the post's own text (author's commentary) is the Source; comments and reactions are excluded. Extract it with Defuddle from the public HTML (chosen over trafilatura in spike S1 because it strips the LinkedIn comments). `lnkd.in/p/…` share links are followed to the post. If Defuddle finds no post text (e.g. an authwall), the Capture becomes a Failed Capture asking for a screenshot or pasted text.
- FR-8a. **YouTube Video:** read only the video's title and description (metadata only; no download, transcript or audio) using the official YouTube Data API v3 (`videos.list`, `part=snippet`: title, description, channel, publish date, tags). The video ID is parsed from every URL form (`watch?v=`, `youtu.be/`, `/shorts/`, `/live/`). An empty `items` list (deleted, private or wrong ID) makes it a Failed Capture. The TL;DR and Source Note state that they are inferred from title and description alone.
- FR-8b. **Linked resources (LinkedIn Post):** many saved posts are lists of links to useful resources (e.g. "12 GitHub repos to secure AI code"). While distilling a LinkedIn Post, the Distiller also judges which of the post's links are helpful resources about the post's topic, leaving out self-promotion (the author's own product, newsletter or profile). Each link it picks is followed:
  - Only links that actually appear in the post are followed; this is checked in code, so the model cannot add or invent a URL.
  - LinkedIn's `lnkd.in/…` external links are resolved to their real destination with the Extraction Server's `resolve_link` tool (they return an interstitial page, not a redirect).
  - Each resolved URL is read and distilled like any Capture (Source Type rules, one extractor, FR-4a) and becomes its own **Linked Capture**, with its own Source Note that records the post it came from.
  - There is no product limit on how many links are followed; a safety ceiling in configuration (default 25 per post) stops a hostile or broken page from causing runaway cost, and is logged when reached.
  - A Linked Capture that fails does not fail the post; the post's reply lists it as failed with the reason.
- FR-9. The full extracted Source text is retained on the server so the Source can be re-processed without re-fetching.

### 4.3 TL;DR (Distiller agent)
- FR-10. The Distiller produces a TL;DR (short key idea) and the Source Note body (key ideas, notable quotes).
- FR-11. The TL;DR is sent as a Telegram reply to the original Capture message. For a post with Linked Captures (FR-8b), the post's TL;DR is sent first, then each linked resource's TL;DR as it finishes.

### 4.4 Graph building (Cartographer agent)
- FR-12. The Cartographer decides which Nodes (Concept, Entity) and Relations a Source adds.
- FR-13. Before creating any Node, the Cartographer searches the Vault, including Aliases, and links to an existing Node if one matches.
- FR-14. Node titles use the canonical full name; abbreviations and alternate names go in `aliases`. Because Obsidian does not resolve `[[Alias]]` through `aliases`, every wikilink targets the canonical note name; an alias may only be display text (`[[Retrieval-Augmented Generation|RAG]]`).
- FR-15. At most 3–5 **new** Concepts/Entities per Source; links to existing Nodes are unlimited. A Concept must be something plausibly encountered again in a different Source.
- FR-16. Relations use a fixed vocabulary: `is_a`, `part_of`, `uses`, `improves_on`, `contrasts_with`, `introduced_by`.
- FR-17. When a Source touches an existing Concept/Entity, the Cartographer may rewrite its **Synthesis**, appends an entry to **Evidence** (linking the Source Note), and flags contradictions with prior Evidence.
- FR-18. Agents never modify any **My Notes** section.

### 4.5 Vault writes
- FR-19. On the server (ADR 0008), the Vault is a folder of plain files (`backend: files`): search, reads, property updates and backlinks are done by reading the Markdown directly, and Obsidian Headless (`ob sync`) syncs the folder with Obsidian Sync. The `cli` backend (a laptop with the Obsidian app open) remains as a fallback; there, vault search, reads, property updates and links/backlinks queries go through the official Obsidian CLI. Note bodies that are large (over ~4 KB) or contain backslashes are written directly to the file, because the CLI's `content=` argument mangles them. The output of every CLI call is checked for errors, because the CLI exits 0 even on failure.
- FR-19a. All notes follow Obsidian Flavored Markdown. The Cartographer's system prompt includes the `obsidian-markdown` skill from [obsidianmd/obsidian-skills](https://github.com/obsidianmd/obsidian-skills) (MIT), vendored into this repo under `skills/obsidian/` and pinned to a specific commit, as the reference for the text it writes (Synthesis and Evidence); the prompt states which parts don't apply, because the tools own frontmatter, headings and sections. The `obsidian-cli` skill is vendored but not included: the agent never runs the CLI, the Content Server's tools do (ADR 0006). Every `[[link]]` in agent-written text is resolved to a Node's canonical title, or refused, in code (FR-14).
- FR-19b. Relation properties are YAML lists of quoted wikilinks (e.g. `uses: ["[[vLLM]]"]`); an unquoted `[[...]]` is parsed as a nested YAML list, not a link.
- FR-20. When `vault.git_commits` is on, the Vault is committed to git after each Capture is fully written, with a message referencing the Source, so `git revert` undoes exactly one Capture. It is opt-in: with it off (the current setting), nothing is committed and there is no per-Capture undo; Obsidian's File recovery snapshots remain, and the write rules still hold (My Notes untouched, no overwrites or deletes, Evidence append-only).

### 4.6 Failures and repeats
- FR-21. **Failed Capture:** the bot tells the user what failed and why. No automatic retry.
- FR-22. Replying to a failure message with a screenshot or pasted text completes the original Capture (the Source Note keeps the original URL). The server stores each failure message's Telegram `message_id` against its Capture so the reply can be matched.
- FR-23. **Repeat Capture:** if the URL exactly matches a previous successful Capture's URL (no normalisation), the bot replies "already captured on <date>" with the original TL;DR and does not re-process. Failed Captures do not count, so re-sending a failed URL processes it again.

### 4.7 Status
- FR-24. `/status` shows: Captures queued but not yet written to the Vault, and recent Failed Captures.

### 4.8 Write conflicts
- FR-25. If a Node the Cartographer needs to write is being edited in Obsidian at that moment, the write is deferred and retried later; the Capture stays queued until it succeeds.

### 4.9 Observability
- FR-26. When tracing is on, every LangGraph run (capture graph and runner) is traced in MLflow: agent inputs/outputs, tool calls, which extractor the Reader used and why it failed, Content Server tool calls, and Cartographer decisions. Tracing is on in local development and off on the server (`TRACING__ENABLED=false`), where MLflow no longer runs to save memory and cost (ADR 0008 amendment); there, failures are diagnosed from `fly logs` and `/status`.

### 4.10 Content Server
- FR-27. The **Content Server** is an MCP server (official `mcp` SDK) of shared tools that Synthora's content agents call while crafting knowledge content: the Distiller, the Cartographer, and new agents still to be defined (§9.11). Each agent connects as an MCP client (e.g. via `langchain-mcp-adapters`), so a tool is written once and every agent gets the same behaviour.
- FR-28. Every tool has a typed input schema and a description written for an LLM, and validates its input in code. Safety rules stay in code, not in prompts: no tool can change a My Notes section (FR-18), and tools never fetch arbitrary URLs. Its tool list is decided during build (§9.10).

---

## 5. Knowledge graph model

### 5.1 Node types and Vault layout
```
Vault/
├── Sources/       one Source Note per successful Capture
├── Concepts/      ideas and techniques (e.g. Retrieval-Augmented Generation)
├── Entities/      people, orgs, models, tools, papers (e.g. Anthropic, vLLM)
└── Attachments/   images from Image Captures
```

Optional (v1.1): a `Bases/` folder with `Sources.base` (Sources by `source_type` and `captured`) and `Concepts.base` views, written with the `obsidian-bases` skill.

### 5.2 Source Note template
Property values with wikilinks must be quoted (FR-19b).
```markdown
---
type: source
source_type: web_article   # web_article | github_repo | image | linkedin_post | youtube_video
url: https://example.com/post
captured: 2026-11-16
saved_via: "[[Best 12 GitHub repos to secure your AI-generated code]]"   # only on a Linked Capture: the post it came from
aliases: []
uses:                      # Relations as properties
  - "[[Retrieval-Augmented Generation]]"
introduced_by:
  - "[[Anthropic]]"
---
## TL;DR
## Why I saved this (Capture Comment, if any)
## Key ideas        (3–7 bullets)
## Notable quotes
## Related          (links to Concepts / Entities)
## My Notes         (user-only)
```

### 5.3 Concept / Entity template
```markdown
---
type: concept             # concept | entity
aliases:
  - RAG
is_a:
  - "[[Information Retrieval]]"
part_of: []
contrasts_with:
  - "[[Fine-Tuning]]"
---
## Synthesis        (agent may rewrite)
## Evidence         (append-only; dated, each links a Source Note; contradictions flagged)
## My Notes         (user-only)
```

---

## 6. System architecture

See ADR 0004, ADR 0007 and ADR 0008 for rationale.

```
Phone ──Capture──▶ ┌──────── Server (always on: Fly.io, 1 machine, 1 GB) ────────┐
                   │ Telegram bot (owner-only)                                  │
                   │ LangGraph "capture graph":                                 │
                   │   Source Type (rules) → Reader ─MCP─▶ Extraction Server     │
                   │   → Distiller → send TL;DR                                 │
Phone ◀──TL;DR──── │   → enqueue result (SQLite; full text retained)            │
                   │                                                            │
                   │ Runner (every 60 s), LangGraph:                            │
                   │   Cartographer ⇄ Content Server (MCP) → Vault files         │
                   │                                                            │
                   │ Obsidian Headless: ob sync --continuous                    │
                   └─────────────────────────────┬──────────────────────────────┘
                                                 │ Obsidian Sync (both ways)
                                   ┌─────────────▼─────────────┐
                                   │ Obsidian on laptop, phone │
                                   │ (no device needs to be on)│
                                   └───────────────────────────┘
```

| Component | Choice |
|---|---|
| Language | Python |
| Orchestration | LangGraph (two graphs, fixed edges between steps; a tool loop inside the Cartographer) |
| LLMs | Claude Sonnet 5.5 (Distiller, Cartographer, image reading); Claude Haiku (cleanup). Called through LangChain (`langchain-anthropic` `ChatAnthropic`); structured output via `with_structured_output(..., method="json_schema")`, because forced tool calls are rejected by current models |
| Extraction | Plain async Python functions (`httpx`) wrapping trafilatura, Defuddle (Node.js CLI, so Node is needed on the server), og-meta parsing, the GitHub REST API (optional read-only token) and the YouTube Data API v3 (API key restricted to that API, sent in a header), hosted as tools on the Extraction Server: Python MCP server (official `mcp` SDK), launched by the bot over stdio, not exposed to the network. The Reader calls them with the SDK's MCP client |
| Queue / storage | SQLite on the server with a persistent volume |
| Content Server | MCP server (official `mcp` SDK) of shared tools for the Cartographer and new content agents; launched by the runner over stdio on the server (ADR 0006, ADR 0008) |
| Vault access | Plain Markdown files on the Fly volume (`FileVault`, `backend: files`), synced by Obsidian Headless (`obsidian-headless`, pinned in `package.json`). Fallback: the Obsidian CLI (v1.12.7+) from a laptop runner (FR-19) |
| Obsidian conventions | [obsidian-skills](https://github.com/obsidianmd/obsidian-skills) (`obsidian-markdown`, `obsidian-cli`) vendored under `skills/obsidian/`, pinned to a commit, loaded into the Cartographer's system prompt |
| Hosting | Fly.io, one shared-cpu-1x machine with 1 GB, `sin` region, one Docker image started by `start.sh` (ADR 0007, ADR 0008) |
| Vault versioning | Obsidian Sync version history and Fly's daily volume snapshots; per-Capture git commits are opt-in and off (FR-20) |
| Runner | `synthora-runner`, started every 60 s inside the container; reads the local SQLite queue. A laptop runner (native Windows, pull API) remains as a fallback |
| Tracing | MLflow (LangGraph tracing for both graphs) in local development only. Off on the server, where MLflow no longer runs (ADR 0008 amendment) |

---

## 7. Non-functional requirements

- NFR-1. **Reliability:** no Capture is silently lost. The server must be up continuously (Telegram discards undelivered updates after 24h).
- NFR-2. **Latency:** TL;DR within ~60s of a Capture for typical articles.
- NFR-3. **Security:** bot restricted to owner's user ID; laptop↔server pull API authenticated with a secret token over HTTPS; secrets kept out of the repo (including the YouTube API key, which is restricted to the YouTube Data API v3 in Google Cloud). All fetching (inside the Extraction Server) allows only `http`/`https` URLs and refuses private, loopback and link-local addresses (including after redirects), so a URL can't reach internal services on the machine or its private network. API keys are sent in headers, never in URLs, so they don't leak into error messages, logs or traces.
- NFR-4. **Safety of the Vault:** every agent write is reversible, through Obsidian Sync's version history (and git, when `vault.git_commits` is on); My Notes never touched.
- NFR-5. **Cost:** hosting target ≤ $5/mo (the owner's Fly credit). The 1 GB machine (about $6.70/mo at Fly's US prices, a little more in `sin`) is still slightly over; the ADR 0008 amendment lists the options to get under it. Obsidian Sync is a separate cost. LLM spend target to be confirmed (see §9).
- NFR-6. **Compliance:** no logged-in scraping or ToS-violating access to LinkedIn.

---

## 8. Risks, spikes and success criteria

### Spikes (do first)
| # | Spike | Pass condition |
|---|---|---|
| S1 | trafilatura vs Defuddle on real saved LinkedIn post links | **Done (2026-11-16):** Defuddle works better because it strips the comments, and it is now used for LinkedIn (FR-8). Still to confirm: does it return full post text for most links, or does the screenshot fallback become the primary path? |
| S2 | Obsidian CLI callable from a background process on Windows while Obsidian runs | create / append / property:set / search / backlinks all work programmatically; failures are detectable from output; >4 KB bodies are handled by direct file writes |

### Risks
- LinkedIn returns authwall/preview only → more manual screenshots.
- Cartographer produces duplicate or noisy Nodes → mitigated by search-before-create, Aliases, node cap, and the evaluation set.
- Synthesis rewrites lose nuance → mitigated by append-only Evidence and Obsidian Sync's version history.
- Obsidian Headless is young (0.0.x) and its interface may change → pinned in `package.json`; upgrade deliberately. (The Obsidian CLI, used only by the laptop fallback, is also new.)
- On the server, `FileVault` reimplements search, aliases and backlinks by reading files → it must resolve links the way Obsidian does (FR-13, FR-14); covered by tests.
- An edit still being typed on a device and not yet synced is invisible to the busy-note check (FR-25) → Obsidian Sync's conflict handling is the last line of defence.
- Defuddle is a JavaScript library, so it is called as a subprocess (Node CLI) → adds Node to the server image; pin its version.
- No fallback extractor: a page that its one extractor mangles or can't read becomes a Failed Capture → watch the Failed Capture rate per Source Type (`/status`, `fly logs`, MLflow when tracing locally); add a fallback for a Source Type only if failures are frequent.
- Prompt injection: a fetched page may contain text addressed to the Distiller or the Cartographer → mitigated by FR-4c (Source text is data, never instructions) and the SSRF guard in NFR-3.
- obsidian-skills changes upstream → mitigated by vendoring and pinning a commit; update deliberately.
- The bot, the Extraction Server, the runner and `ob sync` share one 1 GB machine (about 720 MB at rest, ADR 0008 amendment) → the peak while the runner or Defuddle runs is still to be measured; watch memory if more processes are added.
- No production traces: with MLflow off on the server, a bad Cartographer decision in production can only be investigated from logs and the notes it wrote.

### Success criteria
- An evaluation set of ~15 real saved links across all Source Types is processed end-to-end, and a manual review of the resulting graph finds no duplicate Concepts and sensible Relations.
- After 4 weeks of daily use, ≥90% of Captures reach the Vault and the owner actually reads TL;DRs (self-reported).

---

## 9. Gaps and open questions

### Resolved
- LinkedIn extraction → Defuddle rather than trafilatura, because it strips comments (FR-8, spike S1).
- YouTube metadata → official YouTube Data API v3, not yt-dlp (FR-8a). It is official and stable, works from data-centre IPs (yt-dlp is often blocked there) and stays within YouTube's terms. Quota: 10,000 units/day by default; one lookup costs 1 unit.
- Extraction architecture → rule-based Reader: Source Type → one extractor, no fallback (FR-4, FR-4a, ADR 0004). The extractors are hosted on the Extraction MCP Server and called by rules through an MCP client. Supersedes ADR 0002's Reader agent.
- Capture Comment → saved under "Why I saved this" (FR-1a).
- Multiple items per message → several Captures (FR-1).
- Repeat detection → exact URL match only (FR-23).
- Note being edited while the Cartographer writes → defer and retry later (FR-25).
- Where the Cartographer runs → on the server, writing vault files that Obsidian Headless syncs (ADR 0008, supersedes ADR 0001). The laptop runner (native Windows) remains as a fallback.
- Hosting → Fly.io, one 1 GB machine (ADR 0007, ADR 0008).
- Observability → MLflow (FR-26) in local development; off on the server to save cost (ADR 0008 amendment).
- Hand-edited Synthesis → the Cartographer may overwrite it; prior versions remain in Obsidian Sync's version history. Permanent edits belong in My Notes.

### Deferred
- **LLM budget:** monthly spend cap and behaviour when reached.
- **Image reading (FR-7):** images are rarely saved, so reading them is deferred until after v1's core pipeline works. Until then, the bot acknowledges an image but does not read it, and it is not written to the Vault.

### Decide during build
1. **TL;DR format:** length (1 line vs 3 bullets) and whether it names the Concepts it will link to.
2. **Relation vocabulary growth:** can the list be extended, and by whom (you only, or agent-proposed)?
3. ~~**Vault git remote**~~ → decided for now: local only, no git (`vault.git_commits: false`). Obsidian Sync was then chosen (ADR 0008) and now provides backup and phone access, so git stays off unless per-Capture undo becomes worth having. Original question: pushed to a private remote for backup/phone access, or local only?
4. **Data retention:** how long the server keeps full Source text and images.
5. **Non-English Sources:** translate, or keep the original language?
6. **Undo:** a way to delete a Capture and its Nodes from Telegram (e.g. `/undo`)?
7. ~~**YouTube metadata method**~~ → resolved: YouTube Data API v3 (see Resolved).
8. ~~**Edit detection (FR-25)**~~ → decided: a note is treated as in use if it is open in an Obsidian tab (CLI `tabs`; the CLI can't see unsaved typing, and only an open note can hold any) or its file changed within `vault.busy_seconds` (120 s; catches autosave and Sync), unless that change was Synthora's own write. `Vault.edit` raises `NoteBusy`; the run ends `deferred`, and the Capture stays queued and is retried, never forced. On the server (`FileVault`) there are no tabs, so only the modification-time check applies (ADR 0008).
9. ~~**Obsidian Headless**~~ → decided: the server owns the Vault as plain files and `ob sync --continuous` syncs it through Obsidian Sync (ADR 0008). Original question: obsidian-skills includes an `obsidian-headless` skill for syncing vaults without the desktop app. If it allows reliable server-side Vault writes, revisit ADR 0001's laptop split.
10. ~~**Content Server tools**~~ → decided: read tools plus narrow, guarded write tools (ADR 0006). Original question: which tools it exposes. Candidates: get a Source's full text and metadata, search the Vault by title or Alias, read a Node, list backlinks, check the Relation vocabulary. Decide whether Vault-writing tools belong in it or stay with the Cartographer.
11. **New content agents:** which agents join the Distiller and the Cartographer (e.g. a critic or fact-checker), what each one does, and where it runs. Add each to CONTEXT.md once defined.
12. ~~**Content Server host and transport**~~ → decided: over stdio (ADR 0006), now on the server, launched by the runner (ADR 0008; ADR 0006 originally put it on the laptop). Original question: over stdio, the server runs on whichever machine launches it, so the VPS and the laptop could each start their own copy. But Vault tools only work on the laptop (ADR 0001) and SQLite tools only on the VPS. Options: one server per side with different tools, or one remote server over streamable HTTP with auth. Record the choice in an ADR.
13. ~~**One MCP server or two**~~ → decided: two separate servers (ADR 0006). Original question: keep the Extraction Server and the Content Server separate (default: they may run on different machines), or merge them into one server.
