"""Automatische Tests für alle Prüfungen. Ausführen mit: python -m pytest"""
import csv
from datetime import date

import pytest

import import_sap
import server


class FesterTag(date):
    """Ein Datum, das immer den 19.11.2026 als heute liefert."""

    @classmethod
    def today(cls):
        return cls(2026, 11, 19)


@pytest.fixture(autouse=True)
def testumgebung(monkeypatch, tmp_path):
    """Läuft vor jedem Test: festes Datum, Protokoll in einen Testordner."""
    monkeypatch.setattr(server, "date", FesterTag)
    monkeypatch.setattr(import_sap, "date", FesterTag)
    monkeypatch.setattr(server, "LOG", tmp_path / "test_protokoll.jsonl")


# Benutzersuche

def test_suche_ueber_kennung_und_namen():
    assert server.finde_benutzer("mmuster")["name"] == "Max Mustermann"
    assert server.finde_benutzer("Anna Weber")["benutzer"] == "AWEBER"


def test_unbekannter_benutzer_liefert_fehler():
    assert "fehler" in server.sod_pruefen("XYZ")


# SoD-Prüfung

def test_sod_konflikte_mmuster():
    ergebnis = server.sod_pruefen("MMUSTER")
    assert ergebnis["anzahl_konflikte"] == 3
    assert [k["regel_id"] for k in ergebnis["konflikte"]] == ["SOD01", "SOD02", "SOD03"]


def test_s4_transaktion_bp_wird_erkannt():
    konflikte = server.sod_pruefen("KBAUER")["konflikte"]
    assert {k["regel_id"] for k in konflikte} == {"SOD01", "SOD02"}
    assert all(k["transaktionen_a"] == ["BP"] for k in konflikte)


def test_sauberer_benutzer_hat_keine_konflikte():
    assert server.sod_pruefen("TSCHMIDT")["anzahl_konflikte"] == 0


# Genehmigte Ausnahmen

def test_gueltige_ausnahme_sichert_konflikt_ab():
    ergebnis = server.sod_pruefen("LKRAUSE")
    assert ergebnis["anzahl_konflikte"] == 1
    assert ergebnis["davon_offen"] == 0
    assert ergebnis["konflikte"][0]["status"] == "abgesichert"


def test_abgelaufene_ausnahme_wird_erkannt():
    status = {k["regel_id"]: k["status"] for k in server.sod_pruefen("AWEBER")["konflikte"]}
    assert status == {"SOD04": "offen", "SOD05": "ausnahme_abgelaufen"}


# Weitere Prüfungen

def test_gesperrte_mit_rollen():
    ergebnis = server.gesperrte_mit_rollen()
    assert [b["benutzer"] for b in ergebnis["benutzer"]] == ["NWOLF"]


def test_inaktive_benutzer_mit_schwelle():
    assert [b["benutzer"] for b in server.inaktive_benutzer()["benutzer"]] == ["DSCHULZ"]
    assert {b["benutzer"] for b in server.inaktive_benutzer(30)["benutzer"]} == {"DSCHULZ", "FIREFIGHTER_01"}


def test_kritische_berechtigungen():
    treffer = {b["benutzer"] for b in server.kritische_berechtigungen()["benutzer"]}
    assert treffer == {"JBECKER", "FIREFIGHTER_01", "MMUSTER", "TSCHMIDT", "KBAUER"}


# Risikoübersicht

def test_risiko_uebersicht_zahlen():
    ergebnis = server.risiko_uebersicht()
    assert ergebnis["anzahl_befunde"] == 15
    assert ergebnis["nach_prioritaet"] == {"hoch": 8, "mittel": 2, "prüfen": 5}
    assert len(ergebnis["abgesicherte_konflikte"]) == 1


def test_risiko_uebersicht_ist_nach_prioritaet_sortiert():
    reihenfolge = [server.REIHENFOLGE[b["prioritaet"]] for b in server.risiko_uebersicht()["befunde"]]
    assert reihenfolge == sorted(reihenfolge)


# Protokoll

def test_jeder_aufruf_wird_protokolliert():
    server.sod_pruefen("MMUSTER")
    server.gesperrte_mit_rollen()
    zeilen = server.LOG.read_text(encoding="utf-8").splitlines()
    assert len(zeilen) == 2
    assert '"werkzeug": "sod_pruefen"' in zeilen[0]


# Import

def lies_csv(pfad):
    with open(pfad, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def test_import_ergibt_dieselben_daten(monkeypatch, tmp_path):
    monkeypatch.setattr(import_sap, "ZIEL", tmp_path)
    import_sap.main()
    for datei in ["benutzer.csv", "benutzer_rollen.csv", "rollen.csv"]:
        assert lies_csv(tmp_path / datei) == lies_csv(server.DATA / datei), datei


def test_import_ueberspringt_abgelaufene_rollenzuordnung(monkeypatch, tmp_path):
    monkeypatch.setattr(import_sap, "ZIEL", tmp_path)
    import_sap.main()
    zuordnungen = lies_csv(tmp_path / "benutzer_rollen.csv")
    assert {"benutzer": "PKLEIN", "rolle": "Z_MM_EINKAUF"} not in zuordnungen
