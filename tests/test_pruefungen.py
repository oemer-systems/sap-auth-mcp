"""Automatische Tests für alle Prüfungen. Ausführen mit: python -m pytest"""
import csv

import import_sap
import server


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


def test_zu_hohe_inaktiv_schwelle_wird_gesenkt():
    # Mit 365 Tagen würden Benutzer, die z. B. 200 Tage inaktiv sind, aus der Liste verschwinden
    ergebnis = server.inaktive_benutzer(365)
    assert ergebnis["schwelle_tage"] == 90
    assert "gesenkt" in ergebnis["hinweise"][0]
    uebersicht = server.risiko_uebersicht(inaktiv_tage=365)
    assert uebersicht["inaktiv_ab_tagen"] == 90
    assert uebersicht["anzahl_befunde"] == 19


def test_kleinere_inaktiv_schwelle_ist_erlaubt():
    # So hat die KI im Chat aufgerufen: 30 Tage ergeben mehr Alarm, das ist zulässig
    uebersicht = server.risiko_uebersicht(inaktiv_tage=30)
    assert uebersicht["inaktiv_ab_tagen"] == 30 and uebersicht["hinweise"] == []
    assert uebersicht["anzahl_befunde"] == 20


def test_kritische_berechtigungen():
    treffer = {b["benutzer"] for b in server.kritische_berechtigungen()["benutzer"]}
    assert treffer == {"JBECKER", "FIREFIGHTER_01", "MMUSTER", "TSCHMIDT", "KBAUER"}


# Risikoübersicht

def test_risiko_uebersicht_zahlen():
    ergebnis = server.risiko_uebersicht()
    assert ergebnis["anzahl_befunde"] == 19
    assert ergebnis["nach_prioritaet"] == {"hoch": 9, "mittel": 2, "prüfen": 8}
    assert len(ergebnis["abgesicherte_konflikte"]) == 1


def test_risiko_uebersicht_meldet_nicht_benoetigte_rolle_ohne_doppelung():
    befunde = server.risiko_uebersicht()["befunde"]
    neu = [b for b in befunde if b["kategorie"] == "Rolle nicht benötigt"]
    assert [b["benutzer"] for b in neu] == ["JBECKER"]
    assert "Z_BC_ROLLENADMIN" in neu[0]["detail"] and "SOD07" in neu[0]["detail"]
    # MMUSTER und KBAUER: bestehender Befund wird ergänzt, kein zweiter Befund
    rollenbefunde = ("Rolle außerhalb des Rollenkonzepts", "Rolle nicht benötigt")
    mmuster = [b for b in befunde if b["benutzer"] == "MMUSTER" and b["kategorie"] in rollenbefunde]
    assert len(mmuster) == 1
    assert "Z_FI_ZAHLLAUF seit mindestens 13 Monaten" in mmuster[0]["detail"]
    kbauer = [b for b in befunde if b["benutzer"] == "KBAUER" and b["kategorie"] in rollenbefunde]
    assert len(kbauer) == 1 and kbauer[0]["kategorie"] == "Rolle außerhalb des Rollenkonzepts"
    assert "Z_S4_GP_PFLEGE seit" in kbauer[0]["detail"]


def test_risiko_uebersicht_ist_nach_prioritaet_sortiert():
    reihenfolge = [server.REIHENFOLGE[b["prioritaet"]] for b in server.risiko_uebersicht()["befunde"]]
    assert reihenfolge == sorted(reihenfolge)


# Simulation

def test_simulation_entzug_zahllauf_loest_alle_konflikte():
    ergebnis = server.simuliere_entzug("MMUSTER", ["Z_FI_ZAHLLAUF"])
    assert ergebnis["verlorene_transaktionen"] == ["F110"]
    assert ergebnis["geloeste_konflikte"] == ["SOD01", "SOD02", "SOD03"]
    assert ergebnis["verbleibende_konflikte"] == []
    assert ergebnis["bewertung"].startswith("Empfehlenswert")


def test_simulation_warnt_bei_rollen_der_abteilung():
    ergebnis = server.simuliere_entzug("MMUSTER", ["Z_FI_AP_SACHBEARBEITER"])
    assert ergebnis["rollen_der_abteilung_betroffen"] == ["Z_FI_AP_SACHBEARBEITER"]
    assert ergebnis["bewertung"].startswith("Achtung")


def test_simulation_erkennt_transaktion_statt_rolle():
    ergebnis = server.simuliere_entzug("MMUSTER", ["F110"])
    assert "fehler" in ergebnis
    assert "Z_FI_ZAHLLAUF" in ergebnis["hinweise"][0]
    assert "bewertung" not in ergebnis


