# Memory-MCP-Knowledge for AI Agents: Usage Rules & Learning Protocol

> **Purpose:** This document captures the rules and the learning protocol for operating a cross-session memory MCP server in daily agent work. It is designed to be consumed by any AI agent (Qwen Code, Kimi Code, Claude Code, etc.) — so you don't have to take the same detours we took. It complements the setup package (`memory-mcp-setup.zip`) with the part that took the longest to get right: **when to read, when to write, and how the rules evolved.**
>
> **Last updated:** 2026-09-20
> **Source:** Michael Logies, ~7 weeks of practical daily use with a local Memory-MCP server (SQLite + sqlite-vec + FTS5 + ONNX embeddings).
> **License:** CC0 — use freely, no attribution required.

---

## 1. The server in one paragraph

A lean, fully offline MCP server (`memory_store`, `memory_update`, `memory_search`, `memory_get`, `memory_recent`, `memory_delete`, `memory_status`) backed by SQLite with sqlite-vec (vector search) and FTS5 (BM25 full text), fused via Reciprocal Rank Fusion (RRF, K=60; the keyword arm counts 0.85× since 2026-09-20). The first line of every note is treated as a search anchor — weighted 3× in BM25 and prepended to the embedding — and a maintained everyday-word→domain-term dictionary (`synonyms.json`) invisibly expands queries. `memory_search` returns the best text snippet per hit and accepts an optional token budget. Embeddings: quantized `Xenova/multilingual-e5-small` ONNX (112.8 MB, 384 dim), CPU-only. Query prefix `query: `, passage prefix `passage: `. Temporal decay is **disabled** (λ=0) — see §4 for why. Setup: `memory-mcp-setup.zip` on the same page.

---

## 2. The usage rules (the core)

These rules are the distilled result of daily use. They are deliberately aggressive — every one of them exists because its absence caused a real mistake.

### R1 — Memory-first: search BEFORE you think

**Call `memory_search` as the first step of every question or task — before finishing a thought, before answering, before acting.** Do not trust general knowledge or intuition first; the memory contains checked, experience-based workflows. General knowledge is only the fallback when the memory finds nothing.

*Why this rule is absolute:* with the softer version ("check memory when relevant"), the agent kept reinventing workflows, repeating past mistakes (e.g. running `readfile()` on a SQLite database with BLOBs instead of using the memorized parameterized-query workflow, suggesting a destructive index rebuild that was already forbidden, listing deactivated plugins as active). The rule was escalated to "no exceptions" after each of these incidents.

### R2 — Store after every knowledge-expanding action

After every completed task that produced new knowledge (new rule, new file, new workflow, discovered bug, confirmed approach, user correction), store it **immediately** — also the seemingly small details. What feels obvious today is invisible in three weeks.

### R3 — Deduplicate before storing, update instead of stacking

Before `memory_store`: search for existing entries on the same topic. If one exists and is outdated, **overwrite or update it** — never stack duplicates. Duplicate entries poison the ranking and waste context on retrieval.

### R4 — Read before starting work

Before beginning a task, run targeted searches for that task's keywords. Don't rely on whatever the session-start check surfaced; search for the *specific* topic (server names, tool names, workflow keywords).

### R5 — Close the session loop

At the end of a session, review the most recent entries (`memory_recent`) and backfill anything that was learned but never stored.

### R6 — Tag discipline

Every entry gets content tags **plus** role tags: `rolle:forschung` (research/Zotero), `rolle:system` (MCP, Linux, setup, tooling), `rolle:web` (website, Publii, HTML/CSS). Role tags make targeted retrieval possible; content tags without role tags make the memory searchable only by luck.

### R7 — Check all memory servers at session start

At every session start, verify every MCP server is reachable and functional (for us: the memory server, the Zotero semantic-index server, and the Zoteus server) — before the first task, not on first failure. A dead server discovered mid-task wastes the whole session.

### R8 — What NOT to store

- **API keys, tokens, secrets — never.** Content filters may block the entire request when a key pattern appears in context loaded from memory. Store keys in dedicated config files only.
- **Personal and patient-related data — never unprotected.** Names, birth dates, diagnoses, case numbers belong neither in the memory nor in the LLM context. If a case discussion is unavoidable, pseudonymize or encrypt first. The memory sits in a database with backups and long retention — there is no access-control framework around it the way there is for a patient record.
- Point-in-time observations without a date. Always store *when* a fact was true.
- Ephemeral task state, raw error transcripts, anything derivable from the code/repo.
- Anything the user asked you to forget.

