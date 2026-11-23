"""Tests für den SoD-Linter. Ausführen mit: python -m pytest

Jeder Test baut sich ein kleines CSN von Hand, so wie es cds compile erzeugen würde.
So sieht man in jedem Test genau, welcher Fall geprüft wird, und braucht kein Node.js.
"""
import json
from pathlib import Path

from linter import sod_linter as linter

PROJEKT = Path(__file__).parent.parent
REGELN = linter.lade_regeln()


# ---------------------------------------------------------------- Hilfsfunktionen

def modell(*bausteine, agent=True):
    """Baut ein CSN mit einem Service 'S'. Jeder Baustein ist (Name, Definition)."""
    definitionen = {"S": {"kind": "service", **({"@mcp": True} if agent else {})}}
    for name, definition in bausteine:
        definitionen[f"S.{name}"] = definition
    return {"definitions": definitionen}


def aktion(funktion, rollen=None):
    d = {"kind": "action", "@sod.funktion": funktion}
    if rollen:
        d["@requires"] = rollen
    return d


def regeln_von(befunde):
    return sorted(b["regel"] for b in befunde)


# ---------------------------------------------------------------- Der Beispiel-Service

def test_beispiel_service_findet_alle_eingebauten_fehler():
    befunde = linter.pruefe(linter.lade_json(PROJEKT / "beispiel-cap/csn.json"),
                            linter.lade_json(PROJEKT / "beispiel-cap/xs-security.json"), REGELN)
    assert regeln_von(befunde) == ["H1", "R1", "R1", "R1", "R2", "R3", "R3", "R3"]
    sod = {b["meldung"][:5] for b in befunde if b["regel"] == "R3"}
    assert sod == {"SOD01", "SOD02", "SOD08"}


# ---------------------------------------------------------------- Regel 1: ungeschützt

def test_ungeschuetzte_entitaet_im_agenten_service():
    befunde = linter.pruefe_ungeschuetzt(linter.operationen(modell(("Lieferanten", {"kind": "entity"}))))
    assert [b["ort"] for b in befunde] == ["S.Lieferanten (CREATE)", "S.Lieferanten (UPDATE)", "S.Lieferanten (DELETE)"]


def test_readonly_entitaet_ist_kein_befund():
    befunde = linter.pruefe_ungeschuetzt(linter.operationen(modell(("Lieferanten", {"kind": "entity", "@readonly": True}))))
    assert befunde == []


def test_service_ohne_mcp_wird_bei_regel_1_nicht_gemeldet():
    befunde = linter.pruefe_ungeschuetzt(linter.operationen(modell(("Lieferanten", {"kind": "entity"}), agent=False)))
    assert befunde == []


def test_requires_am_service_schuetzt_alle_operationen():
    m = modell(("Lieferanten", {"kind": "entity"}))
    m["definitions"]["S"]["@requires"] = "Kreditorenpflege"
    assert linter.pruefe_ungeschuetzt(linter.operationen(m)) == []


# ---------------------------------------------------------------- Regel 2: zu offen

def test_kritische_funktion_fuer_authenticated_user():
    befunde = linter.pruefe_offene_rollen(
        linter.operationen(modell(("freigeben", aktion("F_ZAHLUNG_FREIGEBEN", "authenticated-user")))), REGELN)
    assert regeln_von(befunde) == ["R2"]


def test_unkritische_funktion_fuer_authenticated_user_ist_erlaubt():
    befunde = linter.pruefe_offene_rollen(
        linter.operationen(modell(("anlegen", aktion("F_KRED_ANLEGEN", "authenticated-user")))), REGELN)
    assert befunde == []


# ---------------------------------------------------------------- Regel 3: SoD in einer Rolle

def test_sod_konflikt_in_einer_rolle():
    m = modell(("anlegen", aktion("F_KRED_ANLEGEN", "Buchhaltung")),
               ("zahlen", aktion("F_ZAHLUNG", "Buchhaltung")))
    befunde = linter.pruefe_sod_in_rollen(linter.operationen(m), REGELN)
    assert len(befunde) == 1 and befunde[0]["meldung"].startswith("SOD01")


def test_getrennte_rollen_haben_keinen_konflikt():
    m = modell(("anlegen", aktion("F_KRED_ANLEGEN", "Kreditorenpflege")),
               ("zahlen", aktion("F_ZAHLUNG", "Zahlungslauf")),
               ("freigeben", aktion("F_ZAHLUNG_FREIGEBEN", "Zahlungsfreigabe")))
    assert [b for b in linter.pruefe(m, {}, REGELN) if b["schwere"] == "fehler"] == []


def test_was_jeder_darf_zaehlt_fuer_jede_rolle():
    m = modell(("zahlen", aktion("F_ZAHLUNG", "Buchhaltung")),
               ("freigeben", aktion("F_ZAHLUNG_FREIGEBEN", "authenticated-user")))
    befunde = linter.pruefe_sod_in_rollen(linter.operationen(m), REGELN)
    assert len(befunde) == 1
    assert befunde[0]["meldung"].startswith("SOD08") and "[für alle]" in befunde[0]["meldung"]


