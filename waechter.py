"""Wächter: prüft alle Berechtigungen und meldet nur, was sich seit dem letzten Lauf geändert hat.

Ausführen mit:  python waechter.py
Mit kurzer Zusammenfassung durch das lokale Modell:  python waechter.py --ki

Der Wächter ist nur lesend. Er schlägt Lösungen vor, entscheiden muss ein Mensch."""
import argparse
import json
import os
import re
from datetime import datetime
from pathlib import Path

import server

BASIS = Path(__file__).parent
ZUSTAND = BASIS / "zustand" / "letzter_lauf.json"
AUSGABE = BASIS / "berichte"
LOKALES_MODELL = os.environ.get("LOKALES_MODELL", "qwen3:8b")


def schluessel(befund):
    """Eindeutige Kennung eines Befunds, die sich von Tag zu Tag nicht ändert."""
    teile = [befund["kategorie"], befund["benutzer"]]
    regel = re.match(r"SOD\d+", befund["detail"])
    if regel:
        teile.append(regel.group())
    return " | ".join(teile)


def lade_zustand():
    if ZUSTAND.exists():
        return json.loads(ZUSTAND.read_text(encoding="utf-8"))
    return None


def speichere_zustand(zeitpunkt, befunde):
    ZUSTAND.parent.mkdir(exist_ok=True)
    zustand = {"stand": zeitpunkt, "befunde": sorted(schluessel(b) for b in befunde)}
    ZUSTAND.write_text(json.dumps(zustand, ensure_ascii=False, indent=2), encoding="utf-8")


def ergaenze_vorschlaege(befunde):
    """Hängt an neue SoD-Befunde den Vorschlag aus loesungsvorschlaege an."""
    bereits_berechnet = {}
    for b in befunde:
        if b["kategorie"] not in ("SoD-Konflikt", "Ausnahme abgelaufen"):
            continue
        if b["benutzer"] not in bereits_berechnet:
            bereits_berechnet[b["benutzer"]] = server.loesungsvorschlaege(b["benutzer"])
        v = bereits_berechnet[b["benutzer"]]
        b["vorschlag"] = (f'Rolle {v["empfehlung"]} entziehen' if v["empfehlung"]
                          else "Keine einfache Lösung: Ausnahme mit Kontrolle oder Aufgaben trennen")
    return befunde


def ki_zusammenfassung(neu, erledigt):
    """Lässt das lokale Modell die Änderungen in wenigen Sätzen zusammenfassen. Keine Daten gehen nach außen."""
    try:
        import ollama
        antwort = ollama.chat(
            model=LOKALES_MODELL,
            messages=[
                {"role": "system", "content":
                    "Du fasst Ergebnisse einer Berechtigungsprüfung für eine Führungskraft zusammen. "
                    "Nutze ausschließlich die gelieferten Daten. Erfinde keine Namen, Rollen oder Zahlen. "
                    "Höchstens fünf Sätze, zuerst das Wichtigste. Entscheidungen trifft ein Mensch. "
                    "Antworte auf Deutsch."},
                {"role": "user", "content": json.dumps({"neue_befunde": neu, "erledigte_befunde": erledigt},
                                                       ensure_ascii=False)},
            ],
            think=False,
            options={"num_ctx": 16384},
        )
        return antwort.message.content.strip()
    except Exception as fehler:
        return f"(Keine KI-Zusammenfassung möglich: {fehler})"


def als_markdown(ergebnis):
    zeilen = [f'# Wächter-Lauf vom {ergebnis["zeitpunkt"]}', ""]
    if ergebnis["erster_lauf"]:
        zeilen += ["Erster Lauf: Alle aktuellen Befunde gelten als neu.", ""]
    else:
        zeilen += [f'Vergleich mit dem Lauf vom {ergebnis["letzter_lauf"]}.', ""]
    if ergebnis.get("zusammenfassung"):
        zeilen += ["## Zusammenfassung", "", ergebnis["zusammenfassung"], ""]

    zeilen += [f'## Neue Befunde ({len(ergebnis["neu"])})', ""]
    if ergebnis["neu"]:
        zeilen += ["| Priorität | Kategorie | Benutzer | Detail | Vorschlag |", "|---|---|---|---|---|"]
        for b in ergebnis["neu"]:
            zeilen.append(f'| {b["prioritaet"]} | {b["kategorie"]} | {b["benutzer"]} ({b["name"]}) '
                          f'| {b["detail"]} | {b.get("vorschlag", "")} |')
    else:
        zeilen.append("Keine.")

    zeilen += ["", f'## Erledigt seit dem letzten Lauf ({len(ergebnis["erledigt"])})', ""]
    zeilen += [f"- {k}" for k in ergebnis["erledigt"]] or ["Keine."]
    zeilen += ["", f'Offene Befunde insgesamt: {ergebnis["offen_gesamt"]}', ""]
    return "\n".join(zeilen)


def lauf(ki=False):
    """Ein kompletter Wächter-Lauf. Liefert das Ergebnis und schreibt Bericht und Zustand."""
    zeitpunkt = datetime.now().strftime("%Y-%m-%d %H:%M")
    vorher = lade_zustand()
    befunde = server.risiko_uebersicht()["befunde"]

    bekannt = set(vorher["befunde"]) if vorher else set()
    aktuell = {schluessel(b): b for b in befunde}
    neu = ergaenze_vorschlaege([b for k, b in aktuell.items() if k not in bekannt])
    erledigt = sorted(bekannt - aktuell.keys())

    ergebnis = {
        "zeitpunkt": zeitpunkt,
        "erster_lauf": vorher is None,
        "letzter_lauf": vorher["stand"] if vorher else None,
        "neu": neu,
        "erledigt": erledigt,
        "offen_gesamt": len(befunde),
        "zusammenfassung": None,
    }
    if ki and (neu or erledigt):
        ergebnis["zusammenfassung"] = ki_zusammenfassung(neu, erledigt)

    AUSGABE.mkdir(exist_ok=True)
    ergebnis["bericht"] = None
    if neu or erledigt:  # Nur bei Änderungen ein neuer Bericht, sonst entsteht täglich Ballast
        datei = AUSGABE / f'Waechter_{zeitpunkt.replace(":", "").replace(" ", "_")}.md'
        datei.write_text(als_markdown(ergebnis), encoding="utf-8")
        ergebnis["bericht"] = str(datei)
    (AUSGABE / "waechter_letzter.json").write_text(json.dumps(ergebnis, ensure_ascii=False, indent=2),
                                                   encoding="utf-8")
    speichere_zustand(zeitpunkt, befunde)
    return ergebnis


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Wächter für SAP-Berechtigungen")
    parser.add_argument("--ki", action="store_true", help="Zusammenfassung durch das lokale Modell")
    e = lauf(ki=parser.parse_args().ki)

    print(f'Wächter-Lauf {e["zeitpunkt"]}: {len(e["neu"])} neu, {len(e["erledigt"])} erledigt, '
          f'{e["offen_gesamt"]} offen')
    for b in e["neu"]:
        print(f'  NEU [{b["prioritaet"]}] {schluessel(b)}'
              + (f' -> {b["vorschlag"]}' if b.get("vorschlag") else ""))
    for k in e["erledigt"]:
        print(f"  ERLEDIGT {k}")
    if e["zusammenfassung"]:
        print("\n" + e["zusammenfassung"])
    print(f'\nBericht: {e["bericht"]}' if e["bericht"] else "\nKeine Änderungen, kein neuer Bericht.")
