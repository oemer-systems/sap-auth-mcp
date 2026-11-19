import sys

from agent import LOKALES_MODELL, frage_stellen

modus = "lokal" if "lokal" in sys.argv[1:] else "cloud"
verlauf = []
print(f"SAP-Berechtigungsassistent ({'lokal mit ' + LOKALES_MODELL if modus == 'lokal' else 'Cloud mit Claude'}). "
      "'exit' zum Beenden.\n")

while True:
    frage = input("Du: ").strip()
    if frage.lower() in ("exit", "quit"):
        break
    if not frage:
        continue
    antwort, aufrufe = frage_stellen(verlauf, frage, modus)
    for aufruf in aufrufe:
        print(f"  [Werkzeug] {aufruf}")
    print(f"\nKI: {antwort}\n")
