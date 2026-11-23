"""SoD-Linter für CAP-Projekte.

Prüft ein kompiliertes CDS-Modell (CSN) und die xs-security.json gegen ein SoD-Regelwerk,
bevor ein Service für KI-Agenten in Betrieb geht. Der Linter führt nichts aus, er liest nur.

Aufruf:
    python linter/sod_linter.py beispiel-cap/csn.json beispiel-cap/xs-security.json
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

STANDARD_REGELN = Path(__file__).parent / "regeln.yaml"
JEDER = {"authenticated-user", "any"}          # Pseudo-Rollen: gelten für alle Angemeldeten bzw. alle
SCHREIBEND = ["CREATE", "UPDATE", "DELETE"]


# ---------------------------------------------------------------- Dateien lesen

def lade_json(pfad):
    # utf-8-sig, weil PowerShell beim Schreiben mit Out-File ein BOM an den Anfang setzt
    return json.loads(Path(pfad).read_text(encoding="utf-8-sig"))


def lade_regeln(pfad=STANDARD_REGELN):
    return yaml.safe_load(Path(pfad).read_text(encoding="utf-8"))


def als_liste(wert):
    """CDS erlaubt 'Rolle' oder ['Rolle1', 'Rolle2']. Wir machen daraus immer eine Liste."""
    if wert is None:
        return []
    return wert if isinstance(wert, list) else [wert]


# ---------------------------------------------------------------- Operationen sammeln

def ist_agenten_service(definition):
    return bool(definition.get("@mcp") or definition.get("@agent"))


def rollen_aus_restrict(restrict, ereignis):
    """Welche Rollen dürfen laut @restrict ein Ereignis (z. B. CREATE)?"""
    rollen = []
    for regel in restrict:
        erlaubt = als_liste(regel.get("grant", "*"))
        if "*" in erlaubt or ereignis in erlaubt or (ereignis in SCHREIBEND and "WRITE" in erlaubt):
            rollen += als_liste(regel.get("to", "any"))
    return rollen


def operationen(csn):
    """Zerlegt das Modell in einzelne Operationen: wer darf was, und welche Funktion steckt dahinter."""
    definitionen = csn["definitions"]
    services = {name for name, d in definitionen.items() if d.get("kind") == "service"}
    ergebnis = []

    for name, d in definitionen.items():
        service = name.rsplit(".", 1)[0] if "." in name else None
        if service not in services:
            continue
        agent = ist_agenten_service(definitionen[service])
        geschuetzt_im_service = als_liste(definitionen[service].get("@requires"))

        if d.get("kind") == "entity":
            if d.get("@readonly"):
                continue  # Nur lesend: für SoD uninteressant
            for ereignis in SCHREIBEND:
                if "@restrict" in d:
                    rollen = rollen_aus_restrict(d["@restrict"], ereignis)
                    if not rollen:
                        continue  # Niemand darf dieses Ereignis, also auch kein Risiko
                else:
                    rollen = als_liste(d.get("@requires")) or geschuetzt_im_service
                ergebnis.append({"ort": f"{name} ({ereignis})", "service": service, "agent": agent,
                                 "funktion": d.get(f"@sod.{ereignis}"), "rollen": rollen})
            for aktion, a in d.get("actions", {}).items():   # gebundene Aktionen an einer Entität
                rollen = als_liste(a.get("@requires")) or als_liste(d.get("@requires")) or geschuetzt_im_service
                ergebnis.append({"ort": f"{name}.{aktion}", "service": service, "agent": agent,
                                 "funktion": a.get("@sod.funktion"), "rollen": rollen})

        elif d.get("kind") in ("action", "function"):
            if d["kind"] == "function" and not d.get("@sod.funktion"):
                continue  # Funktionen lesen nur, außer jemand hat ihnen bewusst eine SoD-Funktion gegeben
            rollen = als_liste(d.get("@requires")) or geschuetzt_im_service
            if "@restrict" in d:
                rollen = [r for regel in d["@restrict"] for r in als_liste(regel.get("to", "any"))]
            ergebnis.append({"ort": name, "service": service, "agent": agent,
                             "funktion": d.get("@sod.funktion"), "rollen": rollen})
    return ergebnis


# ---------------------------------------------------------------- Regeln

def befund(regel, schwere, ort, meldung):
    return {"regel": regel, "schwere": schwere, "ort": ort, "meldung": meldung}


def pruefe_ungeschuetzt(ops):
    """Regel 1: Jede schreibende Operation eines Agenten-Services braucht eine Rolle."""
    return [befund("R1", "fehler", op["ort"],
                   "Keine Rolle verlangt: Jeder, auch jeder KI-Agent, darf diese Operation ausführen")
            for op in ops if op["agent"] and not op["rollen"]]


def pruefe_offene_rollen(ops, regeln):
    """Regel 2: Kritische Funktionen nie für 'authenticated-user' oder 'any'."""
    funktionen = regeln["funktionen"]
    treffer = []
    for op in ops:
        offen = JEDER & set(op["rollen"])
        if offen and funktionen.get(op["funktion"], {}).get("kritisch"):
            treffer.append(befund("R2", "fehler", op["ort"],
                                  f"Kritische Funktion {op['funktion']} ist für '{sorted(offen)[0]}' freigegeben"))
    return treffer


def funktionen_je_rolle(ops):
    """Baut 'Rolle -> {Funktion: [Orte]}'. Was jeder darf, darf auch jede einzelne Rolle."""
    je_rolle, fuer_alle = {}, {}
    for op in ops:
        if not op["funktion"]:
            continue
        if not op["rollen"] or JEDER & set(op["rollen"]):
            fuer_alle.setdefault(op["funktion"], []).append(op["ort"] + " [für alle]")
        for rolle in set(op["rollen"]) - JEDER:
            je_rolle.setdefault(rolle, {}).setdefault(op["funktion"], []).append(op["ort"])
    for funktionen in je_rolle.values():
        for funktion, orte in fuer_alle.items():
            funktionen.setdefault(funktion, []).extend(orte)
    if fuer_alle:
        je_rolle["(jeder Angemeldete)"] = {f: list(o) for f, o in fuer_alle.items()}
    return je_rolle


def konflikte_in(funktionen, regeln):
    for k in regeln["konflikte"]:
        a, b = k["funktionen"]
        if a in funktionen and b in funktionen:
            yield k, funktionen[a], funktionen[b]


def pruefe_sod_in_rollen(ops, regeln):
    """Regel 3: Keine CAP-Rolle darf beide Seiten eines SoD-Konflikts erhalten."""
    treffer = []
    for rolle, funktionen in sorted(funktionen_je_rolle(ops).items()):
        for k, orte_a, orte_b in konflikte_in(funktionen, regeln):
            treffer.append(befund("R3", "fehler", f"Rolle {rolle}",
                                  f"{k['id']} ({k['risiko']}): {k['beschreibung']}. "
                                  f"Über: {', '.join(orte_a)} + {', '.join(orte_b)}"))
    return treffer


def pruefe_rollenvorlagen(ops, xs_security, regeln):
    """Regel 4: Auch eine Rollenvorlage in der xs-security.json darf keine Konfliktseiten bündeln."""
    je_rolle = funktionen_je_rolle(ops)
    treffer = []
    for vorlage in xs_security.get("role-templates", []):
        rollen = [s.split(".", 1)[1] for s in vorlage.get("scope-references", []) if "." in s]
        if len(rollen) < 2:
            continue  # Eine Vorlage mit nur einer Rolle prüft schon Regel 3
        gebuendelt = {}
        for rolle in rollen:
            for funktion, orte in je_rolle.get(rolle, {}).items():
                gebuendelt.setdefault(funktion, []).extend(orte)
        schon_gemeldet = {k["id"] for r in rollen for k, *_ in konflikte_in(je_rolle.get(r, {}), regeln)}
        for k, _, _ in konflikte_in(gebuendelt, regeln):
            if k["id"] not in schon_gemeldet:  # steckt schon in einer einzelnen Rolle, meldet Regel 3
                treffer.append(befund("R4", "fehler", f"Rollenvorlage {vorlage['name']}",
                                      f"{k['id']} ({k['risiko']}): Die Vorlage bündelt die Rollen "
                                      f"{', '.join(rollen)} und damit {k['funktionen'][0]} und {k['funktionen'][1]}"))
    return treffer


def pruefe_zuordnung(ops, regeln):
    """Hinweise: Operationen ohne SoD-Funktion werden nicht geprüft, unbekannte Funktionen auch nicht."""
    treffer = []
    for op in ops:
        if not op["agent"]:
            continue
        if not op["funktion"]:
            treffer.append(befund("H1", "warnung", op["ort"],
                                  "Keine SoD-Funktion zugeordnet (@sod...). Diese Operation wird nicht auf Konflikte geprüft"))
        elif op["funktion"] not in regeln["funktionen"]:
            treffer.append(befund("H2", "warnung", op["ort"],
                                  f"Funktion {op['funktion']} steht nicht im Regelwerk"))
    return treffer


def pruefe(csn, xs_security, regeln):
    ops = operationen(csn)
    return (pruefe_ungeschuetzt(ops) + pruefe_offene_rollen(ops, regeln) + pruefe_sod_in_rollen(ops, regeln)
            + pruefe_rollenvorlagen(ops, xs_security, regeln) + pruefe_zuordnung(ops, regeln))


# ---------------------------------------------------------------- Ausgabe

def main(argumente=None):
    parser = argparse.ArgumentParser(description="SoD-Linter für CAP-Projekte")
    parser.add_argument("csn", help="Kompiliertes Modell: cds compile srv --to json")
    parser.add_argument("xs_security", nargs="?", help="xs-security.json (optional)")
    parser.add_argument("--regeln", default=STANDARD_REGELN, help="SoD-Regelwerk als YAML")
    a = parser.parse_args(argumente)

    befunde = pruefe(lade_json(a.csn), lade_json(a.xs_security) if a.xs_security else {}, lade_regeln(a.regeln))
    for b in befunde:
        zeichen = "FEHLER " if b["schwere"] == "fehler" else "WARNUNG"
        print(f"{zeichen} [{b['regel']}] {b['ort']}\n        {b['meldung']}")
    fehler = sum(b["schwere"] == "fehler" for b in befunde)
    print(f"\n{fehler} Fehler, {len(befunde) - fehler} Warnungen")
    return 1 if fehler else 0   # 1 = GitHub Action schlägt fehl


if __name__ == "__main__":
    sys.exit(main())
