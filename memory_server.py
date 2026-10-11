#!/usr/bin/env python3
"""
Memory MCP Server — ONNX + SQLite (sqlite-vec + FTS5) + FastMCP

Hybrid search (vector + full-text) via Reciprocal Rank Fusion (RRF).
Multilingual embeddings via Xenova/multilingual-e5-small (quantized ONNX).
"""

import hashlib
import html
import json
import os
import re
import socket
import sqlite3
import tempfile
import threading
import time
import urllib.error
import urllib.request
from contextlib import closing
from pathlib import Path

import numpy as np
import onnxruntime as ort
import sqlite_vec
from fastmcp import FastMCP
from tokenizers import Tokenizer

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
MODEL_DIR = Path(os.environ.get(
    "MEMORY_MODEL_DIR", str(Path.home() / ".qwen" / "models" / "multilingual-e5-small")))
MODEL_PATH = MODEL_DIR / "model_quantized.onnx"
TOKENIZER_PATH = MODEL_DIR / "tokenizer.json"
DB_PATH = Path(os.environ.get("MEMORY_DB_PATH", str(Path.home() / ".qwen" / "memory.db")))

# ---------------------------------------------------------------------------
# Zotero sync configuration (memory_zotero_sync)
# ---------------------------------------------------------------------------
ZOTERO_SYNC_CONFIG = Path(os.environ.get(
    "ZOTERO_SYNC_CONFIG", str(Path.home() / ".qwen" / "zotero-sync.json")))
ZOTERO_SYNC_KEY_DEFAULT = Path.home() / ".qwen" / "zotero-api-key"
ZOTERO_SYNC_INTERVAL_H = float(os.environ.get("ZOTERO_SYNC_INTERVAL_H", "6"))
ZOTERO_SYNC_LAST_FILE = DB_PATH.parent / ".zotero-sync-last"
ZOTERO_SYNC_PREFIX = "KI-Gedächtnis-Backup"
ZOTERO_SYNC_PROTECT_THRESHOLD = 5
ZOTERO_SYNC_CHUNK = 49
_sync_state = {"running": False, "last_error": None, "last_result": None}

# ---------------------------------------------------------------------------
# Embedding helpers
# ---------------------------------------------------------------------------
_ort_session: ort.InferenceSession | None = None
_tokenizer: Tokenizer | None = None


def _download_model():
    """Download the embedding model on first use (mirrors setup.sh).

    Only runs when the model files are missing — installations created by
    setup.sh already have them, so this is a no-op there. The ONNX file sits
    in the repo under onnx/ but the server expects it flat next to
    tokenizer.json, hence the explicit mapping.
    """
    import shutil
    from huggingface_hub import hf_hub_download

    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    mapping = {
        "onnx/model_quantized.onnx": "model_quantized.onnx",
        "tokenizer.json": "tokenizer.json",
        "config.json": "config.json",
        "tokenizer_config.json": "tokenizer_config.json",
        "special_tokens_map.json": "special_tokens_map.json",
    }
    for repo_path, dest_name in mapping.items():
        dest = MODEL_DIR / dest_name
        if dest.exists():
            continue
        src = hf_hub_download("Xenova/multilingual-e5-small", repo_path)
        shutil.copyfile(src, dest)


def _ensure_embedder():
    global _ort_session, _tokenizer
    if _ort_session is None:
        if not MODEL_PATH.exists() or not TOKENIZER_PATH.exists():
            _download_model()
        _ort_session = ort.InferenceSession(str(MODEL_PATH))
    if _tokenizer is None:
        _tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
        # E5 accepts at most 512 tokens; longer input crashes ONNX inference
        _tokenizer.enable_truncation(max_length=512)


def _embed(text: str, *, prefix: str = "passage") -> list[float]:
    """Compute a normalised 384-d embedding for *text*."""
    _ensure_embedder()
    input_text = f"{prefix}: {text}"
    encoding = _tokenizer.encode(input_text)
    input_ids = np.array([encoding.ids], dtype=np.int64)
    attention_mask = np.array([encoding.attention_mask], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids)

    (last_hidden_state,) = _ort_session.run(
        None,
        {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
            "token_type_ids": token_type_ids,
        },
    )

    # Mean pooling
    mask = attention_mask.astype(float).reshape(-1, 1)
    pooled = np.sum(last_hidden_state[0] * mask, axis=0) / np.sum(mask, axis=0)

    # L2 normalise
    norm = np.sqrt(np.sum(pooled**2))
    if norm > 0:
        pooled = pooled / norm
    return pooled.tolist()


# ---------------------------------------------------------------------------
# Chunked embedding — E5 accepts at most 512 tokens, so notes longer than
# CHUNK_MAX_TOKENS are embedded as consecutive chunks; the vector search
# aggregates chunk hits per note (minimum distance).
# CHUNK_OVERLAP_TOKENS = 0 since 19.9.2026 (Phase 3): overlap removed — no
# recall benefit in the literature (SeCom) and it inflated the index; the
# pre-change state is recoverable by setting the constant back to 50.
CHUNK_MAX_TOKENS = 450
CHUNK_OVERLAP_TOKENS = 0