### R9 — Markdown files remain authoritative for permanent rules

The memory server complements `AGENTS.md`/`MEMORY.md`/`WORKLOG.md`, it does not replace them. Durable rules and technical details also live in the Markdown files; when in doubt, **store in both**. The memory wins for semi-structured, incident-driven knowledge; Markdown wins for anything that must survive even a broken memory database.

### R10 — Review, don't hoard

Quarterly: review entries, delete or update stale ones. Outdated knowledge that ranks well is worse than no knowledge — it actively misleads.

### R11 — Report missing tools immediately — even when a workaround exists

If a CLI tool is missing ("command not found") or fails and the task can still be completed via a detour (e.g. a Python script), the user must **always** learn about it: which tool was missing, what it was needed for, whether installing it is worthwhile. Silent compensation means lost information — the installation decision belongs to the user. **Keep working with the fallback, deliver the report in parallel.**

### R12 — Back up claims: no unverified assertions

Commands, features and version behavior are asserted as fact only after verification (documentation, source code, live test) — and against the version actually installed. Mark anything unverified as such ("please check this yourself") instead of stating it as certainty. A verified command also records **which source** (tool, version, date) it was verified against.

### R13 — Source statements for data tables and comparisons

Every comparison or analysis table carries its sources: measurement date, tools/versions involved, and where the numbers came from (own benchmark, documentation, external page). A table without a source line invites stale numbers to be reused as the current state.

### R14 — One entry = one topic, and it stays short

**Rule and knowledge entries stay under ~600 characters.** No multi-topic giant notes: an entry covering five topics will not be found reliably for any of them — the ranking spreads relevance, and retrieval wastes context on the four topics you did not need.

*Background:* a full-text analysis of our own database (2026-09-19) showed that more than half of all entries (226 of 446) exceeded one embedding chunk — almost exclusively session handoffs and protocols, hardly any rules. Precisely this long tail degraded the ranking of the actual rule entries.

### R15 — Long knowledge lives in a file; the memory holds the pointer

Session handoffs, detailed protocols and document texts belong in a Markdown file. The memory holds only the **short version (≤ ~600 characters) plus the file path**. When a hit is retrieved, the session loads the file on demand — the memory stays an index, not a warehouse.

*Background:* same analysis. After converting 28 oversized entries to files, hit quality improved in both measurement baskets (more hits at positions 1–3), even though two entries dropped out of the top 5.

### R16 — The first line is the search anchor

The **first line of every entry is weighted preferentially at search time** (a separate index column with 3× BM25 weight, prepended to the embedding) — maintain it as a title-like line containing the terms you would search for later: domain terms **and** the user's everyday words.

*Background:* fact-key studies measure +9.4 % recall from title-like key lines; in our own system the anchor boost delivered the largest single gains of the tuning round (the embedding prefix alone lifted top-1 hits by 6.5 percentage points).

---

## 3. What to store (and what makes an entry good)

Good entries have: **the rule or fact**, **why it exists** (the incident or reason), **how to apply it** (the concrete command or workflow), and **the date**. The "why" is what lets a future session judge edge cases instead of blindly following a stale rule.

Store: workflows that took iteration, tool quirks and gotchas, user corrections (they are the highest-value entries), repair procedures, benchmark results with dates, decisions with their rejected alternatives.

---

## 4. The learning timeline (how the rules actually evolved)

This section documents the **fixed learning rules and their dates** — because the meta-lesson is that a working memory discipline is not designed in one sitting; it converges over weeks, driven by incidents.

