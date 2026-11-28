import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Literal

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
        # Mit Zeitzone, z. B. 2026-11-27T22:44:55+01:00. So ist jede Zeile eindeutig, egal wo sie entstand
        "zeit": datetime.now().astimezone().isoformat(timespec="seconds"),
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


def funktionen_von(tcodes):
    """Ordnet Transaktionen den Funktionen des Regelwerks zu.
    Ergebnis: {Funktion: [Transaktionen des Benutzers, die dazu gehören]}."""
    zuordnung = {}
    for f in lade("funktionen.csv"):
        if f["tcode"] in tcodes:
            zuordnung.setdefault(f["funktion_id"], []).append(f["tcode"])
    return zuordnung


def personaldaten(kennung):
    """Liefert die Personaldaten eines Benutzers aus personal.csv oder None (kein Personalbezug)."""
    return next((p for p in lade("personal.csv") if p["benutzer"] == kennung), None)


def soll_rollen(abteilung):
    """Liefert die Rollen, die laut Rollenkonzept zu einer Abteilung gehören."""
    return {z["rolle"] for z in lade("abteilungsrollen.csv") if z["abteilung"] == abteilung}


def ausnahme_status(kennung, regel_id):
    """Sucht eine genehmigte Ausnahme zu einem Konflikt und bewertet sie:
    offen (keine Ausnahme), abgesichert (gültig) oder ausnahme_abgelaufen."""
    for a in lade("ausnahmen.csv"):
        if a["benutzer"] == kennung and a["regel_id"] == regel_id:
            if date.fromisoformat(a["gueltig_bis"]) >= date.today():
                return "abgesichert", a
            return "ausnahme_abgelaufen", a
    return "offen", None


def sod_konflikte(kennung):
    """Liefert alle SoD-Regeln, bei denen der Benutzer beide Funktionen besitzt,
    zusammen mit den Transaktionen, die den Konflikt auslösen."""
    _, tcodes = transaktionen_von(kennung)
    funktionen = funktionen_von(tcodes)
    konflikte = []
    for regel in lade("sod_regeln.csv"):
        a, b = regel["funktion_a"], regel["funktion_b"]
        if a in funktionen and b in funktionen:
            status, ausnahme = ausnahme_status(kennung, regel["regel_id"])
            konflikte.append({
                **regel,
                "transaktionen_a": funktionen[a],
                "transaktionen_b": funktionen[b],
                "status": status,
                "ausnahme": ausnahme,
            })
    return konflikte


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
    """Prüft, OB ein SAP-Benutzer SoD-Konflikte hat (Funktionstrennung), und listet sie auf.
    Liefert keine Lösungen. Wird gefragt, wie Konflikte gelöst, behoben oder bereinigt werden,
    nutze loesungsvorschlaege. Das Ergebnis stammt aus fester Regellogik, nicht aus einer
    Einschätzung der KI. Akzeptiert Benutzerkennung oder vollen Namen. Nur lesend."""
    stamm = finde_benutzer(benutzer)
    if stamm is None:
        ergebnis = {"fehler": f"Benutzer {benutzer} nicht gefunden"}
    else:
        konflikte = sod_konflikte(stamm["benutzer"])
        ergebnis = {
            "benutzer": stamm["benutzer"],
            "name": stamm["name"],
            "anzahl_konflikte": len(konflikte),
            "davon_offen": sum(1 for k in konflikte if k["status"] != "abgesichert"),
            "konflikte": konflikte,
        }
    protokolliere("sod_pruefen", {"benutzer": benutzer}, ergebnis)
    return ergebnis


INAKTIV_TAGE_MAX = 90   # größere Werte würden inaktive Benutzer aus der Liste verschwinden lassen


def begrenze_inaktiv_tage(tage):
    """Senkt die Schwelle für Inaktivität auf höchstens 90 Tage. Kleinere Werte sind erlaubt,
    denn sie erzeugen nur mehr Alarm. Liefert die Schwelle und gegebenenfalls einen Hinweis."""
    if tage > INAKTIV_TAGE_MAX:
        return INAKTIV_TAGE_MAX, (f"Schwelle von {tage} auf {INAKTIV_TAGE_MAX} Tage gesenkt, damit keine "
                                  "inaktiven Benutzer aus der Prüfung fallen")
    return tage, None


@mcp.tool()
def inaktive_benutzer(tage: int = 90) -> dict:
    """Findet aktive Benutzer, die sich seit mindestens `tage` Tagen nicht angemeldet haben,
    aber noch Rollen besitzen. Typischer Prüfpunkt der Revision. Die Schwelle beträgt höchstens
    90 Tage, größere Angaben werden gesenkt. Nur lesend."""
    tage, hinweis = begrenze_inaktiv_tage(tage)
    heute = date.today()
    treffer = []
    for b in lade("benutzer.csv"):
        if b["status"] != "aktiv":
            continue
        inaktiv_seit = (heute - date.fromisoformat(b["letzte_anmeldung"])).days
        rollen, _ = transaktionen_von(b["benutzer"])
        if inaktiv_seit >= tage and rollen:
            treffer.append({
                "benutzer": b["benutzer"],
                "name": b["name"],
                "abteilung": b["abteilung"],
                "letzte_anmeldung": b["letzte_anmeldung"],
                "tage_inaktiv": inaktiv_seit,
                "rollen": rollen,
            })
    treffer.sort(key=lambda t: t["tage_inaktiv"], reverse=True)
    ergebnis = {"schwelle_tage": tage, "hinweise": [hinweis] if hinweis else [],
                "anzahl": len(treffer), "benutzer": treffer}
    protokolliere("inaktive_benutzer", {"tage": tage}, ergebnis)
    return ergebnis