# Hybrid-search weights, env-tunable since 20.9.2026 (Phase 3 grid):
# - FTS_KEY_WEIGHT: bm25 weight of the key column (fact-key boost).
# - FTS_ARM_WEIGHT: relative weight of the keyword arm in the RRF fusion
#   (vector arm = 1.0). Grid 20.9.2026 (3 baskets, 126 queries): equal
#   arms let lexical double-hits outvote vector-only hits on
#   vocabulary-gap queries; 0.85 recovers all four Runde-3 RRF losses
#   (R@5 68.4->76.3 %) and lifts Runde 2 to its best values (85.7 %),
#   at the cost of one lexical-only basket-1 hit (97.8->95.7 %, MRR
#   -0.003). Plateau 0.85-0.55 (robust); K (60) has no effect.
FTS_KEY_WEIGHT = float(os.environ.get("MEMORY_FTS_KEY_WEIGHT", "3.0"))
RRF_K = int(os.environ.get("MEMORY_RRF_K", "60"))
FTS_ARM_WEIGHT = float(os.environ.get("MEMORY_FTS_ARM_WEIGHT", "0.85"))

# Query expansion (20.9.2026): everyday-word -> domain-term bridges loaded
# from synonyms.json. At search time, words matching a bridge append their
# expansion terms to BOTH the FTS terms and the embedding query text, so
# vocabulary gaps ("Tresor" vs KeepassXC) stop hiding results. Terms already
# present in the query are never duplicated. MEMORY_SYNONYMS=0 or a missing
# file disables expansion entirely.
SYNONYMS_PATH = os.environ.get(
    "MEMORY_SYNONYMS_PATH", str(Path.home() / ".qwen-memory" / "synonyms.json"))
SYNONYMS_ENABLED = os.environ.get("MEMORY_SYNONYMS", "1") != "0"

_synonym_map: dict[str, list[str]] | None = None


def _synonym_expansion(query: str) -> list[str]:
    global _synonym_map
    if not SYNONYMS_ENABLED:
        return []
    if _synonym_map is None:
        _synonym_map = {}
        try:
            with open(SYNONYMS_PATH, encoding="utf-8") as f:
                for group in json.load(f).get("groups", []):
                    for m in group.get("match", []):
                        _synonym_map[m.lower()] = list(group.get("expand", []))
        except (OSError, ValueError):
            _synonym_map = {}
    expand: list[str] = []
    # Match-Wörter gegen Randzeichen bereinigen ("anzulegen?" am Satzende wäre sonst unsichtbar)
    _norm = lambda w: w.strip(".,;:!?\"'„“()[]–—").lower()
    seen: set[str] = {_norm(w) for w in query.split()}
    for w in query.split():
        for t in _synonym_map.get(_norm(w), []):
            if t.lower() not in seen:
                seen.add(t.lower())
                expand.append(t)
    return expand

_count_tokenizer: Tokenizer | None = None


def _count_tokenizer_() -> Tokenizer:
    global _count_tokenizer
    if _count_tokenizer is None:
        _count_tokenizer = Tokenizer.from_file(str(TOKENIZER_PATH))
    return _count_tokenizer


def _count_tokens(text: str) -> int:
    return len(_count_tokenizer_().encode(text).ids)


def _split_segments(text: str) -> list[str]:
    """Split at blank lines; a markdown heading is glued to the paragraph
    that follows it so a section never starts without its heading."""
    segments: list[str] = []
    pending_heading = ""
    for raw in text.split("\n\n"):
        seg = raw.strip()
        if not seg:
            continue
        if seg.startswith("#"):
            pending_heading = f"{pending_heading}\n\n{seg}" if pending_heading else seg
        elif pending_heading:
            segments.append(f"{pending_heading}\n\n{seg}")
            pending_heading = ""
        else:
            segments.append(seg)
    if pending_heading:
        segments.append(pending_heading)
    return segments


def _chunk_text(text: str) -> list[str]:
    if _count_tokens(text) <= CHUNK_MAX_TOKENS:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    current_tokens = 0
    for seg in _split_segments(text):
        seg_tokens = _count_tokens(seg)
        if seg_tokens > CHUNK_MAX_TOKENS:
            if current:
                chunks.append("\n\n".join(current))
                current, current_tokens = [], 0
            # oversized single segment: non-overlapping token windows
            ids = _count_tokenizer_().encode(seg).ids
            step = CHUNK_MAX_TOKENS - CHUNK_OVERLAP_TOKENS
            start = 0
            while start < len(ids):
                window = ids[start : start + CHUNK_MAX_TOKENS]
                chunks.append(_count_tokenizer_().decode(window))
                if start + CHUNK_MAX_TOKENS >= len(ids):
                    break
                start += step
            continue
        if current and current_tokens + seg_tokens > CHUNK_MAX_TOKENS:
            chunks.append("\n\n".join(current))
            current, current_tokens = [], 0
        current.append(seg)
        current_tokens += seg_tokens
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def _reembed_note_chunks(db: sqlite3.Connection, note_id: int, content: str) -> int:
    """Replace all vector chunks of a note and return the chunk count."""
    db.execute(
        "DELETE FROM note_chunks WHERE rowid IN "
        "(SELECT chunk_rowid FROM note_chunks_map WHERE note_id = ?)",
        (note_id,),
    )
    db.execute("DELETE FROM note_chunks_map WHERE note_id = ?", (note_id,))
    next_rowid = db.execute("SELECT COALESCE(MAX(rowid), 0) + 1 FROM note_chunks").fetchone()[0]
    count = 0
    keyline = _keyline_of(content) if KEYLINE_PREFIX_EMBEDDING else ""
    for idx, chunk in enumerate(_chunk_text(content)):
        passage = chunk
        if keyline and not chunk.strip().startswith(keyline):
            passage = f"{keyline}\n\n{chunk}"
        emb = np.array(_embed(passage, prefix="passage"), dtype=np.float32).tobytes()
        db.execute(
            "INSERT INTO note_chunks(rowid, embedding) VALUES (?, ?)",
            (next_rowid, emb),
        )
        db.execute(
            "INSERT INTO note_chunks_map(chunk_rowid, note_id, chunk_idx) VALUES (?, ?, ?)",
            (next_rowid, note_id, idx),
        )
        next_rowid += 1
        count += 1
    return count


