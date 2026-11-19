"""Erzeugt einen Prüfbericht als Excel-Datei. Ohne KI, direkt aus der Regellogik des Servers."""
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from server import lade, risiko_uebersicht

ZIEL = Path(__file__).parent / "berichte"
KOPF = PatternFill("solid", fgColor="0B3D91")
FARBEN = {"hoch": "F8D7DA", "mittel": "FFF3CD", "prüfen": "DDEBF7"}


def tabelle(blatt, kopf, zeilen, breiten):
    """Schreibt eine Tabelle mit farbiger Kopfzeile, Filter und festen Spaltenbreiten."""
    blatt.append(kopf)
    for zelle in blatt[1]:
        zelle.font = Font(bold=True, color="FFFFFF")
        zelle.fill = KOPF
    for zeile in zeilen:
        blatt.append(zeile)
    for nr, breite in enumerate(breiten, start=1):
        blatt.column_dimensions[get_column_letter(nr)].width = breite
    for zeile in blatt.iter_rows(min_row=2):
        for zelle in zeile:
            zelle.alignment = Alignment(wrap_text=True, vertical="top")
    blatt.freeze_panes = "A2"
    blatt.auto_filter.ref = blatt.dimensions


def main():
    r = risiko_uebersicht()
    mappe = Workbook()

    # Blatt 1: Zusammenfassung
    blatt = mappe.active
    blatt.title = "Zusammenfassung"
    blatt.append(["Prüfbericht SAP-Berechtigungen"])
    blatt["A1"].font = Font(bold=True, size=14)
    blatt.append(["Stand", r["stand"]])
    blatt.append(["Befunde gesamt", r["anzahl_befunde"]])
    for prioritaet, anzahl in r["nach_prioritaet"].items():
        blatt.append([f"Priorität {prioritaet}", anzahl])
    blatt.append(["Abgesicherte Konflikte", len(r["abgesicherte_konflikte"])])
    blatt.append([])
    blatt.append(["Erstellt ohne KI aus fester Regellogik. Befunde der Priorität 'prüfen' "
                  "sind Hinweise und von einem Menschen zu bewerten."])
    blatt.column_dimensions["A"].width = 30
    blatt.column_dimensions["B"].width = 16

    # Blatt 2: Befunde, mit leeren Spalten für die Nachverfolgung
    befunde = mappe.create_sheet("Befunde")
    tabelle(
        befunde,
        ["Priorität", "Kategorie", "Benutzer", "Name", "Detail", "Maßnahme", "Verantwortlich", "Erledigt bis"],
        [[b["prioritaet"], b["kategorie"], b["benutzer"], b["name"], b["detail"], "", "", ""]
         for b in r["befunde"]],
        [10, 22, 16, 18, 70, 30, 18, 13],
    )
    for zeile in befunde.iter_rows(min_row=2):
        farbe = FARBEN.get(zeile[0].value)
        if farbe:
            zeile[0].fill = PatternFill("solid", fgColor=farbe)

    # Blatt 3: Genehmigte Ausnahmen mit Status
    heute = date.today()
    tabelle(
        mappe.create_sheet("Ausnahmen"),
        ["Benutzer", "Regel", "Kontrolle", "Verantwortlich", "Gültig bis", "Status"],
        [[a["benutzer"], a["regel_id"], a["kontrolle"], a["verantwortlich"], a["gueltig_bis"],
          "gültig" if date.fromisoformat(a["gueltig_bis"]) >= heute else "abgelaufen"]
         for a in lade("ausnahmen.csv")],
        [16, 10, 60, 20, 13, 12],
    )

    ZIEL.mkdir(exist_ok=True)
    datei = ZIEL / f"Pruefbericht_{r['stand']}.xlsx"
    try:
        mappe.save(datei)
    except PermissionError:
        print(f"Konnte {datei.name} nicht speichern. Ist die Datei noch in Excel geöffnet?")
        return
    print(f"Bericht gespeichert: {datei}")


if __name__ == "__main__":
    main()