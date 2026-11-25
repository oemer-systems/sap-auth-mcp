"""Importiert SAP-Standardexporte (z. B. aus SE16N) in das Datenformat des Servers.

Erwartet im Ordner data/sap_export:
  USR02.csv       Benutzer: Sperrkennzeichen, Anmeldedatum, Gültigkeit
  USER_ADDR.csv   Namen und Abteilung
  AGR_USERS.csv   Rollenzuordnungen mit Gültigkeitszeitraum
  AGR_TCODES.csv  Transaktionen je Rolle
  AGR_TEXTS.csv   Rollenbeschreibungen
  PA0105.csv      Zuordnung Personalnummer zu SAP-Benutzer (Subtyp 0001)
  PA0000.csv      Personalmaßnahmen: Beschäftigungsstatus, Eintritt, Austritt
  PA0001.csv      Organisatorische Zuordnung mit Gültigkeit
  T527X.csv       Bezeichnungen der Organisationseinheiten
  ST03N_NUTZUNG.csv  Aufrufe je Benutzer, Transaktion und Monat (Nutzungsstatistik aus ST03N)

Schreibt: data/benutzer.csv, data/benutzer_rollen.csv, data/rollen.csv, data/personal.csv,
          data/nutzung.csv
Das Regelwerk (funktionen, sod_regeln, transaktionen, ausnahmen) bleibt unverändert.
"""
import csv
from datetime import date, datetime
from pathlib import Path

BASIS = Path(__file__).parent
QUELLE = BASIS / "data" / "sap_export"
ZIEL = BASIS / "data"


def lies(datei):
    """Liest einen Export. Erkennt Kodierung (UTF-8 oder Windows) und Trennzeichen (; oder ,)."""
    roh = (QUELLE / datei).read_bytes()
    try:
        text = roh.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = roh.decode("cp1252")
    zeilen = text.splitlines()
    trenner = ";" if zeilen[0].count(";") >= zeilen[0].count(",") else ","
    return [
        {k.strip(): (v or "").strip() for k, v in z.items()}
        for z in csv.DictReader(zeilen, delimiter=trenner)
    ]


