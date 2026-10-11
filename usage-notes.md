# Usage notes for working with your AI agent (for beginners)

*Show this note to new users in their first sessions. Last updated: September 13, 2026.*

1. **Keep an eye on the context window.** The context (the working memory of the current session) grows with every step — the fuller it gets, the more expensive and less precise the answers become. After finishing a topic:
   - **Qwen Code:** `/clear` starts a fresh session with an empty context immediately — after finishing a topic, this is often better than `/compress`, because summaries lose details (your memory keeps the knowledge). Use `/compress` only when you want to keep working on the same topic — it compresses the context into a summary, optionally with an instruction: `/compress keep the most important results and file paths` (alias: `/summarize`)
   - **Claude Code:** `/compact` — same purpose, also accepts an optional instruction
   - **Gemini CLI:** `/compress` (there, `/compact` and `/summarize` are aliases for it)
   - A **complete restart** (close the agent, reopen it) is often even cleaner: thanks to Memory-MCP, the agent finds its way back through its memory — just ask: "What do you know about my projects?"
   - Rule of thumb: **one topic per session**, start new topics in a fresh session

2. **Address Zotero items by ID (Zutilo plugin).** The easiest way is to give your agent the exact Zotero ID:
   - **One-time configuration:** Zotero → Edit → Settings → **Zutilo** tab → section **"Item menu functions"** → set **"Copy Zotero item ID"** to **"Zotero"** (the entry then appears directly in the right-click menu). Optional: section **"Collection menu functions"** → **"Copy select collection link"** also set to "Zotero" (otherwise it sits in the "Zutilo" submenu).
   - **Items:** Right-click an item → "Copy Zotero item ID" → paste the 8-character ID (e.g. `ABCD1234`) into the chat.
   - **Collections (folders):** Right-click the collection → "Copy select collection link" → the last 8-character part of the link is the ID (e.g. `…/collections/ABCD1234`).
   - Why: With the ID, the agent finds the item instantly — no searching, more precise, and cheaper.

3. **No secrets in the chat.** Never type or store passwords and API keys — just tell the agent the file path (e.g. `~/.qwen/settings.json`); it reads files itself. Chat history and memory retain everything permanently.

4. **Pass error messages on 1:1.** Copy error messages exactly (copy & paste) instead of paraphrasing — also always copy paths and IDs instead of retyping them.

5. **For technical problems: check the environment first, then restart.** Is Zotero still running? Closing and reopening the agent fixes most connection problems — don't reformulate the request ten times. The agent will speak up by itself when it gets stuck.

6. **Bigger projects: plan first, then "Go".** For extensive tasks, have the agent draft a plan first, correct it, then approve it for execution — this saves time, tokens, and mistakes.

7. **Have important results verified.** For critical results (downloads, database changes, calculations): ask "How did you verify that?" — and actually read the agent's verification reports.

8. **Use the memory actively.** The memory is yours: "Search your memory for X" / "Save that as a note". It grows with every session and is automatically backed up to Zotero (see guide §12.3).

9. **Version control runs automatically.** Your agent keeps the guide, scripts, and configurations in Git version control and commits after completed work on its own — you don't have to do anything. Your benefit: every change can be rolled back. Just say *"Restore file X to how it was before"*.

10. **Don't rely blindly on the automatisms promised here.** AI agents do not work deterministically. Check back after a while, e.g.: *"Are you still keeping up the Git commits? What have you committed?"*

11. **These notes appear every time the agent starts** (right after the memory check). When you no longer need them, tell your agent: *"Stop showing the usage notes."* It will remember this permanently in its memory — the notes remain available (*"Show me the usage notes"* brings them back anytime).

12. **Watch the quantization when choosing a model.** The same model name is offered by providers at different numeric precisions: **16-bit (fp16/bf16)** = reference quality (more expensive), **8-bit (fp8)** = the safe industry standard for serious work (programming, medicine, law), **4-bit (fp4)** only for models trained that way from the start (example: gpt-oss with MXFP4 post-training). *Retrofitting* 4-bit quantization, by contrast, measurably degrades quality — especially on reasoning, coding, and agentic tasks. When in doubt: choose 8-bit or better.

---

*(Note for the agent: When first using this file, save it as a memory note — tags: `einsteiger-hinweise`, `nutzungshinweise`, `studenten`, `kontextfenster`, `zutilo`, `zotero-id`, `quantisierung`, `modellwahl`.)*