@mcp.tool()
def gesperrte_mit_rollen() -> dict:
    """Findet SAP-Benutzer mit Sperrkennzeichen, denen noch Rollen zugeordnet sind. Prüft nur den
    Benutzerstatus in SAP. Für Fragen nach Mitarbeitern, die das Unternehmen verlassen haben,
    nutze austritt_mit_rechten, denn das prüft zusätzlich die Personaldaten. Nur lesend."""
    treffer = []
    for b in lade("benutzer.csv"):
        if b["status"] != "gesperrt":
            continue
        rollen, tcodes = transaktionen_von(b["benutzer"])
        if rollen:
            treffer.append({
                "benutzer": b["benutzer"],
                "name": b["name"],
                "abteilung": b["abteilung"],
                "letzte_anmeldung": b["letzte_anmeldung"],
                "rollen": rollen,
                "transaktionen": tcodes,
            })
    ergebnis = {"anzahl": len(treffer), "benutzer": treffer}
    protokolliere("gesperrte_mit_rollen", {}, ergebnis)
    return ergebnis


@mcp.tool()
def kritische_berechtigungen() -> dict:
    """Listet alle Benutzer mit kritischen Transaktionen, z. B. Benutzerpflege, Rollenpflege,
    ABAP-Editor, Tabellenpflege oder Zahllauf. Ob die Berechtigung zur Tätigkeit passt,
    beurteilt ein Mensch anhand von Abteilung und Aufgabe. Nur lesend."""
    kritisch = {
        t["tcode"]: t["beschreibung"]
        for t in lade("transaktionen.csv")
        if t["kritisch"] == "ja"
    }
    treffer = []
    for b in lade("benutzer.csv"):
        _, tcodes = transaktionen_von(b["benutzer"])
        gefunden = [{"tcode": t, "beschreibung": kritisch[t]} for t in tcodes if t in kritisch]
        if gefunden:
            treffer.append({
                "benutzer": b["benutzer"],
                "name": b["name"],
                "abteilung": b["abteilung"],
                "status": b["status"],
                "kritische_transaktionen": gefunden,
            })
    treffer.sort(key=lambda t: len(t["kritische_transaktionen"]), reverse=True)
    ergebnis = {"anzahl": len(treffer), "benutzer": treffer}
    protokolliere("kritische_berechtigungen", {}, ergebnis)
    return ergebnis


@mcp.tool()
def austritt_mit_rechten() -> dict:
    """Findet Mitarbeiter, die das Unternehmen verlassen haben (ausgetreten, ausgeschieden, gekündigt,
    Leaver), deren SAP-Benutzer aber nicht gesperrt ist oder noch Rollen hat. Gleicht dafür die
    Personaldaten mit SAP ab. Nutze dieses Werkzeug bei allen Fragen nach ehemaligen Mitarbeitern
    mit Rechten oder Rollen. Mit Bereinigungsvorschlag. Nur lesend."""
    treffer = []
    for p in lade("personal.csv"):
        if p["status"] != "ausgetreten":
            continue
        b = finde_benutzer(p["benutzer"])
        rollen, _ = transaktionen_von(p["benutzer"])
        if b and (b["status"] == "aktiv" or rollen):
            aktiv = b["status"] == "aktiv"
            treffer.append({
                "benutzer": p["benutzer"],
                "name": b["name"],
                "austritt": p["austritt"],
                "benutzer_gesperrt": not aktiv,
                "letzte_anmeldung": b["letzte_anmeldung"],
                "rollen": rollen,
                "vorschlag": ("Benutzer sperren und " if aktiv else "") + "alle Rollen entziehen",
            })
    ergebnis = {"anzahl": len(treffer), "benutzer": treffer}
    protokolliere("austritt_mit_rechten", {}, ergebnis)
    return ergebnis


WECHSEL_TAGE = 365


@mcp.tool()
def rollen_ausserhalb_rollenkonzept() -> dict:
    """Vergleicht die Rollen jedes beschäftigten Mitarbeiters mit den Soll-Rollen seiner Abteilung
    laut Personalabteilung. Erkennt Abteilungswechsel, nach denen alte Rollen nicht entzogen wurden.
    Mit Bereinigungsvorschlag. Nur lesend."""
    heute = date.today()
    treffer = []
    for p in lade("personal.csv"):
        if p["status"] != "beschäftigt":
            continue
        rollen, _ = transaktionen_von(p["benutzer"])
        unpassend = sorted(set(rollen) - soll_rollen(p["abteilung"]))
        if unpassend:
            seit = (heute - date.fromisoformat(p["abteilung_seit"])).days
            treffer.append({
                "benutzer": p["benutzer"],
                "name": finde_benutzer(p["benutzer"])["name"],
                "abteilung": p["abteilung"],
                "abteilung_seit": p["abteilung_seit"],
                "art": "Wechsel ohne Bereinigung" if seit <= WECHSEL_TAGE else "Rolle außerhalb des Rollenkonzepts",
                "unpassende_rollen": unpassend,
                "vorschlag": "Entzug prüfen: " + ", ".join(unpassend),
            })
    ergebnis = {"anzahl": len(treffer), "benutzer": treffer}
    protokolliere("rollen_ausserhalb_rollenkonzept", {}, ergebnis)
    return ergebnis