def test_loesungsvorschlaege_mmuster():
    ergebnis = server.loesungsvorschlaege("MMUSTER")
    assert ergebnis["empfehlung"] == "Z_FI_ZAHLLAUF"
    assert ergebnis["optionen"][0]["loest"] == ["SOD01", "SOD02", "SOD03"]


def test_loesungsvorschlaege_kennen_die_nutzung():
    option = server.loesungsvorschlaege("MMUSTER")["optionen"][0]
    assert option["rolle_nicht_benoetigt"] is True
    assert option["davon_genutzt"] == []


def test_nicht_benoetigte_abteilungsrolle_wird_abgestimmt_nicht_empfohlen():
    ergebnis = server.loesungsvorschlaege("JBECKER")
    assert ergebnis["empfehlung"] is None
    assert ergebnis["mit_fachbereich_abstimmen"] == "Z_BC_ROLLENADMIN"
    # Fakten, die die KI sonst raten müsste
    assert ergebnis["abteilung"] == "IT-Basis"
    assert ergebnis["konflikte"][0]["regel_id"] == "SOD07"
    assert ergebnis["konflikte"][0]["transaktionen"] == ["SU01", "PFCG"]
    # Die Bewertung darf der Nutzung nicht widersprechen
    beste = ergebnis["optionen"][0]
    assert beste["rolle_entziehen"] == "Z_BC_ROLLENADMIN"
    assert "nicht benötigt" in beste["bewertung"] and "eingeschränkt" not in beste["bewertung"]


def test_ohne_nutzungsdaten_bleibt_es_bei_der_alten_reihenfolge(monkeypatch, tmp_path):
    for datei in server.DATA.glob("*.csv"):
        (tmp_path / datei.name).write_bytes(datei.read_bytes())
    (tmp_path / "nutzung.csv").write_text("benutzer,tcode,monat,anzahl\n", encoding="utf-8")
    monkeypatch.setattr(server, "DATA", tmp_path)
    ergebnis = server.loesungsvorschlaege("JBECKER")
    assert ergebnis["mit_fachbereich_abstimmen"] is None
    assert all(o["davon_genutzt"] == "keine Nutzungsdaten" for o in ergebnis["optionen"])
    assert server.loesungsvorschlaege("MMUSTER")["empfehlung"] == "Z_FI_ZAHLLAUF"


def test_loesungsvorschlaege_ignorieren_abgesicherte_konflikte():
    ergebnis = server.loesungsvorschlaege("LKRAUSE")
    assert ergebnis["offene_konflikte"] == []
    assert ergebnis["optionen"] == []


def test_simulation_aendert_keine_daten():
    vorher = server.transaktionen_von("AWEBER")
    server.simuliere_entzug("AWEBER", ["Z_MM_EINKAUF"])
    assert server.transaktionen_von("AWEBER") == vorher


# Least Privilege

def test_nicht_benoetigte_rollen():
    ergebnis = server.ungenutzte_rechte()
    assert ergebnis["zeitraum"] == "2025-11 bis 2026-11"
    gefunden = {(e["benutzer"], e["rolle"]) for e in ergebnis["nicht_benoetigte_rollen"]}
    assert gefunden == {("MMUSTER", "Z_FI_ZAHLLAUF"), ("KBAUER", "Z_S4_GP_PFLEGE"),
                        ("JBECKER", "Z_BC_ROLLENADMIN")}


def test_nicht_benoetigte_rolle_kennt_abteilung_und_sod():
    jbecker = next(e for e in server.ungenutzte_rechte()["nicht_benoetigte_rollen"] if e["benutzer"] == "JBECKER")
    assert jbecker["rolle_gehoert_zur_abteilung"] is True
    assert jbecker["loest_sod_konflikte"] == ["SOD07"]


def test_seltene_taetigkeit_zaehlt_bei_13_monaten():
    # LKRAUSE nutzt FD32 nur im Dezember 2025, das liegt im 13-Monats-Zeitraum
    gefunden = {(e["benutzer"], e["rolle"]) for e in server.ungenutzte_rechte()["nicht_benoetigte_rollen"]}
    assert ("LKRAUSE", "Z_FI_KREDITMANAGEMENT") not in gefunden


def test_zu_kurzer_zeitraum_wird_angehoben():
    # So hat die KI im Chat aufgerufen: mit 6 Monaten wäre LKRAUSE ein falscher Alarm
    ergebnis = server.ungenutzte_rechte(6)
    assert ergebnis["zeitraum"] == "2025-11 bis 2026-11"
    assert "angehoben" in ergebnis["hinweise"][0]
    gefunden = {(e["benutzer"], e["rolle"]) for e in ergebnis["nicht_benoetigte_rollen"]}
    assert ("LKRAUSE", "Z_FI_KREDITMANAGEMENT") not in gefunden