def datum(wert):
    """Wandelt SAP-Datumsformate in ein Datum: 20261114, 14.11.2026 oder 2026-11-14.
    Leere Werte und 00000000 ergeben None."""
    if not wert or set(wert) <= {"0", ".", "-"}:
        return None
    for format in ("%Y%m%d", "%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(wert, format).date()
        except ValueError:
            pass
    raise ValueError(f"Unbekanntes Datumsformat: {wert}")


def gueltig(zeile, heute):
    """Prüft, ob ein Datensatz mit BEGDA und ENDDA heute gilt."""
    von, bis = datum(zeile.get("BEGDA")), datum(zeile.get("ENDDA"))
    return (von is None or von <= heute) and (bis is None or bis >= heute)


def importiere_personal(heute):
    """Verknüpft SAP-Benutzer mit Personaldaten: Abteilung laut HR, Beschäftigungsstatus, Austritt."""
    pernr_zu_benutzer = {
        z["PERNR"]: z["USRID"]
        for z in lies("PA0105.csv")
        if z["SUBTY"] == "0001" and gueltig(z, heute)
    }
    org_texte = {t["ORGEH"]: t["ORGTX"] for t in lies("T527X.csv")}
    massnahmen = [z for z in lies("PA0000.csv") if gueltig(z, heute)]
    zuordnungen = [z for z in lies("PA0001.csv") if gueltig(z, heute)]

    personal = []
    for pernr, benutzer in pernr_zu_benutzer.items():
        status = next((m for m in massnahmen if m["PERNR"] == pernr), None)
        org = next((o for o in zuordnungen if o["PERNR"] == pernr), None)
        ausgetreten = status is not None and status["STAT2"] == "0"
        personal.append({
            "benutzer": benutzer,
            "personalnummer": pernr,
            "abteilung": org_texte.get(org["ORGEH"], org["ORGEH"]) if org else "",
            "abteilung_seit": datum(org["BEGDA"]).isoformat() if org else "",
            "status": "ausgetreten" if ausgetreten else "beschäftigt",
            "austritt": datum(status["BEGDA"]).isoformat() if ausgetreten else "",
        })
    return personal


def importiere_nutzung():
    """Liest die Nutzungsstatistik: wie oft ein Benutzer eine Transaktion in einem Monat aufgerufen hat.
    Monat 202603 wird zu 2026-03. Zeilen ohne Aufrufe werden übersprungen."""
    nutzung = []
    for z in lies("ST03N_NUTZUNG.csv"):
        anzahl = int(z["DIALOGSCHRITTE"] or 0)
        if anzahl == 0:
            continue
        nutzung.append({
            "benutzer": z["BENUTZER"],
            "tcode": z["TCODE"],
            "monat": f'{z["MONAT"][:4]}-{z["MONAT"][4:6]}',
            "anzahl": anzahl,
        })
    return nutzung


def schreibe(datei, felder, zeilen):
    with open(ZIEL / datei, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=felder, lineterminator="\n")
        w.writeheader()
        w.writerows(zeilen)


def main():
    heute = date.today()

    # Benutzer aus USR02, ergänzt um Namen aus USER_ADDR
    adressen = {a["BNAME"]: a for a in lies("USER_ADDR.csv")}
    benutzer = []
    for u in lies("USR02.csv"):
        adr = adressen.get(u["BNAME"], {})
        gueltig_bis = datum(u.get("GLTGB"))
        gesperrt = u["UFLAG"] != "0" or (gueltig_bis is not None and gueltig_bis < heute)
        letzte = datum(u.get("TRDAT")) or datum(u.get("ERDAT"))
        if letzte is None:
            print(f"Warnung: {u['BNAME']} hat weder Anmelde- noch Anlagedatum")
            letzte = date(1970, 1, 1)
        benutzer.append({
            "benutzer": u["BNAME"],
            "name": f'{adr.get("NAME_FIRST", "")} {adr.get("NAME_LAST", "")}'.strip() or u["BNAME"],
            "abteilung": adr.get("DEPARTMENT", ""),
            "status": "gesperrt" if gesperrt else "aktiv",
            "letzte_anmeldung": letzte.isoformat(),
        })

    # Rollenzuordnungen aus AGR_USERS, nur die heute gültigen
    zuordnungen, uebersprungen = [], 0
    for z in lies("AGR_USERS.csv"):
        von, bis = datum(z["FROM_DAT"]), datum(z["TO_DAT"])
        if (von and von > heute) or (bis and bis < heute):
            uebersprungen += 1
            continue
        eintrag = {"benutzer": z["UNAME"], "rolle": z["AGR_NAME"]}
        if eintrag not in zuordnungen:
            zuordnungen.append(eintrag)

    # Rollen mit Transaktionen aus AGR_TCODES, Beschreibung aus AGR_TEXTS
    texte = {t["AGR_NAME"]: t["TEXT"] for t in lies("AGR_TEXTS.csv") if not t["LINE"].strip("0")}
    rollen = [
        {"rolle": r["AGR_NAME"], "beschreibung": texte.get(r["AGR_NAME"], ""), "tcode": r["TCODE"]}
        for r in lies("AGR_TCODES.csv")
        if r.get("TYPE", "TR") == "TR"
    ]

    schreibe("benutzer.csv", ["benutzer", "name", "abteilung", "status", "letzte_anmeldung"], benutzer)
    schreibe("benutzer_rollen.csv", ["benutzer", "rolle"], zuordnungen)
    schreibe("rollen.csv", ["rolle", "beschreibung", "tcode"], rollen)

    personal = importiere_personal(heute)
    schreibe("personal.csv",
             ["benutzer", "personalnummer", "abteilung", "abteilung_seit", "status", "austritt"],
             personal)

    nutzung = importiere_nutzung()
    schreibe("nutzung.csv", ["benutzer", "tcode", "monat", "anzahl"], nutzung)

    gesperrte = sum(1 for b in benutzer if b["status"] == "gesperrt")
    print(f"Benutzer:          {len(benutzer)} (davon gesperrt: {gesperrte})")
    print(f"Rollenzuordnungen: {len(zuordnungen)} (nicht gültig, übersprungen: {uebersprungen})")
    print(f"Rollen:            {len({r['rolle'] for r in rollen})} mit {len(rollen)} Transaktionen")
    ohne_personal = sorted({b["benutzer"] for b in benutzer} - {p["benutzer"] for p in personal})
    ausgetreten = sum(1 for p in personal if p["status"] == "ausgetreten")
    print(f"Personaldaten:     {len(personal)} Benutzer verknüpft (davon ausgetreten: {ausgetreten})")
    print(f"Ohne Personalbezug: {', '.join(ohne_personal) or 'keine'}")
    monate = sorted({n["monat"] for n in nutzung})
    zeitraum = f"{monate[0]} bis {monate[-1]}" if monate else "keine Daten"
    print(f"Nutzungsdaten:     {len(nutzung)} Einträge, {len(monate)} Monate ({zeitraum})")


if __name__ == "__main__":
    main()
