# Zotero-MCP-Knowledge for AI Agents

> **Purpose:** This document captures the hard-won knowledge about connecting Zotero with AI agents via MCP servers. It is designed to be consumed by any AI agent (Qwen Code, Kimi Code, Claude Code, etc.) to accelerate onboarding — so you don't have to make the same mistakes twice.
>
> **Last updated:** 2026-09-16
> **Source:** Michael Logies, practical experience with Zotero + AI agents in a real-world research/writing workflow.
> **License:** CC0 — use freely, no attribution required.

---

## 1. Architecture Overview

The standard setup connects a **Linux-based AI client** to a **Windows Zotero host**:

```
+-------------------------------------------------+
| Linux (AI Client)                                |
|  - MCP Client (Qwen Code / Kimi Code / Claude)   |
|  - Python + pyzotero                             |
|  - Project memory (Markdown + Memory MCP server) |
+-------------------------------------------------+
        |
        | HTTP (Streamable MCP, port 3939)
        v
+-------------------------------------------------+
| Windows (Zotero Host)                            |
|  - Zotero 10 with local API enabled              |
|  - Zoteus (MCP server, sole Zotero MCP server)   |
|  - Full PDF library + SMB storage share          |
+-------------------------------------------------+
```

**Key requirements on the Windows host:**
- Zotero with local HTTP API enabled (`extensions.zotero.httpServer.enabled` and `extensions.zotero.httpServer.localAPI.enabled` both `true`)
- SSH server reachable from the Linux client
- Node.js with `Zoteus` — **since 31 Aug 2026 (v1.12.0) the sole Zotero MCP server in the reference setup.** The ChromaDB-backed zotero-mcp-server is no longer used; Zoteus covers its semantic-search role too (see §4.3). Deactivating it in the MCP client config is an owner decision.

**Key requirements on the Linux client:**
- MCP client (Kimí Code, Qwen Code, or Claude Code)
- SSH key for passwordless login to the Windows host
- Python 3 with `pyzotero` for batch operations
- SMB mount for direct Zotero storage access (optional, for large PDFs)

---

## 2. Tool Comparison: Four Ways into Zotero

### 2.1 zotero-mcp-server (Steven Yuyy / 54yyyu)

| Attribute | Details |
|-----------|---------|
| **Type** | MCP server (Python, FastMCP) |
| **URL** | https://github.com/54yyyu/zotero-mcp |
| **Docs** | https://stevenyuyy.com/zotero-mcp/ |
| **Version** | v0.11.0 (August 2026) |
| **Read backend** | Zotero local HTTP API (port 23119) |
| **Write backend** | Zotero local API |
| **Semantic search** | ChromaDB with OpenAI embeddings (or local) |
| **Plugin needed?** | No |

> **No longer used in the reference setup (since 31 Aug 2026):** Zoteus v1.12.0 covers every role of this server — faster reads/writes, plus semantic search that is now *faster* than ChromaDB (§4.3). The sections below are kept for background, not as a recommendation.

**Strengths:**
- Semantic search via ChromaDB (120K+ documents indexed)
- Incremental index updates (`zotero-mcp update-db --fulltext`)
- SQLite search backend (since v0.10.0, `ZOTERO_SEARCH_BACKEND=sqlite`) — advanced search runs server-side SQL instead of paging through the whole library; fixes 300 s timeouts and missing note content
- PDF layout detection, annotations, outlines
- DOI import via `zotero_add_item`