def test_wie_lange_nicht_benoetigt():
    rollen = {e["benutzer"]: e for e in server.ungenutzte_rechte()["nicht_benoetigte_rollen"]}
    assert rollen["MMUSTER"]["zuletzt_genutzt"] is None
    assert rollen["MMUSTER"]["nicht_benoetigt_seit"].startswith("mindestens 13 Monaten")


def test_keine_doppelmeldung_und_notfallbenutzer_ausgenommen():
    ausgenommen = {a["benutzer"] for a in server.ungenutzte_rechte()["ausgenommen"]}
    assert {"NWOLF", "DSCHULZ", "FIREFIGHTER_01"} <= ausgenommen


def test_teilweise_genutzte_rolle():
    teilweise = server.ungenutzte_rechte()["teilweise_genutzte_rollen"]
    entwickler = next(t for t in teilweise if t["benutzer"] == "JBECKER" and t["rolle"] == "Z_BC_ENTWICKLER")
    assert entwickler["ungenutzte_transaktionen"] == ["SE38", "SM30"]


def test_fehlende_monate_ergeben_hinweis():
    ergebnis = server.ungenutzte_rechte(24)
    assert "Nur 13 von 24 Monaten" in ergebnis["hinweise"][0]


def test_ohne_nutzungsdaten_keine_befunde(monkeypatch, tmp_path):
    for datei in server.DATA.glob("*.csv"):
        (tmp_path / datei.name).write_bytes(datei.read_bytes())
    (tmp_path / "nutzung.csv").write_text("benutzer,tcode,monat,anzahl\n", encoding="utf-8")
    monkeypatch.setattr(server, "DATA", tmp_path)
    assert "fehler" in server.ungenutzte_rechte()


# Änderungswünsche

def test_ausgetretene_werden_nie_entsperrt():
    for kennung in ["NWOLF", "DSCHULZ"]:
        ergebnis = server.aenderung_anfragen(kennung, "entsperren")
        assert ergebnis["ausgefuehrt"] is False
        assert ergebnis["zulaessig"] is False
        assert "ausgetreten" in ergebnis["begruendung"]
        assert "antrag" not in ergebnis


def test_entzug_ergibt_antrag_mit_begruendung():
    ergebnis = server.aenderung_anfragen("MMUSTER", "rollen_entziehen", ["Z_FI_ZAHLLAUF"])
    assert ergebnis["ausgefuehrt"] is False and ergebnis["zulaessig"] is True
    text = ergebnis["antrag_text"]
    assert "Rollen entziehen" in text and "SOD01, SOD02, SOD03" in text
    assert "nicht benötigt" in text and "Leitung Kreditorenbuchhaltung" in text
    assert "nicht eingereicht" in text


def test_entzug_einer_transaktion_statt_rolle_wird_abgelehnt():
    ergebnis = server.aenderung_anfragen("MMUSTER", "rollen_entziehen", ["F110"])
    assert ergebnis["zulaessig"] is False and "antrag" not in ergebnis


def test_zuordnung_warnt_vor_neuen_konflikten():
    ergebnis = server.aenderung_anfragen("TSCHMIDT", "rollen_zuordnen", ["Z_FI_AP_STAMMDATEN"])
    assert ergebnis["zulaessig"] == "nur mit Ausnahme"
    assert ergebnis["neue_konflikte"] == ["SOD01", "SOD02"]


def test_ausgetretener_benutzer_soll_gesperrt_werden():
    ergebnis = server.aenderung_anfragen("DSCHULZ", "sperren")
    assert ergebnis["zulaessig"] is True
    assert "ausgetreten" in ergebnis["antrag_text"]


def test_aenderungswunsch_aendert_keine_daten():
    vorher = {d.name: d.read_bytes() for d in server.DATA.glob("*.csv")}
    server.aenderung_anfragen("MMUSTER", "rollen_entziehen", ["Z_FI_ZAHLLAUF"])
    server.aenderung_anfragen("NWOLF", "entsperren")
    server.aenderung_anfragen("TSCHMIDT", "rollen_zuordnen", ["Z_FI_AP_STAMMDATEN"])
    assert {d.name: d.read_bytes() for d in server.DATA.glob("*.csv")} == vorher


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
    for datei in ["benutzer.csv", "benutzer_rollen.csv", "rollen.csv", "nutzung.csv"]:
        assert lies_csv(tmp_path / datei) == lies_csv(server.DATA / datei), datei


def test_import_ueberspringt_abgelaufene_rollenzuordnung(monkeypatch, tmp_path):
    monkeypatch.setattr(import_sap, "ZIEL", tmp_path)
    import_sap.main()
    zuordnungen = lies_csv(tmp_path / "benutzer_rollen.csv")
    assert {"benutzer": "PKLEIN", "rolle": "Z_MM_EINKAUF"} not in zuordnungen