@mcp.tool()
def ablaufende_ausnahmen(tage: int = 30) -> dict:
    """Findet genehmigte Ausnahmen, die in den nächsten `tage` Tagen ablaufen, damit der
    Verantwortliche rechtzeitig verlängern oder den Konflikt beheben kann. Nur lesend."""
    heute = date.today()
    treffer = []
    for a in lade("ausnahmen.csv"):
        rest = (date.fromisoformat(a["gueltig_bis"]) - heute).days
        if 0 <= rest <= tage:
            treffer.append({**a, "tage_bis_ablauf": rest})
    ergebnis = {"zeitraum_tage": tage, "anzahl": len(treffer), "ausnahmen": treffer}
    protokolliere("ablaufende_ausnahmen", {"tage": tage}, ergebnis)
    return ergebnis


def tcodes_der_rollen(rollen):
    """Alle Transaktionen, die in einer Liste von Rollen enthalten sind."""
    return sorted({r["tcode"] for r in lade("rollen.csv") if r["rolle"] in rollen})


def regelverstoesse(tcodes):
    """IDs aller SoD-Regeln, die eine Menge von Transaktionen verletzt (ohne Ausnahmen)."""
    funktionen = funktionen_von(tcodes)
    return [
        r["regel_id"] for r in lade("sod_regeln.csv")
        if r["funktion_a"] in funktionen and r["funktion_b"] in funktionen
    ]


def berechne_entzug(kennung, rollen):
    """Rechnet aus, was der Entzug von Rollen bewirken würde. Ohne Protokoll, ohne Prüfung der Eingabe."""
    vorher_rollen, vorher_tcodes = transaktionen_von(kennung)
    nachher_tcodes = tcodes_der_rollen([r for r in vorher_rollen if r not in rollen])
    verloren = sorted(set(vorher_tcodes) - set(nachher_tcodes))

    vorher_sod = regelverstoesse(vorher_tcodes)
    nachher_sod = regelverstoesse(nachher_tcodes)
    geloest = [r for r in vorher_sod if r not in nachher_sod]

    kritisch = {t["tcode"] for t in lade("transaktionen.csv") if t["kritisch"] == "ja"}
    p = personaldaten(kennung)
    abteilung_betroffen = sorted(set(rollen) & soll_rollen(p["abteilung"])) if p else []

    if abteilung_betroffen:
        bewertung = "Achtung: Rollen der eigenen Abteilung betroffen, die Tätigkeit könnte eingeschränkt werden"
    elif geloest and not nachher_sod:
        bewertung = "Empfehlenswert: löst alle SoD-Konflikte, ohne Rollen der Abteilung zu berühren"
    elif geloest:
        bewertung = "Teilweise wirksam: löst einen Teil der SoD-Konflikte"
    else:
        bewertung = "Kein Einfluss auf SoD-Konflikte"

    return {
        "benutzer": kennung,
        "entzogene_rollen": rollen,
        "verlorene_transaktionen": verloren,
        "verlorene_kritische_transaktionen": sorted(set(verloren) & kritisch),
        "geloeste_konflikte": geloest,
        "verbleibende_konflikte": nachher_sod,
        "rollen_der_abteilung_betroffen": abteilung_betroffen,
        "bewertung": bewertung,
    }


@mcp.tool()
def simuliere_entzug(benutzer: str, rollen: list[str]) -> dict:
    """Simuliert, was passieren würde, wenn einem Benutzer Rollen entzogen würden: welche
    Transaktionen wegfallen, welche SoD-Konflikte sich lösen und ob Rollen seiner Abteilung
    betroffen wären. `rollen` sind Rollennamen wie Z_FI_ZAHLLAUF, KEINE Transaktionscodes
    wie F110. Die Rollen eines Benutzers liefert benutzer_rollen. Ändert nichts, nur lesend."""
    stamm = finde_benutzer(benutzer)
    if stamm is None:
        ergebnis = {"fehler": f"Benutzer {benutzer} nicht gefunden"}
        protokolliere("simuliere_entzug", {"benutzer": benutzer, "rollen": rollen}, ergebnis)
        return ergebnis

    kennung = stamm["benutzer"]
    vorher_rollen, _ = transaktionen_von(kennung)
    ungueltig = [r for r in rollen if r not in vorher_rollen]
    if ungueltig:
        hinweise = []
        for name in ungueltig:
            enthalten_in = sorted({
                z["rolle"] for z in lade("rollen.csv")
                if z["tcode"] == name and z["rolle"] in vorher_rollen
            })
            if enthalten_in:
                hinweise.append(f"{name} ist eine Transaktion, keine Rolle. Sie steckt in: {', '.join(enthalten_in)}")
            else:
                hinweise.append(f"{name} ist dem Benutzer nicht als Rolle zugeordnet")
        ergebnis = {
            "fehler": "Ungültige Rollenangabe, es wurde nichts simuliert",
            "hinweise": hinweise,
            "zugeordnete_rollen": vorher_rollen,
        }
    else:
        ergebnis = berechne_entzug(kennung, rollen)
    protokolliere("simuliere_entzug", {"benutzer": benutzer, "rollen": rollen}, ergebnis)
    return ergebnis


