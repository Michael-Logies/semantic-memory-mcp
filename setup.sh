#!/bin/bash
# ============================================================================
# Memory-MCP Server - Installationsskript
# ============================================================================
# Fuehrt alle Schritte zur Neuinstallation des Memory-MCP Servers aus:
#   1. Python-Virtualenv anlegen
#   2. Abhaengigkeiten installieren
#   3. Einbettungs-Modell herunterladen
#   4. Server-Script kopieren
#   5. Datenbank initialisieren (Testlauf)
# ============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ---- Konfiguration (bei Bedarf anpassen) ----
INSTALL_DIR="${INSTALL_DIR:-$HOME/.qwen}"
VENV_DIR="${VENV_DIR:-$HOME/.venv-memory}"
MODEL_DIR="${MODEL_DIR:-$INSTALL_DIR/models/multilingual-e5-small}"
SERVER_SCRIPT="${SERVER_SCRIPT:-$INSTALL_DIR/memory_server.py}"
MODEL_REPO="Xenova/multilingual-e5-small"

# ---- Farben fuer Ausgaben ----
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

info()  { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; }

# ---- Pruefungen ----
command -v python3 >/dev/null 2>&1 || { error "python3 wird benoetigt."; exit 1; }
python3 -c "import venv" 2>/dev/null || { error "Python-Modul 'venv' fehlt. Installiere es mit: sudo apt install python3-venv"; exit 1; }

# ---- 1. Virtualenv ----
if [ -d "$VENV_DIR" ]; then
    warn "Virtualenv existiert bereits: $VENV_DIR"
    warn "Ueberspringe Schritt 1. Loesche das Verzeichnis fuer eine Neuinstallation."
else
    info "Lege Virtualenv an: $VENV_DIR"
    python3 -m venv "$VENV_DIR"
fi

# ---- 2. Abhaengigkeiten installieren ----
info "Installiere Python-Abhaengigkeiten ..."
"$VENV_DIR/bin/pip" install --upgrade pip
"$VENV_DIR/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

# ---- 3. Modell herunterladen ----
info "Lade Einbettungs-Modell herunter: $MODEL_REPO"
mkdir -p "$MODEL_DIR"

# Benoetigte Dateien (Repo-Pfad -> Zielname im Modellverzeichnis).
# Der Download laeuft ueber die huggingface_hub-Python-API, weil das
# huggingface-cli ab hub 1.x deaktiviert ist und die ONNX-Datei im Repo
# unter onnx/ liegt, der Server sie aber flach neben tokenizer.json erwartet.
"$VENV_DIR/bin/python3" - "$MODEL_DIR" <<'PYEOF'
import os, shutil, sys
from huggingface_hub import hf_hub_download

model_dir = sys.argv[1]
mapping = {
    "onnx/model_quantized.onnx": "model_quantized.onnx",
    "tokenizer.json": "tokenizer.json",
    "config.json": "config.json",
    "tokenizer_config.json": "tokenizer_config.json",
    "special_tokens_map.json": "special_tokens_map.json",
}
for repo_path, dest_name in mapping.items():
    dest = os.path.join(model_dir, dest_name)
    if os.path.exists(dest):
        print(f"  -> {dest_name} (bereits vorhanden)")
        continue
    print(f"  -> {dest_name}")
    src = hf_hub_download("Xenova/multilingual-e5-small", repo_path)
    shutil.copy(src, dest)
print("Modell vollstaendig.")
PYEOF

# ---- 4. Server-Script kopieren ----
info "Kopiere Server-Script nach: $SERVER_SCRIPT"
cp "$SCRIPT_DIR/memory_server.py" "$SERVER_SCRIPT"
chmod +x "$SERVER_SCRIPT"

# ---- 4b. Synonym-Woerterbuch (Query-Erweiterung) ----
# Der Server liest es aus ~/.qwen-memory/synonyms.json (Default-Pfad).
# Abschalten: Umgebungsvariable MEMORY_SYNONYMS=0.
if [ -f "$SCRIPT_DIR/synonyms.json" ]; then
    info "Kopiere Synonym-Woerterbuch nach: $HOME/.qwen-memory/synonyms.json"
    mkdir -p "$HOME/.qwen-memory"
    cp -f "$SCRIPT_DIR/synonyms.json" "$HOME/.qwen-memory/synonyms.json"
fi

# ---- 5. Testlauf (Datenbank initialisieren) ----
info "Fuehre Testlauf durch (initialisiert die Datenbank) ..."
# Startet kurz den Server, der _init_db() und _ensure_embedder() aufruft,
# und beendet ihn sofort wieder.
# Dazu nutzen wir ein Timeout, da der Server sonst auf stdio-Verbindung wartet.
timeout 5 "$VENV_DIR/bin/python3" -c "
import sys
sys.path.insert(0, '$INSTALL_DIR')
from memory_server import _init_db, _ensure_embedder
_init_db()
_ensure_embedder()
print('Datenbank initialisiert, Modell geladen.')
" 2>&1 || true

