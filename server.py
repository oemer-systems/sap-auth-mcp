import csv
import json
from datetime import datetime
from pathlib import Path

from mcp.server.mcpserver import MCPServer

BASIS = Path(__file__).parent
DATA = BASIS / "data"
LOG = BASIS / "logs" / "zugriffe.jsonl"
mcp = MCPServer("sap-auth")


def lade(datei):
    """Liest eine CSV-Datei aus dem Ordner data als Liste von Zeilen."""
    with open(DATA / datei, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def protokolliere(werkzeug, eingabe, ergebnis):
    """Schreibt jeden Werkzeugaufruf als eine Zeile in logs/zugriffe.jsonl."""
    LOG.parent.mkdir(exist_ok=True)
    eintrag = {
        "zeit": datetime.now().isoformat(timespec="seconds"),
        "werkzeug": werkzeug,
        "eingabe": eingabe,
        "erfolgreich": "fehler" not in ergebnis,
    }
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(eintrag, ensure_ascii=False) + "\n")


def finde_benutzer(suchbegriff):
    """Findet einen Benutzer über Kennung (MMUSTER) oder vollen Namen (Max Mustermann)."""
    s = suchbegriff.strip().lower()
    for b in lade("benutzer.csv"):
        if s in (b["benutzer"].lower(), b["name"].lower()):
            return b
    return None


def transaktionen_von(kennung):
    """Liefert die Rollen eines Benutzers und alle Transaktionen daraus."""
    rollen = [z["rolle"] for z in lade("benutzer_rollen.csv") if z["benutzer"] == kennung]
    tcodes = sorted({r["tcode"] for r in lade("rollen.csv") if r["rolle"] in rollen})
    return rollen, tcodes


@mcp.tool()
def benutzer_rollen(benutzer: str) -> dict:
    """Zeigt Stammdaten, Rollen und Transaktionen eines SAP-Benutzers.
    Akzeptiert die Benutzerkennung (z. B. MMUSTER) oder den vollen Namen. Nur lesend."""
    stamm = finde_benutzer(benutzer)
    if stamm is None:
        ergebnis = {"fehler": f"Benutzer {benutzer} nicht gefunden"}
    else:
        rollen, tcodes = transaktionen_von(stamm["benutzer"])
        ergebnis = {"benutzer": stamm, "rollen": rollen, "transaktionen": tcodes}
    protokolliere("benutzer_rollen", {"benutzer": benutzer}, ergebnis)
    return ergebnis


@mcp.tool()
def sod_pruefen(benutzer: str) -> dict:
    """Prüft einen SAP-Benutzer gegen das SoD-Regelwerk (Funktionstrennung).
    Das Ergebnis stammt aus fester Regellogik, nicht aus einer Einschätzung der KI.
    Akzeptiert Benutzerkennung oder vollen Namen. Nur lesend."""
    stamm = finde_benutzer(benutzer)
    if stamm is None:
        ergebnis = {"fehler": f"Benutzer {benutzer} nicht gefunden"}
    else:
        _, tcodes = transaktionen_von(stamm["benutzer"])
        konflikte = [
            regel for regel in lade("sod_regeln.csv")
            if regel["tcode_a"] in tcodes and regel["tcode_b"] in tcodes
        ]
        ergebnis = {
            "benutzer": stamm["benutzer"],
            "name": stamm["name"],
            "anzahl_konflikte": len(konflikte),
            "konflikte": konflikte,
        }
    protokolliere("sod_pruefen", {"benutzer": benutzer}, ergebnis)
    return ergebnis


if __name__ == "__main__":
    mcp.run(transport="stdio")