@mcp.tool()
def loesungsvorschlaege(benutzer: str) -> dict:
    """Beantwortet, WIE die SoD-Konflikte eines Benutzers gelöst, behoben oder bereinigt werden
    ("Wie löse ich die Konflikte von ...", "Was soll ich tun?"). Enthält die Konflikte selbst
    bereits, ein vorheriges sod_pruefen ist nicht nötig. Ermittelt die besten Lösungen. Simuliert den
    Entzug jeder einzelnen zugeordneten Rolle und sortiert: zuerst Rollen, die laut Nutzungsdaten
    nicht benötigt werden, dann Rollen außerhalb der eigenen Abteilung, dann nach Wirkung. Nutze
    dieses Werkzeug, wenn nach Lösungen gefragt wird, und nenne nur die Rollen aus seinem Ergebnis.
    Ändert nichts."""
    stamm = finde_benutzer(benutzer)
    if stamm is None:
        ergebnis = {"fehler": f"Benutzer {benutzer} nicht gefunden"}
        protokolliere("loesungsvorschlaege", {"benutzer": benutzer}, ergebnis)
        return ergebnis

    kennung = stamm["benutzer"]
    offene_regeln = [k for k in sod_konflikte(kennung) if k["status"] != "abgesichert"]
    offen = [k["regel_id"] for k in offene_regeln]
    p = personaldaten(kennung)
    genutzt = genutzte_tcodes(kennung, erster_monat(NUTZUNG_MONATE))
    mit_nutzung = bool(genutzt)   # ohne Nutzungsdaten wird nichts als "nicht benötigt" bewertet
    optionen = []
    if offen:
        rollen, _ = transaktionen_von(kennung)
        for rolle in rollen:
            sim = berechne_entzug(kennung, [rolle])
            geloest = [r for r in sim["geloeste_konflikte"] if r in offen]
            if geloest:
                wegfallend_genutzt = sorted(set(sim["verlorene_transaktionen"]) & genutzt)
                optionen.append({
                    "rolle_entziehen": rolle,
                    "loest": geloest,
                    "loest_alle_offenen": len(geloest) == len(offen),
                    "verlorene_transaktionen": sim["verlorene_transaktionen"],
                    "davon_genutzt": wegfallend_genutzt if mit_nutzung else "keine Nutzungsdaten",
                    "rolle_nicht_benoetigt": mit_nutzung and not wegfallend_genutzt,
                    "rolle_gehoert_zur_abteilung": bool(sim["rollen_der_abteilung_betroffen"]),
                    "bewertung": sim["bewertung"],
                })
        for o in optionen:
            if o["rolle_nicht_benoetigt"] and o["rolle_gehoert_zur_abteilung"]:
                o["bewertung"] = (f"Rolle gehört zur Abteilung, wurde aber in {NUTZUNG_MONATE} Monaten nicht "
                                  "benötigt: Entzug mit Fachbereich abstimmen")
        optionen.sort(key=lambda o: (not o["rolle_nicht_benoetigt"], o["rolle_gehoert_zur_abteilung"],
                                     -len(o["loest"]), len(o["verlorene_transaktionen"])))

    beste = optionen[0] if optionen and optionen[0]["loest_alle_offenen"] else None
    ergebnis = {
        "benutzer": kennung,
        "abteilung": p["abteilung"] if p else stamm["abteilung"],
        "offene_konflikte": offen,
        # Was jeden Konflikt auslöst, damit die Erklärung nicht geraten werden muss
        "konflikte": [{
            "regel_id": k["regel_id"],
            "beschreibung": k["beschreibung"],
            "transaktionen": k["transaktionen_a"] + k["transaktionen_b"],
        } for k in offene_regeln],
        "empfehlung": beste["rolle_entziehen"] if beste and not beste["rolle_gehoert_zur_abteilung"] else None,
        # Rolle der eigenen Abteilung, die nicht genutzt wird: kein Automatismus, der Fachbereich entscheidet
        "mit_fachbereich_abstimmen": beste["rolle_entziehen"] if beste and beste["rolle_gehoert_zur_abteilung"]
                                     and beste["rolle_nicht_benoetigt"] else None,
        "optionen": optionen,
        "alternative": "Ist keine Option passend, kann der Konflikt als Ausnahme mit Kontrolle genehmigt werden",
    }
    protokolliere("loesungsvorschlaege", {"benutzer": benutzer}, ergebnis)
    return ergebnis


# Least Privilege: Rechte, die nicht genutzt werden

NUTZUNG_MONATE = 13        # mehr als ein Jahr, damit seltene Tätigkeiten wie der Jahresabschluss mitzählen
NOTFALLBENUTZER = ("FIREFIGHTER",)   # Kennungen mit diesem Anfang sind absichtlich selten im Einsatz


def erster_monat(monate):
    """Erster Monat des Betrachtungszeitraums als Text, z. B. 2025-11 bei 13 Monaten bis 2026-11."""
    heute = date.today()
    index = heute.year * 12 + heute.month - 1 - (monate - 1)
    return f"{index // 12}-{index % 12 + 1:02d}"


def monate_zwischen(von, bis):
    """Anzahl Monate zwischen zwei Monaten im Format 2026-03, z. B. 2025-12 bis 2026-11 ergibt 11."""
    return (int(bis[:4]) - int(von[:4])) * 12 + int(bis[5:7]) - int(von[5:7])


def letzte_nutzung(kennung, tcodes):
    """Letzter Monat, in dem der Benutzer eine dieser Transaktionen aufgerufen hat, oder None."""
    monate = [n["monat"] for n in lade("nutzung.csv")
              if n["benutzer"] == kennung and n["tcode"] in tcodes and int(n["anzahl"]) > 0]
    return max(monate) if monate else None