def test_restrict_mit_write_gilt_fuer_anlegen_und_aendern():
    m = modell(("Lieferanten", {"kind": "entity", "@restrict": [{"grant": "WRITE", "to": "Buchhaltung"}],
                                "@sod.UPDATE": "F_KRED_AENDERN"}),
               ("zahlen", aktion("F_ZAHLUNG", "Buchhaltung")))
    befunde = linter.pruefe_sod_in_rollen(linter.operationen(m), REGELN)
    assert len(befunde) == 1 and befunde[0]["meldung"].startswith("SOD02")


def test_restrict_nur_lesen_gibt_keine_schreibrechte():
    m = modell(("Lieferanten", {"kind": "entity", "@restrict": [{"grant": "READ", "to": "Pruefer"}],
                                "@sod.UPDATE": "F_KRED_AENDERN"}))
    assert linter.operationen(m) == []


# ---------------------------------------------------------------- Regel 4: Rollenvorlagen

def test_rollenvorlage_buendelt_zwei_saubere_rollen():
    m = modell(("anlegen", aktion("F_KRED_ANLEGEN", "Kreditorenpflege")),
               ("zahlen", aktion("F_ZAHLUNG", "Zahlungslauf")))
    xs = {"role-templates": [{"name": "Buchhaltung_komplett",
                              "scope-references": ["$XSAPPNAME.Kreditorenpflege", "$XSAPPNAME.Zahlungslauf"]}]}
    befunde = linter.pruefe_rollenvorlagen(linter.operationen(m), xs, REGELN)
    assert len(befunde) == 1 and "Buchhaltung_komplett" in befunde[0]["ort"]


def test_rollenvorlage_mit_einer_rolle_wird_nicht_doppelt_gemeldet():
    m = modell(("anlegen", aktion("F_KRED_ANLEGEN", "Buchhaltung")),
               ("zahlen", aktion("F_ZAHLUNG", "Buchhaltung")))
    xs = {"role-templates": [{"name": "Buchhaltung", "scope-references": ["$XSAPPNAME.Buchhaltung"]}]}
    assert linter.pruefe_rollenvorlagen(linter.operationen(m), xs, REGELN) == []


# ---------------------------------------------------------------- Hinweise

def test_operation_ohne_funktion_und_unbekannte_funktion():
    m = modell(("ohne", {"kind": "action", "@requires": "X"}),
               ("unbekannt", aktion("F_GIBT_ES_NICHT", "X")))
    assert regeln_von(linter.pruefe_zuordnung(linter.operationen(m), REGELN)) == ["H1", "H2"]


# ---------------------------------------------------------------- Aufruf von der Kommandozeile

def test_rueckgabewert_und_bom(tmp_path, capsys):
    fehlerhaft = tmp_path / "fehler.json"
    sauber = tmp_path / "sauber.json"
    # So schreibt PowerShell mit Out-File: mit unsichtbarem BOM am Anfang
    fehlerhaft.write_text(json.dumps(modell(("Lieferanten", {"kind": "entity"}))), encoding="utf-8-sig")
    sauber.write_text(json.dumps(modell(("zahlen", aktion("F_ZAHLUNG", "Zahlungslauf")))), encoding="utf-8-sig")
    assert linter.main([str(fehlerhaft)]) == 1
    assert linter.main([str(sauber)]) == 0
    assert "0 Fehler" in capsys.readouterr().out


def test_linter_aendert_keine_dateien():
    csn = PROJEKT / "beispiel-cap/csn.json"
    vorher = csn.read_bytes()
    linter.main([str(csn)])
    assert csn.read_bytes() == vorher


# ---------------------------------------------------------------- Ein Regelwerk für Menschen und Agenten

def test_linter_nutzt_das_regelwerk_des_servers():
    import server
    regeln_server = {r["regel_id"] for r in server.lade("sod_regeln.csv")}
    assert {k["id"] for k in REGELN["konflikte"]} == regeln_server
    assert "SOD08" in regeln_server


def test_kritisch_wird_aus_den_transaktionen_abgeleitet():
    assert REGELN["funktionen"]["F_ZAHLUNG"]["kritisch"]          # F110 ist kritisch
    assert not REGELN["funktionen"]["F_KRED_ANLEGEN"]["kritisch"]  # XK01, FK01, BP sind es nicht


def test_eigenes_regelwerk_als_yaml(tmp_path):
    datei = tmp_path / "regeln.yaml"
    datei.write_text("funktionen:\n  F_A: {beschreibung: A, kritisch: false}\n  F_B: {beschreibung: B, kritisch: false}\n"
                     "konflikte:\n  - {id: X1, funktionen: [F_A, F_B], risiko: hoch, beschreibung: Test}\n",
                     encoding="utf-8")
    regeln = linter.lade_regeln(datei)
    m = modell(("a", aktion("F_A", "R")), ("b", aktion("F_B", "R")))
    assert linter.pruefe_sod_in_rollen(linter.operationen(m), regeln)[0]["meldung"].startswith("X1")