**Known weaknesses:**
- **Global RLock** serializes all access — one slow operation blocks everything
- **pyzotero hardcoded 30s timeout** — not configurable upstream
- **No HTTP connection pooling** — new `httpx.Client` per call
- **No graceful shutdown** — worker threads are abandoned
- **Zotero Local API speaks HTTP/1.0** — single-thread, need to force httpx to HTTP/1.1
- **Instability at scale** — crashes under parallel requests, timeouts with large PDFs
- **Architecture analysis** confirmed 8 structural problems (see [architecture analysis](https://www.logies.de/zotero-mcp-architektur-analyse.html))

### 2.2 Zoteus (oscardvs/zoteus)

| Attribute | Details |
|-----------|---------|
| **Type** | MCP server (TypeScript, Node.js) |
| **URL** | https://github.com/oscardvs/zoteus |
| **Version** | v1.20.1 (14 Sep 2026; running in the reference setup) |
| **Read backend** | Zotero local HTTP API |
| **Write backend** | Zotero Web API v3 |
| **Semantic search** | Built-in index (keyword + vector, RRF fusion) |
| **Plugin needed?** | No |

**Strengths:**
- **31 tools** — very comprehensive coverage
- **Blazing fast reads** — metadata/search/items in < 0.01s
- **PDF fulltext fallback** — downloads and parses PDFs on-the-fly when Zotero hasn't indexed them
- **DOI import** (since v1.2.0 via OpenAlex/Crossref, no translation server needed)
- **~2,800 CSL citation styles** via citeproc-js
- **Semantic search** with auto-build (since v1.2.0)
- **Configurable embedding model** via `ZOTEUS_EMBEDDING_MODEL` (since v1.7.0)
- **Configurable batch size/delay** via `ZOTEUS_EMBED_BATCH_SIZE` / `ZOTEUS_EMBED_BATCH_DELAY_MS` (since v1.7.0)
- **Incremental index updates** (`action: "update"`) (since v1.7.0)
- **Well-architected** — no GIL, true async I/O, per-session context, rate limiting, health checks, graceful shutdown

**Known weaknesses:**
- **Index build capped** at configurable `ZOTEUS_INDEX_MAX_ITEMS` (default 5000) — the reference setup runs 20000 for a ~10.4k-item library
- **Write operations go through cloud Web API** (not local) — needs API key, internet
- **OpenAI embeddings** — requires API key, costs money per build. Rate limiting on large builds is handled automatically since **v1.13.0** (backoff + retry on 429/5xx/timeout/dropped connections, honoring `Retry-After`; `ZOTEUS_EMBED_MAX_RETRIES` default 5, `0` restores the old behavior; a 400 stays fatal on purpose — that is the oversized-batch reply, and retrying it would only slow an actionable failure). Interrupted builds keep their fulltext checkpoint and a resumed `build` embeds exactly the missing vectors. For big libraries the pacing pair `ZOTEUS_EMBED_BATCH_SIZE=256` + `ZOTEUS_EMBED_BATCH_DELAY_MS=8000` (≈ 400k tokens/min, safely under the 1M TPM Tier-2 ceiling) is now the officially documented recommendation — see §4.4
- **Local embeddings without any API key** (since **v1.13.0**, issue #43): `ZOTEUS_EMBEDDINGS=local` + `ZOTEUS_EMBEDDING_MODEL=Xenova/multilingual-e5-small` — markedly better on German than the MiniLM default at the same 384 dimensions; E5 `query:`/`passage:` prefixes are applied automatically (`ZOTEUS_EMBEDDING_PREFIXES` = auto/e5/off). The embedder identity becomes `local:<model>`, so a model switch is one rebuild (old vectors dropped, keyword search keeps working). **Weight precision is selectable since v1.14.0** (#50, issue #43): `ZOTEUS_EMBEDDING_DTYPE` is part of the embedder identity, and the quantized **q8** build of e5-small is **~129 MB** instead of the ~470 MB full-precision ONNX. A measured benchmark on a full ~10.5k-item library (issue #43 comment, 2026-09-03): q8 kept **80.6 %** of fp32's top-10 slots identical — while fp32↔OpenAI agreed on only 59.4 % — so quantization costs less than a model change. Also since v1.14.0 (#52), **pooling follows the model from a curated per-model table**; unknown transformers.js model ids still load, with an unvalidated warning. Realistic on Chromebooks: the model download plus the embedding memory footprint are the binding constraint on small devices — no tested threshold, so have the assistant check the container's actual memory first (`free -h`) — see §4.4
- **Production note on the local route (2026-09-13):** the reference setup switched to local e5-small and completed a full rebuild overnight (~5.7 h): 272,896 passages (= vectors, none missing) over 10,757 items, including 234,861 full-text passages from 7,552 attachments — **books fully indexed** (the 1M-character cap of the OpenAI route had been truncating them). Re-verified afterwards with 4/4 live checks (semantic search, fulltext locators, scholar lookup, daily incremental cron).
- **Semantic search was brute force (fixed in v1.10.0)** — through v1.9.0, the vector scan iterated all stored rows in JavaScript, O(N) per query: 93–105 s on a 255k-passage × 3072-dim index (see §4.3, [zoteus issue #30](https://github.com/oscardvs/zoteus/issues/30)). The maintainer resolved it on main (29 Aug 2026, shipping in v1.10.0): two-stage ranking — one-bit binary codes per vector (sign bits after subtracting the corpus mean), Hamming-scanned via XOR + SWAR popcount to pick an oversampled candidate pool, then exact float32 cosine rerank on those candidates only. Measured 2,269 ms → 54 ms (42×) at exactly this shape, with the codes costing 94 MB beside the 3.1 GB of vectors; every returned score still comes from a real vector. Tunables: `ZOTEUS_INDEX_ANN_OVERSAMPLE` (default 16×, recall 0.986 on real embeddings) and `ZOTEUS_INDEX_ANN=false` to force the old exact scan. Existing indexes gain codes from the next build/update or first semantic query — no re-embedding, no schema bump.

### 2.3 cli-anything-zotero (PiaoyangGuohai1)

| Attribute | Details |
|-----------|---------|
| **Type** | Python CLI toolkit (not an MCP server) |
| **URL** | https://github.com/PiaoyangGuohai1/cli-anything-zotero |
| **Read backend** | SQLite + local API + JS Bridge |
| **Write backend** | Local JS Bridge (no API key, no internet needed) |
| **Plugin needed?** | Yes — requires the "CLI Bridge for Zotero" plugin (see warning below) |

**⚠️ NOT RECOMMENDED (as of 2026-09-01):** This tool depends on the "CLI Bridge for Zotero" plugin. There is no ready-made XPI (source build only), and under Zotero 10 the plugin loads as `appDisabled`; even after a manifest patch, endpoint registration is unverified. Use Zoteus instead — it covers the same use cases and needs no plugin.

**Strengths:**
- **Direct Zotero JS access** (`zotero-cli js ...`) — deepest possible integration
- **DOI import with PDF fetch** — `add doi ... --fetch-pdf` grabs the paper
- **Full-text search** across all PDFs
- **Notes read/write** — `item notes`, `note add`
- **DOCX citations** — static/dynamic, unique among tools
- **Works completely offline** — no API key, no internet for local operations

**Known weaknesses:**
- **Not an MCP server** — must be called via SSH as a shell command
- **Slower** than Zoteus for simple reads (~1s per operation)
- **Requires a Zotero plugin** (CLI Bridge for Zotero)
- **Command syntax may differ** between versions — verify with `zotero-cli --help`

### 2.4 pyzotero (urschrei)

| Attribute | Details |
|-----------|---------|
| **Type** | Python library (not an MCP server) |
| **URL** | https://pyzotero.readthedocs.io/ |
| **Version** | 1.13.5 (August 2026) |
| **Read backend** | Local API (`local=True`) or Web API |
| **Write backend** | Web API only (local API is read-only) |
| **Plugin needed?** | No |

**Strengths:**
- **Fastest reads** — item metadata in ~0.01s, fulltext in ~0.06s (local=True)
- **Parallel access** — 5 concurrent threads tested successfully (0.42s total)
- **Batch operations** — ideal for mass export, tag updates, metadata extraction
- **Very stable** — mature library, active maintenance
- **No server overhead** — direct Python calls, no MCP/SSH startup

**Known weaknesses:**
- **Not an MCP server** — must be called from Python scripts
- **Local API writes** — read-only before Zotero 10; since Zotero 10 (Aug 2026) the local API supports writes, removing the cloud detour for local write workflows
- **PDF binary download broken** from Linux — Zotero redirects to `file://localhost/` path (Windows local)
- **No semantic search, no annotations, no CSL citations**

### 2.5 SMB Storage Access (optional)

| Attribute | Details |
|-----------|---------|
| **Type** | Direct filesystem access |
| **Mount point** | SMB share of Zotero storage directory |
| **Purpose** | Direct access to PDF files, embedded images, large documents |

**Strengths:**
- **Fast** — read a 3 MB PDF in ~0.005s over SMB
- **Extract embedded images** via `pdfimages -j`
- **Render pages as PNG** via `pdftoppm -png` for visual analysis
- **Access original binary PDF** — not available through any API path
- **Read non-indexed PDFs** — no Zotero fulltext index needed

**Known weaknesses:**
- **Read-only** — never write to the storage directory
- **Slower for rendering** — `pdftoppm` over SMB: ~11.6s for a full PDF
- **Additional setup** — SMB credentials, mount configuration

---

## 3. Recommended Tool Division

**Since 31 Aug 2026 (Zoteus v1.12.0): everything over Zoteus** — including semantic search on large libraries. Re-benchmark at v1.12.0 steady state: 0.33–0.84 s per query on the same 255k-passage × 3072-dim index where v1.9.0 took 93–105 s (a factor of ~200–300×; first query after restart ~266 s while the binary codes are built once, ~94 MB, documented in [issue #30](https://github.com/oscardvs/zoteus/issues/30)). That makes Zoteus faster than the ChromaDB-backed server (~13 s/query) and retires the old division of labor. The table below is kept as background.

| Task | Recommended Tool | Why |
|------|-----------------|-----|
| Quick reads (metadata, search) | **Zoteus** | < 0.01s, very stable, 31 tools |
| Semantic search (large own library) | **Zoteus** (since v1.10.0; v1.12.0 measured 0.33–0.84 s/query) | Two-stage binary-code search; ChromaDB no longer needed (~13 s) |
| Batch operations (many items) | **pyzotero** local=True | 0.42s for 5 parallel reads, 2.6× faster than Web API |
| PDF fulltext reading | **Zoteus / pyzotero** local=True | 0.01s for ~40K chars |
| DOI import + PDF fetch | **Zoteus** | `zotero_import` + `attach_url` (OpenAlex/Crossref built in) |
| Notes / annotations | **Zoteus** | `zotero_annotate` anchors highlights by quoted passage |
| CSL citations (APA, etc.) | **Zoteus** | < 0.01s, ~2,800 styles via citeproc |
| Semantic search (external sources) | **Zoteus** | OpenAlex-based via `zotero_scholar` |
| DOCX citations | **cli-anything-zotero** | Only tool with DOCX support |
| Parallel access (multi-agent) | **pyzotero** | MCP servers are sequential only |
| Large PDFs / images | **SMB** | Direct file access, `pdfimages`, `pdftoppm` |

---

## 4. Benchmark Results

### Sequential Benchmarks (August 2026)

| Operation | zotero-mcp-server | Zoteus | cli-anything-zotero | pyzotero local=True |
|-----------|------------------|--------|-------------------|-------------------|
| Init / Connection | 4.47s | 3.99s | 3.35s | — (no server) |
| Item metadata (1 item) | 2.03s | **< 0.01s** | 1.14s | **0.01s** |
| Children / Attachments | 4.08s | — | 1.06s | **0.03s** |
| Fulltext / PDF (~40K chars) | 2.16s | **< 0.01s** | 5.64s | **0.01s** |
| Search (5 results) | 7.63s | **< 0.01s** | — | 0.19s |
| List collections | 2.07s | **< 0.01s** | 1.11s | — |
| List tags | — | **< 0.01s** | — | — |
| Citation (CSL, APA) | — | **< 0.01s** | 1.25s | — |
| Notes | — | — | 1.06s | — |
| Annotations | — | — | 5.09s | — |

### Parallel Access (pyzotero, 5 concurrent threads)

| Test | Total Time | Per Thread | Success |
|------|-----------|-----------|---------|
| Web API: 5× read (top limit=10) | 1.09s | 0.82–0.97s | 5/5 ✅ |
| Web API: 5× write (tag update, same item) | 1.79s | 1.03–1.75s | 5/5 ✅ |
| **local=True: 5× read** | **0.42s** | **0.33–0.35s** | **5/5 ✅** |

**Key insight:** `pyzotero local=True` is **2.6× faster** than the Web API for parallel reads. Write operations work because pyzotero fetches the item (with current version) in each thread before updating — preventing version conflicts.

### HTTP vs. SSH Transport (zotero-mcp-server, 17 Aug 2026)

| Operation | HTTP | SSH | Verdict |
|-----------|------|-----|---------|
| Initialize (session + tools) | 0.04s | 3.0s | **77× faster** |
| List tools | 0.01s | 0.5s | **39× faster** |
| List libraries | 0.02s | 1.0s | **41× faster** |
| Item metadata | 2.0s | 2.0s | same |
| Collections | 2.1s | 2.1s | same |
| Fulltext | 4.1s | 2.2s | same/slower |
| Semantic search | 12.0s | 2.0s | slower |

**Key insight:** HTTP eliminates SSH overhead for meta-operations (40–77×). Data queries are transport-independent — the Zotero Local API itself is the bottleneck.

### 4.3 Full-Index Semantic Search Benchmark (29 August 2026)

Identical queries against a 255,703-passage library with 3072-dimensional embeddings:

| Server | Latency per query |
|--------|------------------|
| zotero-mcp-server v0.11.0 (ChromaDB, HNSW ANN index) | ~13 s (cold start 78 s) |
| Zoteus v1.9.0 (SQLite backend, per-row JS vector scan) | 93–105 s |

**Diagnosis** (via RAM sampling over full queries): Zoteus working memory stays flat during the scan — the cost is a CPU-bound iteration over all vector rows (blob fetch + decode + 3072-dim multiply-accumulate) in JavaScript, not an all-vectors-in-RAM problem and not I/O. Five sequential queries showed no cold-start penalty (93.3 s first, 93–105 s after), confirming the CPU-side explanation.

**Consequence (at measurement time, 29 Aug 2026):** until Zoteus shipped a real ANN index ([issue #30](https://github.com/oscardvs/zoteus/issues/30)), the ChromaDB-backed server was the right choice for semantic search on large libraries — see the resolution below for what changed.

**Resolution (29 Aug 2026, deployed as v1.10.0, re-benchmarked at v1.12.0):** Both issues were resolved by the maintainer on main the same day. #29: `zotero_get_fulltext` now falls back to the attachment file itself (desktop local API → direct read of the storage dir → cloud download, reporting `fileSource`/`fulltextSource`); `outline:true` returns a PDF's table of contents; `page_range` re-extracts real pages by default; EPUBs extract locally. #30: two-stage search with one-bit binary codes + Hamming scan and exact float32 rerank — 2,269 ms → 54 ms (42×) measured at this benchmark's exact shape (255,703 × 3072), codes costing 94 MB, no re-embedding, recall dial via `ZOTEUS_INDEX_ANN_OVERSAMPLE` (default 16× → 0.986).

**Steady-state re-benchmark at v1.12.0 (31 Aug 2026):** 0.33–0.84 s per query on the unchanged index — a ~200–300× improvement over v1.9.0 and faster than ChromaDB (~13 s). The one-time code-build penalty (~266 s on the first query after a server restart) is documented behavior, not a fault. **Consequence:** the semantic-search division of labor is retired — Zoteus is the tool for everything, including semantic search on large libraries.

### 4.4 Index-Build Throttling: the OpenAI 429 Problem (2 September 2026)

**Symptom** ([issue #48](https://github.com/oscardvs/zoteus/issues/48), open at v1.12.0): `zotero_index build fulltext:true` can die permanently with `OpenAI embeddings failed (429)` — with the default dials, the embedding rate rides exactly at the provider's tokens-per-minute ceiling, any variance trips 429, and there is **no retry/backoff**. The build degrades to keyword-only (`embedder=none`); passages stay stored but their vectors are NULL. Worse: resuming re-embeds the **entire fulltext phase from scratch** (the metadata checkpoint survives, the fulltext progress does not — `fulltextVersion` stays 0), so a large-library build that hits this once can never complete and re-burns the embedding budget each attempt.

**Fix in the environment (no code change needed):**

```
ZOTEUS_EMBED_BATCH_SIZE=256
ZOTEUS_EMBED_BATCH_DELAY_MS=8000
```

This paces to ≈ 400k tokens/min on `text-embedding-3-small` — safely under a 1M TPM (Tier 2) ceiling. A full rebuild of a 10.4k-item library (88.5k passages) then completes in a single uninterrupted run (~75 min, ≈ $1). Note: `build` starts from zero unless it resumed from a checkpoint left by stop/crash — a paused build has no fulltext-phase checkpoint at all (that is the second half of issue #48).

**Tool choice (automatisms):** run the semantic index as a **daily incremental update** (`zotero_index` `action: "update"`, **always `fulltext: true`** with a generous `fulltext_max_chars` — without the flag, the fulltext passages of changed items are silently deleted) instead of waiting for a monthly sync. Cost at small-model pricing is cent-level because only changed items are re-embedded. If a build/update ever ends with `embedder=none (OpenAI … 429)`, set the two dials above and re-run — do not diagnose the index; the passages are there, only their vectors are missing.

**Resolution (3 Sep 2026, deployed as v1.13.0):** all four requests were implemented the same day ([issue #48 comment](https://github.com/oscardvs/zoteus/issues/48#issuecomment-5522523056)). (1) 429/5xx/timeout/dropped connections now back off and retry (1 s → 16 s + jitter, `Retry-After` honored in either form, capped at 60 s per wait and ~3 min per request; `ZOTEUS_EMBED_MAX_RETRIES`, default 5, `0` = old behavior; 400 stays fatal on purpose). On the first 429 the log names your batch size and delay numbers. (2) The failing build no longer deletes its checkpoint: it stays *unfinished*, withholds the library version stamp so an `update` falls back to a resuming `build` instead of a no-op delta, and the resumed build finishes by embedding exactly the committed passages without vectors — on this library, ~34,000 vectors instead of 87,000 on the second attempt. (3) The two dials are now documented in the README config table, the desktop-extension settings and the `zotero_index` tool description; the 256/8000 pair measured in this setup is quoted as the recommendation for Tier-2 accounts on large full-text builds. (4) `zotero_index action:"status"` reports the measured `embedRate` (batch size, pause, tokens/request, sustained tokens/min — warning at ≥ 800k tokens/min) and `passagesWithoutVectors`, and names `build`, never `refresh`, as the remedy.

**Upgrade check on this setup (3 Sep 2026):** server restarted to v1.13.0; the existing index was untouched (88,502 passages/vectors over 10,436 items, embedder=openai) and the store was upgraded in place from schema version 1 to 2 (diacritics kept in the keyword index) with no vectors re-computed. No action needed beyond the restart.

**Also in v1.13.0 (issue #43):** local embedding is configurable — `ZOTEUS_EMBEDDINGS=local` + `ZOTEUS_EMBEDDING_MODEL=Xenova/multilingual-e5-small` now reaches the local provider; E5 `query:`/`passage:` prefixes are applied automatically for model ids containing an `e5` segment (`ZOTEUS_EMBEDDING_PREFIXES` = auto/e5/off overrides); the identity becomes `local:<model>`, so a model switch is one rebuild with the old vectors dropped. Maintainer measurement: MiniLM ranked the English twin of a German answer passage 9.5th of 12 (sorting by language, not topic), e5-small ranked it 2.5th — at the same 384 dimensions. Size caveat: the default download is the full-precision ONNX at **~470 MB** on disk (the ~118 MB figure is the quantized build; selecting a quantized dtype is an open follow-up because the dtype must enter the embedder identity first). For the Chromebook student setup this is a deliberate alternative, not the default: the ~470 MB download and the embedding memory footprint are what gets tight on small devices, and the guide (§6.3) deliberately frames the RAM question as untested — the assistant checks the container's memory (`free -h`) before committing to the route.

---

## 5. Active Zotero Plugins for AI Collaboration

These plugins are active in the reference setup and highly recommended for AI-Zotero workflows:

| Plugin | Version | Purpose |
|--------|---------|---------|
| **Better BibTeX for Zotero** | 9.0.61 | BibTeX/BibLaTeX export. AI agents can reference items via citation keys. |
| **Better Notes for Zotero** | 3.3.3 | Advanced note management directly in Zotero. |
| **Beaver** | 0.23.3 | Academic Research Agent — AI-assisted literature work inside Zotero. |
| **Zoplicate** | 5.0.9 | Duplicate detection and management. |
| **Translate for Zotero** | 2.4.7 | Translation of PDFs, EPubs, metadata, and notes. |
| **Zutilo** | 4.2.2 | Macros and keyboard shortcuts. Outputs each item's Zotero ID — and each collection's `zotero://select/library/collections/<KEY>` link — simplifying communication with AI agents. |

("CLI Bridge for Zotero" is no longer listed — see the warning in the cli-anything-zotero section: no ready XPI, loads as appDisabled under Zotero 10.)

---

## 6. Best Practices & Rules

### 6.1 Never Write to Zotero SQLite Databases Directly

**Critical rule:** `zotero.sqlite`, `beaver.sqlite`, and other `.sqlite` files in the Zotero directory must NEVER be modified directly by an AI agent. A corrupt write can destroy 10,000+ items.

**Safe write paths:**
- ✅ MCP server tools — write via Zotero local API (safe)
- ✅ pyzotero — writes via Web API (safe)
- ❌ Direct `sqlite3` UPDATE/INSERT/DELETE over SSH — **forbidden**
- ❌ Direct file copy over SMB — **forbidden**

### 6.2 Always Cite Page Numbers

When citing literature from Zotero fulltexts, always include the page number:

```
"Dong & Guo 2026, p. 7 report that the 5-year survival rate is 74%."
```

Use `zotero_get_fulltext` with the `query` parameter — it returns locators with page numbers.

### 6.3 PDF Download: Use Direct PDF URLs, Not DOI URLs

DOI URLs often return HTML pages, not PDFs. Use direct publisher PDF URL patterns:

- **Springer/BMC:** `https://link.springer.com/content/pdf/10.XXXX/XXXXXX.pdf`
- **Wiley:** `https://onlinelibrary.wiley.com/doi/pdfdirect/10.XXXX/XXXXXX`
- Verify with `file <pdf.pdf>` to confirm it's a real PDF, not HTML.

### 6.4 MCP Server Connection Recovery

| Transport | Reconnect possible? | Method |
|-----------|-------------------|--------|
| **HTTP** (Zoteus, Memory MCP) | ✅ Yes (since Qwen Code v0.22.2) | `qwen mcp reconnect --all` |
| **SSH stdio** (Zotero-MCP-server) | ❌ No | Session restart required |

### 6.5 Semantic Search Index: Never Force-Rebuild

- The ChromaDB index (120K+ documents) is expensive to rebuild (~1h 42min for 9,710 items).
- **Never** run `--force-rebuild` or `--allow-mass-deletion` without explicit user permission.
- If the index shows 0 documents, restart the server process — **not** the index.
- For items not found in the index: check the item type first (`webpage` items with PDF attachments are not indexed by default).

### 6.6 Zotero Process Count

Zotero normally shows 3+ processes in the Windows Task Manager. This is standard behavior (multi-process architecture like Firefox/Electron), not an error indicator. Only if there are 7+ instances should you suspect a problem.

### 6.7 Group Library Items Are Independent Copies

Dragging an item into a group library creates a 1:1 copy with a new item ID. There is no persistent link between the original and the copy. Changes to one do not affect the other.

### 6.8 Attachment-Path Tools Want the Parent Key, Not the Attachment Key

`zotero_get_attachment_path` expects the **parent item key** and then lists all attachment paths. Passing an attachment key instead returns a silent *"No attachments found"* — no error, just an empty result that looks like a sync or storage problem. (`zotero_get_item_fulltext` is the opposite: it accepts both parent and attachment keys.)

**Rule:** Whenever an attachment-related tool returns "No attachments found", first retry with the parent key before diagnosing storage, sync, or server problems. This asymmetry has repeatedly led agents down false storage-diagnosis rabbit holes.

### 6.9 Attach PDFs So They Are Cloud-Independent

If Zotero's cloud file-sync quota is exhausted (sync disabled), attachments that exist only in the cloud appear grayed out and will not open. Two sound routes:

- **Preferred (cloud-free):** download the PDF from the source, then attach it via a tool that writes through the **running Zotero desktop app** (Zotero 10+ local API) — the file lands directly in `storage/<attachment-key>/` and Zotero displays it immediately. Requires the desktop app to be running; otherwise fall back to the cloud route *and stop* until it is.
- **Cloud route + local copy:** upload via the Web API, then copy the file into `storage/<attachment-key>/` directly (e.g., over SMB) so it is available locally without waiting for sync. An attachment key (child item) is not the parent item key — the storage folder is keyed by the attachment key.

### 6.10 Incremental Index Updates Drop Fulltext Without the Right Flag

A semantic-index incremental update that is run *without* the fulltext option re-chunks only metadata and **silently deletes the fulltext passages** of the processed items. Re-confirmed on 2026-09-13 during a full rebuild cycle: the flag governs whether attachment bodies are **read at all** — an update without `fulltext: true` reads no PDF bodies, it does not merely skip re-chunking. Always run index updates for items with attachments with the fulltext option enabled (and a generous `fulltext_max_chars`). Similarly, items whose passages vanished from the index can be repaired by "touching" them (add + remove a tag, bumping the version) and updating incrementally — re-embedding only those items instead of rebuilding the whole index.

### 6.11 Operating the Semantic Index as an Automatic Routine

The reference setup keeps the semantic index current with **automatisms** rather than manual rebuilds:

- **Daily incremental update** (automated task in the MCP client): check that no build is running, then `zotero_index` `action: "update"`, **always with `fulltext: true`** and a generous `fulltext_max_chars`. Only items changed since the last update are re-embedded — cost at `text-embedding-3-small` pricing is cent-level per day. Do the same whenever a build or update was interrupted or ended in `embedder=none` (see §4.4). On a low-RAM device (≈6–8 GB, e.g. Crostini defaults), watch for the OOM killer: if the build aborts together with the whole agent session, apply §7.6 before retrying.
- **Recurring npm update check** (weekly in the reference setup): Zoteus releases move fast (v1.7 → v1.12 in one week); a weekly `npm` check against `@oscardvs/zoteus` catches them. After updating, expect a one-time ~200–400 s first-query penalty while the binary codes are rebuilt (§4.3).
- **Hourly memory backups, not index backups:** the Zotero *search index* is always rebuildable from the Zotero data and the Zotero full-text index — never back it up, just rebuild. What *is* backed up hourly is the separate Memory-MCP database (see the Memory-MCP knowledge files).

### 6.12 Page Numbers: Verify via Running Heads, Not `pageApprox`

`zotero_get_fulltext` reports `pageApprox` computed proportionally from character offsets. On a real 442-page book this was **~5 pages off** the printed pagination. The reliable method: page numbers embedded in the text layer as running heads (e.g. `231 GLOBAL DIVISION OF LABOUR`) — collect them as a page map (standalone digit lines ≤ page count, monotonically increasing) and map character offsets through it. Cite only verified page numbers.

### 6.13 One-Channel Principle for Index Automatisms

After an index incident (2026-09-08: a stale watchdog cron with auto-resume logic kept restarting a crashed index build while an old session from a previous rebuild era fired outdated build instructions in parallel — the production index was lost twice), the reference setup enforces:

1. **Exactly one automated channel** runs index actions (`build`/`update`/`stop`/`pause`) — typically a single daily incremental-update task. No other session or cron may add index-touching jobs, including "progress" or "watchdog" crons.
2. **No auto-resume on `error`.** Neither cron nor session resumes a failed build automatically; resuming is a deliberate user decision. A server-side refusal of a parallel index job (job serialization) is expected behavior, not a fault.
3. **Status gate first.** Every index-touching automated prompt must check `action: "status"` first and, on `building`/`error`, report only — never start anything.
4. **Index actions only from a known, current session.** Never resume old sessions from a rebuild era — they carry stale build instructions.

---

## 7. Troubleshooting Guide

### 7.1 ChromaDB Shows 0 Documents / "Tenant Not Found"

**Cause:** Server startup issue, not an index problem. The Zotero-MCP-server was not cleanly started, or Zotero was restarted while the server was running.

**Fix:**
1. Restart Zotero on the Windows host
2. Kill the Zotero-MCP-server process
3. Restart the MCP client session (SSH stdio transport requires a full session restart)
4. ❌ Do NOT rebuild the index

### 7.2 Item Not Found in Semantic Search

**Debug workflow:**
1. Check item type with `zotero_get_item_metadata` — `webpage`, `attachment`, `note`, `email` are not indexed
2. If item type is `webpage` with a PDF attached: change type to `document`
3. If everything is correct: run `zotero-mcp update-db --fulltext` (incremental) on the Windows host

### 7.3 MCP Tools Not Loading in Session

**Fix:**
- For HTTP transport: `qwen mcp reconnect --all`
- For SSH stdio transport: restart the Qwen Code session

### 7.4 Zotero Multiple Instances / Timeouts

If Zotero operations are slow or timing out:
1. Check for too many Zotero processes (7+ is problematic)
2. Kill all Zotero processes and restart
3. Wait 10–15 seconds for Zotero to fully load
4. Retry the MCP tool call

### 7.5 config.json Empty (ChromaDB)

If `zotero_get_search_database_status` reports 0 documents and a config error:
1. Reset the config: `Set-Content -Path [config_path] -Value '{}'`
2. Run `zotero-mcp-server setup` to regenerate
3. This is a recurring issue — cause unknown

### 7.6 Build Aborts and the Agent Session Dies Together (OOM)

**Symptom:** The full-text index build (or a large incremental update) aborts mid-run and the whole agent session dies with it — repeatedly, seemingly without reason.

**Cause:** The kernel's OOM killer. The embedding process is memory-hungry (~4 GB RSS observed); on devices with little RAM (≈6–8 GB, e.g. Crostini defaults) and larger libraries (~10k+ items) the kernel kills the hungriest process to save the system. Verify: `dmesg | grep -i -e oom -e "killed process"`.

**Fix — 4 GB swap file (requires sudo; confirm with the user first):**

```bash
touch /swapfile
sudo chattr +C /swapfile
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile && sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

**BTRFS ordering trap (Crostini root filesystem):** `chattr +C` (no-copy-on-write) works only on an **empty** file — set it between `touch` and `fallocate`. On an already-filled file the kernel refuses activation: `swapon: /swapfile: swapfile must not be copy-on-write`.

**Verified 2026-10-01/02** (Crostini, 6.3 GB RAM, ~11,800 items): two aborts without swap; the same build completed with swap (peak swap usage 2.1 GB). Resuming a failed build is a deliberate act (§6.13) — after adding swap, rerun the build/update once, with the user's consent. The user-facing copyable version of this recipe lives in the Setup-Guide §6.3 (box "If the index build is killed for lack of memory").

---

## 8. Zotero-Specific Knowledge

### 8.1 Zotero Web UI Notes

- Notes are displayed in the right sidebar only — no way to open them in the main view
- The notes panel closes when scrolling through the item list
- Workaround: reduce browser zoom, or edit notes in an external editor

### 8.2 Local API Details

- Zotero's local HTTP API runs on port 23119 (localhost only)
- **Zotero 10 (August 2026): the local API now supports write requests** — pyzotero `local=True` and MCP tools can write locally without the cloud detour. Before Zotero 10, local writes were read-only.
- It speaks HTTP/1.0 — single-threaded server
- Must be enabled in Zotero settings: `extensions.zotero.httpServer.enabled` and `extensions.zotero.httpServer.localAPI.enabled` = `true`

### 8.3 Zotero 10 Upgrade (August 2026): What Changes for Agents

Zotero 10.0 (2026-08-17) and 10.0.1 (2026-08-24) were verified against all MCP tools after an in-place upgrade from 9.x (2026-08-30). Findings:

- **No breaking changes** for Web API or Local API read access — all MCP tools work unchanged
- **Local API write support (new)** — see §8.2; the biggest practical change for tool workflows
- **Faster fulltext indexing and fulltext searches; accent-insensitive search** ("cafe" matches "café")
- **Database:** improved write performance, automatic compaction, versioned DB backups dropped (regular backup forced before every upgrade instead)
- After the upgrade Zotero re-runs a full/partial fulltext re-index; the schema migrates — both MCP servers' indexes picked up the migrated items via their normal incremental updates (645 changed items on Zoteus, zero errors)
- **Search backends that read zotero.sqlite directly** (e.g. the SQLite search backend) can be briefly blocked while Zotero compacts the DB — retry rather than diagnosing corruption
- Semantic-search verification after the upgrade: ChromaDB and Zoteus metadata/fulltext search both worked normally; any semantic-search slowness is the pre-existing Zoteus v1.9.0 issue (#30), not an upgrade problem

### 8.4 Zotero Storage

- Attachments are stored in a `storage/` directory, organized by item key
- The storage can be shared via SMB for direct file access
- Two SMB shares are typical: one read-write (for config) and one read-only (for storage/attachments)

### 8.5 Standard Workflow: Zotero Note → PDF (German Documents)

**Tools (one-time install on the Linux VM):**

```bash
sudo apt install -y pandoc weasyprint fonts-noto-core
```

**Pipeline (established, avoids LaTeX entirely):**

```bash
pandoc NOTE.md -s --metadata lang=de-DE -o /tmp/note.html
weasyprint /tmp/note.html NOTE.pdf
```

- **pandoc** renders the note content into clean HTML
- **WeasyPrint** prints the HTML to PDF with CSS-based layout (page numbers, justified text, hyphenation)
- **CRITICAL — hyphenation:** use `--metadata lang=de-DE`, **never** `lang=de`. WeasyPrint/Pyphen silently produces **no hyphenation at all** for "de"; only the region code "de-DE" activates the German hyphenation patterns.
- **Verify hyphenation:** `pdftotext NOTE.pdf - | grep -c '‐$'` — counts U+2010 hyphens at line ends (a U+2010, not the ASCII hyphen). Zero hits means hyphenation silently failed.
- **Deliver:** attach the PDF via the cloud-free route (§6.9) and keep the note and the PDF in sync — any content change means regenerating the PDF from the note, not editing both by hand.

### 8.6 Conventions for the Standard Research Assignment

A canonical full research assignment looks like this:

> "Lade mir Arbeiten zur Prüfungsangst und Bewältigungsstrategien dafür herunter, lese die Volltexte und fasse sie kritisch in einer Zotero-Notiz zusammen, konvertiere diese Notiz dann auch in eine PDF-Datei und hänge Notiz und PDF an ein erstelltes Item vom Typ Dokument an, dem du einen treffenden Titel gegeben hast."

The user does not need to state any details — the agent is expected to already know these conventions from its imported memory files:

- **Source identification via OpenAlex** (https://openalex.org/) — never invent or guess sources; verify DOIs. OpenAlex is the identification layer, not the download layer; PDFs come from publisher/OA sources (§6.3).
- **Fulltext obligation:** if fulltexts are available, they must actually be read before writing the note — never summarize from abstracts or prior knowledge alone. Statements not backed by a fulltext are marked "[Praxishinweis]"; sources of which only the abstract was read are marked "[nur Abstract]".
- **Page citations are mandatory** (§6.2) — every claim from a fulltext carries its page number.
- **Note structure (German, for medical/dental readers):** explain technical terms; state study design and evidence level per source; grade the evidence (GRADE logic / evidence pyramid); translate effect sizes into clinical relevance; alphabetical author-year reference list with DOI hyperlinks and the "Volltext"/"nur Abstract" tag after each source; search strategy and creation date at the end of the note.
- **Deliverables:** one Zotero item of type `document` with a meaningful title; the note AND the converted PDF attached as child attachments (PDF via §8.5).

---

## 9. Evidence-Based Literature Summaries: The Standard Workflow

This section generalizes the reference setup's research standard (originally built around the Beaver prompt, v5). Any agent producing literature summaries for clinical or academic readers should follow it. It is deliberately strict — every rule exists because its absence produced a quality incident.

### 9.1 Source identification: OpenAlex only

Literature **identification** runs exclusively through [OpenAlex](https://openalex.org/). Web search, Wikipedia, and prior/model knowledge may provide *context* (domain framing, terminology) — never the source list. There is no retroactive substitute: a summary whose sources cannot be traced to a systematic search is defective even if the sources themselves are good.

**Standard search protocol:**
1. Decompose the question into concept blocks (population / intervention / outcome); build synonym OR-chains per block.
2. Filter: `type` (meta-analyses/systematic reviews first, then RCTs), `from_publication_date`, `is_retracted:false`, optionally open access.
3. Deliberately mix sortings: `relevance_score` (first pass), `cited_by_count` (classics), `publication_date` descending (recent work).
4. Snowball through the OpenAlex graph: `referenced_works` (backward) + `cited_by` (forward) of the core sources — the strongest lever against gaps.

### 9.2 Quality assessment

- **Evidence pyramid first (GRADE logic, wavy transitions):** the pyramid is a guideline, not a rigid hierarchy. Quality can be downgraded for methodological flaws, imprecision, inconsistency, indirectness or publication bias — and upgraded for very large effects or dose-response. A meta-analysis is a tool for *consuming* evidence, not evidence in itself: a meta-analysis of observational studies is not equivalent to one of RCTs. For diagnostic, prognostic, etiological or harm questions, prospective cohorts and case-control studies are methodologically valid and must not be marginalized as "lower-tier".
- Name the **study design and evidence level** of every cited source explicitly ("meta-analysis of RCTs", "single RCT", "prospective cohort", "case series", "in-vitro").
- `is_retracted` (Retraction Watch integration) is checked for every source; retracted work is flagged separately.
- `cited_by_count` only in time-slice-relative comparison (5 years vs. 1 year); journal quality is proxied by source h-index; independent replication beats a single highly cited paper.
- When sources conflict, prefer the higher evidence level *if methodological quality is comparable* — a poorly executed RCT can be less reliable than a good observational study. State the weighting transparently.

### 9.3 Fulltext obligation and availability list

After the first search round, list all identified relevant papers with their availability status ("fulltext available" / "abstract only" / "not in library — obtainable externally"), each with DOI and PMID, and present the list **before writing the summary**. If fulltexts are available, they must actually be read — never summarize from abstracts, search hits or prior knowledge alone. After external imports (e.g. DOI import via OpenAlex), re-check whether PDFs have appeared in the meantime — they often arrive minutes later or after the next sync; availability markers and the analysis itself may need correction.

### 9.4 Precision rules

- **Translate effect sizes.** Small percentages must be made clinically meaningful (absolute risk, clinical translation, methodological framing) — never left uncommented.
- Large time-window spans (e.g. "11–104 weeks") are stated as a limitation when the original work offers no stratification ("the pooled effect cannot be resolved by study duration").
- **Verify citation chains:** secondary sources (reviews, editorials) can contain citation errors; for central findings, retrieve and cite the original work.
- **Multiple adjustment models** (unadjusted, adjusted model 1/2, within-study) go in separate table rows — mixing them produces wrong confidence intervals.
- **Heterogeneity and subgroups:** meta-analyses and meta-regressions are reported with I², the χ²-test p-value, and subgroup results (if reported).
- State irresolvable limitations (missing stratification, unreported multiplicity correction, lack of blinding) explicitly — do not hide them.
- **Active contradiction:** contradict the user (politely, with the correct finding and its source) when a factual claim conflicts with the data or accepted methodological standards; on mere opinions or preferences, don't.
- When citing from fulltexts, always include the page number (§6.2), verified via running heads for long documents (§6.12).

### 9.5 Note format

- **No H1** in the note body — Zotero stores the title separately; notes start at H2.
- DOIs are always hyperlinks, never plain text: `[doi:10.xxxx](https://doi.org/10.xxxx)`.
- Behind every reference: "fulltext" or "abstract only" — depending on what was actually evaluated.
- The reference list is a conventional alphabetical list (Vancouver variant, all authors, journal per Index Medicus, year, volume(issue), pages, DOI, PMID) — not a table, not a truncated author/title/year list.
- After the references: the search strategy, data basis, and creation date (plus revision date if later revised).
- Closing line: an explicit AI disclaimer ("This AI literature search was not reviewed. No guarantee of factual correctness.") with a link to the methodological documentation.
- **HTML quoting gotcha:** when writing note HTML through item-creation tools, use **single quotes** in HTML attributes (`<a href='...'>`) — double quotes can break the JSON argument encoding and produce a misleading "params must be an array" error.

### 9.6 Deliverables as a triple

The standard deliverable for a completed evidence-based review is a **Zotero triple**: (1) an item of type `document` whose creator names the agent *and* the concrete LLM with version, dated and placed in the relevant collection; (2) the summary as a child note (simple HTML per §9.5); (3) the rendered PDF attached as a child attachment (pipeline §8.5), stored locally so it survives cloud quota exhaustion (§6.9). After writing, spot-check the note HTML via a child-inclusive item read.

---

## 10. Reviewing LLM-Produced Texts

A five-pass standard for checking and revising substantial LLM-generated texts (reports, essays, documentation) — not for chat one-liners. Two ground rules:

1. **Separate reviewing from revising.** Never deliver the review report and the revised text in the same answer — otherwise it is undecidable what was checked and what was changed.
2. **Clarify the assignment first:** text type, audience, purpose, target length, and which interventions are permitted.

**The five passes, in order:**

- **(a) Claim inventory:** list every checkable statement — numbers, years, names, quotes, causal claims.
- **(b) Evidence check of existing sources:** are citations complete? Does each source actually support the claim? Primary source or citation chain (press release → study)? Funding or conflicts of interest?
- **(c) Argumentation:** first a *value-neutral reconstruction* (main thesis, supporting arguments), then critique — faulty causality, missing inferential steps, the strongest counterargument.
- **(d) Length:** cut by information density (protected: numbers with sources, proper names, first and last paragraphs; cut repetitions and hedging first). Extend only after a gap diagnosis (missing evidence, untreated objection, misplaced emphasis).
- **(e) Language:** language-only changes, documented in a change table (original / new / reason); meaning-shifting changes are proposed, not applied.

**Cross-cutting rules:**
- **Uncertainty visible:** nothing is confirmed from memory; unverified items are marked "please check yourself" — that list is the user's worklist, not a flaw to hide.
- **Commands and features** cited in the text are verified against the actually installed version (see also the Memory-MCP rules file, R12).
- **Every data table carries its sources** — measurement date, tools/versions, origin of the numbers (see R13).
- **Cross-model critique:** for the assessment pass, a *second* agent (another CLI agent on the same memory/Zotero infrastructure) is worthwhile — models rate their own outputs milder (self-preference bias).
- **Baseline rules remain:** page-cited sources, fulltext evaluation, and honest marking of what was only read as abstract.

---

## 11. Links & Resources

### MCP Servers & Tools
- **Zotero-MCP-server:** https://github.com/54yyyu/zotero-mcp — Docs: https://stevenyuyy.com/zotero-mcp/
- **Zoteus:** https://github.com/oscardvs/zoteus
- **cli-anything-zotero:** https://github.com/PiaoyangGuohai1/cli-anything-zotero
- **pyzotero:** https://pyzotero.readthedocs.io/ — GitHub: https://github.com/urschrei/pyzotero
- **Zotero Local API docs:** https://www.zotero.org/support/dev/client_coding/javascript_api

### Academic & Semantic
- **OpenAlex:** https://openalex.org/ — Open scholarly graph
- **Better BibTeX for Zotero:** https://github.com/retorquere/zotero-better-bibtex
- **Beaver for Zotero:** https://github.com/jlegewie/beaver-zotero — https://www.beaverapp.ai

### Reference
- **Zotero-MCP Architecture Analysis:** https://www.logies.de/zotero-mcp-architektur-analyse.html
- **Zotero-MCP vs. Beaver Comparison:** https://www.logies.de/zotero-mcp-server-beaver-vergleich.html

---

## 12. Changelog

| Date | Changes |
|------|---------|
| 2026-10-02 | New §7.6: OOM kills during full-text index builds on low-RAM Crostini/BTRFS devices — symptom (build and agent session die together), diagnosis via `dmesg`, 4 GB swapfile recipe with the NOCOW-before-fallocate ordering trap, measured verification (two aborts without swap vs. completed build at 2.1 GB swap peak); cross-reference added to §6.11. Mirrors the Setup-Guide §6.3 box "If the index build is killed for lack of memory". |
| 2026-08-27 | Initial export from Qwen Code memory. Covers tool comparison, benchmarks, best practices, troubleshooting. |
| 2026-08-29 | Revision after two more days of operation: full-index semantic-search benchmark (§4.3) with the resulting tool-division change; parent-key rule (§6.8); cloud-independent attachment import (§6.9); fulltext-preserving incremental index updates (§6.10); version updates (zotero-mcp-server v0.11.0 incl. SQLite search backend, Zoteus v1.9.0); plugin list updated (ZotMoov removed — never activated in practice, its file-relocation purpose conflicts with storage-based workflows); Zutilo entry now mentions collection select links. |
| 2026-08-29 (later) | Issues #29 and #30 resolved by the maintainer on main, shipping in v1.10.0 (both issues closed within ~5 h of filing): two-stage binary-code search measured 42× at the reference geometry (2,269 ms → 54 ms), local fulltext fallback with `fileSource` reporting, PDF outline and precise page ranges, local EPUB extraction. §2.2/§3/§4.3 updated accordingly. |
| 2026-08-30 | Zotero 10.0.1 upgrade verified: new §8.3 (what changes for agents — no breaking changes, **Local API now supports writes**, faster fulltext indexing, DB auto-compaction); §2.4 pyzotero local-write note updated accordingly. |
| 2026-08-31 | New §8.5 (standard Zotero-note → PDF workflow: pandoc → WeasyPrint, critical `lang=de-DE` hyphenation gotcha, hyphenation verification, note/PDF sync) and §8.6 (conventions for the canonical full research assignment: OpenAlex source identification, fulltext obligation with [Praxishinweis]/[nur Abstract] markers, page citations, note structure, deliverables). |
| 2026-09-03 | Zoteus v1.12.0 deployed as the **sole** Zotero MCP server; §1 architecture, §2.2 version and weaknesses, §3 tool division, §4.3 re-benchmark (0.33–0.84 s/query steady state) all updated accordingly. New §4.4 (OpenAI 429 kills index builds — no retry/backoff at v1.12.0, fulltext progress lost on resume, workaround dials `ZOTEUS_EMBED_BATCH_SIZE=256` / `ZOTEUS_EMBED_BATCH_DELAY_MS=8000`, [issue #48](https://github.com/oscardvs/zoteus/issues/48)); new §6.11 (automatisms: daily fulltext-preserving incremental index update, weekly npm update check, why the index itself needs no backup); new §6.12 (page verification via running heads instead of `pageApprox`, ~5 pages off on a 442-page book). |
| 2026-09-03 (later) | **Zoteus v1.13.0 deployed** (released 08:00 UTC): §2.2 version bump and rewritten weakness bullets; §4.4 resolution — OpenAI 429 backoff + retry (`ZOTEUS_EMBED_MAX_RETRIES`), fulltext checkpoint survives failed builds (resume embeds only missing vectors), dials 256/8000 now the officially documented Tier-2 recommendation, status reports measured `embedRate` + `passagesWithoutVectors`; **local embedding route** added (`ZOTEUS_EMBEDDINGS=local` + `ZOTEUS_EMBEDDING_MODEL=Xenova/multilingual-e5-small`, E5 prefixes automatic, ~470 MB full-precision download, [issue #43](https://github.com/oscardvs/zoteus/issues/43)); index survived the upgrade untouched (schema 1→2 in place, no re-computation); Setup-Guide §6.3 updated in kind (DE/EN). |
| 2026-09-16 | **Zoteus updated to v1.20.1 (running in the reference setup):** §2.2 version and tool count (31); local-embedder **weight precision selectable since v1.14.0** (#50: `ZOTEUS_EMBEDDING_DTYPE` is part of the embedder identity; q8 e5-small ≈ 129 MB; measured on a full library: 80.6 % identical top-10 slots q8↔fp32 vs. 59.4 % fp32↔OpenAI — quantization costs less than a model change) and **curated pooling registry** (#52: pooling follows the model, unknown ids load open with an unvalidated warning); **production note:** local e5-small full rebuild (2026-09-13, overnight ~5.7 h, 272,896 passages incl. 234,861 full-text passages — books fully indexed, no 1M-char cap truncation). §6.10 re-confirmed: an update without the fulltext flag reads no PDF bodies at all. New **§6.13 one-channel principle** for index automatisms (single channel, no auto-resume, status gate, one known session). New **§9 evidence-based literature summaries** (OpenAlex-only identification, GRADE-style assessment, fulltext obligation, precision rules, note format, document+note+PDF triple) and **§10 reviewing LLM-produced texts** (five-pass standard, separation of review and revision, cross-model critique). Links & Changelog renumbered to §11/§12. |