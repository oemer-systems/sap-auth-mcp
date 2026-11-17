import csv
from pathlib import Path

from mcp.server.mcpserver import MCPServer

DATA = Path(__file__).parent / "data"
mcp = MCPServer("sap-auth")


def lade(datei):
    """Liest eine CSV-Datei aus dem Ordner data als Liste von Zeilen."""
    with open(DATA / datei, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


@mcp.tool()
def benutzer_rollen(benutzer: str) -> dict:
    """Zeigt Stammdaten, Rollen und Transaktionen eines SAP-Benutzers. Nur lesend."""
    benutzer = benutzer.strip().upper()
    stamm = next((b for b in lade("benutzer.csv") if b["benutzer"] == benutzer), None)
    if stamm is None:
        return {"fehler": f"Benutzer {benutzer} nicht gefunden"}

    rollen = [z["rolle"] for z in lade("benutzer_rollen.csv") if z["benutzer"] == benutzer]
    tcodes = sorted({r["tcode"] for r in lade("rollen.csv") if r["rolle"] in rollen})
    return {"benutzer": stamm, "rollen": rollen, "transaktionen": tcodes}


if __name__ == "__main__":
    mcp.run(transport="stdio")