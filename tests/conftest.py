"""Gemeinsame Testumgebung für alle Testdateien. pytest lädt diese Datei automatisch."""
from datetime import date

import pytest

import import_sap
import server
import waechter


class FesterTag(date):
    """Ein Datum, das immer den 19.11.2026 als heute liefert."""

    @classmethod
    def today(cls):
        return cls(2026, 11, 19)


@pytest.fixture(autouse=True)
def testumgebung(monkeypatch, tmp_path):
    """Läuft vor jedem Test: festes Datum, Protokoll, Zustand und Berichte in einen Testordner."""
    monkeypatch.setattr(server, "date", FesterTag)
    monkeypatch.setattr(import_sap, "date", FesterTag)
    monkeypatch.setattr(server, "LOG", tmp_path / "test_protokoll.jsonl")
    monkeypatch.setattr(waechter, "ZUSTAND", tmp_path / "zustand" / "letzter_lauf.json")
    monkeypatch.setattr(waechter, "AUSGABE", tmp_path / "berichte")