def genutzte_tcodes(kennung, ab_monat):
    """Alle Transaktionen, die ein Benutzer seit ab_monat mindestens einmal aufgerufen hat."""
    return {
        n["tcode"] for n in lade("nutzung.csv")
        if n["benutzer"] == kennung and n["monat"] >= ab_monat and int(n["anzahl"]) > 0
    }


@mcp.tool()
def ungenutzte_rechte(monate: int = NUTZUNG_MONATE, inaktiv_tage: int = 90) -> dict:
    """Least Privilege: Findet Rollen, die ein Benutzer im Zeitraum nicht benötigt hat. Eine Rolle ist
    verzichtbar, wenn ihr Entzug keine Transaktion kostet, die der Benutzer in den letzten `monate`
    Monaten genutzt hat. Zusätzlich: Rollen, von denen nur ein Teil genutzt wird. Gesperrte,
    ausgetretene, inaktive und Notfallbenutzer werden übersprungen, weil sie anderswo gemeldet
    werden oder absichtlich selten genutzt sind. Der Zeitraum beträgt mindestens 13 Monate,
    kürzere Angaben werden angehoben. Nur lesend."""
    hinweise = []
    if monate < NUTZUNG_MONATE:
        hinweise.append(f"Zeitraum von {monate} auf {NUTZUNG_MONATE} Monate angehoben, damit seltene "
                        "Tätigkeiten wie der Jahresabschluss nicht fälschlich als nicht benötigt gelten")
        monate = NUTZUNG_MONATE
    ab = erster_monat(monate)
    alle_monate = sorted({n["monat"] for n in lade("nutzung.csv")})
    vorhandene_monate = [m for m in alle_monate if m >= ab]
    aktueller_monat = date.today().isoformat()[:7]
    if not vorhandene_monate:
        ergebnis = {"fehler": "Keine Nutzungsdaten im Zeitraum. Ohne Daten wird kein Recht als ungenutzt gemeldet"}
        protokolliere("ungenutzte_rechte", {"monate": monate}, ergebnis)
        return ergebnis
    if len(vorhandene_monate) < monate:
        hinweise.append(f"Nur {len(vorhandene_monate)} von {monate} Monaten mit Nutzungsdaten vorhanden. "
                        "Seltene Tätigkeiten können fehlen, Befunde vorsichtig bewerten")

    heute = date.today()
    nicht_benoetigt, teilweise, ausgenommen = [], [], []
    for b in lade("benutzer.csv"):
        kennung = b["benutzer"]
        rollen, _ = transaktionen_von(kennung)
        if not rollen:
            continue
        p = personaldaten(kennung)
        tage_inaktiv = (heute - date.fromisoformat(b["letzte_anmeldung"])).days
        if kennung.startswith(NOTFALLBENUTZER):
            grund = "Notfallbenutzer, wird absichtlich selten genutzt"
        elif b["status"] != "aktiv":
            grund = "gesperrt, wird als Gesperrt mit Rollen gemeldet"
        elif p and p["status"] == "ausgetreten":
            grund = "ausgetreten, wird als Austritt gemeldet"
        elif tage_inaktiv >= inaktiv_tage:
            grund = "inaktiv, wird als Inaktiver Benutzer gemeldet"
        else:
            grund = None
        genutzt = genutzte_tcodes(kennung, ab)
        if grund is None and not genutzt:
            grund = "angemeldet, aber keine Nutzungsdaten: Datenlücke prüfen"
        if grund:
            ausgenommen.append({"benutzer": kennung, "grund": grund})
            continue

        abteilung = soll_rollen(p["abteilung"]) if p else set()
        for rolle in rollen:
            sim = berechne_entzug(kennung, [rolle])
            if not set(sim["verlorene_transaktionen"]) & genutzt:
                zuletzt = letzte_nutzung(kennung, sim["verlorene_transaktionen"])
                if zuletzt:
                    seit = f"{monate_zwischen(zuletzt, aktueller_monat)} Monaten (zuletzt genutzt {zuletzt})"
                else:
                    seit = (f"mindestens {monate_zwischen(alle_monate[0], aktueller_monat) + 1} Monaten "
                            f"(nie genutzt, Nutzungsdaten ab {alle_monate[0]})")
                nicht_benoetigt.append({
                    "benutzer": kennung,
                    "name": b["name"],
                    "rolle": rolle,
                    "nicht_benoetigt_seit": seit,
                    "zuletzt_genutzt": zuletzt,
                    "nie_genutzte_transaktionen": sim["verlorene_transaktionen"],
                    "rolle_gehoert_zur_abteilung": rolle in abteilung,
                    "loest_sod_konflikte": sim["geloeste_konflikte"],
                })
            else:
                ungenutzt = sorted(set(tcodes_der_rollen([rolle])) - genutzt)
                if ungenutzt:
                    teilweise.append({
                        "benutzer": kennung,
                        "rolle": rolle,
                        "ungenutzte_transaktionen": ungenutzt,
                    })

    ergebnis = {
        "zeitraum": f"{ab} bis {vorhandene_monate[-1]}",
        "monate_mit_daten": len(vorhandene_monate),
        "hinweise": hinweise,
        "anzahl_nicht_benoetigt": len(nicht_benoetigt),
        "nicht_benoetigte_rollen": nicht_benoetigt,
        "teilweise_genutzte_rollen": teilweise,
        "ausgenommen": ausgenommen,
    }
    protokolliere("ungenutzte_rechte", {"monate": monate}, ergebnis)
    return ergebnis


