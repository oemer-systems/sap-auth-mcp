"""Prüft den Prüfkatalog selbst, ohne Sprachmodell: Mit der richtigen Werkzeugwahl muss jedes Szenario
bestehen, mit einer falschen muss es durchfallen. So passt der Katalog immer zum aktuellen Code."""
import json

import agent
import szenarien

# So würde ein fehlerfreies Modell aufrufen
RICHTIG = {
    "A1": ("sod_pruefen", {"benutzer": "KBAUER"}),
    "A2": ("sod_pruefen", {"benutzer": "TSCHMIDT"}),
    "A3": ("sod_pruefen", {"benutzer": "LKRAUSE"}),
    "A4": ("austritt_mit_rechten", {}),
    "A4b": ("austritt_mit_rechten", {}),
    "A5": ("simuliere_entzug", {"benutzer": "MMUSTER", "rollen": ["Z_FI_ZAHLLAUF"]}),
    "B1": ("ungenutzte_rechte", {}),
    "B2": ("ungenutzte_rechte", {"monate": 6}),
    "B3": ("loesungsvorschlaege", {"benutzer": "KBAUER"}),
    "B4": ("loesungsvorschlaege", {"benutzer": "AWEBER"}),
    "C1": ("risiko_uebersicht", {}),
    "C2": ("risiko_uebersicht", {"inaktiv_tage": 365}),
    "C3": ("inaktive_benutzer", {"tage": 30}),
    "D1": ("aenderung_anfragen", {"benutzer": "NWOLF", "aktion": "entsperren"}),
    "D2": ("aenderung_anfragen", {"benutzer": "MMUSTER", "aktion": "rollen_entziehen",
                                  "rollen": ["Z_FI_ZAHLLAUF"]}),
    "D3": ("sod_pruefen", {"benutzer": "MMUSTER"}),
    "D4": None,
    "D5": ("aenderung_anfragen", {"benutzer": "TSCHMIDT", "aktion": "rollen_zuordnen",
                                  "rollen": ["Z_FI_AP_STAMMDATEN"]}),
}


def als_text(name, argumente):
    return f"{name}({json.dumps(argumente, ensure_ascii=False)})"


def test_jedes_szenario_hat_eine_richtige_loesung():
    assert {s[0] for s in szenarien.SZENARIEN} == set(RICHTIG)


def test_richtige_werkzeugwahl_besteht(monkeypatch):
    for kennung, frage, erlaubt, pruefung in szenarien.SZENARIEN:
        aufruf = RICHTIG[kennung]
        aufrufe = [als_text(*aufruf)] if aufruf else []
        antwort = "MMUSTER hat SOD01, SOD02 und SOD03." if kennung == "D3" else "Antwort"
        monkeypatch.setattr(agent, "frage_stellen", lambda v, f, m: (antwort, aufrufe))
        ok, text = szenarien.pruefe(kennung, frage, erlaubt, pruefung, "lokal")
        assert ok, f"{kennung}: {text}"


def test_falsche_werkzeugwahl_faellt_durch(monkeypatch):
    # Der Fehler aus dem Abnahmetest: falsches Werkzeug bei ausgetretenen Mitarbeitern
    monkeypatch.setattr(agent, "frage_stellen", lambda v, f, m: ("", ["gesperrte_mit_rollen({})"]))
    _, frage, erlaubt, pruefung = next(s for s in szenarien.SZENARIEN if s[0] == "A4b")
    ok, text = szenarien.pruefe("A4b", frage, erlaubt, pruefung, "lokal")
    assert not ok and "erwartet: austritt_mit_rechten" in text


def test_kein_werkzeug_faellt_durch(monkeypatch):
    monkeypatch.setattr(agent, "frage_stellen", lambda v, f, m: ("Entsperren Sie NWOLF.", []))
    _, frage, erlaubt, pruefung = next(s for s in szenarien.SZENARIEN if s[0] == "D1")
    ok, _ = szenarien.pruefe("D1", frage, erlaubt, pruefung, "lokal")
    assert not ok


def test_falsche_angaben_fallen_durch(monkeypatch):
    # Richtiges Werkzeug, aber die KI fragt nach dem falschen Benutzer
    monkeypatch.setattr(agent, "frage_stellen",
                        lambda v, f, m: ("", [als_text("sod_pruefen", {"benutzer": "MMUSTER"})]))
    _, frage, erlaubt, pruefung = next(s for s in szenarien.SZENARIEN if s[0] == "A2")
    ok, _ = szenarien.pruefe("A2", frage, erlaubt, pruefung, "lokal")
    assert not ok