| Date | Event | Rule that came out of it |
|---|---|---|
| 2026-08-09 | Server v1 set up (all-MiniLM-L6-v2, 22 MB model) | Basic rule: store relevant information by default; permanent rules additionally in Markdown files; when in doubt, store twice |
| 2026-08-10 | Alternatives evaluated (Mem0, pure SQLite, PyTorch — rejected for cloud lock-in, missing semantics, and 1.4 GB venv respectively) | ONNX route confirmed; expectations documented: memory complements the Markdown files |
| 2026-08-15 | First care rules formalized | Store after every task, search before work, `memory_recent` at session end, keep WORKLOG.md in sync, quarterly review, role tags |
| 2026-08-24 | Server v2: `multilingual-e5-small` (112.8 MB), hybrid search (FTS5 + vector + RRF) | German queries finally hit reliably; exact terms, proper nouns and version numbers findable without semantic proximity |
| 2026-08-25 | **Temporal decay disabled** (λ=0.005 → 0) | Time-stable knowledge (calculation rules, workflows) was being penalized for its age. Relevance now comes from hybrid ranking; stale knowledge is flagged manually instead |
| 2026-08-26 | Session-start checklist extended | Check ALL memory servers at session start — a server forgotten from the checklist was only discovered on first use |
| 2026-08-26 | Two Zotero incidents: a conditional deletion order ("if X, then Y") was executed with the condition unmet; an irreversible deletion happened without asking first | Follow conditional instructions literally — execute "if X, then Y" only when X is verifiably true, otherwise report back; before any irreversible action (delete, overwrite), always confirm, even when the condition seems met |
| 2026-08-27 | **Memory-first escalated to an absolute rule** | After repeated violations (reinvented workflows, ignored memorized rules), the rule became: search first, no exceptions, with concrete past violations documented as negative examples directly in the agent's instruction file |
| 2026-08-28 | `memory_search` crash fixed (NoneType error on fulltext-only hits), vector-repair workflow established | Operational gotchas captured (see §5); benchmark showed the repaired index at full quality |
| 2026-08-29 | Duplicates consolidated (several memory entries described the same rule twice); fulltext mandate formulated | R3 enforced as routine: consolidating instead of stacking is checked regularly; when Zotero research has fulltexts available, they must be evaluated — no answers from abstracts, search hits, or prior knowledge alone |
| 2026-08-30 | Two Publii incidents: a replaced download file was copied only to `input/media` (Publii syncs `output/media` → the live page promised the new version while the link served the old one); a shortcut via the rendered website was sought instead of the database | Replaced media files belong in **input, output AND preview** (`media/` trees) — Publii syncs `output/media` to the server, not `input/media`; website texts are always changed in the database (`posts.text`), never in rendered files |
| 2026-08-31 | A memory note was lost during an overwrite — response: **hourly SQLite snapshots** (`VACUUM INTO`, only on change, 8-day retention, independent cron) + a `verify.sh` check routine in the setup package | Back up first, write second after any DB change; memory databases are backed up via `VACUUM INTO` (a consistent snapshot without the WAL trap, never a raw `cp`); the check routine (`verify.sh`) reports venv, packages, model, database, backup cron and disk space as a Markdown report |
| 2026-09-02 | A new entry was stacked via `memory_store` on top of an existing one instead of updating it | Before writing, check first: use `memory_update` instead of `memory_store` when an entry already covers the topic — an update without content changes only tags/source; a content change re-embeds |
| 2026-09-03 | **Chunked embedding:** long notes (server v4) are split at Markdown boundaries into ≤450-token chunks (50-token overlap) and fully embedded; search aggregates chunk hits per note | Semantic hits deep inside long notes are now visible (before: only the first 512 tokens were represented in the vector); `memory_status` reports `chunk_entries` |
| 2026-09-08 | Index incident: an old watchdog cron with auto-resume logic restarted the index after every crash; in parallel, a stale session from an earlier rebuild era kept firing outdated build instructions — the production index was lost twice | **One-channel principle:** exactly ONE automated channel for index actions; no auto-resume after errors (resuming is a deliberate user decision); status gate before every start; index actions only from one known, current session |
| 2026-09-12 | An MCP server worked in one interface only (CLI fine, desktop client broken): the desktop wrapper exported `PYTHONHOME`/`LD_LIBRARY_PATH`, so the server loaded the wrong `libsqlite3` and died with a misleading error message | "Works in only one interface" is diagnosed via `/proc/<pid>/environ` and `/proc/<pid>/maps` of the involved server processes; clean up wrapper environment variables before blaming server code or the database |
| 2026-09-19 | **Phase-0 analysis:** 226 of 446 entries (over 50 %) exceeded one embedding chunk; the long tail came from session handoffs, not rules | Write rules R14–R16: one entry = one topic, ≤ ~600 characters, long content as short version + file pointer, first line as a maintained search anchor |
| 2026-09-19 to 20 | **Consolidation:** 39 outdated notes deleted, 28 oversized notes reduced to short version + file pointer (446 → 403 notes) | R10 executed consistently for the first time; measurement confirmed: pointerization improves ranking in both baskets, one single marginal hit was lost |
| 2026-09-20 | **Search fine-tuning measured:** keyword arm 0.85× in the fusion, anchor boost, everyday→domain dictionary (query expansion), chunk overlap removed | Vocabulary gaps (everyday language ↔ domain terms) are the dominant failure mode of search — and fixable with a maintained dictionary: the hardest basket rose from 68.4 % to 89.2 % (Recall@5) |