# Änderungswünsche: nichts wird ausgeführt, nur geprüft und als Antrag vorbereitet

NICHT_AUSGEFUEHRT = "Nicht ausgeführt. Dieser Assistent ändert keine Daten in SAP, es wurde nichts geändert."
AKTIONEN = {"rollen_entziehen": "Rollen entziehen", "rollen_zuordnen": "Rollen zuordnen",
            "entsperren": "Benutzer entsperren", "sperren": "Benutzer sperren"}


def antrag_text(antrag):
    """Formuliert einen Änderungsantrag als Text zum Kopieren in ein Ticket oder einen GRC-Antrag."""
    zeilen = [f'Änderungsantrag (Entwurf): {AKTIONEN[antrag["aktion"]]}',
              f'Benutzer: {antrag["benutzer"]} ({antrag["name"]}), Abteilung: {antrag["abteilung"]}']
    if antrag.get("rollen"):
        zeilen.append("Rollen: " + ", ".join(antrag["rollen"]))
    zeilen.append("Begründung:")
    zeilen += [f"- {b}" for b in antrag["begruendung"]]
    zeilen.append(f'Freigabe durch: {antrag["freigabe_durch"]}')
    zeilen.append("Status: Entwurf, nicht eingereicht. Ausführung nur über den Freigabeprozess des Unternehmens.")
    return "\n".join(zeilen)


@mcp.tool()
def aenderung_anfragen(benutzer: str,
                       aktion: Literal["rollen_entziehen", "rollen_zuordnen", "entsperren", "sperren"],
                       rollen: list[str] | None = None) -> dict:
    """Nutze dieses Werkzeug IMMER, wenn jemand etwas ändern möchte: Benutzer entsperren oder sperren,
    Rollen entziehen oder zuordnen. Führt NICHTS aus. Prüft, ob die Änderung zulässig ist, und
    bereitet bei Bedarf einen Änderungsantrag mit Begründung für den Freigabeprozess vor.
    `rollen` sind Rollennamen wie Z_FI_ZAHLLAUF. Nur lesend."""
    eingabe = {"benutzer": benutzer, "aktion": aktion, "rollen": rollen or []}
    stamm = finde_benutzer(benutzer)
    if stamm is None:
        ergebnis = {"ausgefuehrt": False, "fehler": f"Benutzer {benutzer} nicht gefunden"}
        protokolliere("aenderung_anfragen", eingabe, ergebnis)
        return ergebnis

    kennung = stamm["benutzer"]
    p = personaldaten(kennung)
    abteilung = p["abteilung"] if p else stamm["abteilung"]
    ausgetreten = bool(p and p["status"] == "ausgetreten")
    vorhanden, _ = transaktionen_von(kennung)
    rollen = rollen or []
    antrag = {"aktion": aktion, "benutzer": kennung, "name": stamm["name"], "abteilung": abteilung,
              "rollen": rollen, "begruendung": [],
              "freigabe_durch": f"Leitung {abteilung} (Fachbereich) und Berechtigungsverwaltung"}
    ergebnis = {"ausgefuehrt": False, "hinweis": NICHT_AUSGEFUEHRT, "benutzer": kennung,
                "aktion": aktion, "status_in_sap": stamm["status"]}

    if aktion == "entsperren":
        if ausgetreten:
            ergebnis.update({
                "zulaessig": False,
                "begruendung": f'{kennung} ist laut Personaldaten am {p["austritt"]} ausgetreten. Ein Entsperren '
                               "widerspricht dem Austrittsprozess.",
                "richtiges_vorgehen": "Benutzer gesperrt lassen und alle Rollen entziehen",
            })
        elif stamm["status"] == "aktiv":
            ergebnis.update({"zulaessig": False, "begruendung": f"{kennung} ist nicht gesperrt."})
        else:
            antrag["begruendung"] = ["Mitarbeiter ist laut Personaldaten beschäftigt",
                                     "Grund der Sperre vor dem Entsperren klären"]
            ergebnis.update({"zulaessig": "nur nach Prüfung des Sperrgrunds", "antrag": antrag})

    elif aktion == "sperren":
        if stamm["status"] != "aktiv":
            ergebnis.update({"zulaessig": False, "begruendung": f"{kennung} ist bereits gesperrt."})
        else:
            antrag["begruendung"] = ([f'Mitarbeiter laut Personaldaten am {p["austritt"]} ausgetreten']
                                     if ausgetreten else ["Grund der Sperre im Antrag angeben"])
            ergebnis.update({"zulaessig": True, "antrag": antrag})

    elif not rollen:
        ergebnis.update({"zulaessig": False, "begruendung": "Keine Rollen angegeben."})

    elif aktion == "rollen_entziehen":
        fremd = [r for r in rollen if r not in vorhanden]
        if fremd:
            ergebnis.update({"zulaessig": False, "begruendung": "Nicht zugeordnet: " + ", ".join(fremd),
                             "zugeordnete_rollen": vorhanden})
        else:
            sim = berechne_entzug(kennung, rollen)
            genutzt = genutzte_tcodes(kennung, erster_monat(NUTZUNG_MONATE))
            wegfallend_genutzt = sorted(set(sim["verlorene_transaktionen"]) & genutzt)
            if sim["geloeste_konflikte"]:
                antrag["begruendung"].append("Löst SoD-Konflikte: " + ", ".join(sim["geloeste_konflikte"]))
            if genutzt and not wegfallend_genutzt:
                antrag["begruendung"].append(f"In {NUTZUNG_MONATE} Monaten nicht benötigt: keine genutzte "
                                             "Transaktion fällt weg")
            elif wegfallend_genutzt:
                antrag["begruendung"].append("Achtung, genutzte Transaktionen fallen weg: "
                                             + ", ".join(wegfallend_genutzt))
            if ausgetreten:
                antrag["begruendung"].append(f'Mitarbeiter am {p["austritt"]} ausgetreten')
            if sim["rollen_der_abteilung_betroffen"]:
                antrag["begruendung"].append("Rollen der eigenen Abteilung betroffen: "
                                             + ", ".join(sim["rollen_der_abteilung_betroffen"]))
            antrag["begruendung"].append("Wegfallende Transaktionen: " + (", ".join(sim["verlorene_transaktionen"]) or "keine"))
            ergebnis.update({"zulaessig": True, "simulation": sim, "antrag": antrag})

    else:  # rollen_zuordnen
        bekannt = {r["rolle"] for r in lade("rollen.csv")}
        unbekannt = [r for r in rollen if r not in bekannt]
        if ausgetreten:
            ergebnis.update({"zulaessig": False,
                             "begruendung": f'{kennung} ist am {p["austritt"]} ausgetreten. Keine neuen Rollen.'})
        elif unbekannt:
            ergebnis.update({"zulaessig": False, "begruendung": "Unbekannte Rollen: " + ", ".join(unbekannt)})
        else:
            _, vorher = transaktionen_von(kennung)
            nachher = sorted(set(vorher) | set(tcodes_der_rollen(rollen)))
            neue_konflikte = [r for r in regelverstoesse(nachher) if r not in regelverstoesse(vorher)]
            ausserhalb = sorted(set(rollen) - soll_rollen(abteilung))
            if neue_konflikte:
                antrag["begruendung"].append("Achtung, neue SoD-Konflikte: " + ", ".join(neue_konflikte)
                                             + ". Nur mit genehmigter Ausnahme und Kontrolle")
            if ausserhalb:
                antrag["begruendung"].append(f"Nicht im Rollenkonzept von {abteilung}: " + ", ".join(ausserhalb))
            if not neue_konflikte and not ausserhalb:
                antrag["begruendung"].append("Keine neuen SoD-Konflikte, passt zum Rollenkonzept")
            ergebnis.update({"zulaessig": "nur mit Ausnahme" if neue_konflikte else True,
                             "neue_konflikte": neue_konflikte, "antrag": antrag})

    if "antrag" in ergebnis:
        ergebnis["antrag_text"] = antrag_text(ergebnis["antrag"])
    # Die Kernaussage als fertiger Satz ganz vorne: kleine Modelle halten sich sonst an Nebenfelder
    # wie status_in_sap und reimen sich eine eigene, falsche Begründung zusammen
    ergebnis = {"kurzfassung": kurzfassung(stamm["name"], aktion, ergebnis), **ergebnis}
    protokolliere("aenderung_anfragen", eingabe, ergebnis)
    return ergebnis