def _snippet_text(text: str, max_chars: int = 400) -> str:
    """Compressed retrieval output (RECOMP-style): the best chunk, cut at a
    word boundary. Additive search output only — never used for ranking."""
    text = text.strip()
    if len(text) <= max_chars:
        return text
    cut = text[:max_chars]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut + " …"


# Server-side fact key (Phase 3 item 1a): every chunk embedding carries the
# note's first line as key context. Evidence: LongMemEval fact-key (+9 %),
# A-MEM "attributes into embedding" — the key always ADDS to the body,
# never replaces it (key-only collapses, summary bodies hurt).
# Revert switch: set to False and re-run phase3/rechunk-overlap0.py.
KEYLINE_PREFIX_EMBEDDING = True


def _keyline_of(content: str) -> str:
    """First non-blank line of the note (the Phase-0 keyline), trimmed to
    200 chars — the server-side fact key, derived, not stored separately."""
    for line in (content or "").strip().splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return ""


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def _get_db() -> sqlite3.Connection:
    """Return a connection to the memory database (with extensions loaded)."""
    _zsync_lazy_check()
    db = sqlite3.connect(str(DB_PATH), timeout=30)
    db.row_factory = sqlite3.Row
    db.enable_load_extension(True)
    sqlite_vec.load(db)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def _init_db():
    """Create tables if they don't exist."""
    with closing(_get_db()) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS notes (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                content     TEXT    NOT NULL,
                tags        TEXT    NOT NULL DEFAULT '',
                source      TEXT    NOT NULL DEFAULT '',
                created_at  TEXT    NOT NULL,
                key         TEXT    NOT NULL DEFAULT ''
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                content,
                key,
                content=notes,
                content_rowid=id
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS notes_vec USING vec0(
                embedding float[384]
            );

            CREATE VIRTUAL TABLE IF NOT EXISTS note_chunks USING vec0(
                embedding float[384]
            );

            CREATE TABLE IF NOT EXISTS note_chunks_map (
                chunk_rowid INTEGER PRIMARY KEY,
                note_id     INTEGER NOT NULL,
                chunk_idx   INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_chunks_note ON note_chunks_map(note_id);

            CREATE INDEX IF NOT EXISTS idx_notes_created_at ON notes(created_at);
        """)
        # Migrate databases created before the derived key column existed:
        # notes and notes_fts both need it, else startup and memory_store
        # crash with "no such column: key"
        if "key" not in {r[1] for r in db.execute("PRAGMA table_info(notes)")}:
            db.execute("ALTER TABLE notes ADD COLUMN key TEXT NOT NULL DEFAULT ''")
        if "key" not in {r[1] for r in db.execute("PRAGMA table_info(notes_fts)")}:
            db.execute("DROP TABLE notes_fts")
            db.execute(
                "CREATE VIRTUAL TABLE notes_fts USING fts5("
                "content, key, content=notes, content_rowid=id)"
            )

        # Heal notes missing the derived key column (server-side fact key)
        for row in db.execute(
            "SELECT id, content FROM notes WHERE key = '' AND TRIM(content) != ''"
        ).fetchall():
            db.execute(
                "UPDATE notes SET key = ? WHERE id = ?",
                (_keyline_of(row["content"]), row["id"]),
            )
        db.commit()

        # Rebuild FTS index from the content table — heals stale or missing
        # entries at every start; the content table is the source of truth.
        # Runs after the key heal so migrated FTS rows carry the keys too.
        db.execute("INSERT INTO notes_fts(notes_fts) VALUES('rebuild')")
        db.commit()

        # Re-embed notes missing from the chunk vector table
        missing = db.execute(
            "SELECT id, content FROM notes WHERE id NOT IN (SELECT note_id FROM note_chunks_map)"
        ).fetchall()
        for row in missing:
            _reembed_note_chunks(db, row["id"], row["content"])
        db.commit()


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------
mcp = FastMCP("memory-server")


# ---------------------------------------------------------------------------
# Zotero sync (memory_zotero_sync) — Web API only
# ---------------------------------------------------------------------------
def _zsync_cfg() -> dict:
    """Liest ~/.qwen/zotero-sync.json (device, api_key_path, collection)."""
    if not ZOTERO_SYNC_CONFIG.exists():
        return {}
    try:
        return json.loads(ZOTERO_SYNC_CONFIG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _zsync_user_id(key: str) -> str | None:
    """userID direkt vom Key ableiten (GET /keys/<key>) — kein hartkodierter Nutzer."""
    try:
        req = urllib.request.Request(
            f"https://api.zotero.org/keys/{key}",
            headers={"Zotero-API-Key": key})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.loads(r.read().decode())
        return str(data.get("userID"))
    except Exception:
        return None


def _zsync_snapshot() -> Path | None:
    """Konsistenter Snapshot (VACUUM INTO) der Memory-DB."""
    with closing(_get_db()) as db:
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        tmp.close()
        db.execute(f"VACUUM INTO '{tmp.name}'")
    return Path(tmp.name)


def _zsync_call(method, url, body=None, key=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if key:
        req.add_header("Zotero-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read().decode()
            return r.status, (json.loads(raw) if raw.strip() else None)
    except urllib.error.HTTPError as e:
        detail = e.read().decode()[:300]
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _zsync_parse_note(note_html):
    """content/tags/source/created_at/host aus einer Backup-Notiz (HTML)."""
    pre = re.search(r'<pre[^>]*>(.*?)</pre>', note_html, re.S)
    if not pre:
        return None
    tag_m = re.search(r"Tags:\s*(.*?)\s*\|\s*Quelle:", note_html)
    src_m = re.search(r"Quelle:\s*(.*?)\s*\|\s*host:", note_html, re.S)
    up_m = re.search(r"mem-updated:\s*([0-9T:.+\-Z]+)", note_html)
    host_m = re.search(r"host:\s*([a-zA-Z0-9_]+)", note_html)
    tags_raw = html.unescape(tag_m.group(1)) if tag_m else ""
    try:
        tags = json.loads(tags_raw)
    except Exception:
        tags = [t.strip() for t in tags_raw.strip("[]").split(",") if t.strip()]
    tags = [str(t).strip().strip('"\'') for t in tags if str(t).strip()]
    return {
        "content": html.unescape(pre.group(1)),
        "tags": tags,
        "source": html.unescape(src_m.group(1)).strip() if src_m else "",
        "created_at": up_m.group(1).strip() if up_m else "",
        "host": host_m.group(1) if host_m else None,
    }


def _zsync_list_all_collections(base, key):
    """Alle Sammlungen — PAGINIERT (die Bibliothek hat >1000 Sammlungen;
    ein einzelnes GET /collections liefert nur die erste Seite)."""
    cols, start = [], 0
    while True:
        status, batch = _zsync_call(
            "GET", base + f"/collections?limit=100&start={start}", key=key)
        cols.extend(batch or [])
        if not batch or len(batch) < 100:
            return cols
        start += 100


def _zsync_get_collection(base, key, name, coll_key=None):
    """Auflösung der Ziel-Sammlung. Mit coll_key (Config: collection_key)
    gilt die feste ID — wird NIEMALS angelegt, 404 ist ein Fehler. Ohne
    coll_key: paginierte Namenssuche, erst danach Anlegen."""
    if coll_key:
        status, c = _zsync_call("GET", base + f"/collections/{coll_key}", key=key)
        if status != 200:
            raise RuntimeError(
                f"collection_key {coll_key} nicht gefunden (HTTP {status}) — "
                "es wird bewusst KEINE neue Sammlung angelegt")
        return c["key"]
    for c in _zsync_list_all_collections(base, key):
        if c["data"]["name"] == name:
            return c["key"]
    status, resp = _zsync_call("POST", base + "/collections",
                               [{"name": name}], key=key)
    return resp["success"]["0"]


def _zsync_note_text(content, tags, source, created, host, note_id):
    sha = hashlib.sha256(content.encode()).hexdigest()[:12]
    first = content.strip().splitlines()[0][:80] if content.strip() else "(leer)"
    meta = (f"Tags: {tags} | Quelle: {source} | host: {host} | "
            f"mem-updated: {created} | mem-hash: {sha}")
    return (
        f"<h1>#{host}-{note_id} – {html.escape(first)}</h1>\n"
        f"<p><em>{html.escape(meta)}</em></p>\n"
        f"<pre style=\"white-space:pre-wrap\">{html.escape(content)}</pre>"
    )


def _zsync_parse_existing(items):
    out = {}
    for it in items:
        note = it["data"].get("note", "")
        m = re.search(r"#([a-zA-Z0-9_]+)-(\d+)", note)
        h = re.search(r"mem-hash:\s*([0-9a-f]+)", note)
        t = re.search(r"<h1>(.*?)</h1>", note)
        title = html.unescape(t.group(1)) if t else ""
        if m:
            out[(m.group(1), int(m.group(2)))] = {
                "key": it["key"], "version": it["version"],
                "hash": h.group(1) if h else "", "title": title}
            continue
        m2 = re.search(r"#(\d+)", note)
        mh = re.search(r"host:\s*([a-zA-Z0-9_]+)", note)
        if m2:
            out[(mh.group(1) if mh else None, int(m2.group(1)))] = {
                "key": it["key"], "version": it["version"],
                "hash": h.group(1) if h else "", "title": title}
    return out


def _zsync_do_sync(db: sqlite3.Connection, learn: bool = False) -> dict:
    """Ein Sync-Lauf. Ohne learn: nur Backup (eigene Notizen → Geräte-Sammlung)
    mit Restore-Schutz. Mit learn=True: zusätzlich fehlende Notizen anderer
    Geräte importieren — AUSSCHLIESSLICH auf explizite Anforderung
    (memory_zotero_sync action:"learn"); nie als Automatik."""
    cfg = _zsync_cfg()
    device = cfg.get("device") or re.sub(r"[^a-zA-Z0-9_]", "", socket.gethostname().split(".")[0]) or "host"
    key_path = Path(cfg.get("api_key_path", str(ZOTERO_SYNC_KEY_DEFAULT)))
    key = key_path.read_text().strip() if key_path.exists() else None
    if not key:
        raise RuntimeError(f"Zotero-API-Key nicht gefunden: {key_path}")
    user_id = cfg.get("user_id") or _zsync_user_id(key)
    if not user_id:
        raise RuntimeError("userID konnte nicht vom Key abgeleitet werden")
    base = f"https://api.zotero.org/users/{user_id}"
    collection = cfg.get("collection") or f"{ZOTERO_SYNC_PREFIX} — {device}"

    # --- Eigene Notizen (read) ---
    snap = _zsync_snapshot()
    try:
        con = sqlite3.connect(str(snap))
        rows = con.execute(
            "SELECT id, content, tags, source, created_at FROM notes ORDER BY id"
        ).fetchall()
        con.close()
    finally:
        snap.unlink(missing_ok=True)

    coll = _zsync_get_collection(base, key, collection,
                                 coll_key=cfg.get("collection_key"))
    status, own_items = _zsync_call(
        "GET", base + f"/collections/{coll}/items?itemType=note&limit=100&format=json",
        key=key)
    items = list(own_items or [])
    while len(items) >= 100:
        status, page = _zsync_call(
            "GET", base + f"/collections/{coll}/items?itemType=note&limit=100"
            f"&start={len(items)}&format=json", key=key)
        if not page:
            break
        items.extend(page)
    existing = _zsync_parse_existing(items)

    # --- Restore-Schutz ---
    local_ids = {r[0] for r in rows}
    own_missing = [
        f"#{h}-{i}" for (h, i) in sorted(existing, key=lambda k: (k[0] or "", k[1]))
        if (h == device or h is None) and i not in local_ids
    ]
    protected = len(own_missing) >= ZOTERO_SYNC_PROTECT_THRESHOLD

    # --- Backup: create / update ---
    created, updated, skipped = 0, 0, 0
    to_create, to_update = [], []
    for nid, content, tags, source, c_at in rows:
        sha = hashlib.sha256(content.encode()).hexdigest()[:12]
        note_html = _zsync_note_text(content, tags, source, c_at, device, nid)
        ex = existing.get((device, nid)) or existing.get((None, nid))
        if ex is None:
            to_create.append({"itemType": "note", "note": note_html,
                              "tags": [{"tag": "ki-gedaechtnis-backup"}],
                              "collections": [coll]})
        elif ex["hash"] != sha:
            if protected:
                skipped += 1
            else:
                to_update.append((ex, note_html))
    for i in range(0, len(to_create), ZOTERO_SYNC_CHUNK):
        batch = to_create[i:i + ZOTERO_SYNC_CHUNK]
        s, resp = _zsync_call("POST", base + "/items", batch, key=key)
        if s not in (200, 201):
            raise RuntimeError(f"Batch {i}: HTTP {s}")
        created += len(resp.get("success", {}))
    for ex, note_html_upd in to_update:
        s, full = _zsync_call("GET", base + "/items/" + ex["key"], key=key)
        if s != 200:
            raise RuntimeError(f"GET {ex['key']}: HTTP {s}")
        data = full["data"]
        data["note"] = note_html_upd
        data["tags"] = [{"tag": "ki-gedaechtnis-backup"}]
        data["collections"] = [coll]
        payload = {"key": ex["key"], "version": ex["version"], **data}
        s, _ = _zsync_call("PUT", base + "/items/" + ex["key"], payload, key=key)
        if s == 204:
            updated += 1
        else:
            raise RuntimeError(f"PUT {ex['key']}: HTTP {s}")

    # --- Fernlernen (NUR auf explizite Anforderung, learn=True) ---
    learned, learned_from = 0, {}
    if not learn:
        ZOTERO_SYNC_LAST_FILE.write_text(str(time.time()))
        return {
            "device": device, "collection": collection,
            "own_notes": len(rows), "created": created, "updated": updated,
            "skipped_protected": skipped, "protected": protected,
            "own_missing_in_zotero": len(own_missing),
            "learned": 0, "learned_from": {},
            "hint": "Backup abgeschlossen. Fernlernen ist eine eigene Aktion: action:\"learn\".",
        }
    local_hashes = {hashlib.sha256(r[1].encode()).hexdigest()[:12] for r in rows}
    cols = _zsync_list_all_collections(base, key)
    learned, learned_from = 0, {}
    for c in (cols or []):
        cname = c["data"]["name"]
        if cname == collection or not (
            cname.startswith(ZOTERO_SYNC_PREFIX)
            or cname.startswith("AI-Memory-Backup")
        ):
            continue
        s2, foreign = _zsync_call(
            "GET", base + f"/collections/{c['key']}/items?itemType=note&limit=100&format=json",
            key=key)
        fitems = list(foreign or [])
        while len(fitems) >= 100:
            s2, page = _zsync_call(
                "GET", base + f"/collections/{c['key']}/items?itemType=note"
                f"&limit=100&start={len(fitems)}&format=json", key=key)
            if not page:
                break
            fitems.extend(page)
        for it in fitems:
            note = it["data"].get("note", "")
            hm = re.search(r"mem-hash:\s*([0-9a-f]+)", note)
            if not hm or hm.group(1) in local_hashes:
                continue
            parsed = _zsync_parse_note(note)
            if not parsed or not parsed["content"].strip():
                continue
            tags = parsed["tags"] + ["quelle:cross-import"]
            source = (parsed["source"] +
                      f" | cross-import aus „{cname}“, {time.strftime('%d.%m.%Y')}").strip(" |")
            created_at = parsed["created_at"] or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            key_val = _keyline_of(parsed["content"])
            cur = db.execute(
                "INSERT INTO notes (content, tags, source, created_at, key) VALUES (?, ?, ?, ?, ?)",
                (parsed["content"], json.dumps(tags, ensure_ascii=False), source, created_at, key_val))
            nid = cur.lastrowid
            _reembed_note_chunks(db, nid, parsed["content"])
            db.execute("INSERT INTO notes_fts(rowid, content, key) VALUES (?, ?, ?)",
                       (nid, parsed["content"], key_val))
            local_hashes.add(hashlib.sha256(parsed["content"].encode()).hexdigest()[:12])
            learned += 1
            learned_from[cname] = learned_from.get(cname, 0) + 1
    db.commit()

    ZOTERO_SYNC_LAST_FILE.write_text(str(time.time()))
    return {
        "device": device, "collection": collection,
        "own_notes": len(rows), "created": created, "updated": updated,
        "skipped_protected": skipped, "protected": protected,
        "own_missing_in_zotero": len(own_missing),
        "learned": learned, "learned_from": learned_from,
    }


def _zsync_status() -> dict:
    cfg = _zsync_cfg()
    key_path = Path(cfg.get("api_key_path", str(ZOTERO_SYNC_KEY_DEFAULT)))
    last = None
    if ZOTERO_SYNC_LAST_FILE.exists():
        try:
            last = float(ZOTERO_SYNC_LAST_FILE.read_text().strip())
        except Exception:
            last = None
    due = last is None or (time.time() - last) > ZOTERO_SYNC_INTERVAL_H * 3600
    return {
        "configured": key_path.exists() and bool(cfg.get("device")),
        "device": cfg.get("device"),
        "collection": cfg.get("collection") or f"{ZOTERO_SYNC_PREFIX} — {cfg.get('device')}" if cfg.get("device") else None,
        "api_key_path": str(key_path),
        "key_present": key_path.exists(),
        "interval_h": ZOTERO_SYNC_INTERVAL_H,
        "last_sync_epoch": last,
        "last_sync_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(last)) if last else None,
        "due": due,
        "running": _sync_state["running"],
        "last_error": _sync_state["last_error"],
    }


def _zsync_lazy_check():
    """6h-Trägheit: bei Memory-Tool-Aufruf prüfen, ob ein Backup fällig ist;
    dann im Hintergrund-Thread anstoßen (nur wenn Key + Device konfiguriert).
    Der Lazy-Lauf ist NUR Backup — Fernlernen passiert nie automatisch."""
    try:
        cfg = _zsync_cfg()
        if not cfg.get("device"):
            return
        if not Path(cfg.get("api_key_path", str(ZOTERO_SYNC_KEY_DEFAULT))).exists():
            return
        st = _zsync_status()
        if st["due"] and not _sync_state["running"]:
            _sync_state["running"] = True
            threading.Thread(target=_zsync_bg_run, daemon=True).start()
    except Exception:
        pass  # Trigger darf niemals ein Memory-Tool ausbremsen


def _zsync_bg_run():
    try:
        with closing(_get_db()) as db:
            result = _zsync_do_sync(db, learn=False)
        _sync_state["last_result"] = result
        _sync_state["last_error"] = None
    except Exception as e:
        _sync_state["last_error"] = str(e)
    finally:
        _sync_state["running"] = False


@mcp.tool()
def memory_zotero_sync(action: str = "status") -> dict:
    """
    Synchronisiert den lokalen Memory-Server mit zotero.org.

    Parameters
    ----------
    action : str
        "status" = read-only Bericht (letzter Sync, offene Differenzen, Schutzmodus);
        "sync"   = Backup der eigenen Notizen in die Geräte-Sammlung
                   „KI-Gedächtnis-Backup — <Device>" (mit Restore-Schutz);
        "learn"  = Backup + gezielten Import fehlender Notizen ANDERER Geräte
                   (quellengetaggt quelle:cross-import) — nur auf ausdrücklichen
                   Wunsch, nie als Automatik.
    """
    if action == "sync":
        if _sync_state["running"]:
            return {"status": "already_running", "hint": "Ein Lauf läuft bereits."}
        _sync_state["running"] = True
        try:
            with closing(_get_db()) as db:
                result = _zsync_do_sync(db, learn=False)
            _sync_state["last_result"] = result
            _sync_state["last_error"] = None
            return {"action": "sync", **result}
        except Exception as e:
            _sync_state["last_error"] = str(e)
            return {"action": "sync", "error": str(e)}
        finally:
            _sync_state["running"] = False
    if action == "learn":
        if _sync_state["running"]:
            return {"status": "already_running", "hint": "Ein Lauf läuft bereits."}
        _sync_state["running"] = True
        try:
            with closing(_get_db()) as db:
                result = _zsync_do_sync(db, learn=True)
            _sync_state["last_result"] = result
            _sync_state["last_error"] = None
            return {"action": "learn", **result}
        except Exception as e:
            _sync_state["last_error"] = str(e)
            return {"action": "learn", "error": str(e)}
        finally:
            _sync_state["running"] = False
    result = _zsync_status()
    result["action"] = action
    if action == "status":
        return result
    return {"error": f"unbekannte action: {action}", "hint": "status | sync | learn"}



@mcp.tool()
def memory_store(content: str, tags: str = "", source: str = "") -> dict:
    """
    Store a memory note.

    Parameters
    ----------
    content : str
        The note content / fact to remember.
    tags : str
        Optional comma-separated tags for categorisation.
    source : str
        Optional origin of this information.
    """
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    key_val = _keyline_of(content)

    with closing(_get_db()) as db:
        cur = db.execute(
            "INSERT INTO notes (content, tags, source, created_at, key) VALUES (?, ?, ?, ?, ?)",
            (content, tags, source, now, key_val),
        )
        note_id = cur.lastrowid

        # Vector chunks
        _reembed_note_chunks(db, note_id, content)

        # FTS
        db.execute(
            "INSERT INTO notes_fts(rowid, content, key) VALUES (?, ?, ?)",
            (note_id, content, key_val),
        )

        db.commit()

    return {"id": note_id, "content": content, "tags": tags, "source": source, "created_at": now}


@mcp.tool()
def memory_update(note_id: int, content: str | None = None, tags: str | None = None, source: str | None = None) -> dict | None:
    """
    Update an existing memory note in place. Re-embeds when content changes.

    Parameters
    ----------
    note_id : int
        The note ID to update.
    content : str, optional
        New note content (re-embeds vector and updates the FTS index).
    tags : str, optional
        New comma-separated tags.
    source : str, optional
        New origin of this information.
    """
    with closing(_get_db()) as db:
        row = db.execute(
            "SELECT id, content, tags, source, created_at, key FROM notes WHERE id = ?",
            (note_id,),
        ).fetchone()
        if row is None:
            return None

        new_content = row["content"] if content is None else content
        new_tags = row["tags"] if tags is None else tags
        new_source = row["source"] if source is None else source
        new_key = _keyline_of(new_content)

        if new_content != row["content"]:
            db.execute(
                "INSERT INTO notes_fts(notes_fts, rowid, content, key) VALUES('delete', ?, ?, ?)",
                (note_id, row["content"], row["key"]),
            )
            db.execute(
                "INSERT INTO notes_fts(rowid, content, key) VALUES (?, ?, ?)",
                (note_id, new_content, new_key),
            )
            _reembed_note_chunks(db, note_id, new_content)

        db.execute(
            "UPDATE notes SET content = ?, tags = ?, source = ?, key = ? WHERE id = ?",
            (new_content, new_tags, new_source, new_key, note_id),
        )
        db.commit()

    return {"id": note_id, "content": new_content, "tags": new_tags, "source": new_source, "created_at": row["created_at"]}


@mcp.tool()
def memory_search(query: str, k: int = 10, budget_tokens: int = 0) -> list[dict]:
    """
    Search memory using hybrid search (vector + full-text) with RRF fusion.

    Parameters
    ----------
    query : str
        The search query.
    k : int
        Number of results to return (default 10).
    budget_tokens : int
        0 = disabled (default). Above 0: stop after the first notes whose
        combined content reaches this token budget (minimum 3, maximum k) —
        replaces result-count tuning with a context-size budget.
    """
    if k < 1:
        k = 10
    k = min(k, 50)

    expand_terms = _synonym_expansion(query)
    search_text = query + " " + " ".join(expand_terms) if expand_terms else query
    query_emb = _embed(search_text, prefix="query")
    embedding_blob = np.array(query_emb, dtype=np.float32).tobytes()

    with closing(_get_db()) as db:
        # --- Vector search (chunked) — KNN over chunks, then aggregate
        # to note level by minimum distance ---
        chunk_rows = db.execute(
            """
            SELECT rowid, distance
            FROM note_chunks
            WHERE embedding MATCH ?
            ORDER BY distance
            LIMIT ?
            """,
            (embedding_blob, k * 4),
        ).fetchall()

        vec_rows: list[tuple[int, float]] = []
        if chunk_rows:
            placeholders = ",".join("?" for _ in chunk_rows)
            map_rows = db.execute(
                f"""
                SELECT chunk_rowid, note_id
                FROM note_chunks_map
                WHERE chunk_rowid IN ({placeholders})
                """,
                [r["rowid"] for r in chunk_rows],
            ).fetchall()
            chunk_to_note = {r["chunk_rowid"]: r["note_id"] for r in map_rows}
            best: dict[int, tuple[float, int]] = {}
            for r in chunk_rows:
                nid = chunk_to_note.get(r["rowid"])
                if nid is not None and (nid not in best or r["distance"] < best[nid][0]):
                    best[nid] = (r["distance"], r["rowid"])
            vec_rows = sorted(best.items(), key=lambda x: x[1][0])[: k * 2]

        # --- FTS5 search ---
        # Quote each whitespace-separated term so FTS5 never interprets
        # query syntax (column:term etc.); OR-join maximises lexical
        # recall — precision comes from the semantic half of the hybrid.
        # Match over the whole table so the key column is searched; the
        # key weight is the fact-key boost (LongMemEval) — the keyline
        # already sits in content too, so key terms count ~(1+w)x, body-only
        # terms keep weight 1.0. Grid 20.9.2026: 3.0 overweights the keyline
        # on vocabulary-gap queries (Runde 3: vector beat hybrid), lower
        # weights recover them; MEMORY_FTS_KEY_WEIGHT tunes it.
        terms = ['"' + t.replace('"', '""') + '"'
                 for t in query.split() + expand_terms]
        fts_rows = []
        if terms:
            fts_rows = db.execute(
                """
                SELECT rowid, bm25(notes_fts, 1.0, ?) AS rank
                FROM notes_fts
                WHERE notes_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (FTS_KEY_WEIGHT, " OR ".join(terms), k * 2),
            ).fetchall()

    # --- RRF fusion ---
    K = RRF_K  # RRF constant (MEMORY_RRF_K, default 60)
    scores: dict[int, float] = {}

    for rank, row in enumerate(vec_rows):
        note_id = row[0]
        scores[note_id] = scores.get(note_id, 0.0) + 1.0 / (K + rank + 1)

    for rank, row in enumerate(fts_rows):
        note_id = row[0]
        scores[note_id] = scores.get(note_id, 0.0) + FTS_ARM_WEIGHT / (K + rank + 1)

    if not scores:
        return []

    # Sort by RRF score descending
    ranked = sorted(scores.items(), key=lambda x: -x[1])

    # Fetch full note data (plus the best chunk index per note for snippets)
    with closing(_get_db()) as db:
        placeholders = ",".join("?" for _ in ranked)
        notes = db.execute(
            f"SELECT id, content, tags, source, created_at FROM notes WHERE id IN ({placeholders})",
            [rid for rid, _ in ranked],
        ).fetchall()
        best_chunk_map: dict[int, int] = {}
        best_crids = [v[1] for v in best.values()]
        if best_crids:
            ph = ",".join("?" for _ in best_crids)
            for r in db.execute(
                f"SELECT chunk_rowid, note_id, chunk_idx FROM note_chunks_map WHERE chunk_rowid IN ({ph})",
                best_crids,
            ):
                best_chunk_map[r["note_id"]] = r["chunk_idx"]

    note_map = {n["id"]: dict(n) for n in notes}

    result = []
    for note_id, rrf_score in ranked:
        note = note_map.get(note_id)
        if note:
            note["score"] = round(rrf_score, 4)
            chunk_idx = best_chunk_map.get(note_id)
            if chunk_idx is not None:
                chunks = _chunk_text(note["content"])
                if chunk_idx < len(chunks):
                    note["snippet"] = _snippet_text(chunks[chunk_idx])
            result.append(note)

    result = result[:k]

    # Token-budget stopping (evidence: few notes, ~2k-token budget)
    if budget_tokens > 0:
        picked: list[dict] = []
        total = 0
        for note in result:
            picked.append(note)
            total += _count_tokens(note["content"])
            if len(picked) >= 3 and total >= budget_tokens:
                break
        if picked:
            picked[0]["budget_tokens_used"] = total
        result = picked

    return result


@mcp.tool()
def memory_get(note_id: int) -> dict | None:
    """
    Retrieve a single memory note by ID.

    Parameters
    ----------
    note_id : int
        The note ID.
    """
    with closing(_get_db()) as db:
        row = db.execute("SELECT id, content, tags, source, created_at FROM notes WHERE id = ?", (note_id,)).fetchone()
    return dict(row) if row else None


@mcp.tool()
def memory_recent(limit: int = 20) -> list[dict]:
    """
    Return the most recently stored memory notes.

    Parameters
    ----------
    limit : int
        Maximum number of notes to return (default 20).
    """
    if limit < 1:
        limit = 20
    with closing(_get_db()) as db:
        rows = db.execute(
            "SELECT id, content, tags, source, created_at FROM notes ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@mcp.tool()
def memory_delete(note_id: int) -> bool:
    """
    Delete a memory note and its associated vector/FTS entries.

    Parameters
    ----------
    note_id : int
        The note ID to delete.
    """
    with closing(_get_db()) as db:
        row = db.execute("SELECT content, key FROM notes WHERE id = ?", (note_id,)).fetchone()
        if row is None:
            return False
        # External-content FTS: the index must be updated with the old
        # content BEFORE the content row disappears
        db.execute(
            "INSERT INTO notes_fts(notes_fts, rowid, content, key) VALUES('delete', ?, ?, ?)",
            (note_id, row["content"], row["key"]),
        )
        db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        db.execute("DELETE FROM notes_vec WHERE rowid = ?", (note_id,))
        db.execute(
            "DELETE FROM note_chunks WHERE rowid IN "
            "(SELECT chunk_rowid FROM note_chunks_map WHERE note_id = ?)",
            (note_id,),
        )
        db.execute("DELETE FROM note_chunks_map WHERE note_id = ?", (note_id,))
        db.commit()
    return True


@mcp.tool()
def memory_status() -> dict:
    """Return database statistics — total notes, vector count, FTS count."""
    with closing(_get_db()) as db:
        total = db.execute("SELECT COUNT(*) AS c FROM notes").fetchone()["c"]
        vec_count = db.execute("SELECT COUNT(*) AS c FROM notes_vec").fetchone()["c"]
        chunk_count = db.execute("SELECT COUNT(*) AS c FROM note_chunks").fetchone()["c"]
        fts_count = db.execute("SELECT COUNT(*) AS c FROM notes_fts").fetchone()["c"]
        return {
            "total_notes": total,
            "vector_entries": vec_count,
            "chunk_entries": chunk_count,
            "fts_entries": fts_count,
            "db_path": str(DB_PATH),
        }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def main():
    _init_db()
    _ensure_embedder()
    mcp.run()


if __name__ == "__main__":
    main()