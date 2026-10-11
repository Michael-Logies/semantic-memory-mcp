#!/bin/bash
# ============================================================================
# Memory-MCP Server - Pruefroutine
# ============================================================================
# Prueft die Installation und schreibt einen Bericht nach:
#   ~/.qwen/memory-install-report.md
# Laeuft auch einzeln, ohne die Installation zu veraendern.
# Aufruf:  bash verify.sh   (oder von setup.sh am Ende)
# ============================================================================
set -uo pipefail

INSTALL_DIR="${INSTALL_DIR:-$HOME/.qwen}"
VENV_DIR="${VENV_DIR:-$HOME/.venv-memory}"
MODEL_DIR="${MODEL_DIR:-$INSTALL_DIR/models/multilingual-e5-small}"
DB_PATH="${MEMORY_DB_PATH:-$INSTALL_DIR/memory.db}"
REPORT="${INSTALL_DIR}/memory-install-report.md"

PASS=0; FAIL=0
declare -a ROWS

check() {
    # check <Titel> <Status: ok|warn|fail> <Details...>
    local title="$1" status="$2"; shift 2
    local icon
    case "$status" in
        ok)   icon="✅"; PASS=$((PASS+1));;
        warn) icon="⚠️"; FAIL=$((FAIL+1));;
        *)    icon="❌"; FAIL=$((FAIL+1));;
    esac
    ROWS+=("| $icon | $title | $* |")
}

# ---- 1. Virtualenv + Abhaengigkeiten ----
if [ -d "$VENV_DIR" ]; then
    check "Virtualenv" ok "$VENV_DIR"
    PY="$VENV_DIR/bin/python3"
    for mod in fastmcp sqlite_vec onnxruntime tokenizers; do
        if "$PY" -c "import $mod" 2>/dev/null; then
            check "Python-Paket: $mod" ok
        else
            check "Python-Paket: $mod" fail "Import fehlgeschlagen — \`$VENV_DIR/bin/pip install -r requirements.txt\` ausfuehren"
        fi
    done
else
    check "Virtualenv" fail "nicht gefunden: $VENV_DIR"
fi

# ---- 2. Modell-Dateien ----
for f in model_quantized.onnx tokenizer.json config.json; do
    if [ -f "$MODEL_DIR/$f" ]; then
        check "Modell-Datei: $f" ok
    else
        check "Modell-Datei: $f" fail "fehlt in $MODEL_DIR — setup.sh erneut ausfuehren"
    fi
done

# ---- 3. Datenbank ----
if [ -f "$DB_PATH" ]; then
    check "Datenbank" ok "$DB_PATH ($(du -h "$DB_PATH" | cut -f1))"
    NOTES=$("$VENV_DIR/bin/python3" - "$DB_PATH" <<'PYEOF' 2>/dev/null || echo "?"
import sqlite3, sys
con = sqlite3.connect("file:" + sys.argv[1] + "?mode=ro", uri=True)
try:
    n, = con.execute("SELECT COUNT(*) FROM notes").fetchone()
    print(n)
except Exception as e:
    print("?")
PYEOF
)
    if [ "$NOTES" != "?" ] && [ -n "$NOTES" ]; then
        check "Notizen in der DB" ok "$NOTES Eintraege"
    else
        check "Notizen in der DB" warn "Tabelle nicht lesbar — Server noch nie gestartet?"
    fi
else
    check "Datenbank" warn "nicht gefunden: $DB_PATH (wird beim ersten Serverstart angelegt)"
fi

# ---- 4. Backups (Cron-Job) ----
if crontab -l 2>/dev/null | grep -q "backup_memory.sh"; then
    check "Backup-Cron-Job" ok "stuendlich (13 * * * *)"
    BDIR="$INSTALL_DIR/backups"
    LATEST=$(ls -t "$BDIR"/memory-*.db 2>/dev/null | head -1 || true)
    if [ -n "$LATEST" ]; then
        AGE=$(( ($(date +%s) - $(stat -c %Y "$LATEST")) / 3600 ))
        if [ "$AGE" -le 2 ]; then
            check "Letzter Backup-Snapshot" ok "$LATEST (${AGE}h alt)"
        else
            check "Letzter Backup-Snapshot" warn "$LATEST ist ${AGE}h alt — Cron laeuft?"
        fi
    else
        check "Backup-Snapshots" warn "kein Snapshot in $BDIR — backup_memory.sh manuell ausfuehren"
    fi
else
    check "Backup-Cron-Job" warn "nicht in der Crontab — setup.sh erneut ausfuehren (Schritt 6)"
fi

# ---- 5. MCP-Konfiguration (Hinweis) ----
SETTINGS="$HOME/.qwen/settings.json"
if [ -f "$SETTINGS" ]; then
    if grep -q "memory_server" "$SETTINGS" 2>/dev/null; then
        check "MCP-Konfiguration" ok "memory_server in $SETTINGS eingetragen"
    else
        check "MCP-Konfiguration" warn "memory_server nicht in $SETTINGS gefunden — siehe mcp-config.json im Paket"
    fi
else
    check "MCP-Konfiguration" warn "$SETTINGS nicht gefunden — Konfiguration siehe mcp-config.json"
fi

# ---- 6. Speicherplatz ----
FREE_GB=$(df --output=avail -BG "$INSTALL_DIR" 2>/dev/null | tail -1 | tr -dc '0-9')
if [ -n "$FREE_GB" ] && [ "$FREE_GB" -lt 2 ]; then
    check "Freier Speicherplatz" warn "${FREE_GB} GB — fuer Modell (130 MB) + Backups (bis ~800 MB) knapp"
else
    check "Freier Speicherplatz" ok "${FREE_GB:-?} GB frei"
fi

# ---- Bericht schreiben ----
{
    echo "# Memory-MCP-Installationspruefung"
    echo ""
    echo "**Datum:** $(date '+%Y-%m-%d %H:%M')  "
    echo "**Ergebnis:** $PASS Pruefungen OK, $FAIL Hinweise/Fehler"
    echo ""
    echo "| Status | Pruefung | Details |"
    echo "|---|---|---|"
    for l in "${ROWS[@]}"; do echo "$l"; done
    echo ""
    echo "*Erneut pruefen: \`bash $INSTALL_DIR/verify.sh\` (Paket) bzw. die Pruefroutine des Installationsverzeichnisses.*"
} > "$REPORT"

# ---- Konsolenausgabe (fuer die KI / das Terminal) ----
echo ""
echo "=========== Pruefbericht ==========="
cat "$REPORT"
echo "===================================="
if [ "$FAIL" -gt 0 ]; then
    echo "Hinweise/Fehler: $FAIL (siehe Bericht: $REPORT)"
    exit 1
fi
echo "Alle Pruefungen bestanden. Bericht: $REPORT"