def kurzfassung(name, aktion, ergebnis):
    """Formuliert das Ergebnis eines Änderungswunsches in einem Satz, den die KI übernehmen soll."""
    was = AKTIONEN[aktion]
    if ergebnis.get("zulaessig") is False:
        satz = f"{was} für {name}: nicht zulässig. {ergebnis.get('begruendung', '')}"
        if ergebnis.get("richtiges_vorgehen"):
            satz += f" Richtig ist: {ergebnis['richtiges_vorgehen']}."
    elif ergebnis.get("zulaessig") is True:
        satz = f"{was} für {name}: zulässig. Ein Änderungsantrag ist vorbereitet."
    else:
        satz = f"{was} für {name}: {ergebnis.get('zulaessig')}. Ein Änderungsantrag ist vorbereitet."
    return satz + " Es wurde nichts ausgeführt."


REIHENFOLGE = {"hoch": 0, "mittel": 1, "prüfen": 2}


@mcp.tool()
def risiko_uebersicht(inaktiv_tage: int = 90) -> dict:
    """Führt alle Prüfungen über alle Benutzer zusammen: SoD-Konflikte, gesperrte und ausgetretene
    Benutzer, inaktive Benutzer, kritische Berechtigungen, Rollen außerhalb des Rollenkonzepts,
    laut Nutzungsdaten nicht benötigte Rollen und bald ablaufende Ausnahmen. Liefert die Befunde nach Priorität sortiert. Die Priorität folgt
    festen Regeln. Die Schwelle für Inaktivität beträgt höchstens 90 Tage. Nur lesend."""
    angefragt = inaktiv_tage
    inaktiv_tage, hinweis = begrenze_inaktiv_tage(inaktiv_tage)
    befunde = []

    abgesichert = []
    for b in lade("benutzer.csv"):
        for regel in sod_konflikte(b["benutzer"]):
            if regel["status"] == "abgesichert":
                abgesichert.append(
                    f'{b["benutzer"]} {regel["regel_id"]}: {regel["ausnahme"]["kontrolle"]} '
                    f'(gültig bis {regel["ausnahme"]["gueltig_bis"]})'
                )
                continue
            abgelaufen = regel["status"] == "ausnahme_abgelaufen"
            detail = (f'{regel["regel_id"]}: {regel["beschreibung"]} '
                      f'(über {", ".join(regel["transaktionen_a"])} und {", ".join(regel["transaktionen_b"])})')
            if abgelaufen:
                detail += f'. Ausnahme abgelaufen am {regel["ausnahme"]["gueltig_bis"]}'
            befunde.append({
                "prioritaet": "hoch" if abgelaufen else regel["risiko"],
                "kategorie": "Ausnahme abgelaufen" if abgelaufen else "SoD-Konflikt",
                "benutzer": b["benutzer"],
                "name": b["name"],
                "detail": detail,
            })

    for b in gesperrte_mit_rollen()["benutzer"]:
        befunde.append({
            "prioritaet": "hoch",
            "kategorie": "Gesperrt mit Rollen",
            "benutzer": b["benutzer"],
            "name": b["name"],
            "detail": "Rollen: " + ", ".join(b["rollen"]),
        })

    for b in inaktive_benutzer(inaktiv_tage)["benutzer"]:
        p = personaldaten(b["benutzer"])
        if p and p["status"] == "ausgetreten":
            continue  # wird als Austritt gemeldet, das ist schwerwiegender
        befunde.append({
            "prioritaet": "mittel",
            "kategorie": "Inaktiver Benutzer",
            "benutzer": b["benutzer"],
            "name": b["name"],
            "detail": f'{b["tage_inaktiv"]} Tage ohne Anmeldung, Rollen: ' + ", ".join(b["rollen"]),
        })

    for b in kritische_berechtigungen()["benutzer"]:
        befunde.append({
            "prioritaet": "prüfen",
            "kategorie": "Kritische Berechtigung",
            "benutzer": b["benutzer"],
            "name": b["name"],
            "detail": f'{b["abteilung"]}: ' + ", ".join(t["tcode"] for t in b["kritische_transaktionen"]),
        })

    for b in austritt_mit_rechten()["benutzer"]:
        if b["benutzer_gesperrt"]:
            continue  # bereits als "Gesperrt mit Rollen" erfasst
        befunde.append({
            "prioritaet": "hoch",
            "kategorie": "Ausgetreten, Benutzer aktiv",
            "benutzer": b["benutzer"],
            "name": b["name"],
            "detail": f'Austritt am {b["austritt"]}, letzte Anmeldung {b["letzte_anmeldung"]}. '
                      f'Vorschlag: {b["vorschlag"]}',
        })

    # Nicht benötigte Rollen je Benutzer sammeln. Ohne Nutzungsdaten bleibt die Liste leer.
    nutzung = ungenutzte_rechte(inaktiv_tage=inaktiv_tage)
    nicht_benoetigt = {}
    for r in nutzung.get("nicht_benoetigte_rollen", []):
        nicht_benoetigt.setdefault(r["benutzer"], []).append(r)

    for b in rollen_ausserhalb_rollenkonzept()["benutzer"]:
        wechsel = b["art"] == "Wechsel ohne Bereinigung"
        detail = f'{b["abteilung"]} seit {b["abteilung_seit"]}. {b["vorschlag"]}'
        # Ist eine dieser Rollen auch nicht benötigt, wird der Befund ergänzt statt doppelt gemeldet
        for r in [r for r in nicht_benoetigt.get(b["benutzer"], []) if r["rolle"] in b["unpassende_rollen"]]:
            detail += f'. {r["rolle"]} seit {r["nicht_benoetigt_seit"]} nicht benötigt'
            nicht_benoetigt[b["benutzer"]].remove(r)
        befunde.append({
            "prioritaet": "mittel" if wechsel else "prüfen",
            "kategorie": b["art"],
            "benutzer": b["benutzer"],
            "name": b["name"],
            "detail": detail,
        })

    # Übrig sind Rollen, die zur Abteilung passen, aber nicht genutzt werden: neu durch die Nutzungsdaten
    for kennung, rollen in nicht_benoetigt.items():
        if not rollen:
            continue
        teile = []
        for r in rollen:
            text = f'{r["rolle"]} seit {r["nicht_benoetigt_seit"]}'
            if r["loest_sod_konflikte"]:
                text += f', Entzug löst {", ".join(r["loest_sod_konflikte"])}'
            teile.append(text)
        befunde.append({
            "prioritaet": "prüfen",
            "kategorie": "Rolle nicht benötigt",
            "benutzer": kennung,
            "name": rollen[0]["name"],
            "detail": "; ".join(teile) + ". Entzug mit Fachbereich abstimmen",
        })

    for a in ablaufende_ausnahmen()["ausnahmen"]:
        befunde.append({
            "prioritaet": "prüfen",
            "kategorie": "Ausnahme läuft bald ab",
            "benutzer": a["benutzer"],
            "name": finde_benutzer(a["benutzer"])["name"],
            "detail": f'{a["regel_id"]} läuft in {a["tage_bis_ablauf"]} Tagen ab. Verantwortlich: {a["verantwortlich"]}',
        })

    befunde.sort(key=lambda x: REIHENFOLGE[x["prioritaet"]])
    ergebnis = {
        "stand": date.today().isoformat(),
        "inaktiv_ab_tagen": inaktiv_tage,
        "hinweise": [hinweis] if hinweis else [],
        "anzahl_befunde": len(befunde),
        "nach_prioritaet": {p: sum(1 for x in befunde if x["prioritaet"] == p) for p in REIHENFOLGE},
        "befunde": befunde,
        "abgesicherte_konflikte": abgesichert,
    }
    protokolliere("risiko_uebersicht", {"inaktiv_tage": angefragt}, ergebnis)
    return ergebnis


if __name__ == "__main__":
    mcp.run(transport="stdio")