# ---- 6. Automatische Backups einrichten (stuendlich, 8 Tage Retention) ----
BACKUP_SCRIPT="${BACKUP_SCRIPT:-$INSTALL_DIR/backup_memory.sh}"

if ! command -v sqlite3 >/dev/null 2>&1; then
    warn "sqlite3 fehlt — versuche Installation (noetig fuer die automatischen Backups) ..."
    sudo -n apt install -y sqlite3 2>/dev/null || warn "sqlite3 konnte nicht installiert werden. Backups werden uebersprungen (sudo ohne Passwort nicht moeglich). Installiere sqlite3 manuell und fuehre dieses Skript erneut aus."
fi

if command -v sqlite3 >/dev/null 2>&1; then
    info "Richte automatische stuendliche Backups ein ..."
    cat > "$BACKUP_SCRIPT" <<'BACKUPEOF'
#!/bin/bash
# Automatischer Backup der Memory-MCP-Datenbank (stuendlich, 8 Tage Retention)
# Wird vom Cron-Job aufgerufen. Legt nur bei Aenderung einen neuen Snapshot an.
# VACUUM INTO erzeugt einen konsistenten, kompakten Snapshot ohne WAL-Flag.
set -euo pipefail
DB="${MEMORY_DB_PATH:-$HOME/.qwen/memory.db}"
DIR="$HOME/.qwen/backups"
[ -f "$DB" ] || exit 0
mkdir -p "$DIR"
TMP=$(mktemp /tmp/memory-backup.XXXXXX)
sqlite3 "$DB" "VACUUM INTO '$TMP'"
NEW=$(sha256sum "$TMP" | cut -d' ' -f1)
LATEST=$(ls -t "$DIR"/memory-*.db 2>/dev/null | head -1 || true)
if [ -n "$LATEST" ] && [ "$NEW" = "$(sha256sum "$LATEST" | cut -d' ' -f1)" ]; then
    rm -f "$TMP"
    exit 0
fi
mv "$TMP" "$DIR/memory-$(date +%Y%m%d-%H%M%S).db"
rm -f "$DIR"/memory-*.db-wal "$DIR"/memory-*.db-shm
find "$DIR" -name 'memory-*.db' -mtime +8 -delete
BACKUPEOF
    chmod +x "$BACKUP_SCRIPT"

    # Ersten Snapshot sofort anlegen (falls DB schon existiert)
    "$BACKUP_SCRIPT" || true

    # Cron-Job anlegen (idempotent)
    if ! crontab -l 2>/dev/null | grep -q "backup_memory.sh"; then
        (crontab -l 2>/dev/null; echo "13 * * * * $BACKUP_SCRIPT") | crontab -
        info "  -> Cron-Job angelegt (stuendlich, Minute 13)"
    else
        info "  -> Cron-Job existiert bereits"
    fi
    info "  -> Backups: $HOME/.qwen/backups (8 Tage Aufbewahrung, nur bei Aenderung)"
else
    warn "Ueberspringe automatische Backups (sqlite3 nicht verfuegbar)."
fi

# ---- 7. Pruefroutine ----
if [ -f "$SCRIPT_DIR/verify.sh" ]; then
    info "Fuehre Pruefroutine aus (Bericht: $INSTALL_DIR/memory-install-report.md) ..."
    INSTALL_DIR="$INSTALL_DIR" VENV_DIR="$VENV_DIR" MODEL_DIR="$MODEL_DIR" \
        MEMORY_DB_PATH="${MEMORY_DB_PATH:-$INSTALL_DIR/memory.db}" bash "$SCRIPT_DIR/verify.sh" || true
fi

# ---- 8. Nutzungshinweise kopieren (Einsteiger-Hinweise fuer die Agenten-Session) ----
for f in nutzungshinweise.md usage-notes.md; do
    if [ -f "$SCRIPT_DIR/$f" ]; then
        cp -f "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
        info "Kopiert: $f -> $INSTALL_DIR/"
    fi
done

echo ""
info "============================================"
info "Installation abgeschlossen!"
info "============================================"
echo ""
info "Naechste Schritte:"
info "  1. MCP-Konfiguration in settings.json eintragen (siehe mcp-config.json)"
info "  2. Qwen Code neu starten"
info ""
info "Installationsverzeichnis: $INSTALL_DIR"
info "Virtualenv:              $VENV_DIR"
info "Modell:                  $MODEL_DIR"
info "Datenbank:               $INSTALL_DIR/memory.db"