**Meta-lessons from the timeline:**

1. **Rules without violations documented don't stick.** The memory-first rule only became reliable when past violations were written down as concrete negative examples.
2. **Each incident produced exactly one rule.** Trying to define the whole discipline upfront produced rules that were ignored; rules born from real mistakes stuck.
3. **User corrections are the highest-value entries.** Every "why did you do X instead of the memorized way?" became a permanent rule the same day.
4. **The model/hybrid upgrade (v2) was the turning point for German content** — before that, the memory was searched less because it was trusted less.

---

## 5. Operational gotchas (28.8.–16.9.2026, all battle-tested)

- **Repaired-index quality:** after repairing vectorless passages, re-embed only the affected items — touch them (add+remove a tag for a version bump) and run an incremental update. ~$0.25 instead of a ~$10 full rebuild.
- **Incremental updates and full text:** an index update *without* the fulltext flag silently drops the full-text passages of re-processed items (metadata-only re-chunk). Always run updates with the fulltext option when items carry attachments.
- **Bulk tag/write operations:** API clients time out on large batches (hundreds of items) even though the writes continue server-side. Verify with a count query, re-run idempotently in batches of ≤300.
- **Write storms → HTTP 502:** back off briefly and retry; the writes are idempotent.
- **Search-crash class of bugs:** a single malformed metadata value (e.g. a `None` where a number is expected) can kill the *entire* search. Fixed by None-safe handling; fulltext-only hits now return a `null` vector distance instead of crashing. If your search dies on some queries, look for unguarded type assumptions before blaming the index.
- **Copying SQLite databases (2026-09-02, confirmed three times):** a raw `cp` of an open SQLite DB (WAL mode) yields a 0-byte file. Always use `sqlite3 DB "VACUUM INTO '/path/backup.db';"` — a consistent snapshot, even while the server is running.
- **HTML with links in `memory_store` content:** harmless; don't let content filters scare you off — URLs do not disturb embedding.
- **Error in only one interface (2026-09-12):** if the same MCP server runs fine in the CLI but fails in the desktop client (or vice versa), compare `/proc/<pid>/environ` and `/proc/<pid>/maps` of both server processes. An app wrapper may export `PYTHONHOME`, `PYTHONPATH` or `LD_LIBRARY_PATH` — that library leak then loads an outdated system library (in our case libsqlite3 3.37.2 instead of 3.45.1) and produces misleading error messages. Neither the server code nor the database is at fault.
- **Extra FTS columns are not a free upgrade (2026-09-19):** every additional column in the bm25 calculation changes the length normalization. The search-anchor boost (column `key`, 3× weight) delivered +0.024 MRR; an experimental date field cost −0.011 MRR with zero measured gain and was rolled back. Add columns only with measured benefit.
- **Running sessions pick up server changes only after restart (2026-09-20, bitten repeatedly):** search reads the database live, but code changes, new defaults and the loaded synonym dictionary are not active in an already-running server process. After server updates, restart the session before measuring effects — otherwise you are measuring the old state.

---

## 6. Importing this into your own setup

1. Skim §2 and adopt the rules verbatim — they are CC0 and agent-agnostic.
2. Store them in your own memory system (one entry per rule, or one entry per rule block), tagged for retrieval.
3. Put the memory-first rule and the violation examples into your agent's always-loaded instruction file — the memory alone is not enough, because the rule is about *when to open the memory in the first place*.
4. Adapt the timeline (§4) to your own incidents: your violations will be different, your rules should be too.

*Compiled by Qwen Code (GLM-5.3-Flash) from the live memory database of Michael Logies' agent setup. As of 2026-09-20: 403 notes.*
