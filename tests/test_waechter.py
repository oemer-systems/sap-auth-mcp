"""Tests für den Wächter. Ausführen mit: python -m pytest"""
import hashlib
import json

import server
import waechter


def test_erster_lauf_meldet_alle_befunde_als_neu():
    e = waechter.lauf()
    assert e["erster_lauf"]
    assert len(e["neu"]) == 19
    assert waechter.ZUSTAND.exists()


def test_zweiter_lauf_meldet_nichts_neues():
    waechter.lauf()
    e = waechter.lauf()
    assert not e["erster_lauf"]
    assert e["neu"] == [] and e["erledigt"] == []
    assert e["offen_gesamt"] == 19
    assert e["bericht"] is None


def test_neuer_und_erledigter_befund_werden_erkannt():
    waechter.lauf()
    zustand = json.loads(waechter.ZUSTAND.read_text(encoding="utf-8"))
    zustand["befunde"].remove("SoD-Konflikt | MMUSTER | SOD01")
    zustand["befunde"].append("Gesperrt mit Rollen | ALTUSER")
    waechter.ZUSTAND.write_text(json.dumps(zustand), encoding="utf-8")

    e = waechter.lauf()
    assert [waechter.schluessel(b) for b in e["neu"]] == ["SoD-Konflikt | MMUSTER | SOD01"]
    assert e["erledigt"] == ["Gesperrt mit Rollen | ALTUSER"]


def test_neue_sod_befunde_bekommen_einen_vorschlag():
    e = waechter.lauf()
    mmuster = [b for b in e["neu"] if b["benutzer"] == "MMUSTER" and b["kategorie"] == "SoD-Konflikt"]
    assert mmuster and all(b["vorschlag"] == "Rolle Z_FI_ZAHLLAUF entziehen, in 13 Monaten nicht benötigt"
                           for b in mmuster)
    jbecker = [b for b in e["neu"] if b["benutzer"] == "JBECKER" and b["kategorie"] == "SoD-Konflikt"]
    assert jbecker and all("Z_BC_ROLLENADMIN" in b["vorschlag"] and "Fachbereich" in b["vorschlag"]
                           for b in jbecker)


def test_bericht_wird_geschrieben():
    e = waechter.lauf()
    text = open(e["bericht"], encoding="utf-8").read()
    assert "Neue Befunde (19)" in text
    assert (waechter.AUSGABE / "waechter_letzter.json").exists()


def test_waechter_aendert_keine_daten():
    def pruefsumme():
        return {p.name: hashlib.md5(p.read_bytes()).hexdigest() for p in server.DATA.glob("*.csv")}
    vorher = pruefsumme()
    waechter.lauf()
    assert pruefsumme() == vorher


def test_fakten_text_trennt_behoben_und_neu():
    neu = [{"prioritaet": "hoch", "kategorie": "Gesperrt mit Rollen", "benutzer": "NWOLF",
            "name": "N. Wolf", "detail": "Rollen: Z_SD_VERTRIEB"}]
    text = waechter.fakten_text(neu, ["SoD-Konflikt | MMUSTER | SOD01"], 13)
    assert "1 neue Befunde, 1 behobene Befunde, 13 Befunde" in text
    assert text.index("BEHOBEN") < text.index("SOD01") < text.index("NEU AUFGETRETEN") < text.index("NWOLF")
