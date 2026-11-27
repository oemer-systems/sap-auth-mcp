# Änderungen

Alle Versionen des SAP-Berechtigungsassistenten auf einen Blick. Details stehen in den [Releases](https://github.com/oemer-systems/sap-auth-mcp/releases).

## v0.7.0 (28.11.2026): Ein Befehl, und es läuft

Der Assistent läuft als fertiges Docker-Image. Das Sprachmodell bleibt auf dem eigenen Rechner.

### Neu
- Docker-Image mit eigenem Benutzer ohne Administratorrechte und Zeitzone Europe/Berlin
- `docker-compose.yml`: Chat, Wächter und Tests mit einem Befehl, Daten und Protokoll als Volume auf dem Rechner
- GitHub-Prüfung baut das Image, führt alle Tests im Container aus und prüft Benutzer und Zeitzone
- Prüfkatalog im Container 18 von 18, Betrieb ohne Internet bestätigt

### Verbessert
- Lokal ist die Voreinstellung, verständliche Meldung ohne API-Schlüssel
- Jede Protokollzeile trägt ihre Zeitzone
- `pywin32` nur unter Windows, `requirements.txt` ohne BOM
- 86 automatische Tests (vorher 83)

## v0.6.0 (26.11.2026): Least Privilege

Rollen, die niemand braucht, aus echten Nutzungsdaten. Bereinigung als Antrag statt als Änderung.

### Neu
- `ungenutzte_rechte`: Rollen, die laut ST03N in 13 Monaten nicht benötigt wurden
- Lösungsvorschläge nehmen nicht benötigte Rollen zuerst
- `aenderung_anfragen`: Änderungswünsche werden geprüft, nie ausgeführt, und als Antrag vorbereitet
- `szenarien.py`: Prüfkatalog mit 18 Fragen für die Werkzeugwahl der KI, 18 von 18
- Import der Nutzungsstatistik aus ST03N

### Verbessert
- Feste Grenzen im Code: Nutzung mindestens 13 Monate, Inaktivität höchstens ab 90 Tagen
- Warnhinweise bei Antworten ohne Werkzeugaufruf und beim Entsperren ohne Prüfung
- `temperature 0`, zweiter Versuch mit Nachdenken bei Ratlosigkeit
- Risikoübersicht ergänzt bestehende Befunde, statt sie doppelt zu melden
- 83 automatische Tests (vorher 53)

## v0.5.0 (24.11.2026): SoD-Linter für KI-Agenten

CAP-Services, die per `@mcp` für KI-Agenten geöffnet werden, vor dem Deployment prüfen.

### Neu
- SoD-Linter für CAP-Projekte mit den Regeln R1 bis R4
- Ein Regelwerk für Menschen und Agenten, neue Regel SOD08
- KI-Zuordnung: Ein lokales Modell schlägt fehlende SoD-Funktionen vor und gibt nie Entwarnung
- GitHub Action: Tests und Linter bei jedem Push

### Verbessert
- 53 automatische Tests (vorher 28)

## v0.4.0 (21.11.2026): Vorausdenken

Der Assistent findet Risiken selbst und liefert die Lösung mit.

### Neu
- Wächter: meldet täglich nur neue und erledigte Befunde
- Lösungsvorschläge und Simulation eines Rollenentzugs
- HR-Abgleich: ausgetretene Mitarbeiter, Abteilungswechsel ohne Bereinigung
- Rollenkonzept: Rollen, die nicht zur Abteilung passen
- Ablaufende Ausnahmen 30 Tage vorher

### Verbessert
- Werkzeuge lehnen ungültige Eingaben ab und nennen die Alternative
- KI-Zusammenfassung des Wächters aus fest formulierten Fakten
- 28 automatische Tests (vorher 15)

## v0.3.0 (19.11.2026): Pilotfähigkeit

### Neu
- Vollständig lokaler Betrieb mit Ollama, ohne Cloud und ohne API
- SoD-Regelwerk mit Funktionen, erkennt auch S/4HANA-Transaktionen wie BP
- Genehmigte Ausnahmen mit Kontrolle und Ablaufdatum
- Import von SAP-Standardexporten mit Gültigkeitsprüfung der Rollenzuordnungen
- Prüfbericht als Excel, ohne KI erstellt
- 15 automatische Tests

## v0.2.0 (18.11.2026): Prüfungen und Risikoübersicht

### Neu
- Risikoübersicht über alle Prüfungen, nach Priorität sortiert
- Inaktive Benutzer, gesperrte Benutzer mit Rollen, kritische Berechtigungen
- Feste Prioritätsregeln: hoch, mittel, prüfen

## v0.1.0 (17.11.2026): Erster Prototyp

### Neu
- MCP-Server mit lesendem, protokolliertem Zugriff auf Berechtigungsdaten
- Werkzeuge `benutzer_rollen` und `sod_pruefen` mit fester Regellogik
- Chat im Browser und im Terminal, nutzbar mit Claude Desktop
