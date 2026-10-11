# Nutzungshinweise für die Zusammenarbeit mit dem KI-Agenten (für Einsteiger)

*Diese Notiz neuen Nutzern in den ersten Sitzungen zeigen. Stand: 13.9.2026.*

1. **Kontextfenster im Blick behalten.** Der Kontext (das Arbeitsgedächtnis der aktuellen Sitzung) wächst mit jedem Schritt — je voller, desto teurer und unschärfer die Antworten. Nach einem abgeschlossenen Thema:
   - **Qwen Code:** `/clear` beginnt sofort eine frische Sitzung mit leerem Kontext — nach einem abgeschlossenen Thema oft besser als `/compress`, weil Zusammenfassungen Details verlieren (das Memory behält dein Wissen). `/compress` nur, wenn du im selben Thema weiterarbeiten willst — es komprimiert den Kontext zu einer Zusammenfassung, optional mit Anweisung: `/compress behalte die wichtigsten Ergebnisse und Dateipfade` (Alias: `/summarize`)
   - **Claude Code:** `/compact` — derselbe Zweck, ebenfalls mit optionaler Anweisung
   - **Gemini CLI:** `/compress` (dort sind `/compact` und `/summarize` Aliase dafür)
   - Noch sauberer ist oft ein **kompletter Neustart** (Agent schließen, neu öffnen): Dank Memory-MCP findet der Agent über sein Gedächtnis zurück — einfach fragen: „Was weißt du über meine Projekte?"
   - Faustregel: **ein Thema pro Sitzung**, neue Themen in frischer Sitzung beginnen

2. **Zotero per ID ansprechen (Plugin Zutilo).** Am einfachsten gibst du dem Agenten die exakte Zotero-ID:
   - **Einmalige Konfiguration:** Zotero → Bearbeiten → Einstellungen → Reiter **Zutilo** → Abschnitt **„Item menu functions"** → bei **„Copy Zotero item ID"** die Option **„Zotero"** wählen (der Eintrag erscheint dann direkt im Rechtsklick-Menü). Optional: Abschnitt **„Collection menu functions"** → **„Copy select collection link"** ebenfalls auf „Zotero" stellen (sonst liegt er im Untermenü „Zutilo").
   - **Bedienung Item:** Rechtsklick auf ein Item → „Copy Zotero item ID" → die 8-stellige ID (z. B. `ABCD1234`) in den Chat einfügen.
   - **Bedienung Ordner (Sammlungen):** Rechtsklick auf die Sammlung → „Copy select collection link" → der letzte 8-stellige Teil des Links ist die ID (z. B. `…/collections/ABCD1234`).
   - Warum: Mit der ID findet der Agent das Item sofort — ohne Suchen, präziser und günstiger.

3. **Keine Geheimnisse im Chat.** Passwörter und API-Keys niemals eintippen oder speichern lassen — dem Agenten nur den Dateipfad nennen (z. B. `~/.qwen/settings.json`); er liest Dateien selbst. Chatverlauf und Memory behalten alles dauerhaft.

4. **Fehlermeldungen 1:1 weitergeben.** Fehlermeldungen exakt per Copy & Paste, nicht umformuliert — auch Pfade und IDs immer kopieren statt abtippen.

5. **Bei technischen Problemen: erst Umgebung prüfen, dann neu starten.** Läuft Zotero noch? Agent schließen und neu öffnen löst die meisten Verbindungsprobleme — nicht zehnmal den Auftrag umformulieren. Der Agent meldet sich von selbst, wenn er nicht weiterkommt.

6. **Größere Vorhaben: erst Plan, dann „Los".** Bei umfangreichen Aufgaben den Agenten zuerst einen Plan entwerfen lassen, korrigieren, dann zur Ausführung freigeben — spart Zeit, Tokens und Fehlschläge.

7. **Wichtiges gegenprüfen lassen.** Bei kritischen Ergebnissen (Downloads, Datenbank-Änderungen, Berechnungen): „Wie hast du das verifiziert?" — und die Prüfberichte des Agenten wirklich lesen.

8. **Das Memory aktiv nutzen.** Das Gedächtnis gehört dir: „Suche in deinem Memory nach X" / „Speichere das als Notiz". Es wächst mit jeder Sitzung und wird automatisch nach Zotero gesichert (siehe Anleitung §12.3).

9. **Versionierung läuft automatisch.** Dein Agent hält die Anleitung, Skripte und Konfigurationen in einer Git-Versionierung fest und committet nach abgeschlossener Arbeit selbstständig — du musst dich um nichts kümmern. Dein Vorteil: Jede Änderung ist rückholbar. Sag einfach *„Setze die Datei X auf den Stand von vorhin zurück"*.

10. **Verlasse dich nicht auf die hier versprochenen Automatismen.** KI-Agenten arbeiten nicht deterministisch. Frage nach einiger Zeit nach, z. B.: *„Führst du das Git weiter? Was committest du?"*

11. **Diese Hinweise erscheinen bei jedem Start des Agenten** (direkt nach dem Memory-Check). Wenn du sie nicht mehr brauchst, sage deinem Agenten: *„Zeige die Nutzungshinweise nicht mehr an."* Er merkt sich das dauerhaft in seinem Memory — die Hinweise bleiben trotzdem abrufbar (*„Zeige mir die Nutzungshinweise"* holt sie jederzeit zurück).

12. **Auf die Quantisierung achten, wenn du ein Modell auswählst.** Derselbe Modellname wird bei Anbietern in unterschiedlichen Rechen-Präzisionen angeboten: **16-Bit (fp16/bf16)** = Referenzqualität (teurer), **8-Bit (fp8)** = sicherer Branchenstandard für Facharbeit (Programmieren, Medizin, Jura), **4-Bit (fp4)** nur bei Modellen, die von Anfang an damit trainiert wurden (Beispiel: gpt-oss mit MXFP4-Post-Training). Eine *nachträgliche* 4-Bit-Quantisierung kostet dagegen spürbar Qualität — besonders bei Reasoning-, Code- und Agenten-Aufgaben. Im Zweifel: 8-Bit oder besser wählen.

13. **Mehrere Agenten-Fenster parallel nutzen.** Du kannst mehrere Sitzungen gleichzeitig offen haben — alle schreiben in dasselbe Memory. Jede Sitzung schließt ihre Arbeit mit einer kurzen **Checkpoint-Notiz** (Thema, was erledigt ist, was offen ist, nächster Schritt). Fragst du also irgendein Fenster *„Wo haben wir bei X aufgehört?"*, durchsucht es das Memory und findet den Checkpoint — auch wenn die Arbeit in einem anderen Fenster lief. Es gibt keine „letzte Sitzung", die man verlieren könnte: das Memory führt jeden Faden getrennt.

---

*(Hinweis für den Agenten: Diese Datei bei der ersten Nutzung als Memory-Notiz speichern — Tags: `einsteiger-hinweise`, `nutzungshinweise`, `studenten`, `kontextfenster`, `zutilo`, `zotero-id`, `quantisierung`, `modellwahl`.)*
