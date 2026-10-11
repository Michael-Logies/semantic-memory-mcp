# Semantic Memory MCP

**Persistent, semantic memory for AI coding agents — a single Python file, entirely local.**

`semantic-memory-mcp` is a self-hosted [Model Context Protocol](https://modelcontextprotocol.io) server that gives AI coding agents a durable, searchable memory across sessions. It is built as one Python file, stores everything in a single local SQLite database, and needs no cloud service and no API key.

## What it is

- **Cross-session memory:** notes survive between sessions; the agent searches its own memory at the start of every task instead of relying on the user to re-explain context.
- **Hybrid retrieval:** keyword full-text search (SQLite FTS5) and semantic vector search (sqlite-vec) are combined through Reciprocal Rank Fusion — a note can be found by exact wording *or* by meaning.
- **Multilingual embeddings:** quantized ONNX model (`Xenova/multilingual-e5-small`, ~22 MB) runs locally on CPU. English and German work equally well; no OpenAI key required.
- **Trivially auditable:** the whole server is a single readable file (`memory_server.py`, ~1,100 lines). There is no hidden state beyond the SQLite file.

### Tools

| Tool | Purpose |
|------|---------|
| `memory_store` | Save a note (with tags and source) |
| `memory_search` | Hybrid search (semantic + full-text), optional token budget |
| `memory_get` / `memory_recent` | Read one note by ID / list recent notes |
| `memory_update` / `memory_delete` | Edit or remove a note |
| `memory_status` | Database statistics |
| `memory_zotero_sync` | Optional: back up memory content into a Zotero library |

## Why

AI coding agents forget everything between sessions. File-based memory (markdown indexes that get reloaded) is keyword-based: it has no semantic recall and no ranking — a note saved as *"embedding model comparison"* will not be found when the agent later asks *"which embedding did we test?"*.

This server was built as a working reference implementation and **measured, not assumed**: retrieval quality was tracked throughout development. Recall@5 — the share of queries where the correct note is among the top 5 hits — rose from **68.4 % to 89.2 %** across successive, individually measured optimization steps (chunking, embedding model choice, query rewriting, and more). The full measurement series with a 22-study comparison table is documented here:

**[The Memory Server in the Lab](https://www.logies.de/memory-server-research.html)**

## Installation

**Prerequisites:** Linux, Python 3.11+ with `venv`, ~250 MB free disk space. The server is written for Linux, but not limited to it: since everything is one Python file, any AI assistant can read the script and adapt the installation for Windows or macOS.

```bash
git clone https://github.com/Michael-Logies/semantic-memory-mcp.git
cd semantic-memory-mcp
bash setup.sh      # creates the venv, installs dependencies, downloads the model
bash verify.sh     # checks the installation, writes a report
```

The embedding model is downloaded automatically from Hugging Face (`huggingface_hub`, no account needed).

Then register the server in your AI agent's MCP configuration (example for Qwen Code, `~/.qwen/settings.json`):

```json
{
  "mcpServers": {
    "memory": {
      "command": "/home/<user>/.venv-memory/bin/python3",
      "args": ["/path/to/semantic-memory-mcp/memory_server.py"],
      "transport": "stdio"
    }
  }
}
```

See `mcp-config.json` for a template. `usage-notes.md` contains practical guidance for working with an agent that has persistent memory (context management, what to store, tagging); the German version ships as `nutzungshinweise.md`.

## Documentation for your AI agent

Two documents ship with this repository, written for the agent rather than the human (both CC0-licensed):

- **`memory-mcp-knowledge.md`** — the rules and learning protocol for operating the memory server in daily agent work: when to read, when to write, what to store, and how to tag it.
- **`zotero-mcp-agent-knowledge.md`** — hard-won knowledge about connecting Zotero with AI agents via MCP servers: tool comparison (zotero-mcp-server, Zoteus, pyzotero, CLI access), benchmarks, and best practices. Since 31 August 2026, **Zoteus** is the sole Zotero MCP server in the reference setup; the older ChromaDB-based material is kept as background, not as a recommendation.

## The bigger picture

This server is the memory layer of a reproducible **AI workstation for students** — a complete setup guide (research, learning, literature work on a Linux machine) documented step by step here:

- **[AI Workstation for Students: Research, Learning, and More](https://www.logies.de/ai-workstation-linux-vm.html)** (English)
- **[KI-Arbeitsplatz fürs Studium: Recherche, Lernen und mehr](https://www.logies.de/studienarbeitsplatz-linux-vm.html)** (German)

Memory comes in two layers in that setup: this server stores the *working memory* of day-to-day collaboration with the agent, while scholarly literature lives in a Zotero research database made searchable by meaning through a second MCP server ([Zoteus](https://github.com/oscardvs/zoteus)). How the two fit together is described on the project pages:

- [Cross-Session Memory for AI Agents](https://www.logies.de/memory-mcp-for-ai-agents.html) (English)
- [Cross-Session-Memory für KI-Agenten](https://www.logies.de/memory-mcp-fuer-ki-agenten.html) (German)

## Status and maintenance

The server has been in daily production use since August 2026 as the memory of a working AI workstation. It is developed and maintained by [Michael Logies](https://github.com/Michael-Logies) in close collaboration with AI coding agents — see the project pages above for how it was built and evaluated. The server is provided **as-is**: bug reports are welcome and handled on a best-effort basis, but there is no formal support.

## License

MIT — see [LICENSE](LICENSE).
